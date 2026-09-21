# -*- coding: utf-8 -*-
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
WORKBENCH = os.path.abspath(os.path.join(HERE, ".."))
if WORKBENCH not in sys.path:
    sys.path.insert(0, WORKBENCH)
TOOLS = os.path.join(WORKBENCH, "tools")
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import chatgpt_import
import chatgpt_queue


class ChatGPTImportTests(unittest.TestCase):
    def test_asset_import_updates_material_index(self):
        with open(os.path.join(self.project, "素材", "人物.json"), "w", encoding="utf-8") as fh:
            json.dump({"characters": [{"id": "hero", "name": "主角", "sheet_prompt": "黑发制服"}]}, fh, ensure_ascii=False)
        job = chatgpt_queue.queue_assets(self.project, ["@character:hero"])[0]
        image = io.BytesIO()
        Image.new("RGB", (32, 18), (1, 2, 3)).save(image, format="PNG")
        manifest = {"schema_version": "1.0", "generator": "chatgpt", "project": os.path.basename(self.project), "assets": [{
            "job_id": job["id"], "task_type": "asset_image", "version": job["output_spec"]["version"],
            "file": "images/" + job["output_spec"]["filename"]
        }]}

        chatgpt_import.import_package(self.project, manifest, {manifest["assets"][0]["file"]: image.getvalue()})

        index = json.loads(Path(self.project, "素材", "素材图.json").read_text(encoding="utf-8"))
        self.assertEqual(index["人物"]["hero"]["path"], "素材/人物/hero.png")
        self.assertEqual(index["人物"]["hero"]["name"], "主角")

    def test_state_asset_import_updates_nested_state_without_replacing_mother(self):
        with open(os.path.join(self.project, "素材", "人物.json"), "w", encoding="utf-8") as fh:
            json.dump({"characters": [{
                "id": "hero", "name": "主角", "sheet_prompt": "黑发制服",
                "states": [{"id": "S1", "label": "战损期", "sheet_prompt": "红色破损外套"}],
            }]}, fh, ensure_ascii=False)
        mother = Path(self.project, "素材", "人物", "hero.png")
        mother.parent.mkdir(parents=True, exist_ok=True)
        mother.write_bytes(b"mother")
        job = chatgpt_queue.queue_assets(
            self.project, [{"ref": "@character:hero", "state_id": "S1"}]
        )[0]
        image = io.BytesIO()
        Image.new("RGB", (32, 18), (4, 5, 6)).save(image, format="PNG")
        manifest = {"schema_version": "1.0", "generator": "chatgpt", "project": os.path.basename(self.project), "assets": [{
            "job_id": job["id"], "task_type": "asset_image", "version": job["output_spec"]["version"],
            "file": "images/" + job["output_spec"]["filename"],
        }]}

        chatgpt_import.import_package(self.project, manifest, {manifest["assets"][0]["file"]: image.getvalue()})

        self.assertEqual(mother.read_bytes(), b"mother")
        index = json.loads(Path(self.project, "素材", "素材图.json").read_text(encoding="utf-8"))
        self.assertEqual(index["人物"]["hero"]["states"]["S1"]["path"], "素材/人物/hero__S1.png")
        self.assertEqual(index["人物"]["hero"].get("path"), "素材/人物/hero.png")
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = self.tmp.name
        os.makedirs(os.path.join(self.project, "分镜"), exist_ok=True)
        os.makedirs(os.path.join(self.project, "创作"), exist_ok=True)
        os.makedirs(os.path.join(self.project, "素材"), exist_ok=True)
        with open(os.path.join(self.project, "分镜", "剧本_E1.json"), "w", encoding="utf-8") as fh:
            json.dump({"shots": [{"id": "S1", "prompt": "测试"}]}, fh)
        self.job = chatgpt_queue.queue_shots(self.project, "剧本_E1.json", ["S1"])[0]

    def tearDown(self):
        self.tmp.cleanup()

    def _manifest(self, file_name="images/result.png"):
        return {
            "schema_version": "1.0", "generator": "chatgpt",
            "project": os.path.basename(self.project), "board": "剧本_E1.json",
            "assets": [{"job_id": self.job["id"], "shot_id": "S1",
                        "task_type": "reference_image", "version": 1, "file": file_name}]
        }

    def test_import_manifest_and_image(self):
        result = chatgpt_import.import_package(
            self.project, self._manifest(), {"images/result.png": b"PNG DATA"}
        )
        self.assertEqual(result["imported"], 1)
        with open(os.path.join(self.project, "创作", self.job["id"], "result.png"), "rb") as fh:
            self.assertEqual(fh.read(), b"PNG DATA")
        data = json.load(open(os.path.join(self.project, "创作", "creation.json"), encoding="utf-8"))
        item = next(x for x in data["items"] if x["id"] == self.job["id"])
        self.assertEqual(item["status"], "done")
        self.assertEqual(len(item["outputs"]), 1)

    def test_zip_slip_is_rejected_without_manifest_change(self):
        manifest = self._manifest("../escape.png")
        with self.assertRaises(chatgpt_import.ImportError):
            chatgpt_import.import_package(self.project, manifest, {"../escape.png": b"bad"})
        data = json.load(open(os.path.join(self.project, "创作", "creation.json"), encoding="utf-8"))
        self.assertEqual(data["items"][0]["status"], "queued")

    def test_zip_package_is_supported(self):
        manifest = self._manifest()
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, "w") as zf:
            zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
            zf.writestr("images/result.png", b"PNG DATA")
        result = chatgpt_import.import_package(self.project, raw.getvalue())
        self.assertEqual(result["imported"], 1)

    def test_prepared_transaction_is_recovered_before_next_import(self):
        target = Path(self.project, "创作", self.job["id"], "result.png")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"partial new image")
        creation_path = Path(self.project, "创作", "creation.json")
        original_creation = creation_path.read_bytes()
        broken = json.loads(original_creation.decode("utf-8"))
        broken["items"][0]["status"] = "done"
        creation_path.write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")
        transaction = Path(self.project, "创作", ".chatgpt_import_transactions", "tx-crash")
        backup_dir = transaction / "backups"
        backup_dir.mkdir(parents=True)
        (backup_dir / "creation.bin").write_bytes(original_creation)
        (transaction / "journal.json").write_text(json.dumps({
            "state": "prepared",
            "entries": [
                {"path": f"创作/{self.job['id']}/result.png", "existed": False, "backup": ""},
                {"path": "创作/creation.json", "existed": True,
                 "backup": "backups/creation.bin"},
            ],
        }, ensure_ascii=False), encoding="utf-8")

        chatgpt_import.import_package(
            self.project, self._manifest(), {"images/result.png": b"PNG DATA"},
            transaction_id="tx-next",
        )

        self.assertEqual(target.read_bytes(), b"PNG DATA")
        recovered = json.loads((transaction / "journal.json").read_text(encoding="utf-8"))
        self.assertEqual(recovered["state"], "recovered")

    def test_imports_for_same_project_are_serialized(self):
        manifest = self._manifest()
        files = {"images/result.png": b"PNG DATA"}
        real_begin = chatgpt_import._begin_transaction
        counter_lock = threading.Lock()
        active = 0
        maximum = 0

        def slow_begin(*args, **kwargs):
            nonlocal active, maximum
            with counter_lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.1)
            try:
                return real_begin(*args, **kwargs)
            finally:
                with counter_lock:
                    active -= 1

        with mock.patch.object(chatgpt_import, "_begin_transaction", side_effect=slow_begin):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [
                    pool.submit(chatgpt_import.import_package, self.project, manifest, files,
                                transaction_id=f"tx-{index}")
                    for index in range(2)
                ]
                [future.result() for future in futures]

        self.assertEqual(maximum, 1)


if __name__ == "__main__":
    unittest.main()
