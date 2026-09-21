# -*- coding: utf-8 -*-
"""故事版画格的存取与项目策略装配。"""
import json
import os

from storyboard_panels import compile_image_request, fingerprint, validate_board_panels, build_grid


def image_policy(project_dir):
    style_path = os.path.join(project_dir, "剧本", "style.json")
    try:
        with open(style_path, encoding="utf-8") as fh:
            style = json.load(fh)
    except (OSError, ValueError):
        style = {}
    if not isinstance(style, dict):
        style = {}
    positive = ""  # 画风来自 image skill
    image_skill = str(style.get("image") or "").strip()
    negative = ["文字", "水印", "边框", "对白框", "字幕"]
    if image_skill:
        try:
            import skill_lib
            raw = str(skill_lib.style_for(project_dir, "image") or "")
            # 旧画风 skill 混有资产三视图规则；先只取适用于剧情画格的段落。
            clean = []
            for line in raw.splitlines():
                line = line.strip()
                if not line or any(x in line for x in
                                   ("三视图", "纯白背景", "自然站姿", "表情中性", "角色设定图")):
                    continue
                if "追加" in line and "：" in line:
                    continue
                line = line.split("绝对禁止", 1)[0].strip().strip("\"“” 。")
                line = line.replace(", not a photograph", "").replace("NOT photorealistic", "")
                if line:
                    clean.append(line)
            positive = "；".join(dict.fromkeys([x for x in [positive] + clean if x]))
            negative += [skill_lib.skill_negative(image_skill)]
        except Exception:
            pass
    policy = {"positive": positive, "negative": negative, "target": "image_panel"}
    policy["version"] = fingerprint(policy)
    return policy


def preview_panel(project_dir, panel, draft, refs):
    policy = image_policy(project_dir)
    return compile_image_request(panel, draft, policy, refs)


def save_panel(project_dir, board_name, panel):
    """只保存通过校验的画格事实；草稿和请求属于创作记录。"""
    name = os.path.basename(str(board_name))
    if name != board_name or not name.lower().endswith(".json"):
        raise ValueError("分镜文件名无效")
    path = os.path.join(project_dir, "分镜", name)
    with open(path, encoding="utf-8") as fh:
        board = json.load(fh)
    if not isinstance(board, dict):
        raise ValueError("分镜格式无效")
    panels = board.setdefault("panels", [])
    if not isinstance(panels, list):
        raise ValueError("panels 必须是数组")
    pid = str(panel.get("id") or "")
    if not pid:
        raise ValueError("画格 ID 必填")
    replacement = dict(panel)
    for index, previous in enumerate(panels):
        if isinstance(previous, dict) and previous.get("id") == pid:
            panels[index] = replacement
            break
    else:
        panels.append(replacement)
    errors = validate_board_panels(board)
    if errors:
        raise ValueError("；".join(errors))
    try:
        import versions
        versions.snapshot(path)
    except ImportError:
        pass
    temp = path + ".tmp_panel"
    with open(temp, "w", encoding="utf-8") as fh:
        json.dump(board, fh, ensure_ascii=False, indent=2)
    os.replace(temp, path)
    return board


def save_grid(project_dir, board_name, grid):
    """把有序画格组写回分镜，宫格本身不复制画格剧情事实。"""
    name = os.path.basename(str(board_name))
    if name != board_name or not name.lower().endswith(".json"):
        raise ValueError("分镜文件名无效")
    path = os.path.join(project_dir, "分镜", name)
    with open(path, encoding="utf-8") as fh:
        board = json.load(fh)
    if not isinstance(board, dict):
        raise ValueError("分镜格式无效")
    normalized = build_grid(
        [{"id": x} for x in grid.get("panel_ids") or []],
        str(grid.get("layout") or ""), str(grid.get("id") or ""))
    normalized.pop("capacity", None)
    grids = board.setdefault("grids", [])
    if not isinstance(grids, list):
        raise ValueError("grids 必须是数组")
    for i, old in enumerate(grids):
        if isinstance(old, dict) and old.get("id") == normalized["id"]:
            grids[i] = normalized
            break
    else:
        grids.append(normalized)
    errors = validate_board_panels(board)
    if errors:
        raise ValueError("；".join(errors))
    try:
        import versions
        versions.snapshot(path)
    except ImportError:
        pass
    temp = path + ".tmp_grid"
    with open(temp, "w", encoding="utf-8") as fh:
        json.dump(board, fh, ensure_ascii=False, indent=2)
    os.replace(temp, path)
    return board
