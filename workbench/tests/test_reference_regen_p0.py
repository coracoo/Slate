# -*- coding: utf-8 -*-
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workbench" / "tools"))


class RegeneratedReferenceTests(unittest.TestCase):
    def test_regenerated_prompt_keeps_explicit_reference_frames(self):
        from prompt_assembler import reference_shot_for_prompt

        row = {"path": "创作/参考帧/E1S01/process.png", "role": "process", "time_seconds": 1.4}
        shot = {
            "id": "S01",
            "prompt_source": "regenerated",
            "reference_frames": [row],
            "scene": "room",
        }
        out = reference_shot_for_prompt(shot, "@character:hero 抬头", "image")
        self.assertEqual(out["reference_frames"], [row])


if __name__ == "__main__":
    unittest.main()
