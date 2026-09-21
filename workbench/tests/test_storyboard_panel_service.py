# -*- coding: utf-8 -*-
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools")))
from storyboard_panel_service import preview_panel, save_panel, save_grid


class PanelServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        os.makedirs(os.path.join(self.root, "分镜"))
        self.board = {"shots": [{"id": "S1", "content": "少女从天花板下坠", "shot_size": "中景"}]}

    def tearDown(self):
        self.tmp.cleanup()

    def test_preview_compiles_policy_and_shot_without_legacy_prompt(self):
        panel = {"id": "P1", "shot_ids": ["S1"], "beat": "少女悬在半空",
                 "composition": {"aspect_ratio": "16:9", "shot_size": "中景"},
                 "visible_refs": ["@character:girl"]}
        draft = {"panel_id": "P1", "visual_description": "@character:girl 悬在半空，向下望",
                 "used_refs": ["@character:girl"], "local_negative": ["对白框"]}
        result = preview_panel(self.root, panel, draft, [])
        self.assertIn("少女", result["prompt"])
        self.assertNotIn("对白框", result["prompt"])
        self.assertIn("对白框", result["negative_prompt"])
        self.assertTrue(result["policy_version"])

    def test_policy_positive_does_not_contain_asset_sheet_or_negative_rules(self):
        from storyboard_panel_service import image_policy
        import json
        os.makedirs(os.path.join(self.root, "剧本"))
        with open(os.path.join(self.root, "剧本", "style.json"), "w", encoding="utf-8") as fh:
            json.dump({"image": "ghibli-soft", "anchor": "日式动画"}, fh)
        policy = image_policy(self.root)
        self.assertNotIn("三视图", policy["positive"])
        self.assertNotIn("绝对禁止", policy["positive"])
        self.assertIn("3D渲染", ",".join(str(x) for x in policy["negative"]))

    def test_save_grid_persists_ordered_panel_ids(self):
        import json
        path = os.path.join(self.root, "分镜", "剧本_E1.json")
        board = dict(self.board, panels=[
            {"id": "P1", "shot_ids": ["S1"], "beat": "盖板弹开"},
            {"id": "P2", "shot_ids": ["S1"], "beat": "少女下坠"},
        ])
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(board, fh)
        saved = save_grid(self.root, "剧本_E1.json", {"id": "G1", "layout": "3x3", "panel_ids": ["P2", "P1"]})
        self.assertEqual(saved["grids"][0]["panel_ids"], ["P2", "P1"])

    def test_save_panel_preserves_shots(self):
        path = os.path.join(self.root, "分镜", "剧本_E1.json")
        import json
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.board, fh)
        panel = {"id": "P1", "shot_ids": ["S1"], "beat": "少女悬在半空",
                 "time_role": "mid_action", "composition": {"aspect_ratio": "16:9"},
                 "visible_refs": [], "source": {"kind": "script_shot", "shot_id": "S1"}}
        saved = save_panel(self.root, "剧本_E1.json", panel)
        self.assertEqual(saved["panels"][0]["id"], "P1")
        self.assertEqual(saved["shots"], self.board["shots"])


if __name__ == "__main__":
    unittest.main()
