# -*- coding: utf-8 -*-
import json
import os
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workbench"))
sys.path.insert(0, str(ROOT / "workbench" / "tools"))
sys.path.insert(0, str(ROOT / "previs_system" / "tools"))


class ScriptRepositoryTests(unittest.TestCase):
    def test_merge_assets_keeps_existing_and_records_episode(self):
        from script_repository import merge_assets
        existing = {"characters": [{"id": "a", "name": "甲", "locked_fields": ["name"]}]}
        incoming = {"characters": [{"id": "b", "name": "乙"}, {"id": "a", "name": "改名"}]}
        out = merge_assets(existing, incoming, "E2")
        self.assertEqual({x["id"] for x in out["characters"]}, {"a", "b"})
        self.assertEqual(out["characters"][0]["name"], "甲")
        self.assertEqual(out["characters"][1]["source_episode_ids"], ["E2"])

    def test_generated_script_joins_episode_texts(self):
        from script_repository import load_script
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "剧本"
            base.mkdir()
            (base / "分集.json").write_text(json.dumps({"mode": "generated", "episodes": [
                {"id": "E1", "title": "一", "text": "正文一"},
                {"id": "E2", "title": "二", "text": "正文二"},
            ]}, ensure_ascii=False), encoding="utf-8")
            self.assertIn("正文一", load_script(td))
            self.assertIn("正文二", load_script(td))

    def test_imported_script_wins_over_stale_episode_texts(self):
        """重新导入剧本后权威源回到 剧本.txt：曾继续回读旧分集正文，新稿对全链路不可见。"""
        from script_repository import load_script
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "剧本"
            base.mkdir()
            (base / "分集.json").write_text(json.dumps({"mode": "imported", "episodes": [
                {"id": "E1", "title": "一", "text": "上一稿的旧正文"}]}, ensure_ascii=False), encoding="utf-8")
            (base / "剧本.txt").write_text("新导入的完整剧本", encoding="utf-8")
            text = load_script(td)
            self.assertIn("新导入的完整剧本", text)
            self.assertNotIn("上一稿的旧正文", text)

    def test_legacy_project_without_mode_still_aggregates(self):
        """无 mode 的老项目（09_蜘女 实测如此）仍按分集聚合，不因新规则改变行为。"""
        from script_repository import load_script
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "剧本"
            base.mkdir()
            (base / "分集.json").write_text(json.dumps({"episodes": [
                {"id": "E1", "title": "一", "text": "分集正文一"}]}, ensure_ascii=False), encoding="utf-8")
            (base / "剧本.txt").write_text("根目录旧稿", encoding="utf-8")
            self.assertIn("分集正文一", load_script(td))

    def test_record_script_import_switches_source_and_bumps_rev(self):
        """导入路由必须同时切来源标记与 rev（否则分镜页照显「未过期」），且不动任何一集正文。"""
        from script_repository import load_script, record_script_import
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "剧本"
            base.mkdir()
            book = {"mode": "generated", "rev": 7, "episodes": [{"id": "E1", "title": "一", "text": "正文一"}]}
            (base / "分集.json").write_text(json.dumps(book, ensure_ascii=False), encoding="utf-8")
            (base / "剧本.txt").write_text("用户新导入的稿子", encoding="utf-8")
            rec = record_script_import(td)
            self.assertEqual(rec["mode"], "imported")
            self.assertEqual(rec["rev"], 8)
            saved = json.loads((base / "分集.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["episodes"][0]["text"], "正文一", "只改标记，不得改写正文")
            self.assertEqual(json.loads((base / "source.json").read_text(encoding="utf-8"))["mode"], "imported")
            self.assertIn("用户新导入的稿子", load_script(td))
            # 覆写前必须留快照（产出版本管理通用层）
            self.assertTrue((base / ".versions").is_dir() and any((base / ".versions").iterdir()))

    def test_storyboard_generation_has_room_for_structured_output(self):
        import creation_pipeline
        self.assertGreaterEqual(creation_pipeline.STORYBOARD_MAX_TOKENS, 16000)
    def test_storyboard_completion_retries_incomplete_json(self):
        import creation_pipeline
        calls = []

        def fake_chat(_cli, messages, **kwargs):
            calls.append((messages, kwargs))
            return '{"shots":[{"id":"S1"},' if len(calls) == 1 else '{"shots":[{"id":"S1","dur":2}]}'

        with mock.patch.object(creation_pipeline, "chat_retry", side_effect=fake_chat):
            parsed, _ = creation_pipeline._storyboard_completion(object(), "system", "user")
        self.assertTrue(parsed["complete"])
        self.assertEqual(len(calls), 2)
        self.assertIn("最多 20 镜", calls[1][0][1]["content"])
        self.assertEqual(calls[1][1]["max_tokens"], creation_pipeline.STORYBOARD_MAX_TOKENS)
    def test_storyboard_episode_error_explains_missing_text_before_job(self):
        import server
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "剧本"
            base.mkdir()
            (base / "分集.json").write_text(json.dumps({"episodes": [
                {"id": "E1", "text": "正文一"},
                {"id": "E2", "text": ""},
            ]}, ensure_ascii=False), encoding="utf-8")
            self.assertIsNone(server.storyboard_episode_error(td, "E1"))
            self.assertIn("E2", server.storyboard_episode_error(td, "E2"))
            self.assertIn("剧本文本", server.storyboard_episode_error(td, "E2"))


class AssetRefTests(unittest.TestCase):
    def test_normalize_project_ref_strips_project_prefix_once(self):
        from asset_refs import normalize_project_ref
        self.assertEqual(normalize_project_ref("J:/p/项目", "projects/项目/素材/人物/a.png"), "素材/人物/a.png")
        self.assertEqual(normalize_project_ref("J:/p/项目", "素材/人物/a.png"), "素材/人物/a.png")

    def test_asset_index_resolves_by_id(self):
        from asset_refs import resolve_actor_ref
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            f = p / "素材" / "人物" / "a.png"
            f.parent.mkdir(parents=True); f.write_bytes(b"x")
            (p / "素材" / "素材图.json").write_text(json.dumps({"人物": {"a": {"path": "素材/人物/a.png", "name": "甲"}}}, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(resolve_actor_ref(td, "a", "甲"), str(f.resolve()))

    def test_previz_ref_prefers_asset_index_and_returns_project_relative(self):
        from export_previz import find_ref
        with tempfile.TemporaryDirectory() as td:
            p = Path(td); f = p / "素材" / "人物" / "a.png"
            f.parent.mkdir(parents=True); f.write_bytes(b"x")
            (p / "素材" / "素材图.json").write_text(json.dumps({"人物": {"a": {"path": "素材/人物/a.png"}}}, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(find_ref(td, "旧名", "a"), "素材/人物/a.png")


class ActorCardHydrationTests(unittest.TestCase):
    def test_actor_cards_from_assets_reads_acting_fields_and_outline_hint_fallback(self):
        import actor_pipeline
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "剧本"
            base.mkdir()
            assets = Path(td) / "素材"
            assets.mkdir()
            (assets / "人物.json").write_text(json.dumps({"characters": [
                {"id": "a", "name": "甲", "acting": {
                    "personality": "克制", "goal": "保护同伴", "expression_rules": "少说多看"
                }}
            ]}, ensure_ascii=False), encoding="utf-8")
            (base / "大纲.json").write_text(json.dumps({"characters_hint": [
                "乙：嘴硬心软，遇到危险会先护住同伴"
            ]}, ensure_ascii=False), encoding="utf-8")
            board = {"actors": {"a": {"name": "甲"}, "b": {"name": "乙"}}}
            cards = actor_pipeline.actor_cards_from_assets(td, board)
            self.assertEqual(cards["a"]["personality"], "克制")
            self.assertEqual(cards["a"]["goal"], "保护同伴")
            self.assertIn("嘴硬心软", cards["b"]["personality"])
            self.assertEqual(cards["b"]["source"], "剧本/大纲.json")

    def test_prompt_compiler_includes_cards_for_actor_refs(self):
        from prompt_compiler import compile_shot
        board = {
            "actors": {"a": {"name": "甲"}},
            "acting_context": {"actor_cards": {"a": {"personality": "克制"}, "b": {"personality": "冲动"}}},
            "shots": [{"id": "S1", "prompt": "甲抬头", "actor_refs": ["@character:a"]}],
        }
        text = compile_shot(board, "S1", mode="stateful")["text"]
        self.assertIn("演员角色卡", text)
        self.assertIn("克制", text)
        self.assertNotIn("冲动", text)

class ImageSizingTests(unittest.TestCase):
    def test_storyboard_aspect_ratio_maps_to_explicit_image_size(self):
        import create_media
        self.assertEqual(create_media.image_size_for_aspect("16:9"), "2048x1152")  # Seedream images/generations 忽略 ratio：16:9 必须显式像素
        self.assertEqual(create_media.image_ratio_for_aspect("9:16"), "9:16")
        self.assertEqual(create_media.image_ratio_for_aspect(""), "16:9")

    def test_create_media_passes_size_to_image_client(self):
        import create_media
        with tempfile.TemporaryDirectory() as td:
            manifest = Path(td) / "creation.json"
            manifest.write_text(json.dumps({"items": [{"id": "x"}]}, ensure_ascii=False), encoding="utf-8")
            old_argv = list(sys.argv)
            try:
                sys.argv = ["create_media.py", "--type", "image", "--prompt", "测试",
                            "--vendor", "doubao", "--providers", str(Path(td) / "providers.json"),
                            "--size", "16:9", "--outdir", td, "--manifest", str(manifest), "--item-id", "x"]
                client = mock.Mock()
                client.model.return_value = "seedream"
                client.generate_image.return_value = str(Path(td) / "img_1.png")
                with mock.patch.object(create_media.llm_openai, "VendorClient", return_value=client):
                    create_media.main()
                self.assertEqual(client.generate_image.call_args.kwargs["extra"]["size"], "2048x1152")
                self.assertEqual(client.generate_image.call_args.kwargs["extra"].get("ratio"), "16:9")
            finally:
                sys.argv = old_argv


class VendorRoutingTests(unittest.TestCase):
    def test_vision_chat_uses_vision_endpoint(self):
        from llm_openai import VendorClient
        client = VendorClient.from_config({"id": "t", "enabled": True, "base_url": "https://example.test",
            "api_key": "", "models": {"text": "txt", "vision": "vis"},
            "endpoints": {"text": "/text", "vision": "/vision"}})
        seen = {}
        def fake_post(url, payload, timeout):
            seen["url"] = url
            return {"choices": [{"message": {"content": "ok"}}]}
        client._post = fake_post
        self.assertEqual(client.chat([{"role": "user", "content": "看图"}], kind="vision"), "ok")
        self.assertEqual(seen["url"], "https://example.test/vision")

    def test_text_chat_defaults_to_text_endpoint_even_when_vision_exists(self):
        from llm_openai import VendorClient
        client = VendorClient.from_config({"id": "t", "enabled": True, "base_url": "https://example.test",
            "api_key": "", "models": {"text": "txt", "vision": "vis"},
            "endpoints": {"text": "/text", "vision": "/vision"}})
        seen = {}
        client._post = lambda url, payload, timeout: (seen.update(url=url) or {"choices": [{"message": {"content": "ok"}}]})
        self.assertEqual(client.chat([{"role": "user", "content": "普通文本"}]), "ok")
        self.assertEqual(seen["url"], "https://example.test/text")

    def test_unsupported_vendor_rejects_image_refs_without_fallback(self):
        from llm_openai import VendorClient, VendorError
        client = VendorClient.from_config({"id": "t", "enabled": True, "base_url": "https://example.test",
            "api_key": "", "models": {"image": "img"}, "endpoints": {"image": "/image"}})
        with self.assertRaisesRegex(VendorError, "尚未适配"):
            client.generate_image("test", "unused.png", image_refs=["ref.png"])

    def test_doubao_seedance_video_accepts_png_and_url_refs(self):
        from llm_openai import VendorClient
        with tempfile.TemporaryDirectory() as td:
            ref = Path(td) / "character.png"
            ref.write_bytes(b"character-bytes")
            client = VendorClient.from_config({"id": "doubao", "enabled": True,
                "base_url": "https://ark.cn-beijing.volces.com/api/plan/v3", "api_key": "",
                "models": {"video": "doubao-seedance-2-5-260628"},
                "endpoints": {"video": "/contents/generations/tasks"}})
            seen = {}
            client._post = lambda url, payload, timeout: (seen.update(url=url, payload=payload) or {"id": "task-1"})
            client._poll_ark_task = lambda task_id, ep, interval, max_wait: "https://example.test/video.mp4"
            self.assertEqual(client.generate_video("保持角色连续", image_refs=[str(ref), "https://example.test/scene.png"]),
                             "https://example.test/video.mp4")
            content = seen["payload"]["content"]
            self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/png;base64,"))
            self.assertEqual(content[2]["image_url"]["url"], "https://example.test/scene.png")
    def test_doubao_seedream_5_accepts_local_reference_images(self):
        from llm_openai import VendorClient
        with tempfile.TemporaryDirectory() as td:
            ref1 = Path(td) / "character.png"
            ref2 = Path(td) / "scene.jpg"
            ref1.write_bytes(b"character-bytes")
            ref2.write_bytes(b"scene-bytes")
            client = VendorClient.from_config({"id": "doubao", "enabled": True,
                "base_url": "https://ark.cn-beijing.volces.com/api/plan/v3", "api_key": "",
                "models": {"image": "doubao-seedream-5.0-pro"},
                "endpoints": {"image": "/images/generations"}})
            seen = {}
            client._post = lambda url, payload, timeout: (seen.update(url=url, payload=payload) or {"data": [{}]})
            client._download_or_decode = lambda data, out: out
            out = str(Path(td) / "out.png")
            self.assertEqual(client.generate_image("保持人物和场景一致", out,
                image_refs=[str(ref1), str(ref2)]), out)
            self.assertEqual(seen["url"], "https://ark.cn-beijing.volces.com/api/plan/v3/images/generations")
            self.assertEqual(seen["payload"]["model"], "doubao-seedream-5.0-pro")
            self.assertEqual(len(seen["payload"]["image"]), 2)
            self.assertTrue(seen["payload"]["image"][0].startswith("data:image/png;base64,"))
            self.assertTrue(seen["payload"]["image"][1].startswith("data:image/jpeg;base64,"))


class DialogueContractTests(unittest.TestCase):
    def test_line_duration_must_fit_inside_shot(self):
        from validate_dialogue import validate_document
        result = validate_document({"actors": {"a": {}}, "shots": [{"id": "S1", "cam": "cu", "dur": 2,
            "lines": [{"at": 1.5, "dur": 3, "speaker": "a", "line": "你好"}]}]})
        self.assertIn("LINE_OUT_OF_SHOT", {x["code"] for x in result["errors"]})


class ActorPipelineTests(unittest.TestCase):
    def test_build_request_locks_camera_facts_and_compiles_actor_prompt(self):
        from actor_pipeline import build_request
        from prompt_compiler import compile_shot
        board = {"actors": {"a": {"name": "甲"}}, "shots": [{"id": "S1", "dur": 3,
            "cam": "cu", "pos": [0, 0, 1], "look": [0, 1, 1], "move": "固定",
            "prompt": "甲抬眼", "lines": [{"speaker": "a", "line": "你来了"}]}]}
        request = build_request(board, "S1")
        self.assertEqual(request["fixed"]["cam"], "cu")
        self.assertIn("lines", request["forbidden_output_fields"])
        baseline = compile_shot(board, "S1", mode="baseline")
        self.assertIn("甲：你来了", baseline["prompt"])
        self.assertFalse(baseline["performance_used"])

    def test_apply_result_only_accepts_ready_matching_packet(self):
        from actor_pipeline import apply_result
        board = {"actors": {"a": {}}, "shots": [{"id": "S1", "dur": 2, "cam": "cu"}]}
        result = {"status": "ready", "packet": {"shot_id": "S1", "actors": []}}
        out = apply_result(board, "S1", result)
        self.assertNotIn("performance", board["shots"][0])
        self.assertEqual(out["shots"][0]["performance"]["status"], "ready")

    def test_build_request_filters_facts_per_actor(self):
        from actor_pipeline import build_request
        board = {"actors": {"a": {}, "b": {}}, "shots": [{"id": "S1", "dur": 2, "cam": "two", "target": "b",
            "lines": [{"speaker": "a", "line": "秘密"}]}]}
        context = {"continuity_id": "main", "continuities": [{"id": "main", "initial_state": {
            "a": {"known_facts": ["public"]}, "b": {"known_facts": []}}}],
            "facts": [{"id": "public", "text": "公开"}, {"id": "secret", "text": "秘密"}], "events": []}
        request = build_request(board, "S1", context=context)
        self.assertEqual(request["allowed_fact_ids"], ["public"])
        self.assertEqual(request["allowed_fact_ids_by_actor"]["a"], ["public"])
        self.assertEqual(request["allowed_fact_ids_by_actor"]["b"], [])
        self.assertNotIn("secret", json.dumps(request["visible_context"], ensure_ascii=False))

    def test_prompt_compiler_includes_actor_cards_in_stateful_mode(self):
        from prompt_compiler import compile_shot
        board = {"actors": {"a": {"name": "甲"}}, "acting_context": {"actor_cards": {
            "a": {"personality": "克制", "goal": "隐藏伤口", "expression_rules": "少说多看"}
        }}, "shots": [{"id": "S1", "dur": 2, "speaker": "a", "cam": "cu", "prompt": "甲抬眼"}]}
        result = compile_shot(board, "S1", mode="stateful")
        self.assertIn("角色卡", result["prompt"])
        self.assertIn("隐藏伤口", result["prompt"])
    def test_prompt_compiler_ignores_stale_performance(self):
        from prompt_compiler import compile_shot
        board = {"actors": {"a": {"name": "甲"}}, "shots": [{"id": "S1", "dur": 2, "cam": "cu",
            "prompt": "甲抬眼", "performance": {"status": "ready", "source_hash": "old",
                "packet": {"actors": [{"actor_id": "a", "beats": [{"at": 0, "duration": 1, "intent": "旧表演"}]}]}}}]}
        result = compile_shot(board, "S1", mode="stateful")
        self.assertFalse(result["performance_used"])
        self.assertNotIn("旧表演", result["prompt"])


class ArtifactTests(unittest.TestCase):
    def test_artifact_hash_changes_when_shot_position_changes(self):
        from artifact_provenance import artifact_hash
        board = {"shots": [{"id": "S1", "pos": [0, 1, 2], "look": [0, 0, 1], "prompt": "x"}]}
        a = artifact_hash(board, "diagram", "1")
        board["shots"][0]["pos"][0] = 2
        self.assertNotEqual(a, artifact_hash(board, "diagram", "1"))
        board["acting_context"] = {"actor_cards": {"a": {"goal": "新目标"}}}
        self.assertNotEqual(a, artifact_hash(board, "diagram", "1"))
        prompt_hash = artifact_hash(board, "prompt", "1")
        board["shots"][0]["performance"] = {"status": "ready", "packet": {"actors": []}}
        self.assertEqual(prompt_hash, artifact_hash(board, "prompt", "1"))


class LlmResultTests(unittest.TestCase):
    def test_truncated_structured_output_is_incomplete(self):
        from llm_result import parse_structured
        result = parse_structured('{"shots":[{"id":"S1"},')
        self.assertFalse(result["complete"])
        self.assertEqual(result["data"]["shots"][0]["id"], "S1")

    def test_creation_pipeline_parser_rejects_truncated_object(self):
        import creation_pipeline
        with self.assertRaises(ValueError):
            creation_pipeline.parse_json('{"shots":[{"id":"S1"},')


if __name__ == "__main__":
    unittest.main()












