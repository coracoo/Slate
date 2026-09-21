# -*- coding: utf-8 -*-
"""演员 A/B/C 评估登记器离线测试。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT / "workbench" / "tools", ROOT / "previs_system" / "tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


class ActorEvalTests(unittest.TestCase):
    def test_manifest_contains_shared_inputs_and_is_idempotent(self):
        import evaluate_actor_ab
        with tempfile.TemporaryDirectory() as td:
            project = Path(td) / "项目"
            (project / "分镜").mkdir(parents=True)
            (project / "剧本").mkdir()
            (project / "创作").mkdir()
            board = project / "分镜" / "剧本_E1.json"
            board.write_text(json.dumps({
                "actors": {"a": {"name": "甲"}},
                "shots": [{"id": "S1", "dur": 2, "prompt": "甲抬眼"}]
            }, ensure_ascii=False), encoding="utf-8")
            (project / "剧本" / "剧本.txt").write_text("甲抬眼。", encoding="utf-8")
            result = evaluate_actor_ab.build_manifest(str(board), ["S1"], ["baseline", "style", "stateful"], 2, "exp1")
            self.assertEqual(len(result["rows"]), 6)
            self.assertEqual({row["mode"] for row in result["rows"]}, {"baseline", "style", "stateful"})
            self.assertEqual(len({row["script_hash"] for row in result["rows"]}), 1)
            self.assertEqual(len({row["board_revision"] for row in result["rows"]}), 1)
            self.assertTrue(all(row["video"]["comparable"] is False for row in result["rows"]))
            again = evaluate_actor_ab.build_manifest(str(board), ["S1"], ["baseline", "style", "stateful"], 2, "exp1")
            self.assertEqual(again["input_hash"], result["input_hash"])


if __name__ == "__main__":
    unittest.main()
