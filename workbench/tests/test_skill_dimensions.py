# -*- coding: utf-8 -*-
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workbench" / "tools"))

import skill_lib


ROWS = [
    {"id": "three-act", "name": "三幕", "target": "script", "dimension": "script_structure", "enabled": True},
    {"id": "hook", "name": "钩子", "target": "script", "dimension": "script_pacing", "enabled": True},
    {"id": "slow", "name": "慢节奏", "target": "script", "dimension": "script_pacing", "enabled": True},
    {"id": "camera", "name": "机位", "target": "storyboard", "dimension": "storyboard_camera", "enabled": True},
]
BODIES = {row["id"]: row["name"] + "正文" for row in ROWS}


class SkillDimensionTests(unittest.TestCase):
    def test_distinct_script_dimensions_compose_in_fixed_order(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(skill_lib, "list_skills", return_value=ROWS), \
                mock.patch.object(skill_lib, "load_skill_text", side_effect=BODIES.get):
            skill_lib.set_project_style(td, {"script": "auto", "script_structure": "three-act", "script_pacing": "hook"})
            self.assertEqual(skill_lib.style_for(td, "script"), "三幕正文\n\n钩子正文\n")

    def test_explicit_dimension_overrides_legacy_choice_in_same_dimension(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(skill_lib, "list_skills", return_value=ROWS), \
                mock.patch.object(skill_lib, "load_skill_text", side_effect=BODIES.get):
            skill_lib.set_project_style(td, {"script": "hook", "script_pacing": "slow"})
            self.assertEqual(skill_lib.style_for(td, "script"), "慢节奏正文\n")

    def test_explicit_auto_disables_only_its_dimension(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(skill_lib, "list_skills", return_value=ROWS), \
                mock.patch.object(skill_lib, "load_skill_text", side_effect=BODIES.get):
            skill_lib.set_project_style(td, {"script": "hook", "script_pacing": "auto", "script_structure": "three-act"})
            self.assertEqual(skill_lib.style_for(td, "script"), "三幕正文\n")

    def test_legacy_choice_remains_unchanged_without_dimension_keys(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(skill_lib, "list_skills", return_value=ROWS), \
                mock.patch.object(skill_lib, "load_skill_text", side_effect=BODIES.get):
            skill_lib.set_project_style(td, {"script": "hook"})
            self.assertEqual(skill_lib.style_for(td, "script"), "钩子正文\n")
            saved = json.loads((Path(td) / "剧本" / "style.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["script"], "hook")

    def test_cross_target_id_cannot_enter_other_dimension(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(skill_lib, "list_skills", return_value=ROWS), \
                mock.patch.object(skill_lib, "load_skill_text", side_effect=BODIES.get):
            skill_lib.set_project_style(td, {"script": "auto", "script_structure": "camera"})
            self.assertEqual(skill_lib.style_for(td, "script"), "")

    def test_image_method_does_not_become_visual_style(self):
        from prompt_assembler import skill_positive
        with tempfile.TemporaryDirectory() as td:
            skill_lib.set_project_style(td, {"image": "identity-anchor"})
            self.assertEqual(skill_lib.image_visual_skill_id(td), "")
            self.assertEqual(skill_positive(td), "")
            prompt, _ = skill_lib.compose_asset_image_prompt(td, "一位角色", kind="character")
            self.assertIn("身份锚点", prompt)

    def test_visual_style_and_identity_method_can_coexist(self):
        from prompt_assembler import skill_positive
        with tempfile.TemporaryDirectory() as td:
            skill_lib.set_project_style(td, {"image": "ghibli-soft", "image_identity": "identity-anchor"})
            self.assertEqual(skill_lib.image_visual_skill_id(td), "ghibli-soft")
            shot_style = skill_positive(td)
            self.assertIn("日式动漫赛璐璐", shot_style)
            self.assertNotIn("角色三视图", shot_style)
            self.assertNotIn("身份锚点", shot_style)
            prompt, _ = skill_lib.compose_asset_image_prompt(td, "一位角色", kind="character")
            self.assertIn("日式动漫赛璐璐", prompt)
            self.assertIn("身份锚点", prompt)

    def test_builtin_skill_metadata_distinguishes_method_from_style(self):
        rows = {row["id"]: row for row in skill_lib.list_skills()}
        self.assertEqual(rows["identity-anchor"]["dimension"], "image_identity")
        self.assertEqual(rows["ghibli-soft"]["dimension"], "image_visual")
        self.assertEqual(rows["motion-prompt"]["dimension"], "storyboard_motion")

    def test_custom_skill_can_be_selected_in_its_dimension(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(skill_lib, "SKILLS_DIR", directory):
            skill_id = skill_lib.create_skill("script", "我的节奏", "script", "节奏方法", "按人物冲突调整节奏", "script_pacing")
            project = Path(directory) / "项目"
            project.mkdir()
            skill_lib.set_project_style(str(project), {"script": "auto", "script_pacing": skill_id})
            self.assertIn("按人物冲突调整节奏", skill_lib.style_for(str(project), "script"))
            self.assertEqual(skill_lib.list_skills()[0]["dimension"], "script_pacing")

    def test_custom_skill_rejects_wrong_target_dimension(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(skill_lib, "SKILLS_DIR", directory):
            with self.assertRaises(ValueError):
                skill_lib.create_skill("script", "错误", "script", "", "正文", "storyboard_motion")

    def test_cinematic_style_does_not_override_shot_lens(self):
        from prompt_assembler import assemble_shot_prompt
        with tempfile.TemporaryDirectory() as td:
            skill_lib.set_project_style(td, {"image": "cinematic-real"})
            result = assemble_shot_prompt(
                {"id": "S1", "action": "远处山峦", "shot_size": "大远景", "lens": "24mm"},
                {"dir": td, "board": {"actors": {}, "shots": []}, "media_type": "image"},
            )
            self.assertIn("24mm", result["prompt_assembled"])
            self.assertNotIn("85mm", result["prompt_assembled"])

    def test_script_pacing_does_not_override_project_episode_duration(self):
        import prompt_modules as pm
        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            (project / "剧本").mkdir()
            (project / "剧本" / "brief.json").write_text(
                json.dumps({"episode_minutes": 5}), encoding="utf-8")
            skill_lib.set_project_style(td, {"script": "shortdrama-hook"})
            system, _ = pm.outline_prompt("一部短剧", eps_n=4, style_text=skill_lib.style_for(td, "script"), proj=td)
            self.assertIn("5 分钟", system)
            self.assertNotIn("1~2 分钟", system)

    def test_imported_script_episode_split_uses_project_duration(self):
        import prompt_modules as pm
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "剧本").mkdir()
            (project / "剧本" / "brief.json").write_text(
                json.dumps({"episode_minutes": 0.5}), encoding="utf-8")
            system, _ = pm.episodes_prompt("原有剧本文本", proj=directory)
            self.assertIn("0.5 分钟", system)
            self.assertNotIn("3~15 分钟", system)


if __name__ == "__main__":
    unittest.main()
