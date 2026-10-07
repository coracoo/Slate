# -*- coding: utf-8 -*-
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workbench" / "tools"))

import preproduction_flow


class PreproductionFlowTests(unittest.TestCase):
    def test_empty_project_exposes_ordered_stage_contract_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            result = preproduction_flow.inspect_project(project)
            self.assertEqual([row["id"] for row in result["stages"]],
                             ["concept", "story_units", "episodes", "episode_scripts", "assets", "storyboard"])
            self.assertEqual(result["stages"][0]["status"], "ready")
            self.assertEqual(result["stages"][1]["status"], "blocked")
            self.assertEqual(list(project.iterdir()), [])

    def test_imported_script_can_advance_without_story_unit_anchor(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            script = project / "剧本"
            script.mkdir()
            (script / "剧本.txt").write_text("导入的正文", encoding="utf-8")
            (script / "分集.json").write_text(json.dumps({"mode": "imported", "episodes": [{"id": "E1"}]}), encoding="utf-8")
            result = {row["id"]: row for row in preproduction_flow.inspect_project(project)["stages"]}
            self.assertEqual(result["concept"]["status"], "done")
            self.assertEqual(result["story_units"]["status"], "optional")
            self.assertEqual(result["episodes"]["status"], "done")
            self.assertEqual(result["episode_scripts"]["status"], "done")
            self.assertEqual(result["assets"]["status"], "ready")

    def test_anchored_generated_project_reports_facts_without_modification(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            script = project / "剧本"
            script.mkdir()
            (script / "构想.txt").write_text("一句话构想", encoding="utf-8")
            (script / "大纲.json").write_text(json.dumps({"anchor_rev": 2}), encoding="utf-8")
            (script / "分集.json").write_text(json.dumps({"episodes": [{"id": "E1", "text": "分场正文"}]}), encoding="utf-8")
            (script / "埋线.json").write_text(json.dumps({"foreshadows": []}), encoding="utf-8")
            before = {str(path): path.read_bytes() for path in project.rglob("*") if path.is_file()}
            result = {row["id"]: row for row in preproduction_flow.inspect_project(project)["stages"]}
            after = {str(path): path.read_bytes() for path in project.rglob("*") if path.is_file()}
            self.assertEqual(result["story_units"]["status"], "done")
            self.assertEqual(result["story_units"]["revision"], 2)
            self.assertEqual(result["episode_scripts"]["status"], "done")
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
