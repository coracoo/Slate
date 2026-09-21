# -*- coding: utf-8 -*-
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workbench" / "tools"))


class EpisodeServiceTests(unittest.TestCase):
    def make_project(self):
        td = tempfile.TemporaryDirectory()
        root = Path(td.name)
        script = root / "剧本"
        script.mkdir()
        assets = root / "素材"
        (assets / "人物").mkdir(parents=True)
        (assets / "素材图.json").write_text(json.dumps({"人物": {
            "hero": {"name": "主角", "source_episode_ids": ["E1", "E2"]},
            "guest": {"name": "来客", "source_episode_ids": ["E1"]},
        }}, ensure_ascii=False), encoding="utf-8")
        (script / "分集.json").write_text(json.dumps({
            "mode": "generated",
            "episodes": [
                {"id": "E1", "title": "开端"},
                {"id": "E2", "title": "转折"},
            ],
        }, ensure_ascii=False), encoding="utf-8")
        (assets / "人物.json").write_text(json.dumps({"characters": [
            {"id": "hero", "name": "主角", "source_episode_ids": ["E1", "E2"]},
            {"id": "guest", "name": "来客", "source_episode_ids": ["E1"]},
            {"id": "manual", "name": "手工角色"},
        ]}, ensure_ascii=False), encoding="utf-8")
        (assets / "场景.json").write_text(json.dumps({"scenes": []}, ensure_ascii=False), encoding="utf-8")
        (assets / "道具.json").write_text(json.dumps({"props": []}, ensure_ascii=False), encoding="utf-8")
        return td, root

    def test_delete_episode_unlinks_sources_but_keeps_global_assets(self):
        from episode_service import delete_episode
        td, root = self.make_project()
        self.addCleanup(td.cleanup)
        result = delete_episode(str(root), "E1")
        self.assertEqual(result["remaining_episodes"], ["E2"])
        self.assertEqual(result["assets_unlinked"], 2)
        book = json.loads((root / "剧本" / "分集.json").read_text(encoding="utf-8"))
        self.assertEqual([item["id"] for item in book["episodes"]], ["E2"])
        self.assertEqual(book["rev"], 1)
        chars = json.loads((root / "素材" / "人物.json").read_text(encoding="utf-8"))["characters"]
        by_id = {item["id"]: item for item in chars}
        self.assertEqual(by_id["hero"]["source_episode_ids"], ["E2"])
        self.assertNotIn("source_episode_ids", by_id["guest"])
        self.assertEqual(by_id["manual"]["name"], "手工角色")
        index = json.loads((root / "素材" / "素材图.json").read_text(encoding="utf-8"))["人物"]
        self.assertEqual(index["hero"]["source_episode_ids"], ["E2"])
        self.assertNotIn("source_episode_ids", index["guest"])

    def test_delete_episode_requires_existing_episode(self):
        from episode_service import delete_episode
        td, root = self.make_project()
        self.addCleanup(td.cleanup)
        with self.assertRaisesRegex(ValueError, "找不到分集"):
            delete_episode(str(root), "E9")


if __name__ == "__main__":
    unittest.main()
