# -*- coding: utf-8 -*-
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))


class FakeClient:
    def __init__(self, prompt=None):
        self.messages = None
        self.prompt = prompt or "16:9横构图，教室近景平视。@character:zuotenglu 趴在 @prop:zuoteng_zhuoke 上猛地睁眼，视线转向左上方。背景 @scene:room；日式动漫风格。禁止文字和额外动作。"

    def chat(self, messages, **kwargs):
        self.messages = messages
        return json.dumps({"prompt_text": self.prompt}, ensure_ascii=False)


class RegenerateShotPromptTests(unittest.TestCase):
    def test_configured_text_vendor_is_passed_by_id(self):
        from unittest.mock import patch
        from regenerate_shot_prompt import regenerate_prompt

        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            (project / "分镜").mkdir()
            (project / "分镜" / "剧本_E1.json").write_text(json.dumps({
                "shots": [{"id": "S1", "content": "盖板弹开"}]
            }, ensure_ascii=False), encoding="utf-8")
            with patch("regenerate_shot_prompt.pick_vendor", return_value="text-vendor"), \
                 patch("regenerate_shot_prompt.VendorClient") as client_class, \
                 patch("regenerate_shot_prompt.chat_retry", return_value=json.dumps({
                     "prompt_text": "16:9横构图，教室全景平视，晨光照入。天花板通风口盖板刚刚弹开，课桌前的学生依旧低头读书，没有人回头；画面为单一静态瞬间，不出现文字或额外角色。"
                 }, ensure_ascii=False)):
                regenerate_prompt(project, "剧本_E1.json", "S1")
                client_class.assert_called_once_with("text-vendor")

    def test_regenerates_from_shot_facts_and_saves_new_prompt(self):
        from regenerate_shot_prompt import regenerate_prompt

        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            board_dir = project / "分镜"
            board_dir.mkdir()
            path = board_dir / "剧本_E1.json"
            original = {
                "shots": [{
                    "id": "S3", "prompt": "旧提示词：写实校园光线。", "action": "佐藤陆从课桌睡姿中睁眼",
                    "content": "佐藤陆疑惑醒来", "shot_size": "近景", "angle": "平视", "camera_move": "固定",
                    "scene_ref": "@scene:room", "actor_refs": ["@character:zuotenglu"],
                    "prop_refs": ["@prop:zuoteng_zhuoke"], "negative": ["提前抱住少女"],
                }]
            }
            path.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")
            client = FakeClient()

            result = regenerate_prompt(project, "剧本_E1.json", "S3", client=client)

            saved = json.loads(path.read_text(encoding="utf-8"))["shots"][0]
            self.assertEqual(saved["prompt"], result["prompt_text"])
            self.assertIn("猛地睁眼", saved["prompt"])
            self.assertIn("@character:zuotenglu", saved["prompt"])
            self.assertNotIn("写实校园光线", str(client.messages))
            self.assertIn("提前抱住少女", str(client.messages))
            self.assertEqual(saved["prompt_source"], "regenerated")
            self.assertEqual(saved["prompt_image"], result["prompt_text"])


    def test_video_prompt_is_separate_and_describes_timed_motion(self):
        from regenerate_shot_prompt import regenerate_prompt

        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            board_dir = project / "分镜"
            board_dir.mkdir()
            path = board_dir / "剧本_E1.json"
            path.write_text(json.dumps({"shots": [{
                "id": "S1", "action": "盖板弹开，少女随后下坠", "shot_size": "全景",
                "scene_ref": "@scene:room", "actor_refs": ["@character:girl"], "dur": 4.0,
                "prompt": "原有静态提示词",
            }]}, ensure_ascii=False), encoding="utf-8")
            client = FakeClient("4秒连续镜头：起始盖板闭合，随后 @scene:room 的天花板盖板弹开，@character:girl 从洞口下坠，镜头保持全景平视，末尾定格于人物仍在空中；无字幕和对白框。")

            result = regenerate_prompt(project, "剧本_E1.json", "S1", media_type="video", client=client)

            saved = json.loads(path.read_text(encoding="utf-8"))["shots"][0]
            self.assertEqual(saved["prompt_video"], result["prompt_text"])
            self.assertEqual(saved["prompt"], "原有静态提示词")
            self.assertIn("连续动作", client.messages[0]["content"])


    def test_reference_images_follow_visible_characters_in_static_prompt(self):
        from prompt_assembler import reference_shot_for_prompt, resolve_shot_refs

        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            (project / "素材" / "人物").mkdir(parents=True)
            (project / "素材" / "人物.json").write_text(json.dumps({
                "characters": [{"id": "classmates", "name": "全班同学"},
                               {"id": "girl", "name": "少女"}]
            }, ensure_ascii=False), encoding="utf-8")
            for name in ("classmates", "girl"):
                (project / "素材" / "人物" / (name + ".png")).write_bytes(b"x")
            shot = {"id": "S1", "content": "盖板弹开", "action": "盖板弹开；少女下坠",
                    "prompt_source": "regenerated", "actor_refs": ["@character:classmates", "@character:girl"]}
            prompt = "16:9横构图，教室全景。@character:classmates 低头读书，盖板刚弹开；少女尚未入画。"
            ref_shot = reference_shot_for_prompt(shot, prompt, "image")
            refs = resolve_shot_refs(ref_shot, str(project))
            self.assertEqual([r["path"] for r in refs], [])

    def test_video_without_rewrite_does_not_reuse_legacy_image_prompt(self):
        from prompt_assembler import assemble_shot_prompt

        with tempfile.TemporaryDirectory() as td:
            shot = {
                "id": "S2", "shot_size": "中景", "angle": "平视",
                "action": "少女从通风口向课桌坠落",
                "prompt": "输出单张关键帧，少女悬停在天花板前。",
            }
            result = assemble_shot_prompt(shot, {"dir": td, "board": {"shots": [shot]}, "media_type": "video"})
            self.assertIn("少女从通风口向课桌坠落", result["prompt_assembled"])
            self.assertNotIn("输出单张关键帧", result["prompt_assembled"])

    def test_assembler_uses_current_media_prompt_without_repeating_shot_facts(self):
        from prompt_assembler import assemble_shot_prompt

        with tempfile.TemporaryDirectory() as td:
            shot = {
                "id": "S1", "shot_size": "全景", "angle": "平视", "action": "盖板弹开",
                "scene_ref": "@scene:room", "prompt": "历史多阶段提示词",
                "prompt_image": "16:9横构图，全景平视；@scene:room 中盖板刚弹开，学生尚未反应。",
                "prompt_video": "4秒连续动作：@scene:room 中盖板先弹开，学生随后转头；镜头保持全景平视。",
                "prompt_source": "regenerated", "prompt_video_source": "regenerated",
                "actor_refs": ["@character:classmates", "@character:girl"],
            }
            image = assemble_shot_prompt(shot, {"dir": td, "board": {"shots": [shot]}, "media_type": "image"})
            video = assemble_shot_prompt(shot, {"dir": td, "board": {"shots": [shot]}, "media_type": "video"})
            self.assertIn("学生尚未反应", image["prompt_assembled"])
            self.assertNotIn("连续动作", image["prompt_assembled"])
            self.assertNotIn("历史多阶段", image["prompt_assembled"])
            self.assertNotIn("本镜动作：", image["prompt_assembled"])
            self.assertEqual([a["asset"] for a in image["prompt_json"]["actors"]], [])
            self.assertIn("4秒连续动作", video["prompt_assembled"])
            self.assertNotIn("学生尚未反应", video["prompt_assembled"])
            self.assertNotIn("输出单张关键帧", video["prompt_assembled"])


if __name__ == "__main__":
    unittest.main()
