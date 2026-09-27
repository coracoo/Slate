# -*- coding: utf-8 -*-
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workbench" / "tools"))


def _load_gen():
    import gen_asset_images
    return gen_asset_images


class _FakeClient:
    """生图厂商桩：generate_image 直接写文件并记录入参。"""

    def __init__(self, vendor_id="fake-vendor"):
        self.id = vendor_id
        self.models = {"image": "fake-image"}
        self.cfg = {}
        self.calls = []

    def model(self, kind):
        return self.models.get(kind, "")

    def generate_image(self, prompt, out, timeout=None, negative_prompt=None,
                       image_refs=None, mode=None, **kwargs):
        self.calls.append({"prompt": prompt, "out": out,
                           "image_refs": list(image_refs or []), "mode": mode,
                           "extra": kwargs.get("extra")})
        Path(out).write_bytes(b"fake-image")


def _run_main(gen, project, *extra_args, client=None):
    """以桩厂商跑 gen_asset_images.main()，返回 (exit_code, fake_client)。"""
    client = client or _FakeClient()
    argv = ["gen_asset_images.py", str(project)] + list(extra_args)
    with mock.patch.object(gen, "pick_vendor", lambda v=None: client.id), \
         mock.patch.object(gen, "VendorClient", lambda vid: client), \
         mock.patch.object(sys, "argv", argv):
        code = 0
        try:
            gen.main()
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, client


class AssetGenerationReferenceTests(unittest.TestCase):
    def test_local_comfyui_uses_edit_mode_when_asset_has_references(self):
        from gen_asset_images import asset_image_mode

        self.assertEqual(asset_image_mode("local-comfyui", ["parent.png"]), "edit")

    def test_local_comfyui_uses_generate_mode_without_references(self):
        from gen_asset_images import asset_image_mode

        self.assertEqual(asset_image_mode("local-comfyui", []), "generate")

    def test_mode_label_reflects_vendor_and_refs(self):
        """日志标签按厂商与实际参考图命名：doubao 带参考图应标图生图而非「生图/Z-Image」。"""
        from gen_asset_images import mode_label

        self.assertEqual(mode_label("local-comfyui", "edit", True), "改图/模型匹配")
        self.assertEqual(mode_label("local-comfyui", "generate", False), "生图/模型匹配")
        self.assertEqual(mode_label("doubao", "generate", True), "图生图/doubao")
        self.assertEqual(mode_label("doubao", "generate", False), "文生图/doubao")

    def test_state_only_run_keeps_other_states_indexed(self):
        """只重生成一个状态时，同角色其它状态的索引与时间锚定不得被抹掉。"""
        gen = _load_gen()
        src_file, rows_key, _pfield, zone = gen.KINDS["character"]
        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            (project / "素材" / zone).mkdir(parents=True)
            (project / "素材" / src_file).write_text(json.dumps({rows_key: [{
                "id": "hero", "name": "英雄", "sheet_prompt": "全身立绘",
                "states": [{"id": "S1", "label": "反派期", "episodes": ["E1"], "camp": "敌", "sheet_prompt": "黑风衣"},
                           {"id": "S2", "label": "盟友期", "episodes": ["E6"], "camp": "友", "sheet_prompt": "白衬衫"}]}]},
                ensure_ascii=False), encoding="utf-8")
            for name in ("hero.png", "hero__S1.png", "hero__S2.png"):
                (project / "素材" / zone / name).write_bytes(b"png")
            # S2 只存在于索引里（本轮不生成它）：索引被整块重写时，磁盘扫描也补不回该提示词
            (project / "素材" / "素材图.json").write_text(json.dumps({zone: {
                "hero": {"path": f"素材/{zone}/hero.png", "states": {
                    "S1": {"path": f"素材/{zone}/hero__S1.png", "prompt": "旧索引词"},
                    "S2": {"path": f"素材/{zone}/hero__S2.png", "prompt": "仅索引持有",
                           "episodes": ["E6"], "camp": "友"}}}}}, ensure_ascii=False), encoding="utf-8")
            _code, _client = _run_main(gen, project, "--kind", "character", "--id", "hero", "--state-id", "S1")
            saved = json.loads((project / "素材" / "素材图.json").read_text(encoding="utf-8"))
            states = saved[zone]["hero"]["states"]
            self.assertEqual(sorted(states), ["S1", "S2"], "索引整块重写会丢掉未参与本轮的其它状态")
            self.assertEqual(states["S2"]["prompt"], "仅索引持有")
            self.assertEqual(states["S1"]["episodes"], ["E1"], "补缺跳过分支也要带 episodes/camp")
            self.assertEqual(states["S1"]["camp"], "敌")

    def test_existing_images_keep_generation_provenance_and_only_record_new_desired_spec(self):
        """补缺运行不能把旧像素伪装成由当前提示词和当前 Skill 生成。"""
        gen = _load_gen()
        src_file, rows_key, _pfield, zone = gen.KINDS["character"]
        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            (project / "素材" / zone).mkdir(parents=True)
            (project / "素材" / src_file).write_text(json.dumps({rows_key: [{
                "id": "hero", "name": "英雄", "sheet_prompt": "新的全身立绘",
                "states": [{"id": "S1", "label": "新阶段", "episodes": ["E2"],
                            "camp": "友", "sheet_prompt": "新的白衬衫"}],
            }]}, ensure_ascii=False), encoding="utf-8")
            for name in ("hero.png", "hero__S1.png"):
                (project / "素材" / zone / name).write_bytes(b"old-pixels")
            old_skill = {"image": {"id": "old", "sha12": "123"}}
            (project / "素材" / "素材图.json").write_text(json.dumps({zone: {
                "hero": {"path": f"素材/{zone}/hero.png", "prompt": "旧母图提示词",
                         "skill_snapshot": old_skill, "states": {
                             "S1": {"path": f"素材/{zone}/hero__S1.png", "prompt": "旧状态提示词",
                                    "skill_snapshot": old_skill, "label": "旧阶段"}
                         }}
            }}, ensure_ascii=False), encoding="utf-8")
            with mock.patch.object(gen.skill_lib, "skill_snapshot_for", return_value={"image": {"id": "new", "sha12": "456"}}):
                _run_main(gen, project, "--kind", "character", "--id", "hero")
            saved = json.loads((project / "素材" / "素材图.json").read_text(encoding="utf-8"))[zone]["hero"]
            self.assertEqual(saved["prompt"], "旧母图提示词")
            self.assertEqual(saved["skill_snapshot"], old_skill)
            self.assertIn("新的全身立绘", saved["desired_spec"]["prompt"])
            self.assertTrue(saved["stale"])
            state = saved["states"]["S1"]
            self.assertEqual(state["prompt"], "旧状态提示词")
            self.assertEqual(state["skill_snapshot"], old_skill)
            self.assertIn("新的白衬衫", state["desired_spec"]["prompt"])
            self.assertEqual(state["episodes"], ["E2"])
            self.assertTrue(state["stale"])

    def test_parent_ref_child_uses_parent_image_as_reference(self):
        """子素材口径：parent_ref 以父资产母图为参考改图；related_refs / 提示词 @token 仍不进依赖。"""
        from gen_asset_images import collect_asset_image_plan

        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            (project / "素材").mkdir(parents=True)
            (project / "素材" / "人物").mkdir(parents=True)
            (project / "素材" / "人物" / "hero.png").write_bytes(b"hero")
            (project / "素材" / "道具.json").write_text(json.dumps({
                "props": [{
                    "id": "collar",
                    "name": "项圈",
                    "image_prompt": "黑色项圈，佩戴在 @character:hero 颈上",
                    "parent_ref": "@character:hero",
                    "related_refs": ["@character:hero"],
                }]
            }, ensure_ascii=False), encoding="utf-8")
            plan = collect_asset_image_plan(project, "prop", "collar", "local-comfyui")

            self.assertEqual(len(plan), 1)
            self.assertEqual(plan[0]["reference_tokens"], ["@character:hero"])
            self.assertEqual(plan[0]["refs"], [str(project / "素材" / "人物" / "hero.png")])
            self.assertEqual(plan[0]["missing_refs"], [])
            self.assertEqual(plan[0]["mode"], "edit")

    def test_parent_ref_missing_parent_degrades_to_generate(self):
        """子素材父图缺失：降级为无参考生成（记 missing_refs，不拦截）。"""
        from gen_asset_images import collect_asset_image_plan

        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            (project / "素材").mkdir(parents=True)
            (project / "素材" / "道具.json").write_text(json.dumps({
                "props": [{
                    "id": "collar", "name": "项圈", "image_prompt": "黑色项圈",
                    "parent_ref": "@character:hero",
                }]
            }, ensure_ascii=False), encoding="utf-8")
            plan = collect_asset_image_plan(project, "prop", "collar", "local-comfyui")

            self.assertEqual(plan[0]["mode"], "generate")
            self.assertEqual(plan[0]["reference_tokens"], ["@character:hero"])
            self.assertEqual(plan[0]["missing_refs"], ["@character:hero"])

    def test_strict_plan_marks_missing_parent_as_not_executable(self):
        """ChatGPT 浏览器执行采用严格依赖；父图缺失时等待而不是退化。"""
        from gen_asset_images import collect_asset_image_plan

        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            (project / "素材").mkdir(parents=True)
            (project / "素材" / "道具.json").write_text(json.dumps({
                "props": [{
                    "id": "collar", "name": "项圈", "image_prompt": "黑色项圈",
                    "parent_ref": "@character:hero",
                }]
            }, ensure_ascii=False), encoding="utf-8")

            plan = collect_asset_image_plan(
                project, "prop", "collar", "chatgpt", strict_dependencies=True
            )

            self.assertFalse(plan[0]["can_execute"])
            self.assertEqual(plan[0]["execution_state"], "waiting_dependencies")
            self.assertEqual(plan[0]["missing_refs"], ["@character:hero"])

    def test_derived_from_parent_image_is_edit_input(self):
        """derived_from 派生子图以父资产母图为参考。"""
        from gen_asset_images import collect_asset_image_plan

        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            (project / "素材").mkdir(parents=True)
            (project / "素材" / "场景").mkdir(parents=True)
            (project / "素材" / "场景" / "yetai.png").write_bytes(b"scene")
            (project / "素材" / "场景.json").write_text(json.dumps({
                "scenes": [{
                    "id": "yetai_hei",
                    "name": "夜台·黑",
                    "image_prompt": "夜台变黑版本",
                    "derived_from": "@scene:yetai",
                }]
            }, ensure_ascii=False), encoding="utf-8")
            plan = collect_asset_image_plan(project, "scene", "yetai_hei", "local-comfyui")

            self.assertEqual(plan[0]["mode"], "edit")
            self.assertEqual(plan[0]["refs"], [str(project / "素材" / "场景" / "yetai.png")])
            self.assertEqual(plan[0]["missing_refs"], [])

    def test_derived_from_missing_parent_degrades_to_generate(self):
        """派生父图缺失：降级为无参考生成（记 missing_refs，不拦截）。"""
        from gen_asset_images import collect_asset_image_plan

        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            (project / "素材").mkdir(parents=True)
            (project / "素材" / "场景.json").write_text(json.dumps({
                "scenes": [{
                    "id": "yetai_hei",
                    "name": "夜台·黑",
                    "image_prompt": "夜台变黑版本",
                    "derived_from": "@scene:yetai",
                }]
            }, ensure_ascii=False), encoding="utf-8")
            plan = collect_asset_image_plan(project, "scene", "yetai_hei", "local-comfyui")

            self.assertEqual(plan[0]["mode"], "generate")
            self.assertEqual(plan[0]["reference_tokens"], ["@scene:yetai"])
            self.assertEqual(plan[0]["missing_refs"], ["@scene:yetai"])

    def test_derived_from_orders_parents_before_children(self):
        """拓扑排序只按 derived_from 建边；related_refs 不影响顺序。"""
        from gen_asset_images import collect_asset_image_plan

        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            (project / "素材").mkdir(parents=True)
            (project / "素材" / "道具.json").write_text(json.dumps({
                "props": [
                    {"id": "child", "name": "子", "image_prompt": "子",
                     "derived_from": "@prop:parent"},
                    {"id": "sibling", "name": "关联", "image_prompt": "关联",
                     "related_refs": ["@prop:parent"]},
                    {"id": "parent", "name": "母", "image_prompt": "母"},
                ]
            }, ensure_ascii=False), encoding="utf-8")
            plan = collect_asset_image_plan(project, "prop", None, "local-comfyui")

            ids = [item["id"] for item in plan]
            self.assertLess(ids.index("parent"), ids.index("child"))
            # related_refs 不构成依赖：sibling 无 reference_tokens，顺序不受约束
            sibling = next(item for item in plan if item["id"] == "sibling")
            self.assertEqual(sibling["reference_tokens"], [])
            self.assertEqual(sibling["refs"], [])

    def test_related_parent_ring_does_not_block_plans(self):
        """related/parent 互指不成环：related 不是依赖边——甲（related→乙）无依赖，
        乙（parent→甲）以甲为父级；拓扑先甲后乙，两者都可生成、互不阻塞。"""
        from gen_asset_images import collect_asset_image_plan

        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            (project / "素材").mkdir(parents=True)
            (project / "素材" / "道具.json").write_text(json.dumps({
                "props": [
                    {"id": "a", "name": "甲", "image_prompt": "甲",
                     "related_refs": ["@prop:b"]},
                    {"id": "b", "name": "乙", "image_prompt": "乙",
                     "parent_ref": "@prop:a"},
                ]
            }, ensure_ascii=False), encoding="utf-8")
            plan = collect_asset_image_plan(project, "prop", None, "local-comfyui")

            self.assertEqual(len(plan), 2)
            ids = [item["id"] for item in plan]
            self.assertLess(ids.index("a"), ids.index("b"))
            item_a = next(item for item in plan if item["id"] == "a")
            item_b = next(item for item in plan if item["id"] == "b")
            # 甲：related_refs 不进依赖，母图零参考
            self.assertEqual(item_a["reference_tokens"], [])
            self.assertEqual(item_a["mode"], "generate")
            # 乙：子素材，父级甲在本批次排队 → edit；父图未落盘记 missing 但不拦截
            self.assertEqual(item_b["reference_tokens"], ["@prop:a"])
            self.assertEqual(item_b["missing_refs"], ["@prop:a"])
            self.assertEqual(item_b["mode"], "edit")

    def test_local_comfyui_preflight_allows_builtin_edit_for_derived(self):
        sys.path.insert(0, str(ROOT / "workbench"))
        sys.path.insert(0, str(ROOT / "workbench" / "tools"))
        from server import asset_image_preflight

        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            (project / "素材").mkdir(parents=True)
            (project / "素材" / "场景").mkdir(parents=True)
            (project / "素材" / "场景" / "yetai.png").write_bytes(b"scene")
            (project / "素材" / "场景.json").write_text(json.dumps({
                "scenes": [{"id": "yetai_hei", "name": "夜台·黑", "image_prompt": "黑化",
                            "derived_from": "@scene:yetai"}]
            }, ensure_ascii=False), encoding="utf-8")
            result = asset_image_preflight(str(project), "scene", "yetai_hei", {
                "id": "local-comfyui",
                "models": {"image": "z_image.safetensors", "image_edit": "qwen_edit.safetensors"},
                "extra": {"image_edit_workflow_path": ""},
            })

            self.assertTrue(result["ok"])
            self.assertTrue(result["needs_edit"])
            self.assertEqual(result["edit_workflow"], "builtin:qwen_image_edit_2511")

    def test_preflight_missing_derived_parent_is_note_not_error(self):
        """缺图不再 400：单项任务放行并带 note；批量任务父资产排队时不记 note。"""
        sys.path.insert(0, str(ROOT / "workbench"))
        sys.path.insert(0, str(ROOT / "workbench" / "tools"))
        from server import asset_image_preflight

        vendor = {
            "id": "local-comfyui",
            "models": {"image": "z_image.safetensors", "image_edit": "qwen_edit.safetensors"},
            "extra": {},
        }
        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            (project / "素材").mkdir(parents=True)
            (project / "素材" / "道具.json").write_text(json.dumps({
                "props": [
                    {"id": "child", "name": "子素材", "image_prompt": "子图",
                     "derived_from": "@prop:parent"},
                    {"id": "parent", "name": "母素材", "image_prompt": "母图"},
                ]
            }, ensure_ascii=False), encoding="utf-8")

            single = asset_image_preflight(str(project), "prop", "child", vendor)
            self.assertTrue(single["ok"])
            self.assertTrue(single.get("notes"))
            self.assertIn("降级", single["notes"][0])

            batch = asset_image_preflight(str(project), "all", None, vendor)
            self.assertTrue(batch["ok"])
            self.assertFalse(batch.get("notes"))
            self.assertEqual([item["id"] for item in batch["plans"]], ["parent", "child"])

    def test_prompt_related_assets_are_not_image_references(self):
        """旧行为已废弃：related_refs / 提示词 @token 不再产出参考图。"""
        from gen_asset_images import _derived_refs

        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            character = project / "素材" / "人物"
            character.mkdir(parents=True)
            (character / "hero.png").write_bytes(b"character")
            index = {"人物": {"hero": {"path": "素材/人物/hero.png"}}}
            self.assertEqual(
                _derived_refs(project, index, {"related_refs": ["@character:hero"]}, "@scene:room"),
                [],
            )
            self.assertEqual(
                _derived_refs(project, index,
                              {"image_prompt": "场景中调用 @character:hero"}, "@scene:room"),
                [],
            )

    def test_asset_prompt_records_reference_mentions(self):
        from gen_asset_images import _with_reference_mentions

        prompt = _with_reference_mentions(
            "蛛丝包裹炸弹的复合状态，只生成缓冲茧本身。",
            ["@prop:yaokong_zhadan", "@prop:chili_mao"],
        )
        self.assertIn("资产引用（参考图按顺序）：@prop:yaokong_zhadan、@prop:chili_mao", prompt)
        self.assertEqual(prompt.count("资产引用（参考图按顺序）："), 1)

    def test_main_generates_ring_assets_without_blocking(self):
        """端到端：related/parent 互指不阻塞（项目 08 全灭场景回归）——母素材 jiuweihu
        零参考先出，子素材 huwei_qingwu 随后以母图为参考改图。"""
        gen = _load_gen()

        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            (project / "素材").mkdir(parents=True)
            (project / "素材" / "道具.json").write_text(json.dumps({
                "props": [
                    {"id": "huwei_qingwu", "name": "虎尾青雾", "image_prompt": "青色狐尾雾气",
                     "parent_ref": "@character:jiuweihu"},
                ]
            }, ensure_ascii=False), encoding="utf-8")
            (project / "素材" / "人物.json").write_text(json.dumps({
                "characters": [
                    {"id": "jiuweihu", "name": "九尾狐", "sheet_prompt": "九尾狐三视图",
                     "related_refs": ["@prop:huwei_qingwu"]},
                ]
            }, ensure_ascii=False), encoding="utf-8")

            code, client = _run_main(gen, project)

            self.assertEqual(code, 0)
            mother = project / "素材" / "人物" / "jiuweihu.png"
            self.assertTrue(mother.is_file())
            self.assertTrue((project / "素材" / "道具" / "huwei_qingwu.png").is_file())
            mother_calls = [c for c in client.calls if c["out"].endswith("jiuweihu.png")]
            child_calls = [c for c in client.calls if c["out"].endswith("huwei_qingwu.png")]
            # 母素材零参考直出；子素材以先产出的母图为参考
            self.assertEqual(len(mother_calls), 1)
            self.assertEqual(mother_calls[0]["image_refs"], [])
            self.assertEqual(len(child_calls), 1)
            self.assertEqual(child_calls[0]["image_refs"], [str(mother)])

    def test_main_derived_missing_parent_degrades_without_failing(self):
        """端到端：derived_from 父图缺失时降级生成，退出码仍为 0。"""
        gen = _load_gen()

        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            (project / "素材").mkdir(parents=True)
            (project / "素材" / "场景.json").write_text(json.dumps({
                "scenes": [
                    {"id": "yetai_hei", "name": "夜台·黑", "image_prompt": "夜台黑化版",
                     "derived_from": "@scene:yetai"},
                ]
            }, ensure_ascii=False), encoding="utf-8")

            code, client = _run_main(gen, project)

            self.assertEqual(code, 0)
            self.assertTrue((project / "素材" / "场景" / "yetai_hei.png").is_file())
            self.assertEqual(len(client.calls), 1)
            self.assertEqual(client.calls[0]["image_refs"], [])
            self.assertEqual(client.calls[0]["mode"], "generate")

    def test_main_derived_parent_generated_first_supplies_reference(self):
        """端到端：批量时父资产先生成，派生子图拿到父图参考。"""
        gen = _load_gen()

        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            (project / "素材").mkdir(parents=True)
            (project / "素材" / "场景.json").write_text(json.dumps({
                "scenes": [
                    {"id": "yetai_hei", "name": "夜台·黑", "image_prompt": "夜台黑化版",
                     "derived_from": "@scene:yetai"},
                    {"id": "yetai", "name": "夜台", "image_prompt": "夜台全景"},
                ]
            }, ensure_ascii=False), encoding="utf-8")

            code, client = _run_main(gen, project)

            self.assertEqual(code, 0)
            parent_call = next(c for c in client.calls if c["out"].endswith("yetai.png"))
            child_call = next(c for c in client.calls if c["out"].endswith("yetai_hei.png"))
            self.assertEqual(parent_call["image_refs"], [])
            self.assertTrue(child_call["image_refs"])
            self.assertTrue(child_call["image_refs"][0].endswith("yetai.png"))

    def test_main_states_branch_uses_mother_image_without_nameerror(self):
        """states 状态图：以本资产母图为参考，且不再触发 style_txt NameError。"""
        gen = _load_gen()

        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            (project / "素材").mkdir(parents=True)
            (project / "素材" / "人物.json").write_text(json.dumps({
                "characters": [{
                    "id": "heiyuan",
                    "name": "黑渊刹",
                    "sheet_prompt": "黑风衣男子三视图",
                    "states": [
                        {"id": "S1", "label": "反派期", "sheet_prompt": "黑风衣逼婚造型"},
                        {"id": "S2", "label": "盟友期", "sheet_prompt": "断尾盟友造型"},
                    ],
                }]
            }, ensure_ascii=False), encoding="utf-8")

            code, client = _run_main(gen, project)

            self.assertEqual(code, 0)
            mother = project / "素材" / "人物" / "heiyuan.png"
            self.assertTrue(mother.is_file())
            self.assertTrue((project / "素材" / "人物" / "heiyuan__S1.png").is_file())
            self.assertTrue((project / "素材" / "人物" / "heiyuan__S2.png").is_file())
            state_calls = [c for c in client.calls if "__S" in c["out"]]
            self.assertEqual(len(state_calls), 2)
            for call in state_calls:
                self.assertEqual(call["image_refs"], [str(mother)])
            index = json.loads((project / "素材" / "素材图.json").read_text(encoding="utf-8"))
            self.assertIn("states", index["人物"]["heiyuan"])

    def test_main_states_branch_uses_edit_mode_on_comfyui(self):
        """states 带母图参考时 local-comfyui 使用 edit 模式。"""
        gen = _load_gen()

        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            (project / "素材").mkdir(parents=True)
            (project / "素材" / "人物.json").write_text(json.dumps({
                "characters": [{
                    "id": "jiuweihu", "name": "青妩",
                    "sheet_prompt": "九尾狐三视图",
                    "states": [{"id": "S1", "label": "八尾将生", "sheet_prompt": "八尾形态"}],
                }]
            }, ensure_ascii=False), encoding="utf-8")

            client = _FakeClient("local-comfyui")
            argv = ["gen_asset_images.py", str(project)]
            with mock.patch.object(gen, "pick_vendor", lambda v=None: client.id), \
                 mock.patch.object(gen, "VendorClient", lambda vid: client), \
                 mock.patch.object(sys, "argv", argv):
                try:
                    gen.main()
                except SystemExit:
                    pass

            state_calls = [c for c in client.calls if "__S1" in c["out"]]
            self.assertEqual(len(state_calls), 1)
            self.assertEqual(state_calls[0]["mode"], "edit")
            mother = project / "素材" / "人物" / "jiuweihu.png"
            self.assertEqual(state_calls[0]["image_refs"], [str(mother)])

    @staticmethod
    def _states_project(project):
        (project / "素材").mkdir(parents=True)
        (project / "素材" / "人物.json").write_text(json.dumps({
            "characters": [{
                "id": "jiuweihu", "name": "青妩",
                "sheet_prompt": "九尾狐三视图",
                "states": [{"id": "S1", "label": "八尾将生", "sheet_prompt": "八尾形态"}],
            }]
        }, ensure_ascii=False), encoding="utf-8")

    def test_states_skip_generates_only_mother(self):
        """--states skip：重生成母图时不碰状态图（「重生成母图」按钮口径——点母图不附带 S1/S2）。"""
        gen = _load_gen()

        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            self._states_project(project)

            code, client = _run_main(gen, project, "--force", "--states", "skip")

            self.assertEqual(code, 0)
            self.assertTrue((project / "素材" / "人物" / "jiuweihu.png").is_file())
            self.assertFalse((project / "素材" / "人物" / "jiuweihu__S1.png").exists())
            self.assertEqual(len(client.calls), 1)

    def test_states_only_keeps_existing_mother(self):
        """--states only：母图已存在不重生成，只补缺失状态图，且状态图仍以母图为参考。"""
        gen = _load_gen()

        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            self._states_project(project)
            mother = project / "素材" / "人物" / "jiuweihu.png"
            mother.parent.mkdir(parents=True, exist_ok=True)
            mother.write_bytes(b"original-mother")

            code, client = _run_main(gen, project, "--states", "only")

            self.assertEqual(code, 0)
            self.assertEqual(mother.read_bytes(), b"original-mother")
            state_calls = [c for c in client.calls if "__S1" in c["out"]]
            self.assertEqual(len(client.calls), 1)
            self.assertEqual(state_calls[0]["image_refs"], [str(mother)])

    def test_states_only_generates_missing_mother_as_reference(self):
        """--states only 且母图缺失：仍先生成母图（状态图参考需要），再产状态图。"""
        gen = _load_gen()

        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            self._states_project(project)

            code, client = _run_main(gen, project, "--states", "only")

            self.assertEqual(code, 0)
            mother = project / "素材" / "人物" / "jiuweihu.png"
            self.assertEqual(len(client.calls), 2)
            state_calls = [c for c in client.calls if "__S1" in c["out"]]
            self.assertEqual(state_calls[0]["image_refs"], [str(mother)])

    def test_state_id_generates_single_state_and_protects_mother(self):
        """--state-id：逐张生成派生图——即使带 --force 也只重生成本状态，母图与其它状态不动。"""
        gen = _load_gen()

        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            (project / "素材").mkdir(parents=True)
            (project / "素材" / "人物.json").write_text(json.dumps({
                "characters": [{
                    "id": "jiuweihu", "name": "青妩",
                    "sheet_prompt": "九尾狐三视图",
                    "states": [
                        {"id": "S1", "label": "八尾将生", "sheet_prompt": "八尾形态"},
                        {"id": "S2", "label": "乱世噬凶态", "sheet_prompt": "噬凶形态"},
                    ],
                }]
            }, ensure_ascii=False), encoding="utf-8")
            mother = project / "素材" / "人物" / "jiuweihu.png"
            mother.parent.mkdir(parents=True, exist_ok=True)
            mother.write_bytes(b"original-mother")
            s1 = project / "素材" / "人物" / "jiuweihu__S1.png"
            s1.write_bytes(b"original-s1")

            code, client = _run_main(gen, project, "--force", "--states", "only", "--state-id", "S2")

            self.assertEqual(code, 0)
            self.assertEqual(mother.read_bytes(), b"original-mother")
            self.assertEqual(s1.read_bytes(), b"original-s1")
            self.assertEqual(len(client.calls), 1)
            self.assertTrue(client.calls[0]["out"].endswith("jiuweihu__S2.png"))
            self.assertEqual(client.calls[0]["image_refs"], [str(mother)])


class _GateClient(_FakeClient):
    """带闸门的生图桩：记录并发峰值，用来区分「真并发」和「逐张排队」。"""

    def __init__(self, vendor_id="fake-vendor", hold=0.08):
        super().__init__(vendor_id)
        self._lock = threading.Lock()
        self._live = 0
        self.peak = 0
        self.hold = hold

    def generate_image(self, prompt, out, **kw):
        with self._lock:
            self._live += 1
            self.peak = max(self.peak, self._live)
        time.sleep(self.hold)
        with self._lock:
            self._live -= 1
        return super().generate_image(prompt, out, **kw)


def _wave_project(tmp, chars=4, derived=True):
    """造一个 ② 素材项目：N 个人物母图（+可选 1 个挂父图的角色项圈）。"""
    project = Path(tmp)
    (project / "素材" / "人物").mkdir(parents=True)
    rows = [{"id": f"hero{i}", "name": f"英雄{i}", "gender": "男", "sheet_prompt": f"第 {i} 号青年男子立绘"}
            for i in range(1, chars + 1)]
    rows[0]["states"] = [{"id": "S1", "label": "受伤期", "episodes": ["E3"], "sheet_prompt": "左臂缠绷带"}]
    (project / "素材" / "人物.json").write_text(json.dumps({"characters": rows}, ensure_ascii=False), encoding="utf-8")
    if derived:
        (project / "素材" / "道具").mkdir(parents=True)
        (project / "素材" / "道具.json").write_text(json.dumps({"props": [
            {"id": "collar", "name": "项圈", "image_prompt": "黑色项圈", "parent_ref": "@character:hero1"}]},
            ensure_ascii=False), encoding="utf-8")
    return project


class AssetImageWaveTests(unittest.TestCase):
    def test_plan_waves_groups_independent_assets_and_defers_children(self):
        from gen_asset_images import plan_waves

        plans = [
            {"kind": "character", "id": "a", "reference_tokens": []},
            {"kind": "character", "id": "b", "reference_tokens": []},
            {"kind": "prop", "id": "collar", "reference_tokens": ["@character:a"]},
            {"kind": "prop", "id": "bell", "reference_tokens": ["@prop:collar"]},
        ]
        waves = plan_waves(plans)
        self.assertEqual([[p["id"] for p in w] for w in waves], [["a", "b"], ["collar"], ["bell"]],
                         "无依赖的同一波（可并发），子图往后推一波")

    def test_plan_waves_keeps_parent_outside_batch_at_first_wave(self):
        """父图不在本批次（已生成或被 --id 过滤）＝沿用旧的「缺父图降级无参考」，不额外排波。"""
        from gen_asset_images import plan_waves

        waves = plan_waves([{"kind": "prop", "id": "collar", "reference_tokens": ["@character:gone"]}])
        self.assertEqual(len(waves), 1)

    def test_plan_waves_survives_dependency_cycle(self):
        from gen_asset_images import plan_waves

        waves = plan_waves([
            {"kind": "prop", "id": "x", "reference_tokens": ["@prop:y"]},
            {"kind": "prop", "id": "y", "reference_tokens": ["@prop:x"]},
        ])
        self.assertEqual(sorted(p["id"] for w in waves for p in w), ["x", "y"], "有环也不能丢项或死递归")

    def test_cloud_vendor_generates_independent_assets_in_parallel(self):
        """云端厂商：同波内互不依赖的资产必须并发出图（原来几十张图逐张排队）。"""
        gen = _load_gen()
        with tempfile.TemporaryDirectory() as td:
            project = _wave_project(td, chars=4, derived=False)
            client = _GateClient()
            code, _ = _run_main(gen, project, "--workers", "4", client=client)
            self.assertEqual(code, 0)
            self.assertGreaterEqual(client.peak, 2, "无依赖的人物母图应并发出图，不再逐张排队")
            self.assertLessEqual(client.peak, 4, "并发数不得超过 --workers")
            saved = json.loads((project / "素材" / "素材图.json").read_text(encoding="utf-8"))
            self.assertEqual(sorted(saved["人物"]), ["hero1", "hero2", "hero3", "hero4"],
                             "并发写索引不得互相覆盖")
            self.assertIn("S1", saved["人物"]["hero1"].get("states") or {}, "状态图索引在并发下也要保住")
            saved = json.loads((project / "素材" / "素材图.json").read_text(encoding="utf-8"))
            self.assertEqual(sorted(saved["人物"]), ["hero1", "hero2", "hero3", "hero4"],
                             "并发下索引条目不得互相覆盖")

    def test_parallel_run_still_uses_parent_image_for_derived_child(self):
        """并发不能把子图抢到父图前面——那样项圈拿不到参考图，身份就散了。"""
        gen = _load_gen()
        with tempfile.TemporaryDirectory() as td:
            project = _wave_project(td, chars=3, derived=True)
            client = _GateClient()
            _run_main(gen, project, "--workers", "4", client=client)
            collar = next(c for c in client.calls
                          if c["out"].replace("\\", "/").endswith("素材/道具/collar.png"))
            self.assertEqual([Path(r).name for r in collar["image_refs"]], ["hero1.png"],
                             "项圈必须以 hero1 母图为参考")
            self.assertTrue(collar["out"].replace("\\", "/").endswith("素材/道具/collar.png"))

    def test_local_comfyui_batch_stays_serial(self):
        gen = _load_gen()
        with tempfile.TemporaryDirectory() as td:
            project = _wave_project(td, chars=4, derived=False)
            client = _GateClient(vendor_id="local-comfyui", hold=0.01)
            _run_main(gen, project, "--workers", "4", client=client)
            self.assertEqual(client.peak, 1, "本机 ComfyUI 只有一个队列，并发数必须压回 1")


if __name__ == "__main__":
    unittest.main()
