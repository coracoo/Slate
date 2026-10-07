# -*- coding: utf-8 -*-
"""素材写入统一契约的跨入口验证。"""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1]/'tools'), str(Path(__file__).resolve().parents[2]/'previs_system/tools')]
import asset_repository as repo
import project_store


class AssetRepositoryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.name = '素材/道具.json'
        self.doc = {'props': [{'id': 'flag', 'name': '令旗', 'visual_description': '黑布旗', 'asset_required': True}]}

    def save(self, doc=None, **kwargs):
        return repo.commit(self.root, {self.name: doc or self.doc}, source=kwargs.pop('source', 'planning'), **kwargs)

    def read(self):
        return json.loads((self.root/self.name).read_text('utf-8'))

    def test_operation_replay_and_semantic_noop_preserve_revision(self):
        first = self.save(operation_id='first')
        self.assertTrue(self.save(operation_id='first')['replayed'])
        self.assertEqual(first['changed']['@prop:flag']['revision'], 1)
        self.save()
        self.assertEqual(self.read()['props'][0]['asset_revision'], 1)
        new = copy.deepcopy(self.doc)
        new['props'][0]['visual_description'] = '红布旗'
        self.save(new, operation_id='second')
        self.assertEqual(self.read()['props'][0]['asset_revision'], 2)
        self.save(operation_id='first')
        self.assertEqual(self.read()['props'][0]['visual_description'], '红布旗')

    def test_stale_generation_cannot_overwrite_manual_edit(self):
        self.save()
        base = self.read()
        repo.update_json(self.root/self.name, lambda d: d['props'][0].update(visual_description='人工确认红旗'))
        with self.assertRaises(project_store.RevisionConflict):
            self.save(bases={self.name: base})
        self.assertEqual(self.read()['props'][0]['visual_description'], '人工确认红旗')
        row = self.read()['props'][0]
        self.assertEqual(row['field_sources']['visual_description']['source'], 'manual')

    def test_legacy_array_can_be_edited_and_stale_array_baseline_is_rejected(self):
        path = self.root / self.name
        path.parent.mkdir(parents=True)
        old_rows = copy.deepcopy(self.doc['props'])
        path.write_text(json.dumps(old_rows, ensure_ascii=False), encoding='utf-8')

        repo.update_json(path, lambda d: d['props'][0].update(visual_description='人工红旗'))

        self.assertEqual(self.read()['props'][0]['visual_description'], '人工红旗')
        self.assertEqual(self.read()['props'][0]['asset_revision'], 2)
        with self.assertRaises(project_store.RevisionConflict):
            self.save(bases={self.name: old_rows})

    def test_service_create_preserves_existing_legacy_array_assets(self):
        import asset_service
        path = self.root / self.name
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(self.doc['props'], ensure_ascii=False), encoding='utf-8')

        created = asset_service.create_asset(self.root, kind='prop', id='bell', name='铜铃', prompt='小铜铃')

        self.assertEqual(created['ref'], '@prop:bell')
        self.assertEqual({row['id'] for row in self.read()['props']}, {'flag', 'bell'})

    def test_legacy_import_reuses_identity_and_cannot_replace_accepted_settings(self):
        self.save()
        doc = {'props': [{'id': 'other', 'name': '令旗', 'visual_description': '历史白旗', 'shot_hint': '特写'}]}
        result = self.save(doc, source='legacy')
        self.assertEqual(result['remap'], {'@prop:other': '@prop:flag'})
        self.assertEqual(len(self.read()['props']), 1)
        self.assertEqual(self.read()['props'][0]['visual_description'], '黑布旗')

    def test_failed_multi_document_write_and_crash_recovery(self):
        self.save()
        before = (self.root/self.name).read_bytes()
        write = repo._write
        calls = []
        def fail(path, content):
            calls.append(path)
            if len(calls) == 3:
                raise OSError('磁盘失败')
            return write(path, content)
        with patch.object(repo, '_write', side_effect=fail), self.assertRaises(OSError):
            repo.commit(self.root, {self.name: {'props': []}, '素材/场景.json': {'scenes': []}}, source='manual', allow_removal=True)
        self.assertEqual((self.root/self.name).read_bytes(), before)
        self.assertFalse((self.root/'素材/场景.json').exists())
        import base64
        write(self.root/repo.JOURNAL, repo._bytes({'before': {self.name: base64.b64encode(before).decode()}}))
        (self.root/self.name).write_text('{"props":[]}', encoding='utf-8')
        with repo.locks(self.root):
            self.assertEqual((self.root/self.name).read_bytes(), before)

    def test_same_operation_with_different_payload_is_rejected(self):
        self.save(operation_id='one')
        with self.assertRaises(ValueError):
            self.save({'props': []}, operation_id='one')

    def test_same_batch_aliases_are_one_identity(self):
        result = self.save({'props': [*self.doc['props'], {'id': 'another', 'name': '令旗', 'visual_description': '红旗'}]})
        self.assertEqual(len(self.read()['props']), 1)
        self.assertEqual(result['remap'], {'@prop:another': '@prop:flag'})

    def test_pending_identity_holds_plot_and_accepts_from_normal_review(self):
        import asset_visual_review as review
        self.save({'props': [{'id': 'flag', 'name': '猎仙旗', 'visual_description': '旧黑旗'}]})
        before = (self.root/self.name).read_bytes()
        candidate = {'props': [*self.read()['props'], {'id': 'new_flag', 'name': '玄黑猎仙旗', 'visual_description': '新红缨黑旗'}]}
        docs = {self.name: candidate, '剧本/分集.json': {'episodes': [{'id': 'E1', 'key_asset_refs': ['@prop:new_flag']}]}}
        result = repo.commit(self.root, docs, source='planning', operation_id='plan')
        self.assertFalse(result['ok'])
        self.assertEqual((self.root/self.name).read_bytes(), before)
        self.assertFalse((self.root/'剧本/分集.json').exists())
        self.assertEqual(len(repo.pending_changes(self.root)), 1)
        data = review.state(self.root)
        ticket = data['identity_changes'][0]['id']
        saved = review.save(self.root, [{'id': ticket, 'action': 'reuse', 'target': '@prop:flag'}], data['revision'])
        self.assertIn(ticket, saved['saved'])
        self.assertEqual(len(self.read()['props']), 1)
        self.assertEqual(self.read()['props'][0]['visual_description'], '新红缨黑旗')
        self.assertEqual(repo._read(self.root/'剧本/分集.json')['episodes'][0]['key_asset_refs'], ['@prop:flag'])
        self.assertFalse(repo.pending_changes(self.root))

    def test_pending_new_and_derived_decisions(self):
        import asset_visual_review as review
        for action in ('new', 'derived'):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                repo.commit(root, {self.name: {'props': [{'id': 'flag', 'name': '猎仙旗'}]}}, source='manual')
                repo.commit(root, {self.name: {'props': [{'id': 'burnt', 'name': '烧焦猎仙旗'}]}}, source='planning')
                data = review.state(root)
                ticket = data['identity_changes'][0]['id']
                review.save(root, [{'id': ticket, 'action': action, 'target': '@prop:flag', 'label': '烧焦', 'difference': '旗面边缘焦黑', 'episodes': ['E2']}], data['revision'])
                rows = repo._read(root/self.name)['props']
                self.assertEqual(len(rows), 2 if action == 'new' else 1)
                if action == 'derived':
                    self.assertEqual(rows[0]['states'][0]['look_diff'], '旗面边缘焦黑')
                    self.assertEqual(rows[0]['states'][0]['episodes'], ['E2'])

    def test_pending_uses_baseline_and_rejects_stale_adoption(self):
        import asset_visual_review as review
        self.save({'props': [{'id': 'flag', 'name': '猎仙旗'}]})
        candidate = {'props': [{'id': 'new_flag', 'name': '玄黑猎仙旗'}]}
        self.save(candidate)
        self.save(candidate)
        self.assertEqual(len(repo.pending_changes(self.root)), 1)
        data = review.state(self.root)
        repo.update_json(self.root/self.name, lambda d: d['props'][0].update(visual_description='人工新修改'))
        with self.assertRaises(project_store.RevisionConflict):
            review.save(self.root, [{'id': data['identity_changes'][0]['id'], 'action': 'new'}], data['revision'])
        self.assertEqual(self.read()['props'][0]['visual_description'], '人工新修改')
        fresh = review.state(self.root)
        review.save(self.root, [{'id': fresh['identity_changes'][0]['id'], 'action': 'new'}], fresh['revision'])
        self.assertEqual(self.read()['props'][0]['visual_description'], '人工新修改')
        self.assertEqual(len(self.read()['props']), 2)

    def test_locked_fields_and_retired_index(self):
        from asset_registry import AssetRegistry
        doc = {'props': [{'id': 'flag', 'name': '令旗', 'visual_description': '人工红旗', 'locked_fields': ['visual_description']}]}
        self.save(doc)
        doc['props'][0]['visual_description'] = '模型白旗'
        self.save(doc)
        self.assertEqual(self.read()['props'][0]['visual_description'], '人工红旗')
        (self.root/'素材/素材图.json').write_text(json.dumps({'道具': {'retired': {'id': 'retired', 'name': '旧旗', 'path': 'old.png'}}}), encoding='utf-8')
        self.assertNotIn('@prop:retired', [r['ref'] for r in AssetRegistry(self.root).list()])

    def test_existing_identity_review_moves_references_and_keeps_new_settings(self):
        import asset_visual_review as review
        self.save({'props': [{'id': 'flag', 'name': '猎仙旗', 'visual_description': '旧黑旗', 'states': [{'id': 'burnt', 'look_diff': '烧焦'}]},
                             {'id': 'new_flag', 'name': '玄黑猎仙旗', 'visual_description': '红缨黑旗', 'source': 'story_units', 'states': [{'id': 'wet', 'look_diff': '淋湿'}]}]}, source='manual')
        plot = self.root/'分镜/剧本_E1.json'
        plot.parent.mkdir()
        plot.write_text(json.dumps({'shots': [{'id': 'S1', 'asset_refs': ['@prop:flag'], 'prompt_video': '持@prop:flag'}]}), encoding='utf-8')
        index = self.root/'素材/素材图.json'
        index.write_text(json.dumps({'道具': {'flag': {'path': '素材/道具/flag.png', 'settings_revision': 1}}}), encoding='utf-8')
        data = review.state(self.root)
        item = next(r for r in data['identity_changes'] if r['record']['id'] == 'flag')
        review.save(self.root, [{'id': item['id'], 'action': 'reuse', 'target': '@prop:new_flag'}], data['revision'])
        self.assertEqual(len(self.read()['props']), 1)
        self.assertEqual(self.read()['props'][0]['visual_description'], '红缨黑旗')
        self.assertEqual({s['id'] for s in self.read()['props'][0]['states']}, {'burnt', 'wet'})
        self.assertEqual(repo._read(plot)['shots'][0]['asset_refs'], ['@prop:new_flag'])
        self.assertEqual(repo._read(index)['道具']['new_flag']['path'], '素材/道具/flag.png')
        self.assertFalse(review.state(self.root)['identity_changes'])

    def test_scene_state_projects_prompt_and_reference(self):
        import asset_registry
        import visual_asset_prompt
        import chatgpt_queue
        row = {'id': 'room', 'name': '石屋', 'visual_description': '石墙木门',
               'states': [{'id': 'night', 'label': '夜间', 'look_diff': '冷月光，门口灯笼点亮'}]}
        repo.commit(self.root, {'素材/场景.json': {'scenes': [row]}}, source='manual')
        picture = self.root/'素材/场景/room.png'
        picture.parent.mkdir()
        picture.write_bytes(b'image')
        projected = asset_registry.AssetRegistry(self.root).resolve('@scene:room')
        self.assertEqual(projected['states'][0]['id'], 'night')
        self.assertIn('灯笼点亮', visual_asset_prompt.subject_prompt('scene', row, row['states'][0], project=self.root))
        plan = chatgpt_queue.resolve_asset_execution_plan(str(self.root), '@scene:room', 'night')
        self.assertEqual(plan['target_path'], '素材/场景/room__night.png')
        self.assertEqual(plan['refs'][0]['path'], '素材/场景/room.png')

    def test_selected_changes_merge_disjoint_fields_and_reject_conflicting_edits(self):
        base = {'props': [{'id': 'a', 'name': '旗', 'visual_description': '黑旗'}]}
        a = copy.deepcopy(base); a['props'][0]['name'] = '令旗'
        b = copy.deepcopy(base); b['props'][0]['visual_description'] = '红旗'
        merged = repo.merge_changes(base, a, b)
        self.assertEqual(merged['props'][0], {'id': 'a', 'name': '令旗', 'visual_description': '红旗'})
        b['props'][0]['name'] = '军旗'
        with self.assertRaises(project_store.RevisionConflict):
            repo.merge_changes(base, a, b)
