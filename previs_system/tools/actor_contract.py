# -*- coding: utf-8 -*-
"""角色表演层的输入与输出契约校验。

本模块只做无副作用的结构检查，不读取或写入项目文件，也不调用模型。
返回的每条问题统一为 ``code/path/message/severity``，供工作台和运行时复用。
"""

import math
import sys
from typing import Any, Dict, Iterable, List

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# 关键帧/表演节拍的时间角色受控词表（update.md E07）：
#   start=开始状态（视频首帧只准取它）、beat=动作关键点、end=结束状态（图片模式取此拍）、compose=构图参考。
# 落在表演节拍的 ``time_role`` 字段与分镜已采用关键帧 ``shot["keyframe"]["time_role"]`` 上；
# 旧数据没有该字段即未分类，消费方兜底放行并打警告，不阻断。
TIME_ROLES = ("start", "beat", "end", "compose")


def _issue(code: str, path: str, message: str, severity: str = "error") -> dict:
    return {"code": code, "path": path, "message": message, "severity": severity}


def _join(path: str, part: Any) -> str:
    if isinstance(part, int):
        return f"{path}[{part}]"
    return f"{path}.{part}" if path else str(part)


def _walk_non_finite(value: Any, path: str, issues: List[dict]) -> None:
    """递归找出 NaN/Infinity；布尔值不是数值字段。"""
    if isinstance(value, float) and not math.isfinite(value):
        issues.append(_issue("NON_FINITE_NUMBER", path, "数值必须是有限值"))
    elif isinstance(value, dict):
        for key, child in value.items():
            _walk_non_finite(child, _join(path, key), issues)
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _walk_non_finite(child, _join(path, index), issues)


def _ids(items: Any) -> Iterable[str]:
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, str) and item]


def _duplicate_ids(items: Any, path: str, code: str, label: str, issues: List[dict]) -> None:
    seen = set()
    if not isinstance(items, list):
        return
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id:
            continue
        if item_id in seen:
            issues.append(_issue(code, f"{path}[{index}].id", f"{label} ID 重复：{item_id}"))
        seen.add(item_id)


def validate_context(context: dict, board: dict) -> list[dict]:
    """校验角色表演上下文中的分支、事件、事实和角色引用。"""
    issues: List[dict] = []
    if not isinstance(context, dict):
        return [_issue("INVALID_CONTEXT", "context", "上下文必须是对象")]
    if not isinstance(board, dict):
        return [_issue("INVALID_BOARD", "board", "分镜必须是对象")]

    _walk_non_finite(context, "context", issues)
    _walk_non_finite(board, "board", issues)

    board_actors = board.get("actors", {})
    if isinstance(board_actors, dict):
        actor_ids = set(board_actors)
    elif isinstance(board_actors, list):
        actor_ids = {item.get("id") for item in board_actors if isinstance(item, dict)}
    else:
        actor_ids = set()

    continuities = context.get("continuities")
    if not isinstance(continuities, list) or not continuities:
        issues.append(_issue("MISSING_CONTINUITY", "continuities", "至少需要一个连续性分支"))
        continuities = []
    continuity_ids = {
        item.get("id") for item in continuities
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item.get("id")
    }
    cards = context.get("actor_cards", {})
    if cards is not None and not isinstance(cards, dict):
        issues.append(_issue("INVALID_ACTOR_CARDS", "actor_cards", "角色卡必须是对象"))
        cards = {}
    if isinstance(cards, dict):
        card_fields = {"personality", "goal", "relationship", "expression_rules", "arc_stage", "source", "locked_fields"}
        for actor_id, card in cards.items():
            if actor_id not in actor_ids:
                issues.append(_issue("UNKNOWN_ACTOR", f"actor_cards.{actor_id}", f"角色卡引用了未知角色：{actor_id}"))
                continue
            if not isinstance(card, dict):
                issues.append(_issue("INVALID_ACTOR_CARD", f"actor_cards.{actor_id}", "角色卡必须是对象"))
                continue
            locked_fields = card.get("locked_fields", [])
            if locked_fields is not None and not isinstance(locked_fields, list):
                issues.append(_issue("INVALID_LOCKED_FIELDS", f"actor_cards.{actor_id}.locked_fields", "锁定字段必须是数组"))
            elif isinstance(locked_fields, list):
                for field_index, field in enumerate(locked_fields):
                    if field not in card_fields:
                        issues.append(_issue("INVALID_LOCKED_FIELD", f"actor_cards.{actor_id}.locked_fields[{field_index}]", f"未知角色卡字段：{field}"))
            source = card.get("source")
            if source is not None and not isinstance(source, str):
                issues.append(_issue("INVALID_CARD_SOURCE", f"actor_cards.{actor_id}.source", "角色卡来源必须是文本"))
    _duplicate_ids(continuities, "continuities", "DUPLICATE_CONTINUITY_ID", "连续性分支", issues)
    requested_continuity = context.get("continuity_id")
    if requested_continuity and requested_continuity not in continuity_ids:
        issues.append(_issue("MISSING_CONTINUITY", "continuity_id", f"找不到连续性分支：{requested_continuity}"))

    facts = context.get("facts")
    if not isinstance(facts, list):
        facts = []
    fact_ids = {
        item.get("id") for item in facts
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item.get("id")
    }
    _duplicate_ids(facts, "facts", "DUPLICATE_FACT_ID", "事实", issues)

    events = context.get("events")
    if not isinstance(events, list):
        events = []
    event_ids = {
        item.get("id") for item in events
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item.get("id")
    }
    _duplicate_ids(events, "events", "DUPLICATE_EVENT_ID", "事件", issues)
    # 连续性初始状态同样属于契约输入：未知角色或未知事实不能静默带入。
    for continuity_index, continuity in enumerate(continuities):
        if not isinstance(continuity, dict):
            continue
        initial = continuity.get("initial_state", {})
        if initial is not None and not isinstance(initial, dict):
            issues.append(_issue("INVALID_INITIAL_STATE", f"continuities[{continuity_index}].initial_state", "初始状态必须是对象"))
            continue
        if not isinstance(initial, dict):
            continue
        for initial_actor, actor_state in initial.items():
            if initial_actor not in actor_ids:
                issues.append(_issue("UNKNOWN_ACTOR", f"continuities[{continuity_index}].initial_state.{initial_actor}", f"初始状态引用了未知角色：{initial_actor}"))
                continue
            if not isinstance(actor_state, dict):
                issues.append(_issue("INVALID_INITIAL_ACTOR_STATE", f"continuities[{continuity_index}].initial_state.{initial_actor}", "角色初始状态必须是对象"))
                continue
            known = actor_state.get("known_facts", [])
            if isinstance(known, list):
                for fact_index, fact_id in enumerate(known):
                    if fact_id not in fact_ids:
                        issues.append(_issue("UNKNOWN_FACT", f"continuities[{continuity_index}].initial_state.{initial_actor}.known_facts[{fact_index}]", f"初始状态引用了未知事实：{fact_id}"))
            elif known is not None:
                issues.append(_issue("INVALID_KNOWN_FACTS", f"continuities[{continuity_index}].initial_state.{initial_actor}.known_facts", "known_facts 必须是数组"))


    for index, event in enumerate(events):
        if not isinstance(event, dict):
            issues.append(_issue("INVALID_EVENT", f"events[{index}]", "事件必须是对象"))
            continue
        continuity_id = event.get("continuity_id")
        order = event.get("order", 0)
        if not isinstance(order, (int, float)) or isinstance(order, bool) or not math.isfinite(float(order)):
            issues.append(_issue("INVALID_EVENT_ORDER", f"events[{index}].order", "事件 order 必须是有限数值"))
        if continuity_id not in continuity_ids:
            issues.append(_issue(
                "MISSING_CONTINUITY", f"events[{index}].continuity_id",
                f"事件引用了不存在的连续性分支：{continuity_id}",
            ))
        deltas = event.get("deltas", [])
        if isinstance(deltas, dict):
            deltas = [deltas]
        if not isinstance(deltas, list):
            issues.append(_issue("INVALID_EVENT_DELTAS", f"events[{index}].deltas", "事件变化必须是数组"))
            continue
        for delta_index, delta in enumerate(deltas):
            if not isinstance(delta, dict):
                issues.append(_issue("INVALID_EVENT_DELTA", f"events[{index}].deltas[{delta_index}]", "变化必须是对象"))
                continue
            actor_id = delta.get("actor_id")
            if actor_id is not None and actor_id not in actor_ids:
                issues.append(_issue(
                    "UNKNOWN_ACTOR", f"events[{index}].deltas[{delta_index}].actor_id",
                    f"事件引用了未知角色：{actor_id}",
                ))
            for field in ("known_facts_add", "known_facts_remove", "fact_ids"):
                refs = delta.get(field, [])
                if not isinstance(refs, list):
                    continue
                for ref_index, fact_id in enumerate(refs):
                    if fact_id not in fact_ids:
                        issues.append(_issue(
                            "UNKNOWN_FACT",
                            f"events[{index}].deltas[{delta_index}].{field}[{ref_index}]",
                            f"事件引用了未知事实：{fact_id}",
                        ))
    return issues


def _fixed_fields(request: dict) -> Dict[str, Any]:
    fields: Dict[str, Any] = {}
    for key in ("locked", "fixed"):
        value = request.get(key)
        if isinstance(value, dict):
            fields.update(value)
    immutable = request.get("immutable_fields")
    if isinstance(immutable, dict):
        fields.update(immutable)
    return fields


def validate_performance(packet: dict, request: dict) -> list[dict]:
    """校验单镜表演草稿，阻止越权角色、事实和固定镜头字段。"""
    issues: List[dict] = []
    if not isinstance(request, dict):
        return [_issue("INVALID_REQUEST", "request", "表演请求必须是对象")]
    if not isinstance(packet, dict):
        return [_issue("INVALID_PACKET", "packet", "表演输出必须是对象")]

    _walk_non_finite(packet, "packet", issues)
    _walk_non_finite(request, "request", issues)

    request_shot = request.get("shot_id")
    if request_shot is not None and packet.get("shot_id") != request_shot:
        issues.append(_issue("SHOT_ID_MISMATCH", "shot_id", "输出镜号必须与请求一致"))

    dur = request.get("dur")
    if not isinstance(dur, (int, float)) or isinstance(dur, bool) or not math.isfinite(float(dur)):
        if dur is not None:
            issues.append(_issue("INVALID_SHOT_DURATION", "dur", "镜头时长必须是有限数值"))
        dur = None
    if isinstance(dur, (int, float)) and dur is not None and dur < 0:
        issues.append(_issue("INVALID_SHOT_DURATION", "dur", "镜头时长不能为负数"))

    requested_actors = request.get("actor_ids", request.get("allowed_actor_ids", []))
    allowed_actors = set(_ids(requested_actors))
    actors = packet.get("actors")
    if not isinstance(actors, list):
        issues.append(_issue("MISSING_ACTORS", "actors", "输出必须包含 actors 数组"))
        actors = []

    # 请求中的说话人必须出现在表演包里。
    requested_speakers: List[str] = []
    for line in request.get("lines") or []:
        if not isinstance(line, dict):
            continue
        speaker = str(line.get("speaker") or "").strip()
        for prefix in ("@character:", "character:"):
            if speaker.startswith(prefix):
                speaker = speaker.split(":", 1)[1].strip()
        if speaker and speaker not in requested_speakers:
            requested_speakers.append(speaker)

    actor_seen = set()
    for actor_index, actor in enumerate(actors):
        actor_path = f"actors[{actor_index}]"
        if not isinstance(actor, dict):
            issues.append(_issue("INVALID_ACTOR_PACKET", actor_path, "角色表演必须是对象"))
            continue
        actor_id = actor.get("actor_id")
        if actor_id not in allowed_actors:
            issues.append(_issue("UNKNOWN_ACTOR", f"{actor_path}.actor_id", f"输出包含未授权角色：{actor_id}"))
        if actor_id in actor_seen:
            issues.append(_issue("DUPLICATE_ACTOR", f"{actor_path}.actor_id", f"角色重复：{actor_id}"))
        actor_seen.add(actor_id)

        actor_evidence = actor.get("evidence_fact_ids", [])
        per_actor = request.get("allowed_fact_ids_by_actor", {})
        actor_allowed = per_actor.get(actor_id) if isinstance(per_actor, dict) else None
        allowed_facts = set(_ids(actor_allowed if actor_allowed is not None else request.get("allowed_fact_ids", [])))
        if actor_evidence is not None and not isinstance(actor_evidence, list):
            issues.append(_issue("INVALID_EVIDENCE_FACTS", f"{actor_path}.evidence_fact_ids", "evidence_fact_ids 必须是数组"))
        if isinstance(actor_evidence, list):
            for fact_index, fact_id in enumerate(actor_evidence):
                if fact_id not in allowed_facts:
                    issues.append(_issue(
                        "KNOWLEDGE_LEAK", f"{actor_path}.evidence_fact_ids[{fact_index}]",
                        f"角色不能依据该事实：{fact_id}",
                    ))

        beats = actor.get("beats", [])
        if not isinstance(beats, list):
            issues.append(_issue("INVALID_BEATS", f"{actor_path}.beats", "beats 必须是数组"))
            continue
        for beat_index, beat in enumerate(beats):
            beat_path = f"{actor_path}.beats[{beat_index}]"
            if not isinstance(beat, dict):
                issues.append(_issue("INVALID_BEAT", beat_path, "beat 必须是对象"))
                continue
            at = beat.get("at")
            beat_duration = beat.get("duration")
            valid_numbers = True
            for field, value in (("at", at), ("duration", beat_duration)):
                if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
                    valid_numbers = False
                    # 非有限值由递归检查统一报告；缺字段另报结构问题。
                    if value is None:
                        issues.append(_issue("INVALID_BEAT_NUMBER", f"{beat_path}.{field}", "beat 时间必须是数值"))
            if valid_numbers and isinstance(at, (int, float)) and isinstance(beat_duration, (int, float)):
                if at < 0 or beat_duration <= 0 or (dur is not None and at + beat_duration > dur):
                    issues.append(_issue("BEAT_OUT_OF_SHOT", beat_path, "beat 必须完整落在镜头时长内"))
            evidence = beat.get("evidence_fact_ids", [])
            if evidence is not None and not isinstance(evidence, list):
                issues.append(_issue("INVALID_EVIDENCE_FACTS", f"{beat_path}.evidence_fact_ids", "evidence_fact_ids 必须是数组"))
            if isinstance(evidence, list):
                # 镜内分段可按 beat 索引声明不同的事件快照；没有分段声明时
                # 回退到角色级允许事实集合，兼容旧草稿。
                per_actor_beat = request.get("allowed_fact_ids_by_actor_beat", {})
                beat_allowed = None
                if isinstance(per_actor_beat, dict):
                    actor_beats = per_actor_beat.get(actor_id)
                    if isinstance(actor_beats, dict):
                        beat_allowed = actor_beats.get(str(beat_index), actor_beats.get(beat_index))
                    elif isinstance(actor_beats, list) and beat_index < len(actor_beats):
                        beat_allowed = actor_beats[beat_index]
                per_actor = request.get("allowed_fact_ids_by_actor", {})
                actor_allowed = per_actor.get(actor_id) if isinstance(per_actor, dict) else None
                allowed_facts = set(_ids(beat_allowed if beat_allowed is not None else (actor_allowed if actor_allowed is not None else request.get("allowed_fact_ids", []))))
                for fact_index, fact_id in enumerate(evidence):
                    if fact_id not in allowed_facts:
                        issues.append(_issue(
                            "KNOWLEDGE_LEAK", f"{beat_path}.evidence_fact_ids[{fact_index}]",
                            f"该 beat 不能依据该事实：{fact_id}",
                        ))
            after_events = beat.get("after_event_ids")
            if after_events is not None:
                if not isinstance(after_events, list):
                    issues.append(_issue("INVALID_BEAT_EVENTS", f"{beat_path}.after_event_ids", "after_event_ids 必须是数组"))
                else:
                    allowed_events = set(_ids(request.get("allowed_event_ids", request.get("event_ids", []))))
                    for event_index, event_id in enumerate(after_events):
                        if event_id not in allowed_events:
                            issues.append(_issue("UNKNOWN_EVENT", f"{beat_path}.after_event_ids[{event_index}]", f"该 beat 不能引用事件：{event_id}"))

    if allowed_actors and not actor_seen:
        issues.append(_issue("EMPTY_ACTORS", "actors", "请求包含在场角色但 actors 为空（空包不得视为 ready）"))
    for speaker in requested_speakers:
        if speaker not in actor_seen:
            issues.append(_issue("MISSING_SPEAKER", "actors", f"说话人未出现在表演包中：{speaker}"))

    event_ids = packet.get("event_ids", [])
    if isinstance(event_ids, list):
        seen_events = set()
        for index, event_id in enumerate(event_ids):
            if event_id in seen_events:
                issues.append(_issue("DUPLICATE_EVENT_ID", f"event_ids[{index}]", f"事件 ID 重复：{event_id}"))
            seen_events.add(event_id)

    immutable = _fixed_fields(request)
    forbidden = request.get("forbidden_output_fields", [])
    if isinstance(forbidden, list):
        for key in forbidden:
            if key in packet:
                issues.append(_issue("FIXED_FIELD_MUTATION", str(key), "模型不允许输出固定镜头字段"))
    for key, expected in immutable.items():
        if key in packet and packet.get(key) != expected:
            issues.append(_issue("FIXED_FIELD_MUTATION", str(key), f"固定字段 {key} 不得被表演层修改"))
    return issues


__all__ = ["validate_context", "validate_performance", "TIME_ROLES"]







