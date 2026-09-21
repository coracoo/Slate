# -*- coding: utf-8 -*-
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workbench" / "tools"))


class RebuildProductionTests(unittest.TestCase):
    def make_project(self):
        td = tempfile.TemporaryDirectory()
        root = Path(td.name)
        (root / "剧本").mkdir()
        (root / "分镜").mkdir()
        (root / "素材" / "人物").mkdir(parents=True)
        (root / "素材" / "场景").mkdir(parents=True)
        (root / "素材" / "道具").mkdir(parents=True)
        (root / "素材" / "人物.json").write_text(json.dumps({
            "characters": [{"id": "hero", "name": "主角", "sheet_prompt": "稳定外观"}]
        }, ensure_ascii=False), encoding="utf-8")
        (root / "素材" / "场景.json").write_text(json.dumps({
            "scenes": [{"id": "room", "name": "教室", "image_prompt": "普通教室"}]
        }, ensure_ascii=False), encoding="utf-8")
        (root / "素材" / "道具.json").write_text(json.dumps({"props": []}, ensure_ascii=False), encoding="utf-8")
        (root / "分镜" / "剧本_E1.json").write_text(json.dumps({
            "episode_id": "E1", "shots": [{
                "id": "S1", "scene_ref": "@scene:room", "actor_refs": ["@character:hero"],
                "asset_refs": ["@scene:room", "@character:hero"],
                "shot_size": "中景", "camera_move": "固定", "angle": "平视",
                "action": "主角抬头"
            }]
        }, ensure_ascii=False), encoding="utf-8")
        return td, root

    def test_prompt_only_rebuild_does_not_call_vendor(self):
        from rebuild_production import rebuild_prompts
        td, root = self.make_project()
        self.addCleanup(td.cleanup)
        result = rebuild_prompts(str(root), episode="E1")
        self.assertEqual(result["media_calls"], 0)
        self.assertEqual(result["updated_prompts"], 1)
        board = json.loads((root / "分镜" / "剧本_E1.json").read_text(encoding="utf-8"))
        shot = board["shots"][0]
        self.assertIn("prompt_image", shot)
        self.assertIn("prompt_video", shot)
        self.assertIn("asset_revisions", shot)
        self.assertNotEqual(shot["prompt_revisions"]["storyboard_image"], shot["prompt_revisions"]["video"])

    def test_affected_shots_follow_asset_dependency(self):
        from rebuild_production import affected_shots
        td, root = self.make_project()
        self.addCleanup(td.cleanup)
        self.assertEqual(affected_shots(str(root), ["@character:hero"]), ["S1"])


if __name__ == "__main__":
    unittest.main()
