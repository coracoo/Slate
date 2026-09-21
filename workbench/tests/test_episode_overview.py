# -*- coding: utf-8 -*-
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workbench" / "tools"))


class EpisodeOverviewTests(unittest.TestCase):
    def test_overview_prompt_requires_review_style_summary(self):
        from prompt_modules import episode_overview_prompt
        system, user = episode_overview_prompt("早自习中通风口弹开", {"id": "E1"})
        self.assertIn("影评式概要", system)
        self.assertIn("cast_refs", system)
        self.assertEqual(user, "早自习中通风口弹开")

    def test_overview_update_does_not_change_episode_text(self):
        from creation_pipeline import merge_episode_overview
        before = "原始分场剧本"
        row = {"id": "E1", "text": before}
        merge_episode_overview(row, {"summary": "新概要", "cast_refs": ["@character:hero"]})
        self.assertEqual(row["text"], before)
        self.assertEqual(row["summary"], "新概要")
        self.assertEqual(row["cast_refs"], ["@character:hero"])


if __name__ == "__main__":
    unittest.main()
