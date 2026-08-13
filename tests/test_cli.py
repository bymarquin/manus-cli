import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from manus_cli.cli import (
    OUTPUT_DIR,
    _collect_project_files,
    _download_attachments,
    _extract_mentions,
    _looks_like_secret,
    _run_slash_command,
)


class LooksLikeSecretTests(unittest.TestCase):
    def test_flags_common_secret_filenames(self):
        for name in (".env", ".env.local", "id_rsa", "id_rsa.pub", "server.pem", "credentials.json", ".npmrc"):
            with self.subTest(name=name):
                self.assertTrue(_looks_like_secret(Path(name)))

    def test_flags_files_inside_secret_dirs(self):
        self.assertTrue(_looks_like_secret(Path("home/user/.ssh/config")))
        self.assertTrue(_looks_like_secret(Path("home/user/.aws/credentials")))

    def test_does_not_flag_normal_files(self):
        for name in ("app.py", "README.md", "package.json", "main.go"):
            with self.subTest(name=name):
                self.assertFalse(_looks_like_secret(Path(name)))


class CollectProjectFilesTests(unittest.TestCase):
    def test_excludes_secrets_by_default_and_includes_with_allow_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text("SECRET=1")
            (root / "app.py").write_text("print(1)")
            (root / ".ssh").mkdir()
            (root / ".ssh" / "id_rsa").write_text("key")

            names = {f.name for f in _collect_project_files(root)}
            self.assertEqual(names, {"app.py"})

            names_allowed = {f.name for f in _collect_project_files(root, allow_secret=True)}
            self.assertEqual(names_allowed, {"app.py", ".env", "id_rsa"})

    def test_excludes_oversized_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            small = root / "small.txt"
            small.write_text("x")
            big = root / "big.bin"
            big.write_bytes(b"0" * (11 * 1024 * 1024))

            names = {f.name for f in _collect_project_files(root)}
            self.assertEqual(names, {"small.txt"})


class DownloadAttachmentsPathTraversalTests(unittest.TestCase):
    def test_traversal_filename_is_confined_to_output_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            import manus_cli.cli as cli_module

            original_output_dir = cli_module.OUTPUT_DIR
            cli_module.OUTPUT_DIR = Path(tmp) / "manus-output"
            try:
                client = MagicMock()
                saved = _download_attachments(
                    client, "task123", [{"url": "https://x/f", "filename": "../../../etc/passwd"}]
                )
                self.assertEqual(len(saved), 1)
                dest = Path(saved[0])
                base = (cli_module.OUTPUT_DIR / "task123").resolve()
                self.assertIn(base, dest.parents)
                self.assertEqual(dest.name, "passwd")
            finally:
                cli_module.OUTPUT_DIR = original_output_dir


class ExtractMentionsTests(unittest.TestCase):
    def test_strips_trailing_punctuation_from_mentioned_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            import os

            old_cwd = os.getcwd()
            os.chdir(tmp)
            try:
                Path("bug.py").write_text("x")
                for line in (
                    "tem erro em @bug.py? confirma.",
                    "olha @bug.py, por favor",
                    "(@bug.py)",
                    "@bug.py",
                ):
                    with self.subTest(line=line):
                        mentions = _extract_mentions(line)
                        self.assertEqual([m.name for m in mentions], ["bug.py"])
            finally:
                os.chdir(old_cwd)

    def test_ignores_mentions_of_nonexistent_files(self):
        self.assertEqual(_extract_mentions("olha @nao_existe.py"), [])


class SlashCommandTests(unittest.TestCase):
    def _client(self):
        client = MagicMock()
        client.task_detail.return_value = {
            "task": {"id": "abc", "title": "Minha Tarefa", "status": "stopped", "task_url": "https://manus.im/app/abc"}
        }
        return client

    def test_help_does_not_change_task_or_exit(self):
        task_id, should_exit = _run_slash_command(self._client(), "abc", "/help")
        self.assertEqual(task_id, "abc")
        self.assertFalse(should_exit)

    def test_use_switches_task(self):
        task_id, should_exit = _run_slash_command(self._client(), None, "/use abc")
        self.assertEqual(task_id, "abc")
        self.assertFalse(should_exit)

    def test_exit_signals_should_exit(self):
        task_id, should_exit = _run_slash_command(self._client(), "abc", "/exit")
        self.assertEqual(task_id, "abc")
        self.assertTrue(should_exit)

    def test_unknown_command_does_not_crash_or_change_task(self):
        task_id, should_exit = _run_slash_command(self._client(), "abc", "/blablabla")
        self.assertEqual(task_id, "abc")
        self.assertFalse(should_exit)


if __name__ == "__main__":
    unittest.main()
