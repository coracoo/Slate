# -*- coding: utf-8 -*-
"""悬空/失图资产引用的可见性回归（09_蜘女 实测：@character:quantongban 被 11 镜引用却不在人物档案里）。

以前这两条路都静默：registry.resolve 抛错被 `except: pass` 吞、图文件不存在被 _existing 悄悄丢成 None，
结果提示词少了"身份锚点"却没有任何提示，模型只按文字画人 → 长相漂移，且没人知道是哪一镜。
"""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workbench" / "tools"))

import prompt_assembler as PA  # noqa: E402


def _make_project(td):
    proj = Path(td)
    (proj / "素材" / "人物").mkdir(parents=True)
    (proj / "素材" / "人物.json").write_text(json.dumps({"characters": [
        {"id": "hero", "name": "主角"}, {"id": "ghostless", "name": "缺图角色"}]}, ensure_ascii=False),
        encoding="utf-8")
    (proj / "素材" / "素材图.json").write_text(json.dumps({
        "人物": {"hero": {"path": "素材/人物/hero.png", "name": "主角"},
                 "ghostless": {"path": "素材/人物/ghostless.png", "name": "缺图角色"}}},
        ensure_ascii=False), encoding="utf-8")
    (proj / "素材" / "人物" / "hero.png").write_bytes(b"\x89PNG fake")   # 只有 hero 的图真的在
    return str(proj)


def _shot(refs):
    return {"id": "S7", "prompt_json": {"asset_refs": refs}}


class DanglingRefWarningTests(unittest.TestCase):
    def test_unresolvable_ref_is_reported_not_silently_dropped(self):
        with tempfile.TemporaryDirectory() as td:
            proj = _make_project(td)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rows = PA.resolve_shot_refs(_shot(["@character:nope_at_all"]), proj)
            log = buf.getvalue()
            self.assertEqual([r["path"] for r in rows], [], "解析不到的引用不该进提示词")
            self.assertIn("[警告]", log)
            self.assertIn("S7", log, "警告必须带镜号，否则 20 镜的日志里查不到是谁少了锚点")
            self.assertIn("@character:nope_at_all", log)

    def test_missing_image_file_is_reported(self):
        with tempfile.TemporaryDirectory() as td:
            proj = _make_project(td)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rows = PA.resolve_shot_refs(_shot(["@character:ghostless"]), proj)
            log = buf.getvalue()
            self.assertEqual([r["path"] for r in rows], [])
            self.assertIn("素材图不存在", log)
            self.assertIn("② 重新生成", log, "要给出路：这类缺图去 ② 重生成，不是去改分镜")

    def test_good_ref_still_resolves_without_noise(self):
        with tempfile.TemporaryDirectory() as td:
            proj = _make_project(td)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rows = PA.resolve_shot_refs(_shot(["@character:hero"]), proj)
            self.assertEqual([r["path"] for r in rows], ["素材/人物/hero.png"])
            self.assertNotIn("[警告]", buf.getvalue(), "正常解析不该刷警告")

    def test_narrator_stays_silent(self):
        """旁白按设计不建档，引用它不是破损，不能报悬空。"""
        with tempfile.TemporaryDirectory() as td:
            proj = _make_project(td)
            buf = io.StringIO()
            with redirect_stdout(buf):
                PA.resolve_shot_refs(_shot(["@character:narrator"]), proj)
            self.assertNotIn("narrator", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
