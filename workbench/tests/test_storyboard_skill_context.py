# -*- coding: utf-8 -*-
"""分镜生成的方法选择、画风边界与三类提示词离线回归。"""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workbench" / "tools"))

import prompt_modules as pm
import skill_lib


def row(sid, target, dimension="", enabled=True):
    return {"id": sid, "name": sid, "target": target, "dimension": dimension, "enabled": enabled}


ROWS = [row("legacy", "storyboard"), row("camera", "storyboard", "storyboard_camera"),
        row("keyframe", "storyboard", "storyboard_keyframe"),
        row("motion", "storyboard", "storyboard_motion"),
        row("off", "storyboard", "storyboard_motion", False),
        row("visual", "image", "image_visual"), row("other-visual", "image", "image_visual"),
        row("identity", "image", "image_identity")]
BODIES = {r["id"]: r["id"] + "独立规则正文" for r in ROWS}


class StoryboardSkillContextTests(unittest.TestCase):
    def context(self, style, bodies=None, persist_defaults=False):
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td) / "剧本"
            directory.mkdir()
            file = directory / "style.json"
            file.write_text(json.dumps(style), encoding="utf-8")
            before = file.read_bytes()
            with mock.patch.object(skill_lib, "list_skills", return_value=ROWS), \
                    mock.patch.object(skill_lib, "load_skill_text", side_effect=(bodies or BODIES).get):
                result = skill_lib.storyboard_generation_context(td, persist_defaults=persist_defaults)
            if not persist_defaults:
                self.assertEqual(file.read_bytes(), before)
            return result

    def test_each_selected_dimension_is_injected_once_and_identity_is_excluded(self):
        result = self.context({"storyboard": "camera", "storyboard_camera": "camera",
                               "storyboard_keyframe": "keyframe", "storyboard_motion": "motion",
                               "image": "visual", "image_identity": "identity"})
        for sid in ("camera", "keyframe", "motion", "visual"):
            self.assertEqual(result["text"].count(BODIES[sid]), 1)
        self.assertNotIn(BODIES["identity"], result["text"])
        self.assertNotIn(BODIES["other-visual"], result["text"])
        self.assertEqual({snap["id"] for snap in result["snapshot"].values()},
                         {"camera", "keyframe", "motion", "visual"})

    def test_all_auto_leaves_no_skill_text_or_snapshot(self):
        result = self.context({key: "auto" for key in
                               ("storyboard", "image", "storyboard_camera", "storyboard_keyframe", "storyboard_motion")})
        self.assertEqual(result, {"text": "", "snapshot": {}})

    def test_missing_selections_do_not_inject_all_enabled_skills(self):
        self.assertEqual(self.context({}), {"text": "", "snapshot": {}})

    def test_legacy_image_skill_without_dimension_is_still_injected(self):
        legacy = {"id": "old-image", "name": "旧画风", "target": "image", "enabled": True}
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(skill_lib, "list_skills", return_value=[legacy]), \
                mock.patch.object(skill_lib, "load_skill_text", return_value="旧画风正文"):
            context = skill_lib.storyboard_generation_context(td, persist_defaults=False)
        self.assertIn("旧画风正文", context["text"])
        self.assertEqual(context["snapshot"]["image"]["id"], "old-image")

    def test_dimension_auto_suppresses_matching_legacy_choice(self):
        for legacy in ("camera", "legacy"):
            with self.subTest(legacy=legacy):
                result = self.context({"storyboard": legacy, "storyboard_camera": "auto", "image": "auto"})
                self.assertEqual(result["text"], "")

    def test_explicit_camera_replaces_legacy_without_dimension(self):
        result = self.context({"storyboard": "legacy", "storyboard_camera": "camera", "image": "auto"})
        self.assertIn(BODIES["camera"], result["text"])
        self.assertNotIn(BODIES["legacy"], result["text"])

    def test_disabled_invalid_and_cross_target_choices_are_not_replaced(self):
        for selection in ("off", "missing", "visual"):
            with self.subTest(selection=selection):
                result = self.context({"storyboard": "auto", "storyboard_motion": selection, "image": "auto"})
                self.assertEqual(result["text"], "")

    def test_duplicate_body_only_has_one_actual_injection_snapshot(self):
        bodies = dict(BODIES, keyframe=BODIES["camera"])
        result = self.context({"storyboard": "camera", "storyboard_keyframe": "keyframe", "image": "auto"}, bodies)
        self.assertEqual(result["text"].count(BODIES["camera"]), 1)
        self.assertEqual(len(result["snapshot"]), 1)
        self.assertEqual(result["snapshot"]["storyboard"]["sha"],
                         hashlib.sha256(BODIES["camera"].encode("utf-8")).hexdigest()[:12])

    def test_selected_visual_style_is_preserved_in_generation_context(self):
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td) / "剧本"
            directory.mkdir()
            (directory / "style.json").write_text(json.dumps({
                "storyboard": "realism-cold", "image": "ghibli-soft", "image_identity": "identity-anchor"
            }), encoding="utf-8")
            result = skill_lib.storyboard_generation_context(td, persist_defaults=False)
        with mock.patch.object(pm, "_knowledge", return_value=""), mock.patch.object(pm, "_override", return_value=None):
            system, _ = pm.storyboard_prompt("角色站在门边。", [], [], style_text=result["text"])
        self.assertIn("日式动漫赛璐璐", system)
        self.assertIn("项目唯一画风", system)
        self.assertIn("镜头方法不能将其改成另一种媒介或真人摄影", system)
        self.assertNotIn("角色三视图", result["text"])

    def test_three_prompts_describe_separate_outputs_with_ordered_grid(self):
        with mock.patch.object(pm, "_knowledge", return_value=""), mock.patch.object(pm, "_override", return_value=None):
            system, _ = pm.storyboard_prompt("角色抬手至肩部停住。", [], [])
        for field in ("prompt_image", "prompt_video", "prompt_grid"):
            self.assertIn('"' + field + '"', system)
        self.assertIn("行列布局", system)
        self.assertIn("从左到右从上到下", system)
        self.assertIn("逐格描述起点/变化/终点", system)
        self.assertIn("不要求每镜套齐全部技巧", system)
        self.assertEqual(system.count("【制作提示词契约"), 1)

    def test_entity_and_character_prompts_share_separate_appearance_contract(self):
        from character_design import appearance_guidance
        with mock.patch.object(pm, "_knowledge", return_value=""), mock.patch.object(pm, "_override", return_value=None):
            entity_system, _ = pm.units_entity_prompt({"episodes": []})
            character_system, _ = pm.characters_prompt("甲走入房间。")
        for system in (entity_system, character_system):
            self.assertEqual(system.count(appearance_guidance()), 1)
            self.assertIn('"appearance"', system)
            self.assertIn("appearance.proposals", system)
            self.assertIn("不凭空填具体岁数", system)
        self.assertNotIn("外貌/服装/材质/画风词一概不出现", entity_system)
        self.assertNotIn("voice/sheet_prompt 是创作必需，允许基于人设合理设计", character_system)

    def test_selected_director_methods_preserve_project_identity_and_style(self):
        for sid in ("realism-cold", "hk-crime", "wes-symmetry", "zhangyimou-color"):
            with self.subTest(skill=sid):
                body = skill_lib.load_skill_text(sid)
                self.assertIn("画风", body)
                self.assertNotIn("1.5s", body)
        frozen = skill_lib.load_skill_text("frozen-keyframe")
        self.assertIn("不复制身份锚点长文或五视图布局", frozen)
        self.assertIn("prompt_grid", frozen)


if __name__ == "__main__":
    unittest.main()
