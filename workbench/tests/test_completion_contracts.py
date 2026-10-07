# -*- coding: utf-8 -*-
"""跨流程补全契约：缺项、锁、保存与失败状态一致。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'previs_system/tools'))
sys.path.insert(0, str(ROOT / 'workbench/tools'))
import analyze_film
import character_design
import character_profiles
import story_settings
import story_units
from completion_values import has_content


class CompletionContracts(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for target in ('socket.create_connection', 'socket.socket.connect'):
            guard = patch(target, side_effect=AssertionError('禁止网络'))
            guard.start()
            self.addCleanup(guard.stop)

    def write(self, relative, data):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        return path

    def actor(self, **extra):
        row = {'id': 'a', 'name': '甲', **{key: '已有设定' for key in story_units.CHAR_TEXT_KEYS}, **extra}
        self.write('素材/人物.json', {'characters': [row]})
        self.write('剧本/分集.json', {'episodes': [{'id': 'E1', 'cast_refs': ['@character:a']}]})
        return row

    def test_empty_values_and_false_zero_have_consistent_meaning(self):
        self.assertFalse(has_content(' \n'))
        self.assertFalse(has_content([' ']))
        self.assertTrue(has_content(0))
        self.assertTrue(has_content(False))
        self.actor(biography=' \n')
        story_units.apply_entities(self.root, {'characters': [{'ref': '@character:a', 'biography': '完整小传'}]}, fill_only=True)
        self.assertEqual(story_units.load_units(self.root)['characters'][0]['biography'], '完整小传')

    def test_unknown_appearance_is_fillable_but_adopted_values_survive(self):
        original = {'face': '未知', 'hair': '人工短发', 'sources': {'face': 'unknown', 'hair': 'authored'}}
        result = character_design.merge_generated_appearance(original, {'face': '宽额', 'hair': '长发',
                    'sources': {'face': 'source'}}, fill_only=True)
        self.assertEqual(result['face'], '宽额')
        self.assertEqual(result['hair'], '人工短发')

    def test_editing_one_appearance_field_only_locks_that_field(self):
        self.actor()
        revision = character_profiles.state(self.root)['revision']
        row = character_profiles.save(self.root, 'a', {'appearance': {'face': '人工脸型'}}, expected_revision=revision)['character']
        self.assertIn('appearance.face', row['locked_fields'])
        self.assertNotIn('appearance', row['locked_fields'])
        story_units.apply_entities(self.root, {'characters': [{'ref': '@character:a', 'appearance': {'face': '改脸', 'hair': '短发'}}]}, fill_only=True)
        appearance = story_units.load_units(self.root)['characters'][0]['appearance']
        self.assertEqual(appearance['face'], '人工脸型')
        self.assertEqual(appearance['hair'], '短发')

    def test_locked_missing_appearance_is_reported_before_model_call(self):
        self.actor(locked_fields=['appearance'])
        with self.assertRaisesRegex(ValueError, '缺项被人工锁定'):
            story_settings.complete(self.root, lambda *_: self.fail('锁定缺项不应消耗模型调用'))

    def test_scene_and_prop_required_fields_block_managed_planning(self):
        self.write('剧本/大纲.json', {'units_version': 1})
        self.write('剧本/分集.json', {'episodes': [{'id': 'E1', 'scene_refs': ['@scene:s'], 'key_asset_refs': ['@prop:p']}]})
        self.write('素材/场景.json', {'scenes': [{'id': 's', 'name': '室内', 'spatial_limit': ' ', 'action_slots': [' ']}]})
        self.write('素材/道具.json', {'props': [{'id': 'p', 'name': '钥匙', 'usage_boundary': ''}]})
        errors = [e for e in story_units.check(self.root)['errors'] if e['code'] == 'ASSET_SETTINGS_INCOMPLETE']
        self.assertEqual(len(errors), 2)
        story_units.apply_entities(self.root, {'scenes': [{'ref': '@scene:s', 'spatial_limit': '单门通行', 'action_slots': ['门边']}],
                                               'props': [{'ref': '@prop:p', 'usage_boundary': '仅开本门'}]}, fill_only=True)
        self.assertFalse([e for e in story_units.check(self.root)['errors'] if e['code'] == 'ASSET_SETTINGS_INCOMPLETE'])

    def test_analysis_fill_keeps_existing_fields_and_explicit_silence(self):
        shot = {'id': 'S1', 't_in': 0, 't_out': 2, 'shot_size': '全景', 'story': '人工内容', 'dialogue': [], 'transition': '不确定'}
        response = {field: '新识别' for field in analyze_film.AI_TEXT_FIELDS}
        response.update(transition='硬切', dialogue=[{'text': '不应添加'}])
        analyze_film.apply_ai_result(shot, response, only_empty=True)
        self.assertEqual(shot['shot_size'], '全景')
        self.assertEqual(shot['story'], '人工内容')
        self.assertEqual(shot['transition'], '不确定')
        self.assertEqual(shot['dialogue'], [])
        self.assertEqual(analyze_film.missing_ai_fields(shot), [])

    def test_analysis_partial_response_is_saved_but_job_fails(self):
        directory = self.root / '拉片/test'
        path = self.write('拉片/test/analysis.json', {'shots': [{'id': 'S1', 't_in': 0, 't_out': 2,
                          'shot_size': '全景', 'story': '人工故事', 'keyframes': ['frame.png']}]})
        (directory / 'frame.png').write_bytes(b'image')
        args = SimpleNamespace(fill=str(directory), vendor=None, shots=None, only_empty=True,
                               max_ai=0, transition_frames=False, workers=1)
        with patch.object(analyze_film, 'pick_engine', return_value=(object(), None, 'fake', 'offline')), \
             patch.object(analyze_film, 'ai_with_retry', return_value={'action': '走路', 'story': '改写'}), \
             patch.object(analyze_film, 'self_validate', return_value=([], [])), \
             patch.object(analyze_film, 'write_markdown'):
            with self.assertRaises(SystemExit) as exc:
                analyze_film.fill_mode(args)
        self.assertEqual(exc.exception.code, 1)
        saved = json.loads(path.read_text(encoding='utf-8'))['shots'][0]
        self.assertEqual(saved['story'], '人工故事')
        self.assertEqual(saved['action'], '走路')
        self.assertTrue(list((directory / '.versions').glob('*.json')))
