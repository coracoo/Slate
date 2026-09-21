# -*- coding: utf-8 -*-
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workbench" / "tools"))


class ReferenceLimitTests(unittest.TestCase):
    def test_expanded_cloud_models_allow_ten(self):
        from reference_limits import reference_limit
        self.assertEqual(reference_limit("doubao", "doubao-seedream-5.0-pro", "image"), 10)
        self.assertEqual(reference_limit("doubao", "doubao-seedance-1.5-pro", "video"), 10)
        self.assertEqual(reference_limit("openai-compat", "gpt-image-2", "image"), 10)
        self.assertEqual(reference_limit("gpt2.5", "custom", "image"), 10)

    def test_explicit_config_and_comfyui(self):
        from reference_limits import reference_limit
        self.assertEqual(reference_limit("openai-compat", "custom", "image", {"reference_limit": 12}), 12)
        self.assertEqual(reference_limit("local-comfyui", "anything", "image"), 3)


if __name__ == "__main__":
    unittest.main()
