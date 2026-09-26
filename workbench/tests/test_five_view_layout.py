# -*- coding: utf-8 -*-
"""人物设定图"五视图"构图的单一权威与幂等回归。

两件真事：
1) 同一段构图文字在 ①类别硬约束(skill_lib) ②② 提炼提示词(prompt_modules) ③单资产重生成
   (creation_pipeline) 三处各抄一份 → 加守卫时只加在了其中一处，另两处会分叉；
2) 09_仙 的 zhijing 被"重生成提示词"跑第二次时把模板叠了两遍（"五视图设定图"×2、
   "纯白背景"×3），并留下"纯白背景。，纯白背景"这种接缝残渣——旧写法只 replace 关键词、
   既不幂等也不清标点。同一条提示词里还同时要求"脸部特写"和"不带头部"，
   而"不带头部"这种中文否定句图像模型基本不听（且会被读成"不带头盔"）。
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'previs_system' / 'tools'))
sys.path.insert(0, str(ROOT / 'workbench' / 'tools'))

import skill_lib                                    # noqa: E402
from creation_pipeline import strip_layout          # noqa: E402

# 真实档案里的旧文案（来自 09_仙/素材/.versions 的迁移前快照）
LEGACY = ("正面、侧面、背面三视图，纯白背景；女性成年人，古代军旅甲胄，甲片冰冷，手持长枪，枪缨清晰；"
          "配色为冷色金属甲与暗红枪缨，材质为铁甲、皮革与木质枪杆，战火时代质感。")
LEGACY_2 = "正面、侧面、背面三视图。成年女性，身形挺拔，穿古代军师装束，衣料层次分明。"
# 被叠了两遍的现状（09_仙 zhijing 档案里就是这样的）
DOUBLED = skill_lib.SHEET_VIEW_LAYOUT_ZH + skill_lib.SHEET_VIEW_LAYOUT_ZH + "。，纯白背景；女性成年人，古代军旅甲胄。"


class SingleSource(unittest.TestCase):
    def test_panel_text_is_defined_in_exactly_one_place(self):
        """构图文字只许有一处定义；其他地方引用常量，否则改一处会留下三份不同的说法。"""
        marker = '①脸部正面'
        hits = []
        for path in (ROOT / 'workbench').rglob('*.py'):
            if any(part in path.parts for part in ('exports', 'node_modules', '__pycache__', 'tests')):
                continue
            if marker in path.read_text(encoding='utf-8', errors='replace'):
                hits.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(hits, ['workbench/tools/skill_lib.py'], f'构图文字被复制到了：{hits}')

    def test_extract_spec_and_migration_use_the_same_words(self):
        import prompt_modules as PM
        _sys, user = PM.characters_prompt("剧本", known=None)
        text = _sys + user
        self.assertIn(skill_lib.SHEET_VIEW_PANELS_ZH, text, '② 提炼提示词必须用权威五段，否则 LLM 会写出另一种说法')
        self.assertIn('画面自领口往下', text)


class LayoutWording(unittest.TestCase):
    def test_no_chinese_negation_for_the_headless_panels(self):
        self.assertNotIn('不带头部', skill_lib.SHEET_VIEW_LAYOUT_ZH,
                         '"不带头部"会被模型读成"不带头盔"，必须换成正面表述')
        self.assertIn('画面自领口往下', skill_lib.SHEET_VIEW_LAYOUT_ZH)
        self.assertIn('含头部背面', skill_lib.SHEET_VIEW_LAYOUT_ZH, '⑤ 不写清就会被连带画成无头')

    def test_hard_constraint_carries_an_english_twin(self):
        """图像模型对纯中文指令服从度低（本仓老坑），构图约束必须带英文对照。"""
        text = skill_lib.ASSET_KIND_CONSTRAINTS['character']
        self.assertIn(skill_lib.SHEET_VIEW_PANELS_ZH, text)
        for needle in ('five panels', 'headless torso front view cropped at the collar',
                       'nothing above the neck in this panel'):
            self.assertIn(needle, text, f'英文对照缺了「{needle}」')


class StripLayoutIsIdempotent(unittest.TestCase):
    def test_legacy_prefixes_are_removed_without_leaving_scars(self):
        for legacy in (LEGACY, LEGACY_2):
            fact = strip_layout(legacy)
            self.assertNotIn('三视图', fact)
            self.assertTrue(fact.startswith('女性成年人') or fact.startswith('成年女性'), fact)
            self.assertNotIn('。，', fact)
            self.assertNotIn('，。', fact)

    def test_already_migrated_text_strips_back_to_the_same_facts(self):
        migrated = skill_lib.SHEET_VIEW_LAYOUT_ZH + "女性成年人，古代军旅甲胄。"
        self.assertEqual(strip_layout(migrated), '女性成年人，古代军旅甲胄。')

    def test_double_written_archive_is_cleaned(self):
        """zhijing 那条现状：模板叠两遍 + 残渣逗号。剥完只剩事实，且再迁移一次只有一块模板。"""
        fact = strip_layout(DOUBLED)
        self.assertNotIn('五视图设定图', fact)
        self.assertNotIn('。，', fact)
        once = skill_lib.SHEET_VIEW_LAYOUT_ZH + fact
        self.assertEqual(once.count(skill_lib.SHEET_VIEW_TITLE_ZH), 1)
        self.assertEqual(strip_layout(once), fact, '重复剥离必须稳定（幂等）')

    def test_stripping_twice_changes_nothing(self):
        for text in (LEGACY, DOUBLED, skill_lib.SHEET_VIEW_LAYOUT_ZH + '甲。'):
            self.assertEqual(strip_layout(strip_layout(text)), strip_layout(text))


if __name__ == '__main__':
    unittest.main()
