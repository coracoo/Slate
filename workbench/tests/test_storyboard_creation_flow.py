# -*- coding: utf-8 -*-
"""离线验证分集正文、分镜三类提示词与制作请求的真实保存链。"""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import creation_pipeline as cp
import production_studio as studio
from production_jobs import compile_grid_request
from production_requests import compile_request
from production_prompts import source_hash, media_source_hash, require_prompts


class StoryboardCreationFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ('剧本', '分镜', '素材'):
            (self.root / name).mkdir()
        for target in ('socket.socket.connect', 'socket.socket.connect_ex', 'socket.create_connection'):
            blocker = patch(target, side_effect=AssertionError('离线验收禁止外部请求'))
            blocker.start(); self.addCleanup(blocker.stop)
        self.write('剧本/分集.json', {'mode': 'generated', 'episodes': [{'id': 'E1', 'text': '甲穿过走廊停在窗边，屋外传来雨声。'}]})
        self.write('剧本/brief.json', {'aspect_ratio': '9:16'})
        self.write('剧本/style.json', {'image': 'ghibli-soft', 'storyboard': 'auto',
                                      'storyboard_camera': 'auto', 'storyboard_keyframe': 'auto', 'storyboard_motion': 'auto'})
        self.write('素材/场景.json', {'scenes': [{'id': 'corridor', 'name': '走廊', 'interior': True}]})
        self.out = {'shots': [dict(id=f'S{i}', dur=3, scene_ref='@scene:corridor',
                         scene='room', cam='wide', shot_size='中景', angle='平视', rig='轨道', lens='50mm',
                         camera_move='移', content='长廊木窗', action=action, sound='窗外雨声', lighting='柔和天光',
                         prompt_image=f'静帧{i}：门口的一瞬', prompt_video=f'【S{i}镜（0—3s）：人物移动{i}】',
                         prompt_grid=f'3×3，格1至格9按{action}展开，固定中景', lines=[])
                         for i, action in enumerate(('穿过走廊', '停在窗边'), 1)],
                    'video_units': [{'shot_ids': ['S1', 'S2'], 'title': '走廊', 'prompt_video': '连续穿行后停下',
                                     'prompt_grid': '3×3，先穿行后停下，共9格，不嵌套', 'negative': '水印'}]}
        self.cfg = {'id': 'doubao-api', 'models': {'video': 'seedance-2.5', 'image': 'seedream-4'},
                    'base_url': 'https://invalid.test/api/v3'}
        with patch.object(cp, 'pick_vendor', return_value='fake'), patch.object(cp, 'VendorClient'), \
                patch.object(cp, 'chat_retry', return_value=json.dumps(self.out, ensure_ascii=False)) as chat, \
                patch('knowledge.query', return_value=[]):
            cp.cmd_storyboard(str(self.root), 'fake', 'E1')
            self.assertEqual(chat.call_count, 1)
            self.assertIn('prompt_grid', chat.call_args.args[1][0]['content'])
        self.name = '剧本_E1.json'

    def write(self, name, value):
        (self.root / name).write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def body(self, board, scope='V'):
        return {'board': self.name, 'scope': scope, 'target': board['video_units'][0]['id'] if scope == 'V' else 'S1',
                'type': 'video', 'video_options': {'mode': 'text', 'ratio': '9:16'}, 'plan_refs': False}

    def test_saved_storyboard_edit_duration_and_video_parameters_reach_submission(self):
        board, rev = studio.read_board(self.root, self.name)
        self.assertTrue(all(s['prompt_grid'] for s in board['shots']))
        self.assertTrue(board['video_units'][0]['prompt_grid'])
        board['shots'][0]['dur'] = 4
        studio.save_shots(self.root, self.name, board['shots'], rev)
        board, rev = studio.read_board(self.root, self.name)
        self.assertEqual(board['video_units'][0]['duration'], 7)
        unit = board['video_units'][0]
        unit.update(source_hash=source_hash(board['shots']), duration=10)
        studio.save_units(self.root, self.name, [unit], rev)
        loaded = studio.state(self.root, self.name)['board']
        self.assertEqual(loaded['video_units'][0]['duration'], 10)
        packet = compile_request(self.root, self.body(loaded), self.cfg)
        for fact in ('轨道', '50mm', '平视', '长廊木窗', '穿过走廊', '停在窗边', '窗外雨声', '柔和天光'):
            self.assertIn(fact, packet['prompt'])
        self.assertEqual(packet['duration'], packet['video_options']['duration'])
        self.assertEqual(packet['duration'], 10)
        self.assertEqual(packet['video_options']['ratio'], '9:16')
        self.assertNotIn('0—3s', packet['prompt'])
        self.assertNotIn('3×3', packet['prompt'])
        self.assertNotIn('静帧', packet['prompt'])
        self.assertEqual(packet['prompt'].count('画风：'), 1)
        self.assertEqual(studio.timeline(loaded, loaded['video_units'][0])[-1]['end'], 10)

    def test_grid_design_style_and_scope_flow_without_forced_camera_changes(self):
        board, _ = studio.read_board(self.root, self.name)
        with patch('prompt_assembler.resolve_shot_refs', return_value=[]), patch('asset_registry.AssetRegistry.resolve', side_effect=KeyError):
            body = self.body(board, 'S')
            packet = compile_grid_request(self.root, body, self.cfg)
            self.assertIn(self.out['shots'][0]['prompt_grid'], packet['prompt'])
            self.assertIn('统一画风：', packet['prompt'])
            self.assertNotIn('特写交替', packet['prompt'])
            self.assertEqual(packet['image_options']['ratio'], '9:16')
            packet = compile_grid_request(self.root, self.body(board), self.cfg)
            self.assertIn(self.out['video_units'][0]['prompt_grid'], packet['prompt'])

    def test_grid_dependency_is_separate_from_single_frame(self):
        board, _ = studio.read_board(self.root, self.name)
        shots, unit = board['shots'], board['video_units'][0]
        before_image = media_source_hash(shots, 'image', unit)
        before_grid = media_source_hash(shots, 'grid', unit)
        unit['prompt_grid'] = '2×2，四个明确瞬间'
        self.assertEqual(before_image, media_source_hash(shots, 'image', unit))
        self.assertNotEqual(before_grid, media_source_hash(shots, 'grid', unit))

    def test_structured_objects_cannot_masquerade_as_prompt_strings(self):
        for bad in ({'layout': '3×3'}, ['格1'], True, 9):
            with self.assertRaises(ValueError):
                require_prompts([{'id': 'S1', 'prompt_image': '静帧', 'prompt_video': '连续动作', 'prompt_grid': bad}])

    def test_regeneration_preserves_board_edited_during_model_call(self):
        def generated(*args, **kwargs):
            board, revision = studio.read_board(self.root, self.name)
            board['shots'][0]['prompt_image'] = '人工确认的窗边画面'
            studio.save_shots(self.root, self.name, board['shots'], revision)
            return json.dumps(self.out, ensure_ascii=False)
        with patch.object(cp, 'pick_vendor', return_value='fake'), patch.object(cp, 'VendorClient'), \
                patch.object(cp, 'chat_retry', side_effect=generated), patch('knowledge.query', return_value=[]), \
                self.assertRaises(cp.project_store.RevisionConflict):
            cp.cmd_storyboard(str(self.root), 'fake', 'E1')
        board, _ = studio.read_board(self.root, self.name)
        self.assertEqual(board['shots'][0]['prompt_image'], '人工确认的窗边画面')
        drafts = list((self.root / '剧本/.work/生成冲突').glob('*.json'))
        self.assertEqual(len(drafts), 1)
        self.assertEqual(json.loads(drafts[0].read_text('utf-8'))['data']['shots'][0]['prompt_image'], self.out['shots'][0]['prompt_image'])


if __name__ == '__main__':
    unittest.main()
