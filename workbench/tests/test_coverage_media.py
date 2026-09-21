# -*- coding: utf-8 -*-
import os
import sys
import unittest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools")))
from creation_store import coverage_matrix


class CoverageMediaTests(unittest.TestCase):
    def test_image_and_video_status_are_separate(self):
        items = [
            {"id": "i1", "type": "image", "shot_id": "S1", "status": "done"},
            {"id": "v1", "type": "video", "shot_id": "S1", "status": "error"},
        ]
        self.assertEqual(coverage_matrix(items, ["S1"], media_type="image")["shots"][0]["status"], "done")
        self.assertEqual(coverage_matrix(items, ["S1"], media_type="video")["shots"][0]["status"], "error")


if __name__ == "__main__":
    unittest.main()
