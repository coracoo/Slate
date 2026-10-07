# -*- coding: utf-8 -*-
"""人物外观在临时档案、母图与状态图间的传播及来源保护。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import character_profiles


class CharacterDesignTests(unittest.TestCase):
    def test_creation_and_setting_prompts_share_gender_design_rules(self):
        import prompt_modules as prompts
        import character_design as design
        guidance = design.appearance_guidance()
        extracted, _ = prompts.characters_prompt('成年女军师与成年男医者交谈。')
        settings, _ = prompts.units_entity_prompt({'roster': [], 'episodes': []})
        for prompt in (guidance, extracted, settings):
            self.assertIn('【性别可辨识的形象设计】', prompt)
            self.assertIn('穿衣后的自然胸部轮廓', prompt)
            self.assertIn('中性外观、女扮男装、男扮女装', prompt)
            self.assertIn('不凭名字猜性别', prompt)
            self.assertIn('未成年', prompt)
            self.assertIn('appearance.proposals', prompt)
        schema = json.loads(prompts.UNITS_ENTITY_SCHEMA)
        self.assertIn('body_proportions', schema['characters'][0]['appearance'])
        self.assertIn('"body_proportions":null', extracted)

    def test_structured_save_keeps_exact_values_and_legacy_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            path = project / "素材" / "人物.json"
            path.parent.mkdir()
            path.write_text(json.dumps({"characters": [{"id": "a", "name": "甲",
                "appearance": {"look": "窄长脸", "outfit": "灰衣", "custom": "旧扩展"}}]}), encoding="utf-8")
            revision = character_profiles.state(project)["revision"]
            result = character_profiles.save(project, "a", {"appearance": {
                "age": "三十岁左右", "height": "172.5 cm", "waist": "68.2厘米", "hips": "94厘米",
                "face": "左眉略高", "hair": "发际线略后退，碎发不齐",
                "sources": {"height": "source", "waist": "source"},
                "proposals": {"headwear": "木簪"}}}, expected_revision=revision)
            appearance = result["character"]["appearance"]
            self.assertEqual(appearance["height"], "172.5 cm")
            self.assertEqual(appearance["waist"], "68.2厘米")
            self.assertEqual(appearance["outfit"], "灰衣")
            self.assertEqual(appearance["custom"], "旧扩展")
            self.assertIn("appearance.face", result["character"]["locked_fields"])
            self.assertNotIn("appearance", result["character"]["locked_fields"])
            self.assertEqual(appearance["sources"]["face"], "authored")
            with self.assertRaises(character_profiles.project_store.RevisionConflict):
                character_profiles.save(project, "a", {"appearance": {"face": "不得覆盖"}}, expected_revision=revision)

    def test_prompt_does_not_turn_suggestions_into_facts(self):
        import character_design as design
        record = {"appearance": {"age": "不详", "height": "172.5 cm", "face": "左眉略高",
                  "hair": "直发夹少量自然卷", "headwear": "金冠", "sources": {"headwear": "proposal"},
                  "proposals": {"waist": "60厘米"}}}
        prompt = design.appearance_prompt(record)
        self.assertIn("172.5 cm", prompt)
        self.assertIn("左眉略高", prompt)
        self.assertNotIn("金冠", prompt)
        self.assertNotIn("60厘米", prompt)
        self.assertNotIn("照片", prompt)

    def test_minor_and_nonhuman_do_not_receive_human_measurements(self):
        import character_design as design
        for appearance in ({"age": 16, "waist": "60厘米", "hips": "88厘米"},
                           {"species": "异兽", "age": 300, "waist": "60厘米", "hips": "88厘米"}):
            prompt = design.appearance_prompt({"appearance": appearance})
            self.assertNotIn("60厘米", prompt)
            self.assertNotIn("88厘米", prompt)
        prompt = design.character_image_prompt({"appearance": {"species": "异兽", "look": "羽冠四翼"}})
        self.assertIn("羽冠四翼", prompt)
        self.assertNotIn("发际线", prompt)

    def test_state_prompt_keeps_identity_and_uses_state_costume(self):
        import character_design as design
        record = {"identity_anchor": "眼距偏宽", "appearance": {"face": "左眉略高", "outfit": "灰衣", "height": "172厘米"}}
        prompt = design.character_image_prompt(record, "换成红色战袍", state=True)
        # 身份来自真实母图及结构化外观，不重放可能夹带跨集持物的自由文本锚点。
        self.assertNotIn("眼距偏宽", prompt)
        self.assertIn("左眉略高", prompt)
        self.assertIn("172厘米", prompt)
        self.assertIn("红色战袍", prompt)
        self.assertNotIn("灰衣", prompt)

    def test_asset_summary_carries_structured_appearance(self):
        from prompt_assembler import _asset_summary
        result = _asset_summary("character", {"id": "a", "appearance": {"face": "左眉略高", "height": "172.5 cm"}})
        self.assertIn("左眉略高", result["appearance_prompt"])
        self.assertEqual(result["appearance"]["height"], "172.5 cm")

    def test_offline_generation_uses_saved_appearance_in_mother_and_state_images(self):
        from unittest import mock
        import gen_asset_images as gen
        from test_asset_generation_refs import _run_main
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            assets = project / "素材"
            assets.mkdir()
            (assets / "人物.json").write_text(json.dumps({"characters": [{
                "id": "a", "name": "甲", "identity_anchor": "眼距偏宽",
                "appearance": {"face": "左眉略高", "height": "172.50 cm", "outfit": "灰衣", "hair":"已采用短发",
                               "body_type": "成年女性，穿衣后自然胸部轮廓清楚",
                               "body_proportions": "肩线自然下斜，腰线收束，胯部平顺展开",
                               "proposals": {"hair": "待采用的辫发"}},
                "states": [{"id": "battle", "sheet_prompt": "红色战袍"}],
            }]}), encoding="utf-8")
            self.assertTrue(gen.collect_asset_image_plan(project, "character")[0]["can_generate"])
            with mock.patch.object(gen, "set_billing_project"), \
                 mock.patch("socket.create_connection", side_effect=AssertionError("测试禁止网络")), \
                 mock.patch("socket.socket.connect", side_effect=AssertionError("测试禁止网络")):
                code, client = _run_main(gen, project, "--kind", "character", "--workers", "1")
            self.assertEqual(code, 0)
            self.assertEqual(len(client.calls), 2)
            for call in client.calls:
                self.assertIn("左眉略高", call["prompt"])
                self.assertIn("172.50 cm", call["prompt"])
                self.assertIn("穿衣后自然胸部轮廓清楚", call["prompt"])
                self.assertIn("肩线自然下斜，腰线收束，胯部平顺展开", call["prompt"])
                self.assertNotIn("待采用的辫发", call["prompt"])
            self.assertIn("灰衣", client.calls[0]["prompt"])
            self.assertNotIn("灰衣", client.calls[1]["prompt"])
            self.assertIn("红色战袍", client.calls[1]["prompt"])
            self.assertEqual(client.calls[1]["image_refs"], [client.calls[0]["out"]])


if __name__ == "__main__":
    unittest.main()
