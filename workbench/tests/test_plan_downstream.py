# -*- coding: utf-8 -*-
"""plan v1 下游衔接：strategy_map / shot_diagram 的 --plan 输入模式出图。"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

import plan_adapt
import strategy_map
import shot_diagram

PLAN = {"version": 1, "name": "军帐", "canvas": {"w": 12, "h": 9},
        "room": {"walls": [[0, 0], [12, 0], [12, 9], [0, 9]],
                 "openings": [{"wall": 1, "offset": 5.0, "width": 1.2, "kind": "door"}]},
        "props": [{"id": "p1", "label": "长案", "shape": "rect", "center": [6, 4.5], "size": [3, 1]},
                  {"id": "p2", "label": "火盆", "shape": "circle", "center": [2, 7], "size": [0.5]}],
        "actors": [{"id": "c", "name": "主角", "color": "#e74c3c", "pos": [6, 6], "facing": 270}],
        "paths": [{"actor": "c", "points": [[6, 6, 0], [6, 7, 2]], "style": "walk"}],
        "cameras": [{"id": "cam1", "pos": [6, 8.5], "look": [6, 4.5], "fov": 50}],
        "zones": [{"id": "z1", "label": "帐心议事区", "rect": [3, 2, 6, 5], "scene_ref": "军帐"}]}

BOARD = {"title": "test", "actors": {"c": {"name": "主角", "shirt": [231, 76, 60]}},
         "shots": [{"id": "S1", "dur": 4, "pos": [0, 1.6, -3], "look": [0, 1.4, 0], "fov": 50},
                   {"id": "S2", "dur": 4}]}   # S2 相机缺省 → 以 plan 补


class PlanAdaptTests(unittest.TestCase):
    def test_coordinate_conversion(self):
        # 原点左上 → 世界中心；plan 上方 = 世界 +z
        self.assertEqual(plan_adapt.pw(PLAN, 6, 4.5), (0.0, 0.0))
        self.assertEqual(plan_adapt.pw(PLAN, 0, 0), (-6.0, 4.5))
        self.assertEqual(plan_adapt.pw(PLAN, 12, 9), (6.0, -4.5))

    def test_scene_conversion(self):
        sc = plan_adapt.plan_scene(PLAN)
        lay = sc["layout"]
        self.assertEqual((lay["bounds"]["w"], lay["bounds"]["d"]), (12.0, 9.0))
        self.assertEqual(len(lay["walls"]), 4)
        self.assertEqual(len(lay["furniture"]), 2)
        self.assertEqual(lay["zones"][0]["label"], "帐心议事区")
        self.assertEqual(len(lay["entry"]), 1)
        self.assertEqual(lay["paths"][0]["points"][0], [0.0, -1.5, 0.0])

    def test_camera_and_actor_conversion(self):
        cam = plan_adapt.plan_cameras_world(PLAN)[0]
        self.assertEqual(cam["pos"], [0.0, 1.6, -4.0])
        self.assertEqual(cam["look"], [0.0, 1.4, 0.0])
        self.assertEqual(plan_adapt.plan_actor_positions(PLAN)["c"], (0.0, -1.5))
        self.assertEqual(plan_adapt.plan_actors_map(PLAN)["c"]["shirt"], [231, 76, 60])


class ChoosePlanTests(unittest.TestCase):
    """choose_plan：按分镜 scene_ref 多数镜匹配（plans mtime 降序——同分取新），全零回退最新。"""

    PLANS = [  # 已按 mtime 降序（新在前）
        {"name": "新-教室", "scene_ref": "loc_classroom"},
        {"name": "旧-教室", "scene_ref": "loc_classroom"},
        {"name": "战场", "scene_ref": "loc_field"},
        {"name": "自由图", "scene_ref": None},
    ]
    SHOTS = [{"id": "S1", "scene_ref": "@scene:loc_field"},
             {"id": "S2", "scene_ref": "@scene:loc_field"},
             {"id": "S3", "scene_ref": "@scene:loc_classroom"}]

    def test_shot_scene_ref_strips_prefix(self):
        self.assertEqual(plan_adapt.shot_scene_ref({"scene_ref": "@scene:loc_x"}), "loc_x")
        self.assertEqual(plan_adapt.shot_scene_ref({"scene_ref": "room"}), "")
        self.assertEqual(plan_adapt.shot_scene_ref({}), "")

    def test_majority_match_wins(self):
        # loc_field 2 镜 > loc_classroom 1 镜 → 战场（哪怕它在 mtime 序列里靠后）
        self.assertEqual(plan_adapt.choose_plan(self.PLANS, self.SHOTS)["name"], "战场")

    def test_tie_prefers_newer(self):
        shots = [{"id": "S1", "scene_ref": "@scene:loc_classroom"},
                 {"id": "S2", "scene_ref": "@scene:loc_classroom"}]
        self.assertEqual(plan_adapt.choose_plan(self.PLANS, shots)["name"], "新-教室")

    def test_no_match_falls_back_to_newest(self):
        shots = [{"id": "S1"}, {"id": "S2", "scene_ref": "@scene:不存在"}]
        self.assertEqual(plan_adapt.choose_plan(self.PLANS, shots)["name"], "新-教室")

    def test_empty_plans_returns_none(self):
        self.assertIsNone(plan_adapt.choose_plan([], self.SHOTS))

    def test_choice_stamp_distinguishes_match_from_fallback(self):
        """零匹配回退保留（老项目 scene_ref 填充率 0，硬拦会当场出不了图），但必须可判定。"""
        self.assertEqual(plan_adapt.choose_plan(self.PLANS, self.SHOTS)["choice"],
                         {"mode": "matched", "score": 2, "total": 3})
        loose = [{"name": "唯一一张", "scene_ref": "loc_a"}]
        got = plan_adapt.choose_plan(loose, [{"id": "S1", "scene_ref": "@scene:other"}])
        self.assertEqual(got["choice"], {"mode": "fallback", "score": 0, "total": 1})

    def test_fallback_stamp_survives_when_no_shot_has_scene_ref(self):
        """整本没有 scene_ref 是最常见形态（老项目/纯手写分镜），也要落 fallback 而不是缺键。"""
        loose = [{"name": "无关联图", "scene_ref": None}]
        got = plan_adapt.choose_plan(loose, [{"id": "S1"}, {"id": "S2"}])
        self.assertEqual(got["choice"], {"mode": "fallback", "score": 0, "total": 2})

    def test_returns_the_same_object_not_a_copy(self):
        # plan_frames.choose_board_plan 用 `pl is plan` 把选中项映射回文件路径：
        # 返回副本会让它恒定取不到路径，平面图参考帧整批静默消失（本轮初版就踩过这个）
        got = plan_adapt.choose_plan(self.PLANS, self.SHOTS)
        self.assertIs(got, self.PLANS[2])


class StrategyMapPlanTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.proj = Path(self._td.name)
        (self.proj / "分镜").mkdir()
        (self.proj / "推演").mkdir()
        self.board = self.proj / "分镜" / "test.json"
        self.board.write_text(json.dumps(BOARD, ensure_ascii=False), encoding="utf-8")
        self.plan = self.proj / "推演" / "平面图_军帐.plan.json"
        self.plan.write_text(json.dumps(PLAN, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        self._td.cleanup()

    def _run(self, *argv):
        with patch.object(sys, "argv", ["strategy_map.py", *argv]):
            strategy_map.main()

    def test_storyboard_with_plan_renders_walls(self):
        out = self.proj / "推演" / "战略图_test.html"
        self._run(str(self.board), "--plan", str(self.plan))
        html = out.read_text(encoding="utf-8")
        self.assertIn('"walls"', html)          # plan 墙多边形进底图
        self.assertIn("帐心议事区", html)         # zone
        self.assertIn("长案", html)               # prop
        self.assertIn('"_plan"', html)            # 场景绑定切到 plan
        # S2 相机缺省 → 由 plan cam1 补位（pos [0.0, 1.6, -4.0]）
        self.assertIn("[0.0, 1.6, -4.0]", html)
        # 分镜自带 S1 机位不被 plan 覆盖
        self.assertIn("[0, 1.6, -3]", html)

    def test_standalone_plan_mode(self):
        out = self.proj / "推演" / "战略图_平面图_军帐.html"
        self._run("--plan", str(self.plan))
        html = out.read_text(encoding="utf-8")
        self.assertIn("平面图_军帐", html)
        self.assertIn("cam1", html)

    def test_plan_badge_warns_when_unbound(self):
        """PLAN 没有顶层 scene_ref（自由生成的平面图）→ 产物上必须出琥珀降级标记，
        不能只靠任务日志那句 [警告]：这文件是 /media 直开 + ⑥ iframe 里看的。"""
        self._run(str(self.board), "--plan", str(self.plan))
        html = (self.proj / "推演" / "战略图_test.html").read_text(encoding="utf-8")
        self.assertIn("未关联场景资产", html)
        self.assertIn("空间一致性未经校验", html)
        # 判色要用徽标自己的底色：#fbbf24 单独查是假的——模板里"正在说话的角色"描边就是它
        self.assertIn("#f59e0b22", html, "降级徽标必须是琥珀底，与匹配上的蓝绿底可分辨")

    def test_plan_badge_reports_match_ratio(self):
        plan = dict(PLAN, scene_ref="军帐")
        (self.proj / "推演" / "平面图_军帐.plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        board = dict(BOARD, shots=[dict(BOARD["shots"][0], scene_ref="@scene:军帐"),
                                   dict(BOARD["shots"][1], scene_ref="@scene:军帐")])
        self.board.write_text(json.dumps(board, ensure_ascii=False), encoding="utf-8")
        self._run(str(self.board), "--plan", str(self.plan))
        html = (self.proj / "推演" / "战略图_test.html").read_text(encoding="utf-8")
        self.assertIn("匹配 2/2 镜", html)
        self.assertNotIn("未经校验", html)
        self.assertNotIn("#f59e0b22", html, "匹配上了就不该出琥珀底徽标")
        self.assertIn("#38bdf822", html, "正常匹配给蓝绿底信息徽标")

    def test_standalone_plan_has_no_badge(self):
        """独立渲染一张平面图时底图就是主体，不存在"回退用图"，不该挂任何降级标记。"""
        self._run("--plan", str(self.plan))
        html = (self.proj / "推演" / "战略图_平面图_军帐.html").read_text(encoding="utf-8")
        self.assertNotIn("未经校验", html)


class ShotDiagramPlanTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.proj = Path(self._td.name)
        (self.proj / "分镜").mkdir()
        (self.proj / "推演").mkdir()
        self.board = self.proj / "分镜" / "test.json"
        self.board.write_text(json.dumps(BOARD, ensure_ascii=False), encoding="utf-8")
        self.plan = self.proj / "推演" / "平面图_军帐.plan.json"
        self.plan.write_text(json.dumps(PLAN, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        self._td.cleanup()

    def test_storyboard_with_plan_png(self):
        outdir = self.proj / "推演" / "平面图_test"
        with patch.object(sys, "argv", ["shot_diagram.py", str(self.board),
                                        "--plan", str(self.plan)]):
            shot_diagram.main()
        from PIL import Image
        for sid in ("S1", "S2"):
            p = outdir / f"{sid}.png"
            self.assertTrue(p.is_file(), sid)
            im = Image.open(p).convert("RGB")
            # 底图（墙/陈设/网格）+ 角色 + 相机：颜色数远超单色底
            self.assertGreater(len(set(im.getdata())), 50)

    def test_standalone_plan_png(self):
        outdir = self.proj / "推演" / "平面图_军帐"
        with patch.object(sys, "argv", ["shot_diagram.py", "--plan", str(self.plan)]):
            shot_diagram.main()
        from PIL import Image
        p = outdir / "cam1.png"
        self.assertTrue(p.is_file())
        im = Image.open(p).convert("RGB")
        self.assertGreater(len(set(im.getdata())), 50)


if __name__ == '__main__':
    unittest.main()
