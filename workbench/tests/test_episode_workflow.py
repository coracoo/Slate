# -*- coding: utf-8 -*-
"""单集改稿、制作进度与演员知情依据的离线业务回归。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for folder in (ROOT / 'workbench/tools', ROOT / 'previs_system/tools'):
    sys.path.insert(0, str(folder))
import project_store
from production_prompts import media_source_hash


class EpisodeWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        self.book = {'rev': 2, 'mode': 'generated', 'anchor_rev': 4, 'episodes': [
            {'id': 'E1', 'text': '甲：旧正文', 'beats': ['发现证据']},
            {'id': 'E2', 'text': '乙：第二集正文'}]}
        self.write('剧本/分集.json', self.book)
        self.shot = {'id': 'S1', 'dur': 4, 'prompt_image': '窗边静帧', 'prompt_video': '缓慢转头'}
        self.unit = {'id': 'v-one', 'shot_ids': ['S1'], 'duration': 4}
        self.write('分镜/剧本_E1.json', {'script_rev': 2, 'shots': [self.shot], 'video_units': [self.unit]})
        self.write('分镜/剧本_E2.json', {'script_rev': 2, 'shots': [self.shot], 'video_units': [self.unit]})

    def write(self, relative, value):
        path = self.project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def test_edit_keeps_cards_other_episodes_and_snapshot(self):
        import episode_editor as editor
        revision = project_store.current_revision(self.project / '剧本/分集.json')
        result = editor.save_episode(self.project, 'E1', '甲：新的正文', revision)
        data, _ = project_store.read_json(self.project / '剧本/分集.json')
        self.assertEqual(data['episodes'][0]['text'], '甲：新的正文')
        self.assertEqual(data['episodes'][0]['beats'], ['发现证据'])
        self.assertEqual(data['episodes'][1], self.book['episodes'][1])
        self.assertEqual(data['anchor_rev'], 4)
        self.assertEqual(result['affected']['boards'][0]['name'], '剧本_E1.json')
        self.assertEqual(len(result['affected']['boards']), 1)
        self.assertTrue(any((self.project / '剧本/.versions').glob('分集.*.json')))
        self.assertIn('甲：旧正文', [v['text'] for v in editor.episode_history(self.project, 'E1')])

    def test_old_editor_revision_rejected_and_same_text_is_noop(self):
        import episode_editor as editor
        revision = project_store.current_revision(self.project / '剧本/分集.json')
        result = editor.save_episode(self.project, 'E1', '甲：旧正文', revision)
        self.assertFalse(result['changed'])
        editor.save_episode(self.project, 'E1', '甲：新稿', revision)
        with self.assertRaises(project_store.RevisionConflict):
            editor.save_episode(self.project, 'E1', '甲：过期稿', revision)

    def test_progress_counts_per_episode_and_never_counts_missing_or_stale_binding(self):
        import production_progress as progress
        board, _ = project_store.read_json(self.project / '分镜/剧本_E1.json')
        (self.project / 'frame.png').write_bytes(b'image')
        board['shots'][0]['keyframe'] = {'path': 'frame.png', 'source_hash': media_source_hash([self.shot], 'image', self.unit)}
        board['video_units'][0]['video_binding'] = {'path': 'missing.mp4', 'source_hash': media_source_hash([self.shot], 'video', self.unit)}
        self.write('分镜/剧本_E1.json', board)
        rows = progress.inspect_project(self.project)['episodes']
        self.assertEqual(rows[0]['keyframes'], {'done': 1, 'total': 1, 'review': 0})
        self.assertEqual(rows[0]['videos']['done'], 0)
        self.assertEqual(rows[1]['keyframes']['done'], 0)
        import episode_editor as editor
        editor.save_episode(self.project, 'E1', '甲：改变动作后的正文', project_store.current_revision(self.project / '剧本/分集.json'))
        rows = progress.inspect_project(self.project)['episodes']
        self.assertTrue(rows[0]['storyboard_review'])
        self.assertFalse(rows[1]['storyboard_review'])
        self.assertEqual(rows[0]['keyframes']['done'], 0)
        self.assertEqual(rows[0]['keyframes']['review'], 1)

    def test_partial_project_does_not_report_all_scripts_done(self):
        self.book['episodes'][1]['text'] = ''
        self.write('剧本/分集.json', self.book)
        import preproduction_flow
        stages = {r['id']: r for r in preproduction_flow.inspect_project(self.project)['stages']}
        self.assertEqual(stages['episode_scripts']['status'], 'partial')
        self.assertEqual(stages['episode_scripts']['completed'], 1)
        self.assertEqual(stages['episode_scripts']['total'], 2)

    def test_expansion_started_before_manual_edit_cannot_overwrite_it(self):
        import creation_pipeline as pipeline
        import episode_editor as editor
        from unittest.mock import patch
        (self.project / '剧本/构想.txt').write_text('守住秘密的故事构想', encoding='utf-8')
        def llm_reply(*args, **kwargs):
            editor.save_episode(self.project, 'E1', '甲：刚保存的人工稿', project_store.current_revision(self.project / '剧本/分集.json'))
            return '甲：稍后返回的模型稿'
        with patch.object(pipeline, 'pick_vendor', return_value={}), patch.object(pipeline, 'VendorClient'), \
             patch.object(pipeline, 'chat_retry', side_effect=llm_reply), \
             patch.object(pipeline.story_units, 'units_block', return_value=''), \
             patch.object(pipeline.story_units, 'gap_report', return_value={'blocking': False}):
            with self.assertRaises(project_store.RevisionConflict):
                pipeline.cmd_expand(str(self.project), None, None, 6, 'E1')
        self.assertEqual(project_store.read_json(self.project / '剧本/分集.json')[0]['episodes'][0]['text'], '甲：刚保存的人工稿')

    def test_imported_slices_survive_first_manual_edit(self):
        import episode_editor as editor
        (self.project / '剧本/剧本.txt').write_text('甲：第一集乙：第二集', encoding='utf-8')
        self.write('剧本/分集.json', {'mode': 'imported', 'episodes': [
            {'id': 'E1', 'char_start': 0, 'char_end': 5}, {'id': 'E2', 'char_start': 5, 'char_end': 10}]})
        editor.save_episode(self.project, 'E1', '甲：修改第一集', project_store.current_revision(self.project / '剧本/分集.json'))
        book, _ = project_store.read_json(self.project / '剧本/分集.json')
        self.assertEqual(book['episodes'][1]['text'], '乙：第二集')

    def test_video_completion_needs_current_binding_and_real_file(self):
        import production_progress as progress
        (self.project / 'unit.mp4').write_bytes(b'video')
        board, _ = project_store.read_json(self.project / '分镜/剧本_E1.json')
        board['video_units'][0]['video_binding'] = {'path': 'unit.mp4', 'source_hash': media_source_hash([self.shot], 'video', self.unit)}
        self.write('分镜/剧本_E1.json', board)
        self.assertEqual(progress.inspect_project(self.project)['episodes'][0]['videos']['done'], 1)
        board['shots'][0]['prompt_video'] = '改为背对窗户'
        self.write('分镜/剧本_E1.json', board)
        row = progress.inspect_project(self.project)['episodes'][0]
        self.assertEqual(row['videos']['done'], 0)
        self.assertEqual(row['videos']['review'], 1)

    def test_episode_video_completion_requires_current_board_revision(self):
        import production_progress as progress
        board_path = self.project / '分镜/剧本_E1.json'
        (self.project / 'episode.mp4').write_bytes(b'video')
        self.write('创作/creation.json', {'items': [{'scope': 'E', 'board': board_path.name,
            'board_revision': project_store.current_revision(board_path), 'status': 'done',
            'outputs': [{'path': 'episode.mp4'}]}]})
        self.assertTrue(progress.inspect_project(self.project)['episodes'][0]['episode_video_done'])
        board, _ = project_store.read_json(board_path)
        board['shots'][0]['prompt_video'] = '修改动作后的新版本'
        self.write('分镜/剧本_E1.json', board)
        self.assertFalse(progress.inspect_project(self.project)['episodes'][0]['episode_video_done'])

    def test_asset_progress_counts_only_episode_references_with_files(self):
        import production_progress as progress
        self.book['episodes'][0]['cast_refs'] = ['@character:hero']
        self.book['episodes'][0]['scene_refs'] = ['@scene:room']
        self.book['episodes'][1]['cast_refs'] = ['@character:other']
        self.write('剧本/分集.json', self.book)
        self.write('素材/人物.json', {'characters': [{'id': 'hero', 'name': '甲'}, {'id': 'other', 'name': '乙'}]})
        self.write('素材/场景.json', {'scenes': [{'id': 'room', 'name': '房间'}]})
        path = self.project / '素材/人物/hero.png'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'image')
        rows = progress.inspect_project(self.project)['episodes']
        self.assertEqual(rows[0]['assets'], {'done': 1, 'total': 2})
        self.assertEqual(rows[1]['assets'], {'done': 0, 'total': 1})


class ActorEvidenceTests(unittest.TestCase):
    def test_evidence_uses_exact_visible_snapshot_and_flags_implicit_order(self):
        import actor_pipeline as ap
        board = {'actors': {'a': {'name': '甲'}}, 'shots': [{'id': 'S1', 'dur': 4, 'actor_refs': ['a'], 'after_event_ids': ['e1']}]}
        context = {'version': 'actor-context-v1', 'continuities': [{'id': 'main', 'initial_state': {'a': {'known_facts': []}}}],
                   'facts': [{'id': 'f1', 'text': '乙不在场'}, {'id': 'f2', 'text': '未来才揭晓'}],
                   'events': [{'id': 'e1', 'continuity_id': 'main', 'order': 1, 'deltas': [{'actor_id': 'a', 'known_facts_add': ['f1']}]},
                              {'id': 'e2', 'continuity_id': 'main', 'order': 2, 'deltas': [{'actor_id': 'a', 'known_facts_add': ['f2']}]}]}
        result = ap.continuity_evidence(board, 'S1', context)
        self.assertEqual([f['id'] for f in result['actors'][0]['facts']], ['f1'])
        self.assertTrue(result['event_order_explicit'])
        del board['shots'][0]['after_event_ids']
        self.assertFalse(ap.continuity_evidence(board, 'S1', context)['event_order_explicit'])


if __name__ == '__main__':
    unittest.main()
