# -*- coding: utf-8 -*-
"""历史素材复用与派生匹配的边界。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1]/'tools'), str(Path(__file__).resolve().parents[2]/'previs_system/tools')]
import asset_matching as matching
import asset_reconcile as reconcile
import project_store


class AssetMatchingTests(unittest.TestCase):
    def test_scene_identity_and_time_are_preserved(self):
        scenes = [{'id': 'trench', 'name': '边境·前沿壕沟', 'aliases': ['前沿战壕']}]
        match = matching.scene_key('边境前沿壕沟／夜', scenes)
        self.assertEqual(match['key'], '@scene:trench／夜')
        self.assertTrue(matching.state_matches_shot([match['key']], 'E3', {'scene_ref': '@scene:trench', 'lighting': '夜间月光'}, scenes, ['E3']))
        self.assertFalse(matching.state_matches_shot([match['key']], 'E3', {'scene_ref': '@scene:trench', 'lighting': '白天日光'}, scenes, ['E3']))
        self.assertTrue(matching.state_matches_shot(['E3'], 'E3', {}, [], []))
        self.assertIsNone(matching.scene_key('前沿', scenes))
        self.assertIsNone(matching.scene_key('前沿战壕', scenes + [{'id': 'other', 'name': '前沿战壕'}]))

    def test_legacy_string_scene_alias_matches_state_without_splitting_characters(self):
        scenes = [{'id': 'hall', 'name': '议事厅', 'aliases': '大厅'}]
        self.assertEqual(matching.scene_key('大厅／夜', scenes)['key'], '@scene:hall／夜')
        self.assertTrue(matching.state_matches_shot(['大厅／夜'], 'E1',
            {'scene_ref': '@scene:hall', 'lighting': '夜间月光'}, scenes, ['E1']))
        self.assertIsNone(matching.scene_key('大', scenes))

    def test_effect_asset_survives_merge_and_enters_storyboard_context(self):
        import script_repository
        import prompt_modules
        effect = {'id': 'gold_light', 'name': '金色光点', 'kind': '显现/特效', 'asset_required': True,
                  'actions': ['光点在云层汇聚'], 'shot_hint': '光点特写', 'image_prompt': '云层中汇聚的金色光点'}
        merged = script_repository.merge_assets({'props': []}, {'props': [effect]}, 'E1')
        self.assertEqual([row['id'] for row in merged['props']], ['gold_light'])
        _, context = prompt_modules.storyboard_prompt('云层中出现汇聚的金色光点。', [], [], merged['props'])
        self.assertIn('@prop:gold_light', context)

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for folder in ('剧本', '素材'):
            (self.root/folder).mkdir()
        self.write('剧本/分集.json', {'episodes': [{'id': 'E1', 'key_asset_refs': ['@prop:flag'], 'cast_refs': ['@character:hero']}]})
        self.write('素材/道具.json', {'props': [{'id': 'flag', 'name': '令旗'}, {'id': 'unused', 'name': '旧道具'}]})
        self.write('素材/人物.json', {'characters': [{'id': 'hero', 'name': '主角', 'states': [{'id': 's1', 'label': '夜行', 'episodes': ['前沿战壕／夜']}]}]})
        self.write('素材/场景.json', {'scenes': [{'id': 'trench', 'name': '前沿战壕'}]})
        self.write('素材/素材图.json', {'道具': {'flag': {'name': '令旗', 'prompt': '三角青布旗，竹制旗杆\n风格：旧画风'}}})

    def write(self, name, value):
        (self.root/name).write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def test_preview_is_readonly_and_apply_only_reviewed_items(self):
        path = self.root/'素材/道具.json'
        before = path.read_bytes()
        result = reconcile.preview(self.root)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(set(result['_changes']), {'@character:hero', '@prop:flag'})
        self.assertEqual(result['_changes']['@prop:flag'][3]['visual_description'], '三角青布旗，竹制旗杆')
        reconcile.apply(self.root, ['@prop:flag'], result['revision'])
        props = json.loads(path.read_text('utf-8'))['props']
        self.assertEqual(props[0]['visual_description'], '三角青布旗，竹制旗杆')
        self.assertNotIn('visual_description', props[1])
        self.assertEqual(json.loads((self.root/'素材/人物.json').read_text('utf-8'))['characters'][0]['states'][0]['episodes'], ['前沿战壕／夜'])
        with self.assertRaises(project_store.RevisionConflict):
            reconcile.apply(self.root, ['@character:hero'], result['revision'])

    def test_existing_visual_and_locked_field_are_not_overwritten(self):
        self.write('素材/道具.json', {'props': [{'id': 'flag', 'name': '令旗', 'visual_description': '红旗'}]})
        self.assertNotIn('@prop:flag', reconcile.preview(self.root)['_changes'])

    def test_image_reuse_links_index_without_pinning_archive_to_old_image(self):
        (self.root/'素材/old.png').write_bytes(b'old-image')
        self.write('素材/素材图.json', {'道具': {'old-id': {'name': '令旗', 'path': '素材/old.png'}}})
        result = reconcile.preview(self.root)
        reconcile.apply(self.root, ['@prop:flag'], result['revision'])
        archive = json.loads((self.root/'素材/道具.json').read_text('utf-8'))['props'][0]
        self.assertNotIn('path', archive)
        index = json.loads((self.root/'素材/素材图.json').read_text('utf-8'))
        self.assertEqual(index['道具']['flag']['path'], '素材/old.png')
        self.assertEqual(index['道具']['flag']['reused_from'], 'old-id')
        self.assertNotIn('@prop:flag', reconcile.preview(self.root)['_changes'])
        self.write('素材/道具.json', {'props': [{'id': 'flag', 'name': '令旗', 'locked_fields': ['visual_description']}]})
        self.assertNotIn('@prop:flag', reconcile.preview(self.root)['_changes'])

    def test_write_failure_rolls_back_selected_documents(self):
        result = reconcile.preview(self.root)
        before = {p: p.read_bytes() for p in (self.root/'素材').glob('*.json')}
        write = reconcile.asset_repository._write
        calls = []
        def fail_second(path, data):
            calls.append(path)
            if len(calls) == 2:
                raise OSError('模拟磁盘写入失败')
            return write(path, data)
        with patch.object(reconcile.asset_repository, '_write', side_effect=fail_second), self.assertRaises(OSError):
            reconcile.apply(self.root, ['@character:hero', '@prop:flag'], result['revision'])
        self.assertTrue(all(p.read_bytes() == data for p, data in before.items()))
