# -*- coding: utf-8 -*-
"""两处"上游重跑抹掉下游成果"的回归：
① 重新生成分镜必须保留 ⑤ 采用的表演（production_studio.retain_production）；
② 在场角色判定必须按别名排除旁白（shot_presence 曾只挡字面量 'narrator'）。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "workbench" / "tools"))

import production_studio as studio
from shot_presence import actor_positions


class RetainPerformanceTests(unittest.TestCase):
    def test_regenerated_board_keeps_adopted_performance(self):
        previous = {"shots": [{
            "id": "S1",
            "keyframe": {"item_id": "kf1"},
            "performance": {"status": "ready", "beats": [{"text": "指尖先抖，再抬眼"}]},
            "acting_status": "ready",
        }]}
        generated = {"shots": [{"id": "S1", "prompt_video": "重新生成后的新提示词", "dur": 4}]}
        out = studio.retain_production(previous, generated)
        shot = out["shots"][0]
        self.assertEqual(shot["prompt_video"], "重新生成后的新提示词", "新事实仍要更新")
        self.assertEqual(shot["keyframe"], {"item_id": "kf1"})
        self.assertEqual(shot["performance"]["beats"][0]["text"], "指尖先抖，再抬眼",
                         "⑤ 采用的表演不得被重新分镜抹掉")
        self.assertEqual(shot["acting_status"], "ready")

    def test_missing_keys_are_not_invented(self):
        out = studio.retain_production({"shots": [{"id": "S1"}]}, {"shots": [{"id": "S1"}]})
        self.assertNotIn("performance", out["shots"][0], "旧板没有表演时不得凭空造字段")


class NarratorPresenceTests(unittest.TestCase):
    def test_narrator_aliases_never_appear_on_stage(self):
        actors = {
            "c": {"name": "主角", "pos": [0, 2]},
            "narrator": {"name": "旁白", "pos": [1, 1]},
            "p1": {"name": "画外音", "pos": [2, 1]},
            "p2": {"name": "解说", "pos": [3, 1]},
        }
        shot = {"lines": [{"speaker": "c", "text": "台词"}], "action": "主角推门", "prompt": ""}
        got = actor_positions(shot, actors)
        self.assertEqual(set(got), {"c"}, f"旁白系别名不得当在场人物: {sorted(got)}")

    def test_narrator_aliases_excluded_in_fallback_all_cast(self):
        """无台词无点名的镜头走"全角色回退"：老代码只挡字面量 narrator，
        「画外音/解说」会被当在场人物画进俯视图——这条是判别用例。"""
        actors = {
            "c": {"name": "主角", "pos": [0, 2]},
            "p1": {"name": "画外音", "pos": [2, 1]},
            "p2": {"name": "解说", "pos": [3, 1]},
        }
        got = actor_positions({"lines": [], "action": "", "prompt": ""}, actors)
        self.assertEqual(set(got), {"c"}, f"全角色回退里不该有旁白: {sorted(got)}")

    def test_real_actor_named_by_line_still_counts(self):
        actors = {"c": {"name": "主角", "pos": [0, 2]}, "v": {"name": "配", "pos": [1, 2]}}
        shot = {"lines": [{"speaker": "v", "text": "台词"}], "action": "", "prompt": ""}
        self.assertEqual(set(actor_positions(shot, actors)), {"v"})


if __name__ == "__main__":
    unittest.main()
