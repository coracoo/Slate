# -*- coding: utf-8 -*-
"""素材入口的事务、保留身份和批量关系校验。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import asset_service
import project_store


class AssetServiceTransactionTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        (self.root / '素材').mkdir()
        self.path = self.root / '素材/人物.json'
        self.path.write_text(json.dumps({'characters': [{'id': 'hero', 'name': '主角',
            'states': [{'id': 'rain', 'label': '雨中', 'look_diff': '湿衣'}]}]}), encoding='utf-8')
        (self.root / '素材/场景.json').write_text('{"scenes":[]}', encoding='utf-8')
        (self.root / '素材/道具.json').write_text('{"props":[{"id":"bow","name":"弩"}]}', encoding='utf-8')

    def test_narrator_is_rejected_before_any_write(self):
        before = self.path.read_bytes()
        for name, ident in [('旁白', 'voiceover'), ('声音', 'narrator')]:
            with self.assertRaisesRegex(ValueError, '旁白'):
                asset_service.create_asset(self.root, kind='character', id=ident, name=name)
        self.assertEqual(self.path.read_bytes(), before)

    def test_relations_apply_source_and_keep_display_parent(self):
        result = asset_service.set_relations(self.root, [{'ref': '@prop:bow',
            'parent_ref': '@character:hero', 'derived_from': '@character:hero#rain'}])
        row = next(r for r in result['assets'] if r['ref'] == '@prop:bow')
        self.assertEqual(row['parent_ref'], '@character:hero')
        self.assertEqual(row['derived_from'], '@character:hero#rain')
        raw = json.loads((self.root / '素材/道具.json').read_text('utf-8'))['props'][0]
        self.assertIn('derived_from', raw['locked_fields'])

    def test_new_id_with_same_identity_never_rewrites_existing_asset(self):
        before = self.path.read_bytes()
        with self.assertRaisesRegex(asset_service.AssetConflict, '已登记'):
            asset_service.create_asset(self.root, kind='character', id='another', name='主角', prompt='新外观')
        self.assertEqual(self.path.read_bytes(), before)

    def test_cannot_reparent_under_existing_child(self):
        asset_service.set_relations(self.root, [{'ref': '@prop:bow', 'parent_ref': '@character:hero'}])
        with self.assertRaisesRegex(asset_service.AssetRelationError, '最多两层'):
            asset_service.create_asset(self.root, kind='prop', id='arrow', name='箭', parent_ref='@prop:bow')
        self.assertEqual(len(json.loads((self.root / '素材/道具.json').read_text('utf-8'))['props']), 1)

    def test_edit_cannot_convert_character_to_narrator(self):
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, '旁白'):
            asset_service.edit_asset(self.root, '@character:hero', {'name': '旁白'})
        self.assertEqual(self.path.read_bytes(), before)

    def test_explicit_detach_keeps_story_owner_without_reparenting(self):
        asset_service.create_asset(self.root, kind='prop', id='arrow', name='箭', parent_ref='@character:hero')
        saved = asset_service.set_relations(self.root, [{'ref': '@prop:arrow', 'parent_ref': None}])
        row = next(r for r in saved['assets'] if r['ref'] == '@prop:arrow')
        self.assertNotIn('parent_ref', row)
        raw = json.loads((self.root / '素材/道具.json').read_text('utf-8'))['props'][1]
        self.assertEqual(raw['owner'], '@character:hero')
        self.assertIsNone(raw['parent_ref'])
        asset_service.edit_asset(self.root, '@prop:arrow', {'visual_description': '铁箭头'})
        self.assertNotIn('parent_ref', asset_service.AssetRegistry(self.root).resolve('@prop:arrow'))

    def test_manual_display_parent_is_independent_from_prop_generation_source(self):
        saved = asset_service.create_asset(self.root, kind='prop', id='arrow', name='箭',
            parent_ref='@character:hero', derived_from='@prop:bow')
        self.assertEqual(saved['parent_ref'], '@character:hero')
        self.assertEqual(saved['derived_from'], '@prop:bow')

    def test_prop_can_display_under_scene_with_independent_generation_source(self):
        asset_service.create_asset(self.root, kind='scene', id='room', name='房间', prompt='房间')
        saved = asset_service.create_asset(self.root, kind='prop', id='arrow', name='箭',
            parent_ref='@scene:room', derived_from='@character:hero#rain')
        self.assertEqual(saved['parent_ref'], '@scene:room')
        self.assertEqual(saved['derived_from'], '@character:hero#rain')

    def test_changing_source_does_not_reparent_legacy_asset(self):
        path = self.root / '素材/道具.json'
        path.write_text(json.dumps({'props': [{'id': 'bow', 'name': '弩', 'parent_ref': '@character:hero'},
            {'id': 'base', 'name': '弩部件'}]}), encoding='utf-8')
        saved = asset_service.edit_asset(self.root, '@prop:bow', {'derived_from': '@prop:base'})['asset']
        self.assertEqual(saved['parent_ref'], '@character:hero')
        self.assertEqual(saved['derived_from'], '@prop:base')

    def test_invalid_second_item_never_saves_first_item(self):
        path = self.root / '素材/道具.json'
        before = path.read_bytes()
        for invalid in [None, {'ref': '@prop:unknown'}, {'ref': '@prop:bow', 'parent_ref': '@scene:unknown'}]:
            with self.assertRaises((ValueError, FileNotFoundError)):
                asset_service.set_relations(self.root, [
                    {'ref': '@prop:bow', 'parent_ref': '@character:hero'}, invalid])
            self.assertEqual(path.read_bytes(), before)

    def test_stale_document_revision_rejects_whole_batch(self):
        old = project_store.current_revision(self.path)
        self.path.write_text('{"characters":[{"id":"hero","name":"新称呼"}]}', encoding='utf-8')
        with self.assertRaises(project_store.RevisionConflict):
            asset_service.set_relations(self.root, [{'ref': '@prop:bow', 'parent_ref': '@character:hero'}],
                                        expected_revisions={'character': old})
        self.assertNotIn('parent_ref', json.loads((self.root / '素材/道具.json').read_text('utf-8'))['props'][0])


if __name__ == '__main__':
    unittest.main()
