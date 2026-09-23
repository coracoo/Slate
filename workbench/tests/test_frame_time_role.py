# -*- coding: utf-8 -*-
"""关键帧时间角色分类（update.md E07）回归测试：全部离线，不调模型、不提交任务。

覆盖四块：
- actor_runtime.perform 生成节拍时按顺序默认打 time_role（首拍 start / 末拍 end / 中间 beat，单拍 end）；
- prompt_compiler 图片模式优先取 time_role=end 的节拍，未标角色时维持取末拍；
- production_requests.compile_request 首帧槽位只准 start、尾帧槽位只准 end，
  end 图禁止当首帧；旧数据未分类兜底放行并打警告日志；
- reference 模式不受时间角色约束。
"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT / "workbench" / "tools", ROOT / "previs_system" / "tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


# ---------- actor_runtime：生成时按顺序默认打标 ----------

def _request():
    return {
        "shot_id": "S1",
        "dur": 6,
        "actor_ids": ["a", "b"],
        "allowed_fact_ids": [],
        "actors": [{"actor_id": "a", "known_facts": []}, {"actor_id": "b", "known_facts": []}],
    }


def _packet(beats_a, beats_b=None):
    actors = [{"actor_id": "a", "beats": beats_a}]
    if beats_b is not None:
        actors.append({"actor_id": "b", "beats": beats_b})
    return {"shot_id": "S1", "actors": actors}


def _perform(packet):
    from actor_runtime import perform
    return perform(
        _request(),
        lambda messages: {"content": json.dumps(packet, ensure_ascii=False), "finish_reason": "stop"},
    )


class BeatTimeRoleLabelTests(unittest.TestCase):
    def test_three_beats_labelled_start_beat_end(self):
        result = _perform(_packet([
            {"at": 0, "duration": 1, "posture": "站立"},
            {"at": 2, "duration": 1, "gesture": "抬手"},
            {"at": 4, "duration": 1, "posture": "坐下"},
        ]))
        self.assertEqual(result["status"], "ready")
        roles = [b.get("time_role") for b in result["packet"]["actors"][0]["beats"]]
        self.assertEqual(roles, ["start", "beat", "end"])

    def test_two_beats_labelled_start_end(self):
        result = _perform(_packet([
            {"at": 0, "duration": 1, "posture": "下坠中"},
            {"at": 3, "duration": 1, "posture": "被接住"},
        ]))
        roles = [b.get("time_role") for b in result["packet"]["actors"][0]["beats"]]
        self.assertEqual(roles, ["start", "end"])

    def test_single_beat_labelled_end(self):
        # 单拍镜头与图片模式取末拍的旧行为一致：标 end。
        result = _perform(_packet([{"at": 0, "duration": 2, "posture": "静止"}]))
        self.assertEqual(result["packet"]["actors"][0]["beats"][0].get("time_role"), "end")

    def test_out_of_order_beats_labelled_by_time(self):
        # 模型乱序输出时按 at 稳定排序后再打首末拍。
        result = _perform(_packet([
            {"at": 4, "duration": 1, "posture": "收尾"},
            {"at": 0, "duration": 1, "posture": "开场"},
            {"at": 2, "duration": 1, "gesture": "中段"},
        ]))
        self.assertEqual(result["status"], "ready")
        roles = {b.get("posture") or b.get("gesture"): b.get("time_role") for b in result["packet"]["actors"][0]["beats"]}
        self.assertEqual(roles, {"开场": "start", "中段": "beat", "收尾": "end"})

    def test_every_actor_is_labelled(self):
        result = _perform(_packet(
            [{"at": 0, "duration": 1, "posture": "甲起"}, {"at": 2, "duration": 1, "posture": "甲落"}],
            beats_b=[{"at": 1, "duration": 1, "posture": "乙"}],
        ))
        self.assertEqual([b.get("time_role") for b in result["packet"]["actors"][0]["beats"]], ["start", "end"])
        self.assertEqual(result["packet"]["actors"][1]["beats"][0].get("time_role"), "end")

    def test_invalid_packet_is_not_labelled(self):
        # 校验不通过的草稿不打角色，避免把无效输出当成已分类数据。
        from actor_runtime import perform
        packet = {"shot_id": "S1", "actors": [{"actor_id": "ghost", "beats": [{"at": 0, "duration": 1}]}]}
        result = perform(
            _request(),
            lambda messages: {"content": json.dumps(packet, ensure_ascii=False), "finish_reason": "stop"},
            max_attempts=1,
        )
        self.assertEqual(result["status"], "invalid")
        self.assertNotIn("time_role", result["packet"]["actors"][0]["beats"][0])


# ---------- prompt_compiler：图片模式按角色取拍 ----------

def _board_with_roles(roles):
    beats = []
    for index, role in enumerate(roles):
        beat = {"at": index * 2, "duration": 1, "posture": f"姿态{index}"}
        if role:
            beat["time_role"] = role
        beats.append(beat)
    return {
        "actors": {"a": {"name": "甲"}},
        "shots": [{
            "id": "S1", "dur": 6, "scene": "room",
            "prompt": "甲在窗边", "lines": [],
            "performance": {"status": "ready", "mode": "stateful",
                "packet": {"shot_id": "S1", "actors": [{"actor_id": "a", "beats": beats}]}},
        }],
    }


class ImageBeatSelectionTests(unittest.TestCase):
    def test_image_prefers_end_marked_beat_over_position(self):
        # end 标记不在末位时按标记取，不再盲取最后一拍。
        from prompt_compiler import compile_shot
        out = compile_shot(_board_with_roles(["beat", "end", "beat"]), "S1", mode="stateful", media_type="image")
        self.assertIn("姿态1", out["text"])
        self.assertNotIn("姿态2", out["text"])

    def test_image_falls_back_to_last_beat_when_unmarked(self):
        # 旧数据全部未标角色：维持取末拍，不打断既有流程。
        from prompt_compiler import compile_shot
        out = compile_shot(_board_with_roles(["", "", ""]), "S1", mode="stateful", media_type="image")
        self.assertIn("姿态2", out["text"])
        self.assertNotIn("姿态0", out["text"])

    def test_video_keeps_all_beats_regardless_of_roles(self):
        from prompt_compiler import compile_shot
        out = compile_shot(_board_with_roles(["start", "beat", "end"]), "S1", mode="stateful", media_type="video")
        for marker in ("姿态0", "姿态1", "姿态2"):
            self.assertIn(marker, out["text"])


# ---------- production_requests：首帧/尾帧槽位时间角色约束 ----------

def _cfg():
    return dict(id="minimax", enabled=True, base_url="https://example.test/v1", models={"video": "MiniMax-H3"})


def _compile(project, mode, time_role=None, shot_count=1):
    """构造最小分镜并编译视频请求；time_role 写在第一镜已采用关键帧上。"""
    from production_media import digest
    from production_requests import compile_request
    board = {"actors": {}, "shots": [dict(id=f"S{i+1}", dur=5, prompt_video="运动", prompt_image="图")
                                     for i in range(shot_count)]}
    for index, shot in enumerate(board["shots"]):
        image = project / f"k{index}.png"
        image.write_bytes(b"frame")
        keyframe = {"path": f"k{index}.png", "sha256": digest(image)}
        if index == 0 and time_role is not None:
            keyframe["time_role"] = time_role
        shot["keyframe"] = keyframe
    (project / "分镜").mkdir(exist_ok=True)
    (project / "分镜" / "剧本_E1.json").write_text(json.dumps(board, ensure_ascii=False), encoding="utf-8")
    body = dict(board="剧本_E1.json", scope="S", target="S1", type="video", video_options={"mode": mode})
    return compile_request(project, body, _cfg())


class FrameTimeRoleConstraintTests(unittest.TestCase):
    def test_end_keyframe_rejected_as_first_frame(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError) as ctx:
                _compile(Path(tmp), "first_frame", time_role="end")
            self.assertIn("不能当首帧用", str(ctx.exception))

    def test_beat_and_compose_rejected_as_first_frame(self):
        with tempfile.TemporaryDirectory() as tmp:
            for role in ("beat", "compose"):
                with self.subTest(role=role), self.assertRaises(ValueError):
                    _compile(Path(tmp), "first_frame", time_role=role)

    def test_start_keyframe_accepted_as_first_frame(self):
        with tempfile.TemporaryDirectory() as tmp:
            packet = _compile(Path(tmp), "first_frame", time_role="start")
            self.assertEqual([r["frame_role"] for r in packet["refs"]], ["first_frame"])
            self.assertEqual(packet["refs"][0].get("time_role"), "start")

    def test_unmarked_keyframe_falls_back_with_warning(self):
        # 旧数据未分类：不阻断，打警告日志提醒补标。
        with tempfile.TemporaryDirectory() as tmp:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                packet = _compile(Path(tmp), "first_frame")
            self.assertEqual([r["frame_role"] for r in packet["refs"]], ["first_frame"])
            self.assertIn("未标注时间角色", out.getvalue())

    def test_unknown_role_treated_as_unmarked(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                packet = _compile(Path(tmp), "first_frame", time_role="strat")
            self.assertEqual([r["frame_role"] for r in packet["refs"]], ["first_frame"])
            self.assertIn("不在受控词表", out.getvalue())

    def test_start_keyframe_rejected_as_last_frame(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError) as ctx:
                _compile(Path(tmp), "last_frame", time_role="start")
            self.assertIn("不能当尾帧用", str(ctx.exception))

    def test_end_keyframe_accepted_as_last_frame(self):
        with tempfile.TemporaryDirectory() as tmp:
            packet = _compile(Path(tmp), "last_frame", time_role="end")
            self.assertEqual([r["frame_role"] for r in packet["refs"]], ["last_frame"])

    def test_reference_mode_has_no_time_role_constraint(self):
        # 全能参考只是参考素材，构图参考/结束图都可正常引用。
        with tempfile.TemporaryDirectory() as tmp:
            packet = _compile(Path(tmp), "reference", time_role="end")
            self.assertEqual([r["frame_role"] for r in packet["refs"]], ["reference_image"])


if __name__ == "__main__":
    unittest.main()
