# -*- coding: utf-8 -*-
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workbench" / "tools"))


class ProductionStateTests(unittest.TestCase):
    def make_project(self):
        td = tempfile.TemporaryDirectory()
        root = Path(td.name)
        (root / "剧本").mkdir()
        (root / "分镜").mkdir()
        (root / "素材").mkdir()
        (root / "素材" / "人物.json").write_text(json.dumps({
            "characters": [{"id": "hero", "name": "主角", "asset_revision": 3}]
        }, ensure_ascii=False), encoding="utf-8")
        (root / "素材" / "场景.json").write_text(json.dumps({
            "scenes": [{"id": "room", "name": "教室"}]
        }, ensure_ascii=False), encoding="utf-8")
        (root / "素材" / "道具.json").write_text(json.dumps({
            "props": [
                {"id": "collar", "name": "项圈", "parent_ref": "@character:hero", "related_refs": ["@prop:leg"]},
                {"id": "leg", "name": "机械腿", "parent_ref": "@character:hero"},
            ]
        }, ensure_ascii=False), encoding="utf-8")
        (root / "分镜" / "剧本_E1.json").write_text(json.dumps({
            "episode": "E1", "shots": [
                {"id": "S1", "asset_refs": ["@prop:collar"]},
                {"id": "S2", "asset_refs": ["@scene:room"]},
            ]
        }, ensure_ascii=False), encoding="utf-8")
        return td, root

    def test_missing_revision_defaults_to_one(self):
        from production_state import asset_revision
        self.assertEqual(asset_revision({"id": "hero"}), 1)

    def test_dependency_includes_parent_and_related_refs(self):
        from production_state import asset_dependency_refs
        td, root = self.make_project()
        self.addCleanup(td.cleanup)
        self.assertEqual(asset_dependency_refs(str(root), ["@prop:collar"]), [
            "@prop:collar", "@character:hero", "@prop:leg"
        ])

    def test_changed_asset_marks_only_dependent_shots_stale(self):
        from production_state import mark_stale_for_asset
        td, root = self.make_project()
        self.addCleanup(td.cleanup)
        result = mark_stale_for_asset(str(root), ["@character:hero"])
        self.assertEqual(result["shots"], ["S1"])
        self.assertIn("S1", result["reasons"])


if __name__ == "__main__":
    unittest.main()
