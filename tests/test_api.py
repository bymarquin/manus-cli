import unittest

import httpx

from manus_cli.api import ManusAPIError, _with_retries, last_assistant_entry, last_assistant_message


class LastAssistantMessageTests(unittest.TestCase):
    def test_finds_first_match_in_desc_order(self):
        messages = [
            {"type": "assistant_message", "assistant_message": {"content": "resposta final"}},
            {"type": "user_message", "user_message": {"content": "pergunta"}},
        ]
        self.assertEqual(last_assistant_message(messages), "resposta final")

    def test_none_when_absent(self):
        self.assertIsNone(last_assistant_message([{"type": "status_update"}]))

    def test_entry_includes_attachments(self):
        messages = [
            {
                "type": "assistant_message",
                "assistant_message": {"content": "oi", "attachments": [{"filename": "a.txt"}]},
            }
        ]
        entry = last_assistant_entry(messages)
        self.assertEqual(entry["attachments"], [{"filename": "a.txt"}])


class WithRetriesTests(unittest.TestCase):
    def test_recovers_after_transient_connect_errors(self):
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) < 3:
                raise httpx.ConnectError("conexão recusada (simulada)")

            class FakeResp:
                status_code = 200

            return FakeResp()

        resp = _with_retries(flaky, attempts=4, base_delay=0.01)
        self.assertEqual(len(calls), 3)
        self.assertEqual(resp.status_code, 200)

    def test_raises_manus_api_error_after_exhausting_attempts(self):
        def always_fails():
            raise httpx.ConnectError("sempre falha (simulado)")

        with self.assertRaises(ManusAPIError) as ctx:
            _with_retries(always_fails, attempts=3, base_delay=0.01)
        self.assertEqual(ctx.exception.code, "network_error")

    def test_retries_5xx_then_succeeds(self):
        calls = []

        def flaky_status():
            calls.append(1)

            class FakeResp:
                status_code = 503 if len(calls) < 2 else 200

            return FakeResp()

        resp = _with_retries(flaky_status, attempts=4, base_delay=0.01)
        self.assertEqual(len(calls), 2)
        self.assertEqual(resp.status_code, 200)

    def test_does_not_retry_4xx(self):
        calls = []

        def client_error():
            calls.append(1)

            class FakeResp:
                status_code = 404

            return FakeResp()

        resp = _with_retries(client_error, attempts=4, base_delay=0.01)
        self.assertEqual(len(calls), 1)
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
