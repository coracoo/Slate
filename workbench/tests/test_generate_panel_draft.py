# -*- coding: utf-8 -*-
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools")))
from generate_panel_draft import generate_panel_draft


class FakeClient:
    def __init__(self):
        self.messages = None

    def chat(self, messages, **kwargs):
        self.messages = messages
        return '{"panel_id":"P1","visual_description":"@character:girl 悬在课桌上方，视线向下","local_negative":["仰视"],"used_refs":["@character:girl"]}'


class GeneratePanelDraftTests(unittest.TestCase):
    def test_system_role_and_scene_facts_are_separate(self):
        panel = {"id": "P1", "shot_ids": ["S1"], "beat": "少女下坠中",
                 "composition": {"aspect_ratio": "16:9", "shot_size": "中景"},
                 "visible_refs": ["@character:girl"]}
        board = {"shots": [{"id": "S1", "action": "少女从天花板下坠", "prompt": "旧长提示词不得输入"}]}
        with tempfile.TemporaryDirectory() as project:
            client = FakeClient()
            draft = generate_panel_draft(project, panel, board, client=client)
        self.assertEqual(draft["panel_id"], "P1")
        self.assertEqual(client.messages[0]["role"], "system")
        self.assertIn("故事版", client.messages[0]["content"])
        self.assertIn("少女下坠中", client.messages[1]["content"])
        self.assertNotIn("旧长提示词", client.messages[1]["content"])


if __name__ == "__main__":
    unittest.main()
