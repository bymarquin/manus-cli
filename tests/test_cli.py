from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock

from _helpers import IsolatedConfigTestCase

from manus_cli import cli
from manus_cli.api import ManusAPIError


class ExtractMentionsTests(IsolatedConfigTestCase):
    def test_strips_trailing_punctuation_from_mentioned_paths(self):
        import os

        with tempfile.TemporaryDirectory() as tmp:
            old_cwd = os.getcwd()
            os.chdir(tmp)
            try:
                Path("bug.py").write_text("x")
                for line in ("tem erro em @bug.py? confirma.", "olha @bug.py, por favor", "(@bug.py)", "@bug.py"):
                    with self.subTest(line=line):
                        mentions = cli._extract_mentions(line)
                        self.assertEqual([m.name for m in mentions], ["bug.py"])
            finally:
                os.chdir(old_cwd)

    def test_ignores_mentions_of_nonexistent_files(self):
        self.assertEqual(cli._extract_mentions("olha @nao_existe.py"), [])

    def test_applies_secret_policy_to_mentions(self):
        import os

        with tempfile.TemporaryDirectory() as tmp:
            old_cwd = os.getcwd()
            os.chdir(tmp)
            try:
                Path(".env").write_text("SECRET=1")
                self.assertEqual(cli._extract_mentions("olha @.env"), [])
                self.assertEqual(len(cli._extract_mentions("olha @.env", allow_secret=True)), 1)
            finally:
                os.chdir(old_cwd)


class DownloadAttachmentsPathTraversalTests(unittest.TestCase):
    def test_traversal_filename_is_confined_to_output_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            original_output_dir = cli.OUTPUT_DIR
            cli.OUTPUT_DIR = Path(tmp) / "manus-output"
            try:
                client = MagicMock()
                client.download_file.side_effect = lambda url, dest: dest
                saved = cli._download_attachments(
                    client, "task123", [{"url": "https://x/f", "filename": "../../../etc/passwd"}]
                )
                self.assertEqual(len(saved), 1)
                dest = Path(saved[0])
                base = (cli.OUTPUT_DIR / "task123").resolve()
                self.assertIn(base, dest.parents)
                self.assertEqual(dest.name, "passwd")
            finally:
                cli.OUTPUT_DIR = original_output_dir

    def test_download_failure_is_reported_not_fatal(self):
        client = MagicMock()
        client.download_file.side_effect = ManusAPIError("network_error", "boom")
        saved = cli._download_attachments(client, "task123", [{"url": "https://x/f", "filename": "a.txt"}])
        self.assertEqual(saved, [])


class ResolveConnectorsTests(unittest.TestCase):
    def _client_with_connectors(self, connectors):
        client = MagicMock()
        client.list_connectors.return_value = {"data": connectors}
        return client

    def test_uuid_passes_through_without_calling_api(self):
        client = MagicMock()
        result = cli._resolve_connectors(client, ["356d5bc1-fb9f-4fa1-babb-05039dc09d11"])
        self.assertEqual(result, ["356d5bc1-fb9f-4fa1-babb-05039dc09d11"])
        client.list_connectors.assert_not_called()

    def test_name_resolves_case_insensitively(self):
        client = self._client_with_connectors([{"id": "uuid-1", "name": "GitHub"}])
        result = cli._resolve_connectors(client, ["github"])
        self.assertEqual(result, ["uuid-1"])

    def test_unknown_name_raises_clear_error(self):
        client = self._client_with_connectors([{"id": "uuid-1", "name": "GitHub"}])
        with self.assertRaises(ManusAPIError) as ctx:
            cli._resolve_connectors(client, ["notthere"])
        self.assertEqual(ctx.exception.code, "connector_not_found")
        self.assertIn("GitHub", ctx.exception.message)

    def test_ambiguous_name_raises_clear_error(self):
        client = self._client_with_connectors([{"id": "u1", "name": "Gmail Work"}, {"id": "u2", "name": "Gmail Personal"}])
        with self.assertRaises(ManusAPIError) as ctx:
            cli._resolve_connectors(client, ["gmail"])
        self.assertEqual(ctx.exception.code, "connector_ambiguous")

    def test_none_input_returns_none(self):
        client = MagicMock()
        self.assertIsNone(cli._resolve_connectors(client, None))
        client.list_connectors.assert_not_called()


class SlashCommandTests(IsolatedConfigTestCase):
    def _client(self):
        client = MagicMock()
        client.task_detail.return_value = {
            "task": {"id": "abc", "title": "Minha Tarefa", "status": "stopped", "task_url": "https://manus.im/app/abc"}
        }
        return client

    def test_help_does_not_change_task_or_exit(self):
        task_id, should_exit = cli._run_slash_command(self._client(), "abc", "/help")
        self.assertEqual(task_id, "abc")
        self.assertFalse(should_exit)

    def test_use_switches_task_and_only_touches_isolated_state(self):
        task_id, should_exit = cli._run_slash_command(self._client(), None, "/use abc")
        self.assertEqual(task_id, "abc")
        self.assertFalse(should_exit)
        # This is the regression the isolation fixture exists for: /use persists
        # via config.save_last_task, which must land in the temp dir, not ~/.config.
        from manus_cli import config

        self.assertEqual(config.load_last_task(), "abc")

    def test_exit_signals_should_exit(self):
        task_id, should_exit = cli._run_slash_command(self._client(), "abc", "/exit")
        self.assertEqual(task_id, "abc")
        self.assertTrue(should_exit)

    def test_unknown_command_does_not_crash_or_change_task(self):
        task_id, should_exit = cli._run_slash_command(self._client(), "abc", "/blablabla")
        self.assertEqual(task_id, "abc")
        self.assertFalse(should_exit)

    def test_confirm_without_active_task_fails_cleanly(self):
        task_id, should_exit = cli._run_slash_command(self._client(), None, "/confirm evt1")
        self.assertIsNone(task_id)
        self.assertFalse(should_exit)

    def test_confirm_calls_client_with_parsed_json_input(self):
        client = self._client()
        client.confirm_action.return_value = {"ok": True}
        _task_id, should_exit = cli._run_slash_command(client, "abc", '/confirm evt1 {"foo": "bar"}')
        client.confirm_action.assert_called_once_with("abc", "evt1", {"foo": "bar"})
        self.assertFalse(should_exit)

    def test_confirm_with_invalid_json_reports_error_without_calling_client(self):
        client = self._client()
        _task_id, _should_exit = cli._run_slash_command(client, "abc", "/confirm evt1 {not json")
        client.confirm_action.assert_not_called()


class JsonOutputStdoutDisciplineTests(IsolatedConfigTestCase):
    """P1-11: in --json mode, stdout must contain exactly one JSON line and nothing else."""

    def test_run_turn_json_mode_prints_exactly_one_json_line(self):
        client = MagicMock()
        client.create_task.return_value = {"task_id": "t1"}
        client.list_messages.side_effect = [
            {
                "messages": [{"id": "s1", "timestamp": "999999999999999", "type": "status_update", "status_update": {"agent_status": "stopped"}}],
                "has_more": False,
                "next_cursor": None,
            },
            {"messages": [{"id": "a1", "type": "assistant_message", "assistant_message": {"content": "oi", "attachments": []}}]},
        ]
        buf = io.StringIO()
        with redirect_stdout(buf):
            _task_id, exit_code = cli._run_turn(client, None, "ola", timeout=5, json_output=True)
        lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1)
        parsed = json.loads(lines[0])
        self.assertEqual(parsed["status"], "stopped")
        self.assertEqual(exit_code, 0)

    def test_waiting_status_gives_exit_code_2_not_zero(self):
        client = MagicMock()
        client.list_messages.side_effect = [
            {
                "messages": [
                    {
                        "id": "w1",
                        "timestamp": "999999999999999",
                        "type": "status_update",
                        "status_update": {
                            "agent_status": "waiting",
                            "waiting_for_event_id": "evt1",
                            "waiting_for_event_type": "gmailSendAction",
                        },
                    }
                ],
                "has_more": False,
                "next_cursor": None,
            },
            {"messages": []},
        ]
        buf = io.StringIO()
        with redirect_stdout(buf):
            _, exit_code = cli._run_turn(client, "t1", "continua", timeout=5, json_output=True)
        self.assertEqual(exit_code, 2)

    def test_error_status_gives_exit_code_1_not_zero(self):
        client = MagicMock()
        client.list_messages.side_effect = [
            {
                "messages": [{"id": "e1", "timestamp": "999999999999999", "type": "status_update", "status_update": {"agent_status": "error"}}],
                "has_more": False,
                "next_cursor": None,
            },
            {"messages": []},
        ]
        buf = io.StringIO()
        with redirect_stdout(buf):
            _, exit_code = cli._run_turn(client, "t1", "continua", timeout=5, json_output=True)
        self.assertEqual(exit_code, 1)


class RunTurnNonJsonExitCodesTests(IsolatedConfigTestCase):
    def test_stopped_is_exit_zero(self):
        client = MagicMock()
        client.list_messages.side_effect = [
            {
                "messages": [{"id": "s1", "timestamp": "999999999999999", "type": "status_update", "status_update": {"agent_status": "stopped"}}],
                "has_more": False,
                "next_cursor": None,
            },
            {"messages": []},
        ]
        _, exit_code = cli._run_turn(client, "t1", "oi", timeout=5, json_output=False)
        self.assertEqual(exit_code, 0)

    def test_waiting_is_not_silently_zero(self):
        client = MagicMock()
        client.list_messages.side_effect = [
            {
                "messages": [
                    {
                        "id": "w1",
                        "timestamp": "999999999999999",
                        "type": "status_update",
                        "status_update": {"agent_status": "waiting", "waiting_for_event_type": "messageAskUser"},
                    }
                ],
                "has_more": False,
                "next_cursor": None,
            },
            {"messages": []},
        ]
        _, exit_code = cli._run_turn(client, "t1", "oi", timeout=5, json_output=False)
        self.assertNotEqual(exit_code, 0)
        self.assertEqual(exit_code, 2)

    def test_error_is_not_silently_zero(self):
        client = MagicMock()
        client.list_messages.side_effect = [
            {
                "messages": [{"id": "e1", "timestamp": "999999999999999", "type": "status_update", "status_update": {"agent_status": "error"}}],
                "has_more": False,
                "next_cursor": None,
            },
            {"messages": []},
        ]
        _, exit_code = cli._run_turn(client, "t1", "oi", timeout=5, json_output=False)
        self.assertNotEqual(exit_code, 0)
        self.assertEqual(exit_code, 1)


class SubcommandArgparseTests(unittest.TestCase):
    """Bad input must produce a clean argparse usage error (SystemExit(2)), never
    a raw uncaught exception like ValueError/IndexError."""

    def test_history_rejects_non_integer_cleanly(self):
        with self.assertRaises(SystemExit) as ctx:
            cli.cmd_history(["not-a-number"])
        self.assertEqual(ctx.exception.code, 2)

    def test_history_rejects_negative_with_clean_message(self):
        self.assertEqual(cli.cmd_history(["-1"]), 1)

    def test_use_requires_task_id(self):
        with self.assertRaises(SystemExit) as ctx:
            cli.cmd_use([])
        self.assertEqual(ctx.exception.code, 2)

    def test_alias_requires_subcommand(self):
        with self.assertRaises(SystemExit) as ctx:
            cli.cmd_alias([])
        self.assertEqual(ctx.exception.code, 2)

    def test_connector_rejects_unknown_subcommand(self):
        with self.assertRaises(SystemExit) as ctx:
            cli.cmd_connector(["bogus"])
        self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
