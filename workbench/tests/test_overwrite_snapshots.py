# -*- coding: utf-8 -*-
"""N63 族补漏回归：覆写型产物在写盘前必须留版本快照（台词脚本 / style.json / 素材图索引 / 构想.txt）。

行为测 skill_lib.set_project_style（② 生图风格与 E10 显式选择都存这里，用户手挑的数据）；
其余站点在 CLI 主流程深处、要造整项目输入，改测"写入前确有 snapshot 调用"这一约定，
防止将来有人把这几行删掉又静默退化成不可恢复覆写。
"""
import io
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "workbench" / "tools"
sys.path.insert(0, str(TOOLS))

import skill_lib as SK  # noqa: E402


class StyleSaveSnapshotTests(unittest.TestCase):
    def test_set_project_style_snapshots_previous_content(self):
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td) / "P1"
            (proj / "剧本").mkdir(parents=True)
            style_p = proj / "剧本" / "style.json"
            style_p.write_text(json.dumps({"image": "cinematic-real"}, ensure_ascii=False),
                               encoding="utf-8")

            SK.set_project_style(str(proj), {"image": "ink-wash", "storyboard": "auto"})

            self.assertEqual(json.loads(style_p.read_text(encoding="utf-8")),
                             {"image": "ink-wash", "storyboard": "auto"})
            vd = proj / "剧本" / ".versions"
            snaps = sorted(x.name for x in vd.iterdir()) if vd.is_dir() else []
            self.assertTrue(snaps, "style.json 被覆写却没有快照，用户手挑的风格选择无从回滚")
            old = [json.loads((vd / s).read_text(encoding="utf-8")) for s in snaps]
            self.assertIn({"image": "cinematic-real"}, old)

    def test_first_save_without_existing_file_needs_no_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            proj = Path(td) / "P2"
            SK.set_project_style(str(proj), {"image": "auto"})
            self.assertTrue((proj / "剧本" / "style.json").is_file())
            self.assertFalse((proj / "剧本" / ".versions").exists())


SITES = [
    ("merge_lines.py", "out"),
    ("attribute_speakers.py", "sc_out"),
    ("attribute_speakers.py", "scr_p"),
    ("creation_pipeline.py", "idea_path"),
    ("gen_asset_images.py", "idx_path"),
]


class OverwriteSnapshotContractTests(unittest.TestCase):
    def test_writes_are_preceded_by_snapshot(self):
        for fn, var in SITES:
            with io.open(TOOLS / fn, encoding="utf-8") as fh:
                src = fh.read().splitlines()
            hits = [i for i, l in enumerate(src)
                    if re.search(r'(json\.dump\([^,]*,\s*open\(%s,\s*"w"|open\(%s,\s*"w")' % (var, var), l)]
            self.assertTrue(len(hits) >= 1, "%s 里找不到对 %s 的覆写点，站点已搬家请同步本测试" % (fn, var))
            for i in hits:
                window = "\n".join(src[max(0, i - 10):i])
                self.assertRegex(window, r'(versions|_V)\.snapshot\(\s*%s\s*\)' % re.escape(var),
                                 "%s 覆写 %s 前没有 versions.snapshot（不可回滚的覆写）" % (fn, var))


class SilentLossGuardTests(unittest.TestCase):
    """两处"承诺失效必须留痕"：断点写不进去=重跑重复计费；表演上下文没进包=⑦ 静默丢表演段。

    这两处都在长流程深处（AI 逐镜分析 / 创作包组装），造真跑代价高且有外部依赖；
    锁的是"except 分支不得退回裸 pass"这一约定。
    """

    GUARDS = [
        ("analyze_film.py", "逐镜断点未能落盘"),
        ("creation_pipeline.py", "表演上下文未写入创作包"),
    ]

    def test_swallow_sites_report_a_warning(self):
        for fn, needle in self.GUARDS:
            src = (TOOLS / fn).read_text(encoding="utf-8")
            self.assertIn(needle, src, fn + " 的落盘/注入失败又变回静默 pass 了")


if __name__ == "__main__":
    unittest.main()
