# -*- coding: utf-8 -*-
"""台词说话人解析表：别名要能落到角色 id —— ④ 音色绑定与 ⑦ V 编译都按 character_id 精确查，
失配的中文称呼会一路当 id 落进 actors，导致整链查不到。"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workbench" / "tools"))

from creation_pipeline import _speaker_id_map


class SpeakerIdMapTests(unittest.TestCase):
    def test_resolves_name_id_and_alias(self):
        chars = [{"id": "baixiaozhuxu", "name": "白筱竹", "aliases": ["筱竹", "转学生"]},
                 {"id": "zuotenglu", "name": "佐藤露", "aliases": []}]
        mapping = _speaker_id_map(chars)
        self.assertEqual(mapping["白筱竹"], "baixiaozhuxu")
        self.assertEqual(mapping["转学生"], "baixiaozhuxu")
        self.assertEqual(mapping["zuotenglu"], "zuotenglu", "LLM 直接吐 id 时也要自映射")

    def test_alias_never_shadows_another_character_name(self):
        chars = [{"id": "a", "name": "队长", "aliases": ["老王"]},
                 {"id": "b", "name": "老王", "aliases": []}]
        mapping = _speaker_id_map(chars)
        self.assertEqual(mapping["老王"], "b", "别名不得盖掉另一位角色的真实姓名")

    def test_ignores_malformed_rows(self):
        self.assertEqual(_speaker_id_map([None, {"name": "无 id"}, "字符串", {}]), {})


if __name__ == "__main__":
    unittest.main()
