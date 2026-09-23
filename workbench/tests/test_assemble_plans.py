# -*- coding: utf-8 -*-
"""assemble 挂接 AI 平面图：manifest.plans 字段 + 战略图 --plan 叠加（有/无 plan 两种项目形态）。"""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

import creation_pipeline as cp

BOARD = {"title": "测试剧", "actors": {"c": {"name": "甲", "shirt": [200, 60, 60]}},
         "shots": [{"id": "S1", "dur": 4, "action": "甲进帐。",
                    "lines": [{"speaker": "c", "line": "报——", "at": 0, "dur": 2}]}]}

PLAN_NEW = {"version": 1, "name": "新军帐", "canvas": {"w": 12, "h": 9},
            "room": {"walls": [[0, 0], [12, 0], [12, 9], [0, 9]]},
            "props": [{"id": "p1", "label": "帅案", "shape": "rect", "center": [6, 4.5], "size": [3, 1]}],
            "actors": [{"id": "c", "name": "甲", "pos": [6, 6]}]}
PLAN_OLD = {"version": 1, "name": "旧军帐", "canvas": {"w": 10, "h": 8},
            "props": [{"id": "p1", "label": "旧案", "shape": "rect", "center": [5, 4], "size": [2, 1]}],
            "actors": []}


def make_plan_file(proj, plan, mtime):
    fp = os.path.join(proj, "推演", f"平面图_{plan['name']}.plan.json")
    json.dump(plan, open(fp, "w", encoding="utf-8"), ensure_ascii=False)
    os.utime(fp, (mtime, mtime))
    return fp


class AssemblePlansTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.proj = self._td.name
        os.makedirs(os.path.join(self.proj, "分镜"))
        os.makedirs(os.path.join(self.proj, "推演"))
        json.dump(BOARD, open(os.path.join(self.proj, "分镜", "剧本_E1.json"), "w", encoding="utf-8"),
                  ensure_ascii=False)

    def tearDown(self):
        self._td.cleanup()

    def manifest(self):
        with open(os.path.join(self.proj, "推演", "创作包_剧本_E1", "manifest.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def strategy_html(self):
        with open(os.path.join(self.proj, "推演", "战略图_剧本_E1.html"), encoding="utf-8") as fh:
            return fh.read()

    def test_without_plan_keeps_old_behavior(self):
        cp.cmd_assemble(self.proj, "剧本_E1.json")
        pkg = self.manifest()
        self.assertEqual(pkg["plans"], [])
        self.assertEqual(len(pkg["shots"]), 1)
        self.assertNotIn('"_plan"', self.strategy_html())   # 战略图底图未被 plan 接管

    def test_default_skips_shot_diagram(self):
        """09-23 起默认不再自动生成逐镜平面图：diagram=None + diagram_note 说明 + 不落 PNG。"""
        cp.cmd_assemble(self.proj, "剧本_E1.json")
        pkg = self.manifest()
        self.assertIsNone(pkg["shots"][0]["diagram"])
        self.assertIn("AI 平面图", pkg["diagram_note"])
        self.assertEqual([f for f in os.listdir(os.path.join(self.proj, "推演"))
                          if f.startswith("平面图_剧本_E1")], [])

    def test_with_diagram_flag_generates(self):
        """--with-diagram 手动开关：照常生成逐镜 PNG 并回填 diagram 字段。"""
        cp.cmd_assemble(self.proj, "剧本_E1.json", with_diagram=True)
        pkg = self.manifest()
        self.assertEqual(pkg["shots"][0]["diagram"], "S1.png")
        self.assertTrue(os.path.isfile(os.path.join(self.proj, "推演", "平面图_剧本_E1", "S1.png")))

    def test_with_plans_manifest_and_strategy_overlay(self):
        make_plan_file(self.proj, PLAN_OLD, time.time() - 3600)
        make_plan_file(self.proj, PLAN_NEW, time.time())
        # 新图已有画布 HTML → canvas_html 填相对路径；旧图没有 → null
        open(os.path.join(self.proj, "推演", "战略图_平面图_新军帐.html"), "w", encoding="utf-8").write("<html></html>")
        cp.cmd_assemble(self.proj, "剧本_E1.json")
        pkg = self.manifest()
        self.assertEqual(len(pkg["plans"]), 2)
        # mtime 降序：新军帐在前
        self.assertEqual([p["name"] for p in pkg["plans"]], ["新军帐", "旧军帐"])
        first = pkg["plans"][0]
        self.assertEqual(first["counts"], {"props": 1, "actors": 1, "paths": 0})
        self.assertTrue(first["validate_ok"])
        self.assertEqual(first["canvas_html"], "推演/战略图_平面图_新军帐.html")
        self.assertIsNone(pkg["plans"][1]["canvas_html"])
        # 战略图叠加了最新一张 plan 作底图（新军帐的"帅案"出现，旧案不出现）
        html = self.strategy_html()
        self.assertIn('"_plan"', html)
        self.assertIn("帅案", html)
        self.assertNotIn("旧案", html)
        # 分镜镜头照旧保留
        self.assertEqual(pkg["shots"][0]["id"], "S1")

    def test_collect_plans_skips_broken_and_flags_invalid(self):
        fp = os.path.join(self.proj, "推演", "平面图_坏.plan.json")
        open(fp, "w", encoding="utf-8").write("{不是JSON")
        json.dump({"canvas": {"w": 0, "h": 0}},  # 合法 JSON 但校验不过
                  open(os.path.join(self.proj, "推演", "平面图_错.plan.json"), "w", encoding="utf-8"))
        rows = cp.collect_plans(self.proj)
        self.assertEqual([r["name"] for r in rows], ["错"])
        self.assertFalse(rows[0]["validate_ok"])
        self.assertIsNone(rows[0]["canvas_html"])

    def test_scene_ref_majority_match_beats_mtime(self):
        """底图选择：分镜多数镜 scene_ref 命中的 plan 胜出——哪怕它 mtime 更旧；
        同场景 S1/S2 共用同一张 _plan 底图；manifest plans 带 scene_ref。"""
        board = json.loads(json.dumps(BOARD))
        board["shots"] = [
            {"id": "S1", "dur": 4, "scene_ref": "@scene:loc_new", "action": "甲进帐。",
             "lines": [{"speaker": "c", "line": "报——", "at": 0, "dur": 2}]},
            {"id": "S2", "dur": 3, "scene_ref": "@scene:loc_new", "action": "甲立定。"},
        ]
        json.dump(board, open(os.path.join(self.proj, "分镜", "剧本_E1.json"), "w", encoding="utf-8"),
                  ensure_ascii=False)
        new_match = dict(PLAN_NEW, scene_ref="loc_new")
        old_other = dict(PLAN_OLD, scene_ref="loc_old")
        # 注意：不命中的旧军帐 mtime 反而更新——匹配必须胜过"最新"
        make_plan_file(self.proj, new_match, time.time() - 3600)
        make_plan_file(self.proj, old_other, time.time())
        cp.cmd_assemble(self.proj, "剧本_E1.json")
        pkg = self.manifest()
        # manifest plans 仍是 mtime 降序（旧军帐在前），且带 scene_ref
        self.assertEqual([p["name"] for p in pkg["plans"]], ["旧军帐", "新军帐"])
        self.assertEqual([p["scene_ref"] for p in pkg["plans"]], ["loc_old", "loc_new"])
        # 但战略图底图选了命中的新军帐：帅案出现、旧案不出现；两镜共用同一张 _plan 底图
        html = self.strategy_html()
        self.assertIn("帅案", html)
        self.assertNotIn("旧案", html)
        data = json.loads(html.split("const DATA = ", 1)[1].split("\n", 1)[0].rstrip().rstrip(";"))
        self.assertEqual(list(data["scenes"].keys()), ["_plan"])
        self.assertEqual([s["_scene_id"] for s in data["shots"]], ["_plan", "_plan"])
        self.assertEqual([s["id"] for s in pkg["shots"]], ["S1", "S2"])

    def test_no_scene_ref_shots_falls_back_to_newest(self):
        """老分镜没有 scene_ref：回退最新一张（与旧行为一致）。"""
        make_plan_file(self.proj, dict(PLAN_OLD, scene_ref="loc_old"), time.time() - 3600)
        make_plan_file(self.proj, dict(PLAN_NEW, scene_ref="loc_new"), time.time())
        cp.cmd_assemble(self.proj, "剧本_E1.json")
        html = self.strategy_html()
        self.assertIn("帅案", html)
        self.assertNotIn("旧案", html)


if __name__ == '__main__':
    unittest.main()
