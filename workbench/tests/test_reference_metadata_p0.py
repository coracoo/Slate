# -*- coding: utf-8 -*-
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workbench" / "tools"))


class ReferenceMetadataP0Tests(unittest.TestCase):
    def test_create_media_compiles_reference_role_and_time_into_image_request(self):
        import create_media

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            manifest = root / "creation.json"
            manifest.write_text(json.dumps({"items": [{"id": "i1", "status": "running"}]}), encoding="utf-8")
            ref = root / "ref.png"
            ref.write_bytes(b"x")
            seen = {}

            class FakeClient:
                def __init__(self, *args, **kwargs):
                    pass

                def model(self, kind):
                    return "fake"

                def generate_image(self, prompt, out, **kwargs):
                    seen["prompt"] = prompt
                    Path(out).write_bytes(b"png")

            old = sys.argv[:]
            try:
                sys.argv = [
                    "create_media.py", "--type", "image", "--prompt", "画面",
                    "--vendor", "fake", "--refs", str(ref), "--ref-purpose", "剧情参考帧",
                    "--ref-role", "process", "--ref-time", "1.4",
                    "--outdir", str(root / "out"), "--manifest", str(manifest), "--item-id", "i1",
                ]
                with mock.patch.object(create_media.llm_openai, "VendorClient", FakeClient):
                    create_media.main()
            finally:
                sys.argv = old
            self.assertIn("剧情参考帧（process）", seen["prompt"])
            self.assertIn("目标时间 1.4s", seen["prompt"])


if __name__ == "__main__":
    unittest.main()
