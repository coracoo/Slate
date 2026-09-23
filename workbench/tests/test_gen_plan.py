# -*- coding: utf-8 -*-
"""gen_plan 生成闭环：mock LLM 输出 → validate → 判官打回循环 → 落盘（版本快照）；
--scene 场景绑定（描述取资产、scene_ref 写入）与 draft_missing_scene_plans 自动初稿。"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

import gen_plan
import judge_plan
from llm_openai import VendorError

GOOD = {"version": 1, "name": "军帐", "canvas": {"w": 12, "h": 9},
        "room": {"walls": [[0, 0], [12, 0], [12, 9], [0, 9]],
                 "openings": [{"wall": 1, "offset": 5.0, "width": 1.2, "kind": "door"}]},
        "props": [{"id": "p1", "label": "长案", "shape": "rect", "center": [6, 4.5], "size": [3, 1]}],
        "actors": [{"id": "c", "name": "主角", "pos": [6, 6], "facing": 270}]}
# 坐标越界（y=50 出画布）：validate 必打回
BAD_GEOM = json.loads(json.dumps(GOOD))
BAD_GEOM["props"] = [{"id": "p1", "label": "长案", "shape": "rect", "center": [6, 50], "size": [3, 1]}]

JUDGE_PASS = {"ok": True, "backend": "mock", "result": {}, "reasons": [], "note": None}
JUDGE_FAIL = {"ok": False, "backend": "mock", "result": {}, "reasons": ["Choice[layout] 打回：整体布局='混乱'"], "note": None}


def chat_script(rows):
    state = {"i": 0}

    def chat(msgs):
        out = rows[min(state["i"], len(rows) - 1)]
        state["i"] += 1
        return json.dumps(out, ensure_ascii=False) if isinstance(out, dict) else out
    return chat


def judge_script(rows):
    state = {"i": 0}

    def judge(plan):
        out = rows[min(state["i"], len(rows) - 1)]
        state["i"] += 1
        return dict(out)
    return judge


class GenerateLoopTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.proj = self._td.name
        os.makedirs(os.path.join(self.proj, "推演"), exist_ok=True)

    def tearDown(self):
        self._td.cleanup()

    def out_path(self, name="军帐"):
        return os.path.join(self.proj, "推演", f"平面图_{name}.plan.json")

    def test_validate_retry_then_judge_retry_then_pass(self):
        """第1轮 validate 打回（越界）→ 第2轮判官打回（布局混乱）→ 第3轮通过。"""
        r = gen_plan.generate(self.proj, "军帐", scene_desc="军帐，长案居中",
                              gen_chat=chat_script([BAD_GEOM, GOOD, GOOD]),
                              judge_fn=judge_script([JUDGE_FAIL, JUDGE_PASS]), log=lambda *a: None)
        self.assertEqual(r["attempts"], 3)
        self.assertTrue(r["judged_ok"])
        p = self.out_path()
        self.assertTrue(os.path.isfile(p))
        saved = json.load(open(p, encoding="utf-8"))
        self.assertEqual(saved["name"], "军帐")
        self.assertTrue(saved["_judge"]["ok"])
        self.assertEqual(saved["_judge"]["backend"], "mock")

    def test_judge_cap3_saves_last_valid(self):
        """判官 3 轮全打回：仍落盘最后一版合法产物，judge 标记未通过。"""
        r = gen_plan.generate(self.proj, "军帐", scene_desc="x",
                              gen_chat=chat_script([GOOD]),
                              judge_fn=judge_script([JUDGE_FAIL]), log=lambda *a: None)
        self.assertEqual(r["attempts"], 3)
        self.assertFalse(r["judged_ok"])
        saved = json.load(open(self.out_path(), encoding="utf-8"))
        self.assertFalse(saved["_judge"]["ok"])
        self.assertTrue(saved["_judge"]["reasons"])

    def test_validate_never_passes_raises_and_no_file(self):
        with self.assertRaisesRegex(ValueError, "未通过 validate"):
            gen_plan.generate(self.proj, "军帐", scene_desc="x",
                              gen_chat=chat_script([BAD_GEOM]),
                              judge_fn=judge_script([JUDGE_PASS]), log=lambda *a: None)
        self.assertFalse(os.path.isfile(self.out_path()))

    def test_first_try_pass(self):
        r = gen_plan.generate(self.proj, "军帐", scene_desc="x",
                              gen_chat=chat_script([GOOD]),
                              judge_fn=judge_script([JUDGE_PASS]), log=lambda *a: None)
        self.assertEqual(r["attempts"], 1)
        self.assertTrue(r["judged_ok"])

    def test_feedback_prompt_carries_errors(self):
        """打回后下一轮 prompt 带 validate 错误明细/判官原因。"""
        seen = []

        def chat(msgs):
            seen.append(msgs[1]["content"])
            return json.dumps(GOOD if len(seen) > 1 else BAD_GEOM, ensure_ascii=False)
        gen_plan.generate(self.proj, "军帐", scene_desc="x", gen_chat=chat,
                          judge_fn=judge_script([JUDGE_PASS]), log=lambda *a: None)
        self.assertEqual(len(seen), 2)
        self.assertIn("上轮问题", seen[1])
        self.assertIn("超出画布", seen[1])

    def test_scene_ids_from_project_scenes(self):
        """项目有 素材/场景.json 时 zone scene_ref 悬空会被 validate 打回。"""
        os.makedirs(os.path.join(self.proj, "素材"), exist_ok=True)
        json.dump({"scenes": [{"id": "军帐", "name": "军帐"}]},
                  open(os.path.join(self.proj, "素材", "场景.json"), "w", encoding="utf-8"),
                  ensure_ascii=False)
        zoned = json.loads(json.dumps(GOOD))
        zoned["zones"] = [{"id": "z1", "label": "议事区", "rect": [1, 1, 4, 3], "scene_ref": "不存在"}]
        with self.assertRaisesRegex(ValueError, "未通过 validate"):
            gen_plan.generate(self.proj, "军帐", scene_desc="x",
                              gen_chat=chat_script([zoned]),
                              judge_fn=judge_script([JUDGE_PASS]), log=lambda *a: None)

    def test_keyframe_requires_existing_file(self):
        with self.assertRaisesRegex(ValueError, "关键帧不存在"):
            gen_plan.generate(self.proj, "军帐", keyframe="没有这张图.png",
                              gen_chat=chat_script([GOOD]), log=lambda *a: None)


SCENES_DOC = {"scenes": [
    {"id": "loc_tent", "name": "中军帐", "aliases": ["tent"],
     "geometry": ["矩形帐篷，门朝南", "帅案居北墙下"], "time": "夜", "light": "烛火", "interior": True},
    {"id": "loc_field", "name": "城外战场", "geometry": ["开阔土地，北有山"], "interior": False},
]}


class ScenePlanTests(unittest.TestCase):
    """--scene 场景绑定：描述取自 素材/场景.json、名缺省=场景 id、plan 顶层写 scene_ref。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.proj = self._td.name
        os.makedirs(os.path.join(self.proj, "推演"), exist_ok=True)
        os.makedirs(os.path.join(self.proj, "素材"), exist_ok=True)
        json.dump(SCENES_DOC, open(os.path.join(self.proj, "素材", "场景.json"), "w", encoding="utf-8"),
                  ensure_ascii=False)

    def tearDown(self):
        self._td.cleanup()

    def test_load_scene_by_id_name_alias(self):
        row, desc = gen_plan.load_scene(self.proj, "loc_tent")
        self.assertEqual(row["name"], "中军帐")
        self.assertIn("矩形帐篷，门朝南", desc)
        self.assertIn("中军帐", desc)
        self.assertEqual(gen_plan.load_scene(self.proj, "中军帐")[0]["id"], "loc_tent")
        self.assertEqual(gen_plan.load_scene(self.proj, "tent")[0]["id"], "loc_tent")
        self.assertEqual(gen_plan.load_scene(self.proj, "不存在"), (None, ""))

    def test_generate_with_scene_binds_scene_ref(self):
        seen = []

        def chat(msgs):
            seen.append(msgs[1]["content"])
            return json.dumps(GOOD, ensure_ascii=False)
        r = gen_plan.generate(self.proj, scene="loc_tent", gen_chat=chat,
                              judge_fn=judge_script([JUDGE_PASS]), log=lambda *a: None)
        # 名缺省=场景 id；prompt 带场景资产描述（geometry 行 + 场景名）
        self.assertEqual(r["plan"]["name"], "loc_tent")
        self.assertIn("矩形帐篷，门朝南", seen[0])
        self.assertIn("场景：中军帐", seen[0])
        saved = json.load(open(os.path.join(self.proj, "推演", "平面图_loc_tent.plan.json"), encoding="utf-8"))
        self.assertEqual(saved["scene_ref"], "loc_tent")

    def test_generate_scene_extra_desc_appended(self):
        seen = []
        gen_plan.generate(self.proj, scene="loc_tent", extra_desc="加一架屏风",
                          gen_chat=lambda m: (seen.append(m[1]["content"]), json.dumps(GOOD, ensure_ascii=False))[1],
                          judge_fn=judge_script([JUDGE_PASS]), log=lambda *a: None)
        self.assertIn("补充描述：加一架屏风", seen[0])

    def test_generate_scene_miss_raises(self):
        with self.assertRaisesRegex(ValueError, "场景资产未命中"):
            gen_plan.generate(self.proj, scene="不存在", gen_chat=chat_script([GOOD]),
                              judge_fn=judge_script([JUDGE_PASS]), log=lambda *a: None)


class DraftMissingScenePlansTests(unittest.TestCase):
    """extract 收尾自动初稿：只补缺、逐场景 fail-soft、无厂商整体跳过。"""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.proj = self._td.name
        os.makedirs(os.path.join(self.proj, "推演"), exist_ok=True)
        os.makedirs(os.path.join(self.proj, "素材"), exist_ok=True)
        json.dump(SCENES_DOC, open(os.path.join(self.proj, "素材", "场景.json"), "w", encoding="utf-8"),
                  ensure_ascii=False)

    def tearDown(self):
        self._td.cleanup()

    def test_drafts_only_missing_scenes(self):
        # loc_tent 已有 scene_ref 平面图 → 只为 loc_field 出稿
        old = json.loads(json.dumps(GOOD))
        old.update({"name": "loc_tent", "scene_ref": "loc_tent"})
        json.dump(old, open(os.path.join(self.proj, "推演", "平面图_loc_tent.plan.json"), "w", encoding="utf-8"),
                  ensure_ascii=False)
        stats = gen_plan.draft_missing_scene_plans(self.proj, gen_chat=chat_script([GOOD]),
                                                   judge_fn=judge_script([JUDGE_PASS]), log=lambda *a: None)
        self.assertEqual(stats, {"生成": 1, "跳过": 1, "失败": 0})
        self.assertTrue(os.path.isfile(os.path.join(self.proj, "推演", "平面图_loc_field.plan.json")))

    def test_all_covered_noop(self):
        for sid in ("loc_tent", "loc_field"):
            old = json.loads(json.dumps(GOOD))
            old.update({"name": sid, "scene_ref": sid})
            json.dump(old, open(os.path.join(self.proj, "推演", f"平面图_{sid}.plan.json"), "w", encoding="utf-8"),
                      ensure_ascii=False)
        stats = gen_plan.draft_missing_scene_plans(self.proj, gen_chat=chat_script([GOOD]),
                                                   judge_fn=judge_script([JUDGE_PASS]), log=lambda *a: None)
        self.assertEqual(stats["生成"], 0)
        self.assertEqual(stats["跳过"], 2)

    def test_llm_failure_is_warning_not_exception(self):
        def boom(msgs):
            raise RuntimeError("厂商 429")
        stats = gen_plan.draft_missing_scene_plans(self.proj, gen_chat=boom,
                                                   judge_fn=judge_script([JUDGE_PASS]), log=lambda *a: None)
        self.assertEqual(stats["失败"], 2)
        self.assertEqual(stats["生成"], 0)

    def test_no_text_vendor_skips_quietly(self):
        with patch.object(gen_plan, "pick_vendor", side_effect=VendorError("没有已启用厂商")):
            stats = gen_plan.draft_missing_scene_plans(self.proj, log=lambda *a: None)
        self.assertEqual(stats["生成"], 0)
        self.assertEqual(stats["失败"], 0)


if __name__ == '__main__':
    unittest.main()
