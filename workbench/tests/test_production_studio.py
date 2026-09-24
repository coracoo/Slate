# -*- coding: utf-8 -*-
"""三类提示词及 S/V 编排的回归边界。"""
import sys
import unittest
import copy
import json
import tempfile
from unittest import mock
from unittest.mock import Mock, patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import production_studio as studio


class ProductionContractTests(unittest.TestCase):
    def board(self):
        return {'shots': [dict(id=f'S{i}', dur=3, scene_ref=scene,
                              prompt_image=f'静帧{i}', prompt_video=f'运动{i}', prompt_grid=f'宫格{i}')
                          for i, scene in enumerate(['@scene:a', '@scene:a', '@scene:b', '@scene:a'], 1)]}

    def test_group_only_adjacent_same_actual_scene(self):
        b = self.board()
        units = studio.default_units(b)
        self.assertEqual([u['shot_ids'] for u in units], [['S1', 'S2'], ['S3'], ['S4']])
        b['shots'][0].pop('scene_ref')
        b['shots'][1].pop('scene_ref')
        self.assertEqual(len(studio.default_units(b)), 4)

    def test_coverage_order_rejects_loss_duplicate_and_reordering(self):
        b = self.board()
        for groups in [[['S1'], ['S3', 'S4']], [['S1', 'S2'], ['S2', 'S3', 'S4']], [['S2', 'S1'], ['S3'], ['S4']]]:
            with self.assertRaises(ValueError):
                studio.validate_units(b, [{'id': str(i), 'shot_ids': ids} for i, ids in enumerate(groups)])

    def test_duration_scales_beats_without_changing_shots(self):
        b = self.board()
        u = studio.default_units(b)[0]
        u['duration'] = 10
        beats = studio.timeline(b, u)
        self.assertEqual([(v['start'], v['end']) for v in beats], [(0, 5), (5, 10)])
        self.assertEqual(b['shots'][0]['dur'], 3)

    def test_default_units_caps_by_duration(self):
        # N84：同场景连续 + 累积时长 ≤ 上限；放不下自动切下一个 V
        b = {'shots': [dict(id=f'S{i}', dur=4, scene_ref='@scene:a', prompt_image='a', prompt_video='v') for i in range(1, 7)]}
        units = studio.default_units(b, cap=15)
        self.assertEqual([u['shot_ids'] for u in units], [['S1', 'S2', 'S3'], ['S4', 'S5', 'S6']])
        self.assertTrue(all(u['duration'] == 12 for u in units))

    def test_validate_units_rejects_over_cap(self):
        b = {'shots': [dict(id=f'S{i}', dur=6, scene_ref='@scene:a') for i in range(1, 4)]}
        units = studio.default_units(b, cap=30)   # 18s 单组（30 上限下合法）
        with self.assertRaisesRegex(ValueError, '上限'):
            studio.validate_units(b, units)       # 15s 默认上限下非法

    def test_clean_units_auto_splits_inheriting_prompt(self):
        # 超上限 V 保存时自动拆：首段保 id，两段继承汇总提示词（成员变→source_hash 自然 stale 待重写）
        b = {'shots': [dict(id=f'S{i}', dur=4, scene_ref='@scene:a', prompt_image='a', prompt_video='v') for i in range(1, 7)]}
        big = [{'id': 'v-1', 'title': 'a', 'shot_ids': [f'S{i}' for i in range(1, 7)], 'scene_ref': '@scene:a',
                'prompt_video': '整段汇总', 'duration': 24}]
        clean = studio.clean_units(b, big)
        self.assertEqual([len(u['shot_ids']) for u in clean], [3, 3])
        self.assertEqual(clean[0]['id'], 'v-1')
        self.assertTrue(all(u['prompt_video'] == '整段汇总' for u in clean))
        self.assertTrue(all(u['duration'] == 12 for u in clean))

    def test_timeline_locks_dialogue_at_speech_floor(self):
        # N84：对白镜头锁定 max(叙事, 语速下限 4字/s)；纯视觉镜头分摊剩余伸缩量
        b = {'shots': [dict(id='S1', dur=2, scene_ref='@scene:a', lines=[{'speaker': 'c', 'line': 'x' * 20}]),
                       dict(id='S2', dur=8, scene_ref='@scene:a')]}
        beats = studio.timeline(b, {'shot_ids': ['S1', 'S2'], 'duration': 10})
        self.assertEqual(beats[0]['end'] - beats[0]['start'], 5.0)
        self.assertAlmostEqual(beats[1]['end'] - beats[1]['start'], 5.0)

    def test_judge_flags_dialogue_overflow_and_cap(self):
        b = {'shots': [dict(id='S1', dur=1, scene_ref='@scene:a', lines=[{'speaker': 'c', 'line': 'x' * 20}])]}
        verdict = studio.judge_unit(b, {'shot_ids': ['S1'], 'duration': 2}, cap=15)
        self.assertFalse(verdict['ok'])
        self.assertTrue(any('自然语速' in w for w in verdict['warnings']))
        verdict2 = studio.judge_unit(b, {'shot_ids': ['S1'], 'duration': 20}, cap=15)
        self.assertTrue(any('上限' in w for w in verdict2['warnings']))

    def test_settings_roundtrip_and_choices(self):
        import tempfile
        from pathlib import Path as _P
        old = studio.SETTINGS_PATH
        with tempfile.TemporaryDirectory() as td:
            studio.SETTINGS_PATH = _P(td) / 's.json'
            try:
                self.assertEqual(studio.load_settings()['default_video_duration'], 15)
                studio.save_settings({'default_video_duration': 8})
                self.assertEqual(studio.duration_cap(), 8)
                with self.assertRaises(ValueError):
                    studio.save_settings({'default_video_duration': 12})
            finally:
                studio.SETTINGS_PATH = old

    def test_master_spec_no_downscale_and_orientation(self):
        # 母版规格推导：不降档、取向按多数、帧率取最大、奇数取偶（N82）
        from production_media import resolve_master_spec
        spec = resolve_master_spec([
            {'width': 1920, 'height': 1080, 'avg_frame_rate': '24000/1001'},
            {'width': 1280, 'height': 720, 'avg_frame_rate': '30/1'},
            {'width': 1280, 'height': 720, 'avg_frame_rate': '25/1'},
        ])
        self.assertEqual((spec['w'], spec['h']), (1920, 1080))
        self.assertEqual(spec['fps'], 30.0)
        spec = resolve_master_spec([
            {'width': 720, 'height': 1280, 'avg_frame_rate': '30/1'},
            {'width': 1080, 'height': 1920, 'avg_frame_rate': '30/1'},
            {'width': 1920, 'height': 1080, 'avg_frame_rate': '30/1'},
        ])
        self.assertEqual((spec['w'], spec['h']), (1080, 1920))
        spec = resolve_master_spec([{'width': 1081, 'height': 1921, 'avg_frame_rate': '30/1'}])
        self.assertEqual((spec['w'], spec['h']), (1080, 1920))
        with self.assertRaises(ValueError):
            resolve_master_spec([{'width': 0, 'height': 0}])

    def test_prompt_families_not_copied(self):
        from production_prompts import normalize_prompts, require_prompts
        shot = {'id': 'S1', 'prompt': '旧静帧', 'action': '起身'}
        normalize_prompts(shot)
        self.assertEqual(shot['prompt_image'], '旧静帧')
        self.assertNotIn('prompt_video', shot)
        self.assertNotIn('prompt_grid', shot)
        with self.assertRaises(ValueError): require_prompts([shot])
        # 宫格是按需人工字段：两类齐全即通过，不因缺宫格被拒
        shot['prompt_video'] = '起身连续动作'
        require_prompts([shot])

    def test_old_shot_editor_keeps_new_fields_and_bindings(self):
        b = self.board()
        b['shots'][0]['keyframe'] = {'item_id': 'old'}
        merged = studio.merge_shots(b, [{'id': 'S1', 'action': '新动作', 'dur': 4}])
        self.assertEqual(merged[0]['prompt_video'], '运动1')
        self.assertEqual(merged[0]['keyframe']['item_id'], 'old')
        self.assertEqual(merged[0]['action'], '新动作')


if __name__ == '__main__': unittest.main()


class ProductionExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / '分镜').mkdir()
        (self.root / '素材').mkdir()
        (self.root / '创作').mkdir()
        from PIL import Image
        from production_media import digest
        from production_prompts import media_source_hash
        self.board = {'shots': [dict(id=f'S{i}', dur=3, scene_ref='@scene:a', prompt_image=f'静帧{i}',
                                    prompt_video=f'动态{i}', prompt_grid=f'格序{i}') for i in range(1, 3)]}
        for s in self.board['shots']:
            f = self.root / '素材' / f"{s['id']}.png"; Image.new('RGB', (64, 36), 'red').save(f)
            s['keyframe'] = {'path': f.relative_to(self.root).as_posix(), 'sha256': digest(f), 'source_hash': media_source_hash([s], 'image'), 'item_id': 'old-' + s['id']}
        self.board['video_units'] = studio.default_units(self.board)
        self.board['video_units'][0].update(prompt_video='场景连续动作', prompt_grid='两个独立镜头从左到右')
        self.path = self.root / '分镜/剧本_E1.json'
        self.path.write_text(json.dumps(self.board), encoding='utf-8')
        self.cfg = {'id': 'doubao-api', 'enabled': True, 'models': {'video': 'seedance-2.5', 'image': 'seedream-4'}, 'base_url': 'https://ark.cn-beijing.volces.com/api/v3'}
        self.providers = self.root / 'providers-test.json'
        self.providers.write_text(json.dumps({'vendors': [self.cfg]}), encoding='utf-8')
        self.body = {'project': self.root.name, 'action': 'generate', 'scope': 'V', 'target': self.board['video_units'][0]['id'],
                     'type': 'video', 'board': self.path.name, 'vendor_id': 'doubao-api', 'nonce': 'request-id-unique-123', 'duration': 6}

    def tearDown(self): self.temp.cleanup()

    def test_request_keeps_all_keyframes_and_video_only_prompt(self):
        from production_requests import compile_request
        req = compile_request(self.root, self.body, self.cfg)
        self.assertEqual([r['shot_id'] for r in req['refs']], ['S1', 'S2'])
        self.assertIn('0–3', req['prompt']); self.assertIn('3–6', req['prompt'])
        self.assertNotIn('静帧1', req['prompt']); self.assertNotIn('格序1', req['prompt'])
        self.assertEqual(req['duration'], 6)

    def test_missing_stale_frame_or_unsupported_duration_stops_before_spawn(self):
        from production_jobs import enqueue
        spawn = Mock()
        self.body['duration'] = 31
        with self.assertRaises(ValueError): enqueue(self.root, self.body, spawn, self.providers)
        self.assertFalse(spawn.called)
        self.body['duration'] = 6
        self.board['shots'][0]['keyframe']['sha256'] = 'changed'
        self.path.write_text(json.dumps(self.board), encoding='utf-8')
        with self.assertRaises(ValueError): enqueue(self.root, self.body, spawn, self.providers)
        self.assertFalse(spawn.called)

    def test_duplicate_nonce_only_spawns_once_and_snapshot_has_real_pixels(self):
        from production_jobs import enqueue
        from production_media import digest
        spawn = Mock(return_value=100)
        first = enqueue(self.root, self.body, spawn, self.providers)
        second = enqueue(self.root, self.body, spawn, self.providers)
        self.assertEqual(first['item_id'], second['item_id']); self.assertEqual(spawn.call_count, 1)
        req = json.loads((self.root / '创作' / first['item_id'] / 'request.json').read_text(encoding='utf-8'))
        self.assertTrue(req['refs'][0]['path'].startswith('创作/'))
        (self.root / '素材/S1.png').write_bytes(b'changed')
        self.assertEqual(digest(self.root / req['refs'][0]['path']), req['refs'][0]['sha256'])
        with self.assertRaises(ValueError): enqueue(self.root, {**self.body, 'duration': 9}, spawn, self.providers)

    def test_worker_forwards_model_duration_and_adopts_matching_output(self):
        from production_jobs import enqueue, execute
        first = enqueue(self.root, self.body, Mock(return_value=100), self.providers)
        req = json.loads((self.root / '创作' / first['item_id'] / 'request.json').read_text(encoding='utf-8'))
        client = Mock(id='doubao-api', cfg=self.cfg)
        client.last_request = None
        def generate(*a, **kw): Path(kw['out_path']).write_bytes(b'fake-video')
        client.generate_video.side_effect = generate
        with patch('llm_openai.VendorClient', return_value=client), patch('production_jobs.probe', return_value={'format': {'duration': '6.08'}}):
            execute(req, self.providers)
        kwargs = client.generate_video.call_args.kwargs
        self.assertEqual(kwargs['extra']['duration'], 6)
        self.assertEqual(kwargs['model'], 'seedance-2.5')
        result = json.loads((self.root / '创作/creation.json').read_text(encoding='utf-8'))['items'][0]
        self.assertEqual(result['status'], 'done')
        self.assertIsInstance(result['outputs'][0], str)
        saved = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(saved['video_units'][0]['video_binding']['item_id'], first['item_id'])
        with self.assertRaises(ValueError): execute(req, self.providers)
        self.assertEqual(client.generate_video.call_count, 1)

    def test_worker_image_request_passes_structured_ratio(self):
        # S 图请求必须像旧 create_media 入口一样显式传 size/ratio——
        # 云端图像模型对提示词里的中文「画幅 16:9」服从度低，缺 extra 就按默认画幅出图。
        from production_jobs import enqueue, execute
        from PIL import Image
        body = {**self.body, 'scope': 'S', 'target': 'S1', 'type': 'image'}
        first = enqueue(self.root, body, Mock(return_value=100), self.providers)
        req = json.loads((self.root / '创作' / first['item_id'] / 'request.json').read_text(encoding='utf-8'))
        client = Mock(id='doubao-api', cfg=self.cfg)
        client.last_request = None
        def generate(*a, **kw): Image.new('RGB', (64, 36), 'blue').save(kw['out_path'])
        client.generate_image.side_effect = generate
        with patch('llm_openai.VendorClient', return_value=client):
            execute(req, self.providers)
        kwargs = client.generate_image.call_args.kwargs
        self.assertEqual(kwargs['extra'], {'size': '2k', 'ratio': '16:9'})
        result = json.loads((self.root / '创作/creation.json').read_text(encoding='utf-8'))['items'][0]
        self.assertEqual(result['status'], 'done')

    def test_s_selection_can_generate_video_and_does_not_change_v_duration(self):
        from production_requests import compile_request
        request = compile_request(self.root, {**self.body, 'scope': 'S', 'target': 'S1', 'duration': 4}, self.cfg)
        self.assertEqual(request['duration'], 4)
        self.assertEqual(len(request['refs']), 1)
        self.assertEqual(json.loads(self.path.read_text())['video_units'][0]['duration'], 6)

    def test_concurrent_edit_rejects_old_revision(self):
        _, rev = studio.read_board(self.root, self.path.name)
        studio.save_shots(self.root, self.path.name, [{**self.board['shots'][0], 'prompt_image': '修改'}], rev)
        with self.assertRaises(studio.project_store.RevisionConflict):
            studio.save_units(self.root, self.path.name, self.board['video_units'], rev)

    def test_llm_prompts_refresh_keeps_prompt_grid(self):
        # 宫格文案不被 LLM 刷新覆盖/清空——它是确定性排版的元数据，按需人工配置
        from production_jobs import llm_task
        _, rev = studio.read_board(self.root, self.path.name)
        client = Mock()
        client.chat.return_value = json.dumps({'shots': [{**s, 'prompt_image': '模型静帧', 'prompt_video': '模型动态'} for s in self.board['shots']]})
        llm_task(self.root, {'action': 'prompts', 'snapshot': self.board, 'board': self.path.name, 'board_revision': rev}, client)
        refreshed = studio.read_board(self.root, self.path.name)[0]['shots'][0]
        self.assertEqual(refreshed['prompt_image'], '模型静帧')
        self.assertEqual(refreshed['prompt_grid'], '格序1')

    def test_adopted_performance_changes_submitted_prompt(self):
        # 验收标准：只改已采用表演 → 实际提交 prompt 必变；镜头事实（时长/时间轴/其余正文）不被演员层改动
        from production_requests import compile_request
        from production_prompts import media_source_hash
        body = {**self.body, 'scope': 'V', 'target': self.board['video_units'][0]['id']}
        r1 = compile_request(self.root, body, self.cfg)
        self.assertNotIn('表演指导', r1['prompt'])
        perf = {'status': 'ready', 'source_hash': '',
                'packet': {'actors': [{'actor_id': 'c', 'beats': [{'at': 0, 'duration': 2, 'intent': '施压', 'posture': '前倾按案'}]}]}}
        self.board['shots'][0]['performance'] = copy.deepcopy(perf)
        self.path.write_text(json.dumps(self.board), encoding='utf-8')
        r2 = compile_request(self.root, body, self.cfg)
        self.assertIn('表演指导', r2['prompt']); self.assertIn('施压', r2['prompt'])
        def strip(p): return p.split('\n表演指导')[0]
        self.assertEqual(strip(r1['prompt']), strip(r2['prompt']))   # 表演段之外逐字不变
        perf['packet']['actors'][0]['beats'][0]['intent'] = '隐忍'
        self.board['shots'][0]['performance'] = copy.deepcopy(perf)
        self.path.write_text(json.dumps(self.board), encoding='utf-8')
        r3 = compile_request(self.root, body, self.cfg)
        self.assertNotEqual(r2['prompt'], r3['prompt']); self.assertIn('隐忍', r3['prompt'])
        # 过期表演（source_hash 不匹配）静默不注入
        self.board['shots'][0]['performance'] = {**copy.deepcopy(perf), 'source_hash': 'stale'}
        self.path.write_text(json.dumps(self.board), encoding='utf-8')
        r4 = compile_request(self.root, body, self.cfg)
        self.assertNotIn('表演指导', r4['prompt'])
        # S 图请求注入末拍冻结瞬间：两个节拍只取最后一个
        img_perf = {'status': 'ready', 'source_hash': '',
                    'packet': {'actors': [{'actor_id': 'c', 'beats': [
                        {'at': 0, 'duration': 1, 'intent': '起势', 'posture': '后仰'},
                        {'at': 2, 'duration': 1, 'intent': '落定', 'gaze': '直锁对方'}]}]}}
        self.board['shots'][0]['performance'] = img_perf
        self.path.write_text(json.dumps(self.board), encoding='utf-8')
        r5 = compile_request(self.root, {**body, 'scope': 'S', 'target': 'S1', 'type': 'image'}, self.cfg)
        self.assertIn('落定', r5['prompt']); self.assertNotIn('起势', r5['prompt'])
        # 采用表演 → video 指纹联动（提示重生成）、image 关键帧指纹不受影响
        unit = self.board['video_units'][0]
        with_perf = media_source_hash(self.board['shots'], 'video', unit)
        no_perf = media_source_hash([{**x, 'performance': None} for x in self.board['shots']], 'video', unit)
        self.assertNotEqual(with_perf, no_perf)
        self.assertEqual(media_source_hash(self.board['shots'][:1], 'image', None),
                         media_source_hash([{**self.board['shots'][0], 'performance': None}], 'image', None))

    def test_authored_s_and_v_survive_llm_refresh(self):
        from production_jobs import llm_task
        self.board['shots'][0]['prompt_video_source'] = 'llm'
        patch_row = {**self.board['shots'][0], 'prompt_video': '人工动态'}
        self.board['shots'] = studio.merge_shots(self.board, [patch_row, self.board['shots'][1]])
        self.assertEqual(self.board['shots'][0]['prompt_video_source'], 'authored')
        edited = copy.deepcopy(self.board['video_units']); edited[0]['prompt_video'] = '人工总视频'
        self.board['video_units'] = studio.clean_units(self.board, edited)
        self.path.write_text(json.dumps(self.board), encoding='utf-8')
        _, rev = studio.read_board(self.root, self.path.name)
        client = Mock(); client.chat.return_value = json.dumps({'video_units': [{**edited[0], 'prompt_video': '模型覆盖'}]})
        llm_task(self.root, {'action': 'group', 'snapshot': self.board, 'board': self.path.name, 'board_revision': rev}, client)
        self.assertEqual(studio.read_board(self.root, self.path.name)[0]['video_units'][0]['prompt_video'], '人工总视频')
        snapshot, rev = studio.read_board(self.root, self.path.name)
        client.chat.return_value = json.dumps({'shots': [{**s, 'prompt_video': '模型动作'} for s in snapshot['shots']]})
        llm_task(self.root, {'action': 'prompts', 'snapshot': snapshot, 'board': self.path.name, 'board_revision': rev}, client)
        self.assertEqual(studio.read_board(self.root, self.path.name)[0]['shots'][0]['prompt_video'], '人工动态')

    def test_changed_v_duration_or_frame_invalidates_video_but_not_keyframe(self):
        from production_prompts import media_source_hash
        u = self.board['video_units'][0]
        old = media_source_hash(self.board['shots'], 'video', u)
        u['duration'] = 12
        self.assertNotEqual(old, media_source_hash(self.board['shots'], 'video', u))
        u['duration'] = 6
        self.board['shots'][0]['keyframe']['sha256'] = 'new-frame'
        self.assertNotEqual(old, media_source_hash(self.board['shots'], 'video', u))
        s = self.board['shots'][0]; old_frame = media_source_hash([s], 'image')
        s['prompt_video'] = '新动作写法'; s['prompt_grid'] = '新格序'
        self.assertEqual(old_frame, media_source_hash([s], 'image'))

    def test_changed_provider_stops_before_paid_submission(self):
        from production_jobs import enqueue, execute
        first = enqueue(self.root, self.body, Mock(return_value=100), self.providers)
        req = json.loads((self.root / '创作' / first['item_id'] / 'request.json').read_text(encoding='utf-8'))
        changed = copy.deepcopy(self.cfg); changed['models']['video'] = 'seedance-other'
        client = Mock(id='doubao-api', cfg=changed, last_request=None)
        with patch('llm_openai.VendorClient', return_value=client), self.assertRaisesRegex(ValueError, '配置发生变化'):
            execute(req, self.providers)
        client.generate_video.assert_not_called()

    def test_voice_files_are_frozen_at_enqueue(self):
        from production_jobs import enqueue
        from production_media import digest
        source = self.root / '素材/voice.mp3'; source.write_bytes(b'voice-A')
        voice = {'character_id': 'hero', 'sample': '素材/voice.mp3', 'sha256': digest(source), 'revision': 1}
        with patch('voice_assets.video_voices', return_value=[voice]):
            result = enqueue(self.root, {**self.body, 'include_voices': True}, Mock(return_value=100), self.providers)
        packet = json.loads((self.root / '创作' / result['item_id'] / 'request.json').read_text(encoding='utf-8'))
        source.write_bytes(b'voice-B')
        self.assertEqual((self.root / packet['voices'][0]['sample']).read_bytes(), b'voice-A')

    def test_image_request_freezes_auto_selected_edit_model(self):
        from production_requests import compile_request
        cfg = {**self.cfg, 'models': {'image': 'text-image', 'image_edit': 'edit-image'}}
        with patch('prompt_assembler.resolve_shot_refs', return_value=[{'path': '素材/S1.png', 'purpose': '身份'}]):
            packet = compile_request(self.root, {**self.body, 'scope': 'S', 'target': 'S1', 'type': 'image'}, cfg)
        self.assertEqual(packet['model'], 'edit-image'); self.assertEqual(packet['image_mode'], 'edit')


class LocalMediaTests(unittest.TestCase):
    def test_tail_first_frame_and_audio_have_formal_roles(self):
        from llm_openai import VendorClient, VendorError
        cfg = {'id': 'doubao-api', 'enabled': True, 'base_url': 'https://ark.cn-beijing.volces.com/api/v3',
               'models': {'video': 'seedance-2.5'}}
        client = VendorClient.from_config(cfg)
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / 'voice.mp3'; audio.write_bytes(b'audio')
            with patch.object(client, '_post', return_value={'id': 'task1'}) as post, patch.object(client, '_poll_ark_task', return_value='https://example.test/out.mp4'):
                with self.assertRaises(VendorError):
                    client.generate_video('连续动作', image_refs=['https://example.test/tail.png', 'https://example.test/kf.png'], first_frame='https://example.test/tail.png', extra={'duration':12,'audio_refs':[str(audio)]})
                post.assert_not_called()
                client.generate_video('连续动作', image_refs=['https://example.test/tail.png', 'https://example.test/kf.png'], extra={'duration':12,'audio_refs':[str(audio)]})
            payload = post.call_args.args[1]
            self.assertEqual([x.get('role') for x in payload['content'][1:]], ['reference_image', 'reference_image', 'reference_audio'])
            self.assertEqual(payload['duration'], 12)
            self.assertNotIn('audio_refs', payload)

    def test_comfy_image_branch_does_not_read_video_first_frame(self):
        from llm_openai import VendorClient
        cfg = {'id': 'local-comfyui', 'enabled': True, 'base_url': 'http://127.0.0.1:8188', 'models': {'image': 'z-image'}}
        with patch('comfyui_client.ComfyUIClient') as adapter:
            adapter.return_value.generate_image.return_value = 'result.png'
            self.assertEqual(VendorClient.from_config(cfg).generate_image('画面', 'result.png'), 'result.png')

    def test_real_tail_frame_and_grid_preserve_source_pixels(self):
        from production_media import binary, run, tail_frame, digest, make_grid
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root / 'short.mp4'
            run([binary('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i', 'color=c=red:s=128x72:d=0.5:r=12',
                 '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-y', str(source)])
            meta = tail_frame(root, {'path': source.name, 'sha256': digest(source), 'item_id': 'previous'})
            with Image.open(root / meta['path']) as im:
                pixel = im.convert('RGB').getpixel((30, 30))
                self.assertGreater(pixel[0], 240); self.assertLess(pixel[1], 10)
            ref = {'path': meta['path'], 'sha256': meta['sha256']}
            grid = make_grid(root, [ref, ref], '左右两格')
            with Image.open(root / grid['path']) as im: self.assertEqual(im.size, (1280, 360))
            from production_media import concatenate
            info = concatenate(root, [{'path': source.name, 'sha256': digest(source)}] * 2, root / 'episode.mp4')
            self.assertTrue(any(s['codec_type'] == 'audio' for s in info['streams']))
            self.assertAlmostEqual(float(info['format']['duration']), 1, delta=.2)


class VoiceBindingTests(unittest.TestCase):
    def test_binding_survives_reextraction_and_resolves_only_speaking_roles(self):
        import voice_assets
        from production_media import digest
        from script_repository import _merge_item
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / '素材/音色').mkdir(parents=True)
            sample = root / '素材/音色/sample.mp3'; sample.write_bytes(b'audio-sample')
            row = {'id': 'v1', 'revision': 1, 'name': '角色声', 'voice_id': 'cloud-id', 'sample': '素材/音色/sample.mp3', 'sha256': digest(sample)}
            (root / '素材/音色/音色库.json').write_text(json.dumps({'voices': [row]}), encoding='utf-8')
            (root / '素材/人物.json').write_text(json.dumps({'characters': [{'id': 'hero', 'name': '主角'}]}), encoding='utf-8')
            voice_assets.bind(root, {'character_id': 'hero', 'voice_asset_id': 'v1', 'revision': 1})
            old = voice_assets.characters(root)[0]
            merged = _merge_item(old, {'voice_binding': {'voice_asset_id': 'bad'}, 'voice': '温柔'}, 'E2')
            self.assertEqual(merged['voice_binding']['voice_asset_id'], 'v1')
            self.assertEqual(voice_assets.video_voices(root, [{'actor_refs': ['@character:hero'], 'lines': []}]), [])
            got = voice_assets.video_voices(root, [{'lines': [{'speaker': 'hero'}, {'speaker': 'narrator'}]}])
            self.assertEqual([v['character_id'] for v in got], ['hero'])


class StoryboardGenerationChainTests(unittest.TestCase):
    """分镜生成链（cmd_storyboard）：LLM 超上限分组落盘前按时长自动拆，不再整单打回。"""

    def test_over_cap_llm_grouping_auto_splits_and_writes(self):
        import creation_pipeline as cp
        shots = [{"id": f"S{i}", "dur": 4, "shot_size": "中景", "camera_move": "固定",
                  "angle": "平视", "cam": "wide", "scene": "room", "action": "甲走动",
                  "prompt_image": f"静帧{i}", "prompt_video": f"运动{i}", "lines": []}
                 for i in range(1, 7)]
        llm_out = {"shots": shots,
                   "video_units": [{"shot_ids": [f"S{i}" for i in range(1, 7)],
                                    "title": "整段", "prompt_video": "整段汇总视频描述"}]}
        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            (project / "剧本").mkdir()
            (project / "剧本" / "分集.json").write_text(json.dumps(
                {"episodes": [{"id": "E1", "text": "甲在车间走动。"}], "rev": 1},
                ensure_ascii=False), encoding="utf-8")
            (project / "素材").mkdir()
            (project / "素材" / "场景.json").write_text(json.dumps(
                {"scenes": [{"id": "cj", "name": "车间"}]}, ensure_ascii=False), encoding="utf-8")
            with mock.patch.object(cp, "pick_vendor", lambda v=None: "fake"), \
                 mock.patch.object(cp, "VendorClient", lambda vid: object()), \
                 mock.patch.object(cp, "chat_retry",
                                   lambda *a, **k: json.dumps(llm_out, ensure_ascii=False)):
                cp.cmd_storyboard(str(project), None, "E1")
            cfg = json.loads((project / "分镜" / "剧本_E1.json").read_text(encoding="utf-8"))
            units = cfg["video_units"]
            self.assertEqual([u["shot_ids"] for u in units], [["S1", "S2", "S3"], ["S4", "S5", "S6"]])
            self.assertTrue(all(u["duration"] == 12 for u in units))
            self.assertTrue(all(u["prompt_video"] == "整段汇总视频描述" for u in units))
