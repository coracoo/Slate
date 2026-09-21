# -*- coding: utf-8 -*-
"""演员候选文件、revision 应用和锁定保护的离线回归测试。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "workbench" / "tools"
CORE = ROOT / "previs_system" / "tools"
for p in (TOOLS, CORE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def packet(shot_id="S1"):
    return {"status": "ready", "packet": {"shot_id": shot_id, "actors": [{
        "actor_id": "a", "beats": [{"at": 0, "duration": 1, "visible_action": "直视前方"}]
    }]}}


class ActorCandidateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name) / "p"
        board_dir = self.project / "分镜"
        board_dir.mkdir(parents=True)
        self.board_path = board_dir / "剧本_E1.json"
        self.board = {"actors": {"a": {"name": "甲"}}, "shots": [
            {"id": "S1", "dur": 4, "pos": [0, 1, 1], "look": [0, 0, 1]},
            {"id": "S2", "dur": 3, "pos": [0, 1, 1], "look": [0, 0, 1]},
        ]}
        self.board_path.write_text(json.dumps(self.board, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_prepare_and_apply_candidate_are_revision_checked_and_idempotent(self):
        import actor_pipeline as ap
        prepared = ap.prepare_context(str(self.board_path), ["S1"])
        self.assertTrue(Path(prepared["path"]).is_file())
        saved = ap.save_performance_candidate(str(self.board_path), "S1", packet(), context=prepared["context"])
        result = ap.apply_candidate(str(self.board_path), saved["run_id"])
        self.assertEqual(result["changed_shots"], ["S1"])
        board, revision = ap._read_board(str(self.board_path))
        self.assertEqual(board["shots"][0]["performance"]["status"], "ready")
        again = ap.apply_candidate(str(self.board_path), saved["run_id"])
        self.assertTrue(again["idempotent"])
        self.assertEqual(again["revision"], revision)

    def test_context_candidate_can_be_applied_and_is_idempotent(self):
        import actor_pipeline as ap
        context = ap.default_context(self.board)
        context["notes"] = "甲保持克制"
        candidate = ap.prepare_context(str(self.board_path), ["S1"], context=context)
        result = ap.apply_candidate(str(self.board_path), candidate["run_id"])
        self.assertEqual(result["changed_shots"], [])
        board, revision = ap._read_board(str(self.board_path))
        self.assertEqual(board["acting_context"]["notes"], "甲保持克制")
        self.assertEqual(board["acting_status"]["S1"], "context_ready")
        again = ap.apply_candidate(str(self.board_path), candidate["run_id"])
        self.assertTrue(again["idempotent"])
        self.assertEqual(again["revision"], revision)

    def test_run_performance_saves_multi_shot_candidate_without_writing_board(self):
        import actor_pipeline as ap
        responses = iter([
            {"content": '{"shot_id":"S1","actors":[{"actor_id":"a","beats":[{"at":0,"duration":1,"visible_action":"抬眼"}]}]}'},
            {"content": '{"shot_id":"S2","actors":[{"actor_id":"a","beats":[{"at":0,"duration":1,"visible_action":"转身"}]}]}'}
        ])
        def call(messages):
            return next(responses)
        before, revision = ap._read_board(str(self.board_path))
        candidate = ap.run_performance(str(self.board_path), ["S1", "S2"], call)
        self.assertEqual(candidate["status"], "ready")
        self.assertEqual(candidate["shot_ids"], ["S1", "S2"])
        after, after_revision = ap._read_board(str(self.board_path))
        self.assertEqual(before, after)
        self.assertEqual(revision, after_revision)
    def test_prepare_can_register_injected_llm_result_without_writing_board(self):
        import actor_pipeline as ap
        before, revision = ap._read_board(str(self.board_path))
        candidate = ap.prepare_context(str(self.board_path), ["S1"], call_llm=lambda messages: {"content": "{\"continuity_id\":\"main\",\"actor_context\":{}}"})
        self.assertEqual(candidate["status"], "ready")
        self.assertEqual(len(candidate["preparation"]), 1)
        after, after_revision = ap._read_board(str(self.board_path))
        self.assertEqual(before, after)
        self.assertEqual(revision, after_revision)
    def test_lock_round_trip_updates_status_with_revision(self):
        import actor_pipeline as ap
        result = ap.set_shot_lock(str(self.board_path), ["S1"], True)
        self.assertTrue(result["locked"])
        board, rev = ap._read_board(str(self.board_path))
        self.assertTrue(board["shots"][0]["performance_locked"])
        self.assertEqual(board["acting_status"]["S1"], "locked")
        unlocked = ap.set_shot_lock(str(self.board_path), ["S1"], False, expected_revision=rev)
        self.assertFalse(unlocked["locked"])
        board, _ = ap._read_board(str(self.board_path))
        self.assertFalse(board["shots"][0]["performance_locked"])
    def test_apply_rejects_stale_revision_and_keeps_locked_shot(self):
        import actor_pipeline as ap
        saved = ap.save_performance_candidate(str(self.board_path), "S1", packet())
        import project_store
        project_store.update_json(str(self.board_path), lambda b: b.update({"title": "changed"}))
        with self.assertRaises(project_store.RevisionConflict):
            ap.apply_candidate(str(self.board_path), saved["run_id"])
        # 重新生成后，锁定镜头不会被候选覆盖
        project_store.update_json(str(self.board_path), lambda b: b["shots"][0].update({"performance_locked": True}))
        saved2 = ap.save_performance_candidate(str(self.board_path), "S1", packet())
        out = ap.apply_candidate(str(self.board_path), saved2["run_id"])
        self.assertEqual(out["locked_shots"], ["S1"])
        board, _ = ap._read_board(str(self.board_path))
        self.assertNotIn("performance", board["shots"][0])

    def test_build_request_keeps_main_actor_and_excludes_supporting_actor(self):
        import actor_pipeline as ap
        board = {
            "actors": {
                "hero": {"name": "主角", "role": "主角"},
                "support": {"name": "配角", "role": "配角"},
            },
            "shots": [{
                "id": "S1", "dur": 4, "speaker": "hero", "target": "support",
                "pos": [0, 1, 1], "look": [0, 0, 1],
            }],
        }
        request = ap.build_request(board, "S1")
        self.assertEqual(request["actor_ids"], ["hero"])
        self.assertIn("@character:hero", request["asset_refs"])
        self.assertNotIn("@character:support", request["asset_refs"])

    def test_actor_ids_for_shot_reads_asset_character_refs(self):
        import actor_pipeline as ap
        board = {
            "actors": {
                "hero": {"name": "主角", "role": "主角"},
                "support": {"name": "配角", "role": "配角"},
            },
            "shots": [{
                "id": "S1", "dur": 4,
                "actor_refs": ["@character:hero", "@character:support"],
                "prompt": "@character:hero 在画面左侧抬头",
            }],
        }
        self.assertEqual(ap.actor_ids_for_shot(board, "S1"), ["hero"])

    def test_classified_shot_without_main_reference_is_not_assigned_all_main_actors(self):
        import actor_pipeline as ap
        board = {
            "actors": {
                "hero": {"name": "主角", "role": "主角"},
                "support": {"name": "配角", "role": "配角"},
            },
            "shots": [{"id": "S1", "dur": 4, "prompt": "空教室定场"}],
        }
        self.assertEqual(ap.actor_ids_for_shot(board, "S1"), [])

    def test_main_actor_ids_reads_character_roles_for_legacy_storyboard(self):
        import actor_pipeline as ap
        asset_dir = self.project / "素材"
        asset_dir.mkdir()
        (asset_dir / "人物.json").write_text(json.dumps({"characters": [
            {"id": "hero", "name": "主角", "role": "主角"},
            {"id": "support", "name": "配角", "role": "配角"},
        ]}, ensure_ascii=False), encoding="utf-8")
        board = {
            "actors": {"hero": {"name": "主角"}, "support": {"name": "配角"}},
            "shots": [],
        }
        self.assertEqual(ap.main_actor_ids(board, str(self.project)), ["hero"])

    def test_hydrate_actor_cards_removes_supporting_actor_state(self):
        import actor_pipeline as ap
        board = {
            "actors": {
                "hero": {"name": "主角", "role": "主角"},
                "support": {"name": "配角", "role": "配角"},
            },
            "shots": [],
        }
        context = {
            "continuities": [{"id": "main", "initial_state": {
                "hero": {}, "support": {},
            }}],
            "actor_cards": {"hero": {"personality": "勇敢"}, "support": {"personality": "配角"}},
        }
        out = ap.hydrate_actor_cards(context, self.project, board)
        self.assertIn("hero", out["actor_cards"])
        self.assertNotIn("support", out["actor_cards"])
        self.assertIn("hero", out["continuities"][0]["initial_state"])
        self.assertNotIn("support", out["continuities"][0]["initial_state"])

    def test_save_performance_candidate_rejects_supporting_actor_request(self):
        import actor_pipeline as ap
        board = {
            "actors": {
                "hero": {"name": "主角", "role": "主角"},
                "support": {"name": "配角", "role": "配角"},
            },
            "shots": [{"id": "S1", "dur": 4, "speaker": "hero", "target": "support"}],
        }
        self.board_path.write_text(json.dumps(board, ensure_ascii=False), encoding="utf-8")
        request = {"shot_id": "S1", "dur": 4, "actor_ids": ["hero", "support"]}
        with self.assertRaisesRegex(ValueError, "只允许主角"):
            ap.save_performance_candidate(self.board_path, "S1", packet(), request=request)

    def test_prepare_prompt_declares_only_main_actor_ids(self):
        import actor_pipeline as ap
        board = {
            "actors": {
                "hero": {"name": "主角", "role": "主角"},
                "support": {"name": "配角", "role": "配角"},
            },
            "shots": [{"id": "S1", "dur": 4, "speaker": "hero", "target": "support"}],
        }
        self.board_path.write_text(json.dumps(board, ensure_ascii=False), encoding="utf-8")
        messages = []
        ap.prepare_context(self.board_path, ["S1"], call_llm=lambda value: messages.append(value) or {"content": "{}"})
        self.assertEqual(len(messages), 1)
        self.assertIn('"actor_ids": ["hero"]', messages[0][1]["content"])

    def test_apply_legacy_context_candidate_drops_supporting_actor(self):
        import actor_pipeline as ap
        board = {
            "actors": {
                "hero": {"name": "主角", "role": "主角"},
                "support": {"name": "配角", "role": "配角"},
            },
            "shots": [{"id": "S1", "dur": 4}],
        }
        self.board_path.write_text(json.dumps(board, ensure_ascii=False), encoding="utf-8")
        _, revision = ap._read_board(self.board_path)
        run_id = "context_legacy_support"
        candidate = {
            "run_id": run_id, "candidate_kind": "context", "status": "ready",
            "board": self.board_path.name, "source_revision": revision,
            "shot_ids": ["S1"], "context": {
                "version": "actor-context-v1", "continuity_id": "main",
                "continuities": [{"id": "main", "initial_state": {
                    "hero": {"known_facts": []}, "support": {"known_facts": []},
                }}],
                "facts": [], "events": [],
                "actor_cards": {
                    "hero": {"personality": "勇敢"},
                    "support": {"personality": "配角"},
                },
            },
        }
        path = Path(ap._candidate_dir(self.board_path)) / (run_id + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(candidate, ensure_ascii=False), encoding="utf-8")
        ap.apply_candidate(self.board_path, run_id)
        applied, _ = ap._read_board(self.board_path)
        context = applied["acting_context"]
        self.assertNotIn("support", context["actor_cards"])
        self.assertNotIn("support", context["continuities"][0]["initial_state"])


if __name__ == "__main__":
    unittest.main()






