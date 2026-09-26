# -*- coding: utf-8 -*-
"""validate_plan（plan v1 确定性闸）：错误/警告全规则用例 + 几何小函数 + CLI 退出码。"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

import validate_plan as vp


def valid_plan():
    """规范文档自带样例（军帐）——基线合法（仅触发 1 条穿家具警告）。"""
    return {
        "version": 1, "name": "军帐",
        "canvas": {"w": 12.0, "h": 9.0},
        "room": {"walls": [[0, 0], [12, 0], [12, 9], [0, 9]],
                 "openings": [{"wall": 1, "offset": 5.0, "width": 1.2, "kind": "door"}]},
        "props": [{"id": "p1", "label": "长案", "shape": "rect", "center": [6.0, 4.5],
                   "size": [3.0, 1.0], "rot": 0}],
        "actors": [{"id": "c", "name": "主角", "color": "#e74c3c", "pos": [6.0, 6.0], "facing": 270}],
        "paths": [{"actor": "c", "points": [[6.0, 6.0, 0], [4.0, 4.0, 2.5], [6.0, 3.0, 5]],
                   "style": "walk"}],
        "cameras": [{"id": "cam1", "pos": [6.0, 8.5], "look": [6.0, 4.5], "fov": 50}],
        "zones": [{"id": "z1", "label": "帐心议事区", "rect": [3.0, 2.0, 6.0, 5.0],
                   "scene_ref": "军帐"}],
    }


def codes(result, key="errors"):
    return {item["code"] for item in result[key]}


class ValidBaselineTests(unittest.TestCase):
    def test_doc_example_passes(self):
        r = vp.validate_document(valid_plan())
        self.assertEqual(r["errors"], [])
        # 样例路径 6,6→4,4 确实斜穿长案（3x1 @6,4.5）：警告是正确判定
        self.assertIn("PATH_THROUGH_PROP", codes(r, "warnings"))

    def test_top_level_scene_ref_must_be_asset_id(self):
        """顶层 scene_ref 规范定为场景资产 id（平面图规范 :35/:104）；zones 允许写 name，两套集合不能混用。"""
        ids, only_ids = {"loc_tent", "军帐"}, {"loc_tent"}
        ok = valid_plan(); ok["scene_ref"] = "loc_tent"
        self.assertNotIn("SCENE_REF_DANGLING",
                         codes(vp.validate_document(ok, scene_ids=ids, scene_id_set=only_ids)))
        named = valid_plan(); named["scene_ref"] = "军帐"      # 对 zones 合法、对顶层非法
        self.assertIn("SCENE_REF_DANGLING",
                      codes(vp.validate_document(named, scene_ids=ids, scene_id_set=only_ids)))
        renamed = valid_plan(); renamed["scene_ref"] = "loc_renamed"   # ② 重编号后的悬空引用
        self.assertIn("SCENE_REF_DANGLING",
                      codes(vp.validate_document(renamed, scene_ids=ids, scene_id_set=only_ids)))
        # 老调用方不传仅 id 集合时退回宽集合；完全不传场景清单则跳过该项，避免误报
        self.assertNotIn("SCENE_REF_DANGLING", codes(vp.validate_document(named, scene_ids=ids)))
        self.assertNotIn("SCENE_REF_DANGLING", codes(vp.validate_document(named)))

    def test_scene_ref_skipped_without_scene_ids(self):
        plan = valid_plan()
        plan["zones"][0]["scene_ref"] = "不存在"
        r = vp.validate_document(plan)  # 未提供场景清单 → 跳过该子项
        self.assertNotIn("REF_DANGLING", codes(r))
        r2 = vp.validate_document(plan, scene_ids={"军帐"})
        self.assertIn("REF_DANGLING", codes(r2))
        r3 = vp.validate_document(valid_plan(), scene_ids={"军帐"})
        self.assertNotIn("REF_DANGLING", codes(r3))


class ErrorRuleTests(unittest.TestCase):
    def errors(self, plan, **kw):
        return codes(vp.validate_document(plan, **kw))

    def test_plan_type_and_empty(self):
        self.assertIn("PLAN_TYPE", codes(vp.validate_document([1, 2])))
        plan = {"canvas": {"w": 12, "h": 9}}
        self.assertIn("EMPTY_PLAN", codes(vp.validate_document(plan)))
        # props/actors 其一非空即有效
        plan["actors"] = [{"id": "c", "pos": [1, 1]}]
        self.assertNotIn("EMPTY_PLAN", codes(vp.validate_document(plan)))

    def test_canvas_size(self):
        plan = valid_plan()
        plan["canvas"] = {"w": 0, "h": 9}
        self.assertIn("CANVAS_SIZE", self.errors(plan))
        plan["canvas"] = {"w": 12}  # 缺 h
        self.assertIn("CANVAS_SIZE", self.errors(plan))

    def test_point_bounds(self):
        plan = valid_plan()
        plan["actors"][0]["pos"] = [6, 20]
        self.assertIn("POINT_BOUNDS", self.errors(plan))
        plan = valid_plan()
        plan["room"]["walls"][1] = [13, 0]
        self.assertIn("POINT_BOUNDS", self.errors(plan))
        plan = valid_plan()
        plan["zones"][0]["rect"] = [3, 2, 10, 5]  # 右下角落出画布
        self.assertIn("POINT_BOUNDS", self.errors(plan))
        plan = valid_plan()
        plan["actors"][0]["pos"] = ["x", 1]
        self.assertIn("POINT_INVALID", self.errors(plan))

    def test_size_invalid(self):
        plan = valid_plan()
        plan["props"][0]["size"] = [3, 0]
        self.assertIn("SIZE_INVALID", self.errors(plan))
        plan = valid_plan()
        plan["props"][0] = {"id": "p9", "shape": "circle", "center": [2, 2], "size": []}
        self.assertIn("SIZE_INVALID", self.errors(plan))
        plan = valid_plan()
        plan["zones"][0]["rect"] = [3, 2, 0, 5]
        self.assertIn("SIZE_INVALID", self.errors(plan))
        plan = valid_plan()
        plan["room"]["openings"][0]["width"] = -1
        self.assertIn("SIZE_INVALID", self.errors(plan))

    def test_id_dup_each_domain(self):
        for domain, row in (("props", {"id": "p1", "shape": "rect", "center": [1, 1], "size": [1, 1]}),
                            ("actors", {"id": "c", "pos": [1, 1]}),
                            ("cameras", {"id": "cam1", "pos": [1, 1], "look": [2, 2]}),
                            ("zones", {"id": "z1", "rect": [0, 0, 1, 1]})):
            plan = valid_plan()
            plan[domain].append(dict(row))
            with self.subTest(domain=domain):
                self.assertIn("ID_DUP", self.errors(plan))
        plan = valid_plan()
        del plan["props"][0]["id"]
        self.assertIn("ID_DUP", self.errors(plan))

    def test_ref_dangling(self):
        plan = valid_plan()
        plan["paths"][0]["actor"] = "nobody"
        self.assertIn("REF_DANGLING", self.errors(plan))

    def test_openings(self):
        plan = valid_plan()
        plan["room"]["openings"][0]["wall"] = 4  # 共 4 段（0..3）
        self.assertIn("OPENING_WALL", self.errors(plan))
        plan = valid_plan()
        plan["room"]["openings"][0]["wall"] = -1
        self.assertIn("OPENING_WALL", self.errors(plan))
        plan = valid_plan()
        # 段 1 = [12,0]→[12,9] 长 9；offset 8 + width 1.2 = 9.2 超出
        plan["room"]["openings"][0] = {"wall": 1, "offset": 8.0, "width": 1.2, "kind": "door"}
        self.assertIn("OPENING_SPAN", self.errors(plan))
        plan = valid_plan()
        plan["room"]["openings"][0]["offset"] = -0.5
        self.assertIn("OPENING_SPAN", self.errors(plan))
        # 末段回卷（段 3 = [0,9]→[0,0] 长 9）合法
        plan = valid_plan()
        plan["room"]["openings"] = [{"wall": 3, "offset": 8.0, "width": 1.0, "kind": "window"}]
        self.assertNotIn("OPENING_SPAN", self.errors(plan))

    def test_paths_rules(self):
        plan = valid_plan()
        plan["paths"][0]["points"] = [[6, 6, 0]]
        self.assertIn("PATH_POINTS", self.errors(plan))
        plan = valid_plan()
        plan["paths"][0]["points"] = [[6, 6, 0], [4, 4, 0]]  # t 非严格递增
        self.assertIn("PATH_T", self.errors(plan))
        plan = valid_plan()
        plan["paths"][0]["points"] = [[6, 6], [4, 4, 1]]  # 缺 t
        self.assertIn("PATH_T", self.errors(plan))

    def test_fov_range(self):
        for bad in (3, 171, "abc"):
            plan = valid_plan()
            plan["cameras"][0]["fov"] = bad
            with self.subTest(bad=bad):
                self.assertIn("FOV_RANGE", self.errors(plan))
        plan = valid_plan()
        del plan["cameras"][0]["fov"]  # 缺省按 50，不报错
        self.assertNotIn("FOV_RANGE", self.errors(plan))
        plan["cameras"][0]["fov"] = 5
        self.assertNotIn("FOV_RANGE", self.errors(plan))
        plan["cameras"][0]["fov"] = 170
        self.assertNotIn("FOV_RANGE", self.errors(plan))


class WarningRuleTests(unittest.TestCase):
    def warns(self, plan):
        return codes(vp.validate_document(plan), "warnings")

    def test_actor_prop_camera_outside_room(self):
        plan = valid_plan()
        plan["paths"] = []  # 去掉穿家具警告干扰
        # 缩小房间到画布中央：[6,8.5] 在画布内但在墙外（警告层与越界错误层正交）
        plan["room"]["walls"] = [[2, 2], [10, 2], [10, 7], [2, 7]]
        plan["room"]["openings"] = []
        plan["actors"][0]["pos"] = [6, 8.5]
        plan["props"][0]["center"] = [6, 8.5]
        plan["cameras"][0]["pos"] = [6, 8.5]
        w = self.warns(plan)
        self.assertIn("OUTSIDE_ROOM", w)
        self.assertIn("CAMERA_OUTSIDE", w)

    def test_path_through_prop_and_clear(self):
        plan = valid_plan()
        self.assertIn("PATH_THROUGH_PROP", self.warns(plan))
        # 绕开长案（沿 y=7 走，不碰 y∈[4,5] 的案子）
        plan["paths"][0]["points"] = [[6, 6.5, 0], [2, 7, 2], [6, 7, 5]]
        self.assertNotIn("PATH_THROUGH_PROP", self.warns(plan))

    def test_prop_overlap(self):
        plan = valid_plan()
        plan["props"].append({"id": "p2", "shape": "rect", "center": [6.2, 4.6],
                              "size": [3.0, 1.0]})  # 与 p1 重叠 ~93%
        self.assertIn("PROP_OVERLAP", self.warns(plan))
        plan = valid_plan()
        plan["props"].append({"id": "p2", "shape": "rect", "center": [1.0, 1.0],
                              "size": [1.0, 1.0]})  # 远离 p1
        self.assertNotIn("PROP_OVERLAP", self.warns(plan))

    def test_no_room_skips_room_warnings(self):
        plan = valid_plan()
        del plan["room"]
        plan["paths"] = []
        plan["cameras"][0]["pos"] = [0.1, 0.1]
        w = self.warns(plan)
        self.assertNotIn("OUTSIDE_ROOM", w)
        self.assertNotIn("CAMERA_OUTSIDE", w)


class GeometryTests(unittest.TestCase):
    def test_point_in_polygon(self):
        sq = [[0, 0], [10, 0], [10, 10], [0, 10]]
        self.assertTrue(vp.point_in_polygon([5, 5], sq))
        self.assertTrue(vp.point_in_polygon([0, 5], sq))      # 边界算在内
        self.assertFalse(vp.point_in_polygon([11, 5], sq))
        self.assertFalse(vp.point_in_polygon([-1, 5], sq))

    def test_seg_intersects_rotated_rect(self):
        # 未旋转：横穿命中
        self.assertTrue(vp.seg_intersects_rotated_rect([0, 0], [4, 0], [2, 0], [2, 1], 0))
        # 旋转 90° 后宽高调换：原来从上方穿过不命中，旋转后命中
        self.assertFalse(vp.seg_intersects_rotated_rect([0, 2], [4, 2], [2, 0], [4, 0.5], 0))
        self.assertTrue(vp.seg_intersects_rotated_rect([0, 2], [4, 2], [2, 0], [4, 0.5], 90))
        # 端点在矩形内
        self.assertTrue(vp.seg_intersects_rotated_rect([2, 0], [10, 10], [2, 0], [2, 1], 0))
        # 完全不相交
        self.assertFalse(vp.seg_intersects_rotated_rect([0, 5], [1, 6], [2, 0], [2, 1], 0))


class CliTests(unittest.TestCase):
    def _run(self, plan, *extra):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "plan.json")
            json.dump(plan, open(p, "w", encoding="utf-8"), ensure_ascii=False)
            r = subprocess.run([sys.executable,
                                str(Path(__file__).resolve().parents[1] / 'tools' / 'validate_plan.py'),
                                p, *extra],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            return r

    def test_cli_exit_codes(self):
        ok = self._run(valid_plan())
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        self.assertIn("校验通过", ok.stdout)
        bad = valid_plan()
        bad["cameras"][0]["fov"] = 999
        r = self._run(bad)
        self.assertEqual(r.returncode, 1)
        self.assertIn("fov", r.stdout)
        self.assertIn("校验未通过", r.stdout)

    def test_cli_scenes_arg(self):
        plan = valid_plan()
        plan["zones"][0]["scene_ref"] = "不存在"
        with tempfile.TemporaryDirectory() as td:
            sc = os.path.join(td, "场景.json")
            json.dump({"scenes": [{"id": "军帐", "name": "军帐"}]},
                      open(sc, "w", encoding="utf-8"), ensure_ascii=False)
            r = self._run(plan, "--scenes", sc)
        self.assertEqual(r.returncode, 1)
        self.assertIn("scene_ref", r.stdout)


if __name__ == '__main__':
    unittest.main()
