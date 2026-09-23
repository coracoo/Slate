# -*- coding: utf-8 -*-
"""extract 收尾自动平面图初稿：cmd_extract 完成三件套后链式调 gen_plan.draft_missing_scene_plans；
失败 fail-soft（只警告），不影响已落盘的提炼产物。"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))

import creation_pipeline as cp
import gen_plan

EMPTY_EXTRACT = json.dumps({"characters": [], "scenes": [], "props": []}, ensure_ascii=False)


class ExtractPlanDraftChainTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.proj = self._td.name
        os.makedirs(os.path.join(self.proj, "剧本"))
        os.makedirs(os.path.join(self.proj, "素材"))
        json.dump({"rev": 1, "episodes": [{"id": "E1", "title": "初阵", "text": "中军帐内，甲向乙禀报军情。"}]},
                  open(os.path.join(self.proj, "剧本", "分集.json"), "w", encoding="utf-8"),
                  ensure_ascii=False)

    def tearDown(self):
        self._td.cleanup()

    def _run_extract(self, draft):
        with mock.patch.object(cp, "pick_vendor", return_value="v1"), \
             mock.patch.object(cp, "VendorClient") as vc, \
             mock.patch.object(vc.return_value, "model", return_value="m1"), \
             mock.patch.object(cp, "chat_retry", return_value=EMPTY_EXTRACT), \
             mock.patch.object(cp.PM, "characters_prompt", return_value=("s", "u")), \
             mock.patch.object(cp.PM, "scenes_prompt", return_value=("s", "u")), \
             mock.patch.object(cp.PM, "props_prompt", return_value=("s", "u")), \
             mock.patch.object(cp, "reconcile_characters", return_value=0), \
             mock.patch.object(gen_plan, "draft_missing_scene_plans", draft):
            cp.cmd_extract(self.proj, None, None)

    def test_extract_chains_plan_draft_once(self):
        draft = mock.Mock(return_value={"生成": 0, "跳过": 0, "失败": 0})
        self._run_extract(draft)
        draft.assert_called_once_with(self.proj, vendor=None)
        # 提炼主产物照常落盘
        self.assertTrue(os.path.isfile(os.path.join(self.proj, "素材", "场景.json")))

    def test_draft_failure_does_not_break_extract(self):
        draft = mock.Mock(side_effect=RuntimeError("平面图厂商超时"))
        self._run_extract(draft)   # 不抛异常
        draft.assert_called_once()
        for name in ("人物.json", "场景.json", "道具.json"):
            self.assertTrue(os.path.isfile(os.path.join(self.proj, "素材", name)))


if __name__ == '__main__':
    unittest.main()
