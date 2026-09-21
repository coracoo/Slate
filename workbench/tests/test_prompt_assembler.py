# -*- coding: utf-8 -*-
import json
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workbench" / "tools"))
sys.path.insert(0, str(ROOT / "previs_system" / "tools"))


class PromptAssemblerTests(unittest.TestCase):
    def test_asset_extraction_prompt_is_evidence_first_and_catalog_bound(self):
        import prompt_modules

        system, user = prompt_modules.props_prompt(
            "白咲蛛绪用蛛丝包裹遥控炸弹，镜头给缓冲茧特写。",
            catalog={
                "characters": [{"id": "baixiaozhuxu", "name": "白咲蛛绪"}],
                "scenes": [],
                "props": [{"id": "yaokong_zhadan", "name": "遥控炸弹"}],
            },
        )
        self.assertIn("evidence_ids", system)
        self.assertIn("EV001", user)
        self.assertIn("@prop:yaokong_zhadan", system)
        self.assertIn("owner 是", system)
        self.assertIn("parent_ref 是", system)

    def test_assemble_includes_shot_contract_style_lines_and_knowledge(self):
        from prompt_assembler import assemble_shot_prompt

        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            (project / "剧本").mkdir()
            (project / "剧本" / "style.json").write_text(
                json.dumps({"image": "ink-wash"}, ensure_ascii=False),
                encoding="utf-8",
            )
            board = {"actors": {"a": {"name": "甲"}, "b": {"name": "乙"}}, "shots": []}
            shot = {
                "id": "S01", "dur": 3.2, "shot_size": "近景", "move": "推",
                "rig": "轨道", "lens": "50mm", "action": "甲抬眼逼近乙",
                "lighting": "侧逆光", "sound": "远处风声", "scene": "军帐",
                "pos": [1, 2, 0], "look": [0, 0, 1],
                "lines": [{"speaker": "a", "line": "你终于来了。"}],
            }
            out = assemble_shot_prompt(shot, {"dir": str(project), "board": board})
            self.assertIn("宣纸质感", out["prompt_assembled"])  # 画风来自 image skill
            self.assertIn("甲抬眼逼近乙", out["prompt_assembled"])
            self.assertIn("近景", out["prompt_assembled"])
            self.assertIn("轨道", out["prompt_assembled"])
            self.assertIn("50mm", out["prompt_assembled"])
            self.assertIn("你终于来了", out["prompt_assembled"])
            self.assertIn("文字", out["negative"])
            self.assertIn("照片写实", out["negative"])
            self.assertIn("额外角色", out["negative"])
            self.assertNotIn("多人", out["negative"])
            self.assertNotIn("角色三视图保持", out["prompt_assembled"])
            self.assertNotIn("场景图可大写意", out["prompt_assembled"])
            self.assertIsInstance(out["knowledge_hits"], list)

    def test_resolve_refs_uses_scene_and_characters_without_implicit_previs(self):
        from prompt_assembler import resolve_shot_refs

        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            (project / "白模" / "预演包_剧本_E1").mkdir(parents=True)
            (project / "推演" / "平面图_剧本_E1").mkdir(parents=True)
            (project / "素材" / "人物").mkdir(parents=True)
            (project / "素材" / "场景").mkdir(parents=True)
            for p in [
                project / "白模" / "预演包_剧本_E1" / "S01.png",
                project / "推演" / "平面图_剧本_E1" / "S01.png",
                project / "素材" / "人物" / "a.png",
                project / "素材" / "人物" / "b.png",
                project / "素材" / "场景" / "军帐.png",
            ]:
                p.write_bytes(b"x")
            shot = {"id": "S01", "scene": "军帐", "speaker": "a", "target": "b"}
            refs = resolve_shot_refs(
                shot, str(project), actors={"a": {"name": "甲"}, "b": {"name": "乙"}},
                board_name="剧本_E1.json",
            )
            self.assertLessEqual(len(refs), 10)
            self.assertIn("地理锚点", {r["purpose"] for r in refs})
            self.assertIn("身份锚点", {r["purpose"] for r in refs})
            self.assertTrue(all("path" in r for r in refs))
            self.assertTrue(all("预演包_" not in r["path"] and "平面图_" not in r["path"] for r in refs))

    def test_assemble_uses_extracted_character_scene_and_prop_assets(self):
        from prompt_assembler import assemble_shot_prompt, resolve_shot_refs

        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            (project / "剧本").mkdir()
            (project / "素材" / "人物").mkdir(parents=True)
            (project / "素材" / "场景").mkdir(parents=True)
            (project / "素材" / "道具").mkdir(parents=True)
            (project / "素材" / "人物.json").write_text(json.dumps({
                "characters": [{"id": "hero_one", "name": "甲", "role": "主角",
                                 "appearance": {"look": "短黑发", "outfit": "深色风衣"},
                                 "lens": "用近景捕捉迟疑"}]}, ensure_ascii=False), encoding="utf-8")
            (project / "素材" / "场景.json").write_text(json.dumps({
                "scenes": [{"id": "loc_old_street", "name": "旧街", "time": "黄昏",
                             "light": "橙色侧光", "interior": False,
                             "geometry": ["石板路", "拱门"]}]}, ensure_ascii=False), encoding="utf-8")
            (project / "素材" / "道具.json").write_text(json.dumps({
                "props": [{"id": "red_umbrella", "name": "红伞", "kind": "叙事",
                            "actions": ["甲撑开红伞"], "image_prompt": "旧红伞，伞面有雨珠"}]}, ensure_ascii=False), encoding="utf-8")
            for path in [project / "素材" / "人物" / "hero_one.png",
                         project / "素材" / "场景" / "loc_old_street.png",
                         project / "素材" / "道具" / "red_umbrella.png"]:
                path.write_bytes(b"x")
            shot = {"id": "S01", "scene": "field", "content": "甲走进旧街，撑开红伞",
                    "action": "甲撑开红伞", "lines": []}
            board = {"actors": {}, "shots": [shot]}
            out = assemble_shot_prompt(shot, {"dir": str(project), "board": board})
            prompt = out["prompt_assembled"]
            self.assertIn("@character:hero_one", prompt)
            self.assertIn("@scene:loc_old_street", prompt)
            self.assertIn("@prop:red_umbrella", prompt)
            self.assertNotIn("短黑发", prompt)
            self.assertNotIn("石板路", prompt)
            self.assertNotIn("旧红伞", prompt)
            self.assertEqual(out["prompt_json"]["schema"], "shot-prompt-v1")
            self.assertIn("@character:hero_one", out["asset_refs"])
            self.assertIn("@scene:loc_old_street", out["asset_refs"])
            self.assertIn("@prop:red_umbrella", out["asset_refs"])
            refs = resolve_shot_refs(shot, str(project), board=board)
            self.assertEqual({r["path"] for r in refs}, {
                "素材/人物/hero_one.png", "素材/场景/loc_old_street.png", "素材/道具/red_umbrella.png"
            })

    def test_assemble_keeps_child_prop_in_asset_context_and_prompt(self):
        """镜头引用项圈/状态变体时，子道具不能被装配器过滤掉。"""
        from prompt_assembler import assemble_shot_prompt

        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            (project / "剧本").mkdir()
            (project / "素材" / "人物").mkdir(parents=True)
            (project / "素材" / "道具").mkdir(parents=True)
            (project / "素材" / "人物.json").write_text(json.dumps({
                "characters": [{"id": "hero", "name": "女主", "role": "主角"}]
            }, ensure_ascii=False), encoding="utf-8")
            (project / "素材" / "道具.json").write_text(json.dumps({
                "props": [{"id": "collar", "name": "拟态项圈", "kind": "组件",
                            "parent_ref": "@character:hero", "image_prompt": "黑色项圈",
                            "actions": ["按住项圈"], "asset_required": True}]
            }, ensure_ascii=False), encoding="utf-8")
            (project / "素材" / "人物" / "hero.png").write_bytes(b"x")
            (project / "素材" / "道具" / "collar.png").write_bytes(b"x")
            shot = {"id": "S01", "actor_refs": ["@character:hero"],
                    "prop_refs": ["@prop:collar"], "action": "女主按住项圈"}
            out = assemble_shot_prompt(shot, {"dir": str(project), "board": {"shots": []}})
            self.assertIn("@prop:collar", out["prompt_assembled"])
            self.assertIn("@prop:collar", out["asset_refs"])
            self.assertEqual(out["asset_context"]["props"][0]["id"], "collar")

    def test_coverage_matrix_reports_latest_item_per_shot(self):
        from creation_store import coverage_matrix

        out = coverage_matrix(
            [{"id": "i1", "shot_id": "S01", "status": "done"},
             {"id": "i2", "shot_id": "S02", "status": "error"}],
            ["S01", "S02", "S03"],
        )
        self.assertEqual(out["counts"], {"total": 3, "done": 1, "running": 0, "error": 1, "pending": 1})
        self.assertEqual(out["shots"][0]["status"], "done")
        self.assertEqual(out["shots"][2]["status"], "pending")

    def test_create_media_forwards_negative_prompt(self):
        import create_media
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            manifest = root / "creation.json"
            manifest.write_text(json.dumps({"items": [{"id": "i1", "status": "running"}]}), encoding="utf-8")
            ref = root / "ref.png"
            ref.write_bytes(b"x")
            seen = {}

            class FakeClient:
                def __init__(self, *args, **kwargs):
                    pass
                def model(self, kind):
                    return "fake"
                def generate_image(self, prompt, out, **kwargs):
                    seen["prompt"] = prompt
                    seen.update(kwargs)
                    Path(out).write_bytes(b"png")

            old = sys.argv[:]
            try:
                sys.argv = ["create_media.py", "--type", "image", "--prompt", "p", "--negative", "no text",
                            "--vendor", "fake", "--refs", str(ref), "--ref-purpose", "身份锚点",
                            "--outdir", str(root / "out"), "--manifest", str(manifest), "--item-id", "i1"]
                with mock.patch.object(create_media.llm_openai, "VendorClient", FakeClient):
                    create_media.main()
            finally:
                sys.argv = old
            self.assertEqual(seen.get("negative_prompt"), "no text")
            self.assertIn("身份锚点", seen.get("prompt", ""))

    def test_output_ref_is_media_service_relative(self):
        from create_media import output_ref
        path = str(ROOT / "projects" / "demo" / "创作" / "item-1" / "img_1.png")
        self.assertEqual(output_ref(path), "projects/demo/创作/item-1/img_1.png")


if __name__ == "__main__":
    unittest.main()
