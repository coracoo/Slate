# -*- coding: utf-8 -*-
"""plan_frames 三改造测试：
A. 场景平面图派生（ensure_scene_plan：plan→PNG→素材图注册，幂等、fail-soft）；
C. 分镜平面图帧（ensure_plan_frames 幂等/重渲）与 V 编译参考回流
   （plan_ref_frames 预算抽样必含首尾 + compile_request 注入/开关/预算/无 plan 静默）。"""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

import brief
import gen_plan
import plan_adapt
import plan_frames
import production_requests

PLAN_A = {"version": 1, "name": "a", "scene_ref": "a", "canvas": {"w": 12, "h": 9},
          "room": {"walls": [[1, 1], [11, 1], [11, 8], [1, 8]]},
          "props": [{"id": "p1", "label": "长案", "shape": "rect", "center": [6, 4], "size": [2, 1]}],
          "actors": [{"id": "c", "name": "甲", "color": "#e74c3c", "pos": [5, 4]},
                     {"id": "v", "name": "乙", "color": "#5b8ff9", "pos": [7, 4]}]}

JUDGE_OK = lambda plan: {"ok": True, "reasons": [], "backend": "mock"}


def _write_plan(proj, plan=PLAN_A, name=None):
    return plan_adapt.save_plan(str(proj), name or plan.get("scene_ref") or plan["name"], plan)


def _write_board(proj, n=2, unit=True):
    """最小生产分镜：S1..Sn 带已采用关键帧 + 单个 V（default_units），返回 (board, board_path)。"""
    from PIL import Image
    from production_media import digest
    from production_prompts import media_source_hash
    import production_studio as studio
    board = {'shots': [dict(id=f'S{i}', dur=3, scene_ref='@scene:a', prompt_image=f'静帧{i}',
                            prompt_video=f'动态{i}') for i in range(1, n + 1)]}
    for s in board['shots']:
        f = Path(proj) / '素材' / f"{s['id']}.png"
        Image.new('RGB', (64, 36), 'red').save(f)
        s['keyframe'] = {'path': f.relative_to(proj).as_posix(), 'sha256': digest(f),
                         'source_hash': media_source_hash([s], 'image'), 'item_id': 'old-' + s['id']}
    if unit:
        board['video_units'] = studio.default_units(board)
        board['video_units'][0].update(prompt_video='场景连续动作')
    path = Path(proj) / '分镜' / '剧本_E1.json'
    path.write_text(json.dumps(board), encoding='utf-8')
    return board, path


class PlanDerivativeTests(unittest.TestCase):
    """任务 A：ensure_scene_plan 一站式（草稿→渲染→注册）与素材图派生条目。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.proj = Path(self._td.name)
        (self.proj / '素材').mkdir(parents=True)
        json.dump({"scenes": [{"id": "a", "name": "中军帐", "geometry": ["军帐 8x6，中央长案"],
                               "interior": True, "source_episode_ids": ["E1"]}]},
                  open(self.proj / '素材' / '场景.json', 'w', encoding='utf-8'), ensure_ascii=False)

    def tearDown(self):
        self._td.cleanup()

    def test_ensure_scene_plan_creates_png_and_registers(self):
        chat = mock.Mock(return_value=json.dumps(PLAN_A, ensure_ascii=False))
        r = plan_frames.ensure_scene_plan(str(self.proj), 'a', gen_chat=chat, judge_fn=JUDGE_OK)
        self.assertTrue(r['created'])
        self.assertEqual(r['png'], '素材/场景/a__plan.png')
        self.assertTrue((self.proj / '素材' / '场景' / 'a__plan.png').is_file())
        self.assertTrue(r['registered'])
        self.assertTrue((self.proj / '推演' / '平面图_a.plan.json').is_file())
        idx = json.load(open(self.proj / '素材' / '素材图.json', encoding='utf-8'))
        ent = idx['场景']['a__plan']
        self.assertEqual(ent['usage'], 'plan')
        self.assertEqual(ent['parent_ref'], '@scene:a')
        self.assertEqual(ent['derived_from'], '@scene:a')
        self.assertEqual(ent['source_episode_ids'], ['E1'])   # 从母条目拷贝（前端按集过滤可见）
        # 幂等：再跑一次不重建、不重复注册
        mtime = os.path.getmtime(self.proj / '素材' / '场景' / 'a__plan.png')
        r2 = plan_frames.ensure_scene_plan(str(self.proj), 'a', gen_chat=chat, judge_fn=JUDGE_OK)
        self.assertFalse(r2['created'])
        self.assertFalse(r2['registered'])
        self.assertEqual(mtime, os.path.getmtime(self.proj / '素材' / '场景' / 'a__plan.png'))
        self.assertEqual(chat.call_count, 1)

    def test_ensure_scene_plan_vendor_failure_is_soft(self):
        from llm_openai import VendorError
        with mock.patch.object(gen_plan, 'pick_vendor', side_effect=VendorError('无厂商')):
            r = plan_frames.ensure_scene_plan(str(self.proj), 'a')   # 不抛异常
        self.assertFalse(r['created'])
        self.assertIn('无厂商', r['error'])
        self.assertIsNone(r['png'])

    def test_register_only_when_plan_exists(self):
        # draft=False 且已有 plan：只渲染+注册，不触 LLM
        _write_plan(self.proj)
        r = plan_frames.ensure_scene_plan(str(self.proj), 'a', draft=False)
        self.assertFalse(r['created'])
        self.assertTrue(r['registered'])
        # index 参数传入时只改内存（gen_asset_images 末尾统一落盘路径）
        idx = {"场景": {}}
        changed = plan_frames.register_plan_derivative(str(self.proj), 'a', r['png'], index=idx)
        self.assertTrue(changed)
        self.assertIn('a__plan', idx['场景'])
        idx2 = json.load(open(self.proj / '素材' / '素材图.json', encoding='utf-8'))
        self.assertIn('a__plan', idx2['场景'])   # ensure_scene_plan 已落盘过一次


class PlanFramesTests(unittest.TestCase):
    """任务 C 原料：ensure_plan_frames 幂等与签名失效重渲。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.proj = Path(self._td.name)
        (self.proj / '分镜').mkdir(parents=True)
        (self.proj / '素材').mkdir()
        self.board, self.board_path = _write_board(self.proj, n=3)
        self.plan_path = _write_plan(self.proj)

    def tearDown(self):
        self._td.cleanup()

    def test_render_idempotent_and_v_alias(self):
        frames = plan_frames.ensure_plan_frames(str(self.proj), str(self.board_path),
                                                self.plan_path, v_label='V01')
        self.assertEqual(sorted(frames), ['S1', 'S2', 'S3'])
        outdir = self.proj / '推演' / '平面图帧_剧本_E1'
        for sid in ('S1', 'S2', 'S3'):
            self.assertTrue((outdir / f'{sid}.png').is_file())
            self.assertTrue((outdir / f'V01_{sid}.png').is_file())
            self.assertTrue(frames[sid].endswith(f'V01_{sid}.png'))
        mtime = os.path.getmtime(outdir / 'S1.png')
        # 幂等：签名不变 + 帧齐全 → 不重渲
        frames2 = plan_frames.ensure_plan_frames(str(self.proj), str(self.board_path), self.plan_path)
        self.assertEqual(mtime, os.path.getmtime(outdir / 'S1.png'))
        self.assertEqual(sorted(frames2), ['S1', 'S2', 'S3'])
        # 成员帧缺失 → 触发重渲
        os.remove(outdir / 'S2.png')
        time.sleep(0.02)
        frames3 = plan_frames.ensure_plan_frames(str(self.proj), str(self.board_path), self.plan_path)
        self.assertTrue((outdir / 'S2.png').is_file())
        self.assertEqual(sorted(frames3), ['S1', 'S2', 'S3'])

    def test_multi_scene_board_renders_each_group_with_its_own_plan(self):
        """跨场景分镜按场景各用自己的底图（规范：同场景共用一张）。

        曾整板只喂一张 plan：实测 09_仙 剧本_E1 的 15 镜分属 3 场景，即便按多数镜匹配
        仍有 8 镜画在他场墙纸上——而这些图正是 ⑦ 喂给视频模型的空间参考帧。
        """
        plan_b_path = _write_plan(self.proj, dict(PLAN_A, name="b", scene_ref="b"))
        board = {"shots": [
            {"id": "S1", "scene_ref": "@scene:a", "dur": 3},
            {"id": "S2", "scene_ref": "@scene:b", "dur": 3},
            {"id": "S3", "scene_ref": "@scene:b", "dur": 3}]}
        bp = Path(self.proj) / '分镜' / '剧本_E1.json'
        bp.write_text(json.dumps(board, ensure_ascii=False), encoding='utf-8')
        cmds = []

        def fake_run(cmd, **kw):
            cmds.append(list(cmd))
            outdir = Path(cmd[cmd.index('--outdir') + 1])
            outdir.mkdir(parents=True, exist_ok=True)
            for sid in cmd[cmd.index('--shots') + 1].split(','):
                (outdir / f'{sid}.png').write_bytes(b'x')
            return type('R', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()

        with mock.patch.object(plan_frames.subprocess, 'run', fake_run):
            frames = plan_frames.ensure_plan_frames(str(self.proj), str(bp), self.plan_path)
        self.assertEqual(sorted(frames), ['S1', 'S2', 'S3'])
        self.assertEqual(len(cmds), 2, f'两个场景应分两次渲染，实际 {len(cmds)} 次')
        by_plan = {Path(c[c.index('--plan') + 1]).name: sorted(c[c.index('--shots') + 1].split(','))
                   for c in cmds}
        self.assertEqual(by_plan.get(Path(self.plan_path).name), ['S1'], 'a 场只该用 a 的底图')
        self.assertEqual(by_plan.get(Path(plan_b_path).name), ['S2', 'S3'], 'b 场必须换自己的底图')
        meta = json.loads((Path(self.proj) / '推演' / '平面图帧_剧本_E1' / '_meta.json')
                          .read_text(encoding='utf-8'))
        self.assertEqual(len(meta['plan_sigs'].split(';')), 2, '签名要覆盖两张底图')

    def test_member_subset(self):
        frames = plan_frames.ensure_plan_frames(str(self.proj), str(self.board_path),
                                                self.plan_path, member_ids=['S2', 'S3'])
        self.assertEqual(sorted(frames), ['S2', 'S3'])


class PlanRefFramesTests(unittest.TestCase):
    """任务 C 抽样：plan_ref_frames 预算（必含首尾）、refs 记录结构、无 plan 静默。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.proj = Path(self._td.name)
        (self.proj / '分镜').mkdir(parents=True)
        (self.proj / '素材').mkdir()
        self.board, self.board_path = _write_board(self.proj, n=5)

    def tearDown(self):
        self._td.cleanup()

    def test_no_plan_silently_empty(self):
        refs = plan_frames.plan_ref_frames(str(self.proj), '剧本_E1.json',
                                           self.board['shots'], 'V01', 10)
        self.assertEqual(refs, [])

    def test_budget_sampling_keeps_first_and_last(self):
        _write_plan(self.proj)
        shots = self.board['shots']
        all_refs = plan_frames.plan_ref_frames(str(self.proj), '剧本_E1.json', shots, 'V01', 10)
        self.assertEqual([r['shot_id'] for r in all_refs], ['S1', 'S2', 'S3', 'S4', 'S5'])
        for r in all_refs:
            self.assertEqual(r['frame_role'], 'reference_image')
            self.assertEqual(r['purpose'], '平面推演参考帧')
            self.assertTrue(r['path'].startswith('推演/平面图帧_剧本_E1/V01_'))
            from production_media import digest
            self.assertEqual(digest(self.proj / r['path']), r['sha256'])
        two = plan_frames.plan_ref_frames(str(self.proj), '剧本_E1.json', shots, 'V01', 2)
        self.assertEqual([r['shot_id'] for r in two], ['S1', 'S5'])
        one = plan_frames.plan_ref_frames(str(self.proj), '剧本_E1.json', shots, 'V01', 1)
        self.assertEqual([r['shot_id'] for r in one], ['S1'])
        zero = plan_frames.plan_ref_frames(str(self.proj), '剧本_E1.json', shots, 'V01', 0)
        self.assertEqual(zero, [])


class CompilePlanRefTests(unittest.TestCase):
    """任务 C 编译：compile_request 注入平面图参考帧（开关/预算/静默/提示词约束句）。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.proj = Path(self._td.name)
        (self.proj / '分镜').mkdir(parents=True)
        (self.proj / '素材').mkdir()
        (self.proj / '剧本').mkdir()
        self.board, self.board_path = _write_board(self.proj, n=2)
        self.plan_path = _write_plan(self.proj)
        self.cfg = {'id': 'doubao-api', 'enabled': True,
                    'models': {'video': 'seedance-2.5', 'image': 'seedream-4'},
                    'base_url': 'https://ark.cn-beijing.volces.com/api/v3'}
        self.body = {'project': self.proj.name, 'action': 'generate', 'scope': 'V',
                     'target': self.board['video_units'][0]['id'], 'type': 'video',
                     'board': '剧本_E1.json', 'vendor_id': 'doubao-api',
                     'nonce': 'plan-ref-test', 'duration': 6}

    def tearDown(self):
        self._td.cleanup()

    def _compile(self):
        return production_requests.compile_request(str(self.proj), dict(self.body), self.cfg)

    def test_injects_plan_frames_and_constraint_sentence(self):
        req = self._compile()
        roles = [(r['shot_id'], r['purpose']) for r in req['refs']]
        self.assertEqual(roles[:2], [('S1', 'S1 的已采用关键帧'), ('S2', 'S2 的已采用关键帧')])
        self.assertEqual(roles[2:], [('S1', '平面推演参考帧'), ('S2', '平面推演参考帧')])
        self.assertEqual(req['plan_refs'], 2)
        self.assertIn('场景空间布局与人物走位以参考平面图为准', req['prompt'])
        self.assertIn('保持画左画右关系一致', req['prompt'])
        # 注入的帧是 V 别名 PNG，且 digest 与文件一致
        for r in req['refs'][2:]:
            self.assertTrue((self.proj / r['path']).is_file())

    def test_body_switch_off(self):
        self.body['plan_refs'] = False
        req = self._compile()
        self.assertEqual([r['shot_id'] for r in req['refs']], ['S1', 'S2'])
        self.assertEqual(req['plan_refs'], 0)
        self.assertNotIn('参考平面图', req['prompt'])

    def test_brief_switch_off(self):
        brief.save_brief(str(self.proj), {'plan_refs': False})
        req = self._compile()
        self.assertEqual(req['plan_refs'], 0)
        self.assertEqual(len(req['refs']), 2)

    def test_budget_caps_to_limit(self):
        # 参考上限 3：2 张已采用关键帧占满后只注入 1 张平面图帧（首成员）
        with mock.patch.object(production_requests, 'reference_limit', return_value=3):
            req = self._compile()
        self.assertEqual(req['plan_refs'], 1)
        plan_refs = [r for r in req['refs'] if r['purpose'] == '平面推演参考帧']
        self.assertEqual([r['shot_id'] for r in plan_refs], ['S1'])
        self.assertEqual(len(req['refs']), 3)

    def test_no_plan_keeps_old_behavior(self):
        os.remove(self.plan_path)
        req = self._compile()
        self.assertEqual([r['shot_id'] for r in req['refs']], ['S1', 'S2'])
        self.assertEqual(req['plan_refs'], 0)

    def _adopt_grid(self):
        """按 adopt_grid 写入侧的同一公式造一份"已采用的宫格参考"（新闸：宫格必须是产物，不再隐式抓图）。"""
        from production_media import digest
        from production_prompts import media_source_hash
        from production_studio import shot_list
        grid = self.proj / '创作' / 'grid-adopted.png'
        grid.parent.mkdir(parents=True, exist_ok=True)
        grid.write_bytes(b'grid-bytes')
        unit = self.board['video_units'][0]
        unit['prompt_grid'] = '两镜宫格'
        unit['grid_binding'] = {'item_id': 'g1', 'output_index': 0,
                                'path': grid.relative_to(self.proj).as_posix(),
                                'sha256': digest(grid),
                                'source_hash': media_source_hash(shot_list(self.board, unit), 'image', unit),
                                'purpose': '故事板宫格参考'}
        self.board_path.write_text(json.dumps(self.board), encoding='utf-8')
        self.body['target'] = unit['id']
        return unit

    def test_grid_mode_skips_injection(self):
        self.body['ref_mode'] = 'grid'
        self.body['prompt_grid_note'] = None
        self._adopt_grid()
        req = self._compile()
        self.assertEqual(req['plan_refs'], 0)
        self.assertNotIn('参考平面图', req['prompt'])
        self.assertEqual([r['purpose'] for r in req['refs']], ['故事板宫格参考图'],
                         '宫格模式只带已采用的那张宫格，不再偷偷塞关键帧或平面图')

    def test_grid_mode_requires_adopted_output(self):
        """没点过「采用为宫格参考」就不许提交——这条闸存在的意义是"参考了哪张图"永远可查。"""
        self.body['ref_mode'] = 'grid'
        unit = self._adopt_grid()
        unit.pop('grid_binding')
        self.board_path.write_text(json.dumps(self.board), encoding='utf-8')
        with self.assertRaises(ValueError) as cm:
            self._compile()
        self.assertIn('尚未采用故事板宫格', str(cm.exception))

    def test_stale_grid_binding_gives_the_friendly_message(self):
        """过期分支原本引用了一个未定义的变量（会抛 NameError，友好提示永远出不来）。"""
        self.body['ref_mode'] = 'grid'
        unit = self._adopt_grid()
        unit['grid_binding']['source_hash'] = 'stale-hash'
        self.board_path.write_text(json.dumps(self.board), encoding='utf-8')
        with self.assertRaises(ValueError) as cm:
            self._compile()
        self.assertIn('宫格参考已过期', str(cm.exception))


if __name__ == '__main__':
    unittest.main()
