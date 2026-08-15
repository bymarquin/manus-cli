from __future__ import annotations

import json
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


class TaskLifecycleAndProjectEndpointsTests(unittest.TestCase):
    """task.stop/delete/update and project.create/list — request shape, and that
    these are treated as idempotent (safe to retry on ambiguous failure), unlike
    task.create/sendMessage."""

    def _capture(self, response_extra=None):
        captured = {}

        def handler(request):
            captured["method"] = request.method
            captured["path"] = request.url.path
            captured["body"] = json.loads(request.content) if request.content else None
            body = {"ok": True, "request_id": "r1"}
            if response_extra:
                body.update(response_extra)
            return httpx.Response(200, json=body)

        return _mock_client(handler), captured

    def test_stop_task_posts_task_id(self):
        client, captured = self._capture()
        client.stop_task("t1")
        self.assertEqual(captured["method"], "POST")
        self.assertTrue(captured["path"].endswith("/task.stop"))
        self.assertEqual(captured["body"], {"task_id": "t1"})

    def test_delete_task_posts_task_id(self):
        client, captured = self._capture()
        client.delete_task("t1")
        self.assertTrue(captured["path"].endswith("/task.delete"))
        self.assertEqual(captured["body"], {"task_id": "t1"})

    def test_update_task_only_sends_provided_fields(self):
        client, captured = self._capture()
        client.update_task("t1", title="Novo título")
        self.assertEqual(captured["body"], {"task_id": "t1", "title": "Novo título"})

    def test_update_task_sends_all_fields_when_given(self):
        client, captured = self._capture()
        client.update_task("t1", title="X", share_visibility="public", visible_in_task_list=False)
        self.assertEqual(
            captured["body"],
            {"task_id": "t1", "title": "X", "share_visibility": "public", "enable_visible_in_task_list": False},
        )

    def test_create_project_omits_instruction_when_absent(self):
        client, captured = self._capture()
        client.create_project("Meu Projeto")
        self.assertEqual(captured["body"], {"name": "Meu Projeto"})

    def test_create_project_includes_instruction_when_given(self):
        client, captured = self._capture()
        client.create_project("Meu Projeto", instruction="Sempre responda em português")
        self.assertEqual(captured["body"], {"name": "Meu Projeto", "instruction": "Sempre responda em português"})

    def test_list_projects_is_a_get(self):
        client, captured = self._capture()
        client.list_projects()
        self.assertEqual(captured["method"], "GET")
        self.assertTrue(captured["path"].endswith("/project.list"))

    def _assert_retries_on_ambiguous_drop(self, method_name, args):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] < 2:
                raise httpx.ReadTimeout("simulated", request=request)
            return httpx.Response(200, json={"ok": True, "request_id": "r1"})

        client = _mock_client(handler)
        with patch("manus_cli.api.time.sleep"):
            getattr(client, method_name)(*args)
        self.assertEqual(calls["n"], 2)

    def test_stop_delete_update_retry_on_ambiguous_connection_drop(self):
        # Unlike task.create/sendMessage, these must retry when the connection drops
        # after the request may have been sent — repeating stop/delete/update on an
        # already-applied change is a no-op, not a duplicated side effect.
        for method_name, args in [
            ("stop_task", ("t1",)),
            ("delete_task", ("t1",)),
            ("update_task", ("t1",)),
        ]:
            with self.subTest(method=method_name):
                self._assert_retries_on_ambiguous_drop(method_name, args)


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


class CreateTaskAgentProfileTests(unittest.TestCase):
    """A task_id has been observed to never persist server-side when agent_profile
    is omitted from task.create — always send it explicitly instead of relying on
    the server to apply its own documented default."""

    def _sent_body(self, **kwargs) -> dict:
        captured = {}

        def handler(request):
            captured["body"] = json.loads(request.read())
            return httpx.Response(200, json={"ok": True, "request_id": "r", "task_id": "t1"})

        client = _mock_client(handler)
        client.create_task("oi", **kwargs)
        return captured["body"]

    def test_agent_profile_defaults_to_manus_1_6_when_omitted(self):
        self.assertEqual(self._sent_body()["agent_profile"], "manus-1.6")

    def test_explicit_agent_profile_is_passed_through(self):
        self.assertEqual(self._sent_body(agent_profile="manus-1.6-max")["agent_profile"], "manus-1.6-max")


if __name__ == "__main__":
    unittest.main()
