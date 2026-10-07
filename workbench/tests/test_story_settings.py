# -*- coding: utf-8 -*-
"""设定缺项小批补全、拆批与断点保护的离线回归。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import character_design
import story_settings
import story_units


class StorySettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name)
        for target in ('socket.create_connection', 'socket.socket.connect'):
            guard = patch(target, side_effect=AssertionError('禁止网络'))
            guard.start()
            self.addCleanup(guard.stop)
        for directory in ('剧本', '素材'):
            (self.project / directory).mkdir()
        self.rows = [{'id': str(i), 'name': f'角色{i}', **{key: '已采用小传与演绎' for key in story_units.CHAR_TEXT_KEYS},
                      'appearance': {'species': '人类男性', 'face': '已采用的脸'}, 'sheet_prompt': '旧图描绘'} for i in range(6)]
        self.write('素材/人物.json', {'characters': self.rows})
        self.write('剧本/分集.json', {'episodes': [{'id': 'E1', 'summary': '既有故事', 'text': '不应重复注入的正文' * 100,
                                                'cast_refs': [f'@character:{i}' for i in range(5)]}]})
        self.write('剧本/大纲.json', {'premise': '既有主线', 'arcs': []})

    def write(self, path, data):
        (self.project / path).write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')

    def response(self, targets):
        return {'characters': [{'ref': t['ref'], 'biography': '禁止覆盖的小传',
                                'appearance': {'face': '禁止覆盖的脸', 'species': '异兽',
                                               'proposals': {'hair': '短发', 'body_type': '瘦高', 'outfit': '粗布衣'}}} for t in targets]}

    def test_human_species_description_is_not_creature(self):
        for species in ('人类男性', '人类，性别不明的成年人', '人类男性形态的山神', 'human male'):
            self.assertEqual(character_design.missing_appearance_fields({'appearance': {'species': species}}),
                             ['appearance.face', 'appearance.hair', 'appearance.body_type', 'appearance.outfit'])
        self.assertEqual(character_design.missing_appearance_fields({'appearance': {'species': '非人类九尾狐'}}),
                         ['appearance.body_type', 'appearance.distinctive_features', 'appearance.look'])

    def test_only_active_missing_fields_are_written_and_resume_skips_success(self):
        calls = []
        def generate(context, targets):
            calls.append([t['ref'] for t in targets])
            self.assertLessEqual(len(targets), 4)
            self.assertNotIn('text', context['episodes'][0])
            self.assertEqual(set(context['roster'][0]), {'ref', 'name'})
            self.assertEqual(len(context['current_entities']), len(targets))
            return self.response(targets)
        story_settings.complete(self.project, generate)
        rows = story_units.load_units(self.project)['characters']
        self.assertEqual(rows[0]['biography'], self.rows[0]['biography'])
        self.assertEqual(rows[0]['appearance']['face'], '已采用的脸')
        self.assertEqual(rows[0]['appearance']['species'], '人类男性')
        self.assertEqual(rows[5], self.rows[5], '未被剧情引用的历史实体不生成、不删除')
        self.assertEqual(len(calls), 2)
        story_settings.complete(self.project, lambda *_: self.fail('已补齐不能重复请求'))

    def test_invalid_batch_splits_and_single_failure_keeps_other_success(self):
        calls = []
        def generate(context, targets):
            refs = [t['ref'] for t in targets]
            calls.append(refs)
            if len(targets) > 1 or refs == ['@character:2']:
                raise story_settings.SettingsOutputError('JSON 括号错误')
            return self.response(targets)
        with self.assertRaisesRegex(ValueError, '单实体补全失败'):
            story_settings.complete(self.project, generate)
        pending = story_units.pending_settings(self.project, per_round=20, references_only=True)
        self.assertEqual([t['ref'] for t in pending['targets']], ['@character:2'])
        self.assertEqual(len(calls), len({tuple(c) for c in calls}), '同一批失败不能原样循环')
        self.assertLessEqual(len(calls), 9)

    def test_explicit_unused_scope_and_empty_scope(self):
        with self.assertRaises(ValueError):
            story_settings.complete(self.project, lambda *_: self.fail('空选择不能请求模型'), [])
        calls = []
        def generate(ctx, targets):
            calls.extend(t['ref'] for t in targets)
            return self.response(targets)
        story_settings.complete(self.project, generate, ['@character:5'])
        self.assertEqual(calls, ['@character:5'])
        self.assertEqual(story_units.load_units(self.project)['characters'][0], self.rows[0])

    def test_context_keeps_adopted_body_tail_and_episode_facts(self):
        body = '前文' * 2000 + '结尾伤情：左袖撕裂'
        self.write('剧本/分集.json', {'episodes': [{'id': 'E1', 'text': body, 'cliff': '追兵逼近', 'cast_refs': ['@character:0']}]})
        ctx = story_settings.context(story_units.load_units(self.project), [{'ref': '@character:0'}])
        self.assertEqual(ctx['episodes'][0]['adopted_body'], body)
        self.assertEqual(ctx['episodes'][0]['cliff'], '追兵逼近')

    def test_transport_failure_does_not_split_or_resubmit(self):
        calls = []
        def generate(*_):
            calls.append(1)
            raise ValueError('调用失败：HTTP 401')
        with self.assertRaisesRegex(ValueError, '401'):
            story_settings.complete(self.project, generate)
        self.assertEqual(len(calls), 1)

    def test_valid_json_with_missing_or_extra_entities_is_rejected(self):
        units = story_units.load_units(self.project)
        targets = story_units.pending_settings(self.project, per_round=2, references_only=True)['targets']
        for bad in ({'characters': []}, {'characters': [{'ref': '@character:5'}]}):
            with self.assertRaises(story_settings.SettingsOutputError):
                story_settings.validated_patch(bad, targets, units)
        bad = self.response(targets)
        bad['characters'][0]['appearance']['proposals'].pop('hair')
        with self.assertRaisesRegex(story_settings.SettingsOutputError, 'appearance.hair'):
            story_settings.validated_patch(bad, targets, units)

    def test_prop_visual_completion_preserves_story_and_resumes_without_regeneration(self):
        self.write('剧本/分集.json', {'units_version': 1, 'episodes': [
            {'id': 'E1', 'summary': '以铜环开锁', 'text': '正文保持不变', 'key_asset_refs': ['@prop:ring']}]})
        self.write('素材/道具.json', {'props': [
            {'id': 'ring', 'name': '铜环', 'usage_boundary': '只能开启旧锁', 'image_prompt': '历史自动提示词'},
            {'id': 'unused', 'name': '旧道具', 'usage_boundary': '保留'}]})
        before = (self.project / '剧本/分集.json').read_bytes()
        calls = []
        def generate(ctx, targets):
            calls.append(targets)
            self.assertEqual([t['ref'] for t in targets], ['@prop:ring'])
            self.assertEqual(targets[0]['missing'], ['visual_description'])
            return {'props': [{'ref': '@prop:ring', 'visual_description': '暗褐铜环，外缘有三道刻痕',
                               'usage_boundary': '不允许覆盖'}]}
        story_settings.complete(self.project, generate)
        rows = story_units.load_units(self.project)['props']
        self.assertEqual(rows[0]['visual_description'], '暗褐铜环，外缘有三道刻痕')
        self.assertEqual(rows[0]['usage_boundary'], '只能开启旧锁')
        self.assertNotIn('visual_description', rows[1])
        self.assertEqual((self.project / '剧本/分集.json').read_bytes(), before)
        story_settings.complete(self.project, lambda *_: self.fail('已完成的道具不重复请求'))
        self.assertEqual(len(calls), 1)

    def test_new_full_biographies_are_small_batches(self):
        targets = [{'ref': f'@character:{i}', 'missing': ['biography', 'appearance.face', 'appearance.hair',
                    'appearance.body_type', 'appearance.outfit']} for i in range(7)]
        self.assertEqual([len(group) for group in story_settings.batches(targets)], [2, 2, 2, 1])
