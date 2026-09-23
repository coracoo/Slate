# -*- coding: utf-8 -*-
"""judge_plan（plan v1 语义闸）：三型裁决规则 + 双后端（mock）+ 降级注明。"""
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

import judge_plan as jp

PLAN = {"version": 1, "name": "军帐", "canvas": {"w": 12, "h": 9},
        "props": [{"id": "p1", "label": "长案", "shape": "rect", "center": [6, 4.5], "size": [3, 1]}],
        "actors": [{"id": "c", "name": "主角", "pos": [6, 6]}]}

PASS_RESULT = {"noul": {"path_clear": True, "openings_match": True, "props_grounded": True},
               "choice": {"layout": "合理"}, "score": {"consistency": "高"}, "reasons": []}


def chat_of(payload):
    """脚本化 LLM：payload dict → JSON 字符串。"""
    return lambda msgs: json.dumps(payload, ensure_ascii=False)


class VerdictTests(unittest.TestCase):
    def test_all_pass(self):
        self.assertEqual(jp.verdict(PASS_RESULT), {"ok": True, "reasons": []})
        # 勉强/中 也是通过档
        r = dict(PASS_RESULT, choice={"layout": "勉强"}, score={"consistency": "中"})
        self.assertTrue(jp.verdict(r)["ok"])

    def test_noul_false_rejects(self):
        r = json.loads(json.dumps(PASS_RESULT))
        r["noul"]["props_grounded"] = False
        v = jp.verdict(r)
        self.assertFalse(v["ok"])
        self.assertTrue(any("props_grounded" in x for x in v["reasons"]))

    def test_choice_and_score_reject(self):
        for layout in ("混乱", "不知道", None):
            r = json.loads(json.dumps(PASS_RESULT))
            r["choice"]["layout"] = layout
            with self.subTest(layout=layout):
                self.assertFalse(jp.verdict(r)["ok"])
        r = json.loads(json.dumps(PASS_RESULT))
        r["score"]["consistency"] = "低"
        self.assertFalse(jp.verdict(r)["ok"])
        # 非法值从严按打回
        r["score"]["consistency"] = "还行"
        self.assertFalse(jp.verdict(r)["ok"])


class VendorBackendTests(unittest.TestCase):
    def test_vendor_pass_and_normalize(self):
        out = jp.judge(PLAN, scene_desc="军帐", backend="vendor", chat_fn=chat_of(PASS_RESULT))
        self.assertTrue(out["ok"])
        self.assertEqual(out["backend"], "vendor")
        self.assertEqual(out["reasons"], [])

    def test_vendor_reject_carries_reasons(self):
        bad = json.loads(json.dumps(PASS_RESULT))
        bad["noul"]["path_clear"] = False
        bad["choice"]["layout"] = "混乱"
        out = jp.judge(PLAN, backend="vendor", chat_fn=chat_of(bad))
        self.assertFalse(out["ok"])
        self.assertEqual(len(out["reasons"]), 2)

    def test_vendor_tolerates_markdown_fence(self):
        def chat(msgs):
            return "```json\n" + json.dumps(PASS_RESULT, ensure_ascii=False) + "\n```"
        out = jp.judge(PLAN, backend="vendor", chat_fn=chat)
        self.assertTrue(out["ok"])

    def test_vendor_prompt_contains_schema_and_questions(self):
        seen = {}

        def chat(msgs):
            seen["msgs"] = msgs
            return json.dumps(PASS_RESULT, ensure_ascii=False)
        jp.judge(PLAN, scene_desc="军帐，长案居中", backend="vendor", chat_fn=chat)
        user = seen["msgs"][1]["content"]
        self.assertIn("军帐，长案居中", user)
        self.assertIn("path_clear", user)
        self.assertIn("合理", user)
        self.assertIn('"noul"', seen["msgs"][0]["content"])

    def test_broken_output_raises(self):
        with self.assertRaises(ValueError):
            jp.judge(PLAN, backend="vendor", chat_fn=lambda msgs: "不是JSON{")


class TypesafeBackendTests(unittest.TestCase):
    def _fake_sdk(self, result):
        mod = types.ModuleType("typesafe")
        mod.system_one = lambda questions, subject: result
        return mod

    def test_typesafe_selected_with_key(self):
        with patch.dict(sys.modules, {"typesafe": self._fake_sdk(PASS_RESULT)}), \
             patch.dict("os.environ", {"TYPESAFE_API_KEY": "ts-key"}):
            backend, note = jp.select_backend()
            self.assertEqual((backend, note), ("typesafe", None))
            out = jp.judge(PLAN, scene_desc="军帐")
            self.assertTrue(out["ok"])
            self.assertEqual(out["backend"], "typesafe")

    def test_key_without_sdk_degrades_with_note(self):
        # 本环境未装 typesafe-sdk：配了 key 也应降级默认后端并注明
        with patch.dict("os.environ", {"TYPESAFE_API_KEY": "ts-key"}), \
             patch.dict(sys.modules, {"typesafe": None}):
            backend, note = jp.select_backend()
            self.assertEqual(backend, "vendor")
            self.assertIn("降级", note)

    def test_typesafe_call_failure_falls_back(self):
        def boom(questions, subject):
            raise RuntimeError("SDK 内部错误")
        mod = types.ModuleType("typesafe")
        mod.system_one = boom
        with patch.dict(sys.modules, {"typesafe": mod}):
            out = jp.judge(PLAN, backend="typesafe", chat_fn=chat_of(PASS_RESULT))
        self.assertTrue(out["ok"])
        self.assertEqual(out["backend"], "vendor")
        self.assertIn("降级", out["note"])


if __name__ == '__main__':
    unittest.main()
