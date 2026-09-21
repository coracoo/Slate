# -*- coding: utf-8 -*-
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workbench" / "tools"))
sys.path.insert(0, str(ROOT / "previs_system" / "tools"))


class ReferenceContractP0Tests(unittest.TestCase):
    def test_default_resolution_excludes_previs_and_flat_diagram(self):
        from prompt_assembler import resolve_shot_refs

        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            (project / "白模" / "预演包_剧本_E1").mkdir(parents=True)
            (project / "推演" / "平面图_剧本_E1").mkdir(parents=True)
            (project / "素材" / "场景").mkdir(parents=True)
            (project / "素材" / "人物").mkdir(parents=True)
            (project / "白模" / "预演包_剧本_E1" / "S01.png").write_bytes(b"x")
            (project / "推演" / "平面图_剧本_E1" / "S01.png").write_bytes(b"x")
            (project / "素材" / "场景" / "军帐.png").write_bytes(b"x")
            (project / "素材" / "人物" / "hero.png").write_bytes(b"x")
            refs = resolve_shot_refs(
                {"id": "S01", "scene": "军帐", "speaker": "hero"},
                str(project), actors={"hero": {"name": "甲"}},
                board_name="剧本_E1.json",
            )
            paths = {row["path"] for row in refs}
            self.assertNotIn("白模/预演包_剧本_E1/S01.png", paths)
            self.assertNotIn("推演/平面图_剧本_E1/S01.png", paths)
            self.assertIn("素材/场景/军帐.png", paths)
            self.assertIn("素材/人物/hero.png", paths)

    def test_explicit_frame_keeps_role_and_time(self):
        from prompt_assembler import resolve_shot_refs

        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            (project / "创作" / "参考帧" / "E1S01").mkdir(parents=True)
            (project / "素材" / "场景").mkdir(parents=True)
            (project / "创作" / "参考帧" / "E1S01" / "process.png").write_bytes(b"x")
            (project / "素材" / "场景" / "军帐.png").write_bytes(b"x")
            refs = resolve_shot_refs(
                {
                    "id": "S01", "scene": "军帐",
                    "reference_frames": [{
                        "path": "创作/参考帧/E1S01/process.png",
                        "role": "process", "time_seconds": 1.4,
                    }],
                },
                str(project), board_name="剧本_E1.json",
            )
            frame = next(row for row in refs if row["path"].endswith("process.png"))
            self.assertEqual(frame["purpose"], "剧情参考帧")
            self.assertEqual(frame["reference_role"], "process")
            self.assertEqual(frame["target_time_seconds"], 1.4)

    def test_static_prompt_does_not_append_video_only_sound(self):
        from prompt_assembler import assemble_shot_prompt

        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            (project / "剧本").mkdir()
            (project / "剧本" / "style.json").write_text(
                json.dumps({"image": "cinematic-real"}, ensure_ascii=False),
                encoding="utf-8",
            )
            out = assemble_shot_prompt(
                {
                    "id": "S01", "scene": "room", "action": "人物抬头看向窗外",
                    "lighting": "侧向窗光", "sound": "课桌摩擦声",
                    "lines": [{"speaker": "hero", "line": "嗯？"}],
                },
                {"dir": str(project), "board": {"actors": {"hero": {"name": "甲"}}}, "media_type": "image"},
            )
            self.assertNotIn("课桌摩擦声", out["prompt_assembled"])
            self.assertNotIn("台词：", out["prompt_assembled"])
            self.assertIn("输出单张关键帧", out["prompt_assembled"])


if __name__ == "__main__":
    unittest.main()
