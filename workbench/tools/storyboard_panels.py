# -*- coding: utf-8 -*-
"""故事版画格契约与确定性生图请求编译。分镜保存事实，生成任务保存快照。"""
import hashlib
import json
import re

VALID_TIME_ROLES = {"before_action", "mid_action", "after_action", "transition", "establishing", "custom"}
ASPECTS = {"16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3", "21:9"}


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(value):
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def validate_board_panels(board):
    """只验证新字段；旧分镜没有 panels 时仍合法。"""
    errors = []
    shots = {str(s.get("id")) for s in board.get("shots", []) if isinstance(s, dict)}
    panels = board.get("panels", [])
    grids = board.get("grids", [])
    if not isinstance(panels, list):
        return ["panels 必须是数组"]
    if not isinstance(grids, list):
        return ["grids 必须是数组"]
    seen = set()
    for panel in panels:
        if not isinstance(panel, dict):
            errors.append("panel 必须是对象")
            continue
        pid = str(panel.get("id") or "")
        if not pid or pid in seen:
            errors.append("画格 ID 缺失或重复: " + pid)
        seen.add(pid)
        linked = panel.get("shot_ids")
        if not isinstance(linked, list) or not linked:
            errors.append(pid + " 的 shot_ids 必须非空")
        else:
            for sid in linked:
                if str(sid) not in shots:
                    errors.append(pid + " 引用了不存在的镜头 " + str(sid))
        if panel.get("time_role", "custom") not in VALID_TIME_ROLES:
            errors.append(pid + " 的 time_role 无效")
        composition = panel.get("composition") or {}
        if not isinstance(composition, dict) or composition.get("aspect_ratio", "16:9") not in ASPECTS:
            errors.append(pid + " 的画幅比例无效")
    grid_ids = set()
    for grid in grids:
        if not isinstance(grid, dict):
            errors.append("grid 必须是对象")
            continue
        gid = str(grid.get("id") or "")
        if not gid or gid in grid_ids:
            errors.append("宫格 ID 缺失或重复: " + gid)
        grid_ids.add(gid)
        layout = grid.get("layout")
        capacity = {"3x3": 9, "5x5": 25}.get(layout)
        ids = grid.get("panel_ids")
        if capacity is None or not isinstance(ids, list) or len(ids) > capacity:
            errors.append(gid + " 的布局或画格数量无效")
            continue
        if len(ids) != len(set(ids)):
            errors.append(gid + " 重复引用画格")
        for pid in ids:
            if pid not in seen:
                errors.append(gid + " 引用了不存在的画格 " + str(pid))
    return errors


def ensure_panel_for_shot(board, shot_id):
    """从旧分镜建立可编辑画格事实，不把旧 prompt 当作系统规则。"""
    shot = next((s for s in board.get("shots", []) if str(s.get("id")) == str(shot_id)), None)
    if shot is None:
        raise ValueError("镜头不存在: " + str(shot_id))
    panels = board.setdefault("panels", [])
    for panel in panels:
        if shot_id in panel.get("shot_ids", []):
            return panel
    base = str(shot_id) + "-P"
    used = {str(p.get("id")) for p in panels if isinstance(p, dict)}
    index = 1
    while base + str(index) in used:
        index += 1
    panel = {
        "id": base + str(index),
        "shot_ids": [str(shot_id)],
        "beat": str(shot.get("content") or shot.get("action") or "").strip(),
        "time_role": "custom",
        "composition": {
            "aspect_ratio": str(shot.get("aspect_ratio") or "16:9"),
            "shot_size": str(shot.get("shot_size") or ""),
            "angle": str(shot.get("angle") or ""),
        },
        "visible_refs": list(dict.fromkeys(re.findall(r"@(?:character|scene|prop):[\w-]+",
                                str(shot.get("prompt_image") or shot.get("prompt") or "")))),
        "lighting": str(shot.get("lighting") or ""),
        "source": {"kind": "script_shot", "shot_id": str(shot_id)},
    }
    panels.append(panel)
    return panel


def build_grid(panels, layout="3x3", grid_id="G1"):
    capacity = {"3x3": 9, "5x5": 25}.get(layout)
    if capacity is None:
        raise ValueError("只支持 3x3 或 5x5")
    ids = [str(p.get("id") or "") for p in panels]
    if len(ids) > capacity or any(not x for x in ids) or len(set(ids)) != len(ids):
        raise ValueError("宫格画格 ID 无效或超出容量")
    return {"id": grid_id, "layout": layout, "capacity": capacity, "panel_ids": ids}


def _negative_tokens(value):
    if isinstance(value, str):
        return [x.strip() for x in re.split(r"[,，；;\n]+", value) if x.strip()]
    if isinstance(value, list):
        return [str(x).strip() for part in value for x in _negative_tokens(part)]
    return []


def compile_image_request(panel, draft, policy, refs):
    """唯一生图编译入口；不给图像模型传文本 LLM 的 system/user 消息。"""
    pid = str(panel.get("id") or "")
    if not pid or draft.get("panel_id") != pid:
        raise ValueError("画格草稿与画格 ID 不一致")
    visible = set(panel.get("visible_refs") or [])
    used = list(dict.fromkeys(draft.get("used_refs") or []))
    description = str(draft.get("visual_description") or "").strip()
    embedded = set(re.findall(r"@(?:character|scene|prop):[\w-]+", description))
    if not description:
        raise ValueError("画格描述不能为空")
    if (set(used) | embedded) - visible:
        raise ValueError("草稿引用的资产未列入画格: " + "、".join(sorted((set(used) | embedded) - visible)))
    composition = panel.get("composition") or {}
    aspect = str(composition.get("aspect_ratio") or "16:9")
    if aspect not in ASPECTS:
        raise ValueError("画幅比例无效")
    positive = str(policy.get("positive") or "").strip()
    parts = [aspect + "横构图" if aspect in ("16:9", "21:9") else aspect + "构图",
             str(composition.get("shot_size") or "").strip(),
             str(composition.get("angle") or "").strip(), str(panel.get("beat") or "").strip(), description, positive]
    # 相同段落只保留一次；不把通用禁令或对白写回正向正文。
    prompt = "，".join(dict.fromkeys(x for x in parts if x))
    negative = list(dict.fromkeys(_negative_tokens(policy.get("negative")) +
                                  _negative_tokens(draft.get("local_negative"))))
    checked_refs = []
    for ref in refs or []:
        if not isinstance(ref, dict) or not ref.get("path") or not ref.get("purpose"):
            raise ValueError("参考图必须有 path 和 purpose")
        checked_refs.append({"path": str(ref["path"]), "purpose": str(ref["purpose"]),
                             "version_id": str(ref.get("version_id") or "")})
    if checked_refs:
        purpose_text = "参考图依次用于：" + "；".join(
            str(i + 1) + "." + ref["purpose"] for i, ref in enumerate(checked_refs))
        prompt = prompt + "。" + purpose_text
    return {
        "panel_id": pid, "shot_ids": list(panel.get("shot_ids") or []),
        "prompt": prompt, "negative_prompt": ",".join(negative),
        "aspect_ratio": aspect, "image_refs": checked_refs,
        "policy_version": str(policy.get("version") or ""),
        "panel_spec_hash": fingerprint(panel), "draft_hash": fingerprint(draft),
    }

