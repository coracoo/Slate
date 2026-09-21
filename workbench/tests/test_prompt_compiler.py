# -*- coding: utf-8 -*-
"""统一演员提示词编译出口回归测试。"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT / "workbench" / "tools", ROOT / "previs_system" / "tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def board_with_performance(mode="stateful", source_hash=""):
    return {
        "actors": {"a": {"name": "甲"}, "b": {"name": "乙"}},
        "acting_context": {"actor_cards": {"a": {"personality": "克制", "goal": "守住秘密"}}},
        "shots": [{
            "id": "S1", "dur": 4, "scene": "room", "move": "固定",
            "prompt": "甲坐在桌边", "lines": [{"speaker": "a", "line": "我没事"}],
            "staging": {"a": [0, 1], "b": [1, 2]},
            "performance": {"status": "ready", "mode": mode, "source_hash": source_hash,
                "packet": {"shot_id": "S1", "actors": [{"actor_id": "a", "beats": [
                    {"at": 0, "duration": 1, "expression": "平静", "voice": "低声"},
                    {"at": 2, "duration": 1, "gesture": "握紧手机"}
                ]}]}}
        }]
    }


class PromptCompilerTests(unittest.TestCase):
    def test_baseline_contains_original_prompt_and_lines(self):
        from prompt_compiler import compile_shot
        out = compile_shot(board_with_performance(), "S1", mode="baseline", media_type="video")
        self.assertEqual(out["mode_used"], "baseline")
        self.assertIn("甲坐在桌边", out["text"])
        self.assertIn("甲：我没事", out["text"])
        self.assertFalse(out["performance_used"])

    def test_stateful_uses_card_and_video_beats(self):
        from prompt_compiler import compile_shot
        out = compile_shot(board_with_performance(), "S1", mode="stateful", media_type="video")
        self.assertEqual(out["mode_used"], "stateful")
        self.assertTrue(out["performance_used"])
        self.assertIn("克制", out["text"])
        self.assertIn("握紧手机", out["text"])
        self.assertIn("低声", out["voice_notes"])
        self.assertTrue(out["warnings"])

    def test_image_keeps_only_last_beat(self):
        from prompt_compiler import compile_shot
        out = compile_shot(board_with_performance(), "S1", mode="stateful", media_type="image")
        self.assertIn("握紧手机", out["text"])
        self.assertNotIn("平静", out["text"])

    def test_stale_performance_falls_back(self):
        from prompt_compiler import compile_shot
        out = compile_shot(board_with_performance(source_hash="stale"), "S1", mode="stateful", media_type="video")
        self.assertFalse(out["performance_used"])
        self.assertEqual(out["mode_used"], "stateful")  # 角色卡仍可作为可见表演约束
        self.assertTrue(any("过期" in warning for warning in out["warnings"]))

    def test_style_candidate_is_used_only_for_style_mode(self):
        from prompt_compiler import compile_shot
        board = board_with_performance(mode="style")
        style = compile_shot(board, "S1", mode="style", media_type="video")
        self.assertEqual(style["mode_used"], "style")
        self.assertTrue(style["performance_used"])
        stateful = compile_shot(board, "S1", mode="stateful", media_type="video")
        self.assertTrue(stateful["performance_used"])


if __name__ == "__main__":
    unittest.main()
