from __future__ import annotations

import time
import unittest
from unittest.mock import MagicMock

from manus_cli import task_runner as tr
from manus_cli.api import ManusAPIError


def _page(messages, has_more=False, next_cursor=None):
    return {"messages": messages, "has_more": has_more, "next_cursor": next_cursor}


class PollUntilSettledTests(unittest.TestCase):
    def test_multi_page_burst_delivered_chronologically_and_terminal_found(self):
        client = MagicMock()
        client.list_messages.side_effect = [
            _page(
                [
                    {"id": "m5", "timestamp": "5000", "type": "status_update", "status_update": {"agent_status": "stopped"}},
                    {"id": "m4", "timestamp": "not-a-number", "type": "tool_used", "tool_used": {"brief": "bad ts"}},
                    {"id": "m3", "timestamp": "3000", "type": "status_update", "status_update": "not-a-dict"},
                    {"id": "m2", "timestamp": "2000", "type": "tool_used", "tool_used": {"brief": "pesquisando"}},
                ],
                has_more=True,
                next_cursor="c2",
            ),
            _page(
                [
                    {"id": "m1", "timestamp": "1000", "type": "tool_used", "tool_used": {"brief": "iniciando"}},
                    {"id": "m0", "timestamp": "0", "type": "user_message", "user_message": {"content": "oi"}},
                ]
            ),
        ]
        seen = []
        status, detail = tr.poll_until_settled(client, "t1", since_ms=500, timeout=5, on_event=lambda m: seen.append(m["id"]))
        self.assertEqual(status, "stopped")
        # m4 (malformed timestamp) and m3 (malformed status_update) are safely
        # ignored for status purposes but still delivered as events in order.
        self.assertEqual(seen, ["m1", "m2", "m3", "m5"])
        self.assertEqual(detail, {"agent_status": "stopped"})

    def test_malformed_terminal_timestamp_does_not_crash_just_times_out(self):
        client = MagicMock()
        client.list_messages.return_value = _page(
            [{"id": "x1", "timestamp": None, "type": "status_update", "status_update": {"agent_status": "stopped"}}]
        )
        with self.assertRaises(tr.TaskTimeoutError):
            tr.poll_until_settled(client, "t", since_ms=0, timeout=0.3)

    def test_global_timeout_is_enforced_even_if_never_settles(self):
        client = MagicMock()
        client.list_messages.return_value = _page(
            [{"id": "r1", "timestamp": "999999999999", "type": "status_update", "status_update": {"agent_status": "running"}}]
        )
        start = time.monotonic()
        with self.assertRaises(tr.TaskTimeoutError):
            tr.poll_until_settled(client, "t", since_ms=0, timeout=0.6)
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 2.0)

    def test_remaining_budget_is_passed_to_blocking_http_call(self):
        observed = []

        class BlockingClient:
            def list_messages(self, *args, request_timeout=None, **kwargs):
                observed.append(request_timeout)
                time.sleep(request_timeout)
                raise ManusAPIError("network_error", "timed out")

        start = time.monotonic()
        with self.assertRaises(tr.TaskTimeoutError):
            tr.poll_until_settled(BlockingClient(), "t", since_ms=0, timeout=0.1)
        self.assertLess(time.monotonic() - start, 0.3)
        self.assertGreater(observed[0], 0)
        self.assertLessEqual(observed[0], 0.1)

    def test_missing_id_or_timestamp_skipped_without_crashing(self):
        client = MagicMock()
        client.list_messages.return_value = _page(
            [
                {"timestamp": "1000", "type": "tool_used"},  # no id
                {"id": "ok1", "type": "tool_used"},  # no timestamp
                {"id": "s1", "timestamp": "999999999999", "type": "status_update", "status_update": {"agent_status": "error"}},
            ]
        )
        status, _ = tr.poll_until_settled(client, "t", since_ms=0, timeout=2)
        self.assertEqual(status, "error")


class BuildOutcomeTests(unittest.TestCase):
    def test_error_status_surfaces_error_message(self):
        client = MagicMock()
        client.list_messages.return_value = {
            "messages": [{"id": "e1", "type": "error_message", "error_message": {"error_type": "x", "content": "deu ruim"}}]
        }
        outcome = tr.build_outcome(client, "t1", "error", None)
        self.assertEqual(outcome.error_detail, {"error_type": "x", "content": "deu ruim"})
        self.assertEqual(outcome.status, "error")


class TaskOutcomeConfirmVsReplyTests(unittest.TestCase):
    def test_needs_confirm_for_non_message_ask_user(self):
        outcome = tr.TaskOutcome(
            task_id="t", status="waiting", content=None,
            status_detail={"waiting_for_event_type": "gmailSendAction"},
        )
        self.assertTrue(outcome.needs_confirm)
        self.assertFalse(outcome.needs_reply)

    def test_needs_reply_for_message_ask_user(self):
        outcome = tr.TaskOutcome(
            task_id="t", status="waiting", content=None,
            status_detail={"waiting_for_event_type": "messageAskUser"},
        )
        self.assertFalse(outcome.needs_confirm)
        self.assertTrue(outcome.needs_reply)

    def test_stopped_needs_neither(self):
        outcome = tr.TaskOutcome(task_id="t", status="stopped", content="ok")
        self.assertFalse(outcome.needs_confirm)
        self.assertFalse(outcome.needs_reply)


class RunTurnTests(unittest.TestCase):
    def test_create_then_stopped(self):
        client = MagicMock()
        client.create_task.return_value = {"task_id": "newtask"}
        client.list_messages.side_effect = [
            _page([{"id": "s1", "timestamp": "999999999999999", "type": "status_update", "status_update": {"agent_status": "stopped"}}]),
            {"messages": [{"id": "a1", "type": "assistant_message", "assistant_message": {"content": "oi!", "attachments": []}}]},
        ]
        outcome = tr.run_turn(client, None, "ola", timeout=5)
        self.assertEqual(outcome.task_id, "newtask")
        self.assertEqual(outcome.status, "stopped")
        self.assertEqual(outcome.content, "oi!")
        client.create_task.assert_called_once()
        client.send_message.assert_not_called()

    def test_continue_sends_message_not_create(self):
        client = MagicMock()
        client.list_messages.side_effect = [
            _page([{"id": "s1", "timestamp": "999999999999999", "type": "status_update", "status_update": {"agent_status": "stopped"}}]),
            {"messages": []},
        ]
        outcome = tr.run_turn(client, "existing", "continua", timeout=5)
        self.assertEqual(outcome.task_id, "existing")
        client.create_task.assert_not_called()
        client.send_message.assert_called_once()


class ConfirmActionTests(unittest.TestCase):
    def test_delegates_to_client(self):
        client = MagicMock()
        client.confirm_action.return_value = {"ok": True}
        tr.confirm_action(client, "t1", "evt1", {"foo": "bar"})
        client.confirm_action.assert_called_once_with("t1", "evt1", {"foo": "bar"})


if __name__ == "__main__":
    unittest.main()
