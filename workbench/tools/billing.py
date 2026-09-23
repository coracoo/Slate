# -*- coding: utf-8 -*-
"""模型调用计费账本（billing ledger）。

账本文件 ``workbench/billing/ledger.jsonl`` 为 append-only，每行一条 JSON：
  {ts, vendor, kind, model, op, ok, cost, currency, usage?, units?, project?, source?, error?}
- kind ∈ text/vision/image/image_edit/video/music/speech/asr/voice；
  op 如 chat/generate_image/generate_video。
- ok=true 才计费：cost 按厂商价格表算出，无价格配置则 cost=null 但仍记录调用；
  ok=false 记录 error 摘要、cost=0。
- usage={prompt_tokens,completion_tokens,total_tokens}（文本类）；
  units 如 {images:1}/{seconds:5}/{chars:120}。

价格配置读取厂商记录的可选 ``pricing`` 字段：
  pricing: {"currency":"CNY",
    "text"|"vision": {"<model>": {"input": 每百万token价, "output": 每百万token价}},
    "image": {"<model>": {"per_image": x}},
    "video": {"<model>": {"per_second": x}},
    "speech": {"<model>": {"per_char": x 或 "per_call": x}},
    "music"|"asr"|"voice": {"<model>": {"per_call": x}}}
  model 键找不到时回落 "*" 通配键；image_edit 未单独定价时回落 image 表。

所有写读异常一律静默——记账绝不能炸主流程。
"""
import json
import os
import threading
import time

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "billing")
LEDGER = os.path.normpath(os.path.join(_DIR, "ledger.jsonl"))
_LOCK = threading.Lock()


def _now():
    """本地时间戳 YYYY-MM-DD HH:MM:SS，月份聚合直接按字符串前缀过滤。"""
    return time.strftime("%Y-%m-%d %H:%M:%S")


def record(entry):
    """追加一条账本记录（线程安全；目录自动创建；异常静默返回 False）。"""
    try:
        if not isinstance(entry, dict):
            return False
        row = dict(entry)
        row.setdefault("ts", _now())
        with _LOCK:
            os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
            with open(LEDGER, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        return True
    except Exception:
        return False


def _rate_table(pricing, kind):
    """取某 kind 的单价表；image_edit 未单独配置时回落 image。"""
    table = pricing.get(kind)
    if not isinstance(table, dict) and kind == "image_edit":
        table = pricing.get("image")
    return table if isinstance(table, dict) else {}


def _num(value):
    """宽松转 float；失败返回 None。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def price_of(vendor_cfg, kind, model, usage=None, units=None):
    """按厂商 pricing 计价，返回 (cost, currency)；无价格配置或无法计量返回 (None, None)。"""
    pricing = (vendor_cfg or {}).get("pricing")
    if not isinstance(pricing, dict):
        return (None, None)
    currency = str(pricing.get("currency") or "CNY")
    table = _rate_table(pricing, kind)
    rate = table.get(model) if model else None
    if not isinstance(rate, dict):
        rate = table.get("*")
    if not isinstance(rate, dict):
        return (None, None)
    usage = usage if isinstance(usage, dict) else {}
    units = units if isinstance(units, dict) else {}
    cost = None
    if kind in ("text", "vision"):
        if "input" in rate or "output" in rate:
            inp = _num(usage.get("prompt_tokens") or usage.get("input_tokens")) or 0.0
            out = _num(usage.get("completion_tokens") or usage.get("output_tokens")) or 0.0
            cost = (inp * (_num(rate.get("input")) or 0.0) +
                    out * (_num(rate.get("output")) or 0.0)) / 1e6
        elif "per_call" in rate:
            cost = _num(rate.get("per_call"))
    elif kind in ("image", "image_edit"):
        if "per_image" in rate:
            cost = (_num(rate.get("per_image")) or 0.0) * (_num(units.get("images")) or 1.0)
    elif kind == "video":
        seconds = _num(units.get("seconds"))
        if "per_second" in rate and seconds:
            cost = _num(rate.get("per_second")) * seconds
        elif "per_call" in rate:
            cost = _num(rate.get("per_call"))
    elif kind == "speech":
        chars = _num(units.get("chars"))
        if "per_char" in rate and chars:
            cost = _num(rate.get("per_char")) * chars
        elif "per_call" in rate:
            cost = _num(rate.get("per_call"))
    elif kind in ("music", "asr", "voice"):
        if "per_call" in rate:
            cost = _num(rate.get("per_call"))
        elif kind == "asr" and "per_second" in rate and _num(units.get("seconds")):
            cost = _num(rate.get("per_second")) * _num(units.get("seconds"))
    if cost is None:
        return (None, None)
    return (round(cost, 6), currency)


def bill(vendor_cfg=None, *, vendor=None, kind="", model="", op="", ok=True,
         usage=None, units=None, project=None, source=None, error=None):
    """组装并写入一条账本；ok=true 时按 pricing 计价。任何异常静默返回 False。"""
    try:
        if ok:
            cost, currency = price_of(vendor_cfg, kind, model, usage, units)
        else:
            cost, currency = 0, None
        entry = {"ts": _now(), "vendor": vendor or (vendor_cfg or {}).get("id") or "",
                 "kind": str(kind or ""), "model": str(model or ""), "op": str(op or ""),
                 "ok": bool(ok), "cost": cost, "currency": currency}
        if isinstance(usage, dict) and usage:
            entry["usage"] = usage
        if isinstance(units, dict) and units:
            entry["units"] = units
        if project:
            entry["project"] = str(project)
        if source:
            entry["source"] = str(source)
        if error:
            entry["error"] = str(error)[:300]
        return record(entry)
    except Exception:
        return False


def _rows(month=None):
    """读账本全部行；month="YYYY-MM" 时只取该月。坏行跳过，异常返回空表。"""
    try:
        if not os.path.isfile(LEDGER):
            return []
        out = []
        with open(LEDGER, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                if month and not str(row.get("ts") or "").startswith(month):
                    continue
                out.append(row)
        return out
    except Exception:
        return []


def recent(limit=100, month=None):
    """最近 limit 条账本记录（新在前）；limit<=0 返回全部（新在前）。"""
    rows = _rows(month)
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 100
    return rows[-limit:][::-1] if limit > 0 else rows[::-1]


def summary(month=None):
    """聚合：按 vendor×kind 分组 calls/ok/fail/cost + 总合计 + 按天。cost 按币种分桶。"""
    groups = {}
    days = {}
    total = {"calls": 0, "ok": 0, "fail": 0, "cost": {}}

    def accumulate(bucket, row):
        bucket["calls"] += 1
        if row.get("ok"):
            bucket["ok"] += 1
        else:
            bucket["fail"] += 1
        cost = row.get("cost")
        if cost:  # None 与 0（免费/失败）不进费用合计
            cur = str(row.get("currency") or "?")
            bucket["cost"][cur] = round(bucket["cost"].get(cur, 0.0) + float(cost), 6)

    for row in _rows(month):
        key = (str(row.get("vendor") or ""), str(row.get("kind") or ""))
        group = groups.setdefault(key, {"vendor": key[0], "kind": key[1],
                                        "calls": 0, "ok": 0, "fail": 0, "cost": {}})
        day = str(row.get("ts") or "")[:10]
        daily = days.setdefault(day, {"date": day, "calls": 0, "ok": 0, "fail": 0, "cost": {}})
        for bucket in (group, daily, total):
            accumulate(bucket, row)
    return {"month": month, "total": total,
            "groups": sorted(groups.values(), key=lambda g: (g["vendor"], g["kind"])),
            "days": [days[k] for k in sorted(days)]}
