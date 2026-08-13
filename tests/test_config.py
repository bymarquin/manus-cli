from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

from _helpers import IsolatedConfigTestCase

from manus_cli import config


class ApiKeyTests(IsolatedConfigTestCase):
    def test_roundtrip_and_permissions(self):
        config.save_api_key("sk-abc123")
        self.assertEqual(config.load_api_key(), "sk-abc123")
        path = Path(os.environ["MANUS_CONFIG_DIR"]) / "credentials.json"
        mode = os.stat(path).st_mode & 0o777
        if os.name != "nt":
            # POSIX exposes permission bits. Windows uses ACLs and does not
            # provide an equivalent 0600 contract through chmod/stat.
            self.assertEqual(oct(mode), "0o600")

    def test_env_var_takes_priority_over_file(self):
        config.save_api_key("from-file")
        os.environ["MANUS_API_KEY"] = "from-env"
        try:
            self.assertEqual(config.load_api_key(), "from-env")
        finally:
            del os.environ["MANUS_API_KEY"]

    def test_mask(self):
        self.assertEqual(config.mask("sk-1234567890"), "sk-123...7890")
        self.assertEqual(config.mask("short"), "***")


class StateTests(IsolatedConfigTestCase):
    def test_last_task_and_aliases_coexist(self):
        config.save_last_task("task1")
        config.save_alias("backend", "task1")
        config.save_last_task("task2")  # must not clobber the alias
        self.assertEqual(config.load_last_task(), "task2")
        self.assertEqual(config.resolve_alias("backend"), "task1")

    def test_unknown_alias_resolves_to_none(self):
        self.assertIsNone(config.resolve_alias("nope"))

    def test_corrupted_state_raises_config_error(self):
        path = Path(os.environ["MANUS_CONFIG_DIR"])
        path.mkdir(parents=True, exist_ok=True)
        (path / "state.json").write_text("{not valid json")
        with self.assertRaises(config.ConfigError):
            config.load_last_task()

    def test_state_file_is_a_list_not_object_raises(self):
        path = Path(os.environ["MANUS_CONFIG_DIR"])
        path.mkdir(parents=True, exist_ok=True)
        (path / "state.json").write_text("[1, 2, 3]")
        with self.assertRaises(config.ConfigError):
            config.load_last_task()


class ProjectRcTests(IsolatedConfigTestCase):
    def _write_rc(self, tmp_dir: Path, data: dict) -> None:
        (tmp_dir / ".manusrc").write_text(json.dumps(data))

    def test_valid_manusrc(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._write_rc(tmp_path, {"task_id": "abc", "connectors": ["uuid-1"]})
            self.assertEqual(config.load_project_rc(tmp_path), {"task_id": "abc", "connectors": ["uuid-1"]})

    def test_missing_manusrc_returns_empty_dict(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(config.load_project_rc(Path(tmp)), {})

    def test_unknown_field_rejected(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._write_rc(tmp_path, {"totally_made_up": 1})
            with self.assertRaises(config.ConfigError):
                config.load_project_rc(tmp_path)

    def test_wrong_type_for_task_id_rejected(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            self._write_rc(tmp_path, {"task_id": 123})
            with self.assertRaises(config.ConfigError):
                config.load_project_rc(tmp_path)

    def test_corrupted_manusrc_rejected(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / ".manusrc").write_text("not json at all {{{")
            with self.assertRaises(config.ConfigError):
                config.load_project_rc(tmp_path)


class IsolationSelfTest(unittest.TestCase):
    """Meta-test: confirms the isolation fixture actually redirects away from
    the real user config dir, since every other test's safety depends on it."""

    def test_config_dir_changes_under_isolation(self):
        real_dir = str(config._config_dir())
        case = IsolatedConfigTestCase()
        case.setUp()
        try:
            isolated_dir = str(config._config_dir())
            self.assertNotEqual(real_dir, isolated_dir)
            self.assertIn("MANUS_CONFIG_DIR", os.environ)
        finally:
            case.tearDown()
        self.assertEqual(str(config._config_dir()), real_dir)


if __name__ == "__main__":
    unittest.main()
