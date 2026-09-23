# -*- coding: utf-8 -*-
"""split_units_by_scene 回归：LLM 跨场景/空引用混组的确定性切分（N88）。"""
import os
import sys
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.abspath(os.path.join(HERE, "..", "tools"))
CORE = os.path.abspath(os.path.join(HERE, "..", "..", "previs_system", "tools"))
for p in (TOOLS, CORE):
    if p not in sys.path:
        sys.path.insert(0, p)
import creation_pipeline as cp


def _cfg():
    return {"shots": [
        {"id": "S1", "dur": 3, "scene_ref": "@scene:a"},
        {"id": "S2", "dur": 3, "scene_ref": "@scene:a"},
        {"id": "S3", "dur": 3, "scene_ref": "@scene:b"},
        {"id": "S4", "dur": 3, "scene_ref": ""},
        {"id": "S5", "dur": 3, "scene_ref": ""},
    ]}


class SplitUnitsTests(unittest.TestCase):
    def test_mixed_group_split_by_scene(self):
        cfg = _cfg()
        units = [{"id": "v-1", "shot_ids": ["S1", "S2", "S3"], "prompt_video": "整段", "title": "t"}]
        out = cp.split_units_by_scene(cfg, units)
        self.assertEqual([[i for i in u["shot_ids"]] for u in out], [["S1", "S2"], ["S3"]])
        self.assertEqual(out[0]["id"], "v-1")            # 首段继承原 id
        self.assertNotEqual(out[1]["id"], "v-1")
        self.assertEqual(out[0]["prompt_video"], "整段")  # 提示词继承
        self.assertEqual(out[1]["scene_ref"], "@scene:b")

    def test_empty_scene_refs_become_singletons(self):
        cfg = _cfg()
        units = [{"id": "v-1", "shot_ids": ["S4", "S5"], "prompt_video": "x"}]
        out = cp.split_units_by_scene(cfg, units)
        self.assertEqual([[i for i in u["shot_ids"]] for u in out], [["S4"], ["S5"]])

    def test_valid_group_untouched(self):
        cfg = _cfg()
        units = [{"id": "v-1", "shot_ids": ["S1", "S2"], "prompt_video": "x"}]
        out = cp.split_units_by_scene(cfg, units)
        self.assertEqual(len(out), 1)
        self.assertIs(out[0], units[0])

    def test_single_shot_untouched(self):
        cfg = _cfg()
        units = [{"id": "v-1", "shot_ids": ["S4"], "prompt_video": "x"}]
        self.assertEqual(len(cp.split_units_by_scene(cfg, units)), 1)


if __name__ == "__main__":
    unittest.main()
