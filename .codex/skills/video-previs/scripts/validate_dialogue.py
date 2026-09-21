# -*- coding: utf-8 -*-
"""对白分镜契约主校验器，供生成、编辑和表演层共同调用。"""
import json
import math
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def _err(code, path, message, severity="error"):
    return {"code": code, "path": path, "message": message, "severity": severity}


# 保留说话人：旁白/画外音。不是角色，不要求出现在 actors，也禁止为其建 actors 记录。
RESERVED_SPEAKERS = {"narrator"}


def validate_document(board):
    errors, warnings = [], []
    if not isinstance(board, dict):
        return {"errors": [_err("DOCUMENT_TYPE", "$", "分镜必须是对象")], "warnings": []}
    if "actors" not in board:
        errors = [_err("MISSING_ACTORS", "actors", "缺少顶层字段：actors")]
    else:
        errors = []
    if "shots" not in board:
        errors.append(_err("MISSING_SHOTS", "shots", "缺少顶层字段：shots"))
    actors = board.get("actors") or {}
    if not isinstance(actors, dict):
        errors.append(_err("ACTORS_TYPE", "actors", "actors 必须是对象")); actors = {}
    for rs in RESERVED_SPEAKERS & set(actors):
        errors.append(_err("RESERVED_ACTOR", f"actors.{rs}", f"保留说话人 {rs}（旁白）不是角色，禁止建档"))
    shots = board.get("shots")
    if not isinstance(shots, list):
        errors.append(_err("SHOTS_TYPE", "shots", "shots 必须是数组")); shots = []
    seen = set()
    cams = {"wide", "near", "two", "cu", "ots"}
    for i, shot in enumerate(shots):
        path = f"shots[{i}]"
        if not isinstance(shot, dict):
            errors.append(_err("SHOT_TYPE", path, "镜头必须是对象")); continue
        sid = str(shot.get("id") or "")
        if not sid or sid in seen: errors.append(_err("SHOT_ID", path + ".id", "镜头 id 缺失或重复"))
        seen.add(sid)
        try: dur = float(shot.get("dur"))
        except (TypeError, ValueError): dur = 0
        if not math.isfinite(dur) or dur <= 0: errors.append(_err("DUR_INVALID", path + ".dur", "dur 必须是有限正数"))
        if shot.get("cam") not in cams: errors.append(_err("CAM_INVALID", path + ".cam", "cam 不在对白契约词表"))
        for key in ("speaker", "host", "target", "focus"):
            if shot.get(key) and shot[key] not in actors and shot[key] not in RESERVED_SPEAKERS:
                errors.append(_err("ACTOR_REF", f"{path}.{key}", f"角色 {shot[key]} 不在 actors"))
        for key in ("pos", "look"):
            if key in shot and (not isinstance(shot[key], list) or len(shot[key]) != 3 or
                                any(not isinstance(x, (int, float)) or not math.isfinite(x) for x in shot[key])):
                errors.append(_err("COORDINATE_INVALID", f"{path}.{key}", "坐标必须是有限三元数组"))
        for j, line in enumerate(shot.get("lines") or []):
            lp = f"{path}.lines[{j}]"
            if line.get("speaker") and line["speaker"] not in actors and line["speaker"] not in RESERVED_SPEAKERS:
                errors.append(_err("ACTOR_REF", lp + ".speaker", "台词角色不在 actors"))
            try:
                at = float(line.get("at", 0)); ld = float(line.get("dur", 0) or 0)
            except (TypeError, ValueError): at, ld = -1, -1
            if not math.isfinite(at) or at < 0: errors.append(_err("LINE_AT_INVALID", lp + ".at", "台词起点必须在镜头内"))
            if not math.isfinite(ld) or ld <= 0: errors.append(_err("LINE_DUR_INVALID", lp + ".dur", "台词时长必须为正数"))
            if dur > 0 and (at > dur or at + ld > dur + 1e-6):
                errors.append(_err("LINE_OUT_OF_SHOT", lp, "台词起止必须落在镜头时长内"))
    return {"errors": errors, "warnings": warnings}


def main():
    path = sys.argv[1]
    board = json.load(open(path, encoding="utf-8"))
    result = validate_document(board)
    for item in result["warnings"]: print("  [警告]", item["message"])
    for item in result["errors"]: print("  [错误]", item["path"], item["message"])
    if result["errors"]:
        print(f"❌ 校验未通过：{len(result['errors'])} 错误,{len(result['warnings'])} 警告")
        raise SystemExit(1)
    print(f"✅ 通过（{len(result['warnings'])} 警告）")


if __name__ == "__main__":
    main()
