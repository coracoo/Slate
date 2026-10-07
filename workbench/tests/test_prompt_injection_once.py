# -*- coding: utf-8 -*-
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workbench" / "tools"))

import prompt_modules as pm


class PromptInjectionOnceTests(unittest.TestCase):
    def test_storyboard_system_extension_is_injected_once(self):
        with mock.patch.object(pm, "_override", side_effect=lambda sid: "自定义导演补充" if sid == "storyboard" else None):
            system, _ = pm.storyboard_prompt("一场对话", [], [], mood_text="紧张对峙")
        self.assertEqual(system.count("自定义导演补充"), 1)

    def test_storyboard_knowledge_is_retrieved_once(self):
        with mock.patch.object(pm, "_knowledge", return_value=[{"skill": "正反打"}]) as retrieve:
            pm.storyboard_prompt("一场对话", [], [], mood_text="紧张对峙")
        retrieve.assert_called_once_with("紧张对峙", k=4)

    def test_actor_system_extensions_are_injected_once(self):
        def extension(sid):
            return "表演补充规则" if sid in ("actor_prepare", "actor_perform") else None

        with mock.patch.object(pm, "_override", side_effect=extension):
            prepared, _ = pm.actor_prepare_prompt({"id": "S1", "dur": 4})
            performed, _ = pm.actor_perform_prompt({"shot_id": "S1", "actor_ids": []})
        self.assertEqual(prepared.count("表演补充规则"), 1)
        self.assertEqual(performed.count("表演补充规则"), 1)


if __name__ == "__main__":
    unittest.main()
