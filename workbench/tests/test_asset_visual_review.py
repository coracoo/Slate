# -*- coding: utf-8 -*-
"""集中视觉审核覆盖空缺、跨档案提交与版本保护。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import asset_visual_review as review


class VisualReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        self.docs = {'剧本/分集.json': {'units_version': 1, 'episodes': [{'id': 'E1', 'cast_refs': ['@character:a']}]},
            '素材/人物.json': {'characters': [{'id': 'a', 'name': '甲', 'appearance': None,
                'states': [{'id': 'injured', 'label': '受伤', 'look_diff': '手持弓随后倒下'}]}]},
            '素材/场景.json': {'scenes': [{'id': 'room', 'name': '旧屋'}]},
            '素材/道具.json': {'props': [{'id': 'p', 'name': '铜牌'}, {'id': 'q', 'name': '木牌'}]}}
        for name, doc in self.docs.items():
            path = self.project / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(doc, ensure_ascii=False), encoding='utf-8')

    def test_missing_records_and_scene_choices_are_visible_without_proposals(self):
        data = review.state(self.project)
        self.assertFalse(data['characters'][0]['visual_status']['ready'])
        self.assertEqual(len(data['other_assets']), 3)
        self.assertFalse(data['other_assets'][-1]['in_use'])
        self.assertEqual(data['scenes'], [{'ref': '@scene:room', 'name': '旧屋'}])

    def test_scene_state_confirmation_survives_reopening_review(self):
        path = self.project/'素材/场景.json'
        path.write_text(json.dumps({'scenes': [{'id': 'room', 'name': '旧屋',
            'visual_description': '木屋，灰色砖墙',
            'states': [{'id': 'night', 'look_diff': '灯笼亮起', 'episodes': ['E1']}]}]}), encoding='utf-8')
        result = review.save(self.project, [{'id': '@scene:room', 'confirm': {'base': True, 'states': ['night']}}],
                            review.current_revision(self.project))
        self.assertTrue(all(item['ready'] for item in result['confirmations']))
        row = next(item for item in review.state(self.project)['other_assets'] if item['id']=='@scene:room')
        self.assertEqual(row['visual_status']['review_status'], 'confirmed')
        self.assertEqual(row['states'][0]['visual_status']['review_status'], 'confirmed')

    def test_selected_character_and_prop_saved_together_and_rechecked(self):
        data = review.state(self.project)
        result = review.save(self.project, [
            {'id': 'a', 'patch': {'appearance': {'face': '窄脸', 'hair': '短发', 'body_type': '瘦高', 'outfit': '灰衣'},
                'states': [{'id': 'injured', 'look_diff': '手持弓', 'episodes': ['@scene:room']}]}},
            {'id': '@prop:p', 'patch': {'visual_description': '方形铜牌，暗褐色，边缘缺口'}}], data['revision'])
        self.assertTrue(result['validations']['a']['ready'])
        self.assertTrue(result['validations']['@prop:p']['ready'])
        props = json.loads((self.project/'素材/道具.json').read_text('utf-8'))['props']
        self.assertEqual(props[1], self.docs['素材/道具.json']['props'][1])
        self.assertIn('visual_description', props[0]['locked_fields'])
        with self.assertRaises(review.project_store.RevisionConflict):
            review.save(self.project, [{'id': '@prop:p', 'patch': {'visual_description': '旧版覆盖'}}], data['revision'])

    def test_invalid_batch_and_write_failure_do_not_partially_save(self):
        revision = review.state(self.project)['revision']
        path = self.project/'素材/道具.json'
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            review.save(self.project, [{'id': '@prop:p', 'patch': {'visual_description': '铜牌'}},
                {'id': '@prop:missing', 'patch': {'visual_description': '无'}}], revision)
        self.assertEqual(path.read_bytes(), before)

    def test_review_recovers_interrupted_asset_commit_before_revision_check(self):
        import base64
        data = review.state(self.project)
        path = self.project / '素材/道具.json'
        before = path.read_bytes()
        review.asset_repository._write(self.project / review.asset_repository.JOURNAL,
            review.asset_repository._bytes({'before': {'素材/道具.json': base64.b64encode(before).decode()}}))
        path.write_text('{"props":[]}', encoding='utf-8')

        review.save(self.project, [{'id': '@prop:p', 'patch': {'visual_description': '恢复后保存的铜牌'}}],
                    data['revision'])

        rows = json.loads(path.read_text('utf-8'))['props']
        self.assertEqual({row['id'] for row in rows}, {'p', 'q'})
        self.assertEqual(rows[0]['visual_description'], '恢复后保存的铜牌')
        self.assertFalse((self.project / review.asset_repository.JOURNAL).exists())

    def test_write_failure_rolls_back_all_selected_assets(self):
        revision = review.current_revision(self.project)
        path = self.project/'素材/道具.json'
        before = path.read_bytes()
        original = review.asset_repository._write
        calls = []
        def fail_second(target, content):
            calls.append(target)
            if len(calls) == 2:
                raise OSError('模拟第二份写入失败')
            return original(target, content)
        with patch.object(review.asset_repository, '_write', side_effect=fail_second), self.assertRaises(OSError):
            review.save(self.project, [{'id': '@prop:p', 'patch': {'visual_description': '铜牌'}},
                {'id': '@scene:room', 'patch': {'visual_description': '木屋'}}], revision)
        self.assertEqual(path.read_bytes(), before)

    def test_confirm_unchanged_base_and_selected_states_without_rewriting_others(self):
        path = self.project/'素材/人物.json'
        doc = json.loads(path.read_text('utf-8'))
        row = doc['characters'][0]
        row['appearance'] = {'face': '窄脸', 'hair': '短发', 'body_type': '瘦高', 'outfit': '灰衣'}
        row['states'] = [{'id': 'injured', 'look_diff': '左肩缠白布', 'episodes': ['E1']},
                         {'id': 'calm', 'look_diff': '决定反抗', 'output_asset_ref': '@character:a'}]
        path.write_text(json.dumps(doc, ensure_ascii=False), encoding='utf-8')
        request = [{'id': 'a', 'confirm': {'base': True, 'states': ['injured', 'calm']}}]
        result = review.save(self.project, request, review.current_revision(self.project))
        self.assertEqual(len(result['confirmations']), 3)
        self.assertTrue(all(r['ready'] for r in result['confirmations']))
        after = json.loads(path.read_text('utf-8'))['characters'][0]
        self.assertEqual(after['appearance'], row['appearance'])
        self.assertNotIn('output_asset_ref', after['states'][0])
        self.assertEqual(after['states'][1]['output_asset_ref'], '@character:a')
        self.assertTrue(after['visual_review']['source_hash'])
        self.assertTrue(after['states'][0]['visual_review']['source_hash'])
        from asset_registry import AssetRegistry
        current = AssetRegistry(self.project).resolve('@character:a')
        self.assertEqual(current['visual_status']['review_status'], 'confirmed')
        self.assertEqual(current['states'][0]['visual_status']['review_status'], 'confirmed')
        self.assertEqual(review.state(self.project)['characters'][0]['states'][1]['visual_status']['review_status'], 'confirmed')
        first = path.read_bytes()
        review.save(self.project, request, review.current_revision(self.project))
        self.assertEqual(path.read_bytes(), first)

        after['states'][0]['look_diff'] = '左肩缠红布'
        path.write_text(json.dumps({'characters': [after]}, ensure_ascii=False), encoding='utf-8')
        changed = AssetRegistry(self.project).resolve('@character:a')
        self.assertEqual(changed['visual_status']['review_status'], 'confirmed')
        self.assertEqual(changed['states'][0]['visual_status']['review_status'], 'changed')
        after['appearance']['face'] = '圆脸'
        path.write_text(json.dumps({'characters': [after]}, ensure_ascii=False), encoding='utf-8')
        changed = AssetRegistry(self.project).resolve('@character:a')
        self.assertEqual(changed['visual_status']['review_status'], 'changed')
        self.assertEqual(review.state(self.project)['characters'][0]['states'][1]['visual_status']['review_status'], 'changed')

    def test_confirm_only_selected_state_and_reject_missing_or_conflicting_settings(self):
        path = self.project/'素材/人物.json'
        doc = json.loads(path.read_text('utf-8'))
        doc['characters'][0]['appearance'] = {'face': '窄脸', 'hair': '短发', 'body_type': '瘦高', 'outfit': '灰衣'}
        path.write_text(json.dumps(doc), encoding='utf-8')
        result = review.save(self.project, [{'id': 'a', 'confirm': {'base': False, 'states': ['injured']}},
            {'id': '@prop:p', 'confirm': {'base': True, 'states': []}}], review.current_revision(self.project))
        self.assertFalse(any(r['ready'] for r in result['confirmations']))
        after = json.loads(path.read_text('utf-8'))['characters'][0]
        self.assertNotIn('visual_review', after)
        self.assertNotIn('visual_review', after['states'][0])
        with self.assertRaises(ValueError):
            review.save(self.project, [{'id': 'a', 'confirm': {'base': False, 'states': ['missing']}}], review.current_revision(self.project))


if __name__ == '__main__':
    unittest.main()
