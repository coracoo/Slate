# -*- coding: utf-8 -*-
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workbench" / "tools"))


class ShotPromptTests(unittest.TestCase):
    def setUp(self):
        self.assets = {
            "characters": [
                {"ref": "@character:hero", "id": "hero", "name": "白咲蛛绪", "path": "素材/人物/hero.png"},
                {"ref": "@character:student", "id": "student", "name": "佐藤陆", "path": "素材/人物/student.png"},
            ],
            "scene": {"ref": "@scene:room", "id": "room", "name": "教室", "path": "素材/场景/room.png"},
            "props": [{"ref": "@prop:legs", "id": "legs", "name": "蜘蛛步足", "path": "素材/道具/legs.png"}],
        }
        self.shot = {
            "id": "S03", "dur": 2.5, "shot_size": "中景", "move": "跟", "angle": "平视",
            "prompt": "白咲蛛绪从天花板笔直下坠，目标是熟睡的佐藤陆。",
            "scene": "room",
            "speaker": "xiaoyu",
            "lines": [{"speaker": "xiaoyu", "line": "呜哇——！"}],
            "actor_refs": ["@character:hero", "@character:student", "@character:xiaoyu"],
            "prop_refs": ["@prop:legs"],
            "action_by_actor": {
                "@character:hero": "从天花板笔直下坠，八条步足保持下坠姿态",
                "@character:student": "趴在课桌上熟睡",
                "@character:xiaoyu": "在侧后方惊讶地看向坠落者",
            },
            "scene_action": "普通高中教室，窗光从侧后方进入",
        }

    def test_builds_standard_json_with_refs_and_action_only(self):
        from shot_prompt import build_shot_prompt, validate_shot_prompt, render_prompt_text
        value = build_shot_prompt(self.shot, self.assets, media_type="image", style_ref="@style:ghibli-soft")
        self.assertEqual(value["schema"], "shot-prompt-v1")
        self.assertEqual(value["camera"]["aspect_ratio"], "16:9")
        self.assertEqual([x["asset"] for x in value["actors"]], [
            "@character:hero", "@character:student", "@character:xiaoyu"
        ])
        self.assertEqual(value["actors"][0]["action"], "从天花板笔直下坠，八条步足保持下坠姿态")
        self.assertNotIn("appearance", value["actors"][0])
        self.assertNotIn("sheet_prompt", str(value))
        self.assertNotIn("image_prompt", str(value))
        self.assertEqual(value["dialogue"][0]["speaker"], "@character:xiaoyu")
        self.assertEqual(validate_shot_prompt(value), [])
        rendered = render_prompt_text(value)
        self.assertIn("@character:hero", rendered)
        self.assertIn("八条步足保持下坠姿态", rendered)
        self.assertNotIn("不应被展开", rendered)

    def test_explicit_shot_camera_wins_without_scene_concept_camera_text(self):
        from shot_prompt import build_shot_prompt
        value = build_shot_prompt({
            "id": "S1", "shot_size": "中景", "move": "跟", "angle": "平视",
            "scene_ref": "@scene:room", "scene_action": "教室背景"
        }, {"scene": {
            "ref": "@scene:room", "name": "教室",
            "image_prompt": "全景构图，从后部朝讲台方向俯视并使用荷兰角"
        }}, media_type="image")
        self.assertEqual(value["camera"]["shot_size"], "中景")
        self.assertEqual(value["camera"]["angle"], "平视")
        self.assertNotIn("俯视", str(value))
        self.assertNotIn("荷兰角", str(value))


    def test_natural_prompt_precedes_structured_refs_and_keeps_actions_only(self):
        from shot_prompt import build_shot_prompt, render_prompt_text
        shot = dict(self.shot)
        shot["prompt"] = (
            "16:9 横构图，教室内中景，平视，跟随坠落动作后的最终静帧。"
            "画面同时包含白咲蛛绪、熟睡的佐藤陆和小雨。"
        )
        value = build_shot_prompt(shot, self.assets, media_type="image", style_ref="@style:ghibli-soft")
        rendered = render_prompt_text(value)
        self.assertTrue(rendered.startswith("16:9 横构图"))
        self.assertIn("白咲蛛绪", rendered)
        self.assertIn("从天花板笔直下坠", rendered)
        self.assertIn("资产引用：@scene:room", rendered)
        self.assertNotIn("短黑发", rendered)
        self.assertNotIn("sheet_prompt", rendered)

if __name__ == "__main__":
    unittest.main()
