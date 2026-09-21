# -*- coding: utf-8 -*-
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workbench" / "tools"))


class AssetRegistryTests(unittest.TestCase):
    def make_project(self):
        td = tempfile.TemporaryDirectory()
        root = Path(td.name)
        (root / "素材" / "人物").mkdir(parents=True)
        (root / "素材" / "场景").mkdir(parents=True)
        (root / "素材" / "道具").mkdir(parents=True)
        (root / "素材" / "人物" / "hero.png").write_bytes(b"hero")
        (root / "素材" / "场景" / "room.png").write_bytes(b"room")
        (root / "素材" / "道具" / "prop.png").write_bytes(b"prop")
        (root / "素材" / "人物.json").write_text(json.dumps({"characters": [{
            "id": "hero", "name": "白咲蛛绪", "aliases": ["蛛绪"], "usage": "主角",
            "sheet_prompt": "不应被展开的外观长描述"
        }]}, ensure_ascii=False), encoding="utf-8")
        (root / "素材" / "场景.json").write_text(json.dumps({"scenes": [{
            "id": "room", "name": "二年A班教室", "usage": "主要场景", "image_prompt": "不应被展开的场景长描述"
        }]}, ensure_ascii=False), encoding="utf-8")
        (root / "素材" / "道具.json").write_text(json.dumps({"props": [{
            "id": "prop", "name": "银白步足", "usage": "叙事道具", "image_prompt": "不应被展开的道具长描述"
        }]}, ensure_ascii=False), encoding="utf-8")
        (root / "素材" / "素材图.json").write_text(json.dumps({
            "人物": {"hero": {"path": "素材/人物/hero.png"}},
            "场景": {"room": {"path": "素材/场景/room.png"}},
            "道具": {"prop": {"path": "素材/道具/prop.png"}}
        }, ensure_ascii=False), encoding="utf-8")
        return td, root

    def test_resolves_typed_refs_and_aliases_without_prompt_fields(self):
        from asset_registry import AssetRegistry
        td, root = self.make_project()
        self.addCleanup(td.cleanup)
        registry = AssetRegistry(root)
        hero = registry.resolve("@character:蛛绪")
        self.assertEqual(hero["ref"], "@character:hero")
        self.assertEqual(hero["path"], "素材/人物/hero.png")
        self.assertNotIn("sheet_prompt", hero)
        self.assertEqual(registry.resolve("@scene:room")["ref"], "@scene:room")
        self.assertEqual(registry.resolve("@prop:prop")["usage"], "叙事道具")

    def test_lists_safe_index_only_records_and_rejects_unknown_refs(self):
        from asset_registry import AssetRegistry, AssetReferenceError, resolve_asset_refs
        td, root = self.make_project()
        self.addCleanup(td.cleanup)
        registry = AssetRegistry(root)
        rows = registry.list()
        self.assertEqual({row["ref"] for row in rows}, {"@character:hero", "@scene:room", "@prop:prop"})
        allowed = {"ref", "kind", "id", "name", "path", "usage", "aliases", "prompt", "asset_revision", "children_refs",
                   "parent_ref", "relation", "derived_from", "related_refs", "source_episode_ids", "states"}
        self.assertTrue(all(set(row) <= allowed for row in rows))
        self.assertEqual(registry.resolve("@character:hero")["asset_revision"], 1)
        with self.assertRaises(AssetReferenceError):
            registry.resolve("@character:missing")
        refs = resolve_asset_refs(root, ["@character:hero", "@scene:room", "@character:hero"])
        self.assertEqual([row["ref"] for row in refs], ["@character:hero", "@scene:room"])

    def test_character_states_exposed_with_generated_image_paths(self):
        """人物 states（派生状态）随注册表暴露；已生成的状态图解析出项目相对路径，未生成留空。"""
        from asset_registry import AssetRegistry
        td, root = self.make_project()
        self.addCleanup(td.cleanup)
        data = json.loads((root / "素材" / "人物.json").read_text(encoding="utf-8"))
        data["characters"][0]["states"] = [
            {"id": "hero_S1", "label": "日常态", "look_diff": "校服", "camp": "友", "episodes": ["E1"]},
            {"id": "hero_S2", "label": "战斗态"},
        ]
        (root / "素材" / "人物.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        (root / "素材" / "人物" / "hero__hero_S1.png").write_bytes(b"s1")
        hero = AssetRegistry(root).resolve("@character:hero")
        states = {st["id"]: st for st in hero["states"]}
        self.assertEqual(states["hero_S1"]["path"], "素材/人物/hero__hero_S1.png")
        self.assertEqual(states["hero_S1"]["label"], "日常态")
        self.assertEqual(states["hero_S1"]["episodes"], ["E1"])
        self.assertEqual(states["hero_S2"]["path"], "")
        self.assertNotIn("states", AssetRegistry(root).resolve("@scene:room"))


if __name__ == "__main__":
    unittest.main()
