# -*- coding: utf-8 -*-
"""提示词分层与候选采用边界的离线回归。"""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workbench" / "tools"))

import prompt_modules as pm
from prompt_compiler import compile_shot


def sample_board():
    return {"actors": {"a": {"name": "甲"}, "b": {"name": "乙"}},
            "acting_context": {"actor_cards": {"a": {"personality": "背景甲"},
                                                "b": {"personality": "背景乙"}}},
            "shots": [{"id": "S1", "dur": 4, "scene_ref": "@scene:room",
                       "actor_refs": ["a"], "action": "甲站在门边",
                       "performance": {"status": "ready", "source_hash": "",
                                       "packet": {"actors": [{"actor_id": "a", "beats": [
                                           {"at": 0, "duration": 2, "visible_action": "候选动作",
                                            "voice": "候选声音", "intent": "隐藏动机"}]}]}}}]}


class PromptEngineeringBoundariesTests(unittest.TestCase):
    def test_unselected_or_stale_performance_never_enters_any_output_channel(self):
        for mode, source_hash in (("baseline", ""), ("stateful", "expired")):
            with self.subTest(mode=mode):
                board = sample_board()
                board["shots"][0]["performance"]["source_hash"] = source_hash
                result = compile_shot(board, "S1", mode=mode)
                self.assertFalse(result["performance_used"])
                self.assertNotIn("候选动作", json.dumps(result["prompt_json"], ensure_ascii=False))
                self.assertNotIn("候选动作", result["text"])
                self.assertEqual(result["voice_notes"], [])

    def test_empty_shot_does_not_inherit_the_full_cast(self):
        board = sample_board()
        board["shots"] = [{"id": "S1", "dur": 4, "action": "空房间，门缓慢打开"}]
        result = compile_shot(board, "S1", mode="stateful")
        self.assertFalse(any(ref.startswith("@character:") for ref in result["asset_refs"]))
        self.assertNotIn("背景甲", result["text"])
        self.assertNotIn("背景乙", result["text"])

    def test_image_performance_is_visible_single_instant(self):
        result = compile_shot(sample_board(), "S1", mode="stateful", media_type="image")
        self.assertIn("候选动作", result["text"])
        self.assertNotIn("候选声音", result["text"])
        self.assertNotIn("隐藏动机", result["text"])
        self.assertNotIn("0s-2s", result["text"])

    def test_performance_cannot_add_offscreen_actors_or_narrator(self):
        board = sample_board()
        board["acting_context"]["actor_cards"]["narrator"] = {"personality": "旁白性格"}
        board["shots"][0]["lines"] = [{"speaker": "narrator", "line": "夜幕降临"}]
        board["shots"][0]["performance"]["packet"]["actors"].append(
            {"actor_id": "b", "beats": [{"visible_action": "画外候选", "voice": "画外声音"}]})
        result = compile_shot(board, "S1", mode="stateful")
        self.assertNotIn("画外候选", result["text"])
        self.assertNotIn("画外声音", result["voice_notes"])
        self.assertNotIn("旁白性格", result["text"])

    def test_asset_stage_uses_asset_composer_instead_of_story_frame_rules(self):
        from prompt_compiler import compile_stage_prompt
        with tempfile.TemporaryDirectory() as project, mock.patch("skill_lib.ensure_explicit_defaults", return_value={}):
            result = compile_stage_prompt("asset_image", {"id": "a", "kind": "character",
                                          "sheet_prompt": "蓝色短外套"}, project)
        self.assertIn("蓝色短外套", result["content_prompt"])
        self.assertIn("五视图", result["content_prompt"])
        self.assertNotIn("输出单张关键帧", result["content_prompt"])
        self.assertNotIn("重复角色", result["negative_prompt"])

    def test_regenerated_frame_keeps_selected_style_once(self):
        from prompt_assembler import assemble_shot_prompt
        for source_field in ("prompt_source", "prompt_image_source"):
            with self.subTest(source_field=source_field), tempfile.TemporaryDirectory() as project, \
                    mock.patch("prompt_assembler.skill_positive", return_value="测试画风"), \
                    mock.patch("skill_lib.image_visual_skill_id", return_value=""), \
                    mock.patch("prompt_assembler._knowledge", return_value=[]):
                result = assemble_shot_prompt({"id": "S1", "prompt_image": "@character:a 望着门",
                                              "action": "旧动作", source_field: "regenerated"},
                                             {"dir": project, "media_type": "image"})
            self.assertEqual(result["prompt_assembled"].count("测试画风"), 1)
            self.assertNotIn("旧动作", result["prompt_assembled"])

    def test_storyboard_receives_character_background_without_asset_sheet(self):
        character = {"id": "a", "name": "甲", "biography": "从小守着旧店",
                     "bio_language": "常用短句", "relations": [{"ref": "@character:b", "type": "同事"}],
                     "acting": {"personality": "谨慎"}, "sheet_prompt": "绝不可复制的设定图"}
        original = copy.deepcopy(character)
        with mock.patch.object(pm, "_knowledge", return_value=""), mock.patch.object(pm, "_override", return_value=None):
            system, user = pm.storyboard_prompt("甲打开门", [character], [])
        self.assertIn("从小守着旧店", user)
        self.assertIn("常用短句", user)
        self.assertIn("谨慎", user)
        self.assertNotIn("绝不可复制的设定图", user)
        self.assertIn("人物创作背景", system)
        self.assertEqual(character, original)

    def test_storyboard_has_one_json_contract_without_example_specific_constraints(self):
        with mock.patch.object(pm, "_knowledge", return_value=""), mock.patch.object(pm, "_override", return_value=None):
            system, _ = pm.storyboard_prompt("一个空镜", [], [])
        self.assertNotIn("JSON 上方", system)
        self.assertNotIn("八条步足", system)
        self.assertNotIn("先写 16:9", system)
        self.assertEqual(system.count("【制作提示词契约"), 1)
        self.assertIn("只输出一个 JSON 对象", system)

    def test_custom_policy_has_explicit_scope_after_its_text(self):
        with mock.patch.object(pm, "_override", return_value="示例规则：改成自由散文"):
            system = pm.compile_system_prompt("storyboard", "只输出 JSON")
        self.assertIn("【补充适用边界】", system)
        self.assertGreater(system.index("【补充适用边界】"), system.index("改成自由散文"))
        self.assertIn("输出契约", system)

    def test_asset_style_priority_cannot_replace_subject_or_layout(self):
        import skill_lib
        with mock.patch.object(skill_lib, "resolve_asset_style_text", return_value=("水彩", "project")), \
                mock.patch.object(skill_lib, "image_identity_method_text", return_value=""), \
                mock.patch.object(skill_lib, "compose_asset_negative", return_value=""):
            prompt, _ = skill_lib.compose_asset_image_prompt("", "黑色外套", kind="character")
        self.assertIn("不能改写主体身份、结构或构图", prompt)

    def test_prop_ownership_examples_do_not_import_project_identity(self):
        with mock.patch.object(pm, "_override", return_value=None):
            system, _ = pm.props_prompt("人物修复一件物品。")
        for token in ("白咲蛛绪", "baixiaozhuxu", "yaokong_zhadan", "蜘蛛步足", "蛛丝"):
            self.assertNotIn(token, system)
        self.assertIn("@character:owner_id", system)
        self.assertIn("@prop:source_id", system)


if __name__ == "__main__":
    unittest.main()
