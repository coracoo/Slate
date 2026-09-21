# -*- coding: utf-8 -*-
"""演员驱动短剧的请求构造、模型调用和草稿应用。"""
import argparse
import copy
import sys
import os
import json
import time
import uuid
import hashlib
import re

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 演员契约与运行时位于 previs_system/tools；保证从 server、CLI 或独立脚本调用时都可导入。
_CORE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "previs_system", "tools"))
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)
from actor_contract import validate_context, validate_performance
from actor_state import state_at, visible_context
from actor_runtime import perform

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
try:
    import project_store
except ImportError:
    CORE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "previs_system", "tools"))
    if CORE not in sys.path:
        sys.path.insert(0, CORE)
    import project_store
try:
    import versions
except ImportError:
    versions = None
def _resolve_board_path(board_or_project, board_name=None):
    """兼容绝对分镜路径和(project, board_name)两种调用形态。"""
    if board_name is None:
        path = os.path.abspath(os.fspath(board_or_project))
    else:
        project = os.path.abspath(os.fspath(board_or_project))
        name = os.path.basename(str(board_name))
        path = os.path.abspath(os.path.join(project, "分镜", name))
    if not path.lower().endswith(".json") or not os.path.isfile(path):
        raise FileNotFoundError("分镜不存在：" + path)
    return path


def _shot(board, shot_id):
    for shot in board.get("shots") or []:
        if str(shot.get("id")) == str(shot_id):
            return shot
    raise ValueError(f"找不到镜头：{shot_id}")


def _actor_ids(shot):
    result = []
    def add(value):
        raw = str(value or "").strip()
        if not raw:
            return
        if raw.startswith("@character:"):
            raw = raw.split(":", 1)[1].strip()
        elif raw.startswith("character:"):
            raw = raw.split(":", 1)[1].strip()
        if raw and raw not in result:
            result.append(raw)

    for actor_id in [shot.get("speaker"), shot.get("host"), shot.get("target"), shot.get("focus")]:
        add(actor_id)
    for line in shot.get("lines") or []:
        if isinstance(line, dict):
            add(line.get("speaker"))
    for actor_id in (shot.get("staging") or {}):
        add(actor_id)
    # 创作线把可见人物保存为 actor_refs；旧分镜也可能只有 prompt 中的 @character 引用。
    for key in ("actor_ids", "actor_refs", "character_refs", "cast", "cast_refs"):
        values = shot.get(key)
        if isinstance(values, dict):
            values = list(values.keys())
        elif isinstance(values, str):
            values = [values]
        elif not isinstance(values, (list, tuple, set)):
            values = []
        for value in values:
            if isinstance(value, dict):
                value = value.get("id") or value.get("ref") or value.get("name")
            add(value)
    for key in ("prompt", "prompt_image", "prompt_video", "action", "content", "story"):
        for actor_id in re.findall(r"@character:([\w-]+)", str(shot.get(key) or "")):
            add(actor_id)
    return result


def _asset_refs(shot, actor_ids=None):
    """把分镜中的角色、场景、道具字段归一为稳定 @ 引用。"""
    refs = []

    def add(value, kind):
        raw = str(value or "").strip()
        if not raw:
            return
        if raw.startswith("@"):
            ref = raw
        elif ":" in raw and raw.split(":", 1)[0] in ("人物", "角色", "场景", "道具", "character", "scene", "prop"):
            head, ident = raw.split(":", 1)
            mapped = {"人物": "character", "角色": "character", "场景": "scene", "道具": "prop"}.get(head, head)
            ref = "@" + mapped + ":" + ident.strip()
        else:
            ref = "@" + kind + ":" + raw
        if ref not in refs:
            refs.append(ref)

    for actor_id in actor_ids or _actor_ids(shot):
        add(actor_id, "character")
    scene = shot.get("scene_ref") or shot.get("scene_asset") or shot.get("scene")
    if scene:
        add(scene, "scene")
    for key in ("prop_refs", "props", "prop_ids", "props_used", "objects", "items"):
        values = shot.get(key)
        if isinstance(values, dict):
            values = list(values.values())
        elif not isinstance(values, (list, tuple, set)):
            values = [values] if values else []
        for value in values:
            if isinstance(value, dict):
                value = value.get("ref") or value.get("id") or value.get("name")
            add(value, "prop")
    return refs


def build_request(board, shot_id, context=None, system_prompt=None, project_dir=None):
    """从固定分镜构造演员请求；固定字段由程序带入并锁定。"""
    shot = _shot(board, shot_id)
    actor_ids = actor_ids_for_shot(board, shot, project_dir=project_dir)
    request = {
        "shot_id": str(shot_id), "dur": float(shot.get("dur") or 0), "actor_ids": actor_ids,
        "allowed_fact_ids": [], "fixed": {key: shot.get(key) for key in ("dur", "cam", "pos", "look", "move") if key in shot},
        "forbidden_output_fields": ["dur", "cam", "pos", "look", "move", "lines"],
        "scene": shot.get("scene", "room"), "lines": copy.deepcopy(shot.get("lines") or []),
    }
    request["asset_refs"] = _asset_refs(shot, actor_ids)
    if system_prompt:
        request["system_prompt"] = system_prompt
    if isinstance(context, dict):
        issues = validate_context(context, board)
        if issues:
            raise ValueError("演员上下文校验失败：" + "；".join(item["message"] for item in issues[:5]))
        continuity_id = context.get("continuity_id") or next(
            (item.get("id") for item in context.get("continuities") or [] if isinstance(item, dict) and item.get("id")), None)
        if not continuity_id:
            raise ValueError("演员上下文缺少 continuity_id")
        request["continuity_id"] = continuity_id
        all_event_ids = [str(item.get("id")) for item in (context.get("events") or [])
                         if isinstance(item, dict) and item.get("id") and item.get("continuity_id") == continuity_id]
        # beat_after_event_ids / acting_beats / 台词行的 after_event_ids 都是可选输入。
        # 一旦提供，主请求只看首个快照，逐 beat 另附权限集合，避免把镜尾状态提前喂给模型。
        beat_specs = []
        raw_specs = shot.get("beat_after_event_ids") or shot.get("acting_beats")
        if isinstance(raw_specs, dict):
            raw_specs = list(raw_specs.values())
        if isinstance(raw_specs, list):
            for index, item in enumerate(raw_specs):
                if isinstance(item, dict):
                    ids = item.get("after_event_ids", item.get("event_ids", []))
                    beat_specs.append({"at": item.get("at", 0), "duration": item.get("duration", 0), "after_event_ids": ids})
                elif isinstance(item, list):
                    beat_specs.append({"at": 0, "duration": 0, "after_event_ids": item})
        if not beat_specs:
            for line in shot.get("lines") or []:
                if isinstance(line, dict) and ("after_event_ids" in line or "event_ids" in line):
                    beat_specs.append({"at": line.get("at", 0), "duration": line.get("dur", line.get("duration", 0)),
                                       "after_event_ids": line.get("after_event_ids", line.get("event_ids", []))})
        if not beat_specs and isinstance(shot.get("after_event_ids"), list):
            beat_specs = [{"at": 0, "duration": float(shot.get("dur") or 0), "after_event_ids": shot.get("after_event_ids")}]
        def _norm_ids(value):
            if not isinstance(value, list):
                return []
            out = []
            for event_id in value:
                if isinstance(event_id, str) and event_id and event_id not in out:
                    out.append(event_id)
            return out
        event_ids = _norm_ids(shot.get("after_event_ids")) if isinstance(shot.get("after_event_ids"), list) else all_event_ids
        if beat_specs:
            event_ids = _norm_ids(beat_specs[0].get("after_event_ids"))
        request["event_ids"] = event_ids
        request["allowed_event_ids"] = all_event_ids
        # 每个角色只接收自己的可见事实；上下文中其他角色的秘密不会进入模型请求。
        def _visible_for(ids):
            state = state_at(context, continuity_id, _norm_ids(ids))
            rows, by_actor = [], {}
            for actor_id in actor_ids:
                try:
                    item = visible_context(state, actor_id, context.get("facts") or [])
                except ValueError:
                    item = {"actor_id": actor_id, "state": {}, "known_facts": []}
                rows.append(item)
                by_actor[actor_id] = [str(fact.get("id")) for fact in item.get("known_facts") or [] if isinstance(fact, dict) and fact.get("id")]
            return rows, by_actor
        visible, allowed_by_actor = _visible_for(event_ids)
        request["visible_context"] = visible
        request["allowed_fact_ids"] = sorted({fact_id for values in allowed_by_actor.values() for fact_id in values})
        request["allowed_fact_ids_by_actor"] = allowed_by_actor
        if beat_specs:
            beat_contexts = []
            by_actor_beat = {actor_id: {} for actor_id in actor_ids}
            for beat_index, spec in enumerate(beat_specs):
                ids = _norm_ids(spec.get("after_event_ids"))
                rows, per_actor = _visible_for(ids)
                beat_contexts.append({"at": spec.get("at", 0), "duration": spec.get("duration", 0),
                                      "after_event_ids": ids, "visible_context": rows,
                                      "allowed_fact_ids_by_actor": per_actor})
                for actor_id, values in per_actor.items():
                    by_actor_beat.setdefault(actor_id, {})[str(beat_index)] = values
            request["beat_contexts"] = beat_contexts
            request["allowed_fact_ids_by_actor_beat"] = by_actor_beat
        cards = context.get("actor_cards") if isinstance(context.get("actor_cards"), dict) else {}
        request["actor_cards"] = {actor_id: copy.deepcopy(cards.get(actor_id) or {}) for actor_id in actor_ids}
    return request


def run(request, call_llm, max_attempts=2):
    """运行有限次数的演员生成和程序校验。"""
    return perform(request, call_llm, max_attempts=max_attempts)


def apply_result(board, shot_id, result):
    """只把 ready 草稿挂回目标镜头，返回新对象；不修改原 board。"""
    if not isinstance(result, dict) or result.get("status") != "ready":
        raise ValueError("演员草稿未通过校验，不能应用")
    packet = result.get("packet")
    if not isinstance(packet, dict) or str(packet.get("shot_id")) != str(shot_id):
        raise ValueError("演员草稿镜号不匹配")
    out = copy.deepcopy(board)
    shot = _shot(out, shot_id)
    shot["performance"] = copy.deepcopy(result)
    return out



def _read_board(board_path):
    """读取分镜及 revision；候选流程的所有读写均走 project_store。"""
    return project_store.read_json(os.path.abspath(os.fspath(board_path)))


def _candidate_dir(board_path):
    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(os.fspath(board_path))))
    stem = os.path.splitext(os.path.basename(os.fspath(board_path)))[0]
    return os.path.join(project_dir, "演员", "表演草稿_" + stem)


def _actor_ids_from_board(board, project_dir=None):
    """返回可进入演员层的角色 ID；有角色分类时仅保留主角。"""
    return main_actor_ids(board, project_dir=project_dir)


ACTOR_CARD_FIELDS = ("personality", "goal", "relationship", "expression_rules", "arc_stage")


def _read_json_file(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            value = json.load(fh)
        return value if isinstance(value, type(default)) else default
    except (OSError, ValueError, TypeError):
        return default


def _actor_records(board):
    actors = board.get("actors") or {}
    if isinstance(actors, dict):
        return [(str(actor_id), actor if isinstance(actor, dict) else {}) for actor_id, actor in actors.items()]
    if isinstance(actors, list):
        return [(str(actor.get("id")), actor) for actor in actors
                if isinstance(actor, dict) and actor.get("id")]
    return []


# 人物档案的 role 是中文业务字段，新旧分镜的 actors 记录可能没有该字段。
# 未分类的旧分镜继续按原行为兼容；一旦存在明确主角/配角分类，演员层只接受主角。
_MAIN_ROLE_VALUES = frozenset({
    "主角", "主人公", "核心角色", "主线角色", "protagonist", "main", "maincharacter",
    "main_actor", "hero", "heroine",
})
_SUPPORT_ROLE_VALUES = frozenset({
    "配角", "次要角色", "群演", "路人", "群众演员", "群体", "support", "supporting", "supportingcharacter",
    "extra", "background", "guest", "collective",
})


def _role_kind(value):
    """归一角色分类，返回 main/support/空；描述性 personality 不算角色分类。"""
    raw = str(value or "").strip().casefold()
    if not raw:
        return ""
    compact = raw.replace(" ", "").replace("-", "_")
    if compact in _MAIN_ROLE_VALUES or ("主角" in compact and "配角" not in compact):
        return "main"
    if compact in _SUPPORT_ROLE_VALUES or any(token in compact for token in ("配角", "群演", "路人", "supporting", "background")):
        return "support"
    return ""


def _character_lookup(project_dir):
    """从项目人物.json建立 id/name/alias 到人物档案的索引。"""
    if not project_dir:
        return {}
    doc = _read_json_file(os.path.join(os.path.abspath(os.fspath(project_dir)), "素材", "人物.json"), {})
    result = {}
    for item in doc.get("characters") or []:
        if not isinstance(item, dict):
            continue
        keys = [item.get("id"), item.get("name")]
        aliases = item.get("aliases") if isinstance(item.get("aliases"), list) else []
        keys.extend(aliases)
        for key in keys:
            if key:
                result[str(key)] = item
    return result


def actor_metadata(board, project_dir=None):
    """返回演员层可见的角色元数据，包含主角判定来源。"""
    lookup = _character_lookup(project_dir)
    rows = []
    for actor_id, actor in _actor_records(board):
        name = str(actor.get("name") or actor_id).strip()
        character = lookup.get(actor_id) or lookup.get(name) or {}
        role = actor.get("role") or character.get("role") or ""
        marker = actor.get("is_main")
        if marker is None:
            marker = character.get("is_main")
        collective = actor.get("is_collective")
        if collective is None:
            collective = character.get("is_collective")
        kind = "support" if collective is True else ("main" if marker is True else ("support" if marker is False else _role_kind(role)))
        rows.append({
            "id": str(actor_id), "name": name,
            "role": str(role).strip(), "kind": kind,
            "is_main": kind == "main", "record": actor, "character": character,
        })
    classified = any(row["kind"] for row in rows)
    for row in rows:
        # 没有任何角色分类的历史分镜按旧逻辑放行；存在分类时未知角色不进入演员层。
        row["eligible"] = row["kind"] == "main" or (not classified and not row["kind"])
    return rows


def main_actor_ids(board, project_dir=None):
    """返回演员表现允许生成的主角 ID，保持分镜 actors 原顺序。"""
    return [row["id"] for row in actor_metadata(board, project_dir=project_dir) if row["eligible"]]


def main_actor_records(board, project_dir=None):
    """返回仅主角的 actors 映射，供工作台展示和接口响应。"""
    return {row["id"]: dict(row["record"], name=row["name"], role=row["role"], is_main=True)
            for row in actor_metadata(board, project_dir=project_dir) if row["eligible"]}


def actor_ids_for_shot(board, shot_or_id, project_dir=None):
    """解析本镜演员并与主角白名单求交；旧的未分类分镜才使用主角兜底。"""
    shot = _shot(board, shot_or_id) if not isinstance(shot_or_id, dict) else shot_or_id
    metadata = actor_metadata(board, project_dir=project_dir)
    allowed_ids = [row["id"] for row in metadata if row["eligible"]]
    allowed = set(allowed_ids)
    explicit = [str(value) for value in _actor_ids(shot) if value]
    selected = [actor_id for actor_id in explicit if actor_id in allowed]
    if not explicit:
        # 旧分镜没有任何角色分类时保留兼容兜底；已有主角/配角分类后，空场镜头不凭空绑定主角。
        selected = [] if any(row["kind"] for row in metadata) else allowed_ids
    return selected


def _ensure_main_actor_request(request, board, project_dir=None):
    """校验候选请求没有越过主角白名单。"""
    if not isinstance(request, dict):
        raise ValueError("演员请求必须是对象")
    requested = [str(actor_id) for actor_id in request.get("actor_ids") or [] if actor_id]
    allowed = set(main_actor_ids(board, project_dir=project_dir))
    invalid = [actor_id for actor_id in requested if actor_id not in allowed]
    if invalid:
        raise ValueError("演员表现只允许主角，已拒绝：" + ",".join(invalid))
    if not requested:
        raise ValueError("本镜没有可生成的主角演员")
    return request


def _hint_map(value):
    result = {}
    if not isinstance(value, list):
        return result
    for item in value:
        raw = str(item or "").strip()
        if not raw:
            continue
        name, sep, desc = raw.partition("：")
        if not sep:
            name, sep, desc = raw.partition(":")
        name = name.strip()
        desc = (desc if sep else raw).strip()
        if name and desc:
            result[name] = desc
    return result


def actor_cards_from_assets(project_dir, board):
    """从人物资产和大纲角色提示自动构造演员角色卡，不调用模型。"""
    project_dir = os.path.abspath(os.fspath(project_dir))
    script_dir = os.path.join(project_dir, "剧本")
    character_doc = _read_json_file(os.path.join(project_dir, "素材", "人物.json"), {})
    characters = character_doc.get("characters") or []
    by_key = {}
    for character in characters:
        if not isinstance(character, dict):
            continue
        keys = [character.get("id"), character.get("name")]
        aliases = character.get("aliases") if isinstance(character.get("aliases"), list) else []
        keys.extend(aliases)
        for key in keys:
            if key:
                by_key[str(key)] = character
    outline = _read_json_file(os.path.join(script_dir, "大纲.json"), {})
    hints = _hint_map(outline.get("characters_hint"))
    cards = {}
    allowed_actor_ids = set(main_actor_ids(board, project_dir=project_dir))
    for actor_id, actor in _actor_records(board):
        if actor_id not in allowed_actor_ids:
            continue
        name = str(actor.get("name") or actor_id).strip()
        character = by_key.get(actor_id) or by_key.get(name) or {}
        acting = character.get("acting") if isinstance(character.get("acting"), dict) else {}
        card = {}
        for field in ACTOR_CARD_FIELDS:
            value = acting.get(field) or character.get(field)
            if isinstance(value, (list, tuple)):
                value = "、".join(str(item) for item in value if item)
            if value is not None and str(value).strip():
                card[field] = value
        source = "素材/人物.json" if card else ""
        if not card and name in hints:
            card["personality"] = hints[name]
            source = "剧本/大纲.json"
        if card:
            card["source"] = source
            card["locked_fields"] = []
            cards[actor_id] = card
    return cards


def hydrate_actor_cards(context, project_dir, board):
    """把资产角色卡补入上下文；已有非空或锁定字段保持不变。"""
    out = copy.deepcopy(context) if isinstance(context, dict) else {}
    cards = out.get("actor_cards") if isinstance(out.get("actor_cards"), dict) else {}
    allowed_actor_ids = set(main_actor_ids(board, project_dir=project_dir))
    # 演员层只保存主角角色卡；视觉层的配角仍可留在分镜 actors 中。
    cards = {str(actor_id): value for actor_id, value in cards.items() if str(actor_id) in allowed_actor_ids}
    continuities = out.get("continuities")
    if isinstance(continuities, list):
        for continuity in continuities:
            if not isinstance(continuity, dict):
                continue
            initial = continuity.get("initial_state")
            if isinstance(initial, dict):
                continuity["initial_state"] = {
                    str(actor_id): value for actor_id, value in initial.items()
                    if str(actor_id) in allowed_actor_ids
                }
    events = out.get("events")
    if isinstance(events, list):
        filtered_events = []
        for event in events:
            if not isinstance(event, dict):
                filtered_events.append(event)
                continue
            event_actor = event.get("actor_id")
            if event_actor is not None and str(event_actor) not in allowed_actor_ids:
                continue
            deltas = event.get("deltas")
            if isinstance(deltas, list):
                event = copy.deepcopy(event)
                event["deltas"] = [delta for delta in deltas if not isinstance(delta, dict) or
                                    delta.get("actor_id") is None or str(delta.get("actor_id")) in allowed_actor_ids]
            filtered_events.append(event)
        out["events"] = filtered_events
    for actor_id, asset_card in actor_cards_from_assets(project_dir, board).items():
        target = cards.setdefault(actor_id, {})
        if not isinstance(target, dict):
            target = {}
            cards[actor_id] = target
        locked = set(target.get("locked_fields") or []) if isinstance(target.get("locked_fields"), list) else set()
        for field in ACTOR_CARD_FIELDS:
            if field not in locked and not str(target.get(field) or "").strip() and asset_card.get(field):
                target[field] = asset_card[field]
        if not str(target.get("source") or "").strip():
            target["source"] = asset_card.get("source", "")
        target.setdefault("locked_fields", [])
    out["actor_cards"] = cards
    return out


def default_context(board, project_dir=None):
    """生成最小可用主线上下文；不会把秘密事实默认暴露给角色。"""
    initial = {actor_id: {"known_facts": [], "emotion": "", "gaze": ""}
               for actor_id in _actor_ids_from_board(board, project_dir=project_dir)}
    context = {
        "version": "actor-context-v1", "continuity_id": "main",
        "continuities": [{"id": "main", "label": "主线", "initial_state": initial}],
        "facts": [], "events": [],
        "mutable_fields": ["emotion", "gaze", "posture", "gesture", "props"],
    }
    return hydrate_actor_cards(context, project_dir, board) if project_dir else context


def _shot_ids(board, shot_ids=None):
    available = [str(item.get("id")) for item in board.get("shots") or [] if item.get("id")]
    if shot_ids is None:
        return available
    if isinstance(shot_ids, str):
        shot_ids = [item.strip() for item in shot_ids.split(",") if item.strip()]
    wanted = []
    for shot_id in shot_ids:
        sid = str(shot_id)
        if sid not in wanted:
            wanted.append(sid)
    if not wanted:
        raise ValueError("至少选择一个镜头")
    missing = [sid for sid in wanted if sid not in available]
    if missing:
        raise ValueError("找不到镜头：" + ",".join(missing))
    return wanted


def _write_candidate(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(temporary, path)
    return path


def _input_hash(value):
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

def _new_run_id(kind):
    return f"{kind}_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


def prepare_context(board_path, shot_ids=None, context=None, run_id=None, call_llm=None):
    """准备并落盘上下文候选，不改正式分镜。"""
    board_path = os.path.abspath(os.fspath(board_path))
    board, revision = _read_board(board_path)
    chosen = _shot_ids(board, shot_ids)
    project_dir = os.path.dirname(os.path.dirname(board_path))
    missing_main = [sid for sid in chosen if not actor_ids_for_shot(board, sid, project_dir=project_dir)]
    if missing_main:
        raise ValueError("演员表现只支持主角；以下镜头没有关联主角：" + ",".join(missing_main))
    context = hydrate_actor_cards(copy.deepcopy(context) if isinstance(context, dict) else copy.deepcopy(board.get("acting_context") or default_context(board, project_dir)), project_dir, board)
    issues = validate_context(context, board)
    if issues:
        raise ValueError("演员上下文校验失败：" + "；".join(item["message"] for item in issues[:5]))
    preparation = []
    preparation_errors = []
    if callable(call_llm):
        # 只把固定分镜和已过滤上下文交给准备模型；结果仍是候选。
        try:
            from prompt_modules import actor_prepare_prompt
            for sid in chosen:
                try:
                    shot_data = copy.deepcopy(_shot(board, sid))
                    shot_data["actor_ids"] = actor_ids_for_shot(board, sid, project_dir=project_dir)
                    system_prompt, user_prompt = actor_prepare_prompt(shot_data, context)
                    response = call_llm([{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}])
                    content = response.get("content") if isinstance(response, dict) else response
                    parsed = content if isinstance(content, dict) else json.loads(str(content or "").strip().strip("`") or "{}")
                    if not isinstance(parsed, dict):
                        raise ValueError("准备模型输出必须是对象")
                    preparation.append({"shot_id": sid, "result": parsed})
                except Exception as exc:
                    preparation_errors.append({"shot_id": sid, "message": str(exc)})
        except Exception as exc:
            preparation_errors.append({"message": str(exc)})
    rid = str(run_id or _new_run_id("context"))
    candidate = {
        "run_id": rid, "candidate_kind": "context", "status": "invalid" if preparation_errors else "ready",
        "board": os.path.basename(board_path), "source_revision": revision,
        "source_hash": artifact_hash(board, "prompt", "actor-v1") if artifact_hash else "",
        "input_hash": _input_hash({"context": context, "shot_ids": chosen}),
        "shot_ids": chosen, "context": context,
        "preparation": preparation, "errors": preparation_errors,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    path = os.path.join(_candidate_dir(board_path), rid + ".json")
    _write_candidate(path, candidate)
    candidate["path"] = path
    return candidate


def save_performance_candidate(board_path, shot_id, result, *, request=None, context=None, run_id=None, mode="stateful"): 
    """把单镜模型结果保存为候选文件，正式分镜保持不变。"""
    board_path = os.path.abspath(os.fspath(board_path))
    board, revision = _read_board(board_path)
    sid = str(shot_id)
    _shot(board, sid)
    if not isinstance(result, dict):
        raise ValueError("演员结果必须是对象")
    project_dir = os.path.dirname(os.path.dirname(board_path))
    context = hydrate_actor_cards(copy.deepcopy(context) if isinstance(context, dict) else copy.deepcopy(board.get("acting_context") or default_context(board, project_dir)), project_dir, board)
    issues = validate_context(context, board)
    if issues:
        raise ValueError("演员上下文校验失败：" + "；".join(item["message"] for item in issues[:5]))
    request = copy.deepcopy(request) if isinstance(request, dict) else build_request(board, sid, context=context, project_dir=project_dir)
    _ensure_main_actor_request(request, board, project_dir=project_dir)
    rid = str(run_id or _new_run_id("performance"))
    candidate = {
        "run_id": rid, "candidate_kind": "performance",
        "status": str(result.get("status") or "invalid"),
        "board": os.path.basename(board_path), "source_revision": revision,
        "source_hash": artifact_hash(board, "prompt", "actor-v1") if artifact_hash else "",
        "input_hash": _input_hash(request),
        "shot_ids": [sid], "context": context,
        "mode": str(mode or "stateful"),
        "performances": {sid: copy.deepcopy(result)},
        "requests": {sid: copy.deepcopy(request)},
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    path = os.path.join(_candidate_dir(board_path), rid + ".json")
    _write_candidate(path, candidate)
    candidate["path"] = path
    return candidate


def _candidate_path(board_path, candidate_ref):
    ref = str(candidate_ref or "")
    directory = os.path.abspath(_candidate_dir(board_path))
    if ref.lower().endswith(".json"):
        path = os.path.abspath(ref if os.path.isabs(ref) else os.path.join(directory, ref))
        if os.path.commonpath([directory, path]) != directory:
            raise ValueError("候选路径不在项目目录内")
    else:
        path = os.path.join(directory, ref + ".json")
    if os.path.commonpath([directory, os.path.abspath(path)]) != directory:
        raise ValueError("候选路径不在项目目录内")
    if not os.path.isfile(path):
        raise FileNotFoundError("找不到演员候选：" + ref)
    return path


def list_candidates(board_path):
    directory = _candidate_dir(board_path)
    if not os.path.isdir(directory):
        return []
    rows = []
    for name in sorted(os.listdir(directory), reverse=True):
        if not name.lower().endswith(".json"):
            continue
        path = os.path.join(directory, name)
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        rows.append({key: data.get(key) for key in ("run_id", "candidate_kind", "status", "board", "source_revision", "source_hash", "shot_ids", "created_at")}|{"path": path})
    return rows


def _append_applied_run(current, candidate):
    run_id = candidate.get("run_id")
    applied = current.get("acting_applied_runs") or []
    ids = {item.get("run_id") for item in applied if isinstance(item, dict)}
    ids.update(str(item) for item in applied if isinstance(item, str))
    if run_id not in ids:
        applied.append(run_id)
    current["acting_applied_runs"] = applied[-50:]
    meta = current.get("acting_applied_runs_meta") or []
    meta = [item for item in meta if not isinstance(item, dict) or item.get("run_id") != run_id]
    meta.append({
        "run_id": run_id, "candidate_kind": candidate.get("candidate_kind"),
        "source_revision": candidate.get("source_revision"),
        "source_hash": candidate.get("source_hash", ""),
        "shot_ids": [str(item) for item in candidate.get("shot_ids") or []],
        "applied_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    current["acting_applied_runs_meta"] = meta[-50:]


def apply_candidate(board_path, candidate_ref, expected_revision=None):
    """通过 revision 检查后应用上下文或表演候选；锁定镜头、重复应用均安全。"""
    board_path = os.path.abspath(os.fspath(board_path))
    path = _candidate_path(board_path, candidate_ref)
    with open(path, encoding="utf-8") as fh:
        candidate = json.load(fh)
    if not isinstance(candidate, dict) or candidate.get("candidate_kind") not in ("context", "performance"):
        raise ValueError("只能应用演员上下文或表演候选")
    if candidate.get("status") != "ready":
        raise ValueError("演员候选未通过校验，不能应用")
    if os.path.basename(board_path) != str(candidate.get("board") or ""):
        raise ValueError("演员候选不属于当前分镜")
    board, revision = _read_board(board_path)
    source_revision = candidate.get("source_revision")
    applied = board.get("acting_applied_runs") or []
    applied_ids = {item.get("run_id") for item in applied if isinstance(item, dict)}
    applied_ids.update(str(item) for item in applied if isinstance(item, str))
    run_id = str(candidate.get("run_id") or "")
    if run_id in applied_ids:
        return {"ok": True, "idempotent": True, "run_id": run_id, "revision": revision,
                "changed_shots": [], "locked_shots": [], "stale_shots": []}
    if expected_revision is not None and str(expected_revision) != str(revision):
        raise project_store.RevisionConflict("分镜 revision 已变化，请重新生成演员候选")
    if source_revision and str(source_revision) != str(revision):
        raise project_store.RevisionConflict("演员候选基于旧 revision，请重新生成")
    chosen = _shot_ids(board, candidate.get("shot_ids") or [])
    project_dir = os.path.dirname(os.path.dirname(board_path))
    # 应用候选前校验主角白名单，防止配角状态写入正式上下文。
    candidate_context = candidate.get("context")
    if isinstance(candidate_context, dict):
        candidate_context = hydrate_actor_cards(candidate_context, project_dir, board)
    locked = [sid for sid in chosen if (_shot(board, sid).get("performance_locked") or _shot(board, sid).get("acting_locked"))]
    changed, stale = [], []
    kind = str(candidate.get("candidate_kind"))
    if kind == "context":
        context = candidate_context
        issues = validate_context(context, board)
        if issues:
            raise ValueError("演员上下文候选校验失败：" + "；".join(item["message"] for item in issues[:5]))
    else:
        performances = candidate.get("performances") or {}
        requests = candidate.get("requests") or {}
        unknown = [str(sid) for sid in performances if str(sid) not in chosen]
        if unknown:
            raise ValueError("演员候选包含未声明镜头：" + ",".join(unknown))
        for sid in chosen:
            if sid in locked:
                continue
            result = performances.get(sid)
            if not isinstance(result, dict) or result.get("status") != "ready":
                stale.append(sid)
                continue
            request = requests.get(sid) if isinstance(requests, dict) else None
            request = request if isinstance(request, dict) else build_request(board, sid, context=candidate_context, project_dir=project_dir)
            _ensure_main_actor_request(request, board, project_dir=project_dir)
            packet = result.get("packet") if isinstance(result.get("packet"), dict) else result
            errors = validate_performance(packet, request)
            if errors:
                raise ValueError("演员候选校验失败：" + "; ".join(item["message"] for item in errors[:5]))
            changed.append(sid)

    def mutate(current):
        current_applied = current.get("acting_applied_runs") or []
        ids = {item.get("run_id") for item in current_applied if isinstance(item, dict)}
        ids.update(str(item) for item in current_applied if isinstance(item, str))
        if run_id in ids:
            return
        statuses = current.get("acting_status")
        if not isinstance(statuses, dict):
            statuses = {}
        if kind == "context":
            current["acting_context"] = copy.deepcopy(candidate_context or {})
            for sid in chosen:
                statuses[sid] = "locked" if sid in locked else "context_ready"
        else:
            performances = candidate.get("performances") or {}
            for sid in changed:
                shot = _shot(current, sid)
                result = copy.deepcopy(performances[sid])
                result["source_hash"] = candidate.get("source_hash", "")
                result["candidate_run_id"] = run_id
                result["mode"] = str(candidate.get("mode") or result.get("mode") or "stateful")
                shot["performance"] = result
                shot["acting_status"] = "ready"
                statuses[sid] = "ready"
            for sid in locked:
                statuses.setdefault(sid, "locked")
            for sid in stale:
                statuses[sid] = "stale"
        current["acting_status"] = statuses
        _append_applied_run(current, candidate)
        if kind == "performance" and candidate_context:
            current["acting_context"] = copy.deepcopy(candidate_context)

    snapshot = getattr(versions, "snapshot", None) if versions else None
    _, new_revision = project_store.update_json(board_path, mutate, expected_revision=revision, snapshot=snapshot)
    return {"ok": True, "idempotent": False, "run_id": run_id, "revision": new_revision,
            "changed_shots": changed, "locked_shots": locked, "stale_shots": stale}


def set_shot_lock(board_path, shot_ids, locked, expected_revision=None):
    """锁定或解锁已选镜头的演员结果；只改元数据，不改镜头事实。"""
    board_path = _resolve_board_path(board_path)
    board, revision = _read_board(board_path)
    chosen = _shot_ids(board, shot_ids)
    desired = bool(locked)
    snapshot = getattr(versions, "snapshot", None) if versions else None
    def mutate(current):
        for sid in chosen:
            shot = _shot(current, sid)
            shot["performance_locked"] = desired
            statuses = current.get("acting_status")
            if not isinstance(statuses, dict):
                statuses = {}
            statuses[sid] = "locked" if desired else ("ready" if isinstance(shot.get("performance"), dict) and shot["performance"].get("status") == "ready" else "pending")
            current["acting_status"] = statuses
    _, new_revision = project_store.update_json(board_path, mutate,
                                                expected_revision=expected_revision or revision,
                                                snapshot=snapshot)
    return {"ok": True, "revision": new_revision, "shot_ids": chosen, "locked": desired}

def _save_performance_bundle(board_path, board, revision, chosen, results, requests, context,
                             run_id=None, mode="stateful", model_params=None):
    rid = str(run_id or _new_run_id("performance"))
    ready = all(isinstance(results.get(sid), dict) and results[sid].get("status") == "ready" for sid in chosen)
    candidate = {
        "run_id": rid, "candidate_kind": "performance",
        "status": "ready" if ready else "invalid",
        "board": os.path.basename(board_path), "source_revision": revision,
        "source_hash": artifact_hash(board, "prompt", "actor-v1") if artifact_hash else "",
        "input_hash": _input_hash({"requests": requests, "context": context, "mode": mode}),
        "shot_ids": list(chosen), "context": copy.deepcopy(context),
        "performances": copy.deepcopy(results), "requests": copy.deepcopy(requests),
        "mode": mode, "model_params": copy.deepcopy(model_params or {}),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    path = os.path.join(_candidate_dir(board_path), rid + ".json")
    _write_candidate(path, candidate)
    candidate["path"] = path
    return candidate


def run_performance(board_path, shot_ids, call_llm, *, context=None, mode="stateful",
                   max_attempts=2, run_id=None, model_params=None):
    """串行生成多镜表演候选；网络调用由调用方注入，正式分镜保持不变。"""
    board_path = _resolve_board_path(board_path)
    board, revision = _read_board(board_path)
    chosen = _shot_ids(board, shot_ids)
    project_dir = os.path.dirname(os.path.dirname(board_path))
    active_context = copy.deepcopy(context) if isinstance(context, dict) else copy.deepcopy(
        board.get("acting_context") or default_context(board, project_dir))
    active_context = hydrate_actor_cards(active_context, project_dir, board)
    issues = validate_context(active_context, board)
    if issues:
        raise ValueError("演员上下文校验失败：" + "；".join(item["message"] for item in issues[:5]))
    results, requests = {}, {}
    for sid in chosen:
        request = build_request(board, sid, context=(active_context if mode == "stateful" else None), project_dir=project_dir)
        _ensure_main_actor_request(request, board, project_dir=project_dir)
        request["mode"] = mode
        try:
            from prompt_modules import actor_perform_prompt
            skill_text = None
            try:
                import skill_lib
                skill_text = skill_lib.style_for(os.path.dirname(os.path.dirname(board_path)), "acting") or None
            except Exception:
                pass
            system_prompt, _ = actor_perform_prompt(request, skill_text)
            request["system_prompt"] = system_prompt
        except Exception:
            pass
        requests[sid] = request
        results[sid] = run(request, call_llm, max_attempts=max_attempts)
    return _save_performance_bundle(board_path, board, revision, chosen, results, requests,
                                    active_context, run_id=run_id, mode=mode,
                                    model_params=model_params)


def compile_for_creation(board_path, shot_id, *, mode="stateful", media_type="video"):
    """读取当前分镜并统一编译创作出口提示词。"""
    board_path = _resolve_board_path(board_path)
    board, _ = _read_board(board_path)
    try:
        from prompt_compiler import compile_shot
    except ImportError:
        _HERE = os.path.dirname(os.path.abspath(__file__))
        if _HERE not in sys.path:
            sys.path.insert(0, _HERE)
        from prompt_compiler import compile_shot
    return compile_shot(board, str(shot_id), mode=mode, media_type=media_type)


def _cli():
    ap = argparse.ArgumentParser(description="演员上下文/表演候选管线")
    sub = ap.add_subparsers(dest="command", required=True)
    p_prepare = sub.add_parser("prepare"); p_prepare.add_argument("project"); p_prepare.add_argument("--board", required=True); p_prepare.add_argument("--shots", default="")
    p_apply = sub.add_parser("apply"); p_apply.add_argument("project"); p_apply.add_argument("--board", required=True); p_apply.add_argument("--run-id", required=True); p_apply.add_argument("--expected-revision")
    p_compile = sub.add_parser("compile"); p_compile.add_argument("project"); p_compile.add_argument("--board", required=True); p_compile.add_argument("--shot", required=True); p_compile.add_argument("--mode", default="baseline"); p_compile.add_argument("--media-type", default="video")
    args = ap.parse_args()
    path = _resolve_board_path(args.project, args.board)
    if args.command == "prepare":
        ids = [item for item in str(args.shots).split(",") if item.strip()] or None
        result = prepare_context(path, ids)
    elif args.command == "apply":
        result = apply_candidate(path, args.run_id, expected_revision=args.expected_revision)
    else:
        result = compile_for_creation(path, args.shot, mode=args.mode, media_type=args.media_type)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


__all__ = ["build_request", "run", "apply_result", "default_context", "prepare_context",
           "save_performance_candidate", "run_performance", "compile_for_creation", "set_shot_lock",
           "list_candidates", "apply_candidate", "main_actor_ids", "main_actor_records",
           "actor_ids_for_shot", "actor_metadata", "_read_board"]


if __name__ == "__main__":
    import argparse
    raise SystemExit(_cli())

















