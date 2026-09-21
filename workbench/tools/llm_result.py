# -*- coding: utf-8 -*-
"""结构化 LLM 结果解析；明确区分可修复尾逗号与真正截断。"""
import json
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def _first_object(text):
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    quoted = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if quoted:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                quoted = False
            continue
        if c == '"':
            quoted = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1], True
    return text[start:], False


def _salvage(fragment):
    """保留顶层数组中已闭合的对象，供草稿审阅，不宣称完成。"""
    m = re.match(r"\s*\{\s*([\"']?)([A-Za-z_][\w-]*)\1\s*:\s*\[", fragment)
    if not m:
        return {}
    key = m.group(2)
    array_start = m.end()
    items = []
    pos = array_start
    while pos < len(fragment):
        while pos < len(fragment) and fragment[pos] in " \t\r\n,":
            pos += 1
        if pos >= len(fragment) or fragment[pos] != "{":
            break
        item, complete = _first_object(fragment[pos:])
        if not item or not complete:
            break
        try:
            items.append(json.loads(item))
        except ValueError:
            break
        pos += len(item)
    return {key: items}


def parse_structured(text, finish_reason=None):
    """返回 {data, complete, repair_notes}；finish_reason=length 永不完整。"""
    raw = str(text or "").strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I | re.S).strip()
    obj, balanced = _first_object(raw)
    if not obj:
        return {"data": {}, "complete": False, "repair_notes": ["无 JSON 对象"]}
    try:
        data = json.loads(obj)
        complete = bool(balanced and finish_reason not in ("length", "max_tokens"))
        return {"data": data, "complete": complete, "repair_notes": [] if complete else ["输出被截断"]}
    except ValueError:
        repaired = re.sub(r",\s*([}\]])", r"\1", obj)
        try:
            data = json.loads(repaired)
            complete = bool(balanced and finish_reason not in ("length", "max_tokens"))
            return {"data": data, "complete": complete, "repair_notes": ["去除尾逗号"] if complete else ["输出被截断"]}
        except ValueError:
            data = _salvage(obj)
            return {"data": data, "complete": False, "repair_notes": ["保留已闭合对象；原输出截断或非法"]}
