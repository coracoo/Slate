# -*- coding: utf-8 -*-
"""视频局部修补（重做片段）：时间窗口校验、锚点抽取、三段拼回与制作链路注册。

真实 ffmpeg 的用例跟随 test_production_studio.py 的 LocalMediaTests 口径
（lavfi 合成小视频 fixture）；厂商调用一律 Mock，不触网。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'previs_system' / 'tools'))

import production_studio as studio
from production_media import binary, digest, run
from production_prompts import media_source_hash
from redo_segment import extract_anchor_frames, splice_back, validate_window, video_duration


class WindowValidationTests(unittest.TestCase):
    """时间参数校验：t0≥t1 / 越界 / 负值 / 非数值一律报错；容差内贴边夹取。"""

    def test_reject_inverted_negative_and_overflow(self):
        with self.assertRaisesRegex(ValueError, '起点必须小于终点'):
            validate_window(15, 8, 6)
        with self.assertRaisesRegex(ValueError, '起点必须小于终点'):
            validate_window(15, 6, 6)
        with self.assertRaisesRegex(ValueError, '不能为负'):
            validate_window(15, -1, 6)
        with self.assertRaisesRegex(ValueError, '超出原片时长'):
            validate_window(15, 6, 16)

    def test_reject_non_finite_and_non_numeric(self):
        for bad in (float('nan'), float('inf'), 'abc', None, True):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_window(15, bad, 6)

    def test_tolerance_clamps_to_bounds(self):
        self.assertEqual(validate_window(15, -0.01, 15.02), (0.0, 15.0))
        self.assertEqual(validate_window(15, 6.0, 8.0), (6.0, 8.0))
        with self.assertRaisesRegex(ValueError, '不能为负'):
            validate_window(15, -0.5, 6)

    def test_invalid_duration_rejected(self):
        for bad in (0, -3, float('nan')):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_window(bad, 1, 2)


def make_video(path, color, seconds, *, size='160x90', fps=12, tone=False):
    """lavfi 合成小视频 fixture（纯色，可带音轨）。"""
    cmd = [binary('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i', f'color=c={color}:s={size}:d={seconds}:r={fps}']
    if tone: cmd += ['-f', 'lavfi', '-i', f'sine=frequency=440:duration={seconds}']
    cmd += ['-c:v', 'libx264', '-pix_fmt', 'yuv420p']
    if tone: cmd += ['-c:a', 'aac', '-shortest']
    run(cmd + ['-y', str(path)])


def frame_pixel(video, ts):
    """抽 ts 秒处一帧读中心像素，验证拼回内容归属。"""
    from PIL import Image, ImageStat
    out = Path(video).parent / (Path(video).stem + f'.{ts}.jpg')
    run([binary('ffmpeg'), '-v', 'error', '-ss', str(ts), '-i', str(video), '-frames:v', '1', '-y', str(out)])
    with Image.open(out) as im:
        return ImageStat.Stat(im.convert('RGB')).mean


class RealFFmpegTests(unittest.TestCase):
    """锚点帧抽取与三段拼回（真实小视频 fixture）。"""

    def test_extract_anchor_frames_produces_two_jpgs(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / 'orig.mp4'
            make_video(src, 'red', 4)
            frames = extract_anchor_frames(src, 1.0, 3.0, Path(tmp) / 'anchors')
            self.assertTrue(frames['head'].is_file() and frames['head'].stat().st_size)
            self.assertTrue(frames['tail'].is_file() and frames['tail'].stat().st_size)
            from PIL import Image
            with Image.open(frames['head']) as im: self.assertEqual(im.size, (160, 90))
            # 幂等复用：目录里已有非空文件时不重抽
            digest_before = digest(frames['head'])
            extract_anchor_frames(src, 1.0, 3.0, Path(tmp) / 'anchors')
            self.assertEqual(digest(frames['head']), digest_before)

    def test_extract_rejects_bad_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / 'orig.mp4'
            make_video(src, 'red', 4)
            with self.assertRaises(ValueError):
                extract_anchor_frames(src, 3.0, 1.0, Path(tmp) / 'anchors')
            with self.assertRaises(ValueError):
                extract_anchor_frames(src, 1.0, 9.0, Path(tmp) / 'anchors')

    def test_splice_back_replaces_middle_segment(self):
        # 4s 红片 1.0–2.0s 换 1s 蓝片段：总时长不变，中段内容确实被替换
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            orig, seg, out = root / 'orig.mp4', root / 'seg.mp4', root / 'out.mp4'
            make_video(orig, 'red', 4, tone=True)
            make_video(seg, 'blue', 1)
            info = splice_back(orig, seg, 1.0, 2.0, out)
            self.assertAlmostEqual(float(info['format']['duration']), 4.0, delta=0.3)
            stream = next(s for s in info['streams'] if s['codec_type'] == 'video')
            self.assertEqual((int(stream['width']), int(stream['height'])), (160, 90))
            self.assertTrue(any(s['codec_type'] == 'audio' for s in info['streams']))
            head = frame_pixel(out, 0.5)    # 原片头部仍是红色
            self.assertGreater(head[0], 200); self.assertLess(head[2], 60)
            mid = frame_pixel(out, 1.5)     # 中段已是蓝色片段
            self.assertGreater(mid[2], 200); self.assertLess(mid[0], 60)
            tail = frame_pixel(out, 3.0)    # 原片尾部仍是红色
            self.assertGreater(tail[0], 200); self.assertLess(tail[2], 60)

    def test_splice_scales_segment_to_original_specs(self):
        # 片段分辨率不同（含奇数差异）时 scale/pad 到原片画布，不裁切不拉伸
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            orig, seg, out = root / 'orig.mp4', root / 'seg.mp4', root / 'out.mp4'
            make_video(orig, 'red', 3, size='160x90')
            make_video(seg, 'blue', 1, size='80x44')
            info = splice_back(orig, seg, 1.0, 2.0, out)
            stream = next(s for s in info['streams'] if s['codec_type'] == 'video')
            self.assertEqual((int(stream['width']), int(stream['height'])), (160, 90))

    def test_splice_at_edges_and_bad_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            orig, seg = root / 'orig.mp4', root / 'seg.mp4'
            make_video(orig, 'red', 3)
            make_video(seg, 'blue', 1)
            # 贴边界：0–1s 与 2–3s（末点）都可拼
            self.assertTrue((root / 'a.mp4').parent.is_dir())
            splice_back(orig, seg, 0.0, 1.0, root / 'a.mp4')
            splice_back(orig, seg, 2.0, 3.0, root / 'b.mp4')
            self.assertAlmostEqual(video_duration(root / 'a.mp4'), 3.0, delta=0.3)
            self.assertAlmostEqual(video_duration(root / 'b.mp4'), 3.0, delta=0.3)
            with self.assertRaises(ValueError):
                splice_back(orig, seg, 1.0, 9.0, root / 'c.mp4')
            with self.assertRaises(ValueError):
                splice_back(orig, seg, -2.0, 1.0, root / 'd.mp4')


class RedoJobTests(unittest.TestCase):
    """制作链路：redo_segment 请求的编译、入队快照与执行后拼回登记。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for folder in ('分镜', '素材', '创作'): (self.root / folder).mkdir()
        # 已采用 V 视频：15s 红片，绑定进 video_binding
        self.video = self.root / '素材/adopted.mp4'
        make_video(self.video, 'red', 6, tone=True)
        self.board = {'shots': [dict(id='S1', dur=6, scene_ref='@scene:a', prompt_image='静帧', prompt_video='原动态')]}
        unit = {'id': 'v-redo1', 'title': 'a', 'shot_ids': ['S1'], 'scene_ref': '@scene:a',
                'prompt_video': '原汇总提示词', 'duration': 6}
        unit['video_binding'] = {'item_id': 'old-item', 'output_index': 0,
                                 'path': self.video.relative_to(self.root).as_posix(), 'sha256': digest(self.video),
                                 'source_hash': media_source_hash(self.board['shots'], 'video', unit),
                                 'board': '剧本_E1.json', 'unit_id': 'v-redo1', 'type': 'video'}
        self.board['video_units'] = [unit]
        self.path = self.root / '分镜/剧本_E1.json'
        self.path.write_text(json.dumps(self.board), encoding='utf-8')
        self.cfg = {'id': 'doubao-api', 'enabled': True, 'models': {'video': 'seedance-2.5'},
                    'base_url': 'https://ark.cn-beijing.volces.com/api/v3'}
        self.providers = self.root / 'providers-test.json'
        self.providers.write_text(json.dumps({'vendors': [self.cfg]}), encoding='utf-8')
        self.body = {'project': self.root.name, 'action': 'redo_segment', 'scope': 'V', 'target': 'v-redo1',
                     'type': 'video', 'board': self.path.name, 'vendor_id': 'doubao-api',
                     'nonce': 'redo-request-unique-1', 't0': 1, 't1': 5}

    def tearDown(self): self.temp.cleanup()

    def test_compile_builds_first_last_request_with_anchor_refs(self):
        from production_jobs import compile_redo_request
        req = compile_redo_request(self.root, self.body, self.cfg)
        self.assertEqual(req['video_options']['mode'], 'first_last')
        self.assertEqual(req['video_options']['duration'], 4)
        self.assertEqual([r['frame_role'] for r in req['refs']], ['first_frame', 'last_frame'])
        self.assertIn('原汇总提示词', req['prompt'])
        self.assertIn('1s 与 5s', req['prompt'])
        self.assertEqual(req['redo']['t0'], 1); self.assertEqual(req['redo']['t1'], 5)
        self.assertEqual(req['redo']['source_output']['item_id'], 'old-item')
        # 锚点帧真实落在缓存目录并可复用
        for ref in req['refs']:
            path = self.root / ref['path']
            self.assertTrue(path.is_file() and path.stat().st_size)
            self.assertEqual(digest(path), ref['sha256'])

    def test_compile_prompt_override_and_errors(self):
        from production_jobs import compile_redo_request
        req = compile_redo_request(self.root, {**self.body, 'prompt': '重写中段动作'}, self.cfg)
        self.assertIn('重写中段动作', req['prompt']); self.assertNotIn('原汇总提示词', req['prompt'].split('\n')[0])
        board = json.loads(self.path.read_text(encoding='utf-8'))
        board['video_units'][0].pop('video_binding')
        self.path.write_text(json.dumps(board), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '尚未采用视频'):
            compile_redo_request(self.root, self.body, self.cfg)
        self.path.write_text(json.dumps(self.board), encoding='utf-8')   # 还原已采用绑定再测其他分支
        with self.assertRaisesRegex(ValueError, '分镜视频不存在'):
            compile_redo_request(self.root, {**self.body, 'target': 'missing'}, self.cfg)
        with self.assertRaisesRegex(ValueError, '超出原片时长'):
            compile_redo_request(self.root, {**self.body, 't1': 99}, self.cfg)
        with self.assertRaisesRegex(ValueError, '数值'):
            compile_redo_request(self.root, {**self.body, 't0': 'x'}, self.cfg)

    def test_enqueue_freezes_source_and_registers_redo_metadata(self):
        from production_jobs import enqueue
        first = enqueue(self.root, self.body, Mock(return_value=100), self.providers)
        second = enqueue(self.root, self.body, Mock(return_value=100), self.providers)
        self.assertEqual(first['item_id'], second['item_id'])
        packet = json.loads((self.root / '创作' / first['item_id'] / 'request.json').read_text(encoding='utf-8'))
        # 源视频与锚点帧都已快照进请求目录，源文件后续被覆盖不影响排队请求
        self.assertTrue(packet['redo']['source']['path'].startswith('创作/'))
        self.video.write_bytes(b'changed')
        self.assertEqual(digest(self.root / packet['redo']['source']['path']), packet['redo']['source']['sha256'])
        for ref in packet['refs']:
            self.assertTrue(ref['path'].startswith('创作/'))
            self.assertEqual(digest(self.root / ref['path']), ref['sha256'])
        item = json.loads((self.root / '创作/creation.json').read_text(encoding='utf-8'))['items'][0]
        self.assertEqual(item['unit_id'], 'v-redo1')
        self.assertEqual(item['redo']['t0'], 1)
        self.assertEqual(item['redo']['anchors']['head'], packet['redo']['anchors']['head'])
        self.assertEqual(item['redo']['source_output']['item_id'], 'old-item')

    def test_execute_generates_splices_and_registers_candidate_without_adopt(self):
        from production_jobs import enqueue, execute
        first = enqueue(self.root, self.body, Mock(return_value=100), self.providers)
        req = json.loads((self.root / '创作' / first['item_id'] / 'request.json').read_text(encoding='utf-8'))
        client = Mock(id='doubao-api', cfg=self.cfg, last_request=None)
        def generate(*a, **kw): make_video(kw['out_path'], 'blue', 4)
        client.generate_video.side_effect = generate
        with patch('llm_openai.VendorClient', return_value=client):
            execute(req, self.providers)
        kwargs = client.generate_video.call_args.kwargs
        self.assertEqual(kwargs['extra']['mode'], 'first_last')
        self.assertEqual(kwargs['extra']['duration'], 4)
        self.assertIsNotNone(kwargs['first_frame']); self.assertIsNotNone(kwargs['last_frame'])
        item = json.loads((self.root / '创作/creation.json').read_text(encoding='utf-8'))['items'][0]
        self.assertEqual(item['status'], 'done')
        # 登记的产出是拼回后的完整视频：时长≈原片 6s，中段为蓝色片段
        out_rel = item['outputs'][0]
        out = self.root / str(out_rel).replace('projects/' + self.root.name + '/', '')
        self.assertAlmostEqual(video_duration(out), 6.0, delta=0.4)
        mid = frame_pixel(out, 3.0)
        self.assertGreater(mid[2], 200); self.assertLess(mid[0], 60)
        head = frame_pixel(out, 0.5)
        self.assertGreater(head[0], 200); self.assertLess(head[2], 60)
        # 不自动采用：分镜里的 video_binding 仍指向旧产出
        saved = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(saved['video_units'][0]['video_binding']['item_id'], 'old-item')

    def test_non_integer_window_rejected_for_integer_models(self):
        # seedance 时长必须整数秒：4.5s 窗口（在 4–30s 范围内）在入队前被整数校验拦下
        from production_jobs import compile_redo_request
        with self.assertRaisesRegex(ValueError, '整数'):
            compile_redo_request(self.root, {**self.body, 't0': 1, 't1': 5.5}, self.cfg)


if __name__ == '__main__': unittest.main()
