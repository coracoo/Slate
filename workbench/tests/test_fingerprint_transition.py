# -*- coding: utf-8 -*-
"""转场纳入过期指纹的回归（业务链路审计 §二.B / §六.2；用户 09-25 定：只补 transition，`retime_prompt` 删掉）。

关键约束是**不能让存量产物无端判过期**：`source_hash`/`media_source_hash` 一变公式，
所有已盖指纹的 V 当场对不上、要重跑（= 花钱）。所以 transition 走"真写了才并入"的条件式，
与本文件里 performance 段的既有先例一致；下面的字面量是改代码**之前**实测抓下来的基线。
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'previs_system' / 'tools'))
sys.path.insert(0, str(ROOT / 'workbench' / 'tools'))

import production_prompts as PP   # noqa: E402

# 改动前用同一 fixture 实测抓下的指纹（09-25，production_prompts 改之前的输出）
BASE_SOURCE = 'adcad92ee3079d37762ac8bb70e2952d03802b7f23f306d790f9edcf7dc62abe'
BASE_IMAGE = '74537dcc131088f923b63236d2b5daf8afa070eca805b08e34a211408f5a8779'
BASE_VIDEO = 'f5b5f72bba085852198739d7c7b9c5fdb3547084956d3a9a2699f7485de4c8b7'

SHOTS = [
    {"id": "S1", "dur": 4, "scene_ref": "@scene:loc_tent", "actor_refs": ["@character:hero"],
     "prop_refs": [], "action": "甲推门进来", "content": "甲进门", "shot_size": "中景",
     "camera_move": "固定", "angle": "平视", "lighting": "侧光", "negative": ["文字"],
     "lines": [{"at": 0.5, "speaker": "hero", "line": "你终于来了"}],
     "prompt_image": "【S1镜（0.0—4.0s）：中景；甲推门】", "prompt_video": "【S1镜（0.0—4.0s）：甲推门并入画】"},
    {"id": "S2", "dur": 3, "scene_ref": "@scene:loc_tent", "actor_refs": ["@character:hero"],
     "action": "乙抬头", "content": "乙抬头看向门口", "shot_size": "近景", "camera_move": "推",
     "angle": "平视", "lines": [], "prompt_image": "【S2镜（4.0—7.0s）：近景；乙抬头】",
     "prompt_video": "【S2镜（4.0—7.0s）：镜头缓推到乙的脸】"},
]
UNIT = {"id": "V1", "duration": 7, "prompt_video": "整段", "negative": ["水印"],
        "generation_options": {"ref_mode": "keyframes"}}


class NoBulkInvalidation(unittest.TestCase):
    def test_boards_without_transition_keep_their_stored_hashes(self):
        """没写 transition 的分镜=绝大多数存量：三个指纹必须逐字不变，否则一堆 V 白白判过期重跑。"""
        self.assertEqual(PP.source_hash(SHOTS), BASE_SOURCE)
        self.assertEqual(PP.media_source_hash(SHOTS[:1], 'image', UNIT), BASE_IMAGE)
        self.assertEqual(PP.media_source_hash(SHOTS, 'video', UNIT), BASE_VIDEO)

    def test_empty_transition_string_counts_as_absent(self):
        """LLM 常把没转场的镜写成 ""——空串不得当作"有 transition"去改指纹形状。"""
        blank = [dict(s, transition='') for s in SHOTS]
        self.assertEqual(PP.source_hash(blank), BASE_SOURCE)
        self.assertEqual(PP.media_source_hash(blank, 'video', UNIT), BASE_VIDEO)


class TransitionIsTracked(unittest.TestCase):
    def _with(self, t1, t2):
        return [dict(SHOTS[0], transition=t1), dict(SHOTS[1], transition=t2)]

    def test_changing_transition_moves_every_fingerprint(self):
        plain = self._with('硬切', '硬切')
        base_img = PP.media_source_hash(plain[:1], 'image', UNIT)
        cut = self._with('淡出', '硬切')
        self.assertNotEqual(PP.source_hash(cut), PP.source_hash(plain),
                            '转场变了而 V 编排指纹不变 = 用旧提示词继续生成')
        self.assertNotEqual(PP.media_source_hash(cut[:1], 'image', UNIT), base_img,
                            '转场直接改写 S 图里"如何接上下镜"，静帧指纹必须跟着变')
        self.assertNotEqual(PP.media_source_hash(cut, 'video', UNIT),
                            PP.media_source_hash(plain, 'video', UNIT))

    def test_same_transition_same_fingerprint(self):
        a, b = self._with('叠化', '硬切'), [dict(s) for s in self._with('叠化', '硬切')]
        self.assertEqual(PP.source_hash(a), PP.source_hash(b))
        self.assertEqual(PP.media_source_hash(a, 'image', UNIT), PP.media_source_hash(b, 'image', UNIT))

    def test_only_the_member_shots_transition_matters_for_a_single_frame(self):
        """S1 的静帧只该被 S1 的转场牵动——但 transition 是"本镜→下镜"的事实，
        整组一起并入是指纹的既有粒度（成员级切分留给二期），这里锁住"不会因为无关镜改名而变"。"""
        a = self._with('淡出', '硬切')
        b = [dict(a[0]), dict(a[1], sound='风声')]
        self.assertEqual(PP.media_source_hash(a[:1], 'image', UNIT),
                         PP.media_source_hash(b[:1], 'image', UNIT))


class DeadFunctionRemoved(unittest.TestCase):
    def test_retime_prompt_is_gone(self):
        self.assertFalse(hasattr(PP, 'retime_prompt'),
                         '全仓零调用的死函数不得留着当"已实现"的假证据')

    def test_no_source_still_references_it(self):
        hits = []
        for path in list((ROOT / 'workbench').rglob('*.py')) + list((ROOT / 'previs_system').rglob('*.py')):
            if any(part in path.parts for part in ('exports', 'node_modules', '__pycache__')):
                continue
            if 'retime_prompt' in path.read_text(encoding='utf-8', errors='replace'):
                hits.append(path.relative_to(ROOT).as_posix())
        self.assertEqual([h for h in hits if not h.startswith('workbench/tests/')], [],
                         '还有生产代码在调它——删除前必须接线或改调新入口')


if __name__ == '__main__':
    unittest.main()
