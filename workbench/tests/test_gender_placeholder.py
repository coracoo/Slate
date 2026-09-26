# -*- coding: utf-8 -*-
"""剔"性别不明"这类空信息词时不得把句子剔残（09_仙 沈砚 母图曾经只剩"的成年人"）。"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'previs_system' / 'tools'))
sys.path.insert(0, str(ROOT / 'workbench' / 'tools'))

from gen_asset_images import drop_gender_placeholder   # noqa: E402


class GenderPlaceholderTests(unittest.TestCase):
    def test_unknown_gender_leaves_no_dangling_de(self):
        self.assertEqual(drop_gender_placeholder('性别不明的成年人，古代战场装束'),
                         '成年人，古代战场装束')
        self.assertEqual(drop_gender_placeholder('性别不明，成年战场之人，持断刀'),
                         '成年战场之人，持断刀')

    def test_known_gender_reads_as_a_sentence(self):
        self.assertEqual(drop_gender_placeholder('性别不明的成年人', '男性'), '男性的成年人')
        self.assertEqual(drop_gender_placeholder('性别不明，成年战场之人', '男性'), '男性，成年战场之人')

    def test_real_archive_prompt_is_repairable(self):
        """09_仙 沈砚 母图现状（已被旧写法剔残）：重跑自愈不得再产生新残缺，也不吞别的信息。"""
        broken = '正面、侧面、背面三视图，纯白背景；的成年人，古代战场装束，手持断刀'
        self.assertEqual(drop_gender_placeholder(broken), broken, '已经剔过的文本要原样通过（幂等）')
        source = '正面、侧面、背面三视图，纯白背景；性别不明的成年人，古代战场装束，手持断刀'
        once = drop_gender_placeholder(source)
        self.assertEqual(once, drop_gender_placeholder(once))
        self.assertIn('成年人，古代战场装束', once)
        self.assertNotIn('背景；的', once, '主语前不能只剩一个"的"')

    def test_no_marker_text_is_untouched(self):
        text = '女性成年人，古代军旅甲胄，甲片冰冷'
        self.assertEqual(drop_gender_placeholder(text), text)


if __name__ == '__main__':
    unittest.main()
