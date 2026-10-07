# -*- coding: utf-8 -*-
"""已有项目修订：扩集保留旧框架，改写只使变化的正文失效。"""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import story_planning_versions as planning
import story_units as units
import creation_pipeline as pipeline
import episode_editor


class StoryRevisionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.episodes = [dict(id=f'E{i}', title=f'线索{i}', summary=f'既有事件{i}',
            beats=[f'既有节拍{i}'], arc_id='OLD', cast_refs=['@character:hero'],
            scene_refs=['@scene:room'], text=f'甲：既有正文{i}', script_edit_rev=2) for i in (1, 2)]
        self.write('剧本/分集.json', dict(mode='generated', units_version=1, rev=2, episodes=self.episodes))
        self.write('剧本/大纲.json', dict(premise='寻找证据', units_version=1,
            arcs=[dict(id='OLD', ep_from='E1', ep_to='E2', goal='找到初始线索')]))
        self.write('剧本/埋线.json', dict(foreshadows=[], hooks=[]))
        self.write('剧本/brief.json', dict(total_episodes=2))
        self.write('素材/人物.json', dict(characters=[dict(id='hero', name='甲',
            **{key: '固定人物设定' for key in units.CHAR_TEXT_KEYS},
            appearance=dict(face='长脸', hair='短发', body_type='清瘦', outfit='灰衣'),
            voice_binding={'voice_id': 'voice-1'}, image_path='素材/甲.png')]))
        self.write('素材/场景.json', dict(scenes=[dict(id='room', name='教室',
            spatial_limit='只有一个出口', action_slots=['门口'], visual_description='白墙、木课桌，右侧窗户')]))
        self.write('素材/道具.json', dict(props=[]))
        (self.root / '剧本/构想.txt').write_text('甲在教室寻找证据的故事', encoding='utf-8')
        for row in self.episodes:
            (self.root / f'剧本/分集剧本_{row["id"]}.txt').write_text(row['text'], encoding='utf-8')

    def write(self, relative, value, root=None):
        path = (root or self.root) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def extension(self, workspace, target):
        current = units.load_units(workspace)
        self.assertEqual(current['episodes'], self.episodes)
        context = json.loads((workspace / '剧本/修订上下文.json').read_text(encoding='utf-8'))
        self.assertEqual(context['mode'], 'extend')
        self.assertIn('既有正文2', (workspace / '剧本/剧本.txt').read_text(encoding='utf-8'))
        rows = copy.deepcopy(current['episodes']) + [dict(id=f'E{i}', arc_id='NEW',
            summary=f'接续事件{i}', beats=['沿既有线索调查'], cast_refs=['@character:hero'],
            scene_refs=['@scene:room']) for i in range(3, target + 1)]
        self.write('剧本/分集.json', dict(mode='generated', episodes=rows), workspace)
        self.write('剧本/大纲.json', dict(premise='寻找证据', arcs=[
            dict(id='OLD', ep_from='E1', ep_to='E2'),
            dict(id='NEW', ep_from='E3', ep_to=f'E{target}')]), workspace)
        return {'ok': True}

    def test_increasing_count_preserves_existing_cards_text_assets_and_only_invalidates_added_episodes(self):
        before = planning.current_revision(self.root)
        result = planning.generate(self.root, target_episodes=4, runner=self.extension)
        self.assertTrue(result['ok'])
        self.assertEqual(planning.current_revision(self.root), before)
        planning.adopt(self.root, result['version_id'], before)
        self.assertNotEqual(planning.current_revision(self.root), before)
        meta = planning._meta(self.root, result['version_id'])
        self.assertEqual(meta['revision_mode'], 'extend')
        self.assertEqual(meta['changes']['preserved'], ['E1', 'E2'])
        self.assertEqual(meta['changes']['added'], ['E3', 'E4'])
        revised = units.load_units(self.root)
        self.assertEqual(revised['episodes'][:2], self.episodes)
        self.assertEqual(revised['characters'][0]['bio_arc'], '固定人物设定')
        self.assertEqual(revised['characters'][0]['voice_binding']['voice_id'], 'voice-1')
        self.assertEqual((self.root / '剧本/分集剧本_E2.txt').read_text(encoding='utf-8'), '甲：既有正文2')
        edits = revised['episodes_doc']['script_edit_revisions']
        self.assertEqual(edits[str(revised['episodes_doc']['rev'])], ['E3', 'E4'])
        self.assertFalse(episode_editor.needs_review(self.root, 'E1', revised['episodes'][0],
            '剧本_E1.json', {'script_rev': 2}, book=revised['episodes_doc']))
        self.assertTrue(episode_editor.needs_review(self.root, 'E3', revised['episodes'][2],
            '剧本_E3.json', {'script_rev': 2}, book=revised['episodes_doc']))

    def test_same_count_rewrite_requires_explicit_revision_instructions(self):
        with self.assertRaisesRegex(ValueError, '修改要求'):
            planning.generate(self.root, target_episodes=2, runner=lambda *_: {'ok': True})
        self.assertEqual(planning.list_versions(self.root), [])

    def test_rewrite_uses_old_story_and_retains_text_for_unchanged_episodes(self):
        book = units.load_units(self.root)['episodes_doc']
        book['script_edit_revisions'] = {'2': 'E2'}
        self.write('剧本/分集.json', book)
        before = planning.current_revision(self.root)
        def rewrite(workspace, target):
            context = json.loads((workspace / '剧本/修订上下文.json').read_text(encoding='utf-8'))
            self.assertEqual(context['episodes'], self.episodes)
            self.assertEqual(context['instructions'], '第二集把线索改为失窃信件')
            rows = copy.deepcopy(self.episodes)
            rows[1]['summary'] = '信件失窃，追查内鬼'
            self.write('剧本/分集.json', dict(episodes=rows), workspace)
            self.write('剧本/大纲.json', dict(premise='寻找证据', arcs=[
                dict(id='OLD', ep_from='E1', ep_to='E2')]), workspace)
            return {'ok': True}
        result = planning.generate(self.root, target_episodes=2, revision_mode='rewrite',
            instructions='第二集把线索改为失窃信件', runner=rewrite)
        self.assertTrue(result['ok'])
        planning.adopt(self.root, result['version_id'], before)
        meta = planning._meta(self.root, result['version_id'])
        self.assertEqual(meta['changes']['changed'], ['E2'])
        rows = units.load_units(self.root)['episodes']
        self.assertEqual(rows[0]['text'], '甲：既有正文1')
        self.assertEqual(rows[1]['text'], '甲：既有正文2')
        self.assertTrue(rows[1]['planning_review_required'])
        self.assertTrue((self.root / '剧本/分集剧本_E2.txt').exists())
        self.assertEqual(units.load_units(self.root)['episodes_doc']['script_edit_revisions']['2'], 'E2')

    def test_extension_rejects_candidate_that_replaces_existing_plot(self):
        def wrong(workspace, target):
            self.extension(workspace, target)
            book = units.load_units(workspace)['episodes_doc']
            book['episodes'][0]['summary'] = '完全无关的新主角故事'
            self.write('剧本/分集.json', book, workspace)
            return {'ok': True}
        before = planning.current_revision(self.root)
        result = planning.generate(self.root, target_episodes=4, runner=wrong)
        self.assertFalse(result['ok'])
        self.assertIn('E1', ';'.join(result['incomplete']))
        self.assertEqual(planning.current_revision(self.root), before)

    def test_extension_model_only_generates_new_cards_and_keeps_existing_settings(self):
        skeleton = dict(premise='寻找证据', arcs=[dict(id='OLD', ep_from='E1', ep_to='E2'),
            dict(id='NEW', ep_from='E3', ep_to='E4')], roster={
            'characters': [dict(id='hero', name='甲')], 'scenes': [], 'props': []})
        cards = dict(episodes=[dict(id=f'E{i}', arc_id='NEW', summary='沿旧线索寻找失窃信件',
            beats=['追踪线索'], cast_refs=['@character:hero'], scene_refs=['@scene:room']) for i in (3, 4)])
        with patch.object(pipeline, 'pick_vendor', return_value={}), patch.object(pipeline, 'VendorClient'), \
             patch.object(pipeline, 'chat_retry', side_effect=[json.dumps(skeleton), json.dumps(cards)]) as call:
            result = pipeline.cmd_units(str(self.root), None, eps_n=4, stage='replan')
        self.assertTrue(result['ok'])
        self.assertEqual(call.call_count, 2)
        for request in call.call_args_list:
            self.assertIn('既有事件1', request.args[1][1]['content'])
            self.assertIn('固定人物设定', request.args[1][1]['content'])
        self.assertIn('E3、E4', call.call_args.args[1][0]['content'])
        candidate = units.load_units(planning.version_project(self.root, result['version_id']))
        self.assertEqual(candidate['episodes'][:2], self.episodes)
        self.assertEqual(candidate['characters'][0]['bio_arc'], '固定人物设定')

    def test_shrinking_is_explicit_rewrite_and_removed_body_stays_in_script_history(self):
        before = planning.current_revision(self.root)
        def shrink(workspace, target):
            context = json.loads((workspace / '剧本/修订上下文.json').read_text(encoding='utf-8'))
            self.assertEqual(len(context['episodes']), 2)
            row = copy.deepcopy(self.episodes[0])
            row.update(summary='合并两个既有事件', beats=['找到线索', '核对证据'])
            self.write('剧本/分集.json', dict(episodes=[row]), workspace)
            self.write('剧本/大纲.json', dict(premise='寻找证据', arcs=[
                dict(id='OLD', ep_from='E1', ep_to='E1')]), workspace)
            return {'ok': True}
        result = planning.generate(self.root, target_episodes=1, revision_mode='rewrite',
            instructions='压缩两个既有事件为一集，结局不变', runner=shrink)
        self.assertTrue(result['ok'])
        planning.adopt(self.root, result['version_id'], before)
        meta = planning._meta(self.root, result['version_id'])
        self.assertEqual(meta['changes']['removed'], ['E2'])
        self.assertEqual(len(units.load_units(self.root)['episodes']), 1)
        self.assertIn('甲：既有正文2', [r['text'] for r in episode_editor.episode_history(self.root, 'E2')])


if __name__ == '__main__':
    unittest.main()
