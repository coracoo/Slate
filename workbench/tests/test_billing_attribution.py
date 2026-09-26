# -*- coding: utf-8 -*-
"""记账归属回归：AI 花费要能落到项目上（⑥ 用量计费的 project 列此前 2797 条全空）。

`billing.bill()` 一直支持 project，缺的是调用链传值：
  llm_openai.set_billing_project() 线程局部上下文 + 客户端 billing_project 显式覆盖。
"""
import io
import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workbench" / "tools"))

import llm_openai as L  # noqa: E402


class _FakeBilling:
    def __init__(self):
        self.entries = []

    def bill(self, cfg, **kw):
        self.entries.append(dict(kw))
        return True


class ContextTests(unittest.TestCase):
    def test_set_and_read_back(self):
        L.set_billing_project("09_蜘女")
        self.addCleanup(L.set_billing_project, "")
        self.assertEqual(L.current_billing_project(), "09_蜘女")

    def test_context_is_thread_local(self):
        """服务进程里多个项目的任务并发跑，进程级变量会把花费记到别的项目头上。"""
        L.set_billing_project("甲项目")
        self.addCleanup(L.set_billing_project, "")
        seen = {}
        t = threading.Thread(target=lambda: seen.update(inside=L.current_billing_project()))
        t.start(); t.join()
        self.assertEqual(seen["inside"], "", "新线程不该继承主线程的记账归属")
        self.assertEqual(L.current_billing_project(), "甲项目")


class BillForwardingTests(unittest.TestCase):
    def _client(self):
        c = L.VendorClient.__new__(L.VendorClient)   # 不联网、不读 providers.json
        c.cfg = {"id": "v1"}
        return c

    def test_bill_carries_context_project(self):
        fake = _FakeBilling()
        original, L.billing = L.billing, fake
        try:
            L.set_billing_project("01_买瓜")
            self.addCleanup(L.set_billing_project, "")
            self._client()._bill("image", "seedream", "generate_image", True)
            self.assertEqual(fake.entries[0]["project"], "01_买瓜")

            c = self._client()
            c.billing_project = "显式指定的项目"
            c._bill("text", "glm", "chat", True)
            self.assertEqual(fake.entries[1]["project"], "显式指定的项目",
                             "客户端显式归属应优先于线程上下文")
        finally:
            L.billing = original

    def test_missing_project_stays_absent_not_none_string(self):
        fake = _FakeBilling()
        original, L.billing = L.billing, fake
        try:
            L.set_billing_project("")
            self._client()._bill("image", "m", "generate_image", True)
            self.assertFalse(fake.entries[0]["project"], "未归属要留空（billing 只在真值时才写 project 字段）")
        finally:
            L.billing = original


class WiringTests(unittest.TestCase):
    """三个入口必须真的设上归属，否则上下文永远是空。"""

    SITES = [
        ("creation_pipeline.py", "set_billing_project(os.path.basename(proj))"),
        ("gen_asset_images.py", "set_billing_project(os.path.basename(proj))"),
        ("production_jobs.py", "set_billing_project(project.name)"),
    ]

    def test_entry_points_set_the_project(self):
        for fn, snippet in self.SITES:
            src = (ROOT / "workbench" / "tools" / fn).read_text(encoding="utf-8")
            self.assertIn(snippet, src, fn + " 的记账归属被摘掉了：花费又会全部无归属")


class LedgerEndToEndTests(unittest.TestCase):
    """走真 billing 模块（临时账本文件），确认字段真的落进 ⑥ 读到的那一行。"""

    def test_project_lands_in_ledger_line(self):
        import json
        import tempfile
        from unittest.mock import patch
        import billing as BL
        original, L.billing = L.billing, BL
        try:
            with tempfile.TemporaryDirectory() as td:
                L.set_billing_project("09_蜘女")
                self.addCleanup(L.set_billing_project, "")
                c = L.VendorClient.__new__(L.VendorClient)
                c.cfg = {"id": "v1", "label": "V1"}
                with patch.object(BL, "LEDGER", str(Path(td) / "ledger.jsonl")):
                    c._bill("image", "seedream", "generate_image", True)
                    with io.open(BL.LEDGER, encoding="utf-8") as fh:
                        lines = fh.read().strip().splitlines()
                self.assertEqual(len(lines), 1)
                row = json.loads(lines[0])
                self.assertEqual(row["project"], "09_蜘女")
                self.assertEqual(row["op"], "generate_image")
        finally:
            L.billing = original


if __name__ == "__main__":
    unittest.main()
