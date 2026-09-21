# -*- coding: utf-8 -*-
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workbench" / "tools"))


class AssetGlobalRefsTests(unittest.TestCase):
    def make_project(self):
        td = tempfile.TemporaryDirectory()
        root = Path(td.name)
        (root / "素材").mkdir()
        (root / "素材" / "人物.json").write_text(json.dumps({
            "characters": [{"id": "hero", "name": "主角", "role": "主角"}]
        }, ensure_ascii=False), encoding="utf-8")
        (root / "素材" / "场景.json").write_text(json.dumps({"scenes": [
            {"id": "room", "name": "教室", "image_prompt": "普通教室"}
        ]}, ensure_ascii=False), encoding="utf-8")
        (root / "素材" / "道具.json").write_text(json.dumps({"props": [
            {"id": "manual_note", "name": "便签", "image_prompt": "便签"}
        ]}, ensure_ascii=False), encoding="utf-8")
        return td, root

    def test_child_creation_accepts_existing_asset_refs(self):
        from asset_service import create_asset
        td, root = self.make_project()
        self.addCleanup(td.cleanup)
        result = create_asset(str(root), kind="prop", id="collar", name="项圈", prompt="黑色项圈",
                              parent_ref="@character:hero", related_refs=[])
        self.assertEqual(result["parent_ref"], "@character:hero")
        self.assertEqual(result.get("related_refs", []), [])

    def test_editing_child_does_not_increment_parent(self):
        from asset_service import create_asset, edit_asset, revision
        td, root = self.make_project()
        self.addCleanup(td.cleanup)
        create_asset(str(root), kind="prop", id="collar", name="项圈", prompt="黑色项圈", parent_ref="@character:hero")
        edit_asset(str(root), "@prop:collar", {"appearance": "蓝色"})
        self.assertEqual(revision(str(root), "@character:hero"), 1)
        self.assertEqual(revision(str(root), "@prop:collar"), 2)

    def test_edit_rejects_stale_revision(self):
        from asset_service import edit_asset
        td, root = self.make_project()
        self.addCleanup(td.cleanup)
        with self.assertRaisesRegex(ValueError, "资产版本已变化"):
            edit_asset(str(root), "@character:hero", {"name": "新主角"}, expected_revision=2)

    def test_prompt_refs_are_recorded_as_global_dependencies(self):
        from asset_service import edit_asset
        td, root = self.make_project()
        self.addCleanup(td.cleanup)
        edit_asset(str(root), "@scene:room", {
            "prompt": "教室内调用 @character:hero，保留场景内的 @character:hero",
            "related_refs": ["@prop:manual_note"],
        })
        saved = json.loads((root / "素材" / "场景.json").read_text(encoding="utf-8"))
        room = saved["scenes"][0]
        self.assertEqual(room["prompt_refs"], ["@character:hero"])
        self.assertEqual(room["related_refs"], ["@prop:manual_note", "@character:hero"])

    def test_editing_prompt_removes_old_automatic_ref_but_keeps_manual_ref(self):
        from asset_service import edit_asset
        td, root = self.make_project()
        self.addCleanup(td.cleanup)
        edit_asset(str(root), "@scene:room", {"prompt": "@character:hero"})
        edit_asset(str(root), "@scene:room", {"prompt": "普通空教室"})
        saved = json.loads((root / "素材" / "场景.json").read_text(encoding="utf-8"))
        room = saved["scenes"][0]
        self.assertNotIn("prompt_refs", room)
        self.assertNotIn("related_refs", room)


if __name__ == "__main__":
    unittest.main()
