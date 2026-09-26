# -*- coding: utf-8 -*-
"""E10 Skill 选择稳定性回归（update.md 三、E10）：显性选择 + 默认填入 + 任务创建冻结快照。

修法（用户拍板）：
- style_for / image_skill_id 只吃 剧本/style.json 的显式选择，不再隐式推导；
- ensure_explicit_defaults 首次消费时把旧隐式默认（唯一启用者）冻结写回 style.json，
  多个/无启用写 "auto"；只补缺失键，不覆盖已有显式选择（含用户手选 "auto"）；
- 分镜 cfg / 大纲.json / 资产索引记录本次实际注入的 skill 快照 {id,name,sha256前12位}。
"""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workbench" / "tools"))

import skill_lib

# 假 Skill 库：storyboard 两个启用（测"多启用不注入"）、image/script 各一个启用
# （测"唯一启用冻结成默认"）、acting 零启用（测"无启用写 auto"）
FAKE_SKILLS = [
    {"id": "dir-a", "name": "导演甲", "category": "directing", "target": "storyboard",
     "enabled": True, "builtin": False, "description": "", "path": "directing/a.md", "negative": ""},
    {"id": "dir-b", "name": "导演乙", "category": "directing", "target": "storyboard",
     "enabled": True, "builtin": False, "description": "", "path": "directing/b.md", "negative": ""},
    {"id": "img-a", "name": "画风甲", "category": "image-style", "target": "image",
     "enabled": True, "builtin": False, "description": "", "path": "image-style/a.md", "negative": ""},
    {"id": "scr-a", "name": "拆剧甲", "category": "script", "target": "script",
     "enabled": True, "builtin": False, "description": "", "path": "script/a.md", "negative": ""},
    {"id": "act-off", "name": "表演停", "category": "acting", "target": "acting",
     "enabled": False, "builtin": False, "description": "", "path": "acting/a.md", "negative": ""},
]
FAKE_BODY = {s["id"]: f"{s['name']}正文" for s in FAKE_SKILLS}


def _patched():
    """统一替换 Skill 库视图，测试不进仓库 skills 目录。"""
    return (mock.patch.object(skill_lib, "list_skills", lambda: [dict(s) for s in FAKE_SKILLS]),
            mock.patch.object(skill_lib, "load_skill_text", lambda sid: FAKE_BODY.get(sid, "")))


def _sha(skill_id):
    return hashlib.sha256(FAKE_BODY[skill_id].encode("utf-8")).hexdigest()[:12]


class ExplicitSelectionTests(unittest.TestCase):
    """取消隐式推导 + 默认填入写回。"""

    def test_no_implicit_injection_when_two_enabled(self):
        # 两个启用 + 无显式选择 -> 不注入（旧隐式规则也是），且冻结为显式 "auto"
        p1, p2 = _patched()
        with p1, p2, tempfile.TemporaryDirectory() as td:
            self.assertEqual(skill_lib.style_for(td, "storyboard"), "")
            style = json.loads((Path(td) / "剧本" / "style.json").read_text(encoding="utf-8"))
            self.assertEqual(style["storyboard"], "auto")

    def test_default_fill_freezes_unique_enabled_skill(self):
        # 唯一启用者首次消费时被冻结成显式选择并注入；无启用的 target 写 "auto"
        p1, p2 = _patched()
        with p1, p2, tempfile.TemporaryDirectory() as td:
            self.assertEqual(skill_lib.style_for(td, "image"), "画风甲正文\n")
            style = json.loads((Path(td) / "剧本" / "style.json").read_text(encoding="utf-8"))
            self.assertEqual(style["image"], "img-a")    # 唯一启用 -> 冻结为该 id
            self.assertEqual(style["script"], "scr-a")
            self.assertEqual(style["storyboard"], "auto")  # 多启用 -> auto
            self.assertEqual(style["acting"], "auto")      # 无启用 -> auto

    def test_frozen_default_survives_library_changes(self):
        # 冻结后再加第二个启用 Skill，项目选择不飘移（E10 核心诉求）
        p1, p2 = _patched()
        with p1, p2, tempfile.TemporaryDirectory() as td:
            skill_lib.ensure_explicit_defaults(td)  # 首次：image 冻结为 img-a
            grown = FAKE_SKILLS + [{"id": "img-b", "name": "画风乙", "category": "image-style",
                                    "target": "image", "enabled": True, "builtin": False,
                                    "description": "", "path": "image-style/b.md", "negative": ""}]
            with mock.patch.object(skill_lib, "list_skills", lambda: grown):
                style = skill_lib.ensure_explicit_defaults(td)
                self.assertEqual(style["image"], "img-a")  # 仍是冻结值，不随库变化

    def test_ensure_never_overwrites_existing_explicit_choice(self):
        # 已有显式选择（含用户手选 "auto"）一律不覆盖
        p1, p2 = _patched()
        with p1, p2, tempfile.TemporaryDirectory() as td:
            proj = Path(td)
            (proj / "剧本").mkdir()
            (proj / "剧本" / "style.json").write_text(
                json.dumps({"storyboard": "dir-b", "image": "auto"}, ensure_ascii=False), encoding="utf-8")
            style = skill_lib.ensure_explicit_defaults(td)
            self.assertEqual(style["storyboard"], "dir-b")
            self.assertEqual(style["image"], "auto")  # 手选 auto 不被默认填入改写
            self.assertEqual(skill_lib.style_for(td, "storyboard"), "导演乙正文\n")
            self.assertEqual(skill_lib.style_for(td, "image"), "")  # auto -> 不注入
            # 用户改选 dir-a 后再调 ensure，依然不覆盖
            skill_lib.set_project_style(td, {**style, "storyboard": "dir-a"})
            self.assertEqual(skill_lib.ensure_explicit_defaults(td)["storyboard"], "dir-a")
            self.assertEqual(skill_lib.style_for(td, "storyboard"), "导演甲正文\n")

    def test_style_directive_quoted_after_a_lead_phrase(self):
        """画风取词：引号前允许一段说明词（cinematic-real 写「追加到生图提示词末尾——」）。
        取不到引号会回退整篇正文，把「禁止：卡通…」当正向画风词注入，且与 negative 双写。"""
        from skill_lib import resolve_asset_style_text

        cinematic, _src = resolve_asset_style_text({}, "cinematic-real")
        self.assertTrue(cinematic.startswith("35mm 胶片质感"))
        self.assertNotIn("追加到生图提示词末尾", cinematic, "整篇正文回退=包装语被注入成画风词")
        self.assertLess(len(cinematic), 200, "只应取引号内指令，不应吞下整篇 skill")
        ghibli, _ = resolve_asset_style_text({}, "ghibli-soft")
        self.assertTrue(ghibli.startswith("日式动漫赛璐璐插画风格"))

    def test_image_entry_matches_text_entry(self):
        # 图片入口与文本入口同一口径：都吃显式选择，override 恒优先
        p1, p2 = _patched()
        with p1, p2, tempfile.TemporaryDirectory() as td:
            self.assertEqual(skill_lib.image_skill_id(td), "img-a")  # 默认填入后与 style_for 同源
            self.assertEqual(skill_lib.image_skill_id(td, "dir-b"), "dir-b")  # 资产级覆盖优先
            skill_lib.set_project_style(td, {"image": "auto"})
            self.assertEqual(skill_lib.image_skill_id(td), "")       # auto -> 空，与 style_for 一致
            self.assertEqual(skill_lib.style_for(td, "image"), "")

    def test_ensure_does_not_create_dirs_for_nonexistent_project(self):
        # 非真实项目路径只读不写（防止调用方传错路径时在磁盘上造目录）
        p1, p2 = _patched()
        with p1, p2, tempfile.TemporaryDirectory() as td:
            ghost = str(Path(td) / "nonexistent-proj")
            style = skill_lib.ensure_explicit_defaults(ghost)
            self.assertEqual(style["storyboard"], "auto")  # 仍返回解析结果
            self.assertFalse(Path(ghost).exists())         # 但不落盘

    def test_auto_style_never_leaks_into_style_ref(self):
        # 回归：style.json 显式 "auto" 不得在分镜装配里拼出 @style:auto 假引用
        from prompt_assembler import assemble_shot_prompt
        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            (project / "剧本").mkdir()
            (project / "剧本" / "style.json").write_text(
                json.dumps({"image": "auto"}, ensure_ascii=False), encoding="utf-8")
            shot = {"id": "S01", "scene": "room", "action": "甲抬头看向窗外", "lines": []}
            out = assemble_shot_prompt(shot, {"dir": str(project), "board": {"actors": {}, "shots": []}})
            self.assertFalse(any(str(r).startswith("@style:") for r in out["asset_refs"]))

    def test_asset_registry_skips_auto_style_record(self):
        # 回归：资产注册表不把显式 "auto" 注册成 @style:auto 记录
        import asset_registry
        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            (project / "剧本").mkdir()
            (project / "剧本" / "style.json").write_text(
                json.dumps({"image": "auto"}, ensure_ascii=False), encoding="utf-8")
            registry = asset_registry.AssetRegistry(str(project))
            self.assertNotIn("@style:auto", {r["ref"] for r in registry.list()})


class SkillSnapshotTests(unittest.TestCase):
    """任务创建冻结快照：{target: {id, name, sha256(正文)前12位}}。"""

    def test_snapshot_records_used_skill_with_sha(self):
        p1, p2 = _patched()
        with p1, p2, tempfile.TemporaryDirectory() as td:
            proj = Path(td)
            (proj / "剧本").mkdir()
            (proj / "剧本" / "style.json").write_text(
                json.dumps({"storyboard": "dir-a"}, ensure_ascii=False), encoding="utf-8")
            snap = skill_lib.skill_snapshot_for(td, ["storyboard"])
            self.assertEqual(snap, {"storyboard": {"id": "dir-a", "name": "导演甲", "sha": _sha("dir-a")}})

    def test_snapshot_omits_auto_and_disabled(self):
        p1, p2 = _patched()
        with p1, p2, tempfile.TemporaryDirectory() as td:
            proj = Path(td)
            (proj / "剧本").mkdir()
            (proj / "剧本" / "style.json").write_text(
                json.dumps({"storyboard": "auto", "acting": "act-off"}, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(skill_lib.skill_snapshot_for(td, ["storyboard", "acting"]), {})

    def test_snapshot_override_wins(self):
        p1, p2 = _patched()
        with p1, p2, tempfile.TemporaryDirectory() as td:
            snap = skill_lib.skill_snapshot_for(td, ["image"], overrides={"image": "img-a"})
            self.assertEqual(snap["image"]["id"], "img-a")
            self.assertEqual(snap["image"]["sha"], _sha("img-a"))


class PipelineFreezeTests(unittest.TestCase):
    """三条链路的产物冻结：资产生图索引 / 大纲.json / 分镜 cfg。"""

    def test_gen_asset_images_index_carries_snapshot(self):
        import gen_asset_images as gen

        class _FakeClient:
            id = "fake-vendor"

            def __init__(self):
                self.calls = []
                self.models = {"image": "fake-image"}  # main() 会读 models["image_edit"]
                self.cfg = {}

            def model(self, kind):
                return "fake-image"

            def generate_image(self, prompt, out, timeout=None, negative_prompt=None,
                               image_refs=None, mode=None, **kwargs):
                self.calls.append(out)
                Path(out).write_bytes(b"fake-image")

        p1, p2 = _patched()
        with p1, p2, tempfile.TemporaryDirectory() as td:
            project = Path(td)
            (project / "素材").mkdir()
            (project / "素材" / "场景.json").write_text(json.dumps(
                {"scenes": [{"id": "yetai", "name": "夜台", "image_prompt": "夜台全景"}]},
                ensure_ascii=False), encoding="utf-8")
            (project / "剧本").mkdir()
            (project / "剧本" / "style.json").write_text(
                json.dumps({"image": "img-a"}, ensure_ascii=False), encoding="utf-8")
            client = _FakeClient()
            argv = ["gen_asset_images.py", str(project), "--kind", "scene"]
            with mock.patch.object(gen, "pick_vendor", lambda v=None: client.id), \
                 mock.patch.object(gen, "VendorClient", lambda vid: client), \
                 mock.patch.object(sys, "argv", argv):
                try:
                    gen.main()
                except SystemExit:
                    pass
            index = json.loads((project / "素材" / "素材图.json").read_text(encoding="utf-8"))
            snap = index["场景"]["yetai"]["skill_snapshot"]
            self.assertEqual(snap, {"image": {"id": "img-a", "name": "画风甲", "sha": _sha("img-a")}})

    def test_gen_asset_images_auto_writes_no_snapshot(self):
        import gen_asset_images as gen

        class _FakeClient:
            id = "fake-vendor"

            def __init__(self):
                self.models = {"image": "fake-image"}  # main() 会读 models["image_edit"]
                self.cfg = {}

            def model(self, kind):
                return "fake-image"

            def generate_image(self, prompt, out, timeout=None, negative_prompt=None,
                               image_refs=None, mode=None, **kwargs):
                Path(out).write_bytes(b"fake-image")

        p1, p2 = _patched()
        with p1, p2, tempfile.TemporaryDirectory() as td:
            project = Path(td)
            (project / "素材").mkdir()
            (project / "素材" / "场景.json").write_text(json.dumps(
                {"scenes": [{"id": "yetai", "name": "夜台", "image_prompt": "夜台全景"}]},
                ensure_ascii=False), encoding="utf-8")
            (project / "剧本").mkdir()
            (project / "剧本" / "style.json").write_text(
                json.dumps({"image": "auto"}, ensure_ascii=False), encoding="utf-8")
            argv = ["gen_asset_images.py", str(project), "--kind", "scene"]
            with mock.patch.object(gen, "pick_vendor", lambda v=None: "fake-vendor"), \
                 mock.patch.object(gen, "VendorClient", lambda vid: _FakeClient()), \
                 mock.patch.object(sys, "argv", argv):
                try:
                    gen.main()
                except SystemExit:
                    pass
            index = json.loads((project / "素材" / "素材图.json").read_text(encoding="utf-8"))
            self.assertNotIn("skill_snapshot", index["场景"]["yetai"])

    def test_expand_outline_writes_snapshot(self):
        import creation_pipeline as cp

        outline = {"main_line": "主线", "visual_style": "参考",
                   "episodes": [{"id": "E1", "title": "起", "summary": "概", "duration_min": 3},
                                {"id": "E2", "title": "落", "summary": "概", "duration_min": 3}]}
        p1, p2 = _patched()
        with p1, p2, tempfile.TemporaryDirectory() as td:
            project = Path(td)
            (project / "剧本").mkdir()
            (project / "剧本" / "style.json").write_text(
                json.dumps({"script": "scr-a"}, ensure_ascii=False), encoding="utf-8")
            with mock.patch.object(cp, "pick_vendor", lambda v=None: "fake"), \
                 mock.patch.object(cp, "VendorClient", lambda vid: object()), \
                 mock.patch.object(cp, "chat_retry", lambda *a, **k: json.dumps(outline, ensure_ascii=False)):
                cp.cmd_expand(str(project), None, "一个关于夜间站台重逢的故事", 2, None)
            data = json.loads((project / "剧本" / "大纲.json").read_text(encoding="utf-8"))
            self.assertEqual(data["skill_snapshot"],
                             {"script": {"id": "scr-a", "name": "拆剧甲", "sha": _sha("scr-a")}})

    def test_storyboard_cfg_carries_snapshot(self):
        import creation_pipeline as cp

        llm_out = {
            "shots": [{"id": "S1", "dur": 4, "shot_size": "中景", "camera_move": "固定",
                       "angle": "平视", "cam": "wide", "scene": "room", "action": "甲进门",
                       "prompt_image": "静帧画面描述", "prompt_video": "视频画面描述",
                       "lines": []}],
            "video_units": [{"shot_ids": ["S1"], "title": "段一",
                             "prompt_video": "整段视频描述", "prompt_grid": "宫格描述"}],
        }
        p1, p2 = _patched()
        with p1, p2, tempfile.TemporaryDirectory() as td:
            project = Path(td)
            (project / "剧本").mkdir()
            (project / "剧本" / "分集.json").write_text(json.dumps(
                {"episodes": [{"id": "E1", "text": "甲走进车间。乙：来了。"}], "rev": 1},
                ensure_ascii=False), encoding="utf-8")
            (project / "剧本" / "style.json").write_text(
                json.dumps({"storyboard": "dir-a"}, ensure_ascii=False), encoding="utf-8")
            with mock.patch.object(cp, "pick_vendor", lambda v=None: "fake"), \
                 mock.patch.object(cp, "VendorClient", lambda vid: object()), \
                 mock.patch.object(cp, "chat_retry",
                                   lambda *a, **k: json.dumps(llm_out, ensure_ascii=False)):
                cp.cmd_storyboard(str(project), None, "E1")
            cfg = json.loads((project / "分镜" / "剧本_E1.json").read_text(encoding="utf-8"))
            self.assertEqual(cfg["skill_snapshot"],
                             {"storyboard": {"id": "dir-a", "name": "导演甲", "sha": _sha("dir-a")}})


if __name__ == "__main__":
    unittest.main()
