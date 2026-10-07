# -*- coding: utf-8 -*-
"""正文候选批次不污染正式内容，采用前校验全批基线。"""
import json
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import script_drafts as drafts
import project_store
import creation_pipeline


class ScriptDraftTests(unittest.TestCase):
    def test_latest_batch_replaces_old_candidates_per_episode_and_blocks_old_adoption(self):
        old = self.ready(['E1', 'E2'])
        new = self.ready(['E1'])
        batches = {b['id']: b for b in drafts.list_batches(self.project, include_history=False)}
        self.assertEqual([i['status'] for i in batches[old['id']]['items']], ['stale', 'ready'])
        with self.assertRaisesRegex(project_store.RevisionConflict, '更新候选'):
            drafts.adopt(self.project, old['id'], ['E1'])
        drafts.adopt(self.project, new['id'], ['E1'])
        drafts.adopt(self.project, old['id'], ['E2'])
        self.assertEqual(drafts.list_batches(self.project, include_history=False), [])
        self.assertEqual(len(drafts.list_batches(self.project)), 2)

    def test_formal_text_update_hides_stale_candidate_but_keeps_history(self):
        self.ready(['E1'])
        project_store.update_json(self.book, lambda b: b['episodes'][0].update(text='已修改正文'))
        self.assertEqual(drafts.list_batches(self.project, include_history=False), [])
        item = drafts.list_batches(self.project)[0]['items'][0]
        self.assertEqual(item['status'], 'stale')
        self.assertIn('已更新', item['inactive_reason'])

    def test_candidate_changed_after_diff_review_cannot_be_adopted(self):
        batch = self.ready(['E1'])
        item = drafts.list_batches(self.project)[0]['items'][0]
        reviewed = {'E1': {'before': item['before'], 'after': item['after']}}
        project_store.update_json(drafts._path(self.project, batch['id']), lambda b: b['items'][0].update(after='后来修正的新候选'))
        with self.assertRaises(project_store.RevisionConflict):
            drafts.adopt(self.project, batch['id'], ['E1'], reviewed=reviewed)
        self.assertEqual(json.loads(self.book.read_text(encoding='utf-8'))['episodes'][0]['text'], '原稿一')

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.project = Path(temp.name)
        self.book = self.project / '剧本/分集.json'
        self.book.parent.mkdir()
        self.book.write_text(json.dumps({'mode': 'generated', 'episodes': [
            {'id': 'E1', 'summary': '第一集', 'text': '原稿一'},
            {'id': 'E2', 'summary': '第二集', 'text': '原稿二'}]}, ensure_ascii=False), encoding='utf-8')
        (self.book.parent / '构想.txt').write_text('一个完整的故事', encoding='utf-8')
        for target in ('socket.socket.connect', 'socket.create_connection', 'creation_pipeline.VendorClient.chat'):
            guard = patch(target, side_effect=AssertionError('测试禁止真实网络与模型调用'))
            guard.start()
            self.addCleanup(guard.stop)
        guard = patch.object(creation_pipeline.story_units, 'gap_report', return_value={'blocking': False})
        guard.start()
        self.addCleanup(guard.stop)

    def ready(self, ids=None):
        batch = drafts.create(self.project, ids or ['E1', 'E2'])
        drafts.run(self.project, batch['id'], generate=lambda p,v,i,e,*args: '新稿' + e['id'])
        return batch

    def test_generation_isolated_and_selected_adoption_is_atomic_and_versioned(self):
        before = self.book.read_bytes()
        batch = self.ready()
        self.assertEqual(self.book.read_bytes(), before)
        result = drafts.adopt(self.project, batch['id'], ['E1', 'E2'])
        book = json.loads(self.book.read_text(encoding='utf-8'))
        self.assertEqual([e['text'] for e in book['episodes']], ['新稿E1', '新稿E2'])
        self.assertEqual(book['script_edit_revisions']['1'], ['E1', 'E2'])
        self.assertEqual(result['adopted'], ['E1', 'E2'])
        history = drafts.episode_editor.episode_history(self.project, 'E1')
        self.assertIn('原稿一', [h['text'] for h in history])

    def test_conflict_in_one_episode_blocks_entire_adoption(self):
        batch = self.ready()
        project_store.update_json(self.book, lambda b: b['episodes'][1].update(text='人工编辑'))
        before = self.book.read_bytes()
        with self.assertRaises(project_store.RevisionConflict):
            drafts.adopt(self.project, batch['id'], ['E1', 'E2'])
        self.assertEqual(self.book.read_bytes(), before)

    def test_partial_failure_preserves_success_and_failed_text(self):
        batch = drafts.create(self.project, ['E1', 'E2'])
        def generate(p,v,i,e,*args):
            if e['id'] == 'E2':
                raise ValueError('第二集失败')
            return '成功正文'
        with self.assertRaisesRegex(ValueError, '1 集生成失败'):
            drafts.run(self.project, batch['id'], generate=generate)
        self.assertEqual([e['status'] for e in drafts.list_batches(self.project)[0]['items']], ['ready','failed'])
        drafts.adopt(self.project, batch['id'], ['E1'])
        with self.assertRaisesRegex(ValueError, '成功候选'):
            drafts.adopt(self.project, batch['id'], ['E2'])

    def test_two_subset_confirmations_do_not_invalidate_each_other(self):
        batch = self.ready()
        drafts.adopt(self.project, batch['id'], ['E1'])
        drafts.adopt(self.project, batch['id'], ['E2'])
        self.assertEqual([i['status'] for i in drafts.list_batches(self.project)[0]['items']], ['adopted','adopted'])

    def test_full_script_source_change_blocks_unrelated_episode_adoption(self):
        (self.book.parent / '构想.txt').unlink()
        batch = self.ready(['E2'])
        project_store.update_json(self.book, lambda b: b['episodes'][0].update(text='上集故事已修改'))
        with self.assertRaisesRegex(project_store.RevisionConflict, '故事依据'):
            drafts.adopt(self.project, batch['id'], ['E2'])

    def test_full_script_source_allows_same_batch_subset_adoption(self):
        (self.book.parent / '构想.txt').unlink()
        batch = self.ready()
        drafts.adopt(self.project, batch['id'], ['E1'])
        drafts.adopt(self.project, batch['id'], ['E2'])
        self.assertEqual([i['status'] for i in drafts.list_batches(self.project)[0]['items']], ['adopted', 'adopted'])

    def test_changed_settings_block_adoption_and_repeat_generation_rejected(self):
        batch = self.ready()
        with self.assertRaisesRegex(ValueError, '禁止重复'):
            drafts.run(self.project, batch['id'], generate=lambda *args: self.fail('不应再次调用'))
        (self.book.parent / 'brief.json').write_text('{"episode_minutes":4}', encoding='utf-8')
        with self.assertRaises(project_store.RevisionConflict):
            drafts.adopt(self.project, batch['id'], ['E1'])

    def test_media_and_confirmation_metadata_do_not_invalidate_candidate(self):
        assets = self.project / '素材'
        assets.mkdir()
        characters = assets / '人物.json'
        characters.write_text(json.dumps({'characters':[{'id':'hero','name':'英雄','biography':'守护家人'}]}), encoding='utf-8')
        batch = self.ready(['E1'])
        characters.write_text(json.dumps({'anchor_rev':9, 'characters':[{'id':'hero','name':'英雄','biography':'守护家人',
            'voice_binding':{'voice_id':'new'}, 'sheet_prompt':'新构图', 'asset_revision':2}]}), encoding='utf-8')
        (self.book.parent / 'style.json').write_text('{"image":"new-style"}', encoding='utf-8')
        result = drafts.adopt(self.project, batch['id'], ['E1'])
        self.assertEqual(result['adopted'], ['E1'])

    def test_legacy_candidate_uses_saved_input_for_semantic_comparison(self):
        batch = self.ready(['E1'])
        project_store.update_json(drafts._path(self.project,batch['id']), lambda b:(b.pop('context_version'),b.update(context='old-byte-fingerprint')) and None)
        self.assertEqual(drafts.adopt(self.project,batch['id'],['E1'])['adopted'], ['E1'])

    def test_repeated_confirmation_idempotent_even_after_receipt_write_failure(self):
        batch = self.ready(['E1'])
        with patch.object(drafts.planning, '_write_bytes', side_effect=OSError('显示记录写入失败')):
            result = drafts.adopt(self.project, batch['id'], ['E1'])
        self.assertTrue(result['warnings'])
        first = self.book.read_bytes()
        drafts.adopt(self.project, batch['id'], ['E1'])
        self.assertEqual(self.book.read_bytes(), first)
        self.assertEqual(drafts.list_batches(self.project)[0]['items'][0]['status'], 'adopted')

    def test_prompt_uses_current_text_and_revision_instructions(self):
        with patch.object(creation_pipeline, 'pick_vendor', return_value={}), patch.object(creation_pipeline, 'VendorClient'), \
             patch.object(creation_pipeline, 'chat_retry', return_value='修订后的稿') as chat:
            result = creation_pipeline.generate_episode_text(str(self.project), None, '完整故事构想',
                {'id':'E1','text':'已有事实','summary':'当前概要'}, None, '缩短对白')
        self.assertEqual(result, '修订后的稿')
        payload = str(chat.call_args)
        self.assertIn('已有事实', payload)
        self.assertIn('缩短对白', payload)

    def test_previous_episode_plan_change_blocks_adoption(self):
        batch = self.ready(['E2'])
        project_store.update_json(self.book, lambda b: b['episodes'][0].update(summary='上集剧情已改'))
        with self.assertRaises(project_store.RevisionConflict):
            drafts.adopt(self.project, batch['id'], ['E2'])

    def test_input_failure_is_terminal_and_allows_new_batch(self):
        batch = drafts.create(self.project, ['E1'])
        path = drafts._path(self.project, batch['id']).parent / '输入/剧本/分集.json'
        path.write_text('broken', encoding='utf-8')
        with self.assertRaises(ValueError):
            drafts.run(self.project, batch['id'], generate=lambda *args: self.fail('输入坏时不可请求'))
        self.assertEqual(drafts.list_batches(self.project)[0]['status'], 'partial')
        drafts.create(self.project, ['E1'])

    def test_active_batch_blocks_duplicate_and_dead_job_reconciles(self):
        batch = drafts.create(self.project, ['E1'])
        with self.assertRaisesRegex(ValueError, '已有正文生成批次'):
            drafts.create(self.project, ['E1'])
        drafts.bind_job(self.project, batch['id'], 4)
        drafts.reconcile(self.project, lambda jid: {'status':'running'})
        self.assertEqual(drafts.list_batches(self.project)[0]['status'], 'queued')
        drafts.reconcile(self.project, lambda jid: {'status':'interrupted'})
        self.assertEqual(drafts.list_batches(self.project)[0]['items'][0]['status'], 'failed')
        drafts.create(self.project, ['E1'])

    def test_confirmation_retry_repairs_text_copy(self):
        batch = self.ready(['E1'])
        with patch.object(drafts.episode_editor, 'sync_episode_copy', side_effect=OSError('磁盘繁忙')):
            self.assertTrue(drafts.adopt(self.project,batch['id'],['E1'])['warnings'])
        self.assertFalse((self.book.parent / '分集剧本_E1.txt').exists())
        self.assertFalse(drafts.adopt(self.project,batch['id'],['E1'])['warnings'])
        self.assertEqual((self.book.parent / '分集剧本_E1.txt').read_text(encoding='utf-8'),'新稿E1')

    def test_validation_failure_stores_specific_entities_and_model_gap(self):
        batch = drafts.create(self.project, ['E1'])
        report = {'blocking': True, 'missing_speakers': [{'name':'陌生客','line':'陌生客：你好'}], 'missing_scenes': [{'name':'新码头'}]}
        with patch.object(creation_pipeline.story_units, 'gap_report', return_value=report):
            with self.assertRaisesRegex(ValueError, '1 集生成失败'):
                drafts.run(self.project, batch['id'], generate=lambda *args: '陌生客：你好。\n【缺口：缺少关键账册】')
        item = drafts.list_batches(self.project)[0]['items'][0]
        self.assertEqual(item['status'], 'failed')
        self.assertIn('陌生客', item['error'])
        self.assertIn('新码头', item['error'])
        self.assertIn('关键账册', item['error'])
        self.assertTrue(item['after'])
        self.assertEqual(item['failure_kind'], 'validation')

    def test_legacy_false_positive_is_rechecked_once_without_regeneration_or_adoption(self):
        batch = self.ready(['E1','E2'])
        drafts.adopt(self.project, batch['id'], ['E2'])
        def legacy(doc):
            doc['status'] = 'partial'
            item = doc['items'][0]
            item.update(status='failed', error='候选涉及未登记实体或规划冲突，请修改角色/场景设定后再生成')
            item.pop('validation_version', None)
        drafts._update(drafts._path(self.project,batch['id']), legacy)
        before = self.book.read_bytes()
        doc = drafts.list_batches(self.project)[0]
        self.assertEqual([i['status'] for i in doc['items']], ['ready','adopted'])
        self.assertTrue(doc['items'][0]['validation_rechecked'])
        self.assertEqual(self.book.read_bytes(), before)
        with patch.object(drafts, 'revalidate', side_effect=AssertionError('不能重复校验')):
            drafts.list_batches(self.project)

    def test_model_failure_is_not_reclassified_as_success(self):
        batch = self.ready(['E1'])
        def failed(doc):
            doc['status'] = 'partial'
            doc['items'][0].update(status='failed',failure_kind='generation',error='模型输出截断')
        drafts._update(drafts._path(self.project,batch['id']), failed)
        self.assertEqual(drafts.list_batches(self.project)[0]['items'][0]['status'], 'failed')

    def test_validation_gap_is_repaired_once_and_original_is_kept(self):
        batch = drafts.create(self.project, ['E1'])
        def validate(p, ep, text):
            return {'blocking': text.startswith('陌生追兵'), 'missing_speakers':
                    [{'name':'陌生追兵'}] if text.startswith('陌生追兵') else []}
        calls = []
        def repair(p, v, ep, text, report, instructions):
            calls.append(text)
            return '守卫：停下。'
        with patch.object(creation_pipeline.story_units, 'gap_report', side_effect=validate):
            drafts.run(self.project, batch['id'], generate=lambda *a:'陌生追兵：停下。', repair=repair)
        item = drafts.list_batches(self.project)[0]['items'][0]
        self.assertEqual(calls, ['陌生追兵：停下。'])
        self.assertEqual(item['status'], 'ready')
        self.assertEqual(item['after'], '守卫：停下。')
        self.assertEqual(item['repair_original'], '陌生追兵：停下。')
        self.assertEqual(item['repairs'][0]['status'], 'done')
        self.assertEqual(json.loads(self.book.read_text(encoding='utf-8'))['episodes'][0]['text'], '原稿一')

    def test_failed_repair_does_not_loop_or_discard_generated_text(self):
        batch = drafts.create(self.project, ['E1'])
        calls = []
        with patch.object(creation_pipeline.story_units, 'gap_report', return_value={
                'blocking':True,'missing_speakers':[{'name':'新角色'}]}):
            def repair(*args):
                calls.append(1)
                return '新角色：修改稿。'
            with self.assertRaisesRegex(ValueError, '1 集生成失败'):
                drafts.run(self.project, batch['id'], generate=lambda *a:'新角色：原稿。', repair=repair)
        item = drafts.list_batches(self.project)[0]['items'][0]
        self.assertEqual(len(calls), 1)
        self.assertEqual(item['after'], '新角色：原稿。')
        self.assertEqual(item['repairs'][0]['proposed'], '新角色：修改稿。')
        self.assertEqual(item['repairs'][0]['status'], 'failed')

    def test_confirmed_repair_only_handles_selected_failed_episode(self):
        batch = self.ready(['E1','E2'])
        drafts.adopt(self.project, batch['id'], ['E2'])
        def failed(doc):
            doc['status']='partial'
            doc['items'][0].update(status='failed',failure_kind='validation',error='未登记说话人：新角色')
        drafts._update(drafts._path(self.project,batch['id']), failed)
        original = self.book.read_bytes()
        drafts.queue_repair(self.project,batch['id'],['E1'],'沿用已有角色')
        with self.assertRaisesRegex(ValueError,'处理中|已有'):
            drafts.queue_repair(self.project,batch['id'],['E1'])
        calls=[]
        def repair(p,v,ep,text,report,instructions):
            calls.append((ep['id'],instructions))
            return '修正稿'
        with patch.object(creation_pipeline.story_units, 'gap_report', side_effect=lambda p,e,t: {
                'blocking':t!='修正稿','missing_speakers':[{'name':'新角色'}] if t!='修正稿' else []}):
            drafts.run_repair(self.project,batch['id'],repair=repair)
        doc=drafts.list_batches(self.project)[0]
        self.assertEqual(calls,[('E1','沿用已有角色')])
        self.assertEqual([i['status'] for i in doc['items']], ['ready','adopted'])
        self.assertEqual(self.book.read_bytes(),original)

    def test_repair_refuses_changed_planning_and_generation_failures(self):
        batch=self.ready(['E1'])
        def failed(doc):
            doc['status']='partial'
            doc['items'][0].update(status='failed',failure_kind='generation',error='网络错误')
        drafts._update(drafts._path(self.project,batch['id']),failed)
        with self.assertRaisesRegex(ValueError,'文本校验'):
            drafts.queue_repair(self.project,batch['id'],['E1'])
        drafts._update(drafts._path(self.project,batch['id']),lambda d:d['items'][0].update(failure_kind='validation'))
        project_store.update_json(self.book,lambda b:b['episodes'][0].update(summary='规划改动'))
        with self.assertRaises(project_store.RevisionConflict):
            drafts.queue_repair(self.project,batch['id'],['E1'])

    def test_repair_refuses_replaced_formal_text_before_requesting_model(self):
        batch=self.ready(['E1'])
        def failed(doc):
            doc['status']='partial'
            doc['items'][0].update(status='failed',failure_kind='validation',error='缺口')
        drafts._update(drafts._path(self.project,batch['id']),failed)
        project_store.update_json(self.book,lambda b:b['episodes'][0].update(text='人工更新正文'))
        with self.assertRaisesRegex(project_store.RevisionConflict,'正文已更新'):
            drafts.queue_repair(self.project,batch['id'],['E1'])

    def test_interruption_keeps_initial_generated_text_and_stops_repair_record(self):
        batch=drafts.create(self.project,['E1'])
        report={'blocking':True,'missing_speakers':[{'name':'新角色'}]}
        def repair(*args):
            drafts.interrupt(self.project,batch['id'],'服务中断')
            raise KeyboardInterrupt()
        with patch.object(creation_pipeline.story_units,'gap_report',return_value=report):
            with self.assertRaises(KeyboardInterrupt):
                drafts.run(self.project,batch['id'],generate=lambda *a:'新角色：原稿',repair=repair)
        item=drafts.list_batches(self.project)[0]['items'][0]
        self.assertEqual(item['after'],'新角色：原稿')
        self.assertEqual(item['repairs'][0]['status'],'failed')
        self.assertEqual(item['status'],'failed')


if __name__ == '__main__':
    unittest.main()
