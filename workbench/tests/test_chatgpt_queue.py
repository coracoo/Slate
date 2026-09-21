# -*- coding: utf-8 -*-
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
WORKBENCH = os.path.abspath(os.path.join(HERE, ".."))
if WORKBENCH not in sys.path:
    sys.path.insert(0, WORKBENCH)
TOOLS = os.path.join(WORKBENCH, "tools")
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import chatgpt_queue


class ChatGPTQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = self.tmp.name
        os.makedirs(os.path.join(self.project, "分镜"), exist_ok=True)
        os.makedirs(os.path.join(self.project, "创作"), exist_ok=True)
        board = {
            "actors": [],
            "shots": [
                {"id": "S1", "dur": 2.0, "prompt_image": "教室中景，人物看向窗外。",
                 "prompt": "教室中景，人物看向窗外。", "scene": "room",
                 "shot_size": "中景", "angle": "平视", "camera_move": "固定"},
                {"id": "S2", "dur": 1.5, "prompt_image": "走廊近景，人物停步。",
                 "prompt": "走廊近景，人物停步。", "scene": "room",
                 "shot_size": "近景", "angle": "平视", "camera_move": "固定"},
                {"id": "S3", "dur": 1.0, "prompt_image": "手部特写。",
                 "prompt": "手部特写。", "scene": "room",
                 "shot_size": "特写", "angle": "平视", "camera_move": "固定"},
            ]
        }
        with open(os.path.join(self.project, "分镜", "剧本_E1.json"), "w", encoding="utf-8") as fh:
            json.dump(board, fh, ensure_ascii=False)

    def tearDown(self):
        self.tmp.cleanup()

    def test_queue_shots_creates_chatgpt_items(self):
        jobs = chatgpt_queue.queue_shots(self.project, "剧本_E1.json", ["S1", "S2", "S3"])
        self.assertEqual(len(jobs), 3)
        self.assertTrue(all(job["delivery"] == "chatgpt_queue" for job in jobs))
        self.assertTrue(all(job["status"] == "queued" for job in jobs))
        self.assertEqual(jobs[0]["task_type"], "reference_image")
        self.assertTrue(jobs[0]["output_spec"]["filename"].endswith("_E1_S01_REF_v001.png"))
        with open(os.path.join(self.project, "创作", "creation.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(len(data["items"]), 3)
        self.assertIn("prompt_assembled", data["items"][0])

    def test_duplicate_queue_creates_new_version(self):
        first = chatgpt_queue.queue_shots(self.project, "剧本_E1.json", ["S1"])[0]
        second = chatgpt_queue.queue_shots(self.project, "剧本_E1.json", ["S1"])[0]
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(second["output_spec"]["version"], 2)
        self.assertTrue(second["output_spec"]["filename"].endswith("_E1_S01_REF_v002.png"))

    def test_list_and_export_spec(self):
        jobs = chatgpt_queue.queue_shots(self.project, "剧本_E1.json", ["S1", "S2"])
        listed = chatgpt_queue.list_jobs(self.project, status="queued")
        self.assertEqual([x["id"] for x in listed], [x["id"] for x in jobs])
        spec = chatgpt_queue.get_export_spec(jobs)
        self.assertEqual(spec["manifest_name"], "manifest.json")
        self.assertEqual(len(spec["jobs"]), 2)


if __name__ == "__main__":
    unittest.main()
