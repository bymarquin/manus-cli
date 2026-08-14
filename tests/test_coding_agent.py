from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from manus_cli.agent_policy import PolicyEngine
from manus_cli.coding_agent import (
    CODING_DECISION_SCHEMA,
    AgentDecision,
    CodingAgent,
    CodingTurnError,
    ManusCodingAdapter,
    _parse_decision,
)
from manus_cli.task_runner import TaskOutcome
from manus_cli.workspace_tools import ToolResult, WorkspaceTools


def action(tool, arguments, summary="ação"):
    return AgentDecision("action", summary, tool, arguments, "")


def final(message="feito"):
    return AgentDecision("final", "concluído", "", {}, message)


class FakeModel:
    def __init__(self, decisions):
        self.decisions = iter(decisions)
        self.calls = []

    def next_decision(self, task_id, message, *, timeout, agent_profile):
        self.calls.append((task_id, message, timeout, agent_profile))
        return task_id or "task-1", next(self.decisions)


class FakeTools:
    root = Path("/workspace")

    def __init__(self, results=None):
        self.results = results or {}
        self.calls = []

    def execute(self, tool, arguments):
        self.calls.append((tool, arguments))
        if tool == "git_diff":
            return ToolResult(True, "diff local")
        return self.results.get(tool, ToolResult(True, "ok", {"exit_code": 0}))


class FakeApproval:
    def __init__(self, answer=True):
        self.answer = answer
        self.calls = []

    def approve(self, tool, arguments, reason):
        self.calls.append((tool, arguments, reason))
        return self.answer


class CodingAgentTests(unittest.TestCase):
    def test_action_result_is_returned_to_same_task_then_final(self):
        model = FakeModel([
            action("read_file", {"path": "app.py"}, "ler app"),
            action("replace_text", {"path": "app.py", "old": "a", "new": "b"}, "editar"),
            action("run_command", {"argv": ["python3", "-m", "unittest"]}, "testar"),
            final("corrigido e testado"),
        ])
        tools = FakeTools()
        agent = CodingAgent(model, tools, PolicyEngine(), FakeApproval())

        result = agent.run("corrija")

        self.assertTrue(result.success)
        self.assertEqual(result.task_id, "task-1")
        self.assertEqual(result.changed_files, ["app.py"])
        self.assertEqual(result.commands, [["python3", "-m", "unittest"]])
        self.assertTrue(result.validated)
        self.assertEqual(result.workspace_diff, "diff local")
        self.assertEqual([call[0] for call in model.calls], [None, "task-1", "task-1", "task-1"])
        self.assertIn('"tool": "read_file"', model.calls[1][1])

    def test_denied_action_is_not_executed_and_model_can_recover(self):
        model = FakeModel([action("run_command", {"argv": ["git", "push"]}), final()])
        tools = FakeTools()
        result = CodingAgent(model, tools, PolicyEngine(), FakeApproval()).run("trabalhe")
        self.assertTrue(result.success)
        self.assertNotIn("run_command", [call[0] for call in tools.calls])
        self.assertFalse(result.steps[0].ok)
        self.assertIn("bloqueada", result.steps[0].detail)

    def test_confirmation_decline_is_observable(self):
        model = FakeModel([action("run_command", {"argv": ["npm", "install"]}), final()])
        approval = FakeApproval(False)
        result = CodingAgent(model, FakeTools(), PolicyEngine(), approval).run("trabalhe")
        self.assertEqual(len(approval.calls), 1)
        self.assertIn("recusada", result.steps[0].detail)

    def test_step_limit_fails_without_fake_success(self):
        model = FakeModel([action("read_file", {"path": "a"}), action("read_file", {"path": "b"})])
        result = CodingAgent(model, FakeTools(), PolicyEngine(), FakeApproval(), max_steps=2).run("trabalhe")
        self.assertFalse(result.success)
        self.assertIn("limite de 2", result.error)

    def test_turn_error_is_returned_with_task_id(self):
        class BrokenModel:
            def next_decision(self, *args, **kwargs):
                raise CodingTurnError("sem schema", "task-broken")

        result = CodingAgent(BrokenModel(), FakeTools(), PolicyEngine(), FakeApproval()).run("trabalhe")
        self.assertFalse(result.success)
        self.assertEqual(result.task_id, "task-broken")

    def test_recoverable_protocol_error_retries_same_task(self):
        class RecoveringModel:
            def __init__(self):
                self.calls = 0

            def next_decision(self, task_id, message, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise CodingTurnError("json inválido", "task-retry", recoverable=True)
                self.retry_message = message
                return task_id, final("recuperado")

        model = RecoveringModel()
        result = CodingAgent(model, FakeTools(), PolicyEngine(), FakeApproval()).run("trabalhe")
        self.assertTrue(result.success)
        self.assertEqual(result.task_id, "task-retry")
        self.assertEqual(result.steps[0].tool, "protocol")
        self.assertIn("previous structured decision was invalid", model.retry_message)

    def test_offline_integration_edits_and_validates_real_temp_workspace(self):
        executable = "python" if os.name == "nt" else "python3"
        if shutil.which(executable) is None:
            self.skipTest(f"{executable} não está no PATH")
        model = FakeModel([
            action("write_file", {"path": "app.py", "content": "answer = 42\n"}, "criar arquivo"),
            action(
                "write_file",
                {
                    "path": "test_app.py",
                    "content": "import unittest\nfrom app import answer\n\nclass AppTest(unittest.TestCase):\n    def test_answer(self):\n        self.assertEqual(answer, 42)\n",
                },
                "criar teste",
            ),
            action("run_command", {"argv": [executable, "-m", "unittest", "-q"]}, "rodar teste"),
            final("arquivo criado e validado"),
        ])
        with tempfile.TemporaryDirectory() as temp:
            tools = WorkspaceTools(Path(temp), command_timeout=5)
            result = CodingAgent(model, tools, PolicyEngine(), FakeApproval()).run("crie app")
            content = (Path(temp) / "app.py").read_text()
        self.assertTrue(result.success)
        self.assertTrue(result.validated)
        self.assertEqual(content, "answer = 42\n")


class DecisionParsingTests(unittest.TestCase):
    def test_valid_action(self):
        raw = {
            "kind": "action", "summary": "ler", "tool": "read_file",
            "arguments_json": '{"path":"x.py"}', "final_message": "",
        }
        self.assertEqual(_parse_decision(raw).arguments, {"path": "x.py"})

    def test_invalid_shapes_raise(self):
        invalid = [
            None,
            {"kind": "action"},
            {"kind": "action", "summary": "x", "tool": "bad", "arguments_json": "{}", "final_message": ""},
            {"kind": "action", "summary": "x", "tool": "read_file", "arguments_json": "[]", "final_message": ""},
            {"kind": "final", "summary": "x", "tool": "read_file", "arguments_json": "{}", "final_message": "x"},
        ]
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(CodingTurnError):
                _parse_decision(raw)


class ManusCodingAdapterTests(unittest.TestCase):
    def test_extracts_decision_and_passes_schema(self):
        client = MagicMock()
        value = {
            "kind": "action", "summary": "ler", "tool": "read_file",
            "arguments_json": json.dumps({"path": "x.py"}), "final_message": "",
        }
        outcome = TaskOutcome(
            task_id="t1", status="stopped", content="",
            structured_output={"success": True, "value": value, "error": None},
        )
        with patch("manus_cli.coding_agent.task_runner.run_turn", return_value=outcome) as run_turn:
            task_id, decision = ManusCodingAdapter(client).next_decision(
                None, "prompt", timeout=20, agent_profile="manus-1.6-max"
            )
        self.assertEqual(task_id, "t1")
        self.assertEqual(decision.tool, "read_file")
        run_turn.assert_called_once_with(
            client,
            None,
            "prompt",
            20,
            structured_output_schema=CODING_DECISION_SCHEMA,
            agent_profile="manus-1.6-max",
        )

    def test_failed_or_missing_extraction_raises(self):
        client = MagicMock()
        outcomes = [
            TaskOutcome(task_id="t1", status="stopped", content="", structured_output=None),
            TaskOutcome(
                task_id="t1", status="stopped", content="",
                structured_output={"success": False, "value": {}, "error": "extract failed"},
            ),
        ]
        for outcome in outcomes:
            with self.subTest(outcome=outcome), patch(
                "manus_cli.coding_agent.task_runner.run_turn", return_value=outcome
            ), self.assertRaises(CodingTurnError):
                ManusCodingAdapter(client).next_decision(None, "prompt", timeout=20, agent_profile=None)
