# -*- coding: utf-8 -*-
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "tools")))
from comfyui_client import (
    ComfyUIClient,
    ComfyUIError,
    build_qwen_image_edit_workflow,
    build_z_image_workflow,
    render_workflow,
)


class ComfyUIWorkflowTests(unittest.TestCase):
    def test_qwen21_model_routes_both_modes_to_its_own_encoder(self):
        client = ComfyUIClient("http://example.invalid:8188")
        client.upload_image = lambda p: "uploaded.png"
        client.run_workflow = lambda graph, out_path, timeout=600: graph
        for refs in ([], ["reference.png"]):
            graph = client.generate_image("房间", "out.png", model="qwen_image_2.1_int8_convrot.safetensors", image_refs=refs)
            self.assertEqual(graph["4"]["class_type"], "TextEncodeQwenImage21")
            self.assertEqual(graph["2"]["inputs"]["clip_name"], "qwen3vl_8b_int8_convrot.safetensors")
            self.assertEqual(graph["3"]["inputs"]["vae_name"], "qwen_image_2.1_vae_bf16.safetensors")
            self.assertEqual("images.image_1" in graph["4"]["inputs"], bool(refs))

    def test_unknown_model_does_not_fall_back_to_z_image(self):
        client = ComfyUIClient("http://example.invalid:8188")
        with self.assertRaisesRegex(ComfyUIError, "没有匹配"):
            client.generate_image("房间", "out.png", model="unsupported.safetensors")

    def test_builtin_workflow_uses_installed_model_and_ratio(self):
        graph = build_z_image_workflow(
            "少女在教室下坠", "文字,水印", "z_image_turbo_int8_convrot.safetensors",
            width=1024, height=576, seed=42,
        )
        self.assertEqual(graph["1"]["inputs"]["unet_name"], "z_image_turbo_int8_convrot.safetensors")
        self.assertEqual(graph["6"]["inputs"]["width"], 1024)
        self.assertEqual(graph["6"]["inputs"]["height"], 576)
        self.assertIn("文字", graph["4"]["inputs"]["text"])

    def test_custom_workflow_rejects_references_without_slots(self):
        workflow = {"1": {"class_type": "CLIPTextEncode", "inputs": {"text": "{{prompt}}"}}}
        with self.assertRaisesRegex(ComfyUIError, "参考图"):
            render_workflow(workflow, prompt="镜头", negative="", model="m",
                            width=1024, height=576, seed=1, uploaded_refs=["a.png"])

    def test_reference_slot_must_reach_load_image(self):
        workflow = {
            "1": {"class_type": "CLIPTextEncode", "inputs": {"text": "{{ref1}}"}},
            "2": {"class_type": "SaveImage", "inputs": {"filename_prefix": "x"}},
        }
        with self.assertRaisesRegex(ComfyUIError, "LoadImage.image"):
            render_workflow(workflow, prompt="镜头", negative="", model="m",
                            width=1024, height=576, seed=1, uploaded_refs=["a.png"])

    def test_builtin_qwen_edit_workflow_uses_reference_slots_and_model(self):
        graph = build_qwen_image_edit_workflow(
            "把项圈改成蓝色", "文字,水印", "qwen_image_edit_2511_int8_convrot.safetensors",
            width=1024, height=576, seed=42,
            reference_images=["ref1.png", "ref2.png"],
        )
        self.assertEqual(graph["161"]["inputs"]["unet_name"], "qwen_image_edit_2511_int8_convrot.safetensors")
        self.assertEqual(graph["162"]["inputs"]["clip_name"], "qwen_2.5_vl_7b_fp8_scaled.safetensors")
        self.assertEqual(graph["146"]["inputs"]["vae_name"], "qwen_image_vae.safetensors")
        load_images = [node for node in graph.values() if node.get("class_type") == "LoadImage"]
        self.assertEqual([node["inputs"]["image"] for node in load_images], ["ref1.png", "ref2.png"])
        edit_nodes = [node for node in graph.values() if node.get("class_type") == "TextEncodeQwenImageEditPlus"]
        self.assertEqual(len(edit_nodes), 2)
        self.assertIn("改成蓝色", edit_nodes[1]["inputs"]["prompt"])

    def test_qwen_edit_without_workflow_uploads_refs_and_uses_builtin_graph(self):
        client = ComfyUIClient("http://example.invalid:8188")
        client.upload_image = lambda path: "uploaded-" + os.path.basename(path)
        client.run_workflow = lambda graph, out_path, timeout=600: graph
        graph = client.generate_image(
            "改图", "out.png",
            model="qwen_image_edit_2511_int8_convrot.safetensors",
            image_refs=["ref.png"],
        )
        load_images = [node for node in graph.values() if node.get("class_type") == "LoadImage"]
        self.assertEqual(load_images[0]["inputs"]["image"], "uploaded-ref.png")
        self.assertEqual(client.last_request["mode"], "qwen_builtin_edit")

    def test_vendor_routes_local_comfyui_to_native_adapter(self):
        from llm_openai import VendorClient
        vendor = VendorClient.from_config({
            "id": "local-comfyui", "enabled": True,
            "base_url": "http://192.168.0.134:8188",
            "models": {"image": "z_image_turbo_int8_convrot.safetensors"},
            "endpoints": {"image": "/prompt"},
        })
        vendor._post = lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("本测试禁止连接真实 ComfyUI"))
        with patch("comfyui_client.ComfyUIClient") as cls:
            cls.return_value.generate_image.return_value = "out.png"
            result = vendor.generate_image("画格", "out.png", extra={"ratio": "16:9"},
                                           negative_prompt="文字")
        self.assertEqual(result, "out.png")
        self.assertEqual(cls.call_args.args[0], "http://192.168.0.134:8188")
        self.assertEqual(cls.return_value.generate_image.call_args.kwargs["model"],
                         "z_image_turbo_int8_convrot.safetensors")

    def test_vendor_uses_edit_model_and_workflow_for_edit_mode(self):
        from llm_openai import VendorClient
        vendor = VendorClient.from_config({
            "id": "local-comfyui", "enabled": True,
            "base_url": "http://192.168.0.134:8188",
            "models": {"image": "z_image_turbo_int8_convrot.safetensors", "image_edit": "qwen_image_edit_2511_int8_convrot.safetensors"},
            "endpoints": {"image": "/prompt"},
            "extra": {"workflow_path": "z_ref.json", "image_edit_workflow_path": "qwen_edit.json"},
        })
        with patch("comfyui_client.ComfyUIClient") as cls:
            cls.return_value.generate_image.return_value = "out.png"
            result = vendor.generate_image("改图", "out.png", mode="edit", image_refs=["ref.png"],
                                           extra={"ratio": "16:9"})
        self.assertEqual(result, "out.png")
        kwargs = cls.return_value.generate_image.call_args.kwargs
        self.assertEqual(kwargs["model"], "qwen_image_edit_2511_int8_convrot.safetensors")
        self.assertEqual(kwargs["workflow_path"], "qwen_edit.json")

    def test_vendor_uses_builtin_edit_when_workflow_is_empty(self):
        from llm_openai import VendorClient
        vendor = VendorClient.from_config({
            "id": "local-comfyui", "enabled": True,
            "base_url": "http://192.168.0.134:8188",
            "models": {"image": "z_image_turbo_int8_convrot.safetensors",
                        "image_edit": "qwen_image_edit_2511_int8_convrot.safetensors"},
            "endpoints": {"image": "/prompt"},
            "extra": {"workflow_path": "", "image_edit_workflow_path": ""},
        })
        with patch("comfyui_client.ComfyUIClient") as cls:
            cls.return_value.generate_image.return_value = "out.png"
            result = vendor.generate_image("改图", "out.png", mode="edit", image_refs=["ref.png"],
                                           extra={"ratio": "16:9"})
        self.assertEqual(result, "out.png")
        kwargs = cls.return_value.generate_image.call_args.kwargs
        self.assertEqual(kwargs["model"], "qwen_image_edit_2511_int8_convrot.safetensors")
        self.assertEqual(kwargs["workflow_path"], "")
    def test_queue_history_and_view_recover_image(self):
        graph = {"1": {"class_type": "SaveImage", "inputs": {"filename_prefix": "test"}}}
        client = ComfyUIClient("http://example.invalid:8188")
        calls = []
        def fake_json(method, path, payload=None, timeout=15):
            calls.append((method, path, payload))
            if path == "/prompt":
                return {"prompt_id": "p123"}
            return {"p123": {"outputs": {"1": {"images": [
                {"filename": "out.png", "subfolder": "", "type": "output"}]}}}}
        client._json = fake_json
        client._bytes = lambda path, timeout=30: b"fake-png"
        with tempfile.TemporaryDirectory() as tmp:
            output = os.path.join(tmp, "result.png")
            client.run_workflow(graph, output, poll_interval=0, timeout=2)
            with open(output, "rb") as fh:
                self.assertEqual(fh.read(), b"fake-png")
        self.assertEqual(calls[0][1], "/prompt")
        self.assertIn("/history/p123", [c[1] for c in calls])

    def test_run_workflow_waits_beyond_deadline_while_queued(self):
        """排队宽限：600s 执行窗口过后仍在 ComfyUI 队列中时不报超时，继续等到出图。"""
        client = ComfyUIClient("http://example.invalid:8188")
        polls = {"history": 0}
        def fake_json(method, path, payload=None, timeout=15):
            if path == "/prompt":
                return {"prompt_id": "p9"}
            if path == "/queue":
                return {"queue_pending": [[1, "p9"]], "queue_running": []}
            polls["history"] += 1
            if polls["history"] < 5:
                return {}
            return {"p9": {"outputs": {"1": {"images": [
                {"filename": "out.png", "subfolder": "", "type": "output"}]}}}}
        client._json = fake_json
        client._bytes = lambda path, timeout=30: b"png"
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "r.png")
            client.run_workflow({}, out, poll_interval=0, timeout=0, queue_timeout=60)
            self.assertTrue(os.path.isfile(out))

    def test_run_workflow_timeout_message_distinguishes_lost_task(self):
        """任务不在队列也无历史记录：超时消息指向 ComfyUI 丢失任务而非泛泛轮询超时。"""
        client = ComfyUIClient("http://example.invalid:8188")
        def fake_json(method, path, payload=None, timeout=15):
            if path == "/prompt":
                return {"prompt_id": "p10"}
            if path == "/queue":
                return {"queue_pending": [], "queue_running": []}
            return {}
        client._json = fake_json
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ComfyUIError, "丢失"):
                client.run_workflow({}, os.path.join(tmp, "r.png"),
                                    poll_interval=0, timeout=0, queue_timeout=0)


if __name__ == "__main__":
    unittest.main()

