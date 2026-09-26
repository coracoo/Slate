# -*- coding: utf-8 -*-
"""create_batch 选择闸回归（§六.4 工具侧那半）：漏传选择器不得等于整板付费。"""
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "workbench" / "tools" / "create_batch.py"
BOARD = "projects/09_蜘女"


def run(*extra):
    cmd = [sys.executable, str(TOOL), "--project", str(ROOT / BOARD), "--board", "剧本_E1.json",
           "--type", "image", "--vendor", "none", "--providers", str(ROOT / "workbench" / "providers.json"), *extra]
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")


class SelectionGateTests(unittest.TestCase):
    def test_no_selector_is_refused(self):
        r = run()
        self.assertEqual(r.returncode, 2, "未选镜号曾默认整板执行 = 一跑就是全板费用")
        self.assertIn("--all", r.stdout)

    def test_unknown_shot_id_is_refused_with_names(self):
        r = run("--shot-id", "S999")
        self.assertEqual(r.returncode, 2)
        self.assertIn("S999", r.stdout, "镜号打错要指名道姓，否则日志里只看到 0 条")

    def test_help_documents_the_explicit_all_flag(self):
        r = run("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("--all", r.stdout)
