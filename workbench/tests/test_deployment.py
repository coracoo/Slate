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
        for name in ('server.py', 'launch.py', 'watch.py'):
            with self.subTest(name=name), patch.object(sys, 'version_info', (3, 11, 9)):
                with self.assertRaisesRegex(SystemExit, 'Python 3.12'):
                    runpy.run_path(str(ROOT / 'workbench' / name), run_name='__main__')

    def test_background_launcher_rejects_occupied_port(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            result = subprocess.run([sys.executable, str(ROOT / 'workbench/launch.py'),
                                     str(listener.getsockname()[1])], capture_output=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
