# -*- coding: utf-8 -*-
"""视觉素材展示及队列直接投影当前设定，不要求重新提炼。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import asset_registry
import character_profiles
import chatgpt_queue
from visual_asset_prompt import subject_prompt


class VisualAssetProjectionTests(unittest.TestCase):
    def test_postprocessing_warning_does_not_invalidate_confirmed_settings(self):
        from visual_asset_prompt import visual_contract
        from visual_confirmation import review_hash
        row = {'id': 'a', 'name': '甲', 'sheet_prompt': '蓝衣'}
        initial = visual_contract('character', row)
        row['visual_review'] = {'source_hash': review_hash(initial)}
        status = visual_contract('character', row, has_image=True, image={
            'visual_source_hash': initial['source_hash'],
            'head_mask': {'status': 'needs_review', 'reason': '五视图布局不明确'}})
        self.assertTrue(status['ready'])
        self.assertEqual(status['review_status'], 'confirmed')
        self.assertEqual(status['image_status'], 'current')
        self.assertIn('头部遮盖未完成', status['postprocess_warning'])
        self.assertIn('五视图布局不明确', status['postprocess_warning'])

    def test_placeholder_descriptions_do_not_count_as_visual_settings(self):
        from visual_asset_prompt import missing_visual_fields
        for value in ('   ', '未知', '待定'):
            self.assertEqual(['visual_description'], missing_visual_fields('prop', {'description': value}))
        self.assertEqual([], missing_visual_fields('scene', {'geometry': '窄长石室，一窗一门'}))
        self.assertEqual([], missing_visual_fields('scene', {'geometry': ['石墙环绕', '北面一窗']}))
        self.assertEqual(['visual_description'], missing_visual_fields('scene', {'geometry': ['未知', ' ']}))

    def test_queued_generation_waits_for_confirmation_then_uses_same_facial_details(self):
        from visual_asset_prompt import visual_contract
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / '素材'
            root.mkdir()
            row = {'id':'a','name':'甲','sheet_prompt':'历史模板脸',
                'appearance':{'proposals':{'face':'左眉高、深眼窝、鼻尖宽、方下颌'}}}
            path = root / '人物.json'
            path.write_text(json.dumps({'characters':[row]}), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '确认外观'):
                chatgpt_queue.queue_assets(folder, ['@character:a'])
            self.assertFalse((Path(folder) / '创作' / 'creation.json').exists())
            result = character_profiles.save(folder, 'a', {'appearance':{
                'face':'左眉高、深眼窝、鼻尖宽、方下颌', 'sources':{'face':'authored'}}},
                expected_revision=character_profiles.state(folder)['revision'])
            self.assertTrue(result['visual_validation']['ready'])
            row = asset_registry.AssetRegistry(folder).resolve('@character:a')
            queue = chatgpt_queue.queue_assets(folder, ['@character:a'])[0]
            self.assertIn('左眉高、深眼窝、鼻尖宽、方下颌', queue['prompt_assembled'])
            self.assertNotIn('历史模板脸', queue['prompt_assembled'])
            self.assertEqual(queue['visual_source_hash'], row['visual_status']['source_hash'])

    def test_state_validation_reports_wrong_matching_key_and_multiple_action_phases(self):
        from visual_asset_prompt import visual_contract
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / '剧本').mkdir()
            (root / '素材').mkdir()
            (root / '剧本' / '分集.json').write_text('{"episodes":[{"id":"E1"}]}', encoding='utf-8')
            (root / '素材' / '场景.json').write_text('{"scenes":[{"id":"room","name":"石屋"}]}', encoding='utf-8')
            status = visual_contract('character', {'id':'a'},
                {'look_diff':'手持长刀；后被撞翻脱手', 'episodes':['不存在的旧场景']}, project=root)
            self.assertFalse(status['ready'])
            self.assertTrue(any('前后动作' in s for s in status['warnings']))
            self.assertTrue(any('匹配键' in s for s in status['warnings']))

    def test_rules_alone_cannot_pass_visual_validation_and_new_description_is_inherited(self):
        from visual_asset_prompt import visual_contract
        import story_units
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / '剧本').mkdir()
            (root / '素材').mkdir()
            (root / '剧本' / '大纲.json').write_text('{"units_version":1}', encoding='utf-8')
            path = root / '素材' / '道具.json'
            row = {'id':'flag','name':'旗','usage_boundary':'无法伤人','image_prompt':'旧金色长旗'}
            path.write_text(json.dumps({'props':[row]}), encoding='utf-8')
            self.assertFalse(visual_contract('prop', row, project=root)['ready'])
            self.assertIn('visual_description', story_units.pending_settings(root)['targets'][0]['missing'])
            story_units.apply_entities(root, {'props':[{'ref':'@prop:flag','visual_description':'玄黑短旗，麻布材质'}]}, fill_only=True)
            updated = json.loads(path.read_text(encoding='utf-8'))['props'][0]
            contract = visual_contract('prop', updated, project=root)
            self.assertTrue(contract['ready'])
            self.assertIn('玄黑短旗', contract['subject'])
            self.assertNotIn('旧金色长旗', contract['subject'])

    def test_unadopted_proposal_does_not_change_real_visual_fingerprint(self):
        from visual_asset_prompt import visual_contract
        row = {'appearance':{'face':'窄脸','proposals':{'face':'圆脸'}}}
        before = visual_contract('character', row)['source_hash']
        row['appearance']['proposals']['face'] = '方脸'
        self.assertEqual(visual_contract('character', row)['source_hash'], before)

    def test_unconfirmed_design_does_not_fall_back_to_old_extraction(self):
        from visual_asset_prompt import visual_contract
        record = {'id': 'hero', 'name': '甲', 'sheet_prompt': '旧红衣圆脸',
                  'identity_anchor': '旧长刀成年战士',
                  'appearance': {'proposals': {'face': '窄脸深眼窝', 'outfit': '灰衣'}}}
        contract = visual_contract('character', record)
        self.assertFalse(contract['ready'])
        self.assertIn('face', contract['pending_fields'])
        self.assertNotIn('旧红衣', contract['subject'])
        self.assertNotIn('旧长刀', contract['subject'])
        self.assertNotIn('窄脸深眼窝', contract['subject'])

    def test_planned_scene_and_prop_ignore_unconfirmed_legacy_prompt(self):
        from visual_asset_prompt import visual_contract
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / '剧本'
            root.mkdir()
            (root / '大纲.json').write_text('{"units_version": 1}', encoding='utf-8')
            for kind, record, expected in (
                ('scene', {'id': 'room', 'description': '窄窗石墙', 'image_prompt': '宽阔木屋'}, '窄窗石墙'),
                ('prop', {'id': 'sword', 'appearance': '黑铁短刀', 'image_prompt': '金色长剑'}, '黑铁短刀')):
                contract = visual_contract(kind, record, project=folder)
                self.assertIn(expected, contract['subject'])
                self.assertNotIn(record['image_prompt'], contract['subject'])
                self.assertEqual(contract['editable_prompt'], '')
                record['locked_fields'] = ['image_prompt']
                self.assertIn(record['image_prompt'], visual_contract(kind, record, project=folder)['subject'])

    def test_current_archive_hash_detects_old_image_and_state_without_rewriting_index(self):
        from visual_asset_prompt import visual_contract
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / '素材'
            root.mkdir()
            character = {'id': 'a', 'appearance': {'face': '旧脸型'},
                         'states': [{'id': 'injured', 'look_diff': '左臂包扎'}]}
            old_hash = visual_contract('character', character)['source_hash']
            character['appearance']['face'] = '新脸型'
            (root / '人物.json').write_text(json.dumps({'characters': [character]}), encoding='utf-8')
            (root / '人物').mkdir()
            for name in ('a.png', 'a__injured.png'):
                (root / '人物' / name).write_bytes(b'image')
            index = {'人物': {'a': {'path': '素材/人物/a.png', 'visual_source_hash': old_hash,
                'states': {'injured': {'visual_source_hash': 'old-state'}}}}}
            path = root / '素材图.json'
            path.write_text(json.dumps(index), encoding='utf-8')
            row = asset_registry.AssetRegistry(folder).resolve('@character:a')
            self.assertTrue(row['visual_status']['image_stale'])
            self.assertTrue(row['states'][0]['visual_status']['image_stale'])
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')), index)

    def test_planned_asset_check_never_calls_extraction_or_changes_archive(self):
        from unittest.mock import patch
        import creation_pipeline
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / '剧本').mkdir()
            (root / '素材').mkdir()
            (root / '剧本' / '大纲.json').write_text('{"units_version":1}', encoding='utf-8')
            (root / '剧本' / '分集.json').write_text('{"episodes":[{"id":"E1","text":"正文"}]}', encoding='utf-8')
            path = root / '素材' / '人物.json'
            path.write_text('{"characters":[{"id":"a","sheet_prompt":"历史描绘"}]}', encoding='utf-8')
            before = path.read_bytes()
            with patch.object(creation_pipeline, 'VendorClient', side_effect=AssertionError('不能重提炼')):
                creation_pipeline.cmd_extract(folder, None, '')
            self.assertEqual(path.read_bytes(), before)

    def test_state_merge_preserves_confirmed_target_and_mother_reuse(self):
        from script_repository import _merge_states
        old = [{'id': 'battle', 'look_diff': '持弩', 'output_asset_ref': '@character:hero',
                'episodes': ['E2'], 'locked_fields': ['look_diff', 'output_asset_ref']}]
        result = _merge_states(old, [{'id': 'battle', 'look_diff': '持弩后脱手',
                                     'output_asset_ref': '', 'episodes': ['E3']}])[0]
        self.assertEqual(result['look_diff'], '持弩')
        self.assertEqual(result['output_asset_ref'], '@character:hero')
        self.assertEqual(result['episodes'], ['E2', 'E3'])
        self.assertEqual(result['locked_fields'], old[0]['locked_fields'])
        with self.assertRaisesRegex(ValueError, '复用角色母图'):
            subject_prompt('character', {'id': 'hero'}, result)

    def test_state_keeps_full_target_and_does_not_reinject_parent_equipment(self):
        record = {'identity_anchor': '成年战士，手持旧剑',
                  'appearance': {'face': '左眉略高', 'outfit': '旧甲',
                                 'distinctive_features': '早期持弩，后期持旧剑'}}
        prompt = subject_prompt('character', record, {
            'look_diff': '手持三枚箭矢的连弩',
            'sheet_prompt': '正面、侧面、背面三视图，纯白背景；深蓝劲装，银色护腕，手持连弩。'})
        self.assertIn('三枚箭矢', prompt)
        self.assertIn('深蓝劲装', prompt)
        self.assertIn('银色护腕', prompt)
        self.assertIn('左眉略高', prompt)
        self.assertNotIn('旧剑', prompt)
        self.assertNotIn('旧甲', prompt)
        self.assertNotIn('三视图', prompt)
        self.assertLess(prompt.index('三枚箭矢'), prompt.index('左眉略高'))

    def test_state_label_is_not_a_visual_edit_instruction(self):
        with self.assertRaisesRegex(ValueError, '可见变化'):
            subject_prompt('character', {}, {'label': '决战'})

    def test_legacy_and_explicit_supplement_are_preserved_without_promoting_proposals(self):
        self.assertIn('历史描绘', subject_prompt('character', {'sheet_prompt': '历史描绘'}))
        record = {'appearance': {'face': '新脸型', 'proposals': {'hair': '未采用银发'}},
                  'sheet_prompt': '人工配饰', 'locked_fields': ['sheet_prompt']}
        prompt = subject_prompt('character', record)
        self.assertIn('新脸型', prompt)
        self.assertIn('人工配饰', prompt)
        self.assertNotIn('未采用银发', prompt)

    def test_old_image_index_cannot_restore_removed_prompt(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / '素材'
            root.mkdir()
            (root / '人物.json').write_text(json.dumps({'characters': [{'id': 'a', 'appearance': {'face': '新脸型'}}]}), encoding='utf-8')
            (root / '素材图.json').write_text(json.dumps({'人物': {'a': {'prompt': '旧图红衣'}}}), encoding='utf-8')
            row = asset_registry.AssetRegistry(folder).resolve('@character:a')
            self.assertIn('新脸型', row['prompt'])
            self.assertNotIn('旧图红衣', row['prompt_editable'])

    def test_saved_character_updates_registry_and_queue_without_extraction(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            path = project / '素材' / '人物.json'
            path.parent.mkdir()
            path.write_text(json.dumps({'characters': [{'id': 'a', 'name': '甲',
                'sheet_prompt': '历史红衣长发', 'appearance': {'outfit': '蓝衣', 'hair': '短发'},
                'states': [{'id': 'injured', 'look_diff': '左臂包扎'}]}]}), encoding='utf-8')
            rev = character_profiles.state(project)['revision']
            character_profiles.save(project, 'a', {'appearance': {'outfit': '新灰衣'}}, expected_revision=rev)
            row = asset_registry.AssetRegistry(project).resolve('@character:a')
            queued, _ = chatgpt_queue._asset_source(project, row)
            for prompt in (row['prompt'], queued):
                self.assertIn('新灰衣', prompt)
                self.assertIn('短发', prompt)
                self.assertNotIn('历史红衣长发', prompt)
            state_prompt, _ = chatgpt_queue._asset_source(project, row, 'injured')
            self.assertIn('左臂包扎', state_prompt)
            self.assertNotIn('历史红衣长发', state_prompt)
            self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['characters'][0]['sheet_prompt'], '历史红衣长发')

    def test_current_scene_settings_are_visible_without_image_prompt(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            path = project / '素材' / '场景.json'
            path.parent.mkdir()
            path.write_text(json.dumps({'scenes': [{'id': 'room', 'name': '石屋',
                'description': '窄窗石墙', 'spatial_limit': '只有东侧一扇门', 'action_slots': ['窗边']}]}), encoding='utf-8')
            row = asset_registry.AssetRegistry(project).resolve('@scene:room')
            prompt, _ = chatgpt_queue._asset_source(project, row)
            self.assertIn('窄窗石墙', prompt)
            self.assertIn('只有东侧一扇门', prompt)
            self.assertEqual(row['prompt'], prompt)
