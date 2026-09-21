# -*- coding: utf-8 -*-
"""制作产物与全局资产修订状态辅助函数。

本模块只计算依赖和失效范围，不调用模型，也不删除或覆盖任何媒体文件。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from asset_registry import AssetRegistry, AssetReferenceError


def asset_revision(item: dict) -> int:
    """读取资产修订号；兼容没有该字段的旧资产。"""
    try:
        value = int(item.get("asset_revision", 1))
    except (AttributeError, TypeError, ValueError):
        value = 1
    return max(1, value)


def bump_asset_revision(item: dict) -> int:
    """递增并写回资产修订号，返回新值。"""
    value = asset_revision(item) + 1
    item["asset_revision"] = value
    return value


def _normal_ref(value: str) -> str:
    raw = str(value or "").strip()
    return raw if raw.startswith("@") else "@" + raw


def asset_dependency_refs(project_dir: str, refs: list[str]) -> list[str]:
    """返回引用自身、父级链和关联引用，按遍历顺序去重。"""
    registry = AssetRegistry(project_dir)
    by_ref = {row.get("ref"): row for row in registry.list() if row.get("ref")}
    queue = [_normal_ref(ref) for ref in refs or [] if str(ref or "").strip()]
    result, seen = [], set()
    while queue:
        ref = queue.pop(0)
        if ref in seen:
            continue
        seen.add(ref)
        try:
            row = by_ref.get(ref) or registry.resolve(ref)
        except AssetReferenceError:
            continue
        canonical = row.get("ref") or ref
        if canonical in seen and canonical != ref:
            continue
        seen.add(canonical)
        result.append(canonical)
        parent = row.get("parent_ref")
        if parent:
            queue.append(str(parent))
        for related in row.get("related_refs") or []:
            if related:
                queue.append(str(related))
    return result


def _iter_storyboards(project_dir: str):
    folder = Path(project_dir) / "分镜"
    if not folder.is_dir():
        return
    for path in sorted(folder.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if isinstance(data, dict) and isinstance(data.get("shots"), list):
            yield path, data


def mark_stale_for_asset(project_dir: str, changed_refs: list[str]) -> dict:
    """计算引用变更影响的镜头；调用方决定是否持久化 stale 状态。"""
    changed = set(asset_dependency_refs(project_dir, changed_refs))
    shots, reasons = [], {}
    for path, board in _iter_storyboards(project_dir):
        for shot in board.get("shots") or []:
            if not isinstance(shot, dict) or not shot.get("id"):
                continue
            refs = [str(ref) for ref in (shot.get("asset_refs") or shot.get("refs") or []) if ref]
            dependencies = set(asset_dependency_refs(project_dir, refs)) if refs else set()
            hit = sorted(changed.intersection(dependencies))
            if hit:
                sid = str(shot["id"])
                if sid not in shots:
                    shots.append(sid)
                reasons[sid] = f"依赖资产已更新：{'、'.join(hit)}"
    return {"shots": shots, "reasons": reasons}


__all__ = ["asset_revision", "bump_asset_revision", "asset_dependency_refs", "mark_stale_for_asset"]
