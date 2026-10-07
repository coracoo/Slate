# -*- coding: utf-8 -*-
"""人物形象补全与统一构图回归，禁止网络。"""
import sys
import unittest
import tempfile
import json
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import skill_lib
import character_design
import story_units


class CharacterLayoutFlow(unittest.TestCase):
    def setUp(self):
        for target in ('socket.create_connection', 'socket.socket.connect'):
            guard = patch(target, side_effect=AssertionError('禁止网络'))
            guard.start()
            self.addCleanup(guard.stop)

    def test_biography_without_visual_design_remains_pending(self):
        row = {key: '完整小传' for key in story_units.CHAR_TEXT_KEYS}
        self.assertEqual(story_units.missing_fields('character', row),
                         ['appearance.face', 'appearance.hair', 'appearance.body_type', 'appearance.outfit'])
        row['appearance'] = {'proposals': {'face': '宽额', 'hair': '短发', 'body_type': '瘦高', 'outfit': '灰布衣'}}
        self.assertEqual(story_units.missing_fields('character', row), [])
        self.assertEqual(character_design.appearance_prompt(row), '')

    def test_creature_does_not_require_human_hair_or_clothing(self):
        row = {'appearance': {'species': '九尾狐', 'body_type': '四足', 'distinctive_features': '九尾', 'look': '银白皮毛'}}
        self.assertEqual(character_design.missing_appearance_fields(row), [])
        row['appearance']['look'] = '未知'
        self.assertEqual(character_design.missing_appearance_fields(row), ['appearance.look'])

    def test_sheet_is_idempotent_and_full_length(self):
        facts = '左眉伤痕；' * 100
        old = '正面、侧面、背面三视图，纯白背景；' + facts
        normalized = skill_lib.normalize_character_sheet(old)
        self.assertNotIn('三视图', normalized)
        self.assertIn('双腿与双脚', normalized)
        self.assertTrue(normalized.endswith(facts.rstrip('；')))
        self.assertEqual(normalized, skill_lib.normalize_character_sheet(normalized))

    def test_final_prompt_has_only_authoritative_layout(self):
        source = skill_lib.normalize_character_sheet('正面、侧面、背面三视图，纯白背景；左眉伤痕')
        with patch.object(skill_lib, 'resolve_asset_style_text', return_value=('', 'none')), patch.object(skill_lib, 'image_identity_method_text', return_value=''), patch.object(skill_lib, 'compose_asset_negative', return_value=''):
            prompt, _ = skill_lib.compose_asset_image_prompt(None, source)
        self.assertNotIn('三视图', prompt)
        self.assertEqual(prompt.count(skill_lib.SHEET_VIEW_PANELS_ZH), 1)
        self.assertIn('左眉伤痕', prompt)

    def test_old_torso_template_does_not_leak_into_new_layout(self):
        old = ('五视图设定图（一张图内从左到右五段）：①脸部正面与脖子特写；'
               '②脸部45度左侧脸与脖子特写；③无头躯干正面像；④无头躯干侧面像；'
               '⑤严格背面全身像（含头部背面）。纯白背景。')
        normalized = skill_lib.normalize_character_sheet(old + '旧服饰保持')
        self.assertNotIn('躯干正面像', normalized)
        self.assertEqual(normalized.count(skill_lib.SHEET_VIEW_TITLE_ZH), 1)
        self.assertTrue(normalized.endswith('旧服饰保持'))

    def test_legacy_headless_layout_is_removed_from_generation_prompt(self):
        old = ('五视图设定图：脸部正面特写、45度左侧脸特写、'
               '不带头部正面全身、不带头部侧面全身、严格背面全身；蓝色制服')
        with patch.object(skill_lib, 'resolve_asset_style_text', return_value=('', 'none')), patch.object(skill_lib, 'image_identity_method_text', return_value=''), patch.object(skill_lib, 'compose_asset_negative', return_value=''):
            prompt, _ = skill_lib.compose_asset_image_prompt(None, old)
        self.assertIn('蓝色制服', prompt)
        self.assertIn('画面从颈部开始到脚底', prompt)
        for wording in ('无头', '不带头部', 'headless', 'nothing above'):
            self.assertNotIn(wording, prompt)

    def test_migration_and_queue_preserve_long_description_and_states(self):
        import character_profiles
        import chatgpt_queue
        import creation_pipeline
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '素材' / '人物.json'
            path.parent.mkdir()
            facts = '铁甲的接缝与左侧伤痕。' * 100
            original = {'characters': [{'id': 'a', 'sheet_prompt': '正面、侧面、背面三视图，纯白背景；' + facts,
                          'appearance': {'face': '长脸'}, 'states': [{'id': 'b', 'sheet_prompt': '三视图。雨衣破损'}]}]}
            path.write_text(json.dumps(original, ensure_ascii=False), encoding='utf-8')
            prompt, _ = chatgpt_queue._asset_source(directory, {'kind': 'character', 'id': 'a'})
            self.assertNotIn('三视图', prompt)
            self.assertIn('长脸', prompt)
            self.assertNotIn(facts, prompt)
            self.assertEqual(character_profiles.normalize_sheets(directory), 2)
            self.assertEqual(character_profiles.normalize_sheets(directory), 0)
            saved = json.loads(path.read_text(encoding='utf-8'))['characters'][0]
            self.assertEqual(saved['appearance'], original['characters'][0]['appearance'])
            self.assertTrue(saved['sheet_prompt'].endswith(facts))
            self.assertTrue(list((path.parent / '.versions').rglob('*.json')))
            with patch.object(creation_pipeline, 'pick_vendor', side_effect=AssertionError('无需厂商')):
                result = creation_pipeline.regenerate_asset_prompt(directory, 'character', 'a')
            self.assertTrue(result['prompt'].endswith(facts))
