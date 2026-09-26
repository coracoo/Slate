# -*- coding: utf-8 -*-
"""项目制作规格（brief.json，E05）：数据层合并/校验 + 各消费点回归。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

import brief
import prompt_modules as PM


class BriefStoreTests(unittest.TestCase):
    """load/save 的默认值合并、校验与快照惯例。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.proj = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_missing_file_returns_full_defaults(self):
        self.assertEqual(brief.load_brief(self.proj), brief.DEFAULTS)
        self.assertFalse(brief.has_brief(self.proj))

    def test_save_merges_and_fills_defaults(self):
        out = brief.save_brief(self.proj, {"episode_minutes": 7, "aspect_ratio": "9:16"})
        self.assertTrue(brief.has_brief(self.proj))
        self.assertEqual(out["episode_minutes"], 7)
        self.assertEqual(out["aspect_ratio"], "9:16")
        self.assertIsNone(out["total_episodes"])
        # 二次保存保留既有字段；覆写前走 .versions 快照惯例
        out2 = brief.save_brief(self.proj, {"genre_tone": "悬疑冷峻"})
        self.assertEqual(out2["episode_minutes"], 7)
        self.assertEqual(out2["genre_tone"], "悬疑冷峻")
        snapshots = list((self.proj / "剧本" / ".versions").glob("brief.*.json"))
        self.assertTrue(snapshots, "覆写 brief.json 应自动快照旧版")

    def test_none_resets_field_to_default(self):
        brief.save_brief(self.proj, {"episode_minutes": 7, "max_characters": 4})
        out = brief.save_brief(self.proj, {"episode_minutes": None, "max_characters": None})
        self.assertEqual(out["episode_minutes"], 3)
        self.assertIsNone(out["max_characters"])

    def test_unknown_keys_not_returned(self):
        path = Path(brief.brief_path(self.proj))
        path.parent.mkdir(parents=True, exist_ok=True)
        # resolution/fps 为已下线字段，与未知键一样不透出
        path.write_text(json.dumps({"episode_minutes": 7, "resolution": "4k", "fps": 60, "hack": 1}), encoding="utf-8")
        out = brief.load_brief(self.proj)
        self.assertEqual(out["episode_minutes"], 7)
        self.assertNotIn("resolution", out)
        self.assertNotIn("fps", out)
        self.assertNotIn("hack", out)

    def test_save_purges_legacy_keys(self):
        path = Path(brief.brief_path(self.proj))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"resolution": "4k", "fps": 60, "hack": 1}), encoding="utf-8")
        brief.save_brief(self.proj, {"episode_minutes": 7})
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        self.assertNotIn("resolution", on_disk)
        self.assertNotIn("fps", on_disk)
        self.assertNotIn("hack", on_disk)
        self.assertEqual(on_disk["episode_minutes"], 7)

    def test_corrupt_file_falls_back_to_defaults(self):
        path = Path(brief.brief_path(self.proj))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{bad json", encoding="utf-8")
        self.assertEqual(brief.load_brief(self.proj), brief.DEFAULTS)

    def test_validation_rejects_bad_values(self):
        for patch in ({"episode_minutes": 20}, {"episode_minutes": 0.1}, {"episode_minutes": "x"},
                      {"episode_minutes": True}, {"aspect_ratio": "21:9"}, {"dialogue_density": "极高"},
                      {"max_characters": 0}, {"total_episodes": -2}, {"max_scenes": 1.5},
                      {"genre_tone": "x" * 300}, {"resolution": "1080p"}, {"fps": 24}, {"budget": 5}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                brief.save_brief(self.proj, patch)
        self.assertFalse(brief.has_brief(self.proj))   # 全部拒绝后不落盘


class BriefPromptTests(unittest.TestCase):
    """prompt_modules 的大纲/扩写提示词按 brief 生成。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.proj = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_outline_prompt_scales_with_episode_minutes(self):
        brief.save_brief(self.proj, {"episode_minutes": 7})
        sys_p, _ = PM.outline_prompt("示例构想", 6, proj=str(self.proj))
        self.assertIn("每集约 7 分钟", sys_p)
        self.assertIn("9 个爽点", sys_p)               # 7 分钟 × 60 ÷ 45s ≈ 9
        self.assertNotIn("每集 3~8 分钟", sys_p)
        # 无 brief：维持旧文案逐字不变
        sys_default, _ = PM.outline_prompt("示例构想", 6)
        self.assertIn("每集 3~8 分钟", sys_default)
        self.assertIn("每 30~60 秒一个小冲突或反转（短剧爽点节奏）", sys_default)

    def test_expand_prompt_dialogue_density_tiers(self):
        entry = {"id": "E1", "duration_min": 4}
        brief.save_brief(self.proj, {"dialogue_density": "低"})
        self.assertIn("120~160", PM.expand_episode_prompt("构想", entry, proj=str(self.proj))[0])
        brief.save_brief(self.proj, {"dialogue_density": "高"})
        # 高档原来写 240~300 字/分钟（=4~5 字/s），比 ⑦ 判官的 4 字/s 还快——扩写出的台词到 ⑦ 必然超预算，
        # 属于"先烧钱再提醒"。09-25 定版封顶到 4 字/s，由 test_timing_consistency 锁住两档同源。
        self.assertIn("200~240", PM.expand_episode_prompt("构想", entry, proj=str(self.proj))[0])
        brief.save_brief(self.proj, {"dialogue_density": "中"})
        self.assertIn("180~220", PM.expand_episode_prompt("构想", entry, proj=str(self.proj))[0])
        # 无 brief：维持旧文案
        self.assertIn("约 180~220 字/分钟", PM.expand_episode_prompt("构想", entry)[0])

    def test_genre_tone_injected_only_when_set(self):
        brief.save_brief(self.proj, {"genre_tone": "悬疑冷峻"})
        self.assertIn("悬疑冷峻", PM.outline_prompt("构想", 6, proj=str(self.proj))[0])
        self.assertIn("悬疑冷峻", PM.expand_episode_prompt("构想", {"id": "E1"}, proj=str(self.proj))[0])
        brief.save_brief(self.proj, {"genre_tone": ""})
        self.assertNotIn("题材与基调（项目制作规格）", PM.outline_prompt("构想", 6, proj=str(self.proj))[0])
        self.assertNotIn("题材与基调（项目制作规格）", PM.expand_episode_prompt("构想", {"id": "E1"}, proj=str(self.proj))[0])


class BriefImageRatioTests(unittest.TestCase):
    """S/V 生图画幅缺省跟随 brief.aspect_ratio。"""

    def test_aspect_ratio_of(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(brief.aspect_ratio_of(tmp), "16:9")
            brief.save_brief(tmp, {"aspect_ratio": "9:16"})
            self.assertEqual(brief.aspect_ratio_of(tmp), "9:16")

    def test_compile_request_image_ratio_follows_brief(self):
        from production_requests import compile_request
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '分镜').mkdir()
            board = {'shots': [dict(id='S1', dur=3, scene_ref='@scene:a',
                                    prompt_image='静帧', prompt_video='动态')]}
            (root / '分镜' / 'b.json').write_text(json.dumps(board), encoding='utf-8')
            cfg = {'id': 'doubao-api', 'enabled': True, 'models': {'image': 'seedream-4'}}
            body = {'project': root.name, 'action': 'generate', 'scope': 'S', 'target': 'S1',
                    'type': 'image', 'board': 'b.json'}
            with patch('prompt_assembler.resolve_shot_refs', return_value=[]):
                packet = compile_request(root, body, cfg)
            # 无 brief：维持 16:9 缺省
            self.assertIn('画幅 16:9', packet['prompt'])
            self.assertEqual(packet['image_options'], {'ratio': '16:9'})
            brief.save_brief(root, {'aspect_ratio': '9:16'})
            with patch('prompt_assembler.resolve_shot_refs', return_value=[]):
                packet = compile_request(root, body, cfg)
            self.assertIn('画幅 9:16', packet['prompt'])
            self.assertEqual(packet['image_options'], {'ratio': '9:16'})


class BriefStudioNoticeTests(unittest.TestCase):
    """V 分组提示（E05）：V 总时长超过单集目标时 state() 给出 brief_notice；不改分组算法。"""

    def test_state_brief_notice(self):
        import production_studio as studio
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / '分镜').mkdir()
            board = {'shots': [dict(id='S1', dur=100, scene_ref='@scene:a'),
                               dict(id='S2', dur=100, scene_ref='@scene:a')],
                     'video_units': [{'id': 'v1', 'shot_ids': ['S1', 'S2'],
                                      'scene_ref': '@scene:a', 'duration': 200}]}
            (root / '分镜' / 'b.json').write_text(json.dumps(board), encoding='utf-8')
            st = studio.state(root, 'b.json')
            self.assertNotIn('brief_notice', st)       # 无 brief 不提示
            brief.save_brief(root, {'episode_minutes': 3})
            st = studio.state(root, 'b.json')
            self.assertIn('brief_notice', st)
            self.assertIn('200', st['brief_notice'])   # 总时长 200s > 3 分钟（180s）
            brief.save_brief(root, {'episode_minutes': 10})
            st = studio.state(root, 'b.json')
            self.assertNotIn('brief_notice', st)       # 200s < 10 分钟，不提示


if __name__ == '__main__':
    unittest.main()
