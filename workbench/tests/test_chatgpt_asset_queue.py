# -*- coding: utf-8 -*-
import io
import json
import os
import tempfile
import unittest
import zipfile
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.abspath(os.path.join(HERE, "..", "tools"))
if TOOLS not in __import__("sys").path:
    __import__("sys").path.insert(0, TOOLS)
import chatgpt_import
import chatgpt_queue


class ChatGPTAssetQueueTests(unittest.TestCase):
    def test_character_state_queues_derived_target_and_uses_mother_reference(self):
        with tempfile.TemporaryDirectory() as root:
            project = os.path.join(root, "09_测试剧本")
            os.makedirs(os.path.join(project, "素材", "人物"), exist_ok=True)
            os.makedirs(os.path.join(project, "创作"), exist_ok=True)
            with open(os.path.join(project, "素材", "人物.json"), "w", encoding="utf-8") as fh:
                json.dump({"characters": [{
                    "id": "hero", "name": "主角", "sheet_prompt": "五视图设定图：脸部正面特写、45度左侧脸特写、不带头部正面全身、不带头部侧面全身、严格背面全身；黑发蓝制服",
                    "states": [{"id": "S1", "label": "战损期", "look_diff": "红色破损外套",
                                "sheet_prompt": "保持同一主角身份，穿红色破损外套"}],
                }]}, fh, ensure_ascii=False)
            Image.new("RGB", (800, 450), (10, 20, 30)).save(
                os.path.join(project, "素材", "人物", "hero.png")
            )

            job = chatgpt_queue.queue_assets(
                project, [{"ref": "@character:hero", "state_id": "S1"}]
            )[0]

            self.assertEqual(job["asset_ref"], "@character:hero#S1")
            self.assertEqual(job["asset_state_id"], "S1")
            self.assertEqual(job["output_spec"]["target_path"], "素材/人物/hero__S1.png")
            self.assertIn("红色破损外套", job["prompt_assembled"])
            self.assertEqual(job["reference_tokens"], ["@character:hero"])
            self.assertEqual(job["refs"][0]["path"], "素材/人物/hero.png")

    def test_mother_asset_does_not_reference_its_existing_output(self):
        """回归目标：重生成母图仍是母资产任务，旧图不能被误当身份依赖。"""
        with tempfile.TemporaryDirectory() as root:
            project = os.path.join(root, "09_测试剧本")
            os.makedirs(os.path.join(project, "素材", "人物"), exist_ok=True)
            os.makedirs(os.path.join(project, "创作"), exist_ok=True)
            with open(os.path.join(project, "素材", "人物.json"), "w", encoding="utf-8") as fh:
                json.dump({"characters": [{"id": "hero", "name": "主角", "sheet_prompt": "五视图设定图：脸部正面特写、45度左侧脸特写、不带头部正面全身、不带头部侧面全身、严格背面全身；黑发制服"}]}, fh, ensure_ascii=False)
            Image.new("RGB", (8, 8), (10, 20, 30)).save(os.path.join(project, "素材", "人物", "hero.png"))

            job = chatgpt_queue.queue_assets(project, ["@character:hero"])[0]

            self.assertEqual(job["refs"], [])
            self.assertEqual(job["reference_tokens"], [])
            self.assertEqual(job["missing_refs"], [])
            self.assertEqual(job["execution_state"], "ready")

    def test_child_asset_waits_for_missing_parent_image(self):
        """身份敏感子素材不能在父图缺失时静默退化成文字生图。"""
        with tempfile.TemporaryDirectory() as root:
            project = os.path.join(root, "09_测试剧本")
            os.makedirs(os.path.join(project, "素材"), exist_ok=True)
            os.makedirs(os.path.join(project, "创作"), exist_ok=True)
            with open(os.path.join(project, "素材", "人物.json"), "w", encoding="utf-8") as fh:
                json.dump({"characters": [{"id": "hero", "name": "主角", "sheet_prompt": "五视图设定图：脸部正面特写、45度左侧脸特写、不带头部正面全身、不带头部侧面全身、严格背面全身；黑发制服"}]}, fh, ensure_ascii=False)
            with open(os.path.join(project, "素材", "道具.json"), "w", encoding="utf-8") as fh:
                json.dump({"props": [{"id": "hero_sword", "name": "佩剑", "image_prompt": "青铜佩剑",
                                       "parent_ref": "@character:hero"}]}, fh, ensure_ascii=False)

            job = chatgpt_queue.queue_assets(project, ["@prop:hero_sword"])[0]

            self.assertEqual(job["refs"], [])
            self.assertEqual(job["reference_tokens"], ["@character:hero"])
            self.assertEqual(job["missing_refs"], ["@character:hero"])
            self.assertEqual(job["execution_state"], "waiting_dependencies")

    def test_asset_queue_and_import_use_stable_asset_path(self):
        with tempfile.TemporaryDirectory() as root:
            project = os.path.join(root, "09_测试剧本")
            os.makedirs(os.path.join(project, "素材"), exist_ok=True)
            os.makedirs(os.path.join(project, "创作"), exist_ok=True)
            with open(os.path.join(project, "素材", "人物.json"), "w", encoding="utf-8") as fh:
                json.dump({"characters": [{"id": "hero", "name": "主角", "sheet_prompt": "五视图设定图：脸部正面特写、45度左侧脸特写、不带头部正面全身、不带头部侧面全身、严格背面全身；黑发制服"}]}, fh, ensure_ascii=False)
            with open(os.path.join(project, "创作", "creation.json"), "w", encoding="utf-8") as fh:
                json.dump({"items": []}, fh)
            jobs = chatgpt_queue.queue_assets(project, ["@character:hero"])
            self.assertEqual(len(jobs), 1)
            job = jobs[0]
            spec = job["output_spec"]
            manifest = {"schema_version": "1.0", "generator": "chatgpt", "project": os.path.basename(project), "assets": [{
                "job_id": job["id"], "task_type": "asset_image", "version": spec["version"],
                "file": "images/" + spec["filename"]
            }]}
            package = io.BytesIO()
            with zipfile.ZipFile(package, "w") as zf:
                zf.writestr("manifest.json", json.dumps(manifest))
                zf.writestr("images/" + spec["filename"], b"image")
            result = chatgpt_import.import_package(project, package.getvalue())
            self.assertEqual(result["imported"], 1)
            with open(os.path.join(project, "素材", "人物", "hero.png"), "rb") as fh:
                self.assertEqual(fh.read(), b"image")

    def test_jpeg_import_to_asset_png_is_decodable(self):
        with tempfile.TemporaryDirectory() as root:
            project = os.path.join(root, "09_测试剧本")
            os.makedirs(os.path.join(project, "素材"), exist_ok=True)
            os.makedirs(os.path.join(project, "创作"), exist_ok=True)
            with open(os.path.join(project, "素材", "人物.json"), "w", encoding="utf-8") as fh:
                json.dump({"characters": [{"id": "hero", "name": "主角", "sheet_prompt": "黑发制服"}]}, fh, ensure_ascii=False)
            job = chatgpt_queue.queue_assets(project, ["@character:hero"])[0]
            image = io.BytesIO()
            Image.new("RGB", (2, 2), (10, 20, 30)).save(image, format="JPEG")
            manifest = {"schema_version": "1.0", "generator": "chatgpt", "project": os.path.basename(project), "assets": [{
                "job_id": job["id"], "task_type": "asset_image", "version": job["output_spec"]["version"],
                "file": "images/" + job["output_spec"]["filename"]
            }]}
            chatgpt_import.import_package(project, manifest, {manifest["assets"][0]["file"]: image.getvalue()})
            target = os.path.join(project, "素材", "人物", "hero.png")
            with Image.open(target) as imported:
                self.assertEqual(imported.format, "PNG")
                self.assertEqual(imported.size, (2, 2))


if __name__ == "__main__":
    unittest.main()
