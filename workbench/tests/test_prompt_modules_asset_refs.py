# -*- coding: utf-8 -*-
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workbench" / "tools"))


class PromptModuleAssetRefTests(unittest.TestCase):
    def test_storyboard_prompt_uses_asset_reference_rules_and_safe_asset_index(self):
        from prompt_modules import ASSET_REFERENCE_RULES, storyboard_prompt
        self.assertIn("@character:<id>", ASSET_REFERENCE_RULES)
        self.assertIn("@scene:<id>", ASSET_REFERENCE_RULES)
        self.assertIn("@prop:<id>", ASSET_REFERENCE_RULES)
        chars = [{
            "id": "hero", "name": "白咲蛛绪", "role": "主角",
            "appearance": {"look": "长黑发与八条银白步足"},
            "sheet_prompt": "禁止在分镜中展开这段长设定",
        }]
        scenes = [{
            "id": "room", "name": "教室",
            "geometry": ["北墙黑板", "东墙窗户"],
            "image_prompt": "全景俯视荷兰角概念图提示词",
        }]
        props = [{
            "id": "legs", "name": "蜘蛛步足", "kind": "叙事",
            "image_prompt": "银白甲壳与机械纹理",
        }]
        system, user = storyboard_prompt("白咲蛛绪从天花板坠落。", chars, scenes, props)
        self.assertIn("@character:<id>", system)
        self.assertIn("@scene:<id>", system)
        self.assertIn("@prop:<id>", system)
        self.assertIn('"ref"', user)
        self.assertNotIn("禁止在分镜中展开这段长设定", user)
        self.assertNotIn("全景俯视荷兰角概念图提示词", user)
        self.assertNotIn("银白甲壳与机械纹理", user)

    def test_actor_prompts_include_reference_rule(self):
        from prompt_modules import actor_prepare_prompt, actor_perform_prompt
        prepare_sys, _ = actor_prepare_prompt({"id": "S1"}, {})
        perform_sys, _ = actor_perform_prompt({"shot_id": "S1", "actor_ids": ["hero"]})
        self.assertIn("@character:<id>", prepare_sys)
        self.assertIn("@character:<id>", perform_sys)

    def test_storyboard_prompt_requires_natural_prompt_before_json(self):
        from prompt_modules import storyboard_prompt
        system, _ = storyboard_prompt("白咲蛛绪从天花板坠落。", [], [], [])
        self.assertIn("JSON 上方", system)
        self.assertIn("100~160 字", system)
        self.assertIn("negative", system)
        self.assertIn("台词由后期叠加", system)

    def test_storyboard_prompt_requires_compact_shots_to_avoid_output_truncation(self):
        from prompt_modules import storyboard_prompt
        system, _ = storyboard_prompt("一段剧情", [], [], [])
        self.assertIn("最多 20 镜", system)
        self.assertIn("negative 最多 4 条", system)


if __name__ == "__main__":
    unittest.main()


