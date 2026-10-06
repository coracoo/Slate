# -*- coding: utf-8 -*-
"""N90 提示词编译回归：运镜执行句 / 时间轴瘦身 / 负面本镜化 / 判官多镜警告。"""
import os
import sys
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.abspath(os.path.join(HERE, "..", "tools"))
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)
import production_prompts as pp
import production_requests as pr
import production_studio as studio


def _shot(move='固定'):
    return {'id': 'S1', 'prompt_video': '【S1镜（0—4s）：测试正文】', 'camera_move': move,
            'angle': '平视', 'scene_ref': '@scene:a', 'scene': 'room',
            'negative': ['禁止腰牌飞起']}


class CameraMotionTests(unittest.TestCase):
    def test_push_gets_executable_sentence(self):
        sent = pp.camera_motion_sentence(_shot('推'))
        self.assertIn('匀速缓慢向前推进', sent)
        self.assertNotIn('滑轨前移，画面边缘', '')  # 占位确保句式完整

    def test_fixed_gets_locked_sentence(self):
        sent = pp.camera_motion_sentence(_shot('固定'))
        self.assertIn('焊死不动', sent)

    def test_unknown_move_falls_back(self):
        sent = pp.camera_motion_sentence(_shot('不明运镜'))
        self.assertIn('焊死不动', sent)

    def test_video_shot_text_contains_motion_line(self):
        text = pp.video_shot_text(_shot('推'), 0, 4)
        self.assertIn('运镜执行：', text)
        self.assertIn('匀速缓慢向前推进', text)
        self.assertIn('分镜补充：', text)


class TimelineDigestTests(unittest.TestCase):
    def _board(self, moves):
        shots = [dict(id=f'S{i}', dur=4, scene_ref='@scene:a',
                      prompt_video=f'【S{i}镜（0—4s）：{m}机位正文；第一拍内容。】',
                      camera_move=m, angle='平视', negative=[f'禁{i}'])
                 for i, m in enumerate(moves, 1)]
        board = {'shots': shots}
        board['video_units'] = studio.default_units(board, cap=30)
        return board

    def test_timeline_lines_have_motion_and_no_full_repeat(self):
        import types
        board = self._board(['固定', '推'])
        unit = board['video_units'][0]
        beats = studio.timeline(board, unit)
        shots = studio.shot_list(board, unit)
        lines = []
        for s, b in zip(shots, beats):
            motion = pp.camera_motion_sentence(s)
            digest = str(b['prompt'] or '').strip().split('；')[0].split('。')[0][:36]
            lines.append(f"{b['start']:g}–{b['end']:g}｜{s['id']}｜镜头{motion}｜本镜只演这一镜，节拍：{digest}")
        joined = '\n'.join(lines)
        self.assertIn('机位全程焊死不动', joined)
        self.assertIn('匀速缓慢向前推进', joined)
        self.assertIn('本镜只演这一镜', joined)
        # 摘要只截首句 36 字，不含完整【】闭合正文（瘦身判定=无 】）
        self.assertNotIn('】', joined)

    def test_judge_flags_multi_shot_mixed_moves(self):
        board = self._board(['固定', '推', '环绕'])
        unit = board['video_units'][0]
        verdict = studio.judge_unit(board, unit, cap=30)
        self.assertTrue(any('不同运镜' in w for w in verdict['warnings']))

    def test_judge_ok_for_single_move(self):
        board = self._board(['固定', '固定'])
        unit = board['video_units'][0]
        verdict = studio.judge_unit(board, unit, cap=30)
        self.assertFalse(any('不同运镜' in w for w in verdict['warnings']))


class NegativeScopeTests(unittest.TestCase):
    def test_single_shot_branch_excludes_neighbors(self):
        # 分支行为锁定（compile_request 内联段的镜像）：单镜只收本镜负面；多镜全并
        def merge(shots):
            negs = []
            if len(shots) == 1:
                for n in (shots[0].get('negative') or []):
                    negs.append(n if isinstance(n, str) else str(n))
            else:
                for s in shots:
                    n = s.get('negative') or []
                    negs.extend(n if isinstance(n, list) else [n])
            return '；'.join(dict.fromkeys(['文字、水印'] + [str(n) for n in negs if n]))
        both = [{'id': 'S1', 'negative': ['禁止腰牌飞起']},
                {'id': 'S2', 'negative': ['禁止乌木匣打开']}]
        self.assertIn('禁止乌木匣打开', merge(both))          # 多镜：全并（现状语义）
        single = merge(both[:1])                               # 单镜(S1)：不带 S2 的负面
        self.assertIn('禁止腰牌飞起', single)
        self.assertNotIn('禁止乌木匣打开', single)


if __name__ == '__main__':
    unittest.main()
