# -*- coding: utf-8 -*-
"""E12 前半 证据索引截断回归（update.md 三、3.3 P1 区）。

旧行为：evidence_rows 最多取前 96 条×180 字——长剧本后段的人物/道具拿不到合法
EV 编号，而提取提示词强制每项引用有效证据 ID，落不进去的资产被证据门控丢弃。
修复：① 全文按约 1500 字均匀分块逐块采样，任何文本区间都有证据条目覆盖；
② ID 确定性顺编（同一文本多次调用同位置同 ID）；③ evidence_report 带覆盖率
信息，提取提示词如实告知覆盖范围；④ 条目上限随文本长度自适应（每 1500 字至少
1 条），单条 180 字不变。
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workbench" / "tools"))

import prompt_modules as PM


def _fake_script(scenes, pad=0):
    """构造超旧 96 条规模的假剧本：每场 3 行（场景标题/动作/台词），pad 拉长台词行。"""
    out = []
    for i in range(1, scenes + 1):
        out.append(f"第{i}场 废弃工厂 夜 内")
        out.append(f"角色甲走进车间，打开第{i}号储物柜。")
        out.append(f"角色乙：这是第{i}次见面了{'，手里攥着一张旧照片' * pad}。")
    return "\n".join(out)


class EvidenceRowsCoverageTests(unittest.TestCase):
    """长文本分块全覆盖与自适应上限。"""

    def test_short_text_keeps_sequential_full_collection(self):
        # 短文本全量收录，ID 顺编与旧版一致（EV001 起）
        rows = PM.evidence_rows("蛛丝缓冲茧包裹遥控炸弹。")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "EV001")
        self.assertEqual(rows[0]["text"], "蛛丝缓冲茧包裹遥控炸弹。")
        self.assertEqual(rows[0]["chunk"], 1)
        self.assertEqual(rows[0]["offset"], 0)

    def test_long_text_tail_is_covered(self):
        # 120 场×3 句=360 句：采样后条数 ≤96，但剧本后段（末尾分块）必须有条目
        text = _fake_script(120)
        rows = PM.evidence_rows(text)
        self.assertLessEqual(len(rows), 96)
        report = PM.evidence_report(text)
        self.assertEqual(rows[-1]["chunk"], report["coverage"]["chunks"])
        self.assertGreater(rows[-1]["offset"], len(text) * 0.5)
        # 每个分块都有条目（全文无盲区）
        self.assertEqual(report["coverage"]["covered_chunks"], report["coverage"]["chunks"])

    def test_item_cap_adapts_to_text_length(self):
        # 超长剧本（>15 万字）：上限突破旧 96 硬截断，约每 1500 字 1 条
        text = _fake_script(400, pad=35)
        self.assertGreater(len(text), 1500 * 96)
        rows = PM.evidence_rows(text)
        self.assertGreater(len(rows), 96)
        report = PM.evidence_report(text)
        self.assertEqual(report["coverage"]["row_count"], len(rows))
        self.assertEqual(report["coverage"]["covered_chunks"], report["coverage"]["chunks"])

    def test_row_text_still_capped_at_180(self):
        text = _fake_script(2, pad=40)  # 单行超 180 字
        rows = PM.evidence_rows(text)
        self.assertTrue(all(len(r["text"]) <= 180 for r in rows))

    def test_ids_stable_across_calls(self):
        # ID 全局稳定：同一文本多次调用，同位置同 ID（含 chunk/offset 完全一致）
        text = _fake_script(200)
        a = PM.evidence_rows(text)
        b = PM.evidence_rows(text)
        self.assertEqual(a, b)
        ids = [r["id"] for r in a]
        self.assertEqual(len(ids), len(set(ids)))  # ID 唯一
        self.assertEqual(ids[0], "EV001")

    def test_empty_text_returns_empty(self):
        self.assertEqual(PM.evidence_rows(""), [])
        report = PM.evidence_report("")
        self.assertEqual(report["rows"], [])
        self.assertEqual(report["coverage"]["row_count"], 0)


class EvidenceReportTests(unittest.TestCase):
    """覆盖率报告字段与提示词如实告知。"""

    def test_report_fields_and_values(self):
        text = _fake_script(120)
        report = PM.evidence_report(text)
        cov = report["coverage"]
        for key in ("total_chars", "covered_chars", "coverage_ratio", "chunks",
                    "covered_chunks", "row_count", "sentence_count", "sampled"):
            self.assertIn(key, cov)
        self.assertEqual(cov["total_chars"], len(text))
        self.assertEqual(cov["sentence_count"], 360)
        self.assertTrue(cov["sampled"])                       # 长剧本发生采样裁剪
        self.assertLess(cov["coverage_ratio"], 1.0)           # 逐字收录比例 < 100%
        self.assertGreater(cov["coverage_ratio"], 0.0)

    def test_short_text_not_sampled(self):
        report = PM.evidence_report("甲走进车间。乙：来了。")
        self.assertFalse(report["coverage"]["sampled"])
        self.assertEqual(report["coverage"]["row_count"], report["coverage"]["sentence_count"])

    def test_evidence_block_reports_coverage_honestly(self):
        # 提取提示词的证据块：采样文本带覆盖说明，短文本标全量收录
        _sys_p, user = PM.characters_prompt(_fake_script(120))
        self.assertIn("证据覆盖", user)
        self.assertIn("采样", user)
        self.assertIn("EV001", user)
        _s2, user2 = PM.props_prompt("白咲蛛绪用蛛丝包裹遥控炸弹。")
        self.assertIn("全量收录", user2)


class EvidenceGateCompatTests(unittest.TestCase):
    """消费方兼容：creation_pipeline 证据门控接受新索引（含长剧本后段条目）。"""

    def test_gate_accepts_tail_evidence_id(self):
        import creation_pipeline
        text = _fake_script(120)
        rows = PM.evidence_rows(text)
        tail = rows[-1]  # 旧版 96 条截断时不存在的后段条目
        data, rejected = creation_pipeline._gate_extracted_data(
            "道具",
            {"props": [{"id": "locker", "name": "储物柜", "kind": "叙事",
                        "evidence_ids": [tail["id"]]}]},
            text, {})
        self.assertEqual(rejected, [])
        self.assertEqual(data["props"][0]["evidence_status"], "verified")
        self.assertEqual(data["props"][0]["evidence_ids"], [tail["id"]])

    def test_gate_still_rejects_unknown_evidence_id(self):
        import creation_pipeline
        data, rejected = creation_pipeline._gate_extracted_data(
            "道具",
            {"props": [{"id": "ghost", "name": "幽灵道具", "kind": "叙事",
                        "evidence_ids": ["EV999"]}]},
            "甲走进车间。", {})
        self.assertEqual(len(rejected), 1)
        self.assertEqual(data["props"], [])


if __name__ == "__main__":
    unittest.main()
