# -*- coding: utf-8 -*-
"""拉片知识库（knowledge.py）确定性 bug 回归测试：
①正反打交替按完整角色标识比较（同前缀 ID 不漏判）；②片例项目名解析正确；
③同一影片多分析版本去重计数；④注入措辞如实表述来源数、不称"置信度"。"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workbench" / "tools"))

import knowledge


def _dialog_shots(speakers, size="近景", dur=4.0):
    """构造带台词的说话镜头序列。近景 + 4s 时长，避免误触发紧张快切/长镜台词轨等其他规则。"""
    return [
        {"id": f"S{i + 1:02d}", "duration": dur, "shot_size": size,
         "camera_move": "固定", "angle": "平视", "transition": "无",
         "dialogue": [{"speaker": sp, "text": f"台词{i + 1}"}]}
        for i, sp in enumerate(speakers)
    ]


def _plain_shots(n, **fields):
    """构造无台词普通镜头，fields 为逐镜统一覆盖的字段值。"""
    return [{"id": f"S{i + 1:02d}", "duration": 4.0, "shot_size": "中景",
             "camera_move": "固定", "angle": "平视", "transition": "无",
             **fields} for i in range(n)]


class KnowledgeBuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="knowledge_", dir=str(ROOT)))
        # build()/query() 读模块级 VIDEO/KB/UCARD 常量；重定向到临时目录，
        # 避免扫真实 projects/、覆写真实 skills.json、混入真实用户卡
        self._old = (knowledge.VIDEO, knowledge.KB, knowledge.UCARD)
        knowledge.VIDEO = str(self.tmp)
        knowledge.KB = str(self.tmp / "kb" / "skills.json")
        knowledge.UCARD = str(self.tmp / "kb" / "user_cards.json")

    def tearDown(self):
        knowledge.VIDEO, knowledge.KB, knowledge.UCARD = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_analysis(self, proj, ver, shots, **meta):
        d = self.tmp / "projects" / proj / "拉片" / ver
        d.mkdir(parents=True, exist_ok=True)
        doc = {"name": meta.pop("name", ver), "version": meta.pop("version", 1),
               "created_at": meta.pop("created_at", ""), "shots": shots}
        doc.update(meta)
        (d / "analysis.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def _skill(skills, name):
        for s in skills:
            if s["skill"] == name:
                return s
        return None

    def test_seq_compares_full_speaker_ids(self):
        # 同前缀角色 ID 的交替必须识别为正反打（修复前只比首字符，char_a/char_b 被判同一人）
        self.assertEqual(knowledge._seq(["char_a", "char_b", "char_a", "char_b"]), 4)
        self.assertEqual(knowledge._seq(["角色甲", "角色乙", "角色甲"]), 3)
        # 同一人连说不算交替
        self.assertEqual(knowledge._seq(["char_a", "char_a", "char_b"]), 2)
        self.assertEqual(knowledge._seq(["char_a", "char_a"]), 0)

    def test_dialog_shot_reverse_same_prefix_end_to_end(self):
        # 端到端：char_a/char_b 同前缀 ID 的正反打段能归纳出条目
        self._write_analysis("测试项目", "v1",
                             _dialog_shots(["char_a", "char_b", "char_a", "char_b"]))
        skills = knowledge.build()
        hit = self._skill(skills, "对话正反打")
        self.assertIsNotNone(hit)
        self.assertEqual(hit["count"], 1)
        self.assertIn("测试项目/v1", hit["example"])

    def test_split_analysis_path(self):
        # projects/<项目>/拉片/<版本>/analysis.json 的项目名不能取成字面量"拉片"
        cases = [
            (r"J:\desk\video\projects\01_买瓜\拉片\v1\analysis.json", "01_买瓜", "v1"),
            ("/home/u/video/projects/山海异兽/拉片/2026版/analysis.json", "山海异兽", "2026版"),
            # 项目本身叫"拉片"的极端情况：锚定末尾的"拉片"目录而非第一次出现
            ("projects/拉片/拉片/v2/analysis.json", "拉片", "v2"),
        ]
        for path, proj, ver in cases:
            self.assertEqual(knowledge._split_analysis_path(path), (proj, ver))

    def test_load_all_shots_returns_real_project_name(self):
        self._write_analysis("买瓜项目", "版本甲", _dialog_shots(["a", "b", "a"]))
        rows = knowledge._load_all_shots()
        self.assertEqual(len(rows), 1)
        proj, ver, _shots, _meta = rows[0]
        self.assertEqual(proj, "买瓜项目")
        self.assertEqual(ver, "版本甲")

    def test_multi_versions_deduped_by_film_source(self):
        # 同一影片（同 source）的多个分析版本只计 1 个来源；另一部影片另计 1 个
        shots = _dialog_shots(["甲", "乙", "甲", "乙"])
        self._write_analysis("项目A", "甲片", shots,
                             source="D:/src/甲片.mp4", created_at="2026-09-01 10:00")
        self._write_analysis("项目A", "甲片_v2", shots,
                             source="D:/src/甲片.mp4", created_at="2026-09-02 10:00")
        self._write_analysis("项目A", "乙片", shots,
                             source="D:/src/乙片.mp4", created_at="2026-09-01 11:00")
        skills = knowledge.build()
        hit = self._skill(skills, "对话正反打")
        self.assertIsNotNone(hit)
        self.assertEqual(hit["count"], 2)  # 修复前为 3（版本重复累加）
        # 片例用真实项目名（不是"拉片"），且代表版本取最新一版
        self.assertIn("项目A/甲片_v2", hit["example"])
        self.assertNotIn("拉片/", hit["example"])

    def test_multi_versions_deduped_by_name_fallback(self):
        # 无 source 字段时按分析名去 _vN 后缀归并
        shots = _dialog_shots(["甲", "乙", "甲", "乙"])
        self._write_analysis("项目B", "甲片", shots, created_at="2026-09-01 10:00")
        self._write_analysis("项目B", "甲片_v2", shots, created_at="2026-09-03 10:00")
        skills = knowledge.build()
        hit = self._skill(skills, "对话正反打")
        self.assertIsNotNone(hit)
        self.assertEqual(hit["count"], 1)

    def test_prompt_wording_reports_sources_not_confidence(self):
        # 注入措辞如实写"来自 N 部片/场景的拉片归纳"，不把出现次数称为"置信"；用户卡如实标注
        kb = {"version": 1, "skills": [
            {"id": "对话正反打", "skill": "对话正反打", "trigger": ["对话"],
             "prescription": "处方甲", "example": "项目A/甲片_v2 S01", "count": 2}]}
        uc = {"cards": [
            {"id": "u1", "skill": "对话节奏", "trigger": ["对话"],
             "prescription": "处方乙", "example": "", "count": 1, "source": "user"}]}
        Path(knowledge.KB).parent.mkdir(parents=True, exist_ok=True)
        Path(knowledge.KB).write_text(json.dumps(kb, ensure_ascii=False), encoding="utf-8")
        Path(knowledge.UCARD).write_text(json.dumps(uc, ensure_ascii=False), encoding="utf-8")
        import prompt_modules
        text = prompt_modules._knowledge("对话")
        self.assertIn("来自 2 部片/场景的拉片归纳", text)
        self.assertIn("用户经验卡", text)
        self.assertNotIn("置信", text)

    # ---------- 「不确定」/空值镜头不进归纳（analyze_film VOCAB 合法出口适配） ----------

    def test_uncertain_shot_size_skipped_not_breaking_dialogue(self):
        # 景别「不确定」/空的对白镜跳过且不打断交替段：近/近/(无证据)/近/近 仍归纳出正反打
        for bad in ("不确定", ""):
            with self.subTest(bad=bad):
                shots = _dialog_shots(["char_a", "char_b", "char_a", "char_b", "char_a"])
                shots[2]["shot_size"] = bad
                self._write_analysis("项目C", "v1", shots)
                skills = knowledge.build()
                hit = self._skill(skills, "对话正反打")
                self.assertIsNotNone(hit)
                self.assertEqual(hit["count"], 1)

    def test_all_uncertain_shot_size_no_dialogue_card(self):
        # 全部对白镜景别「不确定」→ 无有效证据，不产对话正反打卡（不计入命中）
        shots = _dialog_shots(["char_a", "char_b", "char_a", "char_b"])
        for s in shots:
            s["shot_size"] = "不确定"
        self._write_analysis("项目D", "v1", shots)
        skills = knowledge.build()
        self.assertIsNone(self._skill(skills, "对话正反打"))

    def test_uncertain_transition_not_counted(self):
        # transition=「不确定」/空 不进转场语汇计数；正常非默认转场不受影响
        base = _dialog_shots(["a", "b", "a", "b"])
        # 影片一：全部「不确定」/空/默认 → 不产转场语汇卡
        s1 = [dict(s, transition=t) for s, t in zip(base, ["不确定", "", "不确定", "无"])]
        self._write_analysis("项目T", "全不确定", s1, source="x/t1.mp4")
        # 影片二：叠化×2 + 「不确定」×1 + 硬切×1 → 只统计叠化
        s2 = [dict(s, transition=t) for s, t in zip(base, ["叠化", "叠化", "不确定", "硬切"])]
        self._write_analysis("项目T", "正常叠化", s2, source="x/t2.mp4")
        skills = knowledge.build()
        hit = self._skill(skills, "转场语汇")
        self.assertIsNotNone(hit)
        self.assertEqual(hit["count"], 1)  # 只有影片二命中（修复前「不确定」×2 会替影片一产废卡）
        self.assertIn("「叠化」×2次", hit["example"])
        self.assertNotIn("不确定", hit["example"])

    def test_uncertain_angle_and_size_not_counted_as_hits(self):
        # angle=「不确定」不算仰视镜：4 仰视 + 2 不确定 = 有效 4 < 阈值 5 → 不命中；
        # 对照影片 5 镜全仰视 → 命中。shot_size=「不确定」也不算大场面定场。
        shots = [dict(s, angle="仰视") for s in _plain_shots(4)] + \
                [dict(s, angle="不确定") for s in _plain_shots(2)]
        for i, s in enumerate(shots):
            s["id"] = f"S{i + 1:02d}"
        self._write_analysis("项目U", "混不确定", shots, source="x/u1.mp4")
        self._write_analysis("项目U", "全仰视",
                             [dict(s, angle="仰视") for s in _plain_shots(5)], source="x/u2.mp4")
        self._write_analysis("项目U", "景别不确定",
                             [dict(s, shot_size="不确定", angle="不确定") for s in _plain_shots(3)],
                             source="x/u3.mp4")
        skills = knowledge.build()
        hit = self._skill(skills, "仰视压迫")
        self.assertIsNotNone(hit)
        self.assertEqual(hit["count"], 1)  # 只有对照影片命中
        self.assertIn("仰视×5镜", hit["example"])
        self.assertIsNone(self._skill(skills, "大场面定场"))


if __name__ == "__main__":
    unittest.main()
