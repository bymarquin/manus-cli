from __future__ import annotations

import unittest

from manus_cli.agent_policy import ApprovalMode, Decision, PolicyEngine


class PolicyEngineTests(unittest.TestCase):
    def test_reads_and_balanced_writes_are_allowed(self):
        policy = PolicyEngine(ApprovalMode.BALANCED)
        self.assertEqual(policy.evaluate("read_file", {"path": "src/app.py"}).decision, Decision.ALLOW)
        self.assertEqual(
            policy.evaluate("replace_text", {"path": "src/app.py"}).decision,
            Decision.ALLOW,
        )

    def test_supervised_confirms_writes_and_commands(self):
        policy = PolicyEngine(ApprovalMode.SUPERVISED)
        self.assertEqual(policy.evaluate("write_file", {"path": "new.py"}).decision, Decision.CONFIRM)
        self.assertEqual(
            policy.evaluate("run_command", {"argv": ["git", "status"]}).decision,
            Decision.CONFIRM,
        )
        verification = policy.evaluate("run_command", {"argv": ["python3", "-m", "unittest"]})
        self.assertEqual(verification.decision, Decision.CONFIRM)
        self.assertTrue(verification.verification)

    def test_autonomous_allows_confirmable_but_not_denied(self):
        policy = PolicyEngine(ApprovalMode.AUTONOMOUS)
        self.assertEqual(
            policy.evaluate("run_command", {"argv": ["npm", "install"]}).decision,
            Decision.ALLOW,
        )
        self.assertEqual(
            policy.evaluate("run_command", {"argv": ["git", "push"]}).decision,
            Decision.DENY,
        )

    def test_paths_outside_git_and_secrets_are_denied(self):
        policy = PolicyEngine()
        for path in ("../outside", "/tmp/outside", ".git/config", ".env", "keys/id_rsa"):
            with self.subTest(path=path):
                self.assertEqual(policy.evaluate("read_file", {"path": path}).decision, Decision.DENY)

    def test_command_classification(self):
        policy = PolicyEngine()
        cases = [
            (["python3", "-m", "unittest"], Decision.ALLOW),
            (["npm", "run", "lint"], Decision.ALLOW),
            (["npm", "run", "test:unit"], Decision.ALLOW),
            (["node", "--test"], Decision.ALLOW),
            (["./gradlew", "test"], Decision.ALLOW),
            (["php", "artisan", "test"], Decision.ALLOW),
            (["npm", "install"], Decision.CONFIRM),
            (["curl", "https://example.com"], Decision.CONFIRM),
            (["bash", "-c", "echo bad"], Decision.DENY),
            (["python3", "-c", "print(1)"], Decision.DENY),
            (["npm", "publish"], Decision.DENY),
            (["npm.cmd", "publish"], Decision.DENY),
            ([".venv/bin/python", "-m", "twine", "upload", "dist/*"], Decision.DENY),
            ([".venv/bin/python", "-m", "unittest"], Decision.ALLOW),
            (["git", "reset", "--hard"], Decision.DENY),
            (["unknown-tool", "x"], Decision.DENY),
            (["/usr/bin/git", "status"], Decision.DENY),
        ]
        for argv, expected in cases:
            with self.subTest(argv=argv):
                self.assertEqual(policy.evaluate("run_command", {"argv": argv}).decision, expected)

    def test_malformed_action_is_denied(self):
        policy = PolicyEngine()
        self.assertEqual(policy.evaluate("missing", {}).decision, Decision.DENY)
        self.assertEqual(policy.evaluate("run_command", {"argv": "git status"}).decision, Decision.DENY)
