# -*- coding: utf-8 -*-
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import chatgpt_queue
import chatgpt_runs


def _png(size=(1600, 900), color=(20, 40, 60)):
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, format="PNG")
    return output.getvalue()


class ChatGPTRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = os.path.join(self.temp.name, "09_测试剧本")
        os.makedirs(os.path.join(self.project, "素材"), exist_ok=True)
        os.makedirs(os.path.join(self.project, "创作"), exist_ok=True)
        with open(os.path.join(self.project, "素材", "人物.json"), "w", encoding="utf-8") as fh:
            json.dump({"characters": [
                {"id": "hero", "name": "主角", "sheet_prompt": "黑发制服人物三视图"},
                {"id": "friend", "name": "同伴", "sheet_prompt": "银发制服人物三视图"},
            ]}, fh, ensure_ascii=False)
        self.first = chatgpt_queue.queue_assets(self.project, ["@character:hero"])[0]

    def tearDown(self):
        self.temp.cleanup()

    def _second_job(self):
        return chatgpt_queue.queue_assets(self.project, ["@character:friend"])[0]

    def _advance_to_generating(self, created, attempt, prefix="evt"):
        for index, phase in enumerate(("uploading", "preparing", "generating"), 1):
            chatgpt_runs.record_event(
                self.project, created["run_id"], attempt["attempt_id"], created["run_token"],
                f"{prefix}-{index}", "phase", {"phase": phase}
            )

    def test_create_run_freezes_scope_and_hides_token_at_rest(self):
        created = chatgpt_runs.create_run(self.project, [self.first["id"]], {"vision_validation": False})
        self._second_job()

        stored = chatgpt_runs.get_run(self.project, created["run_id"])
        disk = json.loads(Path(self.project, "创作", "chatgpt_runs", created["run_id"], "run.json").read_text(encoding="utf-8"))

        self.assertEqual(stored["job_ids"], [self.first["id"]])
        self.assertNotIn("run_token", stored)
        self.assertNotIn("token_hash", stored)
        self.assertNotIn(created["run_token"], json.dumps(disk, ensure_ascii=False))
        self.assertEqual(len(stored["batches"]), 1)

    def test_claim_requires_token_and_returns_frozen_attempt(self):
        created = chatgpt_runs.create_run(self.project, [self.first["id"]], {})
        with self.assertRaises(chatgpt_runs.RunAuthError):
            chatgpt_runs.claim_next(self.project, created["run_id"], "wrong")

        attempt = chatgpt_runs.claim_next(self.project, created["run_id"], created["run_token"])

        self.assertEqual(attempt["job_id"], self.first["id"])
        self.assertRegex(attempt["prompt_sha256"], r"^[0-9a-f]{64}$")
        self.assertNotIn("token_hash", attempt)
        self.assertNotIn("absolute_path", json.dumps(attempt, ensure_ascii=False))

    def test_existing_attempt_is_returned_after_reload(self):
        created = chatgpt_runs.create_run(self.project, [self.first["id"]], {})
        first = chatgpt_runs.claim_next(self.project, created["run_id"], created["run_token"])
        second = chatgpt_runs.claim_next(self.project, created["run_id"], created["run_token"])
        self.assertEqual(second["attempt_id"], first["attempt_id"])

    def test_active_run_prevents_the_same_job_from_being_claimed_twice(self):
        created = chatgpt_runs.create_run(self.project, [self.first["id"]], {})
        with self.assertRaises(chatgpt_runs.RunStateError) as conflict:
            chatgpt_runs.create_run(self.project, [self.first["id"]], {})
        self.assertIn(created["run_id"], str(conflict.exception))

    def test_illegal_transition_is_rejected(self):
        created = chatgpt_runs.create_run(self.project, [self.first["id"]], {})
        attempt = chatgpt_runs.claim_next(self.project, created["run_id"], created["run_token"])
        with self.assertRaises(chatgpt_runs.RunStateError):
            chatgpt_runs.record_event(
                self.project, created["run_id"], attempt["attempt_id"], created["run_token"],
                "evt-import", "phase", {"phase": "importing"}
            )

    def test_unknown_send_outcome_pauses_without_new_attempt(self):
        created = chatgpt_runs.create_run(self.project, [self.first["id"]], {})
        attempt = chatgpt_runs.claim_next(self.project, created["run_id"], created["run_token"])
        chatgpt_runs.record_event(
            self.project, created["run_id"], attempt["attempt_id"], created["run_token"],
            "evt-unknown", "send_unknown", {"reason": "页面刷新，发送结果不明"}
        )

        run = chatgpt_runs.get_run(self.project, created["run_id"])
        again = chatgpt_runs.claim_next(self.project, created["run_id"], created["run_token"])
        self.assertEqual(run["status"], "paused")
        self.assertEqual(again["attempt_id"], attempt["attempt_id"])
        self.assertEqual(again["phase"], "paused")

    def test_upload_diagnostic_preserves_phase_and_excludes_private_data(self):
        created = chatgpt_runs.create_run(self.project, [self.first["id"]], {})
        attempt = chatgpt_runs.claim_next(self.project, created["run_id"], created["run_token"])
        result = chatgpt_runs.record_event(
            self.project, created["run_id"], attempt["attempt_id"], created["run_token"],
            "diag-1", "upload_diagnostic", {"thumbnailCount": 5, "sendReady": False, "base64": "SECRET"}
        )
        self.assertEqual(result["phase"], attempt["phase"])
        events = Path(self.project, "创作", "chatgpt_runs", created["run_id"], "events.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("SECRET", events)
        self.assertEqual(json.loads(events)["payload"], {"thumbnailCount": 5, "sendReady": False})

    def test_stage_rejects_undecodable_image(self):
        created = chatgpt_runs.create_run(self.project, [self.first["id"]], {})
        attempt = chatgpt_runs.claim_next(self.project, created["run_id"], created["run_token"])
        self._advance_to_generating(created, attempt)
        with self.assertRaises(chatgpt_runs.ResultValidationError):
            chatgpt_runs.stage_result(
                self.project, created["run_id"], attempt["attempt_id"], created["run_token"],
                b"not-an-image", "bad.png", {"capture_method": "dom_image_source"}
            )

    def test_same_attempt_and_hash_is_idempotent(self):
        created = chatgpt_runs.create_run(self.project, [self.first["id"]], {})
        attempt = chatgpt_runs.claim_next(self.project, created["run_id"], created["run_token"])
        self._advance_to_generating(created, attempt)
        content = _png()
        first = chatgpt_runs.stage_result(
            self.project, created["run_id"], attempt["attempt_id"], created["run_token"],
            content, "result.png", {"capture_method": "download_link"}
        )
        second = chatgpt_runs.stage_result(
            self.project, created["run_id"], attempt["attempt_id"], created["run_token"],
            content, "result.png", {"capture_method": "download_link"}
        )
        self.assertEqual(second["sha256"], first["sha256"])
        self.assertTrue(second["idempotent"])

    def test_response_locator_drops_signed_query_and_fragment(self):
        created = chatgpt_runs.create_run(self.project, [self.first["id"]], {})
        attempt = chatgpt_runs.claim_next(self.project, created["run_id"], created["run_token"])
        self._advance_to_generating(created, attempt)

        staged = chatgpt_runs.stage_result(
            self.project, created["run_id"], attempt["attempt_id"], created["run_token"],
            _png(), "result.png", {
                "response_locator": "https://cdn.example/result.png?token=secret#viewer",
            },
        )

        self.assertEqual(staged["response_locator"], "https://cdn.example/result.png")

    def test_same_hash_for_different_jobs_requires_review(self):
        second = self._second_job()
        created = chatgpt_runs.create_run(self.project, [self.first["id"], second["id"]], {})
        first_attempt = chatgpt_runs.claim_next(self.project, created["run_id"], created["run_token"])
        self._advance_to_generating(created, first_attempt, "first")
        content = _png()
        chatgpt_runs.stage_result(self.project, created["run_id"], first_attempt["attempt_id"], created["run_token"], content, "one.png", {})
        chatgpt_runs.import_staged_result(self.project, created["run_id"], first_attempt["attempt_id"], created["run_token"])
        second_attempt = chatgpt_runs.claim_next(self.project, created["run_id"], created["run_token"])
        self._advance_to_generating(created, second_attempt, "second")

        duplicate = chatgpt_runs.stage_result(
            self.project, created["run_id"], second_attempt["attempt_id"], created["run_token"], content, "two.png", {}
        )

        self.assertEqual(duplicate["phase"], "needs_review")
        self.assertIn("另一任务", duplicate["reason"])
        self.assertEqual(chatgpt_runs.get_run(self.project, created["run_id"])["status"], "needs_review")

    def test_same_hash_across_runs_requires_review(self):
        content = _png()
        first_run = chatgpt_runs.create_run(self.project, [self.first["id"]], {})
        first_attempt = chatgpt_runs.claim_next(self.project, first_run["run_id"], first_run["run_token"])
        self._advance_to_generating(first_run, first_attempt, "first-run")
        chatgpt_runs.stage_result(
            self.project, first_run["run_id"], first_attempt["attempt_id"], first_run["run_token"],
            content, "one.png", {},
        )
        chatgpt_runs.import_staged_result(
            self.project, first_run["run_id"], first_attempt["attempt_id"], first_run["run_token"]
        )
        second = self._second_job()
        second_run = chatgpt_runs.create_run(self.project, [second["id"]], {})
        second_attempt = chatgpt_runs.claim_next(
            self.project, second_run["run_id"], second_run["run_token"]
        )
        self._advance_to_generating(second_run, second_attempt, "second-run")

        result = chatgpt_runs.stage_result(
            self.project, second_run["run_id"], second_attempt["attempt_id"], second_run["run_token"],
            content, "two.png", {},
        )

        self.assertEqual(result["phase"], "needs_review")
        self.assertIn(self.first["id"], result["reason"])

    def test_waiting_dependency_is_refreshed_after_parent_image_exists(self):
        with open(os.path.join(self.project, "素材", "道具.json"), "w", encoding="utf-8") as fh:
            json.dump({"props": [{
                "id": "hero_sword", "name": "佩剑", "image_prompt": "青铜佩剑",
                "parent_ref": "@character:hero",
            }]}, fh, ensure_ascii=False)
        child = chatgpt_queue.queue_assets(self.project, ["@prop:hero_sword"])[0]
        created = chatgpt_runs.create_run(self.project, [child["id"]], {})
        waiting = chatgpt_runs.claim_next(self.project, created["run_id"], created["run_token"])
        self.assertEqual(waiting["phase"], "waiting_dependencies")
        os.makedirs(os.path.join(self.project, "素材", "人物"), exist_ok=True)
        Path(self.project, "素材", "人物", "hero.png").write_bytes(_png((800, 450)))

        refreshed = chatgpt_runs.claim_next(self.project, created["run_id"], created["run_token"])

        self.assertEqual(refreshed["attempt_id"], waiting["attempt_id"])
        self.assertEqual(refreshed["phase"], "ready")
        self.assertEqual(refreshed["missing_refs"], [])
        self.assertEqual(refreshed["references"][0]["reference_id"], "R1")

    def test_import_error_restores_staged_phase_and_can_retry(self):
        created = chatgpt_runs.create_run(self.project, [self.first["id"]], {})
        attempt = chatgpt_runs.claim_next(self.project, created["run_id"], created["run_token"])
        self._advance_to_generating(created, attempt)
        chatgpt_runs.stage_result(
            self.project, created["run_id"], attempt["attempt_id"], created["run_token"],
            _png(), "result.png", {},
        )
        real_import = chatgpt_runs.chatgpt_import.import_package
        with mock.patch.object(chatgpt_runs.chatgpt_import, "import_package", side_effect=OSError("磁盘中断")):
            with self.assertRaises(OSError):
                chatgpt_runs.import_staged_result(
                    self.project, created["run_id"], attempt["attempt_id"], created["run_token"]
                )
        after_failure = chatgpt_runs.get_run(self.project, created["run_id"])
        self.assertEqual(after_failure["status"], "staged")
        self.assertEqual(after_failure["current_attempt"]["phase"], "staged")

        with mock.patch.object(chatgpt_runs.chatgpt_import, "import_package", wraps=real_import):
            result = chatgpt_runs.import_staged_result(
                self.project, created["run_id"], attempt["attempt_id"], created["run_token"]
            )
        self.assertEqual(result["status"], "done")

    def test_importing_attempt_can_recover_after_process_restart(self):
        created = chatgpt_runs.create_run(self.project, [self.first["id"]], {})
        attempt = chatgpt_runs.claim_next(self.project, created["run_id"], created["run_token"])
        self._advance_to_generating(created, attempt)
        chatgpt_runs.stage_result(
            self.project, created["run_id"], attempt["attempt_id"], created["run_token"],
            _png(), "result.png", {},
        )
        attempt_path = Path(self.project, "创作", "chatgpt_runs", created["run_id"],
                            "attempts", attempt["attempt_id"] + ".json")
        interrupted = json.loads(attempt_path.read_text(encoding="utf-8"))
        interrupted["phase"] = "importing"
        attempt_path.write_text(json.dumps(interrupted, ensure_ascii=False), encoding="utf-8")

        result = chatgpt_runs.import_staged_result(
            self.project, created["run_id"], attempt["attempt_id"], created["run_token"]
        )

        self.assertEqual(result["status"], "done")

    def test_changed_job_snapshot_moves_staged_result_to_review(self):
        created = chatgpt_runs.create_run(self.project, [self.first["id"]], {})
        attempt = chatgpt_runs.claim_next(self.project, created["run_id"], created["run_token"])
        self._advance_to_generating(created, attempt)
        chatgpt_runs.stage_result(
            self.project, created["run_id"], attempt["attempt_id"], created["run_token"],
            _png(), "result.png", {},
        )
        Path(self.project, "素材", "人物.json").write_text(json.dumps({"characters": [
            {"id": "hero", "name": "主角", "sheet_prompt": "已经修改为红发战甲人物三视图"},
            {"id": "friend", "name": "同伴", "sheet_prompt": "银发制服人物三视图"},
        ]}, ensure_ascii=False), encoding="utf-8")

        result = chatgpt_runs.import_staged_result(
            self.project, created["run_id"], attempt["attempt_id"], created["run_token"]
        )

        self.assertEqual(result["status"], "needs_review")
        self.assertIn("任务内容已变化", result["reason"])
        self.assertEqual(chatgpt_runs.get_run(self.project, created["run_id"])["status"], "needs_review")

    def test_changed_reference_image_moves_staged_result_to_review(self):
        with open(os.path.join(self.project, "素材", "道具.json"), "w", encoding="utf-8") as fh:
            json.dump({"props": [{
                "id": "hero_sword", "name": "佩剑", "image_prompt": "青铜佩剑",
                "parent_ref": "@character:hero",
            }]}, fh, ensure_ascii=False)
        mother = Path(self.project, "素材", "人物", "hero.png")
        mother.parent.mkdir(parents=True, exist_ok=True)
        mother.write_bytes(_png((800, 450), (10, 20, 30)))
        child = chatgpt_queue.queue_assets(self.project, ["@prop:hero_sword"])[0]
        created = chatgpt_runs.create_run(self.project, [child["id"]], {})
        attempt = chatgpt_runs.claim_next(self.project, created["run_id"], created["run_token"])
        self._advance_to_generating(created, attempt)
        chatgpt_runs.stage_result(
            self.project, created["run_id"], attempt["attempt_id"], created["run_token"],
            _png(), "result.png", {},
        )
        mother.write_bytes(_png((800, 450), (90, 80, 70)))

        result = chatgpt_runs.import_staged_result(
            self.project, created["run_id"], attempt["attempt_id"], created["run_token"]
        )

        self.assertEqual(result["status"], "needs_review")
        self.assertIn("参考图", result["reason"])

    def test_import_marks_done_only_after_result_is_staged(self):
        created = chatgpt_runs.create_run(self.project, [self.first["id"]], {})
        attempt = chatgpt_runs.claim_next(self.project, created["run_id"], created["run_token"])
        with self.assertRaises(chatgpt_runs.RunStateError):
            chatgpt_runs.import_staged_result(self.project, created["run_id"], attempt["attempt_id"], created["run_token"])
        queued = chatgpt_queue.get_job(self.project, self.first["id"])
        self.assertEqual(queued["status"], "queued")

        self._advance_to_generating(created, attempt)
        chatgpt_runs.stage_result(self.project, created["run_id"], attempt["attempt_id"], created["run_token"], _png(), "result.png", {})
        result = chatgpt_runs.import_staged_result(self.project, created["run_id"], attempt["attempt_id"], created["run_token"])

        self.assertEqual(result["status"], "done")
        self.assertEqual(chatgpt_queue.get_job(self.project, self.first["id"])["status"], "done")
        self.assertTrue(Path(self.project, "素材", "人物", "hero.png").is_file())
        index = json.loads(Path(self.project, "素材", "素材图.json").read_text(encoding="utf-8"))
        self.assertEqual(index["人物"]["hero"]["path"], "素材/人物/hero.png")


if __name__ == "__main__":
    unittest.main()
