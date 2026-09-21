# -*- coding: utf-8 -*-
"""派生产物来源摘要，避免仅凭文件存在性复用旧图。"""
import hashlib
import json
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _shot_projection(shot, kind):
    if kind == "diagram":
        keys = ("id", "dur", "scene", "cam", "shot_size", "angle", "pos", "look", "speaker", "action", "staging", "pose", "actors")
    elif kind in ("prompt", "previz"):
        # 表演结果是派生产物，不应参与 prompt/previz 输入来源摘要。
        keys = ("id", "dur", "scene", "move", "prompt", "action", "lines", "actors", "refs")
    else:
        keys = tuple(sorted(shot))
    return {key: shot.get(key) for key in keys if key in shot}


def artifact_hash(board, artifact_kind, tool_version="1"):
    """按产物类型提取输入字段并返回 sha256。"""
    payload = {"artifact_kind": artifact_kind, "tool_version": str(tool_version),
               "project": {key: board.get(key) for key in ("w", "h", "fps", "env", "set") if key in board},
               "acting_context": board.get("acting_context", {}),
               "actors": board.get("actors", {}),
               "shots": [_shot_projection(s, artifact_kind) for s in board.get("shots", [])]}
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def is_current(manifest, expected_hash):
    return bool(manifest and manifest.get("source_hash") == expected_hash and manifest.get("status", "ready") != "stale")



