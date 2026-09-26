# -*- coding: utf-8 -*-
"""样例契约（N76）：examples/*.example.json 里的枚举值必须是管线真会写出的值。

样例是给人与 AI 照着写的"契约文档"，写错大小写就等于教出一个管线永不产出的取值。
词表不硬编码在测试里，而从写入方 merge_lines.py 现场解析，改工具即改契约。
"""
import io
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples"
MERGE = ROOT / "workbench" / "tools" / "merge_lines.py"


def writer_source_vocabulary():
    """merge_lines 里所有 `["source"] = "xxx"` / `"source": "xxx"` 字面量。"""
    src = MERGE.read_text(encoding="utf-8")
    vals = re.findall(r'\["source"\]\s*=\s*"([a-z_]+)"|"source":\s*"([a-z_]+)"', src)
    return {a or b for a, b in vals}


class LinesExampleContractTests(unittest.TestCase):
    def test_example_json_is_valid(self):
        for p in sorted(EXAMPLES.glob("*.example.json")):
            with self.subTest(example=p.name):
                self.assertIsInstance(json.loads(p.read_text(encoding="utf-8")), (dict, list))

    def test_source_values_match_what_the_pipeline_writes(self):
        vocab = writer_source_vocabulary()
        self.assertTrue(vocab, "没从 merge_lines.py 解析出 source 词表，工具写法变了要同步本测试")
        self.assertEqual(vocab, {"manual", "asr", "ocr"}, "merge_lines 的 source 词表意外变化")
        data = json.loads((EXAMPLES / "lines.example.json").read_text(encoding="utf-8"))
        bad = [(i, l.get("source")) for i, l in enumerate(data.get("lines") or [])
               if l.get("source") not in vocab]
        self.assertEqual(bad, [], "样例里的 source 取值管线永不产出（大写「ASR」即此坑）")

    def test_speaker_keys_are_ids_referenced_by_lines(self):
        data = json.loads((EXAMPLES / "lines.example.json").read_text(encoding="utf-8"))
        speakers = data.get("speakers") or {}
        self.assertTrue(speakers, "样例缺 speakers 表")
        for sp in speakers.values():
            self.assertIn("name", sp)
            self.assertIn("color", sp)
        for line in data.get("lines") or []:
            self.assertIn(line.get("speaker"), speakers,
                          "台词行的 speaker 不在 speakers 键里（键法应统一为短 id）")


if __name__ == "__main__":
    unittest.main()
