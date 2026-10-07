# -*- coding: utf-8 -*-
"""全剧规划、设定锁定和正文扩写的业务顺序。"""
import json
import sys
import tempfile
import unittest
import io
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import story_units as units
import preproduction_flow as flow
import creation_pipeline as pipeline
import brief


class ScriptWorkflowTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.write('剧本/分集.json', {'mode': 'generated', 'units_version': 1, 'episodes': [
            {'id': 'E1', 'title': '发现', 'summary': '甲发现证据', 'hook': '发现', 'cliff': '质问',
             'arc_id': 'A1', 'beats': ['发现证据'], 'cast_refs': ['@character:hero'],
             'scene_refs': ['@scene:room'], 'text': '甲：是谁留下的？'}]})
        self.write('剧本/大纲.json', {'units_version': 1, 'premise': '发现真相',
            'arcs': [{'id': 'A1', 'ep_from': 'E1', 'ep_to': 'E1', 'goal': '发现证据'}]})
        self.write('剧本/埋线.json', {'foreshadows': [], 'hooks': []})
        self.write('素材/人物.json', {'characters': [{'id': 'hero', 'name': '甲', 'role': '主角',
            **{k: '已有设定' for k in units.CHAR_TEXT_KEYS}, 'relations': [],
            'appearance': {'face':'窄脸', 'hair':'短发', 'body_type':'清瘦', 'outfit':'蓝衣'}}]})
        self.write('素材/场景.json', {'scenes': [{'id': 'room', 'name': '教室',
            'spatial_limit': '只有一扇门', 'action_slots': ['门口']}]})
        self.write('素材/道具.json', {'props': []})
        (self.root / '剧本/构想.txt').write_text('甲在教室发现证据的故事', encoding='utf-8')
        brief.save_brief(self.root, {'total_episodes': 1, 'episode_minutes': 2})

    def write(self, relative, data):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')

    def read(self, relative):
        return json.loads((self.root / relative).read_text(encoding='utf-8'))

    def test_managed_unlocked_project_blocks_expansion_before_model_and_writes(self):
        before = (self.root / '剧本/分集.json').read_bytes()
        with patch.object(pipeline, 'VendorClient') as client:
            with self.assertRaisesRegex(ValueError, '确认'):
                pipeline.cmd_expand(str(self.root), None, None, 1, 'E1')
            client.assert_not_called()
        self.assertEqual((self.root / '剧本/分集.json').read_bytes(), before)
        self.assertFalse(flow.workflow_state(self.root)['can_expand'])

    def test_locked_project_enables_expansion_and_text_edits_do_not_break_lock(self):
        self.assertTrue(units.anchor(self.root)['ok'])
        self.assertTrue(flow.workflow_state(self.root)['can_expand'])
        doc = self.read('剧本/分集.json')
        doc['episodes'][0]['text'] = '甲：人工修改后的完整正文'
        doc['rev'] = 10
        self.write('剧本/分集.json', doc)
        self.assertTrue(units.is_anchored(self.root))

    def test_changed_rules_or_episode_card_invalidates_lock(self):
        units.anchor(self.root)
        brief.save_brief(self.root, {'episode_minutes': 3})
        self.assertFalse(units.is_anchored(self.root))
        self.assertFalse(flow.workflow_state(self.root)['can_expand'])
        self.assertEqual(units.units_block(self.root, 'E1'), '')
        units.anchor(self.root)
        units.edit_episode(self.root, 'E1', {'summary': '改成另一个事件'})
        self.assertFalse(units.is_anchored(self.root))

    def test_identical_rules_and_image_style_do_not_invalidate_script_lock(self):
        units.anchor(self.root)
        brief.save_brief(self.root, {'episode_minutes': 2})
        self.write('剧本/style.json', {'image': 'cinematic-real'})
        self.assertTrue(units.is_anchored(self.root))

    def test_imported_script_and_legacy_project_remain_usable(self):
        doc = self.read('剧本/分集.json')
        doc['mode'] = 'imported'
        self.write('剧本/分集.json', doc)
        (self.root / '剧本/剧本.txt').write_text('甲：导入的原稿', encoding='utf-8')
        self.assertTrue(flow.workflow_state(self.root)['can_expand'])
        doc.pop('units_version')
        doc['mode'] = 'generated'
        self.write('剧本/分集.json', doc)
        self.write('剧本/大纲.json', {})
        self.assertTrue(flow.workflow_state(self.root)['can_expand'])

    def test_completion_preserves_existing_cards_and_only_fills_missing_fields(self):
        result = {'premise': '另一条主线', 'episodes': [
            {'id': 'E1', 'title': '改名', 'summary': '错误换主角', 'relation_shift': ['甲信任乙'],
             'text': '不能覆盖正文'}]}
        units.apply_story(self.root, result, fill_only=True)
        row = self.read('剧本/分集.json')['episodes'][0]
        self.assertEqual(row['title'], '发现')
        self.assertEqual(row['summary'], '甲发现证据')
        self.assertEqual(row['text'], '甲：是谁留下的？')
        self.assertEqual(row['relation_shift'], ['甲信任乙'])
        self.assertEqual(self.read('剧本/大纲.json')['premise'], '发现真相')

    def test_biography_is_generated_persisted_and_required_before_expansion(self):
        import prompt_modules
        schema = json.loads(prompt_modules.UNITS_ENTITY_SCHEMA)
        self.assertIn('biography', schema['characters'][0])
        doc = self.read('素材/人物.json')
        doc['characters'][0].pop('biography', None)
        self.write('素材/人物.json', doc)
        pending = units.pending_settings(self.root, references_only=True)
        self.assertEqual(pending['targets'][0]['missing'], ['biography'])
        self.assertIn('CHARACTER_SETTINGS_INCOMPLETE', [e['code'] for e in units.check(self.root)['errors']])
        self.assertFalse(units.anchor(self.root)['ok'])
        biography = '甲曾因隐瞒线索失去朋友，如今寻找真相，却仍害怕承担后果，最终选择公开证据。'
        units.apply_entities(self.root, {'characters': [{'ref': '@character:hero', 'biography': biography,
            'bio_language': '不应覆盖'}]}, fill_only=True)
        row = self.read('素材/人物.json')['characters'][0]
        self.assertEqual(row['biography'], biography)
        self.assertEqual(row['bio_language'], '已有设定')
        self.assertEqual(units.pending_settings(self.root, references_only=True)['remaining'], 0)
        self.assertTrue(units.anchor(self.root)['ok'])
        self.assertTrue(flow.require_expansion_ready(self.root)['can_expand'])
        self.assertEqual(self.read('剧本/分集.json')['episodes'][0]['text'], '甲：是谁留下的？')

    def test_unused_character_does_not_block_planning_but_missing_biography_on_used_character_does(self):
        doc = self.read('素材/人物.json')
        doc['characters'].append({'id': 'unused', 'name': '历史人物'})
        self.write('素材/人物.json', doc)
        self.assertTrue(units.check(self.root)['ok'])
        doc['characters'][0]['biography'] = ''
        self.write('素材/人物.json', doc)
        self.assertFalse(units.check(self.root)['ok'])

    def test_biography_completion_respects_manual_lock(self):
        doc = self.read('素材/人物.json')
        doc['characters'][0].update(biography='人工小传', locked_fields=['biography'])
        self.write('素材/人物.json', doc)
        units.apply_entities(self.root, {'characters': [{'ref': '@character:hero', 'biography': '模型改写'}]})
        self.assertEqual(self.read('素材/人物.json')['characters'][0]['biography'], '人工小传')

    def test_setting_completion_preserves_filled_and_manually_locked_fields(self):
        doc = self.read('素材/人物.json')
        doc['characters'][0]['bio_crack'] = ''
        doc['characters'][0]['locked_fields'] = ['bio_language']
        self.write('素材/人物.json', doc)
        units.apply_entities(self.root, {'characters': [{'ref': '@character:hero',
            'bio_language': '覆盖人工稿', 'bio_crack': '慌张时结巴'}]}, fill_only=True)
        row = self.read('素材/人物.json')['characters'][0]
        self.assertEqual(row['bio_language'], '已有设定')
        self.assertEqual(row['bio_crack'], '慌张时结巴')
        units.apply_entities(self.root, {'characters': [{'ref': '@character:hero',
            'bio_language': '仍不允许覆盖'}]})
        self.assertEqual(self.read('素材/人物.json')['characters'][0]['bio_language'], '已有设定')

    def test_completion_adds_missing_arc_without_replacing_existing_arc_or_episodes(self):
        units.apply_story(self.root, {'arcs': [
            {'id': 'A1', 'ep_from': 'E1', 'ep_to': 'E9', 'goal': '错误扩大'},
            {'id': 'A2', 'ep_from': 'E2', 'ep_to': 'E2', 'goal': '待核对段'}],
            'episodes': [{'id': 'E9', 'summary': '擅自扩集'}]}, fill_only=True)
        arcs = self.read('剧本/大纲.json')['arcs']
        self.assertEqual(arcs[0]['ep_to'], 'E1')
        self.assertEqual(arcs[1]['id'], 'A2')
        self.assertEqual([e['id'] for e in self.read('剧本/分集.json')['episodes']], ['E1'])

    def test_incomplete_single_entity_stops_without_repeated_model_calls(self):
        doc = self.read('素材/人物.json')
        doc['characters'][0]['bio_language'] = ''
        doc['characters'][0]['bio_crack'] = ''
        self.write('素材/人物.json', doc)
        replies = [json.dumps({'characters': [{'ref': '@character:hero', 'bio_language': '短句'}]}),
                   json.dumps({'characters': [{'ref': '@character:hero', 'bio_crack': '结巴'}]})]
        with patch.object(pipeline, 'pick_vendor', return_value={}), \
             patch.object(pipeline, 'VendorClient'), \
             patch.object(pipeline, 'chat_retry', side_effect=replies) as call:
            result = pipeline.cmd_units(str(self.root), None, stage='complete')
        self.assertEqual(call.call_count, 1)
        self.assertFalse(result['ok'])
        self.assertEqual(units.pending_settings(self.root)['remaining'], 1)

    def test_expand_without_episode_reuses_units_pipeline(self):
        with patch.object(pipeline, 'cmd_units', return_value={'ok': True}) as build, \
             patch.object(pipeline, 'chat_retry', side_effect=AssertionError('不得调用模型')):
            pipeline.cmd_expand(str(self.root), None, '一个新的故事构想', 1, None)
            build.assert_called_once()
            self.assertEqual(build.call_args.kwargs['stage'], 'complete')

    def test_target_episode_count_must_match_before_lock(self):
        brief.save_brief(self.root, {'total_episodes': 15})
        result = units.anchor(self.root)
        self.assertFalse(result['ok'])
        self.assertIn('SPEC_EP_COUNT', [e['code'] for e in result['report']['errors']])
        self.assertFalse(flow.workflow_state(self.root)['can_anchor'])

    def test_completion_rejects_count_conflict_before_model_or_idea_write(self):
        brief.save_brief(self.root, {'total_episodes': 15})
        idea_path = self.root / '剧本/构想.txt'
        before = idea_path.read_bytes()
        with patch.object(pipeline, 'VendorClient') as client:
            with self.assertRaisesRegex(ValueError, '目标.*15.*当前.*1'):
                pipeline.cmd_units(str(self.root), None, stage='complete', idea_text='另一个故事的新构想')
            client.assert_not_called()
        self.assertEqual(idea_path.read_bytes(), before)
        state = flow.workflow_state(self.root)
        self.assertFalse(state['can_complete'])
        self.assertEqual(state['episode_count'], 1)
        self.assertEqual(state['target_episodes'], 15)
        self.assertEqual(state['completion_blockers'][0]['code'], 'SPEC_EP_COUNT')
        self.assertFalse(flow.completion_preflight(self.root, target_episodes=1)['ok'],
                         '请求参数不能掩盖落盘制作规则的冲突')

    def test_completion_rejects_existing_overlap_before_model(self):
        doc = self.read('剧本/大纲.json')
        doc['arcs'].append({'id': 'A2', 'ep_from': 'E1', 'ep_to': 'E1', 'goal': '另一个目标'})
        self.write('剧本/大纲.json', doc)
        with patch.object(pipeline, 'VendorClient') as client:
            with self.assertRaisesRegex(ValueError, '分段集区间重叠'):
                pipeline.cmd_units(str(self.root), None, stage='complete')
            client.assert_not_called()

    def test_completion_preflight_allows_missing_fields_and_keeps_conflicts_after_count_fix(self):
        self.write('剧本/大纲.json', {'units_version': 1, 'premise': '发现真相'})
        self.assertTrue(flow.completion_preflight(self.root)['ok'])
        self.assertFalse(flow.completion_preflight(self.root, target_episodes=15)['ok'])
        self.write('剧本/大纲.json', {'units_version': 1, 'arcs': [
            {'id': 'A1', 'ep_from': 'E1', 'ep_to': 'E1'},
            {'id': 'A2', 'ep_from': 'E1', 'ep_to': 'E1'}]})
        brief.save_brief(self.root, {'total_episodes': 1})
        report = flow.completion_preflight(self.root)
        self.assertFalse(report['ok'])
        self.assertEqual([e['code'] for e in report['errors']], ['ARC_OVERLAP'])

    def test_completion_makes_no_progress_stops_without_loop(self):
        doc = self.read('剧本/分集.json')
        doc['episodes'][0].pop('beats')
        self.write('剧本/分集.json', doc)
        with patch.object(pipeline, 'pick_vendor', return_value={}), \
             patch.object(pipeline, 'VendorClient'), \
             patch.object(pipeline, 'chat_retry', return_value='{"episodes": []}') as call:
            result = pipeline.cmd_units(str(self.root), None, stage='complete')
        self.assertEqual(call.call_count, 1)
        self.assertFalse(result['ok'])
        self.assertIn('未产生变化', result['incomplete'][0])
        self.assertEqual(self.read('剧本/分集.json')['episodes'][0]['summary'], '甲发现证据')

    def test_rule_change_during_expansion_does_not_write_generated_text(self):
        units.anchor(self.root)
        before = (self.root / '剧本/分集.json').read_bytes()
        def response(*args, **kwargs):
            brief.save_brief(self.root, {'episode_minutes': 3})
            return '甲：生成的正文'
        with patch.object(pipeline, 'pick_vendor', return_value={}), \
             patch.object(pipeline, 'VendorClient'), \
             patch.object(pipeline, 'chat_retry', side_effect=response), \
             patch.object(pipeline.PM, 'expand_episode_prompt', return_value=('系统', '用户')):
            with self.assertRaisesRegex(ValueError, '确认'):
                pipeline.cmd_expand(str(self.root), None, None, 1, 'E1')
        self.assertEqual((self.root / '剧本/分集.json').read_bytes(), before)

    def test_workflow_exposes_check_before_lock_and_lock_before_expansion(self):
        state = flow.workflow_state(self.root)
        self.assertEqual(state['state'], 'review')
        self.assertTrue(state['can_anchor'])
        self.assertFalse(state['can_expand'])
        self.assertEqual(state['next_action'], 'anchor')
        units.anchor(self.root)
        self.assertEqual(flow.workflow_state(self.root)['state'], 'locked')
        self.assertEqual(flow.workflow_state(self.root)['next_action'], 'expand')

    def test_missing_settings_expose_completion_action_and_exact_fields(self):
        doc = self.read('素材/人物.json')
        doc['characters'][0].pop('biography')
        self.write('素材/人物.json', doc)
        state = flow.workflow_state(self.root)
        self.assertEqual(state['next_action'], 'complete_settings')
        self.assertTrue(any(e['code']=='CHARACTER_SETTINGS_INCOMPLETE' for e in state['blockers']))
        self.assertEqual(state['settings_missing'][0]['ref'], '@character:hero')
        self.assertIn('biography', state['settings_missing'][0]['missing'])
        self.assertFalse(state['can_anchor'])

    def test_structural_conflict_routes_to_edit_before_paid_completion(self):
        brief.save_brief(self.root, {'total_episodes': 2})
        state = flow.workflow_state(self.root)
        self.assertEqual(state['next_action'], 'repair')
        self.assertFalse(state['can_complete'])

    def test_old_confirmed_project_can_complete_newly_required_design_without_unlock_step(self):
        units.anchor(self.root)
        doc = self.read('素材/人物.json')
        doc['characters'][0].pop('appearance')
        self.write('素材/人物.json', doc)
        state = flow.workflow_state(self.root)
        self.assertEqual(state['next_action'], 'complete_settings')
        self.assertTrue(state['can_complete'])
        reply = json.dumps({'characters':[{'ref':'@character:hero', 'appearance':{'proposals':
            {'face':'窄脸','hair':'短发','body_type':'清瘦','outfit':'蓝衣'}}}]})
        with patch.object(pipeline,'pick_vendor',return_value={}), patch.object(pipeline,'VendorClient'), patch.object(pipeline,'chat_retry',return_value=reply) as call:
            result = pipeline.cmd_units(str(self.root),None,stage='entity')
        self.assertTrue(result['ok'])
        self.assertEqual(call.call_count,1)

    def test_rewriting_locked_project_reuses_settings_and_generates_beyond_first_batch(self):
        units.anchor(self.root)
        before = (self.root / '剧本/分集.json').read_bytes()
        def episodes(first, last):
            return [{'id': f'E{i}', 'summary': f'寻找新证据{i}', 'beats': ['寻找'],
                     'arc_id': 'NEW', 'cast_refs': ['@character:hero'],
                     'scene_refs': ['@scene:room']} for i in range(first, last + 1)]
        responses = [json.dumps({'premise': '新主线', 'arcs': [{'id': 'NEW', 'ep_from': 'E1', 'ep_to': 'E3'}],
            'roster': {'characters': [{'id': 'hero', 'name': '甲', 'role': '配角', 'gender': '女'}]},
            'episodes': episodes(1, 2)}), json.dumps({'episodes': episodes(3, 3),
            'arcs': [{'id': 'NEW', 'ep_from': 'E3', 'ep_to': 'E3'}]}),
            json.dumps({'characters': [{'ref': '@character:hero', **{k: '新设定' for k in units.CHAR_KEYS}}],
                        'scenes': [{'ref': '@scene:room', 'spatial_limit': '新的限制', 'action_slots': ['窗边']}]})]
        with patch.object(pipeline, 'pick_vendor', return_value={}), patch.object(pipeline, 'VendorClient'), \
             patch.object(pipeline, 'chat_retry', side_effect=responses) as call:
            result = pipeline.cmd_units(str(self.root), None, eps_n=3, arc_size=2, stage='replan',
                revision_mode='rewrite', revision_instructions='沿旧主线增加线索，调整分集')
        self.assertTrue(result['ok'])
        self.assertEqual(call.call_count, 2)
        self.assertEqual((self.root / '剧本/分集.json').read_bytes(), before)
        import story_planning_versions as planning
        candidate = units.load_units(planning.version_project(self.root, result['version_id']))
        self.assertEqual([e['id'] for e in candidate['episodes']], ['E1', 'E2', 'E3'])
        self.assertEqual(candidate['outline']['arcs'][0]['ep_from'], 'E1')
        self.assertEqual(candidate['characters'][0]['role'], '主角')
        self.assertEqual(candidate['characters'][0]['bio_arc'], '已有设定')

    def test_replan_episode_json_failure_stops_before_any_entity_generation(self):
        skeleton = {'premise': '新主线', 'arcs': [{'id': 'NEW', 'ep_from': 'E1', 'ep_to': 'E2'}]}
        replies = [json.dumps(skeleton), '{"episodes":[{"id":"E1"', '{"episodes":[{"id":"E1"']
        output = io.StringIO()
        with patch.object(pipeline, 'pick_vendor', return_value={}), patch.object(pipeline, 'VendorClient'), \
             patch.object(pipeline, 'chat_retry', side_effect=replies) as call, \
             patch.object(units, 'apply_entities') as entities, redirect_stdout(output):
            result = pipeline.cmd_units(str(self.root), None, eps_n=2, stage='replan')
        self.assertFalse(result['ok'])
        self.assertEqual(call.call_count, 3)
        entities.assert_not_called()
        self.assertNotIn('U2 设定层', output.getvalue())
        self.assertNotIn('[待确认]', output.getvalue())
        import story_planning_versions as planning
        folder = planning.version_project(self.root, result['version_id']) / '剧本/规划诊断'
        self.assertEqual(len(list(folder.glob('*.json'))), 3)

    def test_batch_arc_is_bound_by_saved_skeleton_not_model_spelling(self):
        cards = {'episodes': [
            {'id': 'E13', 'summary': '证人到场', 'beats': ['证人到场']},
            {'id': 'E14', 'arc_id': 'ARC4', 'summary': '核对证据', 'beats': ['核对证据']},
            {'id': 'E15', 'arc_id': 'RC4', 'summary': '公开真相', 'beats': ['公开真相']}]}
        pipeline._validate_planning_cards(cards, ['E13', 'E14', 'E15'], 'ARC4')
        self.assertEqual([r['arc_id'] for r in cards['episodes']], ['ARC4', 'ARC4', 'ARC4'])
        self.assertEqual(cards['episodes'][2]['summary'], '公开真相')

    def test_arc_binding_does_not_accept_missing_plot_or_wrong_episode_ids(self):
        for row, message in [
            ({'id': 'E2', 'summary': '', 'beats': ['动作']}, '概要'),
            ({'id': 'E2', 'summary': {'text': '不是字符串'}, 'beats': ['动作']}, '概要'),
            ({'id': 'E2', 'summary': '事件', 'beats': []}, '节拍'),
            ({'id': 'E9', 'summary': '事件', 'beats': ['动作']}, '集号')]:
            with self.subTest(row=row):
                with self.assertRaisesRegex(ValueError, message):
                    pipeline._validate_planning_cards({'episodes': [row]}, ['E2'], 'A2')

    def test_resume_reuses_saved_response_and_existing_cards_without_new_calls(self):
        import story_planning_versions as planning
        skeleton = {'premise': '延续证据调查', 'arcs': [{'id': 'NEXT', 'ep_from': 'E1', 'ep_to': 'E3'}]}
        replies = [json.dumps(skeleton), '{"episodes":[]}', '{"episodes":[]}']
        with patch.object(pipeline, 'pick_vendor', return_value={}), patch.object(pipeline, 'VendorClient'), \
             patch.object(pipeline, 'chat_retry', side_effect=replies):
            failed = pipeline.cmd_units(str(self.root), None, eps_n=3, stage='replan')
        candidate = planning.version_project(self.root, failed['version_id'])
        units.apply_story(candidate, {'episodes': [{'id': 'E2', 'arc_id': 'NEXT',
            'summary': '人工已核对的线索', 'beats': ['保存线索'], 'cast_refs': ['@character:hero']}]})
        pipeline._record_planning_response(candidate, 'U1 批 NEXT', 2,
            json.dumps({'episodes': [{'id': 'E3', 'arc_id': 'NEX', 'summary': '沿线索公开真相',
                'beats': ['公开真相'], 'cast_refs': ['@character:hero']}]}, ensure_ascii=False),
            type('Client', (), {'last_finish_reason': 'stop', 'last_usage': {}})(), '分段号错误')
        before = planning.current_revision(self.root)
        versions_before = len(planning.list_versions(self.root))
        with patch.object(pipeline, 'pick_vendor', return_value={}), patch.object(pipeline, 'VendorClient'), \
             patch.object(pipeline, 'chat_retry', side_effect=AssertionError('已保存内容不应重调模型')):
            result = pipeline.cmd_units(str(self.root), None, stage='replan', planning_version=failed['version_id'])
        self.assertTrue(result['ok'])
        self.assertEqual(result['version_id'], failed['version_id'])
        self.assertEqual(len(planning.list_versions(self.root)), versions_before)
        self.assertEqual(planning.current_revision(self.root), before)
        episodes = units.load_units(candidate)['episodes']
        self.assertEqual([e['id'] for e in episodes], ['E1', 'E2', 'E3'])
        self.assertEqual(episodes[1]['summary'], '人工已核对的线索')
        self.assertEqual(episodes[2]['arc_id'], 'NEXT')

    def test_resume_will_not_replay_truncated_or_unrelated_responses(self):
        for reason, data in [('length', {'episodes': [{'id': 'E2', 'summary': '剧情', 'beats': ['动作']}]}),
                             ('stop', {'episodes': [{'id': 'E9', 'summary': '别的集', 'beats': ['动作']}]})]:
            pipeline._record_planning_response(self.root, 'U1 批 A2', 1, json.dumps(data),
                type('Client', (), {'last_finish_reason': reason, 'last_usage': {}})())
        self.assertIsNone(pipeline._replay_planning_cards(self.root, ['E2'], 'A2'))

    def test_resume_calls_model_only_for_missing_referenced_settings(self):
        import story_planning_versions as planning
        def pending_settings(workspace, target):
            units.apply_story(workspace, {'premise': '延续调查',
                'arcs': [{'id': 'NEXT', 'ep_from': 'E1', 'ep_to': 'E2'}],
                'roster': {'props': [{'id': 'clue', 'name': '信件'}]},
                'episodes': [{'id': 'E2', 'arc_id': 'NEXT', 'summary': '信件揭示真相',
                    'beats': ['读信'], 'cast_refs': ['@character:hero'], 'key_asset_refs': ['@prop:clue']}]})
            book = units.load_units(workspace)['episodes_doc']
            book['episodes'][0]['arc_id'] = 'NEXT'
            units._write(units.path_episodes(workspace), book)
            return {'ok': False, 'incomplete': ['信件使用边界未补全']}
        failed = planning.generate(self.root, target_episodes=2, runner=pending_settings)
        candidate = planning.version_project(self.root, failed['version_id'])
        before_cards = units.load_units(candidate)['episodes']
        answer = json.dumps({'props': [{'ref': '@prop:clue', 'usage_boundary': '只能证明信上写明的事实'}]})
        with patch.object(pipeline, 'pick_vendor', return_value={}), patch.object(pipeline, 'VendorClient'), \
             patch.object(pipeline, 'chat_retry', return_value=answer) as call:
            result = pipeline.cmd_units(str(self.root), None, stage='replan', planning_version=failed['version_id'])
        self.assertTrue(result['ok'])
        self.assertEqual(call.call_count, 1, '已有分集不应重跑模型')
        self.assertIn('设定监督', call.call_args.args[1][0]['content'])
        recovered = units.load_units(candidate)
        self.assertEqual(recovered['episodes'], before_cards)
        self.assertEqual(recovered['props'][0]['usage_boundary'], '只能证明信上写明的事实')

    def test_replan_rejects_missing_requested_cards_without_running_entity_stage(self):
        skeleton = {'premise': '新主线', 'arcs': [{'id': 'NEW', 'ep_from': 'E1', 'ep_to': 'E2'}]}
        replies = [json.dumps(skeleton), '{"episodes":[]}', '{"episodes":[]}']
        with patch.object(pipeline, 'pick_vendor', return_value={}), patch.object(pipeline, 'VendorClient'), \
             patch.object(pipeline, 'chat_retry', side_effect=replies) as call, \
             patch.object(units, 'apply_entities') as entities:
            result = pipeline.cmd_units(str(self.root), None, eps_n=2, stage='replan')
        self.assertFalse(result['ok'])
        self.assertEqual(call.call_count, 3)
        entities.assert_not_called()
        self.assertIn('E1', ';'.join(result['incomplete']))

    def test_rewrite_preserves_existing_settings_for_used_and_unused_assets(self):
        doc = self.read('素材/人物.json')
        doc['characters'].append({'id': 'unused', 'name': '历史人物',
            **{k: '历史设定保持' for k in units.CHAR_KEYS}, 'voice_binding': {'voice_id': 'v-old'}})
        self.write('素材/人物.json', doc)
        skeleton = {'premise': '新主线', 'arcs': [{'id': 'NEW', 'ep_from': 'E1', 'ep_to': 'E2'}]}
        cards = {'episodes': [{'id': f'E{i}', 'summary': '寻找证据', 'beats': ['寻找'],
            'arc_id': 'NEW', 'cast_refs': ['@character:hero']} for i in (1, 2)]}
        settings = {'characters': [{'ref': '@character:hero', **{k: '新设定' for k in units.CHAR_KEYS}}]}
        with patch.object(pipeline, 'pick_vendor', return_value={}), patch.object(pipeline, 'VendorClient'), \
             patch.object(pipeline, 'chat_retry', side_effect=[json.dumps(x) for x in (skeleton, cards, settings)]) as call:
            result = pipeline.cmd_units(str(self.root), None, eps_n=2, stage='replan',
                revision_mode='rewrite', revision_instructions='增加寻找证据的剧情')
        self.assertTrue(result['ok'])
        self.assertEqual(call.call_count, 2)
        import story_planning_versions as planning
        candidate = units.load_units(planning.version_project(self.root, result['version_id']))
        self.assertEqual(candidate['characters'][0]['bio_arc'], '已有设定')
        self.assertEqual(candidate['characters'][1]['bio_arc'], '历史设定保持')
        self.assertEqual(candidate['characters'][1]['voice_binding']['voice_id'], 'v-old')
        self.assertEqual(candidate['scenes'][0]['spatial_limit'], '只有一扇门')
        self.assertNotIn('历史设定保持', call.call_args.args[1][1]['content'])

    def test_replan_invalid_arc_coverage_stops_after_skeleton(self):
        skeleton = {'premise': '新主线', 'arcs': [{'id': 'NEW', 'ep_from': 'E1', 'ep_to': 'E1'}]}
        with patch.object(pipeline, 'pick_vendor', return_value={}), patch.object(pipeline, 'VendorClient'), \
             patch.object(pipeline, 'chat_retry', return_value=json.dumps(skeleton)) as call:
            result = pipeline.cmd_units(str(self.root), None, eps_n=2, stage='replan')
        self.assertFalse(result['ok'])
        self.assertEqual(call.call_count, 2)
        self.assertIn('E2', ';'.join(result['incomplete']))

    def test_replan_fifteen_episodes_with_112_historical_assets_uses_small_batches(self):
        doc = self.read('素材/人物.json')
        doc['characters'] += [{'id': f'history-{n}', 'name': f'历史人物{n}',
            **{key: '历史未引用设定' for key in units.CHAR_KEYS}} for n in range(110)]
        self.write('素材/人物.json', doc)
        spans = [(1, 3), (4, 6), (7, 10), (11, 15)]
        skeleton = {'premise': '到发现真相为止', 'arcs': [
            {'id': f'ARC{i}', 'ep_from': f'E{lo}', 'ep_to': f'E{hi}'}
            for i, (lo, hi) in enumerate(spans, 1)], 'foreshadows': [
            {'id': 'FS1', 'plant': '证据', 'set_in': 'E2', 'pay_in': 'E15', 'refs': ['@character:hero']}]}
        responses = [json.dumps(skeleton)]
        expected_batches = []
        for i, (lo, hi) in enumerate(spans, 1):
            for start in range(max(2, lo), hi + 1, 3):
                ids = [f'E{n}' for n in range(start, min(start + 3, hi + 1))]
                expected_batches.append(ids)
                responses.append(json.dumps({'episodes': [{
                    'id': ident, 'arc_id': f'ARC{i}', 'summary': '寻找证据', 'beats': ['发现新线索'],
                    'cast_refs': ['@character:hero'], 'scene_refs': ['@scene:room']} for ident in ids]}))
        with patch.object(pipeline, 'pick_vendor', return_value={}), patch.object(pipeline, 'VendorClient'), \
             patch.object(pipeline, 'chat_retry', side_effect=responses) as call:
            result = pipeline.cmd_units(str(self.root), None, eps_n=15, stage='replan')
        self.assertTrue(result['ok'])
        self.assertEqual(call.call_count, 7)
        self.assertEqual(len(expected_batches), 6)
        for request, ids in zip(call.call_args_list[1:7], expected_batches):
            self.assertIn('、'.join(ids), request.args[1][0]['content'])
        self.assertNotIn('历史未引用设定', call.call_args.args[1][1]['content'])
        self.assertIn('已有设定', call.call_args.args[1][1]['content'], '修订必须使用既有设定作为输入事实')
        import story_planning_versions as planning
        candidate = units.load_units(planning.version_project(self.root, result['version_id']))
        self.assertEqual(len(candidate['episodes']), 15)
        self.assertIn('FS1', candidate['episodes'][1]['fs_plant'])
        self.assertIn('FS1', candidate['episodes'][14]['fs_pay'])
        self.assertEqual(candidate['characters'][-1]['bio_arc'], '历史未引用设定')
        self.assertEqual(candidate['characters'][0]['bio_arc'], '已有设定')
        self.assertEqual(candidate['episodes'][0]['summary'], '甲发现证据')
        self.assertEqual(candidate['episodes'][0]['text'], '甲：是谁留下的？')


if __name__ == '__main__':
    unittest.main()
