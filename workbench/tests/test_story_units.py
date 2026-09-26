# -*- coding: utf-8 -*-
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('SLATE_NO_AUTH', '1')   # HTTP 边界用例不做口令认证（必须在导入 server 之前）
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workbench" / "tools"))


class StoryUnitsTests(unittest.TestCase):
    """① 第一步最小单元：锚定 / 体检 / 反查派生 / 扩写注入 / 缺口上报。"""

    def make_project(self, anchored=False, broken=False):
        td = tempfile.TemporaryDirectory()
        root = Path(td.name)
        (root / "剧本").mkdir(parents=True)
        (root / "素材").mkdir(parents=True)
        characters = [{
            "id": "hero", "name": "白咲蛛绪", "aliases": ["蛛绪"], "gender": "女",
            "identity_anchor": "女，十七岁，瘦削，黑发", "bio_language": "话短，习惯先报数字",
            "bio_crack": "心虚时开始数自己的头发", "bio_pressure": "被逼急先让利益再连本讨回",
            "bio_address": "熟人叫蛛绪，生人叫白同学", "bio_arc": "从被利用者到执棋人",
            "states": [{"id": "hero_S1", "label": "染血", "episodes": ["E2"], "look_diff": "左臂渗血"}],
        }, {"id": "rival", "name": "佐藤", "aliases": [], "gender": "男"}]
        if broken:
            characters[1]["bio_language"] = ""
        scenes = [{"id": "room", "name": "二年A班教室", "aliases": ["教室"],
                   "spatial_limit": "只有前后两门，被堵即无退路", "action_slots": ["窗台", "讲台"]}]
        props = [{"id": "leg", "name": "银白步足", "usage_boundary": "只在夜间生效"}]
        if broken:
            props[0]["usage_boundary"] = ""
        (root / "素材" / "人物.json").write_text(json.dumps({"characters": characters}, ensure_ascii=False), encoding="utf-8")
        (root / "素材" / "场景.json").write_text(json.dumps({"scenes": scenes}, ensure_ascii=False), encoding="utf-8")
        (root / "素材" / "道具.json").write_text(json.dumps({"props": props}, ensure_ascii=False), encoding="utf-8")

        episodes = [
            {"id": "E1", "title": "开局", "arc_id": "ARC1", "cast_refs": ["@character:hero"],
             "scene_refs": ["@scene:room"], "key_asset_refs": ["@prop:leg"],
             "beats": ["发现尸体", "被当成凶手", "反证清白"], "fs_plant": ["FS1"], "fs_pay": []},
            {"id": "E2", "title": "转折", "arc_id": "ARC1", "cast_refs": ["@character:hero", "@character:rival"],
             "scene_refs": ["@scene:room"], "key_asset_refs": [], "state_derive": [
                 {"ref": "@character:hero", "state_id": "hero_S1", "label": "染血"}],
             "fs_plant": [], "fs_pay": ["FS1"]},
            {"id": "E3", "title": "收尾", "arc_id": "ARC2", "cast_refs": ["@character:rival"],
             "scene_refs": [], "key_asset_refs": []},
        ]
        if broken:
            episodes[2]["cast_refs"] = ["@character:ghost"]     # 悬空引用
            episodes[2]["arc_id"] = "ARC9"                       # 分段不存在
        outline = {"main_line": "一条主线", "premise": "前哨", "rules": [{"id": "R1", "text": "主角不会任何法术"}],
                   "taboos": [{"id": "TB1", "rule": "禁止靠武力翻盘", "detect": ["拔剑", "爆种"]}],
                   "arcs": [{"id": "ARC1", "title": "起", "ep_from": "E1", "ep_to": "E2",
                             "goal": "从嫌疑人到调查者", "release": ["世界规则"]},
                            {"id": "ARC2", "title": "承", "ep_from": "E3", "ep_to": "E3",
                             "goal": "从调查者到执棋人", "release": ["身世"]}]}
        threads = {"foreshadows": [{"id": "FS1", "plant": "步足夜间才亮", "set_in": "E1", "form": "道具特写",
                                    "pay_in": "E2", "payoff": "靠亮灭识破谎言", "refs": ["@prop:leg"], "status": "open"}],
                   "hooks": [{"id": "HK1", "beat": "尸体手心一枚印", "question": "谁留的？", "ep": "E1"}]}
        if broken:
            threads["foreshadows"][0]["pay_in"] = "E1"           # 收在早于埋
            threads["hooks"][0]["ep"] = "第 9 集"                 # 集号口径错
        (root / "剧本" / "大纲.json").write_text(json.dumps(outline, ensure_ascii=False), encoding="utf-8")
        (root / "剧本" / "分集.json").write_text(json.dumps({"episodes": episodes, "rev": 1}, ensure_ascii=False), encoding="utf-8")
        (root / "剧本" / "埋线.json").write_text(json.dumps(threads, ensure_ascii=False), encoding="utf-8")
        if anchored:
            import story_units
            story_units.anchor(str(root))
        return td, root

    def codes(self, report, key="errors"):
        return {item["code"] for item in report[key]}

    # ── 体检 ──
    def test_check_passes_on_consistent_units(self):
        import story_units
        td, root = self.make_project()
        self.addCleanup(td.cleanup)
        rep = story_units.check(str(root))
        self.assertTrue(rep["ok"], rep["errors"])
        self.assertEqual(rep["counts"]["foreshadows"], 1)

    def test_check_flags_dangling_ref_bad_arc_and_episode_format(self):
        import story_units
        td, root = self.make_project(broken=True)
        self.addCleanup(td.cleanup)
        rep = story_units.check(str(root))
        codes = self.codes(rep)
        self.assertIn("EP_REF", codes)           # @character:ghost 悬空
        self.assertIn("EP_ARC_REF", codes)       # ARC9 不存在
        self.assertIn("FS_ORDER", codes)         # 收在不早于埋
        self.assertIn("HK_EP", codes)            # 「第 9 集」不是 E\d+
        self.assertFalse(rep["ok"])

    def test_check_aggregates_legacy_bios_into_few_warnings(self):
        """老项目几十条空传记不能刷几十条警告——聚合成包级一条。"""
        import story_units
        td, root = self.make_project(broken=True)
        self.addCleanup(td.cleanup)
        rep = story_units.check(str(root))
        bio = [w for w in rep["warnings"] if w["code"] == "BIO_EMPTY"]
        self.assertEqual(len(bio), 1)
        self.assertIn("1 项", bio[0]["message"])

    # ── 锚定 ──
    def test_anchor_refuses_when_blocking_then_stamps_all_packages(self):
        import story_units
        td, root = self.make_project(broken=True)
        self.addCleanup(td.cleanup)
        res = story_units.anchor(str(root))
        self.assertFalse(res["ok"])
        self.assertEqual(story_units.anchor_rev(str(root)), 0)

        td2, root2 = self.make_project()
        self.addCleanup(td2.cleanup)
        res2 = story_units.anchor(str(root2))
        self.assertTrue(res2["ok"], res2["report"]["errors"])
        self.assertEqual(res2["anchor_rev"], 1)
        for name in ("人物", "场景", "道具"):
            doc = json.loads((root2 / "素材" / f"{name}.json").read_text(encoding="utf-8"))
            self.assertEqual(doc.get("anchor_rev"), 1, name)
        self.assertEqual(story_units.anchor_rev(str(root2)), 1)
        story_units.unanchor(str(root2))
        self.assertEqual(story_units.anchor_rev(str(root2)), 0)

    # ── 反查派生 ──
    def test_index_derives_first_and_key_episodes_without_touching_archive(self):
        import story_units
        td, root = self.make_project(anchored=True)
        self.addCleanup(td.cleanup)
        idx = story_units.build_index(str(root))
        hero = idx["@character:hero"]
        self.assertEqual(hero["first_ep"], "E1")
        self.assertEqual(hero["key_eps"], ["E1", "E2"])
        self.assertEqual(hero["state_eps"]["hero_S1"], ["E2"])
        self.assertEqual(idx["@prop:leg"]["foreshadows"], ["FS1"])
        archive = json.loads((root / "素材" / "人物.json").read_text(encoding="utf-8"))
        for key in ("first_ep", "first_appearance", "key_episodes", "key_eps"):
            self.assertNotIn(key, archive["characters"][0], f"{key} 只能派生，不得写回档案")

    # ── 扩写注入 ──
    def test_units_block_empty_when_unanchored_and_full_when_anchored(self):
        import story_units
        td, root = self.make_project()
        self.addCleanup(td.cleanup)
        self.assertEqual(story_units.units_block(str(root), "E1"), "")

        td2, root2 = self.make_project(anchored=True)
        self.addCleanup(td2.cleanup)
        block = story_units.units_block(str(root2), "E1")
        for needle in ("全剧规则", "创作禁区", "本段阶段目标", "本集节拍", "出场人物锚定",
                       "心虚时开始数自己的头发", "只有前后两门", "只在夜间生效",
                       "本集要埋的线", "可引用素材白名单"):
            self.assertIn(needle, block, needle)
        # E2 只该看到自己的伏笔收点，不该带上 E1 的待埋清单
        self.assertIn("本集要收的线", story_units.units_block(str(root2), "E2"))

    # ── 缺口上报 ──
    def test_gap_report_flags_unregistered_speaker_and_scene_only(self):
        import story_units
        td, root = self.make_project(anchored=True)
        self.addCleanup(td.cleanup)
        ep = next(e for e in json.loads((root / "剧本" / "分集.json").read_text(encoding="utf-8"))["episodes"]
                  if e["id"] == "E1")
        ep["text"] = "【场景：二年A班教室／日】\n蛛绪：三具，都在窗台。\n神秘人：你数错了。\n【场景：钟楼／夜】"
        rep = story_units.gap_report(str(root), "E1", ep["text"])
        self.assertTrue(rep["enabled"])
        self.assertEqual([x["name"] for x in rep["missing_speakers"]], ["神秘人"])
        self.assertEqual([x["name"] for x in rep["missing_scenes"]], ["钟楼"])
        self.assertTrue(rep["blocking"])
        clean = story_units.gap_report(str(root), "E1", "【场景：教室／夜】\n佐藤：走吧。")
        self.assertFalse(clean["blocking"])
        # 别名「教室」也算命中；未锚定的项目整条闸关闭，老项目行为不变
        td3, root3 = self.make_project()
        self.addCleanup(td3.cleanup)
        self.assertFalse(story_units.gap_report(str(root3), "E1", "神秘人：你是谁")["enabled"])


class StoryUnitsWriteTests(unittest.TestCase):
    """第一步产物落盘：名册建档 / 幂等 / 设定投影 / 状态派生 / ② 投影约束块。"""

    def setUp(self):
        import story_units
        self.su = story_units
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.root = Path(self.td.name)
        (self.root / "剧本").mkdir(parents=True)
        (self.root / "素材").mkdir(parents=True)
        # 既有档案：同名资产必须沿用旧 id（重跑提炼换 id 就是 N93 的成因）
        (self.root / "素材" / "人物.json").write_text(json.dumps({"characters": [
            {"id": "old_hero", "name": "白咲蛛绪", "aliases": ["蛛绪"], "sheet_prompt": "旧外观"}]},
            ensure_ascii=False), encoding="utf-8")
        (self.root / "素材" / "场景.json").write_text(json.dumps({"scenes": []}), encoding="utf-8")
        (self.root / "素材" / "道具.json").write_text(json.dumps({"props": []}), encoding="utf-8")
        (self.root / "剧本" / "分集.json").write_text(json.dumps({"episodes": [
            {"id": "E1", "title": "旧标题", "text": "已经写好的正文，第一步不得覆写"}]}, ensure_ascii=False),
            encoding="utf-8")

    def story(self, **over):
        data = {"premise": "前哨", "rules": [{"id": "R1", "text": "不会法术"}],
                "taboos": [{"id": "TB1", "rule": "禁止武力翻盘", "detect": ["爆种"]}],
                "arcs": [{"id": "ARC1", "ep_from": "E1", "ep_to": "E2", "goal": "从A到B"}],
                "roster": {"characters": [{"id": "hero", "name": "白咲蛛绪", "role": "主角", "gender": "女"},
                                          {"id": "rival", "name": "佐藤", "gender": "男"}],
                           "scenes": [{"id": "room", "name": "二年A班教室", "interior": True}],
                           "props": [{"id": "leg", "name": "银白步足", "kind": "叙事"}]},
                "episodes": [{"id": "E1", "arc_id": "ARC1", "summary": "新概要", "beats": ["甲", "乙"],
                              "cast_refs": ["@character:hero", "@character:rival"],
                              "scene_refs": ["@scene:room"], "key_asset_refs": ["@prop:leg"],
                              "fs_plant": ["FS1"]},
                             {"id": "E2", "arc_id": "ARC1", "summary": "第二集",
                              "cast_refs": ["@character:hero"], "scene_refs": [], "key_asset_refs": [],
                              "fs_pay": ["FS1"]}],
                "foreshadows": [{"id": "FS1", "plant": "步足夜间才亮", "set_in": "E1", "pay_in": "E2",
                                 "refs": ["@prop:leg"]}],
                "hooks": [{"id": "HK1", "beat": "手心一枚印", "question": "谁留的", "ep": "E1"}]}
        data.update(over)
        return data

    def test_apply_story_reuses_existing_id_and_never_touches_text(self):
        res = self.su.apply_story(str(self.root), self.story())
        chars = json.loads((self.root / "素材" / "人物.json").read_text(encoding="utf-8"))["characters"]
        hero = next(c for c in chars if c["name"] == "白咲蛛绪")
        self.assertEqual(hero["id"], "old_hero", "同名资产必须沿用既有 id")
        self.assertEqual(hero["sheet_prompt"], "旧外观", "不得覆写既有外观字段")
        self.assertIn("@character:old_hero", res["reused"])
        self.assertIn("@character:rival", res["created"])
        self.assertEqual(len([c for c in chars if c["name"] == "白咲蛛绪"]), 1)
        eps = json.loads((self.root / "剧本" / "分集.json").read_text(encoding="utf-8"))["episodes"]
        e1 = next(e for e in eps if e["id"] == "E1")
        self.assertEqual(e1["text"], "已经写好的正文，第一步不得覆写")
        self.assertEqual(e1["beats"], ["甲", "乙"])
        self.assertEqual(len(eps), 2)

    def test_apply_story_is_idempotent_and_keeps_paid_status(self):
        self.su.apply_story(str(self.root), self.story())
        again = self.su.apply_story(str(self.root), self.story())
        self.assertEqual(again["created"], [], "第二次不该再建同名档")
        fs = [{"id": "FS1", "plant": "改写了", "set_in": "E1", "pay_in": "E2", "status": "open",
               "refs": ["@prop:leg"]}]
        self.su.apply_story(str(self.root), {"foreshadows": fs})
        stored = json.loads((self.root / "剧本" / "埋线.json").read_text(encoding="utf-8"))["foreshadows"][0]
        self.assertEqual(stored["status"], "open")
        self.su.apply_story(str(self.root), {"foreshadows": [dict(fs[0], status="paid")]})
        self.su.apply_story(str(self.root), {"foreshadows": [dict(fs[0], status="open")]})
        stored = json.loads((self.root / "剧本" / "埋线.json").read_text(encoding="utf-8"))["foreshadows"][0]
        self.assertEqual(stored["status"], "paid", "人工标记已回收的线不得被生成流程改回 open")

    def test_apply_entities_writes_only_named_refs_and_reports_gaps(self):
        self.su.apply_story(str(self.root), self.story())
        # U2 的输入名册来自 load_units，ref 用的是档案真实 id（hero 被沿用成 old_hero）
        res = self.su.apply_entities(str(self.root), {
            "characters": [{"ref": "@character:old_hero", "bio_crack": "心虚时数头发",
                            "bio_address": "熟人叫蛛绪",
                            "relations": [{"to_ref": "@character:rival", "kind": "同盟"}]},
                           {"ref": "@character:hero", "bio_crack": "名册改名后的旧提议 id，不该被采纳"},
                           {"ref": "@character:nobody", "bio_crack": "不该被建档"}],
            "scenes": [{"ref": "@scene:room", "spatial_limit": "只有前后两门", "action_slots": ["窗台"]}],
            "props": [{"ref": "@prop:leg", "usage_boundary": "只在夜间生效"}],
            "state_derive": [{"ep": "E1", "ref": "@character:old_hero", "state_id": "old_hero_S1",
                              "label": "染血", "look_diff": "左臂渗血", "camp": "中立"},
                             {"ep": "E2", "ref": "@character:old_hero", "state_id": "old_hero_S1",
                              "label": "染血", "look_diff": "左臂渗血"}],
            "gaps": [{"kind": "prop", "ref": "@prop:knife", "need": "第三集要用的刀没在名册"}]})
        self.assertEqual(res["applied"], 3)
        self.assertIn("@prop:knife", [g.get("ref") for g in res["gaps"]])
        self.assertEqual({"@character:hero", "@character:nobody"} - {r.get("ref") for r in res["gaps"]},
                         set(), "命不中档案的 ref 一律进 gaps 上报，不静默丢弃也不新建")
        chars = json.loads((self.root / "素材" / "人物.json").read_text(encoding="utf-8"))["characters"]
        hero = next(c for c in chars if c["id"] == "old_hero")
        self.assertEqual(hero["bio_crack"], "心虚时数头发")
        self.assertNotIn("nobody", json.dumps(chars, ensure_ascii=False), "名册外的人物不得被建档")
        self.assertEqual([s["episodes"] for s in hero["states"]], [["E1", "E2"]], "同状态跨集累积不重复建档")
        self.assertNotIn("_kind", hero, "临时索引键不得写回档案")
        scene = json.loads((self.root / "素材" / "场景.json").read_text(encoding="utf-8"))["scenes"][0]
        self.assertEqual(scene["spatial_limit"], "只有前后两门")

    def test_authority_note_empty_unanchored_and_carries_projection_rules(self):
        self.assertEqual(self.su.authority_note(str(self.root), "人物"), "")
        self.su.apply_story(str(self.root), self.story())
        self.su.apply_entities(str(self.root), {"characters": [{"ref": "@character:old_hero",
                                                               "bio_crack": "数头发"}]})
        self.assertEqual(self.su.authority_note(str(self.root), "人物"), "", "未锚定不注入")
        story_units_anchor = self.su.anchor(str(self.root))
        if not story_units_anchor.get("ok"):
            # 复用旧 id 后 hero 的 gender 缺失等 warn 不阻断；此处仍不通就是真缺陷
            self.fail(story_units_anchor["report"]["errors"])
        note = self.su.authority_note(str(self.root), "人物")
        self.assertIn("投影", note)
        self.assertIn("禁止新增或改写", note)
        self.assertIn("数头发", note)
        self.assertIn("GAP:", note)
        self.assertEqual(self.su.authority_note(str(self.root), "道具")[:1], "\n")

    def test_check_passes_after_full_build(self):
        self.su.apply_story(str(self.root), self.story())
        rep = self.su.check(str(self.root))
        self.assertEqual([e["code"] for e in rep["errors"] if e["code"] in
                          ("EP_REF", "FS_REF", "ARC_EP_MISSING", "HK_EP_MISSING")], [],
                         f"名册与引用应自洽：{rep['errors']}")


class StoryUnitsWiringTests(unittest.TestCase):
    """接线哨兵：注入块只有一处渲染函数，且未锚定时全链逐字不变。"""

    def test_units_block_rendered_only_from_story_units(self):
        """扩写的锚定注入必须单点：谁再抄一份 units_block，两处真相立刻分叉。"""
        import inspect
        import creation_pipeline
        import prompt_modules as PM
        src_expand = inspect.getsource(creation_pipeline.cmd_expand)
        self.assertIn("story_units.units_block", src_expand, "扩写要从 story_units 取注入块")
        self.assertEqual(src_expand.count("units_block"), 1, "不得在 cmd_expand 里再拼一份锚定文本")
        # 渲染函数本体只此一处
        import story_units
        self.assertTrue(hasattr(story_units, "units_block"))
        self.assertNotIn("弧光：", PM.expand_episode_prompt.__doc__ or "", "提示词函数不代抄设定内容")

    def test_expand_prompt_identical_when_unanchored(self):
        import prompt_modules as PM
        entry = {"id": "E1", "title": "t", "summary": "s", "hook": "h", "cliff": "c", "duration_min": 3}
        with_empty = PM.expand_episode_prompt("idea", entry, "prev", style_text=None, proj=None, units="")
        legacy = PM.expand_episode_prompt("idea", entry, "prev")
        self.assertEqual(with_empty[0], legacy[0], "未锚定项目的系统提示词必须逐字不变")
        self.assertEqual(with_empty[1], legacy[1], "未锚定项目的用户提示词必须逐字不变")

    def test_extract_prompt_identical_when_unanchored(self):
        """② 的投影块只挂在 _extract_one 的 sys_p 尾部；未锚定时不得多出任何字。"""
        import inspect
        import creation_pipeline
        src = inspect.getsource(creation_pipeline._extract_one)
        self.assertIn("story_units.authority_note", src)
        self.assertIn('if note:', src, "空 note 必须被跳过，而不是拼上空串")
        import story_units
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.assertEqual(story_units.authority_note(td.name, "人物"), "")

    def test_new_routes_registered(self):
        sys.path.insert(0, str(ROOT / "workbench"))
        try:
            import server
        except Exception as exc:  # pragma: no cover - 依赖缺失时不误红
            self.skipTest(f"server 导入失败：{exc}")
        keys = {tuple(k) for k in server.ROUTES}
        for method, path in (("GET", "/api/units"), ("POST", "/api/units/build"),
                             ("POST", "/api/units/anchor"), ("POST", "/api/units/check"),
                             ("POST", "/api/units/edit")):
            self.assertIn((method, path), keys, f"路由 {method} {path} 未注册")


class StoryUnitsTabooTests(unittest.TestCase):
    """③ 的创作禁区闸：只告警不阻断，且未锚定时完全不出声。"""

    def setUp(self):
        import story_units
        self.su = story_units
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.root = Path(self.td.name)
        (self.root / "剧本").mkdir(parents=True)
        (self.root / "素材").mkdir(parents=True)
        (self.root / "素材" / "人物.json").write_text(json.dumps({"characters": []}), encoding="utf-8")
        (self.root / "素材" / "场景.json").write_text(json.dumps({"scenes": []}), encoding="utf-8")
        (self.root / "素材" / "道具.json").write_text(json.dumps({"props": []}), encoding="utf-8")
        (self.root / "剧本" / "分集.json").write_text(json.dumps({"episodes": []}), encoding="utf-8")
        (self.root / "剧本" / "大纲.json").write_text(json.dumps({"main_line": "x"}), encoding="utf-8")

    shots = [
        {"id": "S1", "content": "她爆种反杀全场", "action": "挥剑"},
        {"id": "S2", "content": "她验出丹是真的", "lines": [{"at": 0.5, "speaker": "hero", "line": "静观其变"}]},
        {"id": "S3", "content": "柜台前对峙", "prompt_video": "对手突然爆种冲上来"},
    ]

    def test_empty_when_unanchored(self):
        self.assertEqual(self.su.taboo_scan(str(self.root), self.shots), [])

    def test_flags_shots_hitting_detect_words(self):
        self.su.edit_outline(str(self.root), {"taboos": [
            {"id": "TB1", "rule": "禁止靠武力翻盘", "detect": ["爆种"]},
            {"id": "TB2", "rule": "没有 detect 的死条款", "detect": []}]})
        self.assertTrue(self.su.anchor(str(self.root), force=True).get("ok"))
        hits = self.su.taboo_scan(str(self.root), self.shots)
        self.assertEqual([(h["shot_id"], h["taboo_id"]) for h in hits],
                         [("S1", "TB1"), ("S3", "TB1")], "只命中带 detect 词的条款，逐镜定位")
        self.assertEqual(hits[0]["hits"][0]["field"], "content")
        self.assertEqual(hits[1]["hits"][0]["field"], "prompt_video")

    def test_lines_text_is_scanned_too(self):
        self.su.edit_outline(str(self.root), {"taboos": [
            {"id": "TB3", "rule": "不许出现某台词", "detect": ["静观其变"]}]})
        self.su.anchor(str(self.root), force=True)
        hits = self.su.taboo_scan(str(self.root), self.shots)
        self.assertEqual([h["shot_id"] for h in hits], ["S2"])
        # negative 是禁令清单，写着"禁止静观其变"不该被当成违例
        noisy = self.shots + [{"id": "S9", "negative": ["禁止出现静观其变"], "content": "对峙"}]
        self.assertEqual([h["shot_id"] for h in self.su.taboo_scan(str(self.root), noisy)], ["S2"],
                         "扫 negative 会造误报，必须排除")

    def test_storyboard_prompt_unchanged_without_units(self):
        import prompt_modules as PM
        a = PM.storyboard_prompt("剧本", [], [], units="")
        b = PM.storyboard_prompt("剧本", [], [])
        self.assertEqual(a[0], b[0], "未锚定项目的分镜系统提示词必须逐字不变")
        c = PM.storyboard_prompt("剧本", [], [], units="【创作禁区】禁止武力翻盘")
        self.assertIn("已锚定设定", c[0])
        self.assertIn("禁止武力翻盘", c[0])

    def test_cmd_storyboard_wired_to_single_renderer(self):
        import inspect
        import creation_pipeline
        src = inspect.getsource(creation_pipeline.cmd_storyboard)
        self.assertEqual(src.count("units_block"), 1, "分镜侧也只能从 story_units 取注入块")
        self.assertIn("story_units.taboo_scan", src)
        self.assertIn('cfg["unit_warnings"]', src, "告警要落进分镜 JSON 供页面显示")


    def test_check_flags_unresolvable_state_match_keys(self):
        """C10：状态匹配键只能认 E+数字 或真实场名/别名——其余永远匹配不上，选图会静默回退。"""
        (self.root / "素材" / "场景.json").write_text(json.dumps({"scenes": [
            {"id": "shop", "name": "小铺", "aliases": ["铺子"]}]}, ensure_ascii=False), encoding="utf-8")
        (self.root / "素材" / "人物.json").write_text(json.dumps({"characters": [
            {"id": "hero", "name": "阿照", "bio_arc": "从A到B", "bio_language": "短句",
             "bio_crack": "数头发", "bio_pressure": "让利益", "bio_address": "阿照",
             "states": [{"id": "hero_S1", "label": "染血", "episodes": ["E1", "铺子", "第七集", "／夜"]}]}]},
            ensure_ascii=False), encoding="utf-8")
        self.su.edit_outline(str(self.root), {"taboos": [{"id": "T", "rule": "r", "detect": ["x"]}]})
        rep = self.su.check(str(self.root))
        hits = [w for w in rep["warnings"] if w["code"] == "STATE_KEY_UNRESOLVABLE"]
        self.assertEqual(len(hits), 1)
        self.assertIn("第七集", hits[0]["message"])
        self.assertIn("／夜", hits[0]["message"])
        self.assertNotIn("E1、", hits[0]["message"])
        self.assertNotIn("铺子、", hits[0]["message"], "集号与真实场名/别名都算合法，不该误报")
        self.assertTrue(rep["ok"], "C10 只 warn 不阻断锚定")


class UnitsHttpRouteTests(unittest.TestCase):
    """新路由的真实 HTTP 边界：GET /api/units 与各 POST（不跑 LLM，build 只验分发）。"""

    @classmethod
    def setUpClass(cls):
        import http.client
        import threading
        from http.server import ThreadingHTTPServer
        cls.http_client = http.client
        cls.server = __import__("workbench.server", fromlist=["server"])
        cls.http = ThreadingHTTPServer(("127.0.0.1", 0), cls.server.H)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown(); cls.http.server_close(); cls.thread.join()

    def call(self, path, method="GET", body=None):
        payload = b"" if body is None else json.dumps(body).encode("utf-8")
        conn = self.http_client.HTTPConnection("127.0.0.1", self.http.server_address[1], timeout=6)
        conn.request(method, path, body=payload,
                     headers={"Host": "localhost", "Connection": "close",
                              "Content-Type": "application/json", "Content-Length": str(len(payload))})
        r = conn.getresponse()
        data = r.read()
        conn.close()
        return r.status, (json.loads(data.decode("utf-8")) if data and "json" in (r.getheader("Content-Type") or "") else data)

    def make_project(self):
        """临时项目落在 VIDEO/projects/<名> 下：proj_dir 只认这个根，不能直接指任意目录。"""
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name) / "projects" / "unitstest"
        self.patch = patch.object(self.server, "VIDEO", td.name)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        (root / "剧本").mkdir(parents=True)
        (root / "素材").mkdir(parents=True)
        (root / "素材" / "人物.json").write_text(json.dumps({"characters": [
            {"id": "hero", "name": "阿照", "gender": "女", "bio_arc": "从A到B", "bio_language": "短句",
             "bio_crack": "数头发", "bio_pressure": "让利益", "bio_address": "阿照"}]}, ensure_ascii=False), encoding="utf-8")
        (root / "素材" / "场景.json").write_text(json.dumps({"scenes": [
            {"id": "shop", "name": "小铺", "spatial_limit": "被堵门只能走暗格", "action_slots": ["柜台"]}]},
            ensure_ascii=False), encoding="utf-8")
        (root / "素材" / "道具.json").write_text(json.dumps({"props": [
            {"id": "coin", "name": "铜钱", "usage_boundary": "涉己大妄不示警"}]}, ensure_ascii=False), encoding="utf-8")
        (root / "剧本" / "大纲.json").write_text(json.dumps({"main_line": "x", "rules": [{"id": "R1", "text": "不会法术"}],
            "taboos": [{"id": "T1", "rule": "禁武力翻盘", "detect": ["爆种"]}],
            "arcs": [{"id": "ARC1", "ep_from": "E1", "ep_to": "E2", "goal": "从A到B"}]}, ensure_ascii=False), encoding="utf-8")
        (root / "剧本" / "分集.json").write_text(json.dumps({"rev": 1, "episodes": [
            {"id": "E1", "arc_id": "ARC1", "beats": ["甲", "乙"], "cast_refs": ["@character:hero"],
             "scene_refs": ["@scene:shop"], "key_asset_refs": ["@prop:coin"], "fs_plant": ["F1"], "fs_pay": []},
            {"id": "E2", "arc_id": "ARC1", "cast_refs": ["@character:hero"], "scene_refs": ["@scene:shop"],
             "key_asset_refs": [], "fs_plant": [], "fs_pay": ["F1"]}]},
            ensure_ascii=False), encoding="utf-8")
        (root / "剧本" / "埋线.json").write_text(json.dumps({"foreshadows": [
            {"id": "F1", "plant": "钱只判真假", "set_in": "E1", "pay_in": "E2",
             "refs": ["@prop:coin", "@character:hero"]}]}, ensure_ascii=False), encoding="utf-8")
        return root

    def test_get_units_returns_bundles_and_report(self):
        self.make_project()
        code, body = self.call("/api/units?project=unitstest")
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        self.assertFalse(body["anchored"])
        self.assertEqual([e["id"] for e in body["episodes"]], ["E1", "E2"])
        self.assertEqual(body["episodes"][0]["key_asset_refs"], ["@prop:coin"])
        self.assertEqual(body["bios"][0]["ref"], "@character:hero")
        self.assertEqual(body["report"]["errors"], [], body["report"]["errors"])
        hero = body["index"]["@character:hero"]
        self.assertEqual(hero["first_ep"], "E1")
        self.assertIn("E1", hero["key_eps"])

    def test_get_units_missing_project_is_400(self):
        self.assertEqual(self.call("/api/units?project=nope")[0], 400)

    def test_check_anchor_and_edit_roundtrip(self):
        self.make_project()
        code, body = self.call("/api/units/check", "POST", {"project": "unitstest"})
        self.assertEqual((code, body["ok"]), (200, True))
        code, body = self.call("/api/units/edit", "POST",
                               {"project": "unitstest", "kind": "asset", "zone": "人物", "id": "hero",
                                "fields": {"bio_crack": "改口：摸耳垂"}, "lock": ["bio_crack"]})
        self.assertEqual(code, 200)
        self.assertEqual(body["applied"], ["bio_crack"])
        self.assertIn("bio_crack", body["locked_fields"], "人工改的设定要进 locked_fields，生成流程不得覆盖")
        code, body = self.call("/api/units/anchor", "POST", {"project": "unitstest"})
        self.assertEqual((code, body["ok"], body["anchor_rev"]), (200, True, 1))
        self.assertEqual(self.call("/api/units?project=unitstest")[1]["anchor_rev"], 1)

    def test_edit_rejects_bad_kind_and_unknown_ids(self):
        self.make_project()
        self.assertEqual(self.call("/api/units/edit", "POST", {"project": "unitstest", "kind": "whatever"})[0], 400)
        code, body = self.call("/api/units/edit", "POST",
                               {"project": "unitstest", "kind": "episode", "id": "E99", "fields": {"beats": ["x"]}})
        self.assertEqual(code, 400)
        self.assertIn("E99", body["err"])

    def test_build_dispatches_job_and_validates_project(self):
        root = self.make_project()
        self.assertEqual(self.call("/api/units/build", "POST", {"project": "nope"})[0], 400)
        with patch.object(self.server.H, "spawn_job", return_value=91) as spawn:
            code, body = self.call("/api/units/build", "POST",
                                   {"project": "unitstest", "arc_size": 5, "anchor": True})
        self.assertEqual((code, body["ok"], body["job"], body["id"]), (200, True, True, 91))
        self.assertEqual(spawn.call_args.args[0], "creation", "必须走既有 job 体系，前端才能看任务日志")
        cmd = spawn.call_args.args[1]
        self.assertTrue(str(cmd[1]).endswith("creation_pipeline.py"), cmd)
        self.assertEqual(cmd[2], "units")
        self.assertEqual(os.path.realpath(cmd[3]), os.path.realpath(str(root)), "项目目录没传对")
        self.assertEqual(cmd[cmd.index("--arc-size") + 1], "5")
        self.assertIn("--anchor", cmd)
        # 不传 eps 时不该硬塞 --eps（否则会把默认 6 集覆盖掉 brief 的目标集数）
        with patch.object(self.server.H, "spawn_job", return_value=92) as spawn2:
            self.call("/api/units/build", "POST", {"project": "unitstest"})
        self.assertNotIn("--eps", spawn2.call_args.args[1])


class UnitsOrchestrationTests(unittest.TestCase):
    """cmd_units 的三条实跑事故回归：截断要能重试、集数不许被 brief 带跑、崩了不能裸 traceback。"""

    def setUp(self):
        import story_units
        self.su = story_units
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.root = Path(self.td.name)
        (self.root / "剧本").mkdir(parents=True)
        (self.root / "素材").mkdir(parents=True)
        for name, key in (("人物", "characters"), ("场景", "scenes"), ("道具", "props")):
            (self.root / "素材" / f"{name}.json").write_text(json.dumps({key: []}), encoding="utf-8")
        eps = [{"id": f"E{i}", "title": f"第{i}集", "text": f"【场景：小铺／日】\n阿照：集{i}"} for i in range(1, 7)]
        (self.root / "剧本" / "分集.json").write_text(json.dumps({"episodes": eps, "rev": 3}, ensure_ascii=False), encoding="utf-8")
        (self.root / "剧本" / "大纲.json").write_text(json.dumps({"main_line": "旧主线"}, ensure_ascii=False), encoding="utf-8")
        (self.root / "剧本" / "构想.txt").write_text("一句话构想", encoding="utf-8")

    def good_payload(self, ids=("E1", "E2", "E3"), arc_to="E3", arc_id="ARC1"):
        return {"premise": "p", "arcs": [{"id": arc_id, "ep_from": ids[0], "ep_to": arc_to, "goal": "从A到B"}],
                "roster": {"characters": [{"id": "hero", "name": "阿照", "gender": "女"}],
                           "scenes": [{"id": "shop", "name": "小铺"}], "props": []},
                "episodes": [{"id": i, "arc_id": arc_id, "beats": ["甲"], "cast_refs": ["@character:hero"],
                              "scene_refs": ["@scene:shop"], "key_asset_refs": [], "fs_plant": [], "fs_pay": []}
                             for i in ids],
                "foreshadows": [], "hooks": []}

    def patch_chat(self, replies):
        """replies: 每次 chat_retry 调用的返回字符串或异常；返回记录调用参数的 list。"""
        import creation_pipeline
        calls = []
        seq = list(replies)

        def fake_chat(cli, msgs, **kw):
            calls.append(msgs)
            item = seq.pop(0) if seq else None
            if item is None:
                raise AssertionError("多余的 LLM 调用")
            if isinstance(item, Exception):
                raise item
            return item
        self.patches = [patch.object(creation_pipeline, "chat_retry", fake_chat),
                        patch.object(creation_pipeline, "VendorClient", lambda *a, **k: object()),
                        patch.object(creation_pipeline, "pick_vendor", lambda *a, **k: {})]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])
        return calls

    U2_EMPTY = {"characters": [], "scenes": [], "props": [], "state_derive": [], "gaps": []}

    def test_truncated_json_retries_instead_of_crashing(self):
        import creation_pipeline
        calls = self.patch_chat(['{"premise": "p", "arcs": [{"id": "ARC1", "ep_from": "E1", "ep_to": "E3"',
                                 json.dumps(self.good_payload(), ensure_ascii=False),
                                 json.dumps(self.good_payload(("E4", "E5", "E6"), "E6", "ARC2"), ensure_ascii=False),
                                 json.dumps(self.U2_EMPTY)])
        rep = creation_pipeline.cmd_units(str(self.root), None, eps_n=6, arc_size=3)
        self.assertEqual(len(calls), 4, "第一次截断→第二次重试同一批→第二段→U2")
        self.assertIn("只输出一个 JSON 对象", json.dumps(calls[1][-1], ensure_ascii=False))
        self.assertEqual(rep.get("incomplete"), [])
        ids = [e["id"] for e in self.su.load_units(str(self.root))["episodes"]]
        self.assertEqual(ids[:6], ["E1", "E2", "E3", "E4", "E5", "E6"], "两段补齐后 6 集都在")

    def test_episode_count_clamped_to_existing_episodes(self):
        """brief 写 15 集也不能把 6 集的剧编成 15 集——09_仙 实跑翻过的车。"""
        import creation_pipeline
        calls = self.patch_chat([json.dumps(self.good_payload(("E1", "E2", "E3", "E4", "E5", "E6"), "E6"),
                                            ensure_ascii=False),
                                 json.dumps(self.U2_EMPTY)])
        creation_pipeline.cmd_units(str(self.root), None, eps_n=15, arc_size=6)
        user = json.dumps(calls[0], ensure_ascii=False)
        self.assertIn("E1~E6", user, "提示词必须锁在现有 6 集")
        self.assertNotIn("E1~E15", user)

    def test_all_batches_failing_is_reported_not_raised(self):
        import creation_pipeline
        self.patch_chat([RuntimeError("厂商 503")] * 8)
        rep = creation_pipeline.cmd_units(str(self.root), None, eps_n=6, arc_size=3)
        self.assertTrue(rep.get("incomplete"), "批次失败要进 incomplete，由 main 决定退出码")
        self.assertIn("厂商 503", rep["incomplete"][0])
        outline = json.loads((self.root / "剧本" / "大纲.json").read_text(encoding="utf-8"))
        self.assertEqual(outline.get("arcs"), None, "一批都没成时不该留半截分段")
        self.assertEqual(outline.get("main_line"), "旧主线", "原有大纲内容不被清空")

    def test_partial_segments_do_not_anchor(self):
        import creation_pipeline
        calls = self.patch_chat([json.dumps(self.good_payload(), ensure_ascii=False),        # 段1 成
                                 RuntimeError("厂商 503"), RuntimeError("厂商 503"),         # 段2 两次都败
                                 json.dumps(self.U2_EMPTY)])
        rep = creation_pipeline.cmd_units(str(self.root), None, eps_n=6, arc_size=3, do_anchor=True)
        self.assertEqual(len(rep["incomplete"]), 1)
        self.assertEqual(self.su.anchor_rev(str(self.root)), 0, "有段没补齐就不许锚定，否则权威底是半张")


    def test_second_batch_merges_arcs_instead_of_wiping_them(self):
        """并批生成：后一批只回自己那一段，前几批的分段不能被抹掉。"""
        first = self.good_payload(("E1", "E2"), "E2", "ARC1")
        first["arcs"] = [{"id": "ARC1", "ep_from": "E1", "ep_to": "E2", "goal": "第一段"},
                         {"id": "ARC2", "ep_from": "E3", "ep_to": "E4", "goal": "第二段"},
                         {"id": "ARC3", "ep_from": "E5", "ep_to": "E6", "goal": "第三段"}]
        first["rules"] = [{"id": "R1", "text": "规则一"}]
        self.su.apply_story(str(self.root), first)
        self.su.apply_story(str(self.root), {"arcs": [{"id": "ARC2", "goal": "第二段改文"}],
                                             "rules": [{"id": "R2", "text": "规则二"}],
                                             "episodes": [{"id": "E3", "arc_id": "ARC2"},
                                                          {"id": "E4", "arc_id": "ARC2"}]})
        outline = json.loads((self.root / "剧本" / "大纲.json").read_text(encoding="utf-8"))
        self.assertEqual([a["id"] for a in outline["arcs"]], ["ARC1", "ARC2", "ARC3"], "分段必须按 id 并批合并")
        self.assertEqual(next(a for a in outline["arcs"] if a["id"] == "ARC2")["ep_from"], "E3", "旧字段不能被后批留空擦掉")
        self.assertEqual(next(a for a in outline["arcs"] if a["id"] == "ARC2")["goal"], "第二段改文")
        self.assertEqual([r["id"] for r in outline["rules"]], ["R1", "R2"])
        self.assertEqual(self.su.check(str(self.root))["errors"], [])

    def test_stale_arc_reference_triggers_a_fresh_batch(self):
        """分段被覆写成分裂状态时（分集指向不存在的段），cmd_units 必须自己补回来而不是当已完成。"""
        import creation_pipeline
        self.su.apply_story(str(self.root), self.good_payload(("E1", "E2", "E3", "E4", "E5", "E6"), "E6"))
        outline = json.loads((self.root / "剧本" / "大纲.json").read_text(encoding="utf-8"))
        outline["arcs"] = [{"id": "ARC1", "ep_from": "E1", "ep_to": "E2", "goal": "第一段"}]
        (self.root / "剧本" / "大纲.json").write_text(json.dumps(outline, ensure_ascii=False), encoding="utf-8")
        self.assertIn("ARC_GAP", [e["code"] for e in self.su.check(str(self.root))["errors"]],
                      "先确认制造出了分裂状态（E3~E6 掉在分段之外）")
        calls = self.patch_chat([json.dumps(self.good_payload(("E1", "E2"), "E2"), ensure_ascii=False),
                                 json.dumps(self.good_payload(("E3", "E4", "E5", "E6"), "E6", "ARC2"),
                                            ensure_ascii=False),
                                 json.dumps(self.U2_EMPTY)])
        rep = creation_pipeline.cmd_units(str(self.root), None, eps_n=6, arc_size=2)
        self.assertEqual(len(calls), 3)
        self.assertIn("E3", json.dumps(calls[1], ensure_ascii=False), "第二批必须去补 E3~E6")
        self.assertEqual(rep.get("incomplete"), [])
        codes = [e["code"] for e in self.su.check(str(self.root))["errors"]]
        self.assertNotIn("EP_ARC_REF", codes, "补批后分段引用应重新自洽")
        arcs = json.loads((self.root / "剧本" / "大纲.json").read_text(encoding="utf-8"))["arcs"]
        self.assertEqual([a["id"] for a in arcs], ["ARC1", "ARC2"], "两段都在，没被后批抹掉")


    def test_roster_catalog_is_given_to_the_story_call_so_ids_get_reused(self):
        """09_仙 首跑实测：没看过既有档案的 LLM 给同一人物造了三个 id。必须有这一层。"""
        import creation_pipeline
        (self.root / "素材" / "人物.json").write_text(json.dumps({"characters": [
            {"id": "xianen-yao", "name": "仙恩药"}]}, ensure_ascii=False), encoding="utf-8")
        catalog = self.su.roster_catalog(str(self.root))
        self.assertEqual(catalog, ["@character:xianen-yao | 仙恩药"])
        calls = self.patch_chat([json.dumps(self.good_payload(("E1", "E2", "E3"), "E3"), ensure_ascii=False),
                                 json.dumps(self.U2_EMPTY)])
        creation_pipeline.cmd_units(str(self.root), None, eps_n=3, arc_size=3)
        sent = json.dumps(calls[0], ensure_ascii=False)
        self.assertIn("项目既有资产", sent)
        self.assertIn("@character:xianen-yao | 仙恩药", sent)
        self.assertIn("禁止另起新 id", sent)

    def test_merge_roster_reuses_by_id_even_when_the_name_differs(self):
        self.su.apply_story(str(self.root), {"roster": {"characters": [{"id": "hero", "name": "阿照", "gender": "女"}]}})
        again = self.su.apply_story(str(self.root), {"roster": {"characters": [
            {"id": "hero", "name": "阿照（老年）", "gender": "女"}]}})
        chars = json.loads((self.root / "素材" / "人物.json").read_text(encoding="utf-8"))["characters"]
        self.assertEqual(len([c for c in chars if c["id"] == "hero"]), 1, "同 id 视为同一物，不得再建一条")
        self.assertEqual(again["created"], [])

    def test_check_flags_duplicate_looking_entities(self):
        """同区里名字互相包含的实体＝疑似重复建档，报出来给人并档（不自动并）。"""
        (self.root / "素材" / "道具.json").write_text(json.dumps({"props": [
            {"id": "xianen_hei_yaowan", "name": "仙恩药黑色药丸", "usage_boundary": "b"},
            {"id": "xianen-yao", "name": "仙恩药", "usage_boundary": "b"},
            {"id": "other", "name": "断仙刀", "usage_boundary": "b"}]}, ensure_ascii=False), encoding="utf-8")
        rep = self.su.check(str(self.root))
        dup = [w for w in rep["warnings"] if w["code"] == "DUP_ASSET"]
        self.assertEqual(len(dup), 1)
        self.assertIn("仙恩药", dup[0]["message"])
        self.assertNotIn("断仙刀", dup[0]["message"])


    def test_sync_thread_claims_repairs_two_way_mismatch(self):
        """表说埋在 E4、E4 自己不认领——纯机械回填，不该花一次 LLM。"""
        (self.root / "剧本" / "埋线.json").write_text(json.dumps({"foreshadows": [
            {"id": "FS9", "plant": "铜牌军纹", "set_in": "E4", "pay_in": "E5", "refs": []}]},
            ensure_ascii=False), encoding="utf-8")
        rep = self.su.check(str(self.root))
        self.assertIn("FS_UNMATCHED", [w["code"] for w in rep["warnings"]])
        done = self.su.sync_thread_claims(str(self.root))
        self.assertEqual(done["claimed"], 2, "埋点与收点各回填一次")
        eps = {e["id"]: e for e in self.su.load_units(str(self.root))["episodes"]}
        self.assertEqual(eps["E4"]["fs_plant"], ["FS9"])
        self.assertEqual(eps["E5"]["fs_pay"], ["FS9"])
        self.assertNotIn("FS_UNMATCHED", [w["code"] for w in self.su.check(str(self.root))["warnings"]])

    def test_duplicate_ids_block_anchor_until_collapsed(self):
        rows = [{"id": "hero", "name": "阿照", "bio_arc": "从A到B", "bio_language": "短句",
                 "bio_crack": "数头发", "bio_pressure": "让", "bio_address": "照"},
                {"id": "hero", "name": "阿照（老年）", "basis": "后批重复建的同一条"}]
        (self.root / "素材" / "人物.json").write_text(json.dumps({"characters": rows}, ensure_ascii=False), encoding="utf-8")
        rep = self.su.check(str(self.root))
        self.assertIn("ID_DUP", [e["code"] for e in rep["errors"]])
        self.assertFalse(self.su.anchor(str(self.root), force=False)["ok"])

        plan = self.su.collapse_duplicate_ids(str(self.root))          # dry-run 不碰数据
        self.assertEqual(plan["人物"]["merged"], ["character:hero←「阿照（老年）」"])
        still = json.loads((self.root / "素材" / "人物.json").read_text(encoding="utf-8"))["characters"]
        self.assertEqual(len(still), 2)
        self.assertEqual(self.su.anchor(str(self.root), force=True)["anchor_rev"], 1)

        applied = self.su.collapse_duplicate_ids(str(self.root), apply_changes=True)
        self.assertEqual(applied["人物"]["after"], 1)
        merged = json.loads((self.root / "素材" / "人物.json").read_text(encoding="utf-8"))["characters"]
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["name"], "阿照", "保留先出现那条的字段")
        self.assertEqual(merged[0]["basis"], "后批重复建的同一条", "空字段由后条补上")
        self.assertNotIn("ID_DUP", [e["code"] for e in self.su.check(str(self.root))["errors"]])


    def test_check_lists_entities_no_episode_references(self):
        """C13 孤儿建档：建了没人用。只报不删（删档属业务判断）。"""
        (self.root / "素材" / "人物.json").write_text(json.dumps({"characters": [
            {"id": "hero", "name": "阿照", "bio_arc": "从A到B", "bio_language": "短句", "bio_crack": "数头发",
             "bio_pressure": "让", "bio_address": "照"},
            {"id": "nobody", "name": "路人乙", "source": "story_units"}]}, ensure_ascii=False), encoding="utf-8")
        book = json.loads((self.root / "剧本" / "分集.json").read_text(encoding="utf-8"))
        book["episodes"][0]["cast_refs"] = ["@character:hero"]      # E1 确实引用了 hero
        (self.root / "剧本" / "分集.json").write_text(json.dumps(book, ensure_ascii=False), encoding="utf-8")
        rep = self.su.check(str(self.root))
        hits = [w for w in rep["warnings"] if w["code"] == "UNREF_ASSET"]
        self.assertEqual(len(hits), 1)
        self.assertIn("nobody", hits[0]["message"])
        self.assertIn("第一步新造", hits[0]["message"])
        self.assertNotIn("hero「阿照」", hits[0]["message"], "被 E1 引用的不该进孤儿清单")
        self.assertTrue(rep["ok"], "孤儿建档只 warn，不阻断锚定")


    def test_same_id_from_different_batches_becomes_alias_not_duplicate(self):
        """ID_DUP 的根因：两批各自造一条同 id。撞 id 必须并成一条、把叫法登记成别名。"""
        self.su.apply_story(str(self.root), {"roster": {"characters": [
            {"id": "shizhe", "name": "猎仙使", "gender": "男"}]}})
        again = self.su.apply_story(str(self.root), {"roster": {"characters": [
            {"id": "shizhe", "name": "白袍猎仙使", "gender": "男"}]}})
        chars = json.loads((self.root / "素材" / "人物.json").read_text(encoding="utf-8"))["characters"]
        self.assertEqual(len([c for c in chars if c["id"] == "shizhe"]), 1)
        self.assertIn("白袍猎仙使", chars[0]["aliases"], "另一种叫法进别名，后面 fillrefs 才认得")
        self.assertEqual(again["created"], [])
        self.assertEqual([e["code"] for e in self.su.check(str(self.root))["errors"] if e["code"] == "ID_DUP"], [])

    def test_same_batch_cannot_create_two_rows_with_one_id(self):
        res = self.su.apply_story(str(self.root), {"roster": {"characters": [
            {"id": "dao", "name": "老道", "gender": "男"},
            {"id": "dao", "name": "持刀老道", "gender": "男"}]}})
        chars = json.loads((self.root / "素材" / "人物.json").read_text(encoding="utf-8"))["characters"]
        self.assertEqual(len([c for c in chars if c["id"] == "dao"]), 1, "同一批里也必须走去重")
        self.assertEqual(res["created"], ["@character:dao"])

    def test_fillrefs_matches_by_alias_family(self):
        """正文写「白袍猎仙使：…」而档案叫「猎仙使」也要挂上引用，否则反查索引是瞎的。"""
        (self.root / "素材" / "人物.json").write_text(json.dumps({"characters": [
            {"id": "shizhe", "name": "猎仙使", "aliases": ["白袍猎仙使"]}]}, ensure_ascii=False), encoding="utf-8")
        (self.root / "素材" / "场景.json").write_text(json.dumps({"scenes": [
            {"id": "jiangjun", "name": "将军府", "aliases": ["北境军营校场"]}]}, ensure_ascii=False), encoding="utf-8")
        eps = json.loads((self.root / "剧本" / "分集.json").read_text(encoding="utf-8"))
        eps["episodes"][0]["text"] = "【场景：北境军营校场／日】\n白袍猎仙使：放箭。\n沈砚：我猎的是什么？"
        (self.root / "剧本" / "分集.json").write_text(json.dumps(eps, ensure_ascii=False), encoding="utf-8")
        res = self.su.fill_refs_from_text(str(self.root), apply_changes=True)
        self.assertEqual(len(res["episodes"]), 1)
        row = json.loads((self.root / "剧本" / "分集.json").read_text(encoding="utf-8"))["episodes"][0]
        self.assertEqual(row["scene_refs"], ["@scene:jiangjun"])
        self.assertEqual(row["cast_refs"], ["@character:shizhe"], "别名要能命中，没登记的别称才放过")


    def test_pending_settings_batches_by_missing_fields(self):
        """U2 一轮写不完一百多个实体：分批依据必须按"还缺什么"算，不是按总数。"""
        rows = []
        for i in range(30):
            row = {"id": f"c{i}", "name": f"人物{i}"}
            if i == 0:
                row.update({k: "已填" for k in ("bio_language", "bio_crack", "bio_pressure",
                                                "bio_address", "bio_arc")})
            rows.append(row)
        (self.root / "素材" / "人物.json").write_text(json.dumps({"characters": rows}, ensure_ascii=False), encoding="utf-8")
        pend = self.su.pending_settings(str(self.root), per_round=12)
        self.assertEqual(pend["remaining"], 29)
        self.assertEqual(len(pend["batch"]["characters"]), 12)
        self.assertNotIn("c0", [c["id"] for c in pend["batch"]["characters"]], "已填齐的不该再占批次")

    def test_prune_only_touches_first_step_creations(self):
        (self.root / "素材" / "道具.json").write_text(json.dumps({"props": [
            {"id": "used", "name": "封仙钉", "usage_boundary": "b"},
            {"id": "ghost", "name": "路人剑", "source": "story_units"},
            {"id": "legacy", "name": "旧档案道具", "usage_boundary": "b"}]}, ensure_ascii=False), encoding="utf-8")
        book = json.loads((self.root / "剧本" / "分集.json").read_text(encoding="utf-8"))
        book["episodes"][0]["key_asset_refs"] = ["@prop:used"]
        (self.root / "剧本" / "分集.json").write_text(json.dumps(book, ensure_ascii=False), encoding="utf-8")
        res = self.su.prune_unreferenced_new(str(self.root))
        self.assertEqual([o["id"] for o in res["candidates"]], ["ghost"], "既有档案不碰、被引用的不删")
        self.assertEqual(self.su.prune_unreferenced_new(str(self.root))["removed"], 0)
        kept = json.loads((self.root / "素材" / "道具.json").read_text(encoding="utf-8"))["props"]
        self.assertEqual(len(kept), 3, "dry-run 不写盘")
        self.su.prune_unreferenced_new(str(self.root), apply_changes=True)
        kept = json.loads((self.root / "素材" / "道具.json").read_text(encoding="utf-8"))["props"]
        self.assertEqual(sorted(k["id"] for k in kept), ["legacy", "used"])


    def test_fillrefs_scans_props_in_action_lines_not_only_dialogue(self):
        """道具在正文里是被动作提及的（玄黑猎仙旗/九字铁牌），只扫台词行就把它们全成了孤儿。"""
        (self.root / "素材" / "道具.json").write_text(json.dumps({"props": [
            {"id": "qi", "name": "玄黑猎仙旗", "usage_boundary": "b"},
            {"id": "tie", "name": "九字铁牌", "usage_boundary": "b"},
            {"id": "never", "name": "从不出场的东西", "usage_boundary": "b"}]}, ensure_ascii=False), encoding="utf-8")
        book = json.loads((self.root / "剧本" / "分集.json").read_text(encoding="utf-8"))
        book["episodes"][0]["text"] = "【场景：猎仙坛／日】\n玄黑猎仙旗在风里绷紧。\n阿照：看见了？"
        book["episodes"][0]["key_asset_refs"] = ["@prop:tie"]      # 已有引用不能被重复追加
        (self.root / "剧本" / "分集.json").write_text(json.dumps(book, ensure_ascii=False), encoding="utf-8")
        self.su.fill_refs_from_text(str(self.root), apply_changes=True)
        row = json.loads((self.root / "剧本" / "分集.json").read_text(encoding="utf-8"))["episodes"][0]
        self.assertEqual(row["key_asset_refs"], ["@prop:tie", "@prop:qi"])
        self.assertNotIn("@prop:never", row["key_asset_refs"])


if __name__ == "__main__":
    unittest.main()