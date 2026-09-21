# -*- coding: utf-8 -*-
"""创作台编辑、定向优化与派生音色的持久化边界。"""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import production_studio as studio
import production_jobs as jobs
import voice_assets as voices


class EditingTests(unittest.TestCase):
    def test_duration_change_recalculates_only_affected_unit(self):
        old = [{'id': 'S1', 'dur': 2}, {'id': 'S2', 'dur': 3}, {'id': 'S3', 'dur': 4}]
        board = {'shots': copy.deepcopy(old), 'video_units': [{'shot_ids': ['S1', 'S2'], 'duration': 20}, {'shot_ids': ['S3'], 'duration': 10}]}
        studio.sync_shot_durations(board, old)
        self.assertEqual(board['video_units'][0]['duration'], 20)
        board['shots'][0]['dur'] = 4.5
        studio.sync_shot_durations(board, old)
        self.assertEqual([u['duration'] for u in board['video_units']], [7.5, 10])

    def test_optimize_uses_current_text_and_writes_only_selected_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / '分镜').mkdir()
            board = {'shots': [{'id': 'S1', 'dur': 2, 'prompt_image': '静帧不变', 'prompt_video': '用户新加：小猫跳入竹篮', 'prompt_grid': '宫格不变'}]}
            (root / '分镜/E1.json').write_text(json.dumps(board), encoding='utf-8')
            snapshot, revision = studio.read_board(root, 'E1.json')
            client = Mock(); client.chat.return_value = '{"text":"小猫跃入竹篮，竹篮轻晃"}'
            packet = {'action': 'optimize', 'scope': 'S', 'target': 'S1', 'field': 'prompt_video', 'current_text': board['shots'][0]['prompt_video'], 'snapshot': snapshot, 'board': 'E1.json', 'board_revision': revision}
            jobs.llm_task(root, packet, client)
            user = json.loads(client.chat.call_args.args[0][1]['content'])
            self.assertEqual(user['current_text'], '用户新加：小猫跳入竹篮')
            result, _ = studio.read_board(root, 'E1.json')
            self.assertEqual(result['shots'][0]['prompt_image'], '静帧不变')
            self.assertEqual(result['shots'][0]['prompt_grid'], '宫格不变')
            self.assertTrue(result['shots'][0]['prompt_video'].startswith('【S1镜（0.0—2.0s）：'))
            with self.assertRaises(Exception): jobs.llm_task(root, packet, client)

    def test_named_voice_variant_preserves_project_default_and_reextraction(self):
        from script_repository import _merge_item
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / '素材/音色').mkdir(parents=True)
            (root / '素材/音色/sample.mp3').write_bytes(b'sample')
            (root / '素材/人物.json').write_text(json.dumps({'characters': [{'id': 'hero', 'name': '主角'}]}), encoding='utf-8')
            row = {'id': 'voice1', 'revision': 1, 'sample': '素材/音色/sample.mp3', 'voice_id': 'remote'}
            (root / '素材/音色/音色库.json').write_text(json.dumps({'voices': [row]}), encoding='utf-8')
            request = {'character_id': 'hero', 'voice_asset_id': 'voice1', 'revision': 1}
            voices.bind(root, request)
            with self.assertRaises(ValueError): voices.bind(root, {**request, 'mode': 'variant', 'name': '角色音乐·'})
            voices.bind(root, {**request, 'mode': 'variant', 'name': '角色音乐·幼年'})
            actor = voices.characters(root)[0]
            self.assertEqual(actor['voice_binding']['voice_asset_id'], 'voice1')
            variant = voices.resolve(root, 'hero', actor['voice_variants'][0]['voice_asset_id'])
            self.assertEqual(variant['name'], '角色音乐·幼年')
            merged = _merge_item(actor, {'voice_variants': []}, 'E2')
            self.assertEqual(merged['voice_variants'], actor['voice_variants'])


if __name__ == '__main__': unittest.main()
