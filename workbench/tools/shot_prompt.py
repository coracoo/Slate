# -*- coding: utf-8 -*-
"""shot-prompt-v1 的结构化构造、校验与文本编译。

资产在此层只以 @character/@scene/@prop/@style 引用出现；资产外观、设定图
和场景概念图描述属于资产生成阶段，永远不会自动展开到单镜提示词。
"""
import json
import re
import sys
from narration import is_narrator

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_REF_RE = re.compile(r"^@(character|scene|prop|style):[^:\s]+$")
_BASE_NEGATIVE = ["文字", "水印", "边框", "三视图", "角色设定图", "额外动作阶段"]
_EXTRAS_QUALITY = "多人/群演质量提示：路人和配角的五官、发型、体型、服饰与表情彼此有明显差异，符合当前场景与时代；保持背景层级，不复制脸、不复制姿态、不抢主角。"


def _canon(value):
    return "".join(ch.lower() for ch in str(value or "") if ch.isalnum())


def _ref(value, kind="character"):
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.startswith("@"):
        tagged = raw[1:]
        if ":" in tagged:
            head, ident = tagged.split(":", 1)
            mapped = {"人物": "character", "角色": "character", "actor": "character",
                      "场景": "scene", "location": "scene",
                      "道具": "prop", "object": "prop",
                      "画风": "style"}.get(head)
            if mapped:
                return "@" + mapped + ":" + ident.strip()
        return raw
    if ":" in raw:
        head, ident = raw.split(":", 1)
        if head in ("人物", "角色", "character", "actor"):
            return "@character:" + ident.strip()
        if head in ("场景", "scene", "location"):
            return "@scene:" + ident.strip()
        if head in ("道具", "prop", "object"):
            return "@prop:" + ident.strip()
        if head in ("画风", "style"):
            return "@style:" + ident.strip()
    return "@" + kind + ":" + raw


def _records(assets, key):
    if not isinstance(assets, dict):
        return []
    value = assets.get(key)
    if isinstance(value, dict):
        value = [value]
    return [dict(item) for item in (value or []) if isinstance(item, dict)]


def _record_ref(record, kind):
    if not isinstance(record, dict):
        return ""
    return _ref(record.get("ref") or record.get("asset") or record.get("id") or record.get("name"), kind)


def _find_record(value, records, kind):
    needle = _canon(value)
    if not needle:
        return None
    for record in records:
        values = [record.get("id"), record.get("name"), record.get("ref"), record.get("asset")]
        aliases = record.get("aliases") or record.get("alias") or []
        if isinstance(aliases, str):
            aliases = [aliases]
        values.extend(aliases if isinstance(aliases, list) else [])
        if any(_canon(item) == needle or _canon(_ref(item, kind)) == needle for item in values if item):
            return record
    return None


def _explicit_list(shot, keys):
    values = []
    for key in keys:
        value = shot.get(key)
        if isinstance(value, (list, tuple, set)):
            values.extend(value)
        elif value:
            values.append(value)
    return [item for item in values if isinstance(item, str) and item.strip()]


def _action_for(shot, asset):
    actions = shot.get("action_by_actor") or shot.get("actor_actions") or {}
    if not isinstance(actions, dict):
        return ""
    candidates = [asset, asset.split(":", 1)[-1] if ":" in asset else asset]
    for key in candidates:
        if key in actions:
            return str(actions[key] or "").strip()
    return ""


def _actor_entries(shot, assets):
    records = _records(assets, "characters")
    raw = shot.get("actors")
    entries = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                asset = item.get("asset") or item.get("ref") or item.get("actor") or item.get("id") or item.get("name")
                if asset:
                    entries.append({"asset": _ref(asset, "character"), "action": str(item.get("action") or "").strip(),
                                    **({"target": _ref(item["target"], "character")} if item.get("target") else {})})
            elif item:
                entries.append({"asset": _ref(item, "character"), "action": ""})
    refs = _explicit_list(shot, ("actor_refs", "character_refs", "cast"))
    if refs:
        for item in refs:
            asset = _ref(item, "character")
            if not any(row["asset"] == asset for row in entries):
                entries.append({"asset": asset, "action": _action_for(shot, asset)})
    if not entries:
        ids = []
        for key in ("speaker", "host", "target", "focus"):
            value = shot.get(key)
            if value and str(value) not in ids:
                ids.append(str(value))
        for line in shot.get("lines") or []:
            if isinstance(line, dict) and line.get("speaker") and str(line["speaker"]) not in ids:
                ids.append(str(line["speaker"]))
        for actor_id in ids:
            asset = _ref(actor_id, "character")
            entries.append({"asset": asset, "action": _action_for(shot, asset)})
    # 如果有角色动作表但没有显式 actor_refs，用动作表 key 补齐角色。
    if not entries:
        actions = shot.get("action_by_actor") or shot.get("actor_actions") or {}
        if isinstance(actions, dict):
            for actor_id, action in actions.items():
                asset = _ref(actor_id, "character")
                entries.append({"asset": asset, "action": str(action or "").strip()})
    entries = [entry for entry in entries if not is_narrator(entry.get("asset"), _find_record(entry.get("asset"), records, "character"))]
    for entry in entries:
        if is_narrator(entry.get("target"), _find_record(entry.get("target"), records, "character")):
            entry.pop("target", None)
        record = _find_record(entry.get("asset"), records, "character")
        if record and record.get("name"):
            entry["name"] = str(record.get("name"))
    return entries


def _scene_entry(shot, assets):
    records = _records(assets, "scenes")
    value = shot.get("scene_ref") or shot.get("scene_asset")
    record = _find_record(value, records, "scene") if value else None
    if record is None and isinstance(assets, dict) and isinstance(assets.get("scene"), dict):
        record = assets["scene"]
    if record is None and shot.get("scene"):
        record = _find_record(shot.get("scene"), records, "scene")
    if record is None and len(records) == 1:
        record = records[0]
    asset = _record_ref(record, "scene") if record else (_ref(value, "scene") if value else "")
    if not asset:
        return None
    row = {"asset": asset, "action": str(shot.get("scene_action") or "").strip()}
    if isinstance(record, dict) and record.get("name"):
        row["name"] = str(record.get("name"))
    return row


def _prop_entries(shot, assets):
    records = _records(assets, "props")
    raw = shot.get("props")
    entries = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                asset = item.get("asset") or item.get("ref") or item.get("id") or item.get("name")
                action = str(item.get("action") or "").strip()
            else:
                asset, action = item, ""
            if asset:
                entries.append({"asset": _ref(asset, "prop"), "action": action})
    refs = _explicit_list(shot, ("prop_refs", "props_refs", "prop_ids", "props_used"))
    for item in refs:
        asset = _ref(item, "prop")
        if not any(row["asset"] == asset for row in entries):
            entries.append({"asset": asset, "action": ""})
    if not entries:
        actions = shot.get("action_by_prop") or {}
        if isinstance(actions, dict):
            entries = [{"asset": _ref(key, "prop"), "action": str(value or "").strip()} for key, value in actions.items()]
    for entry in entries:
        record = _find_record(entry.get("asset"), records, "prop")
        if record and record.get("name"):
            entry["name"] = str(record.get("name"))
    return entries


def _dialogue(shot, assets=None):
    rows = []
    for line in shot.get("lines") or shot.get("dialogue") or []:
        if not isinstance(line, dict):
            continue
        speaker = line.get("speaker") or line.get("actor")
        text = str(line.get("text") or line.get("line") or "").strip()
        if not speaker or not text:
            continue
        row = {"speaker": "narrator" if is_narrator(speaker) else _ref(speaker, "character"), "text": text}
        chars = _records(assets or {}, "characters")
        record = _find_record(speaker, chars, "character")
        if is_narrator(speaker, record):
            row["speaker"] = "narrator"
        if record and record.get("name"):
            row["name"] = str(record.get("name"))
        if line.get("at") is not None:
            row["at"] = line["at"]
        if line.get("dur") is not None:
            row["duration"] = line["dur"]
        rows.append(row)
    return rows


def _negative(shot):
    value = shot.get("negative")
    if isinstance(value, str):
        values = [item.strip() for item in value.split(",") if item.strip()]
    elif isinstance(value, list):
        values = [str(item).strip() for item in value if str(item).strip()]
    else:
        values = []
    return list(dict.fromkeys(_BASE_NEGATIVE + values))


def build_shot_prompt(shot, assets=None, *, media_type="image", style_ref=None):
    """从旧版单镜字段构造不展开资产描述的 shot-prompt-v1 对象。"""
    shot = shot if isinstance(shot, dict) else {}
    scene = _scene_entry(shot, assets or {})
    actors = _actor_entries(shot, assets or {})
    props = _prop_entries(shot, assets or {})
    style = style_ref or shot.get("style_ref") or shot.get("style_asset")
    prompt_text = str(shot.get("prompt_text") or shot.get("prompt") or "").strip()
    value = {
        "schema": "shot-prompt-v1",
        "shot_id": str(shot.get("id") or ""),
        "media_type": str(media_type or "image"),
        "camera": {
            "shot_size": str(shot.get("shot_size") or shot.get("fov") or "").strip(),
            "move": str(shot.get("camera_move") or shot.get("move") or "").strip(),
            "angle": str(shot.get("angle") or "").strip(),
            "rig": str(shot.get("rig") or "").strip(),
            "lens": str(shot.get("lens") or "").strip(),
            "pos": shot.get("pos") if isinstance(shot.get("pos"), (list, tuple)) else None,
            "look": shot.get("look") if isinstance(shot.get("look"), (list, tuple)) else None,
            "aspect_ratio": str(shot.get("aspect_ratio") or shot.get("ratio") or "16:9").strip(),
            "phase": str(shot.get("action_phase") or shot.get("phase") or ("动作结束帧" if media_type == "image" else "连续动作")).strip(),
        },
        "scene": scene,
        "actors": actors,
        "props": props,
        "dialogue": _dialogue(shot, assets or {}),
        "style": {"asset": _ref(style, "style")} if style else None,
        "negative": _negative(shot),
    }
    if prompt_text:
        value["prompt_text"] = prompt_text
    for key in ("content", "lighting", "sound"):
        if shot.get(key):
            value[key] = str(shot.get(key)).strip()
    if shot.get("prompt") or shot.get("action") or shot.get("content"):
        value["action"] = str(shot.get("action") or shot.get("content") or shot.get("prompt") or "").strip()
    value["asset_refs"] = []
    for row in ([scene] if scene else []) + actors + props + ([value["style"]] if value["style"] else []):
        if isinstance(row, dict) and row.get("asset") and row["asset"] not in value["asset_refs"]:
            value["asset_refs"].append(row["asset"])
    issues = validate_shot_prompt(value)
    if issues:
        raise ValueError("shot-prompt-v1 校验失败：" + "；".join(item["message"] for item in issues[:5]))
    return value


def validate_shot_prompt(value, registry=None):
    issues = []
    if not isinstance(value, dict):
        return [{"code": "INVALID_PROMPT", "path": "prompt", "message": "提示词必须是对象"}]
    if value.get("schema") != "shot-prompt-v1":
        issues.append({"code": "INVALID_SCHEMA", "path": "schema", "message": "提示词 schema 必须为 shot-prompt-v1"})
    if not str(value.get("shot_id") or "").strip():
        issues.append({"code": "MISSING_SHOT_ID", "path": "shot_id", "message": "缺少 shot_id"})
    camera = value.get("camera")
    if not isinstance(camera, dict):
        issues.append({"code": "INVALID_CAMERA", "path": "camera", "message": "camera 必须是对象"})
    elif not str(camera.get("aspect_ratio") or "").strip():
        issues.append({"code": "MISSING_ASPECT_RATIO", "path": "camera.aspect_ratio", "message": "缺少 aspect_ratio"})
    if value.get("media_type") not in ("image", "video"):
        issues.append({"code": "INVALID_MEDIA_TYPE", "path": "media_type", "message": "media_type 只能是 image/video"})
    for section, kind in (("scene", "scene"), ("actors", "character"), ("props", "prop")):
        rows = value.get(section)
        if section == "scene":
            rows = [rows] if rows else []
        if not isinstance(rows, list):
            issues.append({"code": "INVALID_ASSETS", "path": section, "message": f"{section} 必须是对象或数组"})
            continue
        for index, row in enumerate(rows):
            if not isinstance(row, dict) or not _REF_RE.match(str(row.get("asset") or "")):
                issues.append({"code": "INVALID_ASSET_REF", "path": f"{section}[{index}].asset", "message": "资产必须使用 @kind:id 引用"})
                continue
            ref = str(row["asset"])
            if not ref.startswith("@" + kind + ":"):
                issues.append({"code": "ASSET_KIND_MISMATCH", "path": f"{section}[{index}].asset", "message": f"{section} 引用了错误类型的资产"})
            if section != "scene" and "action" in row and not isinstance(row.get("action"), str):
                issues.append({"code": "INVALID_ACTION", "path": f"{section}[{index}].action", "message": "动作必须是文本"})
            if registry is not None:
                try:
                    registry.resolve(ref)
                except Exception as exc:
                    issues.append({"code": "UNKNOWN_ASSET", "path": f"{section}[{index}].asset", "message": str(exc)})
    for index, line in enumerate(value.get("dialogue") or []):
        if not isinstance(line, dict) or not (line.get("speaker") == "narrator" or _REF_RE.match(str(line.get("speaker") or ""))) or not str(line.get("text") or "").strip():
            issues.append({"code": "INVALID_DIALOGUE", "path": f"dialogue[{index}]", "message": "台词必须包含 @character 说话人和文本"})
    style = value.get("style")
    if style:
        if not isinstance(style, dict) or not str(style.get("asset") or "").startswith("@style:"):
            issues.append({"code": "INVALID_STYLE_REF", "path": "style.asset", "message": "画风必须使用 @style 引用"})
    refs = value.get("asset_refs")
    if refs is not None and (not isinstance(refs, list) or any(not _REF_RE.match(str(item)) for item in refs)):
        issues.append({"code": "INVALID_ASSET_REFS", "path": "asset_refs", "message": "asset_refs 必须是 @资产 数组"})
    return issues


def render_prompt_text(value):
    """把标准 JSON 编译为生图模型优先理解的自然语言提示词。

    ``prompt_text`` 是镜头提示词的第一层，JSON 字段只负责可审计和下游引用。
    这里仅补充镜头、动作和行为，不展开资产档案中的外观长描述。
    """
    issues = validate_shot_prompt(value)
    if issues:
        raise ValueError("无法编译无效 shot-prompt-v1：" + "；".join(item["message"] for item in issues[:5]))
    camera = value["camera"]
    prompt_text = str(value.get("prompt_text") or "").strip()
    rows = [prompt_text] if prompt_text else []
    ratio = str(camera.get("aspect_ratio") or "16:9")
    shot_size = str(camera.get("shot_size") or "中景")
    angle = str(camera.get("angle") or "平视")
    move = str(camera.get("move") or "固定")
    phase = str(camera.get("phase") or "动作结束帧")
    move_phrase = {"跟": "跟随", "固定": "固定"}.get(move, move + "镜头")
    phase_phrase = {"动作结束帧": "动作后的最终静帧", "连续动作": "连续动作"}.get(phase, phase)
    camera_line = f"{ratio} 横构图，{shot_size}，{angle}，{move_phrase}{phase_phrase}。"
    if not prompt_text or not all(token in prompt_text for token in (ratio, shot_size, angle)):
        rows.append(camera_line)
    camera_detail = [str(camera.get(key) or "").strip() for key in ("shot_size", "move", "rig", "lens", "angle") if str(camera.get(key) or "").strip()]
    if camera_detail and (not prompt_text or not all(token in prompt_text for token in (str(camera.get("rig") or "").strip(), str(camera.get("lens") or "").strip()) if token)):
        rows.append("镜头执行：" + "；".join(camera_detail))

    if value.get("action"):
        rows.append("本镜动作：" + str(value["action"]).strip())

    actors = value.get("actors") or []
    actor_names = []
    for actor in actors:
        if not isinstance(actor, dict):
            continue
        name = str(actor.get("name") or actor.get("asset") or "").strip()
        if name and name not in actor_names:
            actor_names.append(name)
    if actors and not all(str(actor.get("asset") or "") in prompt_text for actor in actors if isinstance(actor, dict)):
        labels = []
        for actor in actors:
            if not isinstance(actor, dict):
                continue
            asset = str(actor.get("asset") or "").strip()
            name = str(actor.get("name") or "").strip()
            if asset:
                labels.append(asset + (f"（{name}）" if name else ""))
        if labels:
            rows.append("画面同时包含" + "、".join(labels) + "。")
    for actor in actors:
        if not isinstance(actor, dict) or not actor.get("action"):
            continue
        name = str(actor.get("name") or actor.get("asset") or "角色").strip()
        line = f"{name}：{str(actor.get('action')).strip()}"
        if actor.get("target"):
            line += f"，目标 {actor['target']}"
        rows.append(line + "。")

    if len(actors) >= 3 or any(token in prompt_text for token in ("群演", "路人", "全班", "师生", "同学们")):

        rows.append(_EXTRAS_QUALITY)


    scene = value.get("scene")
    if isinstance(scene, dict) and scene.get("asset"):
        scene_name = str(scene.get("name") or scene.get("asset") or "场景").strip()
        scene_line = "背景是" + scene_name
        if scene.get("action"):
            scene_line += "，" + str(scene["action"]).strip()
        rows.append(scene_line + "。")
    for prop in value.get("props") or []:
        if not isinstance(prop, dict) or not prop.get("asset"):
            continue
        name = str(prop.get("name") or prop.get("asset") or "道具").strip()
        line = name + ("：" + str(prop["action"]).strip() if prop.get("action") else "")
        rows.append(line + "。")
    content = str(value.get("content") or "").strip()
    # LLM 已给出完整自然语言主提示词时，避免把同一剧情再重复一遍；
    # 存量短 prompt 则保留 content 作为补充事实。
    if content and content not in prompt_text and (not prompt_text or len(prompt_text) < 90):
        rows.append("剧情内容：" + content)
    if value.get("lighting"):
        rows.append("光线：" + str(value["lighting"]).strip())
    if value.get("sound"):
        rows.append("声音与氛围：" + str(value["sound"]).strip())
    for line in value.get("dialogue") or []:
        if not isinstance(line, dict) or not line.get("text"):
            continue
        label = str(line.get("name") or line.get("speaker") or "角色")
        if is_narrator(line.get("speaker")):
            rows.append("旁白仅用于后期配音，不作为画面角色：" + str(line["text"]).strip())
            continue
        rows.append("台词仅作表演参考，不生成对白框、字幕和文字：" + label + "：" + str(line["text"]).strip())
    if value.get("style") and value["style"].get("asset"):
        rows.append("画风引用：" + str(value["style"]["asset"]))
    refs = [str(ref) for ref in value.get("asset_refs") or [] if str(ref).strip()]
    if refs:
        rows.append("资产引用：" + "、".join(dict.fromkeys(refs)))
    if value.get("negative"):
        rows.append("禁止：" + "、".join(str(item) for item in value["negative"] if str(item).strip()))
    return "\n".join(row for row in rows if row.strip())


__all__ = ["build_shot_prompt", "validate_shot_prompt", "render_prompt_text"]



