from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from manus_cli import files as F
from manus_cli.api import ManusAPIError


class LooksLikeSecretTests(unittest.TestCase):
    def test_flags_common_secret_filenames(self):
        for name in (".env", ".env.local", "id_rsa", "id_rsa.pub", "server.pem", "credentials.json", ".npmrc"):
            with self.subTest(name=name):
                self.assertTrue(F.looks_like_secret(Path(name)))

    def test_flags_files_inside_secret_dirs(self):
        self.assertTrue(F.looks_like_secret(Path("home/user/.ssh/config")))
        self.assertTrue(F.looks_like_secret(Path("home/user/.aws/credentials")))

    def test_does_not_flag_normal_files(self):
        for name in ("app.py", "README.md", "package.json", "main.go"):
            with self.subTest(name=name):
                self.assertFalse(F.looks_like_secret(Path(name)))


class RejectedTypeTests(unittest.TestCase):
    def test_rejects_executables(self):
        for name in ("run.exe", "install.sh", "setup.bat", "app.dmg"):
            with self.subTest(name=name):
                self.assertTrue(F.is_rejected_type(Path(name)))

    def test_accepts_normal_types(self):
        self.assertFalse(F.is_rejected_type(Path("app.py")))


class GitignoreMatcherTests(unittest.TestCase):
    def test_basic_patterns(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".gitignore").write_text("*.log\nbuild/\n!keep.log\n")
            gi = F.GitignoreMatcher.load(root)
            self.assertTrue(gi.matches(Path("debug.log")))
            self.assertTrue(gi.matches(Path("build/out.bin")))
            self.assertFalse(gi.matches(Path("keep.log")))  # negation
            self.assertFalse(gi.matches(Path("app.py")))

    def test_no_gitignore_matches_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            gi = F.GitignoreMatcher.load(Path(tmp))
            self.assertFalse(gi.matches(Path("anything.txt")))

    def test_double_star_anchoring_and_nested_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".gitignore").write_text("/root-only.txt\n**/cache/*.tmp\n")
            (root / "pkg").mkdir()
            (root / "pkg" / ".gitignore").write_text("*.generated\n!keep.generated\n")
            gi = F.GitignoreMatcher.load(root)
            self.assertTrue(gi.matches(Path("root-only.txt")))
            self.assertFalse(gi.matches(Path("pkg/root-only.txt")))
            self.assertTrue(gi.matches(Path("a/cache/drop.tmp")))
            self.assertTrue(gi.matches(Path("pkg/drop.generated")))
            self.assertFalse(gi.matches(Path("pkg/keep.generated")))


class SelectProjectFilesTests(unittest.TestCase):
    def test_secrets_excluded_by_default_included_with_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text("SECRET=1")
            (root / "app.py").write_text("print(1)")

            result = F.select_project_files(root)
            names = {f.relative_path.name for f in result.files}
            self.assertEqual(names, {"app.py"})
            self.assertTrue(any(s.relative_path.name == ".env" for s in result.skipped))

            result2 = F.select_project_files(root, allow_secret=True)
            names2 = {f.relative_path.name for f in result2.files}
            self.assertEqual(names2, {"app.py", ".env"})

    def test_gitignore_respected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".gitignore").write_text("*.log\n")
            (root / "debug.log").write_text("x")
            (root / "app.py").write_text("x")
            result = F.select_project_files(root)
            names = {f.relative_path.name for f in result.files}
            self.assertEqual(names, {"app.py", ".gitignore"})

    def test_gitignore_can_be_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".gitignore").write_text("*.log\n")
            (root / "debug.log").write_text("x")
            result = F.select_project_files(root, respect_gitignore=False)
            names = {f.relative_path.name for f in result.files}
            self.assertIn("debug.log", names)

    def test_symlink_escaping_root_is_skipped_symlink_inside_is_followed(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            root = Path(tmp)
            (root / "app.py").write_text("x")
            (Path(outside) / "secret.txt").write_text("outside data")
            (root / "escape").symlink_to(Path(outside) / "secret.txt")
            (root / "inside_link").symlink_to(root / "app.py")

            result = F.select_project_files(root)
            names = {f.relative_path.name for f in result.files}
            self.assertNotIn("escape", names)
            self.assertIn("inside_link", names)
            self.assertTrue(any(s.relative_path.name == "escape" for s in result.skipped))

    def test_name_collisions_get_disambiguated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app.py").write_text("a")
            (root / "src").mkdir()
            (root / "src" / "app.py").write_text("b")
            result = F.select_project_files(root)
            display_names = sorted(f.display_name for f in result.files)
            self.assertEqual(len(display_names), len(set(display_names)), "display names must not collide")
            self.assertIn("app.py", display_names)

    def test_deterministic_ordering(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("z.py", "a.py", "m.py"):
                (root / name).write_text("x")
            names1 = [f.relative_path.as_posix() for f in F.select_project_files(root).files]
            names2 = [f.relative_path.as_posix() for f in F.select_project_files(root).files]
            self.assertEqual(names1, names2)
            self.assertEqual(names1, sorted(names1))

    def test_oversized_file_reported_as_skipped_not_silently_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "big.bin").write_bytes(b"0" * 1024)
            with patch.object(F, "MAX_FILE_BYTES", 500):
                result = F.select_project_files(root)
            self.assertEqual(result.files, [])
            self.assertEqual(len(result.skipped), 1)
            self.assertIn("MB", result.skipped[0].reason)

    def test_total_size_limit_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.bin").write_bytes(b"0" * 600)
            (root / "b.bin").write_bytes(b"0" * 600)
            with patch.object(F, "MAX_FILE_BYTES", 10_000), patch.object(F, "MAX_TOTAL_BYTES", 800):
                result = F.select_project_files(root)
            self.assertEqual(len(result.files), 1)
            self.assertEqual(len(result.skipped), 1)

    def test_file_count_limit_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for i in range(5):
                (root / f"f{i}.txt").write_text("x")
            with patch.object(F, "MAX_FILE_COUNT", 3):
                result = F.select_project_files(root)
            self.assertEqual(len(result.files), 3)
            self.assertEqual(len(result.skipped), 2)


class CheckSingleFileTests(unittest.TestCase):
    def test_applies_secret_and_size_policy_to_mentions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secret = root / ".env"
            secret.write_text("SECRET=1")
            self.assertIsNotNone(F.check_single_file(secret))
            self.assertIsNone(F.check_single_file(secret, allow_secret=True))

            normal = root / "app.py"
            normal.write_text("print(1)")
            self.assertIsNone(F.check_single_file(normal))

            big = root / "big.bin"
            big.write_bytes(b"0" * 1000)
            with patch.object(F, "MAX_FILE_BYTES", 500):
                self.assertIsNotNone(F.check_single_file(big))

    def test_preserves_parent_directories_when_checking_mentions(self):
        with tempfile.TemporaryDirectory() as tmp:
            secret_dir = Path(tmp) / ".aws"
            secret_dir.mkdir()
            secret = secret_dir / "config"
            secret.write_text("token=secret")
            self.assertIsNotNone(F.check_single_file(secret))


class UploadManyTests(unittest.TestCase):
    def test_returns_the_successful_files_for_manifest_generation(self):
        sel = [F.SelectedFile(Path(f"/tmp/f{i}"), Path(f"f{i}"), 1, f"f{i}") for i in range(5)]
        client = MagicMock()
        client.upload_file.side_effect = [f"id{i}" for i in range(5)]
        result = F.upload_many(client, sel)
        self.assertEqual(len(result.content), 5)
        self.assertEqual(result.uploaded, sel)
        self.assertEqual(result.failed, [])

    def test_a_failed_upload_is_reported_not_silently_dropped_and_does_not_stop_the_batch(self):
        sel = [F.SelectedFile(Path("/tmp/a"), Path("a"), 1, "a"), F.SelectedFile(Path("/tmp/b"), Path("b"), 1, "b")]
        client = MagicMock()
        client.upload_file.side_effect = [ManusAPIError("rate_limited", "too many"), "id_b"]
        result = F.upload_many(client, sel)
        self.assertEqual(len(result.content), 1)
        self.assertEqual(result.content[0]["filename"], "b")
        self.assertEqual(result.uploaded, [sel[1]])
        self.assertEqual(len(result.failed), 1)
        self.assertEqual(result.failed[0].relative_path, Path("a"))


class ManifestTests(unittest.TestCase):
    def test_manifest_lists_relative_path_and_display_name(self):
        files = [F.SelectedFile(Path("/abs/src/app.py"), Path("src/app.py"), 42, "app.py")]
        text = F.build_manifest_text(files)
        self.assertIn("src/app.py", text)
        self.assertIn("app.py", text)
        self.assertIn("42 bytes", text)


if __name__ == "__main__":
    unittest.main()
