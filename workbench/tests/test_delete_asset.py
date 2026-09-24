# -*- coding: utf-8 -*-
"""子素材图删除 / 版本删除回归（N89）。"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.abspath(os.path.join(HERE, "..", "tools"))
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)
import versions
import gen_asset_images


class DeleteAssetImageTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.proj = Path(self._td.name)
        (self.proj / "素材" / "场景").mkdir(parents=True)
        # 场景资产 + 母图 + 派生平面图条目
        json.dump({"scenes": [{"id": "a", "name": "中军帐", "source_episode_ids": ["E1"]}]},
                  open(self.proj / "素材" / "场景.json", "w", encoding="utf-8"), ensure_ascii=False)
        png = self.proj / "素材" / "场景" / "a__plan.png"
        png.write_bytes(b"fake-png")
        idx = {"场景": {
            "a": {"path": "素材/场景/a.png", "name": "中军帐", "source_episode_ids": ["E1"]},
            "a__plan": {"path": "素材/场景/a__plan.png", "name": "中军帐·平面图", "usage": "plan",
                        "parent_ref": "@scene:a", "source_episode_ids": ["E1"]},
        }}
        json.dump(idx, open(self.proj / "素材" / "素材图.json", "w", encoding="utf-8"), ensure_ascii=False)

    def tearDown(self):
        self._td.cleanup()

    def test_delete_removes_file_sidecar_and_index_entry(self):
        side = self.proj / "素材" / "场景" / "a__plan.png.comfy-task.json"
        side.write_text("{}", encoding="utf-8")
        rel = gen_asset_images.delete_asset_image(str(self.proj), "scene", "a__plan")
        self.assertEqual(rel.replace("\\", "/"), "素材/场景/a__plan.png")
        self.assertFalse((self.proj / "素材" / "场景" / "a__plan.png").exists())
        self.assertFalse(side.exists())
        idx = json.load(open(self.proj / "素材" / "素材图.json", encoding="utf-8"))
        self.assertNotIn("a__plan", idx["场景"])
        self.assertIn("a", idx["场景"])          # 母条目不受影响

    def test_delete_missing_raises(self):
        with self.assertRaises(ValueError):
            gen_asset_images.delete_asset_image(str(self.proj), "scene", "not_exists")

    def test_delete_bad_kind_raises(self):
        with self.assertRaises(ValueError):
            gen_asset_images.delete_asset_image(str(self.proj), "effect", "x")


class DeleteVersionTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.f = Path(self._td.name) / "board.json"
        self.f.write_text("v1", encoding="utf-8")
        versions.snapshot(str(self.f))
        self.f.write_text("v2", encoding="utf-8")

    def tearDown(self):
        self._td.cleanup()

    def test_delete_history_version(self):
        vs = versions.list_versions(str(self.f))
        old = [v for v in vs if not v["current"]]
        self.assertTrue(old)
        removed = versions.delete_version(str(self.f), old[0]["ts"])
        self.assertTrue(str(removed).endswith(".json"))
        remaining = versions.list_versions(str(self.f))
        self.assertNotIn(old[0]["ts"], [v["ts"] for v in remaining if not v["current"]])

    def test_delete_unknown_ts_raises(self):
        with self.assertRaises(FileNotFoundError):
            versions.delete_version(str(self.f), "19990101_000000")

    def test_delete_never_removes_current_file(self):
        # 守卫：delete_version 只匹配非当前快照；即使 ts 撞上当前 mtime，原位文件也必须完好
        vs = versions.list_versions(str(self.f))
        cur = next(v for v in vs if v["current"])
        versions.delete_version(str(self.f), cur["ts"])
        self.assertTrue(self.f.exists())
        self.assertEqual(self.f.read_text(encoding="utf-8"), "v2")


if __name__ == "__main__":
    unittest.main()
