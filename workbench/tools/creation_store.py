# -*- coding: utf-8 -*-
"""创作清单 v2 的纯数据辅助函数。"""
import datetime
import time


def make_item(item_id, media_type, prompt, *, board="", shot_id="", prompt_user="",
              prompt_assembled="", negative="", refs=None, vendor_id="", note="", asset_context=None,
              source_hash="", acting_mode="", actor_performance_used=False, actor_warnings=None,
              prompt_json=None, asset_refs=None, panel_id="", compiled_request=None, panel_draft=None,
              prompt_stage="", prompt_system="", prompt_revision="", asset_revisions=None,
              image_mode="generate"):
    """构造可追溯的单镜条目，同时保留 v1 prompt 字段兼容旧前端。"""
    now = datetime.datetime.now().isoformat(timespec="seconds")
    return {
        "id": item_id, "type": media_type, "image_mode": image_mode or "generate", "prompt": prompt,
        "panel_id": panel_id or "", "compiled_request": compiled_request if isinstance(compiled_request,dict) else None,
        "panel_draft": panel_draft if isinstance(panel_draft,dict) else None,
        "prompt_stage": prompt_stage or "", "prompt_system": prompt_system or "",
        "prompt_revision": prompt_revision or "", "asset_revisions": dict(asset_revisions or {}),
        "board": board or "", "shot_id": shot_id or "",
        "prompt_user": prompt_user or "", "prompt_assembled": prompt_assembled or "",
        "prompt_json": prompt_json if isinstance(prompt_json, dict) else None,
        "asset_refs": list(asset_refs or []),
        "negative": negative or "", "refs": list(refs or []),
        "vendor_id": vendor_id, "status": "running", "created_at": now,
        "updated_at": now, "outputs": [], "note": note or "",
        "asset_context": asset_context if isinstance(asset_context, dict) else {},
        "source_hash": source_hash or "", "acting_mode": acting_mode or "",
        "actor_performance_used": bool(actor_performance_used), "actor_warnings": list(actor_warnings or []),
    }


def coverage_matrix(items, shot_ids, media_type=None):
    """按镜头返回最新状态，供覆盖率卡片和重跑入口复用。"""
    latest = {}
    for item in items or []:
        if not isinstance(item, dict) or not item.get("shot_id") or (media_type and item.get("type") != media_type):
            continue
        latest[str(item["shot_id"])] = item
    rows = []
    counts = {"total": 0, "done": 0, "running": 0, "error": 0, "pending": 0}
    for sid in shot_ids or []:
        sid = str(sid)
        item = latest.get(sid)
        status = str(item.get("status") or "pending") if item else "pending"
        if status not in ("done", "running", "queued", "generating", "awaiting_import", "error"):
            status = "pending"
        if status not in counts:
            counts[status] = 0
        row = {"shot_id": sid, "status": status, "item_ids": [item.get("id")] if item else []}
        rows.append(row)
        counts["total"] += 1
        counts[status] += 1
    return {"shots": rows, "counts": counts}



