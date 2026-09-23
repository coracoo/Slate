# -*- coding: utf-8 -*-
"""角色表演草稿运行时：注入模型调用，最多一次有限修正。"""

import hashlib
import json
import re
import sys
from typing import Any, Callable, List

try:
    from .actor_contract import validate_performance
except ImportError:
    from actor_contract import validate_performance

try:
    # 工作台运行时若已把通用工具目录加入路径，优先复用统一解析器。
    from llm_result import parse_structured as _shared_parse_structured
except Exception:
    _shared_parse_structured = None

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def _issue(code: str, path: str, message: str, severity: str = "error") -> dict:
    return {"code": code, "path": path, "message": message, "severity": severity}


def _first_object(text: str) -> tuple[str | None, bool]:
    start = text.find("{")
    if start < 0:
        return None, False
    depth = 0
    quoted = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1], True
    return text[start:], False


def _parse_json(content: Any, finish_reason: str | None = None) -> dict:
    if isinstance(content, dict):
        return content
    if not isinstance(content, str) or not content.strip():
        raise ValueError("模型没有返回 JSON")
    if _shared_parse_structured is not None:
        parsed = _shared_parse_structured(content, finish_reason=finish_reason)
        if not parsed.get("complete"):
            notes = "；".join(str(item) for item in parsed.get("repair_notes", []))
            raise ValueError(notes or "模型结构化输出不完整")
        data = parsed.get("data")
        if not isinstance(data, dict):
            raise ValueError("模型 JSON 顶层必须是对象")
        return data
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].lstrip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    fragment, balanced = _first_object(text)
    if not fragment or not balanced:
        raise ValueError("模型结构化输出不完整")
    try:
        result = json.loads(fragment)
    except json.JSONDecodeError:
        result = json.loads(re.sub(r",\s*([}\]])", r"\1", fragment))
    if not isinstance(result, dict):
        raise ValueError("模型 JSON 顶层必须是对象")
    return result


def _input_hash(request: dict) -> str:
    payload = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _messages(request: dict, previous_errors: List[dict]) -> list[dict]:
    system = request.get("system_prompt") or (
        "你是职业演员，负责扮演请求中的角色。只输出 JSON 表演草稿；\n"
        "只写可见的动作、视线、姿态、语气和简短 intent；不得新增台词、改变剧情、时长、走位或机位。"
    )
    system = str(system).strip() + (
        "\n\n输出格式必须是 JSON 对象：{\"shot_id\":\"请求镜号\",\"actors\":[{\"actor_id\":\"角色ID\",\"beats\":[{\"at\":0,\"duration\":1,\"intent\":\"\",\"posture\":\"\",\"gaze\":\"\",\"gesture\":\"\",\"expression\":\"\",\"voice\":\"\"}]}]}。"
        "只输出演员可控字段；每个 beat 必须完整落在镜头时长内；如请求提供 beat_contexts，必须只依据该 beat 的可见上下文和 after_event_ids，不得提前使用后续事件。"
    )
    user_payload = {key: value for key, value in request.items() if key != "system_prompt"}
    user = "请为固定镜头生成角色表演 beats，保持每个 beat 在镜头时长内。\n" + json.dumps(
        user_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    )
    if previous_errors:
        user += "\n上一次输出未通过程序校验，请只修正这些问题后重新输出：\n" + json.dumps(
            previous_errors, ensure_ascii=False, separators=(",", ":"),
        )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _label_beat_time_roles(packet: dict) -> None:
    """按节拍顺序给每个角色的 beats 默认打时间角色（update.md E07）。

    首拍 ``start``、末拍 ``end``、中间拍 ``beat``；单拍镜头标 ``end``
    （与图片模式取末拍的旧行为一致）。按 ``at`` 稳定排序后再打标，
    乱序输出的 beats 也得到正确首末拍。只在生成时运行一次；
    人工在分镜 JSON 上改标后不会再经本函数，修改得以保留。
    """
    if not isinstance(packet, dict):
        return
    for actor in packet.get("actors") or []:
        if not isinstance(actor, dict):
            continue
        beats = [beat for beat in (actor.get("beats") or []) if isinstance(beat, dict)]
        count = len(beats)
        if not count:
            continue
        # 校验已通过，at/duration 必为有限数值，可直接排序。
        order = sorted(range(count), key=lambda i: float(beats[i].get("at") or 0))
        for rank, index in enumerate(order):
            if count == 1:
                role = "end"
            elif rank == 0:
                role = "start"
            elif rank == count - 1:
                role = "end"
            else:
                role = "beat"
            beats[index]["time_role"] = role


def perform(request: dict, call_llm: Callable[[list[dict]], dict], max_attempts: int = 2) -> dict:
    """生成并校验表演草稿。

    ``max_attempts`` 只控制最多两次生成（初次加一次修正）；网络层的重试由
    外部客户端负责，避免本层把修正次数和网络重试相乘。
    """
    budget = min(2, max(1, int(max_attempts)))
    attempts = 0
    errors: List[dict] = []
    warnings: List[dict] = []
    packet: dict = {}
    source_hash = _input_hash(request if isinstance(request, dict) else {})

    while attempts < budget:
        attempts += 1
        messages = _messages(request if isinstance(request, dict) else {}, errors)
        try:
            response = call_llm(messages)
        except Exception as exc:
            errors = [_issue("LLM_CALL_ERROR", "call_llm", f"模型调用失败：{exc}")]
            continue
        if not isinstance(response, dict):
            errors = [_issue("INVALID_LLM_RESULT", "response", "模型响应必须是对象")]
            continue
        finish_reason = response.get("finish_reason")
        content = response.get("content")
        if finish_reason in {"length", "max_tokens", "partial", "incomplete"}:
            errors = [_issue("PARTIAL_OUTPUT", "response.content", "模型输出被截断，不能作为完成结果")]
            continue
        try:
            packet = _parse_json(content, finish_reason=finish_reason)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            errors = [_issue("PARSE_ERROR", "response.content", f"无法解析模型 JSON：{exc}")]
            continue
        errors = validate_performance(packet, request if isinstance(request, dict) else {})
        if errors:
            continue
        # 校验通过的节拍按顺序默认打时间角色（start/beat/end），供首帧/图片模式按角色取图。
        _label_beat_time_roles(packet)
        check = packet.get("ooc_check")
        if isinstance(check, dict) and check.get("passed") is False:
            warnings.append(_issue("SEMANTIC_REVIEW", "ooc_check", "模型自检认为可能崩人设，需人工复核", "warning"))
        if attempts > 1:
            warnings.append(_issue("RETRY_USED", "attempts", "表演草稿经过一次程序校验修正", "warning"))
        return {
            "status": "ready",
            "actors": packet.get("actors", []),
            "packet": packet,
            "errors": [],
            "warnings": warnings,
            "attempts": attempts,
            "input_hash": source_hash,
        }

    return {
        "status": "invalid",
        "actors": packet.get("actors", []) if isinstance(packet, dict) else [],
        "packet": packet,
        "errors": errors,
        "warnings": warnings,
        "attempts": attempts,
        "input_hash": source_hash,
    }


__all__ = ["perform"]





