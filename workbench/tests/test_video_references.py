# -*- coding: utf-8 -*-
import os
import sys
import unittest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools")))
from llm_openai import VendorClient


class VideoReferenceTests(unittest.TestCase):
    def test_storyboard_reference_is_not_automatically_first_frame(self):
        cli = VendorClient.from_config({'id':'doubao-api','base_url':'https://example.invalid',
                                      'models':{'video':'doubao-seedance-2-0'},'enabled':True})
        captured = {}
        def fake_post(url, payload, timeout):
            captured.update(payload)
            return {"id": "test-task"}
        cli._post = fake_post
        cli._poll_ark_task = lambda *args: "https://example.invalid/video.mp4"
        cli.generate_video("少女下坠", image_refs=["https://example.invalid/panel.png"])
        self.assertNotIn("first_frame", captured)
        self.assertEqual(captured["content"][1]["role"], "reference_image")
        self.assertEqual(captured["content"][1]["image_url"]["url"], "https://example.invalid/panel.png")


if __name__ == "__main__":
    unittest.main()
