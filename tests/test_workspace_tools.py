from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from manus_cli.workspace_tools import SubprocessCommandRunner, ToolResult, WorkspaceTools


class FakeRunner:
    def __init__(self):
        self.calls = []

    def run(self, argv, cwd, timeout):
        self.calls.append((argv, cwd, timeout))
        return ToolResult(
            True,
            "ok",
            {"stdout": "fake output\n", "stderr": "", "exit_code": 0, "truncated": False},
        )


class WorkspaceToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "src").mkdir()
        (self.root / "src" / "app.py").write_text("one\ntarget line\nthree\n", encoding="utf-8")
        (self.root / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
        (self.root / "ignored.txt").write_text("hidden", encoding="utf-8")
        (self.root / ".env").write_text("TOKEN=secret", encoding="utf-8")
        self.runner = FakeRunner()
        self.tools = WorkspaceTools(self.root, command_runner=self.runner, command_timeout=12)

    def tearDown(self):
        self.temp.cleanup()

    def test_filesystem_root_cannot_be_workspace(self):
        with self.assertRaisesRegex(ValueError, "raiz do sistema"):
            WorkspaceTools(Path(Path.cwd().anchor))

    def test_list_respects_gitignore_and_secret_filters(self):
        result = self.tools.execute("list_files", {"path": "."})
        self.assertTrue(result.ok)
        self.assertIn("src/app.py", result.content)
        self.assertNotIn("ignored.txt", result.content)
        self.assertNotIn(".env", result.content)

    def test_read_file_returns_numbered_slice(self):
        result = self.tools.execute("read_file", {"path": "src/app.py", "start_line": 2, "max_lines": 1})
        self.assertTrue(result.ok)
        self.assertEqual(result.content, "2: target line")
        self.assertTrue(result.metadata["truncated"])

    def test_search_literal_and_regex(self):
        literal = self.tools.execute("search", {"path": ".", "query": "target"})
        regex = self.tools.execute("search", {"path": "src", "query": "t.rget", "regex": True})
        self.assertIn("src/app.py:2:target line", literal.content)
        self.assertIn("src/app.py:2:target line", regex.content)

    def test_write_and_replace_are_atomic_observable_operations(self):
        written = self.tools.execute("write_file", {"path": "src/new.py", "content": "value = 1\n"})
        replaced = self.tools.execute(
            "replace_text",
            {"path": "src/new.py", "old": "1", "new": "2", "expected_occurrences": 1},
        )
        self.assertTrue(written.ok)
        self.assertTrue(replaced.ok)
        self.assertEqual((self.root / "src" / "new.py").read_text(), "value = 2\n")

    def test_replace_rejects_ambiguous_occurrence(self):
        result = self.tools.execute(
            "replace_text",
            {"path": "src/app.py", "old": "e", "new": "x", "expected_occurrences": 1},
        )
        self.assertFalse(result.ok)
        self.assertIn("aparece", result.content)

    def test_traversal_secret_and_symlink_escape_are_blocked(self):
        outside = self.root.parent / f"outside-{self.root.name}.txt"
        outside.write_text("outside")
        try:
            (self.root / "escape").symlink_to(outside)
            for path in ("../x", ".env", "escape"):
                with self.subTest(path=path):
                    result = self.tools.execute("read_file", {"path": path})
                    self.assertFalse(result.ok)
        finally:
            outside.unlink()

    def test_binary_and_oversized_read_are_rejected(self):
        (self.root / "binary.dat").write_bytes(b"a\x00b")
        binary = self.tools.execute("read_file", {"path": "binary.dat"})
        self.assertFalse(binary.ok)
        self.assertIn("binário", binary.content)

    def test_run_command_uses_relative_cwd_and_caps_timeout(self):
        result = self.tools.execute(
            "run_command", {"argv": ["git", "status"], "cwd": "src", "timeout": 60}
        )
        self.assertTrue(result.ok)
        argv, cwd, timeout = self.runner.calls[-1]
        self.assertEqual(argv, ["git", "status"])
        self.assertEqual(cwd, (self.root / "src").resolve())
        self.assertEqual(timeout, 12)
        self.assertEqual(result.metadata["cwd"], "src")

    def test_local_executable_must_exist_inside_workspace(self):
        local_bin = self.root / ".venv" / "bin"
        local_bin.mkdir(parents=True)
        (local_bin / "python").write_text("fixture")
        allowed = self.tools.execute(
            "run_command", {"argv": [".venv/bin/python", "-m", "unittest"], "cwd": "."}
        )
        escaped = self.tools.execute(
            "run_command", {"argv": ["../python", "-m", "unittest"], "cwd": "."}
        )
        self.assertTrue(allowed.ok)
        self.assertFalse(escaped.ok)

    def test_unexpected_arguments_are_rejected(self):
        result = self.tools.execute("read_file", {"path": "src/app.py", "surprise": True})
        self.assertFalse(result.ok)
        self.assertIn("inesperados", result.content)

    def test_git_diff_uses_read_only_commands(self):
        result = self.tools.execute("git_diff", {})
        self.assertTrue(result.ok)
        self.assertEqual(self.runner.calls[0][0], ["git", "status", "--short"])
        self.assertEqual(self.runner.calls[1][0], ["git", "diff"])


class SubprocessCommandRunnerTests(unittest.TestCase):
    def test_environment_is_sanitized(self):
        runner = SubprocessCommandRunner()
        with tempfile.TemporaryDirectory() as temp:
            old = os.environ.get("MANUS_TEST_SECRET_TOKEN")
            os.environ["MANUS_TEST_SECRET_TOKEN"] = "never-print"
            try:
                result = runner.run(
                    ["python3", "-c", "import os; print(os.getenv('MANUS_TEST_SECRET_TOKEN', 'clean'))"],
                    Path(temp),
                    3,
                )
            finally:
                if old is None:
                    os.environ.pop("MANUS_TEST_SECRET_TOKEN", None)
                else:
                    os.environ["MANUS_TEST_SECRET_TOKEN"] = old
        self.assertTrue(result.ok)
        self.assertEqual(result.metadata["stdout"].strip(), "clean")

    def test_relative_path_entries_cannot_shadow_executable(self):
        runner = SubprocessCommandRunner()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fake = root / "python3"
            fake.write_text("not executable")
            old_path = os.environ.get("PATH", "")
            os.environ["PATH"] = "." + os.pathsep + old_path
            try:
                result = runner.run(
                    ["python3", "-c", "print('system')"],
                    root,
                    3,
                )
            finally:
                os.environ["PATH"] = old_path
        self.assertTrue(result.ok)
        self.assertEqual(result.metadata["stdout"].strip(), "system")

    def test_timeout_returns_observable_failure(self):
        runner = SubprocessCommandRunner()
        with tempfile.TemporaryDirectory() as temp:
            started = time.monotonic()
            result = runner.run(["python3", "-c", "import time; time.sleep(3)"], Path(temp), 0.1)
        self.assertFalse(result.ok)
        self.assertTrue(result.metadata["timed_out"])
        self.assertLess(time.monotonic() - started, 2)
