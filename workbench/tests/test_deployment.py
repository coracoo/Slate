# -*- coding: utf-8 -*-
"""部署失败须在进入业务流程前显式报告。"""
import runpy
import socket
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


class DeploymentTests(unittest.TestCase):
    def test_old_python_rejected_at_entry(self):
        for name in ('server.py',):
            with self.subTest(name=name), patch.object(sys, 'version_info', (3, 11, 9)):
                with self.assertRaisesRegex(SystemExit, 'Python 3.12'):
                    runpy.run_path(str(ROOT / 'workbench' / name), run_name='__main__')

    def test_newer_python_is_rejected_for_the_pinned_lockfile(self):
        with patch.object(sys, 'version_info', (3, 13, 0)):
            with self.assertRaisesRegex(SystemExit, 'Python 3.12'):
                runpy.run_path(str(ROOT / 'workbench' / 'server.py'), run_name='__main__')

    def test_install_groups_reject_arbitrary_commands(self):
        from workbench.tools.install_environment import normalize_groups, commands
        for value in ([], None, ["pip install evil"], ["base", 1]):
            with self.assertRaises(ValueError):
                normalize_groups(value)
        self.assertEqual(normalize_groups(["depth", "base", "base"]), ["base", "depth"])
        plan = commands(["base"], "python-test")
        self.assertIn("--require-hashes", plan[0])
        self.assertEqual(plan[-1], ["python-test", "-m", "pip", "check"])


if __name__ == '__main__':
    unittest.main()
