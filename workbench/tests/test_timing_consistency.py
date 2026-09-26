# -*- coding: utf-8 -*-
"""时长档与语速档的"单一权威"回归（业务链路审计 §六.10 / §二.G）。

用户 09-25 定版：
* **语速档**只有一个 4 字/s（`SPEECH_RATE`），① 扩写的对白密度档不得写出比它更快的台词；
  ③ 分镜落盘按"字数÷语速"顶高镜长，超过硬顶就不再自动加（拆词交回给人）。
* **时长档**权威值 1.5~15s（`SHOT_DURATION_MIN/MAX`）：③ 落盘夹取与 ③ 表格保存都以它为准；
  提示词里写的"每镜 2~12 秒"是给 LLM 的偏好档，不是硬闸；模型能力上限另有 V 上限（8/15/30）。

原来三处各说各话（2~12 / 1.5~15 / 1~60）+ 两处语速（4 vs 5 字/s），实测 09_仙 S2
41 字需 10.25s 而镜长 6s → 白模字幕中途消失，判官要到 ⑦ 才报（钱已烧）。
"""
import inspect
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'previs_system' / 'tools'))
sys.path.insert(0, str(ROOT / 'workbench' / 'tools'))

import creation_pipeline           # noqa: E402
import prompt_modules as PM        # noqa: E402
import production_studio as studio  # noqa: E402


def _shot(chars, dur=4.0):
    return {'id': 'S1', 'dur': dur, 'lines': [{'speaker': 'hero', 'line': '字' * chars}]}


class SpeechRateCeiling(unittest.TestCase):
    def test_expansion_density_never_outruns_the_judge_rate(self):
        """① 扩写若允许比判官更快的语速，写出来的台词到 ⑦ 必然超预算——那是先烧钱再提醒。"""
        ceiling = studio.SPEECH_RATE * 60      # 字/分钟
        for label, (lo, hi) in PM.DIALOGUE_RATE.items():
            self.assertLessEqual(hi, ceiling, f'{label} 密度档 {hi} 字/分钟 超过 {ceiling:g}（={studio.SPEECH_RATE:g} 字/s）')
            self.assertLess(lo, hi)
        self.assertEqual(sorted(PM.DIALOGUE_RATE), ['中', '低', '高'], '密度档名不得悄悄增删（brief 存的是这个键）')


class SpeechBudgetAppliedAtStoryboard(unittest.TestCase):
    def test_short_shot_is_raised_to_what_the_lines_need(self):
        """30 字 ÷ 4 字/s = 7.5s：叙事给了 4s 也要顶到 7.5s，否则字幕中途消失。"""
        dur, need = studio.fit_speech_budget(_shot(30, 4.0), 4.0)
        self.assertEqual((dur, need), (7.5, 7.5))

    def test_visual_shot_keeps_its_authored_length(self):
        dur, need = studio.fit_speech_budget({'id': 'S1', 'dur': 6}, 6.0)
        self.assertEqual((dur, need), (6.0, 0.0), '没有台词就不该被预算闸碰到')

    def test_brief_line_does_not_shrink_a_long_shot(self):
        dur, need = studio.fit_speech_budget(_shot(8, 9.0), 9.0)
        self.assertEqual((dur, need), (9.0, 2.0), '顶高只做下限，绝不把用户给长的镜头压回去')

    def test_overlong_lines_stop_at_the_hard_cap(self):
        """>15s 的台词不再自动拉长：时长顶到 15，need 如实带回，由调用方点名让人拆词。"""
        dur, need = studio.fit_speech_budget(_shot(80, 6.0), 6.0)     # 80 字 = 20s
        self.assertEqual(dur, studio.SHOT_DURATION_MAX)
        self.assertEqual(need, 20.0)
        self.assertLessEqual(dur, studio.SHOT_DURATION_MAX, '任何情况下都不得越过单镜硬顶')

    def test_storyboard_pipeline_calls_it(self):
        """断言要盯**调用形状**：cmd_storyboard 顶部的 import 也在函数体内，只查函数名会出现
        "import 在、调用没了"还算通过的假绿（第一次反证就是这么露的）。"""
        src = inspect.getsource(creation_pipeline.cmd_storyboard)
        self.assertIn('fit_speech_budget(s, authored)', src, '③ 落盘没接预算闸 = 又回到"到 ⑦ 才报"')
        self.assertIn('时长按台词预算', src, '顶高必须留痕，否则用户以为时长是自己填的')
        self.assertIn('已超过单镜硬顶', src, '顶到硬顶仍装不下必须点名（这条不自动改数值，交回给人）')


class DurationBandIsSingleSourced(unittest.TestCase):
    def test_only_production_studio_defines_the_band(self):
        hits = []
        needle = 'SHOT_DURATION_MAX' + ' ='      # 拼一下：别让本测试文件自己被当成第二处定义
        needle2 = 'SHOT_DURATION_MIN' + ' ='
        for path in list((ROOT / 'workbench').rglob('*.py')) + list((ROOT / 'previs_system').rglob('*.py')):
            if any(part in path.parts for part in ('exports', 'node_modules', 'tests')):
                continue      # 测试里引用常量不算定义点
            text = path.read_text(encoding='utf-8', errors='replace')
            if needle in text or needle2 in text:
                hits.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(hits, ['workbench/tools/production_studio.py'],
                         '时长档只能有一处定义，第二处必然漂移')

    def test_storyboard_save_route_no_longer_hardcodes_1_to_60(self):
        """③ 表格保存原来是全仓最宽的 1~60s（比管线还松），手填 40s 单镜能一路进到 E 成片。"""
        sys.path.insert(0, str(ROOT / 'workbench'))
        import workbench.server as server
        src = inspect.getsource(server.H.route_post_api_storyboard_save)
        self.assertNotIn('60.0', src, '路由里不该再出现字面量上限')
        self.assertIn('SHOT_DURATION_MAX', src, '必须引用权威常量，否则两处又会各说各话')

    def test_pipeline_clamps_with_the_shared_constants(self):
        src = inspect.getsource(creation_pipeline.cmd_storyboard)
        self.assertNotIn('min(15.0', src, '落盘夹取应引用常量而不是重复写数字')
        self.assertIn('SHOT_DURATION_MIN', src)


if __name__ == '__main__':
    unittest.main()
