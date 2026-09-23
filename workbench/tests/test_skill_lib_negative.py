# -*- coding: utf-8 -*-
"""E11 负面提示词分层回归（update.md 三、3.3 P1 区）。

冲突原貌：ASSET_KIND_CONSTRAINTS.character 要求"同一主体三视图"（一张图三个角度），
而旧全局负面 ASSET_BASE_NEGATIVE 禁"多人/重复角色"——图像模型会把三视图拉成单人或拒答。
修复：负面拆两层——全局基础（资产/剧情帧通用）+ 剧情帧专属（额外角色/重复角色）；
compose_asset_negative 按 kind 组装，character/scene/prop 资产图不带剧情帧禁令。
剧情帧专属层不含"多人"：shot-prompt-v1 规范明确多角色/群像是合法构图
（docs/shot-prompt-v1-资产引用.md），现有剧情帧消费方（prompt_assembler.BASE_NEG）
也只禁"额外角色/重复角色"。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workbench" / "tools"))

import skill_lib


class NegativeLayerTests(unittest.TestCase):
    """分层常量与 compose_asset_negative 的 kind 语义。"""

    def _proj(self, td):
        """带项目级生图风格（ink-wash）的临时项目，用于验证 skill 定制负面并集。"""
        p = Path(td)
        (p / "剧本").mkdir()
        (p / "剧本" / "style.json").write_text(
            json.dumps({"image": "ink-wash"}, ensure_ascii=False), encoding="utf-8")
        return str(p)

    def test_base_negative_no_longer_bans_multi_person(self):
        # 全局基础层：通用禁令保留，"多人/重复角色"已剥离
        self.assertIn("文字", skill_lib.ASSET_BASE_NEGATIVE)
        self.assertIn("畸形手指", skill_lib.ASSET_BASE_NEGATIVE)
        self.assertNotIn("多人", skill_lib.ASSET_BASE_NEGATIVE)
        self.assertNotIn("重复角色", skill_lib.ASSET_BASE_NEGATIVE)

    def test_character_sheet_negative_drops_multi_person_ban(self):
        # 角色三视图：负面不再与"同一主体三视图"正约束冲突；skill 定制负面仍并集
        with tempfile.TemporaryDirectory() as td:
            proj = self._proj(td)
            prompt, negative = skill_lib.compose_asset_image_prompt(
                proj, "某角色的外观事实", kind="character")
        self.assertIn("三视图", prompt)          # 类别硬约束（正面）保留不动
        self.assertIn("文字", negative)          # 全局基础负面保留
        self.assertIn("畸形手指", negative)
        self.assertNotIn("多人", negative)       # E11：冲突禁令移除
        self.assertNotIn("重复角色", negative)
        self.assertIn("照片写实", negative)      # ink-wash skill 定制负面并集仍在

    def test_scene_and_prop_negatives_also_drop_ban(self):
        # 场景/道具资产图同样不带剧情帧禁令
        for kind in ("scene", "prop"):
            negative = skill_lib.compose_asset_negative("nonexistent-proj", kind=kind)
            self.assertNotIn("多人", negative)
            self.assertNotIn("重复角色", negative)
            self.assertIn("文字", negative)

    def test_scene_empty_shot_positive_constraint_untouched(self):
        # scene 空镜"禁止人物"正面约束保留不动（E11 只动负面层）
        prompt, _ = skill_lib.compose_asset_image_prompt(
            "nonexistent-proj", "场景事实", kind="scene")
        self.assertIn("纯场景空镜", prompt)
        self.assertIn("不出现任何人物", prompt)

    def test_story_frame_kinds_keep_character_bans(self):
        # 剧情帧类 kind（关键帧/画格/视频首帧）：并入剧情帧专属负面
        for kind in ("frame", "keyframe", "panel", "shot", "video_frame", "story_frame"):
            negative = skill_lib.compose_asset_negative("nonexistent-proj", kind=kind)
            self.assertIn("额外角色", negative, kind)
            self.assertIn("重复角色", negative, kind)
            self.assertIn("文字", negative, kind)
            # 剧情帧仍不禁"多人"（群像合法，shot-prompt-v1 规范）
            self.assertNotIn("多人", negative, kind)

    def test_default_kind_is_asset_safe_backward_compatible(self):
        # 不传 kind 视为资产图（向后兼容旧调用）：不带剧情帧专属禁令
        negative = skill_lib.compose_asset_negative("nonexistent-proj")
        self.assertNotIn("额外角色", negative)
        self.assertNotIn("重复角色", negative)

    def test_drama_frame_consumers_policy_unchanged(self):
        # 剧情帧实际消费方（分镜提示词装配器）的负面政策不受影响：
        # 禁"额外角色/重复角色"，不禁"多人"
        import prompt_assembler
        self.assertIn("额外角色", prompt_assembler.BASE_NEG)
        self.assertIn("重复角色", prompt_assembler.BASE_NEG)
        self.assertNotIn("多人", prompt_assembler.BASE_NEG)


if __name__ == "__main__":
    unittest.main()
