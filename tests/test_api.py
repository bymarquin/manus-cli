from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from manus_cli.api import ManusAPIError, ManusClient, SlidingWindowRateLimiter, _with_retries


def _mock_client(handler) -> ManusClient:
    client = ManusClient("fake-key")
    client._http = httpx.Client(base_url="https://api.manus.ai/v2", transport=httpx.MockTransport(handler))
    client._external_http = httpx.Client(transport=httpx.MockTransport(handler))
    return client


class RetryPolicyTests(unittest.TestCase):
    """The core P0 concern: POST (task.create/sendMessage/confirmAction/file.upload's
    record POST) must never be blindly retried on an ambiguous failure, since the
    server may already have processed it — that would duplicate a real side effect.
    GET/PUT-to-presigned-url are idempotent and safe to retry on anything transient.
    """

    def test_idempotent_get_retries_readtimeout_and_recovers(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] < 3:
                raise httpx.ReadTimeout("simulated", request=request)
            return httpx.Response(200, json={"ok": True, "request_id": "r", "task": {"id": "t", "status": "stopped"}})

        with patch("manus_cli.api.time.sleep"):
            resp = _with_retries(lambda: handler(httpx.Request("GET", "https://x")), idempotent=True)
        self.assertEqual(calls["n"], 3)
        self.assertEqual(resp.status_code, 200)

    def test_non_idempotent_post_does_not_retry_ambiguous_timeout(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            raise httpx.ReadTimeout("simulated", request=request)

        with self.assertRaises(ManusAPIError) as ctx:
            _with_retries(lambda: handler(httpx.Request("POST", "https://x")), idempotent=False)
        self.assertEqual(calls["n"], 1, "non-idempotent call must not be retried on an ambiguous failure")
        self.assertEqual(ctx.exception.code, "ambiguous_failure")

    def test_non_idempotent_post_retries_pure_connect_error(self):
        # A connect error means the request never reached the server — always safe.
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] < 2:
                raise httpx.ConnectError("simulated", request=request)
            return httpx.Response(200, json={"ok": True, "request_id": "r", "task_id": "new"})

        with patch("manus_cli.api.time.sleep"):
            resp = _with_retries(lambda: handler(httpx.Request("POST", "https://x")), idempotent=False)
        self.assertEqual(calls["n"], 2)
        self.assertEqual(resp.status_code, 200)

    def test_non_idempotent_post_retries_on_429_only(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(429, json={"ok": False, "request_id": "r", "error": {"code": "rate_limited", "message": "slow"}})
            return httpx.Response(200, json={"ok": True, "request_id": "r", "task_id": "new"})

        with patch("manus_cli.api.time.sleep"):
            resp = _with_retries(lambda: handler(httpx.Request("POST", "https://x")), idempotent=False)
        self.assertEqual(calls["n"], 2)
        self.assertEqual(resp.status_code, 200)

    def test_non_idempotent_post_does_not_retry_5xx(self):
        # A 5xx after the request was sent is ambiguous for a non-idempotent op —
        # unlike 429, we can't be sure the server didn't do the work — so it's
        # surfaced as a normal application error on the first response, not retried.
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(
                500, json={"ok": False, "request_id": "r", "error": {"code": "server_error", "message": "oops"}}
            )

        client = _mock_client(handler)
        with patch("manus_cli.api.time.sleep"), self.assertRaises(ManusAPIError):
            client.create_task("oi")
        self.assertEqual(calls["n"], 1)

    def test_retry_after_header_and_jitter(self):
        calls = {"n": 0}
        sleeps = []

        def handler(request):
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(429, headers={"Retry-After": "0.2"}, json={"ok": False, "request_id": "r", "error": {"code": "rate_limited", "message": "slow"}})
            return httpx.Response(200, json={"ok": True, "request_id": "r"})

        with patch("manus_cli.api.time.sleep", side_effect=lambda s: sleeps.append(s)):
            _with_retries(lambda: handler(httpx.Request("GET", "https://x")), idempotent=True)
        self.assertEqual(len(sleeps), 1)
        self.assertGreaterEqual(sleeps[0], 0.2)
        self.assertLess(sleeps[0], 0.2 * 1.26)  # base + up to 25% jitter

    def test_exhausts_attempts_and_raises(self):
        def handler(request):
            raise httpx.ConnectError("always fails", request=request)

        with patch("manus_cli.api.time.sleep"), self.assertRaises(ManusAPIError) as ctx:
            _with_retries(lambda: handler(httpx.Request("GET", "https://x")), idempotent=True, attempts=3)
        self.assertEqual(ctx.exception.code, "network_error")

    def test_before_attempt_runs_for_every_http_attempt(self):
        attempts = []
        responses = [
            httpx.Response(429, json={"ok": False}),
            httpx.Response(200, json={"ok": True}),
        ]
        with patch("manus_cli.api.time.sleep"):
            _with_retries(
                lambda: responses.pop(0),
                idempotent=False,
                before_attempt=lambda: attempts.append("attempt"),
            )
        self.assertEqual(attempts, ["attempt", "attempt"])


class SlidingWindowRateLimiterTests(unittest.TestCase):
    def test_waits_after_limit_and_opens_next_window(self):
        now = [0.0]
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            now[0] += seconds

        limiter = SlidingWindowRateLimiter(2, period=60, clock=lambda: now[0], sleep=sleep)
        limiter.acquire()
        limiter.acquire()
        limiter.acquire()
        self.assertEqual(sleeps, [60.0])


class HttpErrorHandlingTests(unittest.TestCase):
    def test_non_json_body_raises_clean_error(self):
        client = _mock_client(lambda request: httpx.Response(200, text="<html>oops</html>"))
        with self.assertRaises(ManusAPIError) as ctx:
            client.task_detail("t1")
        self.assertEqual(ctx.exception.code, "invalid_response")

    def test_unexpected_json_shape_raises_clean_error(self):
        client = _mock_client(lambda request: httpx.Response(200, json=[1, 2, 3]))
        with self.assertRaises(ManusAPIError) as ctx:
            client.task_detail("t1")
        self.assertEqual(ctx.exception.code, "invalid_response")

    def test_request_id_preserved_on_application_error(self):
        client = _mock_client(
            lambda request: httpx.Response(
                200, json={"ok": False, "request_id": "req_xyz", "error": {"code": "not_found", "message": "gone"}}
            )
        )
        with self.assertRaises(ManusAPIError) as ctx:
            client.task_detail("t1")
        self.assertEqual(ctx.exception.request_id, "req_xyz")

    def test_missing_error_object_does_not_crash(self):
        client = _mock_client(lambda request: httpx.Response(200, json={"ok": False, "request_id": "r"}))
        with self.assertRaises(ManusAPIError) as ctx:
            client.task_detail("t1")
        self.assertEqual(ctx.exception.code, "unknown_error")

    def test_api_key_never_appears_in_error_message(self):
        client = ManusClient("sk-super-secret-key-value")
        client._http = httpx.Client(
            base_url="https://api.manus.ai/v2",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"ok": False, "request_id": "r", "error": {"code": "x", "message": "boom"}})
            ),
        )
        try:
            client.task_detail("t1")
        except ManusAPIError as e:
            self.assertNotIn("sk-super-secret-key-value", str(e))
            self.assertNotIn("sk-super-secret-key-value", e.message)


class ClientLifecycleTests(unittest.TestCase):
    def test_close_closes_both_http_clients(self):
        client = ManusClient("fake-key")
        client.close()
        self.assertTrue(client._http.is_closed)
        self.assertTrue(client._external_http.is_closed)

    def test_context_manager_closes_on_exit(self):
        with ManusClient("fake-key") as client:
            self.assertFalse(client._http.is_closed)
        self.assertTrue(client._http.is_closed)


class DownloadFileTests(unittest.TestCase):
    def test_never_overwrites_existing_file_silently(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "out.txt"
            dest.write_text("original")
            client = _mock_client(lambda request: httpx.Response(200, content=b"new content"))
            result = client.download_file("https://example.com/f.txt", dest)
            self.assertNotEqual(result, dest)
            self.assertEqual(dest.read_text(), "original")
            self.assertEqual(result.read_text(), "new content")

    def test_aborts_and_cleans_up_temp_file_when_over_size_limit(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "big.bin"
            client = _mock_client(lambda request: httpx.Response(200, content=b"x" * 1000))
            with self.assertRaises(ManusAPIError):
                client.download_file("https://example.com/big", dest, max_bytes=500)
            self.assertFalse(dest.exists())
            self.assertEqual(list(Path(tmp).glob(".manus-dl-*")), [])

    def test_rejects_non_https_url(self):
        client = ManusClient("fake-key")
        with self.assertRaises(ManusAPIError):
            client.download_file("http://example.com/f.txt", Path("/tmp/whatever"))


class UploadFileTests(unittest.TestCase):
    def test_put_retry_reopens_stream_and_resends_complete_body(self):
        import tempfile

        bodies = []

        def api_handler(request):
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "upload_url": "https://uploads.example/file",
                    "file": {"id": "f1"},
                },
            )

        def upload_handler(request):
            bodies.append(request.read())
            if len(bodies) == 1:
                return httpx.Response(503)
            return httpx.Response(200)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "data.bin"
            path.write_bytes(b"complete payload")
            client = ManusClient("fake-key")
            client._http.close()
            client._external_http.close()
            client._http = httpx.Client(
                base_url="https://api.manus.ai/v2", transport=httpx.MockTransport(api_handler)
            )
            client._external_http = httpx.Client(transport=httpx.MockTransport(upload_handler))
            with patch("manus_cli.api.time.sleep"):
                file_id = client.upload_file(path)
            client.close()

        self.assertEqual(file_id, "f1")
        self.assertEqual(bodies, [b"complete payload", b"complete payload"])


if __name__ == "__main__":
    unittest.main()
