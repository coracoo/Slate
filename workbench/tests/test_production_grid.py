# -*- coding: utf-8 -*-
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workbench" / "tools"))


class GridRequestContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        for name in ("分镜", "剧本", "素材"):
            (self.project / name).mkdir()
        self.board = {"shots": [{"id": "S1", "dur": 4, "content": "角色穿过长廊",
                                  "prompt_video": "角色从门口走到窗边",
                                  "prompt_grid": "5×5 二十五宫格，按时间顺序展示动作"}]}
        self.path = self.project / "分镜/剧本_E1.json"
        self.path.write_text(json.dumps(self.board, ensure_ascii=False), encoding="utf-8")
        (self.project / "剧本/brief.json").write_text(
            json.dumps({"aspect_ratio": "9:16"}), encoding="utf-8")
        self.body = {"board": self.path.name, "scope": "S", "target": "S1"}
        self.cfg = {"id": "unknown", "models": {"image": "plain", "image_edit": "edit"},
                    "reference_limit": {"image": 3}}

    def tearDown(self):
        self.temp.cleanup()

    def test_custom_layout_and_project_aspect_are_the_only_layout_contract(self):
        from production_jobs import compile_grid_request
        packet = compile_grid_request(self.project, self.body, self.cfg)
        self.assertIn("5×5 二十五宫格", packet["prompt"])
        self.assertNotIn("正好 9 格", packet["prompt"])
        self.assertNotIn("3 列 × 3 行", packet["prompt"])
        self.assertIn("9:16", packet["prompt"])
        self.assertEqual(packet["image_options"]["ratio"], "9:16")

    def test_required_references_over_model_limit_fail_instead_of_truncating(self):
        from production_jobs import compile_grid_request
        refs = []
        for index in range(4):
            path = self.project / "素材" / f"R{index}.png"
            path.write_bytes(b"image")
            refs.append({"path": path.relative_to(self.project).as_posix(), "purpose": f"R{index}"})
        with mock.patch("prompt_assembler.resolve_shot_refs", return_value=refs):
            with self.assertRaisesRegex(ValueError, "参考图上限"):
                compile_grid_request(self.project, self.body, self.cfg)


if __name__ == "__main__":
    unittest.main()
