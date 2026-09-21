# -*- coding: utf-8 -*-
"""演员提示词模块的离线回归测试。"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "workbench" / "tools"
CORE = ROOT / "previs_system" / "tools"
for p in (TOOLS, CORE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


class ActorPromptTests(unittest.TestCase):
    def test_actor_prompts_are_registered_and_constrain_camera(self):
        import prompt_modules as pm
        self.assertIn("actor_prepare", pm.SKILLS)
        self.assertIn("actor_perform", pm.SKILLS)
        ids = {item["id"] for item in pm.SYSTEM_SKILLS}
        self.assertIn("actor_prepare", ids)
        self.assertIn("actor_perform", ids)
        system, user = pm.actor_prepare_prompt({"id": "S1", "dur": 4}, {})
        self.assertIn("不能修改", system)
        self.assertIn("S1", user)
        system, user = pm.actor_perform_prompt({"shot_id": "S1", "actor_ids": ["a"]})
        self.assertIn("actors", system)
        self.assertIn("主角", system)
        self.assertIn("S1", user)


if __name__ == "__main__":
    unittest.main()
