# -*- coding: utf-8 -*-
"""项目风格局部更新保留其他维度与已保存值。"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import skill_lib


class ProjectStyleUpdateTests(unittest.TestCase):
    def test_separate_dimension_patches_preserve_each_other(self):
        with tempfile.TemporaryDirectory() as folder:
            skill_lib.patch_project_style(folder, {'image_visual': 'ink'})
            saved = skill_lib.patch_project_style(folder, {'script_pacing': 'fast'})
            self.assertEqual(saved, {'image_visual': 'ink', 'script_pacing': 'fast'})
            self.assertEqual(skill_lib.project_style(folder), saved)

    def test_failed_validation_preserves_saved_style(self):
        with tempfile.TemporaryDirectory() as folder:
            skill_lib.set_project_style(folder, {'image': 'ink'})
            with self.assertRaises(ValueError):
                skill_lib.patch_project_style(folder, {'image': []})
            self.assertEqual(skill_lib.project_style(folder), {'image': 'ink'})

    def test_default_fill_keeps_choice_saved_after_initial_read(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / '剧本/style.json'
            def concurrent_read(_project):
                path.parent.mkdir()
                path.write_text(json.dumps({'image': 'manual'}), encoding='utf-8')
                return {}
            with patch.object(skill_lib, 'project_style', side_effect=concurrent_read), \
                 patch.object(skill_lib, 'list_skills', return_value=[]):
                style = skill_lib.ensure_explicit_defaults(folder)
            self.assertEqual(style['image'], 'manual')
            self.assertEqual(json.loads(path.read_text('utf-8'))['image'], 'manual')


if __name__ == '__main__':
    unittest.main()
