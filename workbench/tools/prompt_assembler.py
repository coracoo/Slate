# -*- coding: utf-8 -*-
"""分镜执行器的提示词与参考图装配。

该模块只做确定性装配，不调用模型：把 storyboard 单镜、项目级画风、知识卡片
和项目内资产整理成可审计的 prompt/negative/refs 三件套，供创作台和批量执行复用。
"""
import json
import math
import os
import re
import sys
from narration import is_narrator

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

try:
    from script_repository import is_collective_asset, is_asset_prop
except Exception:
    def is_collective_asset(item):
        return bool(isinstance(item, dict) and item.get("is_collective"))
    def is_asset_prop(item):
        if not isinstance(item, dict) or not item.get("asset_required", True):
            return False
        kind = str(item.get("kind") or "叙事").strip()
        return (kind == "叙事" or kind in ("关联素材", "服饰", "配饰", "组件")
                or bool(item.get("parent_ref") or item.get("relation") or item.get("derived_from")))

BASE_NEG = "文字,水印,边框,画框,额外角色,重复角色,畸形手指,多余肢体,肢体交叉错乱,面部变形"

try:
    from reference_contract import explicit_reference_rows, reference_label, resolve_reference_path, strip_video_only_lines
except Exception:
    from .reference_contract import explicit_reference_rows, reference_label, resolve_reference_path, strip_video_only_lines


def _project_dir(project):
    if isinstance(project, dict):
        value = project.get("dir") or project.get("project_dir") or project.get("path")
    else:
        value = project
    return os.path.abspath(str(value or ""))


def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            value = json.load(fh)
        return value
    except (OSError, ValueError, TypeError):
        return default


def _style(project_dir):
    style = _read_json(os.path.join(project_dir, "剧本", "style.json"), {})
    return style if isinstance(style, dict) else {}


def _skill_negative(style_id):
    if not style_id:
        return ""
    try:
        import skill_lib
        return str(skill_lib.skill_negative(style_id) or "").strip()
    except Exception:
        return ""


def _skill_positive(project_dir):
    """读取画风 skill 的正向追加词，过滤只适用于资产设定图的指令。

    人物三视图、角色设定图和场景概念图是资产生成阶段的约束，混进单镜
    生图会把横构图拉成白底设定图，因此不能随镜头提示词下发。
    """
    try:
        import skill_lib
        raw = str(skill_lib.style_for(project_dir, "image") or "").strip()
        if not raw:
            return ""
        blocked = ("三视图", "角色设定图", "场景图可", "纯白背景", "自然站姿", "表情中性")
        lines = [line.rstrip() for line in raw.splitlines()
                 if line.strip() and not any(token in line for token in blocked)]
        return "\n".join(lines).strip()
    except Exception:
        return ""


def _knowledge(text):
    try:
        import knowledge
        return knowledge.query(text, k=2) or []
    except Exception:
        return []


def _line_text(line):
    if not isinstance(line, dict):
        return str(line or "")
    speaker = str(line.get("speaker") or line.get("actor") or "").strip()
    text = str(line.get("line") or line.get("text") or "").strip()
    return f"{speaker}：{text}" if speaker and text else text or speaker


def _canon(value):
    """用于资产 ID/名称匹配的宽松键，兼容 linyifan 与 lin_yifan。"""
    return "".join(ch.lower() for ch in str(value or "") if ch.isalnum())


def _clip(value, limit=900):
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _asset_list(data, keys):
    if not isinstance(data, dict):
        return []
    value = next((data.get(k) for k in keys if data.get(k) is not None), [])
    if isinstance(value, list):
        return [dict(x) for x in value if isinstance(x, dict)]
    if isinstance(value, dict):
        out = []
        for rid, record in value.items():
            if isinstance(record, dict):
                item = dict(record)
                item.setdefault("id", rid)
                out.append(item)
        return out
    return []


_ASSET_INDEX_CACHE = {}


def _asset_index(project_dir):
    """资产图索引（按 mtime 缓存：批量逐镜装配只读一次盘，文件变更自动失效）。"""
    key = os.path.abspath(project_dir)
    idx_path = os.path.join(key, "素材", "素材图.json")
    try:
        # mtime+size 双键：同秒内重写（mtime 偶同）也能靠尺寸变化失效
        stamp = (os.path.getmtime(idx_path), os.path.getsize(idx_path))
    except OSError:
        stamp = (-1.0, -1)
    hit = _ASSET_INDEX_CACHE.get(key)
    if hit and hit[0] == stamp:
        return hit[1]
    data = _read_json(os.path.join(project_dir, "素材", "素材图.json"), {})
    data = data if isinstance(data, dict) else {}
    _ASSET_INDEX_CACHE[key] = (stamp, data)
    return data


def _merge_index_records(records, index_records, append_missing=True):
    """把已生成资产图的 path/prompt 补回剧本资产记录，不覆盖提炼结果。"""
    by_key = {}
    for record in index_records:
        if not isinstance(record, dict):
            continue
        for value in (record.get("id"), record.get("name")):
            if value:
                by_key[_canon(value)] = record
    out = []
    for record in records:
        item = dict(record)
        idx = by_key.get(_canon(item.get("id"))) or by_key.get(_canon(item.get("name")))
        if isinstance(idx, dict):
            for key in ("path", "prompt", "image_prompt", "name"):
                if not item.get(key) and idx.get(key):
                    item[key] = idx[key]
        out.append(item)
    known = {_canon(x.get("id")) for x in out if x.get("id")}
    if not append_missing:
        return out
    for record in index_records:
        if not isinstance(record, dict):
            continue
        rid = _canon(record.get("id"))
        if rid and rid not in known:
            out.append(dict(record))
    return out


def _load_assets(project_dir):
    """读取资产提炼三件套，并用资产图索引补齐图片路径。"""
    script_specs = {
        "characters": ("人物.json", ("characters", "人物", "actors"), ("人物", "character", "characters")),
        "scenes": ("场景.json", ("scenes", "场景", "locations"), ("场景", "scene", "scenes")),
        "props": ("道具.json", ("props", "道具", "objects", "items"), ("道具", "prop", "props")),
    }
    index = _asset_index(project_dir)
    result = {}
    for kind, (filename, keys, zones) in script_specs.items():
        data = _read_json(os.path.join(project_dir, "素材", filename), {})
        records = _asset_list(data, keys)
        indexed = []
        for zone in zones:
            zone_data = index.get(zone)
            if isinstance(zone_data, dict):
                for rid, record in zone_data.items():
                    if isinstance(record, dict):
                        item = dict(record)
                        item.setdefault("id", rid)
                        indexed.append(item)
            elif isinstance(zone_data, list):
                indexed.extend(x for x in zone_data if isinstance(x, dict))
        # 有资产提炼 JSON 时只用资产图索引补路径；没有提炼文件才退回索引记录。
        result[kind] = _merge_index_records(records, indexed, append_missing=not records)
        if kind == "characters":
            result[kind] = [item for item in result[kind] if not is_collective_asset(item)]
        elif kind == "props":
            # 子素材也是可被镜头 @ 引用的全局资产。旧逻辑只留下“叙事”
            # 道具，导致项圈、步足、状态变体即使已写入 prop_refs，
            # 装配上下文也找不到它们，最终提示词/参考图清单会漏项。
            result[kind] = [item for item in result[kind] if is_asset_prop(item)]
    return result


def _shot_text(shot):
    values = []
    for key in ("prompt", "content", "action", "story", "dialogue", "description", "scene", "location", "sound", "lighting"):
        value = shot.get(key)
        if isinstance(value, list):
            values.extend(_line_text(x) for x in value)
        elif isinstance(value, dict):
            values.extend(str(x) for x in value.values())
        elif value:
            values.append(str(value))
    for line in shot.get("lines") or []:
        values.append(_line_text(line))
    return " ".join(x for x in values if x)


def _record_match(value, records):
    key = _canon(value)
    if not key:
        return None
    for record in records:
        keys = [record.get("id"), record.get("name")]
        aliases = record.get("aliases") or record.get("alias") or []
        if isinstance(aliases, str):
            aliases = [aliases]
        keys.extend(aliases if isinstance(aliases, (list, tuple)) else [])
        if any(key == _canon(x) for x in keys if x):
            return record
    return None


def _record_in_text(record, text):
    haystack = _canon(text)
    if not haystack:
        return False
    values = [record.get("name"), record.get("id")]
    aliases = record.get("aliases") or record.get("alias") or []
    if isinstance(aliases, str):
        aliases = [aliases]
    values.extend(aliases if isinstance(aliases, (list, tuple)) else [])
    for value in values:
        token = _canon(value)
        # ID 很短时容易误命中普通英文单词，只用长度 >= 3 的 ID。
        if token and (value == record.get("name") or len(token) >= 3) and token in haystack:
            return True
    return False


def _explicit_values(shot, keys):
    out = []
    for key in keys:
        value = shot.get(key)
        if isinstance(value, dict):
            value = [value.get("id"), value.get("name")]
        if isinstance(value, (list, tuple, set)):
            values = value
        elif value:
            values = [value]
        else:
            values = []
        for item in values:
            if isinstance(item, dict):
                item = item.get("id") or item.get("name")
            if item is not None and str(item).strip() and str(item) not in out:
                out.append(str(item).strip())
    return out


def _match_assets(shot, project_dir, actors=None, board=None):
    """按镜头显式字段、文本和归属关系匹配资产提炼记录。"""
    shot = shot if isinstance(shot, dict) else {}
    assets = _load_assets(project_dir)
    text = _shot_text(shot)
    actors = actors if isinstance(actors, dict) else {}
    board = board if isinstance(board, dict) else {}

    characters = assets.get("characters", [])
    matched_chars = []
    values = _explicit_values(shot, ("actor", "actors", "characters", "character_ids", "cast", "in_scene", "present"))
    values += _explicit_values(shot, ("speaker", "host", "target", "focus"))
    for line in shot.get("lines") or []:
        if isinstance(line, dict) and line.get("speaker"):
            values.append(str(line.get("speaker")))
    for value in values:
        rec = _record_match(value, characters)
        if rec is None:
            actor = actors.get(value)
            actor_name = actor.get("name") if isinstance(actor, dict) else ""
            rec = _record_match(actor_name, characters)
        if rec is not None and rec not in matched_chars:
            matched_chars.append(rec)
    for rec in sorted(characters, key=lambda x: len(_canon(x.get("name") or x.get("id"))), reverse=True):
        if _record_in_text(rec, text) and rec not in matched_chars:
            matched_chars.append(rec)

    scenes = assets.get("scenes", [])
    scene_values = _explicit_values(shot, ("scene_id", "location_id", "location"))
    raw_scene = str(shot.get("scene") or "").strip()
    if raw_scene and raw_scene.lower() not in ("room", "field", "wide", "indoor", "outdoor"):
        scene_values.append(raw_scene)
    board_set = board.get("set")
    if isinstance(board_set, dict):
        scene_values += _explicit_values(board_set, ("id", "name", "scene", "location"))
    matched_scene = None
    best_score = 0
    for rec in scenes:
        score = 0
        if any(_record_match(value, [rec]) is rec for value in scene_values):
            score += 100
        if _record_in_text(rec, text):
            score += 80
        if raw_scene.lower() == "room" and rec.get("interior") is True:
            score += 8
        if raw_scene.lower() == "field" and rec.get("interior") is False:
            score += 8
        used_by = rec.get("used_by") or []
        used_keys = {_canon(x) for x in used_by}
        for char in matched_chars:
            if _canon(char.get("id")) in used_keys or _canon(char.get("name")) in used_keys:
                score += 15
        if score > best_score:
            best_score, matched_scene = score, rec
    if matched_scene is None and len(scenes) == 1 and (raw_scene or text):
        matched_scene = scenes[0]

    props = assets.get("props", [])
    prop_values = _explicit_values(shot, ("prop", "props", "prop_ids", "objects", "items", "props_used"))
    matched_props = []
    for value in prop_values:
        rec = _record_match(value, props)
        if rec is not None and rec not in matched_props:
            matched_props.append(rec)
    for rec in sorted(props, key=lambda x: len(_canon(x.get("name") or x.get("id"))), reverse=True):
        if _record_in_text(rec, text):
            if rec not in matched_props:
                matched_props.append(rec)
            continue
        hint = " ".join(str(x) for x in (rec.get("actions") or []) if x)
        hint += " " + str(rec.get("shot_hint") or "")
        if hint.strip() and _canon(hint) in _canon(text) and rec not in matched_props:
            matched_props.append(rec)
    # 文本没有点名道具时，只按角色/场景 owner 补叙事道具，避免每镜塞入整套陈设。
    if not matched_props:
        owner_keys = {_canon(x.get("id")) for x in matched_chars if x.get("id")}
        owner_keys.update(_canon(x.get("name")) for x in matched_chars if x.get("name"))
        if matched_scene:
            owner_keys.update({_canon(matched_scene.get("id")), _canon(matched_scene.get("name"))})
        for rec in props:
            owner = _canon(rec.get("owner"))
            if owner and any(owner in key or key in owner for key in owner_keys if key):
                if str(rec.get("kind") or "").strip() in ("叙事", ""):
                    matched_props.append(rec)
    narrators = [rec for rec in matched_chars if is_narrator(None, rec)]
    matched_chars = [rec for rec in matched_chars if not is_narrator(None, rec)]
    return {"characters": matched_chars[:6], "scene": matched_scene, "props": matched_props[:6], "narrators": narrators}


def _asset_summary(kind, record):
    if not isinstance(record, dict):
        return {}
    if kind == "character":
        appearance = record.get("appearance") if isinstance(record.get("appearance"), dict) else {}
        return {
            "id": record.get("id", ""), "name": record.get("name", ""), "role": record.get("role", ""),
            "look": appearance.get("look") or "", "outfit": appearance.get("outfit") or "",
            "lens": record.get("lens") or "", "voice": record.get("voice") or "",
            "sheet_prompt": record.get("sheet_prompt") or record.get("prompt") or "",
            "path": record.get("path") or "",
        }
    return {
        "id": record.get("id", ""), "name": record.get("name", ""), "kind": record.get("kind", ""),
        "time": record.get("time", ""), "light": record.get("light", ""), "interior": record.get("interior"),
        "geometry": record.get("geometry") or [], "actions": record.get("actions") or [],
        "shot_hint": record.get("shot_hint") or "", "image_prompt": record.get("image_prompt") or record.get("prompt") or "",
        "path": record.get("path") or "",
    }


def _camera_view(shot):
    pos, look = shot.get("pos"), shot.get("look")
    if not isinstance(pos, (list, tuple)) or not isinstance(look, (list, tuple)):
        return ""
    try:
        px, py = float(pos[0]), float(pos[1])
        lx, ly = float(look[0]), float(look[1])
        dist = math.hypot(px - lx, py - ly)
        angle = math.degrees(math.atan2(ly - py, lx - px))
        return f"机位坐标({px:g},{py:g})，视点({lx:g},{ly:g})，距离{dist:.1f}m，朝向{angle:.0f}°"
    except (ValueError, TypeError, IndexError):
        return ""


def assemble_shot_prompt(shot, project):
    """返回单镜可执行提示词包。"""
    shot = shot if isinstance(shot, dict) else {}
    project_dir = _project_dir(project)
    # 子素材只描述自身差异；编译镜头时自动带上父级和关联引用，
    # 让资产图与提示词共享同一条身份链。长设定仍不在此处展开。
    try:
        from production_state import asset_dependency_refs
        raw_refs = list(shot.get("asset_refs") or shot.get("refs") or [])
        dependency_refs = asset_dependency_refs(project_dir, raw_refs)
        if dependency_refs:
            shot = dict(shot)
            merged_refs = list(raw_refs)
            for ref in dependency_refs:
                if ref not in merged_refs:
                    merged_refs.append(ref)
            shot["asset_refs"] = merged_refs
            chars = list(shot.get("actor_refs") or shot.get("character_refs") or [])
            props = list(shot.get("prop_refs") or shot.get("props_refs") or [])
            for ref in dependency_refs:
                if ref.startswith("@character:") and ref not in chars:
                    chars.append(ref)
                elif ref.startswith("@prop:") and ref not in props:
                    props.append(ref)
            if chars:
                shot["actor_refs"] = chars
            if props:
                shot["prop_refs"] = props
            scene_dep = next((ref for ref in dependency_refs if ref.startswith("@scene:")), "")
            if scene_dep and not shot.get("scene_ref"):
                shot["scene_ref"] = scene_dep
    except Exception:
        # 旧项目没有资产注册表时继续按原逻辑装配。
        pass
    style = _style(project_dir)
    # E10：显式 "auto"（仅知识库、不注 skill）视为未选，不能拼出 @style:auto 假引用
    image_skill = str(style.get("image") or "").strip()
    if image_skill == "auto":
        image_skill = ""
    image_style_text = _skill_positive(project_dir)
    board = project.get("board") if isinstance(project, dict) else None
    actors = (board or {}).get("actors") if isinstance(board, dict) else {}
    actors = actors if isinstance(actors, dict) else {}
    asset_ctx = _match_assets(shot, project_dir, actors=actors, board=board)
    asset_chars = asset_ctx.get("characters") or []
    narrator_records = asset_ctx.get("narrators") or []
    narrator_records += [dict(rec, id=aid) for aid, rec in actors.items() if isinstance(rec, dict) and is_narrator(aid, rec)]
    narrator_ids = {_canon(rec.get(key)) for rec in narrator_records for key in ("id", "name", "ref") if rec.get(key)}
    def visual_actor(ref):
        return not is_narrator(ref) and _canon(str(ref).split(":", 1)[-1]) not in narrator_ids
    asset_scene = asset_ctx.get("scene")
    asset_props = asset_ctx.get("props") or []

    # 资产匹配只负责得到稳定 ID；生图提示词永远只消费 @ 引用和镜头动作。
    structured_shot = dict(shot)
    media_type = str(project.get("media_type") or "image") if isinstance(project, dict) else "image"
    selected_prompt = ((shot.get("prompt_video") or shot.get("action") or shot.get("content"))
                       if media_type == "video" else (shot.get("prompt_image") or shot.get("prompt")))
    if selected_prompt:
        structured_shot["prompt"] = str(selected_prompt).strip()
        structured_shot["prompt_text"] = str(selected_prompt).strip()
    regenerated = bool((shot.get("prompt_video_source") if media_type == "video" else shot.get("prompt_source")) == "regenerated" and selected_prompt)
    if regenerated:
        visible_chars = set(re.findall(r"@character:[\w-]+", str(selected_prompt)))
        visible_props = set(re.findall(r"@prop:[\w-]+", str(selected_prompt)))
        structured_shot["actors"] = []
        structured_shot["actor_refs"] = list(visible_chars)
        structured_shot["props"] = []
        structured_shot["prop_refs"] = list(visible_props)
        asset_chars = [rec for rec in asset_chars
                       if str(rec.get("ref") or "@character:" + str(rec.get("id") or "")) in visible_chars]
        asset_props = [rec for rec in asset_props
                       if str(rec.get("ref") or "@prop:" + str(rec.get("id") or "")) in visible_props]
        if media_type == "image":
            structured_shot["action"] = str(shot.get("content") or selected_prompt).strip()
            structured_shot["lines"] = []
            structured_shot["dialogue"] = []
    # 旧分镜的 prompt 可能已经包含上一轮完整装配结果；重装配时只保留动作字段。
    raw_prompt = str(structured_shot.get("prompt") or "").strip()
    clean_action = str(structured_shot.get("action") or structured_shot.get("content") or "").strip()
    authored = (shot.get('prompt_video_source') if media_type == 'video' else shot.get('prompt_image_source')) in ('llm', 'authored')
    if not regenerated and not authored and clean_action and any(marker in raw_prompt for marker in ("资产引用：", "画风锚定：", "图像风格技能指令：", "演员角色卡", "输出单张关键帧")):
        structured_shot["prompt"] = clean_action
        structured_shot["prompt_text"] = clean_action
    # 旧分镜可能只有 board.actors 和台词说话人，没有 actor_refs；先把这些
    # 可见角色纳入安全索引，避免自然语言提示词漏掉侧后方反应角色。
    actor_refs = list(structured_shot.get("actor_refs") or [])
    if not actor_refs and not regenerated:
        actor_refs = [
            str(x.get("id") or x.get("name")) for x in asset_chars
            if x.get("id") or x.get("name")
        ]
    for key in (() if regenerated else ("speaker", "host", "target", "focus")):
        if shot.get(key) and str(shot.get(key)) not in actor_refs:
            actor_refs.append(str(shot.get(key)))
    for line in ([] if regenerated else (shot.get("lines") or shot.get("dialogue") or [])):
        if isinstance(line, dict) and line.get("speaker") and str(line["speaker"]) not in actor_refs:
            actor_refs.append(str(line["speaker"]))
    actor_refs = [ref for ref in actor_refs if visual_actor(ref)]
    structured_shot["actor_refs"] = actor_refs
    if actor_refs:
        known_refs = {str(x.get("id") or x.get("name")) for x in asset_chars if x.get("id") or x.get("name")}
        for raw in actor_refs:
            ident = str(raw).split(":", 1)[-1].lstrip("@").strip()
            if not ident or ident in known_refs:
                continue
            board_actor = actors.get(ident) if isinstance(actors, dict) else None
            if isinstance(board_actor, dict):
                asset_chars.append({
                    "ref": "@character:" + ident,
                    "id": ident,
                    "name": board_actor.get("name") or ident,
                    "path": board_actor.get("path") or "",
                })
                known_refs.add(ident)
    if asset_scene and not structured_shot.get("scene_ref"):
        structured_shot["scene_ref"] = str(asset_scene.get("id") or asset_scene.get("name") or "")
    if asset_props and not regenerated and not structured_shot.get("prop_refs"):
        structured_shot["prop_refs"] = [
            str(x.get("id") or x.get("name")) for x in asset_props
            if x.get("id") or x.get("name")
        ]
    if not structured_shot.get("action") and structured_shot.get("prompt"):
        structured_shot["action"] = structured_shot.get("prompt")
    assets_for_prompt = {
        "characters": asset_chars + narrator_records,
        "scene": asset_scene,
        "props": asset_props,
    }
    try:
        from shot_prompt import build_shot_prompt, render_prompt_text
        prompt_json = build_shot_prompt(
            structured_shot,
            assets_for_prompt,
            media_type=str(project.get("media_type") or "image") if isinstance(project, dict) else "image",
            style_ref=("@style:" + image_skill) if image_skill else None,
        )
        prompt = render_prompt_text(prompt_json)
        if str(project.get("media_type") or "image").lower() == "image":
            prompt = strip_video_only_lines(prompt)
            if "输出单张关键帧" not in prompt:
                prompt = (prompt + "\n" if prompt else "") + "输出单张关键帧，保留本镜最终姿态和视线。"
        if regenerated:
            prompt = str(prompt_json.get("prompt_text") or "").strip()
            if str(project.get("media_type") or "image").lower() == "image":
                prompt = strip_video_only_lines(prompt)
                if "输出单张关键帧" not in prompt:
                    prompt = (prompt + "\n" if prompt else "") + "输出单张关键帧，保留本镜最终姿态和视线。"
            # 单张图允许只出现本镜的一部分角色；不能把未入画者补回提示词。
            prompt_json["asset_refs"] = [ref for ref in prompt_json.get("asset_refs") or [] if ref in prompt]
    except Exception:
        # 旧数据缺少镜头 id 等字段时仍保留最小可用文本，便于用户修复后重组装。
        prompt_json = None
        prompt = str(
            structured_shot.get("action")
            or structured_shot.get("content")
            or structured_shot.get("prompt")
            or ""
        ).strip()

    media_type = str(project.get("media_type") or "image").lower() if isinstance(project, dict) else "image"
    content = str(shot.get("content") or "").strip()
    lighting = str(shot.get("lighting") or shot.get("light") or "").strip()
    sound = str(shot.get("sound") or shot.get("audio") or "").strip()
    lines = [_line_text(x) for x in (shot.get("lines") or [])]
    line_text = "；".join(x for x in lines if x)
    camera = "；".join(x for x in [
        str(shot.get("shot_size") or shot.get("fov") or "").strip(),
        str(shot.get("camera_move") or shot.get("move") or "").strip(),
        str(shot.get("rig") or "").strip(),
        str(shot.get("lens") or "").strip(),
        str(shot.get("angle") or "").strip(),
        _camera_view(shot),
    ] if x)
    knowledge_text = " ".join(x for x in [
        content, str(shot.get("action") or shot.get("prompt") or ""), line_text, camera
    ] if x)
    hits = _knowledge(knowledge_text)
    # 仅追加镜头动作/声音等旧字段，避免把知识库或资产设定再次展开成外观描述。
    additions = []
    if content and content not in prompt:
        additions.append("剧情内容：" + content)
    if camera and "镜头" not in prompt:
        additions.append("镜头执行：" + camera)
    if lighting and "光线：" not in prompt:
        additions.append("光线：" + lighting)
    if media_type == "video" and sound and "声音与氛围：" not in prompt:
        additions.append("声音与氛围：" + sound)
    if media_type == "video" and line_text and "台词" not in prompt:
        additions.append("台词：" + line_text)
    if image_style_text and image_style_text not in prompt:
        additions.append("图像风格技能指令：" + image_style_text)
    if additions and not regenerated:
        prompt = "\n".join([prompt] + additions) if prompt else "\n".join(additions)
    negative_parts = [BASE_NEG]
    skill_neg = _skill_negative(image_skill)
    if skill_neg:
        negative_parts.append(skill_neg)
    return {
        "prompt_assembled": prompt,
        "prompt_json": prompt_json,
        "asset_refs": list((prompt_json or {}).get("asset_refs") or []),
        "negative": ",".join(dict.fromkeys(
            x.strip() for p in negative_parts for x in p.split(",") if x.strip()
        )),
        "knowledge_hits": [h.get("skill") for h in hits if h.get("skill")],
        "style_anchor": "",  # 保留空键以兼容已有消费方
        "image_skill": image_skill,
        "asset_context": {
            "characters": [_asset_summary("character", x) for x in asset_chars],
            "scene": _asset_summary("scene", asset_scene) if asset_scene else None,
            "props": [_asset_summary("prop", x) for x in asset_props],
        },
    }


def _existing(project_dir, rel):
    path = os.path.realpath(os.path.join(project_dir, rel.replace("/", os.sep)))
    root = os.path.realpath(project_dir)
    try:
        inside = os.path.commonpath([root, path]) == root
    except ValueError:
        inside = False
    return path if inside and os.path.isfile(path) else None


def _rel(project_dir, path):
    return os.path.relpath(path, project_dir).replace(os.sep, "/")


def _asset_ref(project_dir, zone, record):
    """按资产提炼记录、资产图索引和约定目录解析图片。"""
    if not isinstance(record, dict):
        return None
    raw_paths = [record.get("path"), record.get("image")]
    for raw in raw_paths:
        if not raw:
            continue
        raw = str(raw).strip()
        if os.path.isabs(raw):
            real = os.path.realpath(raw)
            try:
                found = real if os.path.commonpath([os.path.realpath(project_dir), real]) == os.path.realpath(project_dir) and os.path.isfile(real) else None
            except ValueError:
                found = None
        else:
            found = _existing(project_dir, raw)
        if found:
            return found
    stems = [record.get("id"), record.get("name")]
    for stem in stems:
        if not stem:
            continue
        for ext in (".png", ".jpg", ".jpeg", ".webp"):
            found = _existing(project_dir, f"素材/{zone}/{str(stem).strip()}{ext}")
            if found:
                return found
    return None


def _scene_ref(project_dir, scene, record=None):
    if isinstance(scene, dict) and record is None:
        record, scene = scene, scene.get("name") or scene.get("id")
    if isinstance(record, dict):
        found = _asset_ref(project_dir, "场景", record)
        if found:
            return found
    if not scene:
        return None
    safe = str(scene).strip()
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        found = _existing(project_dir, f"素材/场景/{safe}{ext}")
        if found:
            return found
    index = _asset_index(project_dir)
    wanted = _canon(safe)
    for zone in ("场景", "scene", "scenes"):
        zone_data = index.get(zone)
        items = zone_data.items() if isinstance(zone_data, dict) else []
        for key, rec in items:
            if _canon(key) == wanted or (isinstance(rec, dict) and (_canon(rec.get("id")) == wanted or _canon(rec.get("name")) == wanted)):
                found = _asset_ref(project_dir, "场景", dict(rec or {}, id=rec.get("id") or key) if isinstance(rec, dict) else {"id": key})
                if found:
                    return found
    return None


def reference_shot_for_prompt(shot, prompt, media_type="image"):
    """重写后的提示词以画面中实际出现的 @ 引用为准，过滤下一动作的参考图。"""
    if not isinstance(shot, dict):
        return {}
    source = shot.get("prompt_video_source") if media_type == "video" else shot.get("prompt_source")
    if source != "regenerated":
        return shot
    text = str(prompt or "")
    return {
        "id": shot.get("id"),
        "prompt": text,
        "scene": shot.get("scene"),
        "scene_ref": shot.get("scene_ref"),
        # 重写提示词只过滤画外角色，不丢失用户显式绑定的剧情参考帧。
        "reference_frames": shot.get("reference_frames"),
        "references": shot.get("references"),
        "reference_refs": shot.get("reference_refs"),
        "layout_ref": shot.get("layout_ref"),
        "actor_refs": re.findall(r"@character:[\w-]+", text),
        "prop_refs": re.findall(r"@prop:[\w-]+", text),
        "_visible_actor_ids": [ref.split(":", 1)[1] for ref in re.findall(r"@character:[\w-]+", text)],
    }


def _board_episode(board_name):
    """从分镜文件名（剧本_E1.json）解析当前集号（E1）；解析不出返回 None。"""
    import re as _re
    m = _re.search(r"(E\d+)", str(board_name or ""))
    return m.group(1) if m else None


def _state_ref_for_board(project_dir, zone, rec, board_name):
    """按集号选状态图：索引 states 里 episodes 含当前集且文件存在的那张；无匹配返回 None。"""
    ep = _board_episode(board_name)
    if not ep:
        return None
    idx = _asset_index(project_dir)
    entry = (idx.get(zone) or {}).get(str(rec.get("id") or ""))
    if not isinstance(entry, dict):
        return None
    states = entry.get("states")
    if not isinstance(states, dict):
        return None
    for sid, meta in states.items():
        if not isinstance(meta, dict):
            continue
        eps = [str(x) for x in (meta.get("episodes") or [])]
        if ep in eps:
            path = os.path.join(project_dir, str(meta.get("path") or ""))
            if os.path.isfile(path):
                return path
    return None


def resolve_shot_refs(shot, project_dir, actors=None, board_name=None, max_refs=10, board=None, media_type="image"):
    """按构图、角色身份、场景地理的优先级返回项目相对引用。"""
    shot = shot if isinstance(shot, dict) else {}
    project_dir = _project_dir(project_dir)
    out, seen = [], set()

    def add(path, purpose, metadata=None):
        if path and path not in seen:
            seen.add(path)
            row = {"path": _rel(project_dir, path), "purpose": purpose}
            if isinstance(metadata, dict):
                for key in ("reference_role", "target_time_seconds", "usage", "label"):
                    if metadata.get(key) is not None:
                        row[key] = metadata[key]
            out.append(row)

    # 参考帧必须由镜头显式选择；不扫描白模预演包或平面图目录。
    for row in explicit_reference_rows(shot):
        found, normalized = resolve_reference_path(project_dir, row)
        if found and normalized:
            add(found, normalized.get("purpose") or "剧情参考帧", normalized)

    # 若镜头已保存 shot-prompt-v1，优先按结构化 @ 引用解析真实资产图。
    structured = shot.get("prompt_json") if isinstance(shot.get("prompt_json"), dict) else {}
    structured_refs = structured.get("asset_refs") if isinstance(structured.get("asset_refs"), list) else []
    if structured_refs:
        try:
            from asset_registry import AssetRegistry
            registry = AssetRegistry(project_dir)
            ordered_refs = sorted(structured_refs, key=lambda ref: {"scene": 0, "character": 1, "prop": 2, "style": 3}.get(str(ref or "").lstrip("@").split(":", 1)[0], 4))
            for ref in ordered_refs:
                raw = str(ref or "")
                if is_narrator(raw):
                    continue
                kind = raw.split(":", 1)[0].lstrip("@")
                purpose = "身份锚点" if kind == "character" else ("地理锚点" if kind == "scene" else ("道具锚点" if kind == "prop" else "画风"))
                try:
                    row = registry.resolve(raw)
                    if kind == "character" and is_narrator(raw, row):
                        continue
                    add(_existing(project_dir, row.get("path")), purpose)
                except Exception:
                    pass
        except Exception:
            pass

    actors = actors if isinstance(actors, dict) else {}
    asset_ctx = _match_assets(shot, project_dir, actors=actors, board=board)
    matched_chars = asset_ctx.get("characters") or []
    visible_ids = shot.get("_visible_actor_ids")
    if isinstance(visible_ids, list):
        allowed_ids = {_canon(x) for x in visible_ids}
        matched_chars = [rec for rec in matched_chars if _canon(rec.get("id")) in allowed_ids]
    matched_char_keys = {_canon(x.get("id")) for x in matched_chars if x.get("id")}

    matched_scene = asset_ctx.get("scene")
    # 场景是跨镜头的地理锚点，优先于次要人物和道具进入参考图列表。
    if matched_scene:
        add(_scene_ref(project_dir, matched_scene), "地理锚点")
    matched_char_keys.update(_canon(x.get("name")) for x in matched_chars if x.get("name"))
    # 资产提炼人物图优先，确保即使 storyboard 角色 ID 被压缩为 v/h 也能回到真实人物。
    for rec in matched_chars:
        found = _asset_ref(project_dir, "人物", rec)
        if not found:
            try:
                from asset_refs import resolve_actor_ref
                found = resolve_actor_ref(project_dir, str(rec.get("id") or ""), str(rec.get("name") or ""))
            except Exception:
                found = None
        # 状态资产优先：角色在当前集有状态变体图（<id>__<state>.png）时，身份锚点改用状态版，
        # 保证"反派期引用反派形象、盟友期引用盟友形象"——时间维度不错位。
        state_hit = _state_ref_for_board(project_dir, "人物", rec, board_name)
        if state_hit:
            add(state_hit, "身份锚点(状态)")
        else:
            add(found, "身份锚点")
    ids = []
    for value in _explicit_values(shot, ("speaker", "host", "target", "focus", "actor", "actors", "characters", "character_ids")):
        if value and str(value) not in ids:
            ids.append(str(value))
    for line in shot.get("lines") or []:
        if isinstance(line, dict) and line.get("speaker") and str(line["speaker"]) not in ids:
            ids.append(str(line["speaker"]))
    if "c" in ids:
        ids.remove("c")
        ids.insert(0, "c")
    try:
        from asset_refs import resolve_actor_ref
        for aid in ids:
            if is_narrator(aid) or _canon(aid) in matched_char_keys:
                continue
            rec = actors.get(aid, {})
            if is_narrator(aid, rec):
                continue
            name = rec.get("name", "") if isinstance(rec, dict) else ""
            add(resolve_actor_ref(project_dir, aid, name), "身份锚点")
    except Exception:
        pass
    matched_props = asset_ctx.get("props") or []
    # 没有角色的物件特写优先保留道具图，再补场景图；有角色时仍按身份→地理→道具排序。
    if not matched_chars:
        for rec in matched_props:
            add(_asset_ref(project_dir, "道具", rec), "道具锚点")
    if not matched_scene:
        add(_scene_ref(project_dir, shot.get("scene") or shot.get("location")), "地理锚点")
    if matched_chars:
        for rec in matched_props:
            add(_asset_ref(project_dir, "道具", rec), "道具锚点")

    # MiniMax H3 只有 3 个参考输入位：保留场景 + 前两名具名角色，
    # 其余角色/道具仍然留在 prompt 的 @资产引用和动作描述中，不再让构图白模抢掉身份槽。
    if str(media_type or "").lower() == "video" and int(max_refs or 0) <= 3:
        scene_refs = [r for r in out if r.get("purpose") == "地理锚点"]
        char_refs = [r for r in out if r.get("purpose") == "身份锚点"]
        prop_refs = [r for r in out if r.get("purpose") == "道具锚点"]
        selected = []
        for row in scene_refs[:1] + char_refs[:2] + prop_refs:
            if row not in selected and len(selected) < int(max_refs or 3):
                selected.append(row)
        out = selected
    return out[:max_refs]


if __name__ == "__main__":
    print("该模块供 /api/create/assemble 与批量执行器调用")


