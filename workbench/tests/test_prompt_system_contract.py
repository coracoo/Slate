# -*- coding: utf-8 -*-
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workbench" / "tools"))

import prompt_modules as pm


class PromptSystemContractTests(unittest.TestCase):
    def test_custom_text_extends_instead_of_replacing_builtin_contract(self):
        with mock.patch.object(pm, "_override", lambda sid: "用户补充：偏好长焦。知识={{knowledge}}"):
            result = pm._emit("storyboard", "内置硬契约：只输出 JSON。", "参考片例A")
        self.assertTrue(result.startswith("内置硬契约：只输出 JSON。"))
        self.assertIn("【用户策略补充】", result)
        self.assertIn("参考片例A", result)

    def test_deferred_format_prompt_tolerates_json_braces_in_custom_text(self):
        custom = '补充输出示例：{"confidence":1}；知识={{knowledge}}'
        with mock.patch.object(pm, "_override", lambda sid: custom):
            compiled = pm.sys_for("attribute", "硬契约，时间 {t0:.1f}s；JSON={{\"lines\":[]}}")
        rendered = compiled.format(t0=1.25)
        self.assertIn('"confidence":1', rendered)
        self.assertIn("硬契约", rendered)

    def test_attribute_previews_share_runtime_compiler_and_do_not_fail(self):
        with mock.patch.object(pm, "_override", return_value=None):
            rows = {row["id"]: row for row in pm.list_system_skills()}
        for sid in ("attribute", "attribute_norm"):
            self.assertNotIn("预览失败", rows[sid]["text"])
            self.assertIn("只输出", rows[sid]["text"])

    def test_text_repair_preserves_builtin_contract_and_includes_original_and_gaps(self):
        with mock.patch.object(pm,'_override',return_value='补充：保留口语感'):
            system,user=pm.repair_episode_prompt({'id':'E1'},'原稿台词',
                {'missing_speakers':[{'name':'未登记人物'}]},'原有世界设定',{},'复用守卫')
        self.assertIn('禁止新增实体',system)
        self.assertIn('保留口语感',system)
        self.assertIn('原稿台词',user)
        self.assertIn('未登记人物',user)
        self.assertIn('复用守卫',user)
        self.assertIn('script_repair',[s['id'] for s in pm.SYSTEM_SKILLS])


if __name__ == "__main__":
    unittest.main()
