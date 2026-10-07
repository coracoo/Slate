# -*- coding: utf-8 -*-
"""冻结的输入目录读取风格时不得补写选择或版本快照。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import skill_lib


class ReadonlySkillTests(unittest.TestCase):
    def setUp(self):
        rows = [{"id": "structure", "target": "script", "dimension": "script_structure",
                 "enabled": True, "name": "结构"}]
        for name, kwargs in (("list_skills", {"return_value": rows}),
                             ("load_skill_text", {"return_value": "编剧结构方法"})):
            patcher = mock.patch.object(skill_lib, name, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_missing_style_resolves_default_without_creating_any_file(self):
        with tempfile.TemporaryDirectory() as td, mock.patch.object(skill_lib, "set_project_style") as write:
            result = skill_lib.style_for(td, "script", persist_defaults=False)
            self.assertEqual(result, "编剧结构方法\n")
            write.assert_not_called()
            self.assertEqual(list(Path(td).iterdir()), [])

    def test_existing_partial_style_and_its_revision_remain_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "剧本" / "style.json"
            path.parent.mkdir()
            path.write_text(json.dumps({"image": "auto"}), encoding="utf-8")
            before = (path.read_bytes(), path.stat().st_mtime_ns)
            self.assertEqual(skill_lib.style_for(td, "script", persist_defaults=False), "编剧结构方法\n")
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)
            self.assertEqual(sorted(str(p.relative_to(td)) for p in Path(td).rglob("*")),
                             ["剧本", str(Path("剧本") / "style.json")])

    def test_readonly_selection_preserves_explicit_dimension_auto(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "剧本" / "style.json"
            path.parent.mkdir()
            path.write_text(json.dumps({"script_structure": "auto"}), encoding="utf-8")
            self.assertEqual(skill_lib.selected_skills_for(td, "script", persist_defaults=False), [])
            self.assertEqual(skill_lib.style_for(td, "script", persist_defaults=False), "")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"script_structure": "auto"})

    def test_default_call_still_freezes_missing_choice(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(skill_lib.style_for(td, "script"), "编剧结构方法\n")
            style = json.loads((Path(td) / "剧本" / "style.json").read_text(encoding="utf-8"))
            self.assertEqual(style["script"], "structure")

    def test_episode_generation_prompt_chain_preserves_frozen_input(self):
        import creation_pipeline as cp
        with tempfile.TemporaryDirectory() as td, \
                mock.patch.object(cp, "pick_vendor", return_value="fake"), \
                mock.patch.object(cp, "VendorClient", return_value=object()), \
                mock.patch.object(cp, "chat_retry", return_value="生成正文") as chat, \
                mock.patch("socket.socket.connect", side_effect=AssertionError("离线测试")) as network:
            script = Path(td) / "剧本"
            script.mkdir()
            (script / "brief.json").write_text(json.dumps({"dialogue_density": "低"}), encoding="utf-8")
            before = {str(path): (path.read_bytes(), path.stat().st_mtime_ns)
                      for path in Path(td).rglob("*") if path.is_file()}
            text = cp.generate_episode_text(td, "fake", "测试构想", {"id": "E1", "summary": "本集剧情"})
            self.assertEqual(text, "生成正文")
            self.assertIn("编剧结构方法", chat.call_args.args[1][0]["content"])
            self.assertIn("对白密度「低」", chat.call_args.args[1][0]["content"])
            self.assertEqual(before, {str(path): (path.read_bytes(), path.stat().st_mtime_ns)
                                     for path in Path(td).rglob("*") if path.is_file()})
            network.assert_not_called()


if __name__ == "__main__":
    unittest.main()
