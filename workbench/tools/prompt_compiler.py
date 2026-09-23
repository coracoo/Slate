# -*- coding: utf-8 -*-
"""把对白分镜和演员表演草稿编译为可执行的生成提示词。

分镜字段仍是机位、时长、走位的唯一来源；演员层只能补充可见表演，不能覆盖镜头事实。
"""
import hashlib
import json
import copy
import sys
import os

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    from artifact_provenance import artifact_hash
except ImportError:
    _HERE = os.path.dirname(os.path.abspath(__file__))
    if _HERE not in sys.path:
        sys.path.insert(0, _HERE)
    try:
        from artifact_provenance import artifact_hash
    except ImportError:
        artifact_hash = None


def _actor_name(actors, actor_id):
    item = (actors or {}).get(actor_id, {})
    return item.get("name", actor_id) if isinstance(item, dict) else actor_id


def _actor_id_from_ref(value, actors=None, cards=None):
    """把 @character:id / 人物:id / 角色名统一解析为演员 id。"""
    raw = str(value or "").strip()
    if not raw:
        return ""
    token = raw[1:] if raw.startswith("@") else raw
    if ":" in token:
        head, ident = token.split(":", 1)
        if head.lower() in ("character", "actor", "人物", "角色"):
            token = ident.strip()
    token = token.strip()
    actors = actors if isinstance(actors, dict) else {}
    cards = cards if isinstance(cards, dict) else {}
    if token in actors or token in cards:
        return token
    needle = token.casefold()
    for actor_id, actor in actors.items():
        if isinstance(actor, dict):
            values = [actor_id, actor.get("name")]
            aliases = actor.get("aliases") or actor.get("alias") or []
            values.extend([aliases] if isinstance(aliases, str) else (aliases if isinstance(aliases, list) else []))
            if any(str(v).strip().casefold() == needle for v in values if v):
                return str(actor_id)
    return token


def _lines(shot, actors):
    rows = []
    for line in shot.get("lines") or []:
        speaker = _actor_name(actors, line.get("speaker", ""))
        text = str(line.get("line", line.get("text", "")) or "").strip()
        if text:
            rows.append(f"{speaker}：{text}")
    return rows


def _image_beats(beats):
    """图片模式只取一拍作为最终姿态（update.md E07 按角色取图）：

    优先取 ``time_role == "end"`` 的节拍；旧数据未标时间角色时维持取最后一拍的行为。
    只保留有效 dict 节拍，调用方不必再逐个判型。
    """
    beats = [beat for beat in beats if isinstance(beat, dict)]
    if not beats:
        return []
    marked = [beat for beat in beats if str(beat.get("time_role") or "").strip() == "end"]
    return [marked[-1] if marked else beats[-1]]


def _performance_text(performance, actors, media_type="video"):
    if not isinstance(performance, dict) or performance.get("status") not in (None, "ready"):
        return []
    packet = performance.get("packet") if isinstance(performance.get("packet"), dict) else performance
    rows = []
    for actor in packet.get("actors") or []:
        if not isinstance(actor, dict):
            continue
        name = _actor_name(actors, actor.get("actor_id", ""))
        beats = list(actor.get("beats") or [])
        if media_type == "image" and beats:
            # 单张关键帧只取该角色"结束状态"拍（time_role=end；未标角色时取末拍），避免把连续动作堆进静态图。
            beats = _image_beats(beats)
        for beat in beats:
            if not isinstance(beat, dict):
                continue
            at = beat.get("at", 0); dur = beat.get("duration", 0)
            parts = [str(beat.get(k)).strip() for k in ("intent", "posture", "gaze", "gesture", "expression", "voice")
                     if beat.get(k)]
            if parts:
                rows.append(f"{at}s-{float(at or 0)+float(dur or 0):g}s {name}：" + "；".join(parts))
    return rows


def _actor_card_text(shot, board):
    context = board.get("acting_context") if isinstance(board.get("acting_context"), dict) else {}
    cards = context.get("actor_cards") if isinstance(context.get("actor_cards"), dict) else {}
    if not cards:
        return []
    ids = []
    actors = board.get("actors") or {}
    for value in [shot.get("speaker"), shot.get("host"), shot.get("target"), shot.get("focus")]:
        actor_id = _actor_id_from_ref(value, actors, cards)
        if actor_id and actor_id not in ids:
            ids.append(actor_id)
    for line in shot.get("lines") or []:
        if isinstance(line, dict):
            actor_id = _actor_id_from_ref(line.get("speaker"), actors, cards)
            if actor_id and actor_id not in ids:
                ids.append(actor_id)
    for actor_id in (shot.get("staging") or {}):
        actor_id = _actor_id_from_ref(actor_id, actors, cards)
        if actor_id and actor_id not in ids:
            ids.append(actor_id)
    # 分镜提示词契约优先使用显式 actor_refs；不能因缺少 speaker 等字段而把全组演员卡混入本镜。
    refs = shot.get("actor_refs") or shot.get("character_refs") or shot.get("cast") or []
    if isinstance(refs, str):
        refs = [refs]
    if isinstance(refs, (list, tuple, set)):
        for value in refs:
            actor_id = _actor_id_from_ref(value, actors, cards)
            if actor_id and actor_id not in ids:
                ids.append(actor_id)
    if not ids:
        ids = [str(key) for key in cards]
    rows = []
    for actor_id in ids:
        card = cards.get(actor_id)
        if not isinstance(card, dict):
            continue
        name = _actor_name(board.get("actors") or {}, actor_id)
        parts = []
        for key in ("personality", "goal", "expression_rules", "behavior", "rules"):
            value = str(card.get(key) or "").strip()
            if value and value not in parts:
                parts.append(value)
        if parts:
            rows.append(name + "：" + "；".join(parts))
    return rows

def compile_shot(board, shot_id, mode="baseline", media_type="video", supports_audio=False):
    """返回提示词、结构化 shot-prompt-v1、资产引用和来源 hash。"""
    if not isinstance(board, dict):
        raise ValueError("分镜必须是对象")
    shot = next((s for s in board.get("shots") or [] if str(s.get("id")) == str(shot_id)), None)
    if not shot:
        raise ValueError(f"找不到镜头：{shot_id}")
    actors = board.get("actors") or {}
    lines = _lines(shot, actors)
    selected_prompt = ((shot.get("prompt_video") or shot.get("action") or shot.get("content") or shot.get("prompt"))
                       if media_type == "video" else (shot.get("prompt_image") or shot.get("prompt")))
    raw_prompt = str(selected_prompt or "").strip()
    regenerated = (shot.get("prompt_video_source") if media_type == "video" else (shot.get("prompt_image_source") or shot.get("prompt_source"))) in ("regenerated", "llm", "authored")
    # prompt 可能是旧版已装配文本；重新编译时只取镜头动作，避免把旧的资产/画风/演员段落再次套入。
    generated_markers = ("资产引用：", "画风锚定：", "图像风格技能指令：", "演员角色卡", "输出单张关键帧")
    clean_action = str(shot.get("action") or shot.get("content") or "").strip()
    if not regenerated and any(marker in raw_prompt for marker in generated_markers) and clean_action:
        base = clean_action
    else:
        base = raw_prompt or clean_action
    if not base:
        base = "保持分镜中的角色、场景和动作连续性。"
    sections = [base]
    if shot.get("scene"):
        sections.append(f"场景：{shot.get('scene')}")
    if shot.get("move"):
        sections.append(f"镜头运动：{shot.get('move')}")
    if lines:
        sections.append("台词：" + "；".join(lines))
    baseline_prompt = "\n".join(sections)
    warnings = []
    source_board = copy.deepcopy(board)
    for source_shot in source_board.get("shots") or []:
        if str(source_shot.get("id")) == str(shot_id):
            source_shot.pop("performance", None)
            break
    current_source = artifact_hash(source_board, "prompt", "actor-v1") if artifact_hash else ""
    performance = shot.get("performance")
    performance_fresh = isinstance(performance, dict) and performance.get("status") == "ready" and (
        not performance.get("source_hash") or performance.get("source_hash") == current_source)
    performance_mode = str(performance.get("mode") or "stateful") if isinstance(performance, dict) else ""
    use_performance = performance_fresh and ((mode in ("stateful", "actor", "performance")) or (mode == "style" and performance_mode == "style"))
    if (mode in ("stateful", "actor", "performance") or (mode == "style" and performance_mode == "style")) and isinstance(performance, dict) and performance.get("status") == "ready" and not performance_fresh:
        warnings.append("演员表演候选来源已过期，已回退基础提示词")
    performance_rows = _performance_text(performance, actors, media_type) if use_performance else []
    actor_card_rows = []
    if mode in ("stateful", "actor", "performance"):
        actor_card_rows = _actor_card_text(shot, board)
        if actor_card_rows:
            sections.append("演员角色卡（只用于指导可见表演，不改变镜头事实）：" + "；".join(actor_card_rows))
    if performance_rows:
        sections.append("演员表演节拍（只补充可见表演，不改变机位/走位/台词）：" + "；".join(performance_rows))
    if media_type == "image":
        sections.append("输出单张关键帧，保留本镜最终姿态和视线。")
    else:
        sections.append("输出连续动作，严格保持本镜时长内的节奏和角色身份。")

    # 演员层和图像层消费同一份结构化资产引用，角色卡不再复制外观档案。
    actor_ids = []
    for value in [shot.get("speaker"), shot.get("host"), shot.get("target"), shot.get("focus")]:
        actor_id = _actor_id_from_ref(value, actors)
        if actor_id and actor_id not in actor_ids:
            actor_ids.append(actor_id)
    for line in shot.get("lines") or []:
        if isinstance(line, dict):
            actor_id = _actor_id_from_ref(line.get("speaker"), actors)
            if actor_id and actor_id not in actor_ids:
                actor_ids.append(actor_id)
    for actor_id in (shot.get("staging") or {}):
        actor_id = _actor_id_from_ref(actor_id, actors)
        if actor_id and actor_id not in actor_ids:
            actor_ids.append(actor_id)
    refs = shot.get("actor_refs") or shot.get("character_refs") or shot.get("cast") or []
    if isinstance(refs, str):
        refs = [refs]
    if isinstance(refs, (list, tuple, set)):
        for value in refs:
            actor_id = _actor_id_from_ref(value, actors)
            if actor_id and actor_id not in actor_ids:
                actor_ids.append(actor_id)
    if not actor_ids and isinstance(actors, dict):
        actor_ids = [str(key) for key in actors]
    action_by_actor = {}
    if isinstance(performance, dict) and isinstance(performance.get("packet"), dict):
        for actor in performance["packet"].get("actors") or []:
            if not isinstance(actor, dict) or not actor.get("actor_id"):
                continue
            beats = list(actor.get("beats") or [])
            if media_type == "image" and beats:
                beats = _image_beats(beats)
            actions = []
            for beat in beats:
                if not isinstance(beat, dict):
                    continue
                action = beat.get("visible_action") or beat.get("gesture") or beat.get("posture") or beat.get("expression")
                if action:
                    actions.append(str(action).strip())
            if actions:
                action_by_actor[str(actor["actor_id"])] = "；".join(actions)
    structured_shot = copy.deepcopy(shot)
    structured_shot["prompt"] = base
    structured_shot["prompt_text"] = base
    structured_shot["actor_refs"] = actor_ids
    structured_shot["action_by_actor"] = action_by_actor
    structured_shot.setdefault("action", base)
    structured_shot.setdefault("scene_ref", shot.get("scene") or "room")
    try:
        from shot_prompt import build_shot_prompt, render_prompt_text
        asset_chars = []
        for actor_id in actor_ids:
            record = actors.get(actor_id, {}) if isinstance(actors, dict) else {}
            name = record.get("name") if isinstance(record, dict) else actor_id
            actor_ref = actor_id if str(actor_id).startswith("@character:") else "@character:" + str(actor_id)
            actor_ident = str(actor_ref).split(":", 1)[-1]
            asset_chars.append({"ref": actor_ref, "id": actor_ident, "name": name})
        props = []
        for key in ("prop_refs", "props", "prop_ids", "props_used"):
            values = shot.get(key)
            if not isinstance(values, (list, tuple, set)):
                values = [values] if values else []
            for value in values:
                if isinstance(value, dict):
                    value = value.get("ref") or value.get("id") or value.get("name")
                if value:
                    raw_value = str(value)
                    ident = raw_value.split(":", 1)[-1].lstrip("@")
                    prop_ref = raw_value if raw_value.startswith("@prop:") else "@prop:" + ident
                    props.append({"ref": prop_ref, "id": ident, "name": ident})
        scene_value = str(structured_shot["scene_ref"])
        scene_ref = scene_value if scene_value.startswith("@scene:") else "@scene:" + scene_value
        scene_ident = scene_ref.split(":", 1)[-1]
        prompt_json = build_shot_prompt(
            structured_shot,
            {"characters": asset_chars, "scene": {"ref": scene_ref, "id": scene_ident},
             "props": props},
            media_type=media_type,
        )
        structured_text = render_prompt_text(prompt_json)
    except Exception as exc:
        prompt_json = None
        structured_text = baseline_prompt
        warnings.append("shot-prompt-v1 编译降级：" + str(exc))
    # 保留演员角色卡/节拍等可见行为信息，结构化引用作为提示词的第一层。
    prompt_sections = [structured_text]
    if actor_card_rows:
        prompt_sections.append("演员角色卡（只用于指导可见表演，不改变镜头事实）：" + "；".join(actor_card_rows))
    if performance_rows:
        prompt_sections.append("演员表演节拍（只补充可见表演，不改变机位/走位/台词）：" + "；".join(performance_rows))
    if media_type == "image":
        prompt_sections.append("输出单张关键帧，保留本镜最终姿态和视线。")
    else:
        prompt_sections.append("输出连续动作，严格保持本镜时长内的节奏和角色身份。")
    source = current_source
    if mode == "style" and performance_rows:
        mode_used = "style"
    elif performance_rows or actor_card_rows:
        mode_used = "stateful"
    else:
        mode_used = "style" if mode == "style" else "baseline"
    voice_notes = []
    packet = performance.get("packet") if isinstance(performance, dict) and isinstance(performance.get("packet"), dict) else {}
    for actor in packet.get("actors") or []:
        beats = list(actor.get("beats") or [])
        if media_type == "image" and beats:
            beats = _image_beats(beats)
        for beat in beats:
            if isinstance(beat, dict) and beat.get("voice"):
                voice_notes.append(str(beat.get("voice")).strip())
    if voice_notes and not supports_audio:
        warnings.append("当前出口未声明音频能力，配音提示仅作为 voice_notes 保留")
    prompt = "\n".join(sections if prompt_json is None else prompt_sections)
    return {"shot_id": str(shot_id), "mode": mode_used, "mode_used": mode_used,
            "media_type": media_type, "prompt": prompt, "text": prompt,
            "baseline_prompt": baseline_prompt, "prompt_json": prompt_json,
            "asset_refs": list((prompt_json or {}).get("asset_refs") or []),
            "voice_notes": voice_notes, "warnings": warnings, "source_hash": source,
            "performance_used": bool(performance_rows)}

_STAGE_SYSTEM = {
    "script": "你是短剧编剧。只整理剧情事实、场景、人物关系和动作，不改变原剧本意图，输出结构化剧本内容。",
    "storyboard_image": "你是分镜参考图提示词编译器。输出一张单一静态关键帧，明确构图、空间关系、最终姿态和视线；不描述连续动作过程，不输出对白框、字幕或文字。",
    "video": "你是生视频提示词编译器。输出连续动作，明确动作起点、过程、终点、时序、镜头运动和转场；保持资产身份、空间关系和镜头事实连续。",
    "asset_image": "你是资产设定图提示词编译器。只生成当前母素材或子素材，保持父级身份和结构锚点，不加入无关角色、动作或场景。",
}


def _stage_refs(shot):
    refs = []
    for value in (shot.get("asset_refs") or shot.get("refs") or []):
        if isinstance(value, dict):
            value = value.get("ref") or value.get("asset_ref")
        value = str(value or "").strip()
        if value and value not in refs:
            refs.append(value if value.startswith("@") else "@" + value)
    for value in (shot.get("actor_refs") or shot.get("character_refs") or []):
        value = str(value or "").strip()
        if value:
            ref = value if value.startswith("@") else "@character:" + value
            if ref not in refs:
                refs.append(ref)
    for value in (shot.get("prop_refs") or shot.get("props_refs") or shot.get("prop_ids") or shot.get("props_used") or []):
        if isinstance(value, dict):
            value = value.get("ref") or value.get("asset_ref") or value.get("id") or value.get("name")
        value = str(value or "").strip()
        if value:
            ref = value if value.startswith("@") else "@prop:" + value
            if ref not in refs:
                refs.append(ref)
    scene = str(shot.get("scene_ref") or "").strip()
    if scene:
        ref = scene if scene.startswith("@scene:") else "@scene:" + scene
        if ref not in refs:
            refs.append(ref)
    return refs


def _stage_board(project_dir, shot, refs):
    """构造 assembler 所需的最小 board，不把资产长描述塞进镜头。"""
    actors = {}
    try:
        with open(os.path.join(project_dir, "素材", "人物.json"), encoding="utf-8") as fh:
            rows = json.load(fh).get("characters", [])
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict) and row.get("id"):
                actors[str(row["id"])] = row
    except (OSError, ValueError, TypeError):
        pass
    return {"actors": actors, "set": {"scene": next((r for r in refs if r.startswith("@scene:")), "")}}


def compile_stage_prompt(stage, shot, project_dir, mode="generate", board=None):
    """统一编译剧本/静态参考图/生视频/资产图阶段提示词。

    返回系统提示词、剧情提示词、负面提示词、资产引用及其当前修订号。
    """
    stage = str(stage or "").strip().lower()
    if stage not in _STAGE_SYSTEM:
        raise ValueError(f"不支持的提示词阶段：{stage}")
    mode = str(mode or "generate").strip().lower()
    if mode not in ("generate", "edit"):
        raise ValueError(f"不支持的提示词模式：{mode}")
    if not isinstance(shot, dict):
        raise ValueError("镜头必须是对象")
    project_dir = os.path.abspath(str(project_dir or ""))
    refs = _stage_refs(shot)
    try:
        from production_state import asset_dependency_refs
        dependency_refs = asset_dependency_refs(project_dir, refs)
    except Exception:
        dependency_refs = list(refs)
    for ref in dependency_refs:
        if ref not in refs:
            refs.append(ref)
    if stage == "script":
        content = str(shot.get("text") or shot.get("content") or shot.get("action") or "").strip()
        return {"system_prompt": _STAGE_SYSTEM[stage], "content_prompt": content,
                "negative_prompt": "", "asset_refs": refs, "asset_revisions": {}, "text": content}

    media_type = "image" if stage in ("storyboard_image", "asset_image") else "video"
    from prompt_assembler import assemble_shot_prompt
    normalized = dict(shot)
    normalized["asset_refs"] = refs
    normalized["actor_refs"] = [r.split(":", 1)[1] for r in refs if r.startswith("@character:")]
    normalized["prop_refs"] = [r.split(":", 1)[1] for r in refs if r.startswith("@prop:")]
    scene_ref = next((r for r in refs if r.startswith("@scene:")), "")
    if scene_ref:
        normalized["scene_ref"] = scene_ref
    project = {"dir": project_dir, "media_type": media_type,
               "board": board if isinstance(board, dict) else _stage_board(project_dir, normalized, refs)}
    assembled = assemble_shot_prompt(normalized, project)
    all_refs = list(refs)
    revisions = {}
    try:
        from asset_registry import AssetRegistry
        rows = {row.get("ref"): row for row in AssetRegistry(project_dir).list()}
        revisions = {ref: int(rows[ref].get("asset_revision") or 1) for ref in all_refs if ref in rows}
    except Exception:
        revisions = {}
    system = _STAGE_SYSTEM[stage]
    if mode == "edit":
        system += " 当前为改图模式：必须基于参考图修改，保留未要求改变的主体、构图和画风。"
    content = str(assembled.get("prompt_assembled") or "").strip()
    asset_refs = []
    for ref in refs + list(assembled.get("asset_refs") or []):
        if ref not in asset_refs:
            asset_refs.append(ref)
    revision_source = json.dumps({"stage": stage, "mode": mode, "content": content,
                                  "negative": assembled.get("negative") or "",
                                  "asset_refs": asset_refs, "asset_revisions": revisions},
                                 ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    prompt_revision = "sha256:" + hashlib.sha256(revision_source.encode("utf-8")).hexdigest()
    return {"system_prompt": system, "content_prompt": content,
            "negative_prompt": str(assembled.get("negative") or ""),
            "asset_refs": asset_refs, "asset_revisions": revisions,
            "text": content, "prompt_json": assembled.get("prompt_json"),
            "asset_context": assembled.get("asset_context") or {},
            "mode": mode, "stage": stage, "prompt_revision": prompt_revision}


__all__ = ["compile_shot", "compile_stage_prompt"]











