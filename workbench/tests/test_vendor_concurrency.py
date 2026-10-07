# -*- coding: utf-8 -*-
"""并发口径：云端 API 可并发，本机 ComfyUI 与 ChatGPT 网页队列必须串行。"""
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workbench" / "tools"))


def _cloud_cfg():
    return {"id": "openai-compat", "label": "通用", "base_url": "https://x/v1", "api_key": "k",
            "enabled": True, "models": {"image": "seedream-4.0"}, "endpoints": {}, "extra": {}}


class VendorConcurrencyTests(unittest.TestCase):
    def test_cloud_vendors_are_parallel_and_local_are_serial(self):
        from vendor_concurrency import parallel_cap
        self.assertEqual(parallel_cap("doubao", 4), 4)
        self.assertEqual(parallel_cap("minimax", 99), 8, "云端也封顶，防手滑一次提几百个")
        self.assertEqual(parallel_cap("local-comfyui", 8), 1, "本机 ComfyUI 只有一个队列")
        self.assertEqual(parallel_cap("chatgpt-queue", 8), 1, "浏览器自动化是单张串行队列")
        self.assertEqual(parallel_cap("my-comfyui-gateway", 6), 1, "自建同质条目按 id 片段兜底")
        self.assertEqual(parallel_cap("agnes", 0), 1, "请求值非法时退回 1，不炸")

    def test_run_prepared_parallelizes_cloud_and_serializes_local(self):
        import create_batch

        cmds = [("S%d" % i, ["python", "x", "FAIL" if i == 3 else "ok"]) for i in range(1, 7)]
        self.assertEqual(len(cmds), 6)

        def make(sleep):
            lock = threading.Lock()
            stat = {"max_inflight": 0, "live": 0, "n": 0, "cwds": []}

            def run(cmd, cwd=None, check=False):
                with lock:
                    stat["n"] += 1
                    stat["live"] += 1
                    stat["cwds"].append(cwd)
                    stat["max_inflight"] = max(stat["max_inflight"], stat["live"])
                if sleep:
                    time.sleep(sleep)
                with lock:
                    stat["live"] -= 1
                rc = 3 if "FAIL" in cmd else 0
                return subprocess.CompletedProcess(cmd, rc)

            return run, stat

        run, stat = make(0.02)
        with patch.object(create_batch.subprocess, "run", run):
            failed = create_batch.run_prepared(cmds, "doubao", workers=3, cwd="/tmp")
        self.assertEqual(stat["n"], 6, "六个镜头都要提交")
        self.assertGreater(stat["max_inflight"], 1, "云端厂商必须真并发")
        self.assertLessEqual(stat["max_inflight"], 3)
        self.assertEqual(set(stat["cwds"]), {"/tmp"}, "cwd 要透传给每个子进程")
        self.assertEqual(failed, 1, "非零退出计一次失败，但不中断其余镜头")

        run2, stat2 = make(0.0)
        with patch.object(create_batch.subprocess, "run", run2):
            failed2 = create_batch.run_prepared(cmds, "local-comfyui", workers=8, cwd="/tmp")
        self.assertEqual(stat2["n"], 6)
        self.assertEqual(stat2["max_inflight"], 1, "本机 ComfyUI 只能一张张排队")
        self.assertEqual(failed2, 1)

        run3, stat3 = make(0.0)
        with patch.object(create_batch.subprocess, "run", run3):
            create_batch.run_prepared(cmds[:2], "chatgpt-queue", workers=8, cwd=None)
        self.assertEqual(stat3["max_inflight"], 1, "ChatGPT 网页队列同理")


    def test_image_retry_covers_rate_limit_but_not_timeout(self):
        """并发批量生图最容易撞限流：429/5xx 退避重试；超时不重试（图可能已出、已计费）。"""
        import llm_openai as L

        class _Ledger:
            def __init__(self):
                self.entries = []

            def bill(self, cfg, **kw):
                self.entries.append(dict(kw))

        def make_client():
            client = L.VendorClient.from_config(_cloud_cfg(), check_enabled=False)
            client.billing_project = "测试项目"
            return client

        for err, should_retry in (("HTTP 429: too many requests", True),
                                  ("HTTP 503: upstream busy", True),
                                  ("请求超时（>300s）", False),
                                  ("HTTP 400: bad prompt", False)):
            attempts = []
            client = make_client()

            def impl(prompt, out, **kw):
                attempts.append(prompt)
                raise L.VendorError(err)

            client._generate_image_impl = impl
            ledger = _Ledger()
            with patch.object(L, "billing", ledger), patch.object(L.time, "sleep", lambda s: None):
                with self.assertRaises(L.VendorError):
                    client.generate_image("提示词", "out.png")
            want = 3 if should_retry else 1
            self.assertEqual(len(attempts), want, f"{err} 的重试次数不对")
            self.assertEqual([e["ok"] for e in ledger.entries], [False], "失败只记一笔账，别重复计费")

    def test_image_rate_limit_recovers_and_bills_once(self):
        import llm_openai as L

        class _Ledger:
            def __init__(self):
                self.entries = []

            def bill(self, cfg, **kw):
                self.entries.append(dict(kw))

        client = L.VendorClient.from_config(_cloud_cfg(), check_enabled=False)
        attempts = []

        def impl(prompt, out, **kw):
            attempts.append(prompt)
            if len(attempts) < 3:
                raise L.VendorError("HTTP 429: too many requests")
            return out

        client._generate_image_impl = impl
        ledger = _Ledger()
        with patch.object(L, "billing", ledger), patch.object(L.time, "sleep", lambda s: None):
            self.assertEqual(client.generate_image("提示词", "out.png"), "out.png")
        self.assertEqual(len(attempts), 3)
        self.assertEqual([e["ok"] for e in ledger.entries], [True], "退避后成功只计费一次")


if __name__ == "__main__":
    unittest.main()
