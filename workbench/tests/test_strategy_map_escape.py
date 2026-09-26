# -*- coding: utf-8 -*-
"""战略图产物是同源 HTML（经 /media 回吐、推演页 iframe 自动加载）：
模板填充必须转义 < >，否则角色名/台词里的 </script> 即存储型 XSS。"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workbench" / "tools"))

from strategy_map import render_strategy_html


class StrategyMapEscapeTests(unittest.TestCase):
    def _evil(self):
        return {
            "title": '教室</title><img src=x onerror=alert(1)>',
            "actors": [{"name": '甲</script><script>alert(2)</script>', "color": "#f00"}],
            "scenes": [],
            "shots": [{"id": "S1", "lines": [{"speaker": "甲", "text": "</script><b>x</b>"}]}],
        }

    def test_script_breakout_is_neutralised(self):
        html = render_strategy_html(self._evil())
        head, _, _tail = html.partition("const DATA =")
        data_region, _, _rest = _tail.partition("\n")
        self.assertNotIn("</script>", data_region, "DATA 区不得出现可用的 </script>")
        self.assertIn("\\u003c/script\\u003e", data_region, "应转义为 \\u003c 序列")
        self.assertEqual(html.count("<script"), head.count("<script"),
                         "注入的 <script 标签不得出现在 DATA 之后")

    def test_title_text_is_escaped(self):
        html = render_strategy_html(self._evil())
        title_region = html.partition("<title>")[2].partition("</title>")[0]
        self.assertNotIn("<img", title_region)
        self.assertIn("&lt;img", title_region)

    def test_normal_data_still_reads_back_identically(self):
        import json
        data = {"title": "战略图_E1", "actors": [{"name": "白筱竹"}], "scenes": [],
                "shots": [{"id": "S1", "action": "推开教室门，中文与 emoji 😀 保留"}]}
        html = render_strategy_html(data)
        payload = html.partition("const DATA =")[2].partition(";\n")[0]
        self.assertEqual(json.loads(payload), data)


if __name__ == "__main__":
    unittest.main()
