# -*- coding: utf-8 -*-
"""脱敏覆盖面回归（O01/N65）：回前端的异常文本必须过 scrub_err()。

约定是"把根因给操作者看 + 顺手打码凭据"，不是不外传；集中函数 scrub_err(error_utils.scrub_error)
一直在，漏的是响应点。本测试锁两件事：函数确实打码凭据、且响应型裸 str(e) 归零。
"""
import io
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workbench" / "tools"))
SERVER = (ROOT / "workbench" / "server.py").read_text(encoding="utf-8")

from error_utils import scrub_error  # noqa: E402


class ScrubBehaviorTests(unittest.TestCase):
    def test_masks_credentials_only(self):
        self.assertIn("sk-***", scrub_error("Authorization failed for sk-abcdef1234567890"))
        self.assertIn("Bearer ***", scrub_error("header Bearer abcdef1234567890 rejected"))
        self.assertIn("***", scrub_error('payload {"api_key":"supersecretvalue123"}'))
        kept = scrub_error("KeyError: 'staging'")
        self.assertEqual(kept, "KeyError: 'staging'", "根因文本不能被吃掉")

    def test_accepts_exception_objects(self):
        self.assertEqual(scrub_error(ValueError("boom")), "boom", "异常对象要能直接丢进来")


class ResponseEchoCoverageTests(unittest.TestCase):
    RESP = re.compile(r'"err"\s*:|\'err\'\s*:|err=|_send\(')
    RAW = re.compile(r'(?<!scrub_err\()str\((e|exc|err|exception)\)')

    def test_no_unscrubbed_exception_text_reaches_the_client(self):
        bad = []
        for i, line in enumerate(SERVER.split("\n"), 1):
            if self.RESP.search(line) and "scrub_err" not in line and self.RAW.search(line):
                bad.append((i, line.strip()[:70]))
        self.assertEqual(bad, [], "这些响应点把异常原文直接回前端，未经凭据打码")


if __name__ == "__main__":
    unittest.main()
