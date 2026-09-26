# -*- coding: utf-8 -*-
"""台词归属工具的系统提示词覆盖层：内置提示词与 sys_for 覆盖的先后顺序曾写错，
attribute_norm 覆盖层永久失效（NameError 被 except 吞掉）。"""
import importlib
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workbench" / "tools"))


class AttributeSystemPromptTests(unittest.TestCase):
    def setUp(self):
        sys.modules.pop("attribute_speakers", None)

    def tearDown(self):
        sys.modules.pop("attribute_speakers", None)

    def test_both_step_prompts_pass_through_the_overlay(self):
        import prompt_modules

        def fake_sys_for(key, default=""):
            return f"[覆盖:{key}]" + (default or "")

        with mock.patch.object(prompt_modules, "sys_for", fake_sys_for):
            mod = importlib.import_module("attribute_speakers")
        self.assertTrue(mod.ATTR_PROMPT.startswith("[覆盖:attribute]"),
                        "归属提示词未走覆盖层")
        self.assertTrue(mod.NORM_PROMPT.startswith("[覆盖:attribute_norm]"),
                        "别名归一提示词未走覆盖层（历史缺陷：在定义前就被引用）")


if __name__ == "__main__":
    unittest.main()
