# -*- coding: utf-8 -*-
"""角色表演层的事件状态和角色知情范围纯函数。"""

import copy
import sys
from typing import Any, Dict, List

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def _continuity(context: dict, continuity_id: str) -> dict:
    for item in context.get("continuities", []) or []:
        if isinstance(item, dict) and item.get("id") == continuity_id:
            return item
    raise ValueError(f"找不到连续性分支：{continuity_id}")


def _events(context: dict) -> Dict[str, dict]:
    result = {}
    for event in context.get("events", []) or []:
        if isinstance(event, dict) and isinstance(event.get("id"), str):
            result.setdefault(event["id"], event)
    return result


def _apply_delta(actor: dict, delta: dict) -> None:
    """把一个剧情事件变化应用到角色快照。"""
    known = actor.setdefault("known_facts", [])
    if not isinstance(known, list):
        known = actor["known_facts"] = []
    for fact_id in delta.get("known_facts_add", []) or []:
        if fact_id not in known:
            known.append(fact_id)
    for fact_id in delta.get("known_facts_remove", []) or []:
        while fact_id in known:
            known.remove(fact_id)

    props = actor.setdefault("props", {})
    if not isinstance(props, dict):
        props = actor["props"] = {}
    props_set = delta.get("props_set", delta.get("props", {}))
    if isinstance(props_set, dict):
        props.update(copy.deepcopy(props_set))

    reserved = {"actor_id", "known_facts_add", "known_facts_remove", "props_set", "props", "fact_ids"}
    for key, value in delta.items():
        if key not in reserved:
            actor[key] = copy.deepcopy(value)


def state_at(context: dict, continuity_id: str, event_ids: list[str]) -> dict:
    """按连续性分支和事件 order 生成确定性状态快照。

    重复事件只应用一次；事件属于其他分支、事件不存在或事件 ID 无效时抛出
    ``ValueError``，调用方必须补上下文后再继续。
    """
    if not isinstance(context, dict):
        raise ValueError("上下文必须是对象")
    continuity = _continuity(context, continuity_id)
    initial = continuity.get("initial_state", {})
    state = copy.deepcopy(initial) if isinstance(initial, dict) else {}
    events = _events(context)
    selected = []
    seen = set()
    for event_id in event_ids or []:
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("事件 ID 必须是非空字符串")
        if event_id in seen:
            continue
        event = events.get(event_id)
        if event is None:
            raise ValueError(f"找不到事件：{event_id}")
        if event.get("continuity_id") != continuity_id:
            raise ValueError(f"事件 {event_id} 不属于连续性分支 {continuity_id}")
        selected.append(event)
        seen.add(event_id)

    # order 缺省为 0，保持传入顺序作为稳定排序的第二关键字。
    selected = sorted(enumerate(selected), key=lambda pair: (pair[1].get("order", 0), pair[0]))
    for _, event in selected:
        deltas = event.get("deltas", [])
        if isinstance(deltas, dict):
            deltas = [deltas]
        for delta in deltas if isinstance(deltas, list) else []:
            if not isinstance(delta, dict):
                continue
            actor_id = delta.get("actor_id")
            if actor_id is None:
                continue
            actor = state.setdefault(actor_id, {})
            if not isinstance(actor, dict):
                actor = state[actor_id] = {}
            _apply_delta(actor, delta)
    return state


def visible_context(state: dict, actor_id: str, facts: list[dict]) -> dict:
    """只返回角色已知事实及其表演状态，不暴露未知事实正文或 ID。"""
    if not isinstance(state, dict) or actor_id not in state:
        raise ValueError(f"找不到角色状态：{actor_id}")
    actor = state.get(actor_id)
    if not isinstance(actor, dict):
        raise ValueError(f"角色状态必须是对象：{actor_id}")
    known_ids = actor.get("known_facts", [])
    if not isinstance(known_ids, list):
        known_ids = []
    by_id = {
        fact.get("id"): fact for fact in (facts or [])
        if isinstance(fact, dict) and isinstance(fact.get("id"), str)
    }
    visible_facts = [copy.deepcopy(by_id[fact_id]) for fact_id in known_ids if fact_id in by_id]
    actor_state = copy.deepcopy(actor)
    # 事实 ID 也属于权限边界，输出事实对象即可，避免把 secret 这样的 ID 带入模型。
    actor_state.pop("known_facts", None)
    return {"actor_id": actor_id, "state": actor_state, "known_facts": visible_facts}


def _proposal_fields(delta: dict) -> Dict[str, Any]:
    fields: Dict[str, Any] = {}
    for key, value in delta.items():
        if key in {"props_set", "state_set", "props"} and isinstance(value, dict):
            fields.update(value)
        elif key not in {"known_facts_add", "known_facts_remove", "event_ids"}:
            fields[key] = value
    return fields


def merge_proposals(state: dict, proposals: list[dict], constraints: dict) -> dict:
    """合并各角色提议，只允许表演状态字段并报告物品冲突。

    ``constraints.mutable_fields`` 定义可由表演改变的字段；服装、伤势、持物
    等剧情事实必须由上游事件归并，除非显式列入 ``event_backed_fields``。
    """
    merged = copy.deepcopy(state) if isinstance(state, dict) else {}
    constraints = constraints if isinstance(constraints, dict) else {}
    errors: List[dict] = []
    warnings: List[dict] = []
    applied: List[dict] = []
    mutable = set(constraints.get("mutable_fields", constraints.get("allowed_state_fields", [
        "emotion", "attention", "posture", "expression", "gaze", "gesture", "voice", "intent",
    ])))
    mutable.update(constraints.get("event_backed_fields", []) or [])
    allowed_actors = set(constraints.get("actor_ids", merged.keys()))
    existing_owners = constraints.get("object_owners", {})
    existing_owners = dict(existing_owners) if isinstance(existing_owners, dict) else {}
    # 从当前快照推导已有持物者，避免另一角色在后续镜头重新声明同一物品。
    for owner_id, owner_state in merged.items():
        if not isinstance(owner_state, dict):
            continue
        holding = owner_state.get("holding")
        if holding is None and isinstance(owner_state.get("props"), dict):
            holding = owner_state["props"].get("holding")
        values = holding if isinstance(holding, list) else [holding]
        for object_id in values:
            if isinstance(object_id, str) and object_id:
                existing_owners.setdefault(object_id, owner_id)
    claims: Dict[str, str] = {}

    # 先收集持物声明，让冲突不被字段白名单短路。
    for index, proposal in enumerate(proposals or []):
        if not isinstance(proposal, dict):
            continue
        actor_id = proposal.get("actor_id")
        delta = proposal.get("proposed_delta", {})
        if not isinstance(delta, dict):
            continue
        fields = _proposal_fields(delta)
        holding = fields.get("holding")
        if holding in (None, ""):
            continue
        if not isinstance(holding, str):
            continue
        owner = claims.get(holding) or existing_owners.get(holding)
        if owner and owner != actor_id:
            errors.append({
                "code": "OBJECT_OWNERSHIP_CONFLICT",
                "path": f"proposals[{index}].proposed_delta.holding",
                "message": f"物品 {holding} 同时被 {owner} 和 {actor_id} 持有",
                "severity": "error",
            })
        else:
            claims[holding] = actor_id

    for index, proposal in enumerate(proposals or []):
        path = f"proposals[{index}]"
        if not isinstance(proposal, dict):
            errors.append({"code": "INVALID_PROPOSAL", "path": path, "message": "提议必须是对象", "severity": "error"})
            continue
        actor_id = proposal.get("actor_id")
        if actor_id not in allowed_actors or actor_id not in merged:
            errors.append({"code": "UNKNOWN_ACTOR", "path": f"{path}.actor_id", "message": f"未知角色：{actor_id}", "severity": "error"})
            continue
        delta = proposal.get("proposed_delta", {})
        if not isinstance(delta, dict):
            errors.append({"code": "INVALID_PROPOSED_DELTA", "path": f"{path}.proposed_delta", "message": "状态变化必须是对象", "severity": "error"})
            continue
        # 知情事实必须来自已批准剧情事件，表演模型不能伪造或推进知识。
        approved_facts = set(constraints.get("approved_fact_ids", []) or [])
        for fact_field in ("known_facts_add", "known_facts_remove"):
            refs = delta.get(fact_field, [])
            if not isinstance(refs, list):
                refs = [refs] if refs else []
            for fact_index, fact_id in enumerate(refs):
                if fact_id not in approved_facts:
                    errors.append({
                        "code": "KNOWLEDGE_DELTA_FORBIDDEN",
                        "path": f"{path}.proposed_delta.{fact_field}[{fact_index}]",
                        "message": f"表演层不能直接改变角色知情事实：{fact_id}",
                        "severity": "error",
                    })
        fields = _proposal_fields(delta)
        actor = merged.setdefault(actor_id, {})
        if not isinstance(actor, dict):
            actor = merged[actor_id] = {}
        props = actor.setdefault("props", {})
        if not isinstance(props, dict):
            props = actor["props"] = {}
        actor_applied = {}
        for field, value in fields.items():
            if field == "holding" and any(
                issue["code"] == "OBJECT_OWNERSHIP_CONFLICT" and issue["path"].startswith(path)
                for issue in errors
            ):
                continue
            if field not in mutable:
                errors.append({
                    "code": "PROPOSAL_FIELD_FORBIDDEN",
                    "path": f"{path}.proposed_delta.{field}",
                    "message": f"表演层不能直接改变剧情字段：{field}",
                    "severity": "error",
                })
                continue
            # holding 等字段若被上游事件明确允许，保存在 props 里。
            if field in {"holding", "clothing", "injury"}:
                props[field] = copy.deepcopy(value)
            else:
                actor[field] = copy.deepcopy(value)
            actor_applied[field] = copy.deepcopy(value)
        if actor_applied:
            applied.append({"actor_id": actor_id, "fields": actor_applied})
    return {"state": merged, "applied": applied, "errors": errors, "warnings": warnings}


__all__ = ["state_at", "visible_context", "merge_proposals"]

