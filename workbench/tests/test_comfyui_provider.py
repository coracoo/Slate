# -*- coding: utf-8 -*-
"""ComfyUI 厂商配置契约测试：使用临时 providers.json，不依赖生产配置文件。"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools")))

VENDOR = {
    "id": "local-comfyui",
    "label": "本地 ComfyUI",
    "base_url": "http://127.0.0.1:8188",
    "api_key": "",
    "enabled": True,
    "models": {
        "image": "z_image_turbo_int8_convrot.safetensors",
        "image_edit": "qwen_image_edit_2511_int8_convrot.safetensors",
    },
}


class ComfyUIProviderConfigTests(unittest.TestCase):
    def _client(self, vendor=None):
        from llm_openai import VendorClient
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        path = os.path.join(td.name, "providers.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"vendors": [vendor or VENDOR]}, fh, ensure_ascii=False)
        return VendorClient("local-comfyui", providers_json=path)

    def test_user_instance_has_separate_edit_model_slot(self):
        cli = self._client()
        self.assertNotEqual(cli.model("image"), cli.model("image_edit"))
        self.assertTrue(cli.model("image_edit").endswith(".safetensors"))

    def test_user_instance_is_enabled_in_provider_list(self):
        cli = self._client()
        self.assertEqual(cli.id, "local-comfyui")
        self.assertTrue(cli.base.startswith("http"))
        self.assertTrue(cli.model("image").endswith(".safetensors"))


if __name__ == "__main__":
    unittest.main()
