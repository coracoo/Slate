# -*- coding: utf-8 -*-
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workbench" / "tools"))


class CreationAssetRefTests(unittest.TestCase):
    def test_make_item_persists_prompt_json_and_asset_refs(self):
        from creation_store import make_item
        prompt_json = {"schema": "shot-prompt-v1", "asset_refs": ["@scene:room"]}
        item = make_item("i1", "image", "p", prompt_json=prompt_json, asset_refs=["@scene:room"])
        self.assertEqual(item["prompt_json"], prompt_json)
        self.assertEqual(item["asset_refs"], ["@scene:room"])


if __name__ == "__main__":
    unittest.main()

