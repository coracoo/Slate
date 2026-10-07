# -*- coding: utf-8 -*-
"""新旧素材合并、引用迁移与再次入库的回归验证。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1]/'tools'), str(Path(__file__).resolve().parents[2]/'previs_system/tools')]
import asset_duplicates
import asset_reconcile
import asset_registry
import script_repository
import story_units
import project_store


class DuplicateAssetsTests(unittest.TestCase):
    def test_bulk_duplicate_chain_merges_to_final_target_without_touching_disk(self):
        import asset_identity_review as identity
        rows=[{'id': 'a', 'name': '令旗', 'visual_description': '旧旗'},
              {'id': 'b', 'name': '令旗', 'visual_description': '中间旗'},
              {'id': 'c', 'name': '令旗', 'visual_description': '当前红旗'}]
        self.write('素材/道具.json', {'props': rows})
        proposals=[{'id':'existing-identity-prop:'+row['id'],'kind':'prop','record':row,
                    'matches':['@prop:'+target]} for row,target in ((rows[0],'b'),(rows[1],'c'))]
        decisions=[{'id':item['id'],'action':'reuse','target':item['matches'][0]} for item in proposals]
        with patch.object(identity,'existing_preview',return_value=proposals):
            docs=identity.prepare_existing(self.root, decisions)
        merged=docs[self.root/'素材/道具.json']['props']
        self.assertEqual([r['id'] for r in merged], ['c'])
        self.assertEqual(merged[0]['visual_description'], '当前红旗')
        self.assertEqual(set(merged[0]['merged_ids']), {'a','b'})
        self.assertEqual(len(json.loads((self.root/'素材/道具.json').read_text('utf-8'))['props']),3)
        proposals[1]['matches']=['@prop:a']
        decisions[1]['target']='@prop:a'
        with patch.object(identity,'existing_preview',return_value=proposals), self.assertRaisesRegex(ValueError,'循环'):
            identity.prepare_existing(self.root, decisions)
        self.assertEqual(len(json.loads((self.root/'素材/道具.json').read_text('utf-8'))['props']),3)

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.write('素材/人物.json', {'characters': []})
        self.write('素材/场景.json', {'scenes': []})
        self.old = {'id': 'old_flag', 'name': '指挥令旗', 'asset_required': True, 'visual_description': '旧白布旗', 'image_prompt': '旧组装词'}
        self.new = {'id': 'flag', 'name': '玄黑指挥令旗', 'asset_required': True, 'source': 'story_units', 'visual_description': '新黑布旗'}
        self.write('素材/道具.json', {'props': [self.old, self.new]})
        self.write('素材/素材图.json', {'道具': {'old_flag': {'path': '素材/道具/old_flag.png', 'prompt': '旧组装词'}}})
        self.write('剧本/分集.json', {'episodes': [{'id': 'E1', 'key_asset_refs': ['@prop:old_flag', '@prop:flag']}]})
        self.write('分镜/剧本_E1.json', {'shots': [{'prompt_video': '手持@prop:old_flag；勿变@prop:old_flag_red', 'asset_refs': ['@prop:old_flag']}]})
        self.write('剧本/.versions/old.json', {'refs': ['@prop:old_flag']})

    def write(self, name, data):
        path = self.root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')

    def read(self, name):
        return json.loads((self.root/name).read_text('utf-8'))

    def proposal(self):
        result = asset_reconcile.preview(self.root)
        selected = [i['id'] for i in result['items'] if i['id'].startswith('merge:')]
        self.assertEqual(len(selected), 1)
        return result, selected

    def test_review_merges_new_settings_and_old_image_and_current_references(self):
        before = (self.root/'素材/道具.json').read_bytes()
        result, selected = self.proposal()
        self.assertEqual((self.root/'素材/道具.json').read_bytes(), before)
        asset_reconcile.apply(self.root, selected, result['revision'])
        rows = self.read('素材/道具.json')['props']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['visual_description'], '新黑布旗')
        self.assertNotIn('image_prompt', rows[0])
        self.assertIn('old_flag', rows[0]['merged_ids'])
        self.assertEqual(self.read('剧本/分集.json')['episodes'][0]['key_asset_refs'], ['@prop:flag'])
        shot = self.read('分镜/剧本_E1.json')['shots'][0]
        self.assertEqual(shot['prompt_video'], '手持@prop:flag；勿变@prop:old_flag_red')
        images = self.read('素材/素材图.json')['道具']
        self.assertNotIn('old_flag', images)
        self.assertEqual(images['flag']['path'], '素材/道具/old_flag.png')
        self.assertEqual(self.read('剧本/.versions/old.json')['refs'], ['@prop:old_flag'])
        self.assertFalse(any(i['id'].startswith('merge:') for i in asset_reconcile.preview(self.root)['items']))
        roster = story_units.merge_roster(self.root, {'props': [{'id': 'old_flag', 'name': '指挥令旗'}]})
        self.assertEqual(roster['created'], [])
        self.assertEqual(roster['remap']['@prop:old-flag'], '@prop:flag')

    def test_downstream_change_invalidates_review(self):
        result, selected = self.proposal()
        self.write('分镜/剧本_E1.json', {'shots': []})
        with self.assertRaises(project_store.RevisionConflict):
            asset_reconcile.apply(self.root, selected, result['revision'])
        self.assertEqual(len(self.read('素材/道具.json')['props']), 2)

    def test_derived_images_remain_usable_after_character_merge(self):
        self.write('素材/人物.json', {'characters': [
            {'id': 'old', 'name': '守卫', 'states': [{'id': 'hurt', 'label': '负伤'}]},
            {'id': 'guard', 'name': '守卫', 'source': 'story_units'}]})
        path = self.root/'素材/人物/old__hurt.png'
        path.parent.mkdir()
        path.write_bytes(b'image')
        self.write('素材/素材图.json', {'人物': {'old': {'states': {'hurt': {'path': '素材/人物/old__hurt.png'}}}}})
        result = asset_reconcile.preview(self.root)
        selected = ['merge:character:old:guard']
        asset_reconcile.apply(self.root, selected, result['revision'])
        row = asset_registry.AssetRegistry(self.root).resolve('@character:old')
        self.assertEqual(row['ref'], '@character:guard')
        self.assertEqual(row['states'][0]['path'], '素材/人物/old__hurt.png')

    def test_new_image_wins_while_old_plan_index_is_unified(self):
        self.write('素材/素材图.json', {'道具': {
            'old_flag': {'path': 'old.png'}, 'flag': {'path': 'new.png'},
            'old_flag__plan': {'path': 'old-plan.png'}}})
        result, selected = self.proposal()
        asset_reconcile.apply(self.root, selected, result['revision'])
        index = self.read('素材/素材图.json')['道具']
        self.assertEqual(index['flag']['path'], 'new.png')
        self.assertEqual(index['flag__plan']['path'], 'old-plan.png')
        self.assertNotIn('old_flag__plan', index)

    def test_rollback_restores_archive_index_and_references(self):
        result, selected = self.proposal()
        before = {p: p.read_bytes() for p in self.root.rglob('*.json')}
        write = asset_reconcile.asset_repository._write
        count = 0
        def fail(path, content):
            nonlocal count
            count += 1
            if count == 3:
                raise OSError('写入中断')
            return write(path, content)
        with patch.object(asset_reconcile.asset_repository, '_write', side_effect=fail), self.assertRaises(OSError):
            asset_reconcile.apply(self.root, selected, result['revision'])
        self.assertTrue(all(p.read_bytes() == b for p, b in before.items()))

    def test_explicit_derived_asset_and_locked_conflict_are_not_merged(self):
        self.new['parent_ref'] = '@prop:old_flag'
        self.write('素材/道具.json', {'props': [self.old, self.new]})
        self.assertFalse(any(i['id'].startswith('merge:') for i in asset_reconcile.preview(self.root)['items']))
        self.new.pop('parent_ref')
        self.old['locked_fields'] = ['visual_description']
        self.write('素材/道具.json', {'props': [self.old, self.new]})
        result = asset_reconcile.preview(self.root)
        self.assertFalse(any(i['id'].startswith('merge:') for i in result['items']))
        self.assertTrue(any('人工锁定' in issue for issue in result['issues']))

    def test_ingestion_matches_prop_aliases_without_replacing_planning_settings(self):
        old = dict(self.new, aliases=['指挥令旗'], merged_ids=['old_flag'], shot_hint='旗面特写')
        self.old['shot_hint'] = '旧旗特写'
        result = script_repository.merge_assets({'props': [old]}, {'props': [self.old]})
        self.assertEqual(len(result['props']), 1)
        self.assertEqual(result['props'][0]['id'], 'flag')
        self.assertEqual(result['props'][0]['visual_description'], '新黑布旗')

    def test_raw_character_identifiers_are_migrated_but_paths_are_not(self):
        value = {'actors': {'old': {'name': '甲'}, 'new': {'name': '新版'}},
                 'shots': [{'lines': [{'speaker': 'old'}], 'staging': {'old': [1, 2]}}],
                 'path': '@character:old/file.png', 'id': 'old'}
        out = asset_duplicates.remap(value, {'@character:old': '@character:new'})
        self.assertEqual(out['actors'], {'new': {'name': '新版'}})
        self.assertEqual(out['shots'][0]['lines'][0]['speaker'], 'new')
        self.assertEqual(out['shots'][0]['staging'], {'new': [1, 2]})
        self.assertEqual(out['path'], value['path'])
        self.assertEqual(out['id'], 'old')
