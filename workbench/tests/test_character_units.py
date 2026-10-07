# -*- coding: utf-8 -*-
"""全剧最小单元的外观落库与投影；全程禁止网络。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import story_units


class CharacterUnitsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        self.path = self.project / "素材" / "人物.json"
        self.path.parent.mkdir()
        self.path.write_text(json.dumps({"characters": [{"id": "a", "name": "甲"}]}), encoding="utf-8")
        for method in ("socket.create_connection", "socket.socket.connect"):
            patcher = mock.patch(method, side_effect=AssertionError("测试禁止网络"))
            patcher.start()
            self.addCleanup(patcher.stop)

    def row(self):
        return json.loads(self.path.read_text(encoding="utf-8"))["characters"][0]

    def apply(self, appearance, **kwargs):
        return story_units.apply_entities(self.project, {"characters": [{"ref": "@character:a", "appearance": appearance}]}, **kwargs)

    def test_u2_preserves_precise_appearance_and_partial_metadata(self):
        self.apply({"height": "172.50厘米", "face": "左眉略高", "sources": {"height": "source"},
                    "proposals": {"headwear": "木簪"}})
        self.apply({"hair": "不齐的碎发", "sources": {"hair": "source"}})
        row = self.row()
        self.assertIn("appearance", row)
        self.assertEqual(row["appearance"]["height"], "172.50厘米")
        self.assertEqual(row["appearance"]["sources"], {"height": "source", "hair": "source"})
        self.assertEqual(row["appearance"]["proposals"], {"headwear": "木簪"})

    def test_fill_only_fills_missing_appearance_without_replacing_existing_values(self):
        self.apply({"face": "左眉略高", "sources": {"face": "source"}})
        self.apply({"face": "模板脸", "hair": "发际线略后移", "sources": {"face": "proposal", "hair": "source"}}, fill_only=True)
        row = self.row()
        self.assertIn("appearance", row)
        self.assertEqual(row["appearance"]["face"], "左眉略高")
        self.assertEqual(row["appearance"]["hair"], "发际线略后移")
        self.assertEqual(row["appearance"]["sources"]["face"], "source")

    def test_manual_appearance_lock_survives_u2_and_projection(self):
        import character_profiles
        import script_repository
        character_profiles.save(self.project, "a", {"appearance": {"face": "人工采用的眉骨"}},
                                expected_revision=character_profiles.state(self.project)["revision"])
        self.apply({"face": "自动覆盖"})
        self.assertEqual(self.row()["appearance"]["face"], "人工采用的眉骨")
        result = script_repository.merge_assets({"characters": [self.row()]},
                                                {"characters": [{"id": "a", "appearance": {"face": "自动覆盖"}}]})
        self.assertEqual(result["characters"][0]["appearance"]["face"], "人工采用的眉骨")

    def test_projection_merge_retains_source_proposal_and_old_look(self):
        import script_repository
        original = {"id": "a", "name": "甲", "appearance": {"look": "旧综合外貌", "height": "172.50厘米",
                    "face": "人工采用的眉骨", "sources": {"height": "source", "face": "authored"},
                    "proposals": {"headwear": "木簪"}}}
        result = script_repository.merge_assets({"characters": [original]}, {"characters": [{"id": "a", "appearance": {
            "hair": "少量卷发", "face": "模型新脸", "height": "180厘米", "sources": {"hair": "source", "height": "proposal"}}}]})
        appearance = result["characters"][0]["appearance"]
        self.assertEqual(appearance.get("look"), "旧综合外貌")
        self.assertEqual(appearance["face"], "人工采用的眉骨")
        self.assertEqual(appearance["height"], "172.50厘米")
        self.assertEqual(appearance["proposals"]["headwear"], "木簪")
        self.assertEqual(appearance["proposals"]["height"], "180厘米")

    def test_u2_does_not_overwrite_a_manual_save_during_generation(self):
        import character_profiles
        original = story_units.merge_generated_appearance
        called = False
        def during_merge(*args, **kwargs):
            nonlocal called
            result = original(*args, **kwargs)
            if not called:
                called = True
                character_profiles.save(self.project, "a", {"appearance": {"face": "生成途中人工确认的脸"}},
                                        expected_revision=character_profiles.state(self.project)["revision"])
            return result
        with mock.patch.object(story_units, "merge_generated_appearance", side_effect=during_merge):
            self.apply({"face": "稍后返回的旧生成结果"})
        self.assertEqual(self.row()["appearance"]["face"], "生成途中人工确认的脸")

    def test_authority_carries_appearance_with_guidance_without_changing_story_anchor(self):
        (self.project / "剧本").mkdir()
        (self.project / "剧本" / "大纲.json").write_text('{"premise":"守住故乡"}', encoding="utf-8")
        self.apply({"face": "左眉略高", "sources": {"face": "source"}, "proposals": {"headwear": "木簪"}})
        self.assertTrue(story_units.anchor(self.project, force=True)["ok"])
        fingerprint = story_units.anchor_fingerprint(self.project)
        note = story_units.authority_note(self.project, "人物")
        self.assertIn("左眉略高", note)
        self.assertIn("appearance.proposals", note)
        self.assertIn("尚未采用", note)
        self.apply({"hair": "自然碎发"})
        self.assertEqual(fingerprint, story_units.anchor_fingerprint(self.project))
        self.assertTrue(story_units.is_anchored(self.project))


if __name__ == "__main__":
    unittest.main()
