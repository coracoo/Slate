# -*- coding: utf-8 -*-
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workbench" / "tools"))
sys.path.insert(0, str(ROOT / "previs_system" / "tools"))


class ActorAssetRefTests(unittest.TestCase):
    def setUp(self):
        self.board = {
            "actors": {
                "hero": {"name": "白咲蛛绪"},
                "student": {"name": "佐藤陆"},
            },
            "shots": [{
                "id": "S1", "dur": 3,
                "scene": "room", "shot_size": "中景", "move": "跟", "angle": "平视",
                "prompt": "从天花板坠落并砸向课桌",
                "speaker": "hero", "target": "student",
                "prop_refs": ["legs"],
                "lines": [{"speaker": "hero", "line": "呜哇！"}],
            }],
        }

    def test_build_request_contains_stable_asset_refs_without_descriptions(self):
        from actor_pipeline import build_request
        request = build_request(self.board, "S1")
        self.assertIn("@character:hero", request["asset_refs"])
        self.assertIn("@character:student", request["asset_refs"])
        self.assertIn("@scene:room", request["asset_refs"])
        self.assertIn("@prop:legs", request["asset_refs"])
        self.assertNotIn("appearance", str(request))
        self.assertNotIn("sheet_prompt", str(request))

    def test_compile_shot_returns_structured_prompt_and_refs(self):
        from prompt_compiler import compile_shot
        out = compile_shot(self.board, "S1", media_type="image")
        self.assertEqual(out["prompt_json"]["schema"], "shot-prompt-v1")
        self.assertIn("@character:hero", out["asset_refs"])
        self.assertIn("@scene:room", out["asset_refs"])
        self.assertIn("@prop:legs", out["asset_refs"])
        self.assertNotIn("appearance", str(out["prompt_json"]))


if __name__ == "__main__":
    unittest.main()


