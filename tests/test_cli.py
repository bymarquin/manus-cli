import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from manus_cli.cli import OUTPUT_DIR, _collect_project_files, _download_attachments, _looks_like_secret


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


if __name__ == "__main__":
    unittest.main()
