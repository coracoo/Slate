# -*- coding: utf-8 -*-
"""N78 诊断口径回归：读档案失败与探针失败不能被伪装成"资产不存在"或"表演已就绪"。

两条路由都要真发 HTTP 才能跑到（依赖 proj_dir/看门狗上下文），这里按源码层契约锁住判定分支——
与 http_route_contract 同一思路：不改实现就永远不会有人把 except 又改回 pass。
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = (ROOT / "workbench" / "server.py").read_text(encoding="utf-8")


def _src(name):
    """按 def 名切出路由处理函数源码（不 import server，避免拉起常驻依赖）。"""
    m = re.search(r"^    def %s\(self, ctx\):$" % re.escape(name), SRC, re.M)
    assert m, "server.py 里找不到处理函数 " + name
    nxt = re.search(r"^    (?:@route|def )", SRC[m.end():], re.M)
    return SRC[m.start():m.end() + (nxt.start() if nxt else len(SRC))]


class PromptLayersDiagnosisTests(unittest.TestCase):
    def test_archive_read_failure_is_not_reported_as_missing_asset(self):
        src = _src("route_get_api_asset_prompt_layers")
        self.assertNotIn("except Exception: pass", src,
                         "三件套解析失败被吞掉后，404「资产不存在」会把用户支去重建档案")
        self.assertIn("read_err", src)
        self.assertRegex(src, r'if read_err:\s*\n\s*return self\._send\(500',
                         "读取失败必须独立成一条响应，不能落到 not item 分支")


class ActingContextProbeTests(unittest.TestCase):
    def test_failed_staleness_probe_leaves_a_trace(self):
        src = _src("route_get_api_acting_context")
        self.assertRegex(src, r"except Exception as exc:\s*\n(?:\s*#.*\n)+\s*check_err\s*=",
                         "compile_shot 探针失败不能再静默 pass：页面会假绿（状态仍 ready）")
        self.assertIn('"performance_check_error":check_err', src,
                      "探针失败原因要随响应带出去，前端/日志才查得到")


if __name__ == "__main__":
    unittest.main()
