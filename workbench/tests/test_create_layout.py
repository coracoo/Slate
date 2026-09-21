# -*- coding: utf-8 -*-
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
VIEW = ROOT / "workbench" / "web" / "src" / "views" / "CreateView.vue"


class CreateLayoutTests(unittest.TestCase):
    def setUp(self):
        self.source = VIEW.read_text(encoding="utf-8")

    def test_create_page_uses_two_column_workspace(self):
        self.assertIn("xl:grid-cols-[minmax(0,1.7fr)_minmax(360px,0.9fr)]", self.source)
        self.assertNotIn("xl:grid-cols-[300px_minmax(0,1fr)_380px]", self.source)

    def test_storyboard_cards_are_grouped_and_show_full_frames(self):
        self.assertIn("shotGroups", self.source)
        self.assertIn("v-for=\"group in shotGroups\"", self.source)
        self.assertIn("object-contain", self.source)
        self.assertIn("aspect-video", self.source)

    def test_right_workspace_contains_gallery_below_creator(self):
        self.assertIn("<!-- 右栏：创作台 + 产出画廊 -->", self.source)
        creator = self.source.index("<!-- 右栏：创作台 + 产出画廊 -->")
        gallery = self.source.index("<!-- 产出画廊 -->")
        self.assertLess(creator, gallery)


if __name__ == "__main__":
    unittest.main()
