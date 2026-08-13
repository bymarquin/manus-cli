from __future__ import annotations

import os
import tempfile
import unittest


class IsolatedConfigTestCase(unittest.TestCase):
    """Points MANUS_CONFIG_DIR at a throwaway temp dir for the whole test.

    Every manus_cli.config function reads MANUS_CONFIG_DIR lazily (not cached at
    import time), so this is enough to guarantee no test ever reads or writes the
    real ~/.config/manus on the machine running the suite.
    """

    def setUp(self) -> None:
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_env = os.environ.get("MANUS_CONFIG_DIR")
        os.environ["MANUS_CONFIG_DIR"] = self._tmp.name

    def tearDown(self) -> None:
        if self._orig_env is None:
            os.environ.pop("MANUS_CONFIG_DIR", None)
        else:
            os.environ["MANUS_CONFIG_DIR"] = self._orig_env
        self._tmp.cleanup()
        super().tearDown()
