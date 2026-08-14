from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from _helpers import IsolatedConfigTestCase
from prompt_toolkit.document import Document

from manus_cli import cli
from manus_cli.api import ManusAPIError
from manus_cli.coding_agent import AgentResult


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


class ReplCompleterTests(unittest.TestCase):
    def test_slash_completion_only_at_start_and_shows_arguments(self):
        completer = cli._ReplCompleter(Path.cwd())
        completions = list(completer.get_completions(Document("/co"), None))
        self.assertEqual([item.text for item in completions], ["/confirm"])
        self.assertIn("<event_id>", str(completions[0].display))
        self.assertEqual(list(completer.get_completions(Document("texto /co"), None)), [])

    def test_file_completion_respects_gitignore_and_secret_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "app.py").write_text("print(1)")
            (root / "ignored.log").write_text("x")
            (root / ".env").write_text("SECRET=1")
            (root / "run.sh").write_text("#!/bin/sh")
            (root / ".gitignore").write_text("*.log\n")

            completer = cli._ReplCompleter(root)
            completions = list(completer.get_completions(Document("veja @"), None))
            names = {item.text for item in completions}

            self.assertIn("@src/app.py", names)
            self.assertNotIn("@ignored.log", names)
            self.assertNotIn("@.env", names)
            self.assertNotIn("@run.sh", names)

    def test_file_completion_filters_by_prefix_and_is_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "alpha.txt").write_text("a")
            (root / "beta.txt").write_text("b")
            completer = cli._ReplCompleter(root)

            first = list(completer.get_completions(Document("@al"), None))
            (root / "also.txt").write_text("later")
            second = list(completer.get_completions(Document("@al"), None))

            self.assertEqual([item.text for item in first], ["@alpha.txt"])
            self.assertEqual([item.text for item in second], ["@alpha.txt"])

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


class ResolveProjectTests(unittest.TestCase):
    def _client_with_projects(self, projects):
        client = MagicMock()
        client.list_projects.return_value = {"data": projects}
        return client

    def test_uuid_passes_through_without_calling_api(self):
        client = MagicMock()
        result = cli._resolve_project(client, "356d5bc1-fb9f-4fa1-babb-05039dc09d11")
        self.assertEqual(result, "356d5bc1-fb9f-4fa1-babb-05039dc09d11")
        client.list_projects.assert_not_called()

    def test_name_resolves_case_insensitively(self):
        client = self._client_with_projects([{"id": "proj-1", "name": "Backend"}])
        self.assertEqual(cli._resolve_project(client, "backend"), "proj-1")

    def test_unknown_name_raises_clear_error(self):
        client = self._client_with_projects([{"id": "proj-1", "name": "Backend"}])
        with self.assertRaises(ManusAPIError) as ctx:
            cli._resolve_project(client, "notthere")
        self.assertEqual(ctx.exception.code, "project_not_found")
        self.assertIn("Backend", ctx.exception.message)

    def test_ambiguous_name_raises_clear_error(self):
        client = self._client_with_projects([{"id": "p1", "name": "Alpha One"}, {"id": "p2", "name": "Alpha Two"}])
        with self.assertRaises(ManusAPIError) as ctx:
            cli._resolve_project(client, "alpha")
        self.assertEqual(ctx.exception.code, "project_ambiguous")

    def test_none_input_returns_none(self):
        client = MagicMock()
        self.assertIsNone(cli._resolve_project(client, None))
        client.list_projects.assert_not_called()


class TaskLifecycleCommandTests(IsolatedConfigTestCase):
    def test_stop_calls_client_and_reports_success(self):
        client = MagicMock()
        with patch("manus_cli.cli._client") as client_factory:
            client_factory.return_value.__enter__.return_value = client
            exit_code = cli.cmd_stop(["abc"])
        self.assertEqual(exit_code, 0)
        client.stop_task.assert_called_once_with("abc")

    def test_stop_without_task_id_or_last_task_fails_cleanly(self):
        exit_code = cli.cmd_stop([])
        self.assertEqual(exit_code, 1)

    def test_delete_without_yes_cancels_on_non_affirmative_answer(self):
        client = MagicMock()
        with (
            patch("manus_cli.cli._client") as client_factory,
            patch("manus_cli.cli.console.input", return_value="n"),
        ):
            client_factory.return_value.__enter__.return_value = client
            exit_code = cli.cmd_delete(["abc"])
        self.assertEqual(exit_code, 1)
        client.delete_task.assert_not_called()

    def test_delete_with_yes_skips_prompt_and_calls_client(self):
        client = MagicMock()
        with patch("manus_cli.cli._client") as client_factory:
            client_factory.return_value.__enter__.return_value = client
            exit_code = cli.cmd_delete(["abc", "--yes"])
        self.assertEqual(exit_code, 0)
        client.delete_task.assert_called_once_with("abc")

    def test_update_with_no_flags_fails_without_calling_client(self):
        client = MagicMock()
        with patch("manus_cli.cli._client") as client_factory:
            client_factory.return_value.__enter__.return_value = client
            exit_code = cli.cmd_update(["abc"])
        self.assertEqual(exit_code, 1)
        client.update_task.assert_not_called()

    def test_update_passes_title_and_share_through(self):
        client = MagicMock()
        with patch("manus_cli.cli._client") as client_factory:
            client_factory.return_value.__enter__.return_value = client
            exit_code = cli.cmd_update(["abc", "--title", "Novo", "--share", "team"])
        self.assertEqual(exit_code, 0)
        client.update_task.assert_called_once_with(
            "abc", title="Novo", share_visibility="team", visible_in_task_list=None
        )

    def test_update_hide_and_show_are_mutually_exclusive(self):
        with self.assertRaises(SystemExit) as ctx:
            cli.cmd_update(["abc", "--hide", "--show"])
        self.assertEqual(ctx.exception.code, 2)

    def test_project_create_reports_success_with_id(self):
        client = MagicMock()
        client.create_project.return_value = {"project": {"id": "proj-1", "name": "Backend"}}
        with patch("manus_cli.cli._client") as client_factory:
            client_factory.return_value.__enter__.return_value = client
            exit_code = cli.cmd_project(["create", "Backend", "--instruction", "seja conciso"])
        self.assertEqual(exit_code, 0)
        client.create_project.assert_called_once_with("Backend", instruction="seja conciso")

    def test_project_list_calls_client(self):
        client = MagicMock()
        client.list_projects.return_value = {"data": []}
        with patch("manus_cli.cli._client") as client_factory:
            client_factory.return_value.__enter__.return_value = client
            exit_code = cli.cmd_project(["list"])
        self.assertEqual(exit_code, 0)
        client.list_projects.assert_called_once()

    def test_project_requires_subcommand(self):
        with self.assertRaises(SystemExit) as ctx:
            cli.cmd_project([])
        self.assertEqual(ctx.exception.code, 2)


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

    def test_stop_without_active_task_fails_cleanly(self):
        task_id, should_exit = cli._run_slash_command(self._client(), None, "/stop")
        self.assertIsNone(task_id)
        self.assertFalse(should_exit)

    def test_stop_calls_client_and_keeps_task_active(self):
        client = self._client()
        task_id, should_exit = cli._run_slash_command(client, "abc", "/stop")
        client.stop_task.assert_called_once_with("abc")
        self.assertEqual(task_id, "abc")
        self.assertFalse(should_exit)


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


class CodingAgentCliTests(IsolatedConfigTestCase):
    @patch("manus_cli.cli.CodingAgent")
    @patch("manus_cli.cli.WorkspaceTools")
    @patch("manus_cli.cli._client")
    def test_code_wires_balanced_mode_and_json_stdout(self, make_client, tools_class, agent_class):
        client = make_client.return_value
        agent_class.return_value.run.return_value = AgentResult(
            task_id="task-code",
            success=True,
            final_message="feito",
            changed_files=["app.py"],
            validated=True,
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            exit_code = cli.cmd_code(["corrija", "o bug", "--json", "--root", "."])

        self.assertEqual(exit_code, 0)
        payload = json.loads(buf.getvalue())
        self.assertTrue(payload["success"])
        self.assertEqual(payload["changed_files"], ["app.py"])
        self.assertEqual(agent_class.call_args.args[2].mode.value, "balanced")
        self.assertIsNone(agent_class.call_args.kwargs["on_step"])
        agent_class.return_value.run.assert_called_once_with("corrija o bug")
        client.close.assert_called_once()

    @patch("manus_cli.cli.err_console")
    @patch("sys.stdin")
    @patch("manus_cli.cli.CodingAgent")
    @patch("manus_cli.cli.WorkspaceTools")
    @patch("manus_cli.cli._client")
    def test_json_mode_prompts_for_missing_objective_on_stderr_not_stdout(
        self, make_client, tools_class, agent_class, stdin_mock, err_console_mock
    ):
        # Regression: an interactive `manus code --json` with no prompt argument
        # used to fall back to console.input() (stdout) for the objective prompt,
        # breaking the tested "--json is exclusively JSON on stdout" guarantee.
        stdin_mock.isatty.return_value = True
        err_console_mock.input.return_value = "corrija o bug"
        agent_class.return_value.run.return_value = AgentResult(
            task_id="task-code", success=True, final_message="feito", validated=True
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            exit_code = cli.cmd_code(["--json", "--root", "."])

        self.assertEqual(exit_code, 0)
        err_console_mock.input.assert_called_once()
        payload = json.loads(buf.getvalue())
        self.assertTrue(payload["success"])
        agent_class.return_value.run.assert_called_once_with("corrija o bug")

    @patch("manus_cli.cli._client")
    def test_code_rejects_invalid_limits_before_network(self, make_client):
        self.assertEqual(cli.cmd_code(["tarefa", "--max-steps", "0"]), 1)
        self.assertEqual(cli.cmd_code(["tarefa", "--command-timeout", "0"]), 1)
        make_client.assert_not_called()

    def test_noninteractive_approval_denies_without_yes(self):
        approval = cli._ConsoleApproval(yes=False, interactive=False)
        self.assertFalse(approval.approve("run_command", {"argv": ["npm", "install"]}, "instalação"))

    def test_yes_approval_accepts_confirmable_action(self):
        approval = cli._ConsoleApproval(yes=True, interactive=False)
        self.assertTrue(approval.approve("run_command", {"argv": ["npm", "install"]}, "instalação"))

    def test_code_is_registered_as_subcommand(self):
        self.assertIs(cli._SUBCOMMANDS["code"], cli.cmd_code)


class CheckProvisioningTests(unittest.TestCase):
    def test_reports_ok_and_cleans_up_when_task_is_findable(self):
        client = MagicMock()
        client.create_task.return_value = {"task_id": "t1", "request_id": "r1"}
        self.assertTrue(cli._check_provisioning(client))
        client.task_detail.assert_called_once_with("t1")
        client.delete_task.assert_called_once_with("t1")

    def test_fails_when_task_detail_cant_find_a_just_created_task(self):
        client = MagicMock()
        client.create_task.return_value = {"task_id": "t1", "request_id": "r1"}
        client.task_detail.side_effect = ManusAPIError("not_found", "task not found")
        self.assertFalse(cli._check_provisioning(client))
        client.delete_task.assert_not_called()

    def test_fails_when_create_itself_errors(self):
        client = MagicMock()
        client.create_task.side_effect = ManusAPIError("rate_limited", "too many requests")
        self.assertFalse(cli._check_provisioning(client))
        client.task_detail.assert_not_called()


if __name__ == "__main__":
    unittest.main()
