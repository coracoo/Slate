# -*- coding: utf-8 -*-
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workbench" / "tools"))


class PromptStageCompilerTests(unittest.TestCase):
    def make_project(self):
        td = tempfile.TemporaryDirectory()
        root = Path(td.name)
        (root / "剧本").mkdir()
        (root / "素材" / "人物").mkdir(parents=True)
        (root / "素材" / "场景").mkdir(parents=True)
        (root / "素材" / "道具").mkdir(parents=True)
        (root / "素材" / "人物" / "hero.png").write_bytes(b"hero")
        (root / "素材" / "人物.json").write_text(json.dumps({
            "characters": [{"id": "hero", "name": "主角", "asset_revision": 3, "sheet_prompt": "外观"}]
        }, ensure_ascii=False), encoding="utf-8")
        (root / "素材" / "场景.json").write_text(json.dumps({
            "scenes": [{"id": "room", "name": "教室", "image_prompt": "教室"}]
        }, ensure_ascii=False), encoding="utf-8")
        (root / "素材" / "道具.json").write_text(json.dumps({"props": []}, ensure_ascii=False), encoding="utf-8")
        return td, root

    def test_storyboard_image_and_video_have_different_motion_rules(self):
        from prompt_compiler import compile_stage_prompt
        td, root = self.make_project()
        self.addCleanup(td.cleanup)
        shot = {"id": "S1", "asset_refs": ["@character:hero"], "action": "主角抬头"}
        image = compile_stage_prompt("storyboard_image", shot, str(root))
        video = compile_stage_prompt("video", shot, str(root))
        self.assertIn("单一静态关键帧", image["system_prompt"])
        self.assertIn("连续动作", video["system_prompt"])
        self.assertEqual(image["asset_refs"], video["asset_refs"])

    def test_compiled_prompt_uses_latest_asset_revision(self):
        from prompt_compiler import compile_stage_prompt
        td, root = self.make_project()
        self.addCleanup(td.cleanup)
        shot = {"id": "S1", "asset_refs": ["@character:hero"], "action": "主角抬头"}
        result = compile_stage_prompt("storyboard_image", shot, str(root))
        self.assertEqual(result["asset_revisions"]["@character:hero"], 3)


if __name__ == "__main__":
    unittest.main()
