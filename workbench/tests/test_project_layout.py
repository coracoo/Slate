# -*- coding: utf-8 -*-
"""project_layout 迁移的离线回归：目录改名 + 字符串改写 + 幂等 + 新路径防腐蚀。"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workbench" / "tools"))

import project_layout as PL  # noqa: E402


def _write(proj: Path, rel: str, content: str) -> None:
    path = proj / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class ProjectLayoutTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.proj = Path(self.td.name) / "P"
        self.proj.mkdir()

    def test_full_migration_and_idempotent(self):
        _write(self.proj, "素材/v.mp4", "v")
        _write(self.proj, "资产/资产图.json", json.dumps({"人物": {"a": {"path": "资产/人物/a.png"}}}, ensure_ascii=False))
        _write(self.proj, "剧本/人物.json", json.dumps({"characters": []}, ensure_ascii=False))
        _write(self.proj, "台词脚本.json", json.dumps({"lines": []}, ensure_ascii=False))
        _write(self.proj, "帧/每秒/frames_manifest.json", json.dumps({"frames": []}, ensure_ascii=False))
        _write(self.proj, "创作/表演草稿_剧本_E1/x.json", "{}")
        _write(self.proj, "创作/创作包_剧本_E1/manifest.json", json.dumps({"d": "创作/平面图_剧本_E1"}, ensure_ascii=False))
        _write(self.proj, "创作/creation.json", json.dumps({"refs": ["素材/角色参考/甲.png", "资产/人物/a.png"]}, ensure_ascii=False))

        log = PL.migrate_project(str(self.proj))
        self.assertTrue(any("素材/ → 拉片素材/" in x for x in log))
        self.assertTrue(self.proj.joinpath("拉片素材/v.mp4").is_file())
        self.assertTrue(self.proj.joinpath("素材/素材图.json").is_file())
        self.assertTrue(self.proj.joinpath("素材/人物.json").is_file())
        self.assertTrue(self.proj.joinpath("台词/台词脚本.json").is_file())
        self.assertTrue(self.proj.joinpath("逐帧/每秒/frames_manifest.json").is_file())
        self.assertTrue(self.proj.joinpath("演员/表演草稿_剧本_E1/x.json").is_file())
        self.assertTrue(self.proj.joinpath("推演/创作包_剧本_E1/manifest.json").is_file())

        index = json.loads(self.proj.joinpath("素材/素材图.json").read_text(encoding="utf-8"))
        self.assertEqual(index["人物"]["a"]["path"], "素材/人物/a.png")
        creation = json.loads(self.proj.joinpath("创作/creation.json").read_text(encoding="utf-8"))
        self.assertEqual(creation["refs"], ["拉片素材/角色参考/甲.png", "素材/人物/a.png"])
        pkg = json.loads(self.proj.joinpath("推演/创作包_剧本_E1/manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(pkg["d"], "推演/平面图_剧本_E1")

        # 幂等：二跑零动作，且新"素材/"（资产）路径不被误改成拉片素材
        self.assertEqual(PL.migrate_project(str(self.proj)), [])
        creation2 = json.loads(self.proj.joinpath("创作/creation.json").read_text(encoding="utf-8"))
        self.assertEqual(creation2["refs"], ["拉片素材/角色参考/甲.png", "素材/人物/a.png"])

    def test_fix_strings_flag_forces_rewrite(self):
        """早期版本已迁目录但没改字符串的项目：--fix-strings 补改。"""
        _write(self.proj, "素材/素材图.json", json.dumps({"人物": {"a": {"path": "资产/人物/a.png"}}}, ensure_ascii=False))
        _write(self.proj, "拉片素材/v.mp4", "v")
        self.assertEqual(PL.migrate_project(str(self.proj)), [])
        PL.migrate_project(str(self.proj), fix_strings=True)
        index = json.loads(self.proj.joinpath("素材/素材图.json").read_text(encoding="utf-8"))
        self.assertEqual(index["人物"]["a"]["path"], "素材/人物/a.png")

    def test_coexisting_old_and_new_not_merged(self):
        _write(self.proj, "素材/v.mp4", "v")
        _write(self.proj, "拉片素材/old.mp4", "o")
        log = PL.migrate_project(str(self.proj))
        self.assertTrue(any("并存" in x for x in log))
        self.assertTrue(self.proj.joinpath("素材/v.mp4").is_file())


if __name__ == "__main__":
    unittest.main()
