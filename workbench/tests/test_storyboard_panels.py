# -*- coding: utf-8 -*-
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools")))
from storyboard_panels import validate_board_panels, ensure_panel_for_shot, compile_image_request, build_grid


class StoryboardPanelTests(unittest.TestCase):
    def setUp(self):
        self.board = {"shots": [
            {"id": "S1", "content": "盖板弹开", "action": "盖板弹开后少女下坠", "shot_size": "全景", "angle": "平视"},
            {"id": "S2", "content": "少女下坠", "action": "少年抬头"},
        ]}

    def test_panel_can_link_multiple_shots_without_copying_motion(self):
        panel = ensure_panel_for_shot(self.board, "S1")
        panel["shot_ids"].append("S2")
        self.assertEqual(validate_board_panels(self.board), [])
        self.assertEqual(panel["source"]["kind"], "script_shot")
        self.assertNotIn("prompt", panel)
        self.assertEqual(panel["beat"], "盖板弹开")

    def test_rejects_unknown_shot_and_duplicate_panel(self):
        self.board["panels"] = [{"id": "P1", "shot_ids": ["S99"], "beat": "画面"},
                                {"id": "P1", "shot_ids": ["S1"], "beat": "画面"}]
        errors = validate_board_panels(self.board)
        self.assertTrue(any("S99" in x for x in errors))
        self.assertTrue(any("P1" in x for x in errors))

    def test_compiler_is_stable_and_separates_negative(self):
        panel = {"id": "P1", "shot_ids": ["S1"], "beat": "少女悬在空中",
                 "composition": {"aspect_ratio": "16:9"}, "visible_refs": ["@character:girl"]}
        draft = {"panel_id": "P1", "visual_description": "@character:girl 悬在空中，视线朝下",
                 "local_negative": ["仰视", "文字"], "used_refs": ["@character:girl"]}
        policy = {"version": "v1", "positive": "日式赛璐璐风格", "negative": ["文字", "水印"]}
        result = compile_image_request(panel, draft, policy, [])
        self.assertEqual(result["negative_prompt"], "文字,水印,仰视")
        self.assertEqual(result["prompt"].count("日式赛璐璐风格"), 1)
        self.assertNotIn("仰视", result["prompt"])
        self.assertEqual(result["prompt"], compile_image_request(panel, draft, policy, [])["prompt"])
        self.assertEqual(result["panel_id"], "P1")

    def test_compiler_records_reference_purpose_in_sent_prompt(self):
        panel = {"id": "P1", "shot_ids": ["S1"], "composition": {"aspect_ratio": "16:9"},
                 "visible_refs": []}
        draft = {"panel_id": "P1", "visual_description": "少女向下望", "used_refs": []}
        result = compile_image_request(panel, draft, {}, [{"path": "素材/人物/girl.png", "purpose": "角色身份"}])
        self.assertIn("角色身份", result["prompt"])
        self.assertEqual(result["image_refs"][0]["path"], "素材/人物/girl.png")

    def test_compiler_rejects_unselected_asset(self):
        with self.assertRaisesRegex(ValueError, "未列入画格"):
            compile_image_request(
                {"id": "P1", "composition": {}, "visible_refs": []},
                {"panel_id": "P1", "visual_description": "@character:extra", "used_refs": ["@character:extra"]},
                {}, [],
            )

    def test_grid_keeps_empty_cells(self):
        panels = [{"id": "P1"}, {"id": "P2"}]
        grid = build_grid(panels, "3x3")
        self.assertEqual(len(grid["panel_ids"]), 2)
        self.assertEqual(grid["capacity"], 9)


if __name__ == "__main__":
    unittest.main()
