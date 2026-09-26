# -*- coding: utf-8 -*-
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import chatgpt_queue
import chatgpt_run_api


def _png(size=(1600, 900), color=(12, 34, 56)):
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, format="PNG")
    return output.getvalue()


class ChatGPTRunApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name, "09_测试剧本")
        (self.project / "素材" / "人物").mkdir(parents=True)
        (self.project / "创作").mkdir(parents=True)
        (self.project / "素材" / "人物.json").write_text(json.dumps({
            "characters": [{"id": "hero", "name": "主角", "sheet_prompt": "黑发制服，五视图设定图：脸部正面特写、45度左侧脸特写、不带头部正面全身、不带头部侧面全身、严格背面全身"}]
        }, ensure_ascii=False), encoding="utf-8")
        (self.project / "素材" / "道具.json").write_text(json.dumps({
            "props": [{
                "id": "hero_sword", "name": "佩剑", "image_prompt": "主角佩剑单体设定图",
                "parent_ref": "@character:hero",
            }]
        }, ensure_ascii=False), encoding="utf-8")
        self.reference_bytes = _png((800, 450), (90, 40, 20))
        (self.project / "素材" / "人物" / "hero.png").write_bytes(self.reference_bytes)
        self.job = chatgpt_queue.queue_assets(str(self.project), ["@prop:hero_sword"])[0]

    def tearDown(self):
        self.temp.cleanup()

    def _create(self):
        return chatgpt_run_api.create_run(str(self.project), {
            "job_ids": [self.job["id"]], "options": {"vision_validation": False}
        })

    def test_create_rejects_empty_or_foreign_jobs(self):
        with self.assertRaises(chatgpt_run_api.ApiError) as empty:
            chatgpt_run_api.create_run(str(self.project), {"job_ids": []})
        self.assertEqual(empty.exception.status, 400)

        with self.assertRaises(chatgpt_run_api.ApiError) as foreign:
            chatgpt_run_api.create_run(str(self.project), {"job_ids": ["foreign-job"]})
        self.assertEqual(foreign.exception.status, 400)

    def test_claim_hides_absolute_paths_and_exposes_reference_ids(self):
        created = self._create()

        attempt = chatgpt_run_api.claim_next(
            str(self.project), {"run_id": created["run_id"]}, created["run_token"]
        )

        self.assertEqual(attempt["references"][0]["reference_id"], "R1")
        serialized = json.dumps(attempt, ensure_ascii=False)
        self.assertNotIn(str(self.project), serialized)
        self.assertNotIn("path", attempt["references"][0])

    def test_reference_endpoint_requires_run_token_and_returns_exact_bytes(self):
        created = self._create()
        attempt = chatgpt_run_api.claim_next(
            str(self.project), {"run_id": created["run_id"]}, created["run_token"]
        )
        args = (str(self.project), created["run_id"], attempt["attempt_id"], "R1")

        with self.assertRaises(chatgpt_run_api.ApiError) as denied:
            chatgpt_run_api.open_reference(*args, token="wrong")
        self.assertEqual(denied.exception.status, 401)

        opened = chatgpt_run_api.open_reference(*args, token=created["run_token"])
        self.assertEqual(opened["content"], self.reference_bytes)
        self.assertEqual(opened["content_type"], "image/png")
        self.assertEqual(opened["file_name"], "hero.png")

    def test_staged_upload_rejects_wrong_revision(self):
        created = self._create()
        attempt = chatgpt_run_api.claim_next(
            str(self.project), {"run_id": created["run_id"]}, created["run_token"]
        )
        for index, phase in enumerate(("uploading", "preparing", "generating"), 1):
            chatgpt_run_api.record_event(str(self.project), {
                "run_id": created["run_id"], "attempt_id": attempt["attempt_id"],
                "event_id": f"evt-{index}", "event_type": "phase", "payload": {"phase": phase},
            }, created["run_token"])
        current = chatgpt_run_api.get_run(str(self.project), {"run_id": created["run_id"]})

        with self.assertRaises(chatgpt_run_api.ApiError) as stale:
            chatgpt_run_api.stage_result(str(self.project), {
                "run_id": created["run_id"], "attempt_id": attempt["attempt_id"],
                "expected_revision": current["revision"] - 1, "file_name": "result.png",
                "metadata": {"capture_method": "download_link"},
            }, created["run_token"], _png())

        self.assertEqual(stale.exception.status, 409)
        refreshed = chatgpt_run_api.get_run(str(self.project), {"run_id": created["run_id"]})
        self.assertEqual(refreshed["status"], "generating")


if __name__ == "__main__":
    unittest.main()
