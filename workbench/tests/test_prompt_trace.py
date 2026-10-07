# -*- coding: utf-8 -*-
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workbench" / "tools"))

import prompt_trace
import llm_openai
import creation_pipeline


class PromptTraceTests(unittest.TestCase):
    def test_records_text_and_sources_without_provider_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            projects = Path(directory)
            (projects / "示例项目").mkdir()
            with mock.patch.object(prompt_trace, "PROJECTS_DIR", projects):
                result = prompt_trace.record_prompt_run(
                    "示例项目", "outline", [{"role": "system", "content": "结构规则"},
                                            {"role": "user", "content": "一句话构想"}],
                    vendor_id="test", model="text-model", sources={"brief": "rev-1"},
                    input_revision="rev-2",
                )
            saved = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
            self.assertEqual(saved["stage"], "outline")
            self.assertEqual(saved["sources"]["brief"], "rev-1")
            self.assertEqual(saved["sources"]["input_files"], {})
            self.assertEqual(saved["messages"][1]["content"], "一句话构想")
            self.assertNotIn("api_key", saved)
            self.assertTrue(saved["fingerprint"].startswith("sha256:"))

    def test_missing_or_unsafe_project_is_not_written(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(prompt_trace, "PROJECTS_DIR", Path(directory)):
            self.assertIsNone(prompt_trace.record_prompt_run("不存在", "outline", [], vendor_id="test", model="m"))
            self.assertIsNone(prompt_trace.record_prompt_run("../逃逸", "outline", [], vendor_id="test", model="m"))
            self.assertEqual(list(Path(directory).rglob("*.json")), [])

    def test_inline_image_pixels_are_not_copied_to_text_trace(self):
        with tempfile.TemporaryDirectory() as directory:
            projects = Path(directory)
            (projects / "示例项目").mkdir()
            messages = [{"role": "user", "content": [{"type": "text", "text": "识别角色"},
                                                   {"type": "image_url", "image_url": {"url": "data:image/png;base64,YWJj"}}]}]
            with mock.patch.object(prompt_trace, "PROJECTS_DIR", projects):
                result = prompt_trace.record_prompt_run("示例项目", "vision", messages, vendor_id="test", model="m")
            raw = Path(result["path"]).read_text(encoding="utf-8")
            self.assertIn("识别角色", raw)
            self.assertNotIn("YWJj", raw)

    def test_embedded_token_is_scrubbed_from_prompt_text(self):
        with tempfile.TemporaryDirectory() as directory:
            projects = Path(directory)
            (projects / "示例项目").mkdir()
            with mock.patch.object(prompt_trace, "PROJECTS_DIR", projects):
                result = prompt_trace.record_prompt_run(
                    "示例项目", "outline", [{"role": "user", "content": "Bearer abcd1234"}],
                    vendor_id="test", model="m")
            raw = Path(result["path"]).read_text(encoding="utf-8")
            self.assertNotIn("abcd1234", raw)

    def test_client_chat_records_attempt_for_active_project(self):
        with tempfile.TemporaryDirectory() as directory:
            projects = Path(directory)
            (projects / "示例项目").mkdir()
            client = llm_openai.VendorClient.from_config({
                "id": "test", "enabled": True, "base_url": "https://example.test/v1",
                "api_key": "secret-key", "models": {"text": "text-model"},
                "endpoints": {"text": "/chat/completions"}}, check_enabled=False)
            llm_openai.set_billing_project("示例项目")
            try:
                with mock.patch.object(prompt_trace, "PROJECTS_DIR", projects), mock.patch.object(
                    client, "_post", return_value={"choices": [{"message": {"content": "完成"}}]}
                ):
                    self.assertEqual(client.chat([{"role": "user", "content": "构想"}], trace_stage="outline"), "完成")
            finally:
                llm_openai.set_billing_project("")
            traces = list((projects / "示例项目" / "创作" / "提示词调用").glob("*.json"))
            self.assertEqual(len(traces), 1)
            self.assertEqual(json.loads(traces[0].read_text(encoding="utf-8"))["stage"], "outline")

    def test_client_instance_project_records_other_pipeline_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            projects = Path(directory)
            (projects / "示例项目").mkdir()
            client = llm_openai.VendorClient.from_config({
                "id": "test", "enabled": True, "base_url": "https://example.test/v1",
                "api_key": "secret-key", "models": {"text": "text-model"},
                "endpoints": {"text": "/chat/completions"}}, check_enabled=False)
            client.billing_project = "示例项目"
            with mock.patch.object(prompt_trace, "PROJECTS_DIR", projects), mock.patch.object(
                client, "_post", return_value={"choices": [{"message": {"content": "完成"}}]}
            ):
                client.chat([{"role": "user", "content": "平面图"}], trace_stage="plan")
            traces = list((projects / "示例项目" / "创作" / "提示词调用").glob("*.json"))
            self.assertEqual(len(traces), 1)
            self.assertEqual(json.loads(traces[0].read_text(encoding="utf-8"))["stage"], "plan")

    def test_retry_forwards_stage_and_sources_to_client(self):
        class Client:
            def chat(self, messages, **kwargs):
                self.kwargs = kwargs
                return "完成"

        client = Client()
        result = creation_pipeline.chat_retry(
            client, [{"role": "user", "content": "构想"}], tries=1,
            trace_stage="outline", trace_sources={"brief": "rev-1"},
        )
        self.assertEqual(result, "完成")
        self.assertEqual(client.kwargs["trace_stage"], "outline")
        self.assertEqual(client.kwargs["trace_sources"], {"brief": "rev-1"})

    def test_retry_keeps_legacy_client_contract_without_trace_options(self):
        class Client:
            def chat(self, messages, *, kind, max_tokens, timeout, temperature, extra):
                return "完成"

        self.assertEqual(creation_pipeline.chat_retry(Client(), [], tries=1), "完成")

    def test_source_file_changes_update_input_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            projects = Path(directory)
            project = projects / "示例项目"
            script_dir = project / "剧本"
            script_dir.mkdir(parents=True)
            brief = script_dir / "brief.json"
            brief.write_text('{"episode_minutes": 3}', encoding="utf-8")
            with mock.patch.object(prompt_trace, "PROJECTS_DIR", projects):
                first = prompt_trace.record_prompt_run("示例项目", "outline", [], vendor_id="test", model="m")
                brief.write_text('{"episode_minutes": 5}', encoding="utf-8")
                second = prompt_trace.record_prompt_run("示例项目", "outline", [], vendor_id="test", model="m")
            a = json.loads(Path(first["path"]).read_text(encoding="utf-8"))
            b = json.loads(Path(second["path"]).read_text(encoding="utf-8"))
            self.assertIn("剧本/brief.json", a["sources"]["input_files"])
            self.assertNotEqual(a["input_revision"], b["input_revision"])

    def test_lists_metadata_and_reads_only_safe_run_id(self):
        with tempfile.TemporaryDirectory() as directory:
            projects = Path(directory)
            (projects / "示例项目").mkdir()
            with mock.patch.object(prompt_trace, "PROJECTS_DIR", projects):
                created = prompt_trace.record_prompt_run("示例项目", "storyboard", [{"role": "user", "content": "镜头内容"}], vendor_id="test", model="m")
                rows = prompt_trace.list_prompt_runs("示例项目")
                full = prompt_trace.read_prompt_run("示例项目", created["run_id"])
                unsafe = prompt_trace.read_prompt_run("示例项目", "../providers")
            self.assertEqual(rows[0]["stage"], "storyboard")
            self.assertNotIn("messages", rows[0])
            self.assertEqual(full["messages"][0]["content"], "镜头内容")
            self.assertIsNone(unsafe)
