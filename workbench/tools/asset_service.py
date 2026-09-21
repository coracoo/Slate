# -*- coding: utf-8 -*-
"""全局资产的增改服务。

服务只写对应的三件套 JSON，并在每次实际设定修改前做版本快照。关系字段
继续由 asset_relations 归一化，媒体文件和分镜不会在这里被覆盖。
"""
from __future__ import annotations

import copy
import json
import os
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
CORE = os.path.abspath(os.path.join(HERE, "..", "..", "previs_system", "tools"))
if CORE not in sys.path:
    sys.path.insert(0, CORE)

import asset_relations
import project_store
from asset_registry import AssetRegistry, AssetReferenceError, normalize_asset_ref
from production_state import bump_asset_revision, mark_stale_for_asset

_META = {
    "character": ("人物.json", "characters", "人物"),
    "scene": ("场景.json", "scenes", "场景"),
    "prop": ("道具.json", "props", "道具"),
}


def _paths(project_dir: str):
    root = Path(project_dir).resolve()
    return {kind: (root / "素材" / filename, key, zone) for kind, (filename, key, zone) in _META.items()}


def _read(path: Path, key: str):
    if not path.is_file():
        return {key: []}, []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError(f"无法读取 {path.name}：{exc}") from exc
    rows = raw.get(key) if isinstance(raw, dict) else raw
    return (raw if isinstance(raw, dict) else {key: rows if isinstance(rows, list) else []},
            list(rows) if isinstance(rows, list) else [])


def _load(project_dir: str):
    files = _paths(project_dir)
    file_data, combined = {}, {}
    for kind, (path, key, _zone) in files.items():
        raw, rows = _read(path, key)
        file_data[kind] = (path, raw, key)
        combined[key] = copy.deepcopy([row for row in rows if isinstance(row, dict)])
    return files, file_data, combined


def _ref(kind: str, ident: str) -> str:
    return f"@{kind}:{ident}"


def _parse_ref(value: str):
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("资产 ref 不能为空")
    try:
        normalized = normalize_asset_ref(raw)
    except AssetReferenceError as exc:
        raise ValueError(str(exc)) from exc
    if not normalized.startswith("@") or ":" not in normalized:
        raise ValueError("资产 ref 必须包含 @kind:id")
    kind, ident = normalized[1:].split(":", 1)
    if kind not in _META or not ident:
        raise ValueError(f"资产 ref 不合法：{value}")
    return kind, ident, normalized


def _target_row(combined, kind, ident):
    key = _META[kind][1]
    return next((row for row in combined.get(key, [])
                 if isinstance(row, dict) and str(row.get("id") or "") == ident), None)


def _write_changed(file_data, normalized, changed_kinds, snapshot):
    for kind in changed_kinds:
        path, raw, key = file_data[kind]
        rows = normalized.get(key, [])
        current = raw.get(key, []) if isinstance(raw, dict) else raw
        if current == rows:
            continue

        def mutate(data, key=key, rows=rows):
            if not isinstance(data, dict):
                data = {}
            data[key] = rows
            return data

        project_store.update_json(str(path), mutate, create_default={key: rows}, snapshot=snapshot)


def _snapshot():
    try:
        import versions
        return versions.snapshot
    except Exception:
        return None


def _validate_target(normalized, kind, ident):
    target = _target_row(normalized, kind, ident)
    if target is None:
        raise ValueError(f"资产未写入：{_ref(kind, ident)}")
    target_ref = _ref(kind, ident)
    # 关系归一化会保留 issues；只阻止当前对象的问题，旧档案中的孤立
    # 引用作为 warning，不阻断新素材保存。
    return target, target_ref


def _prompt_refs(text: str, combined: dict) -> list[str]:
    """提取提示词中的全局资产引用，并归一为项目内稳定 ref。"""
    extractor = getattr(asset_relations, "extract_asset_refs", None)
    if not extractor:
        return []
    return list(extractor(str(text or ""), combined) or [])


def _sync_prompt_relations(item: dict, prompt_refs: list[str]) -> None:
    """把提示词引用并入 related_refs，同时保留手工关联。

    ``prompt_refs`` 是提示词自动解析出的集合；其它 related_refs 视为手工
    关联。再次编辑提示词时，只会移除已经不在提示词中的自动关联，不会
    误删用户通过关系面板建立的手工关联。
    """
    old_prompt_refs = {
        str(value).strip() for value in (item.get("prompt_refs") or [])
        if str(value).strip()
    }
    manual_refs = []
    for value in item.get("related_refs") or []:
        ref = str(value).strip()
        if ref and ref not in old_prompt_refs and ref not in manual_refs:
            manual_refs.append(ref)
    merged = manual_refs[:]
    for ref in prompt_refs:
        if ref and ref not in merged:
            merged.append(ref)
    if merged:
        item["related_refs"] = merged
    else:
        item.pop("related_refs", None)
    if prompt_refs:
        item["prompt_refs"] = list(prompt_refs)
    else:
        item.pop("prompt_refs", None)


def create_asset(project_dir: str, *, kind: str, id: str | None = None,
                 name: str, prompt: str = "", parent_ref: str | None = None,
                 relation: str | None = None, related_refs: list[str] | None = None,
                 episode: str | None = None, fields: dict | None = None) -> dict:
    """创建母素材或子素材，返回规范化后的注册表记录。"""
    project_dir = os.path.abspath(os.fspath(project_dir))
    kind = str(kind or "").strip()
    if kind not in _META or not str(name or "").strip():
        raise ValueError("项目、kind 或素材名不合法")
    files, file_data, combined = _load(project_dir)
    ident = str(id or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,63}", ident):
        ident = re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")[:56] or "asset"
    if any(str(row.get("id") or "") == ident for rows in combined.values() for row in rows):
        raise ValueError(f"资产 id 已存在：{ident}")
    item = {"id": ident, "name": str(name).strip(), "asset_revision": 1,
            "source_episode_ids": [str(episode).strip()] if str(episode or "").strip() else []}
    if kind == "character":
        item.update({"role": "配角" if parent_ref else "主角", "is_collective": False,
                     "basis": "手工新增素材", "sheet_prompt": str(prompt or "").strip()})
    elif kind == "scene":
        item.update({"time": "日", "light": "日光", "interior": True, "geometry": [],
                     "image_prompt": str(prompt or "").strip()})
    else:
        item.update({"kind": "关联素材" if parent_ref else "叙事", "asset_required": True,
                     "owner": str(parent_ref or ""), "actions": [],
                     "image_prompt": str(prompt or "").strip(), "shot_hint": None})
    if isinstance(fields, dict):
        for key, value in fields.items():
            if key not in ("id", "ref", "kind", "asset_revision", "children_refs"):
                item[str(key)] = value
    if parent_ref:
        item["parent_ref"] = parent_ref
        item["relation"] = relation or ("located_in" if str(parent_ref).startswith("@scene:") else "component_of")
    if isinstance(related_refs, list) and related_refs:
        item["related_refs"] = list(related_refs)
    prompt_field = "sheet_prompt" if kind == "character" else "image_prompt"
    _sync_prompt_relations(item, _prompt_refs(item.get(prompt_field, ""), combined))
    combined[_META[kind][1]].append(item)
    normalized, issues = asset_relations.normalize_asset_relations(combined)
    target, target_ref = _validate_target(normalized, kind, ident)
    blocking = [issue for issue in issues if issue.get("ref") == target_ref]
    if blocking:
        raise ValueError("新素材关系无法解析：" + "；".join(str(x.get("message")) for x in blocking))
    _write_changed(file_data, normalized, {kind}, _snapshot())
    registry = AssetRegistry(project_dir)
    return registry.resolve(target_ref)


def edit_asset(project_dir: str, ref: str, patch: dict, *, expected_revision: str | None = None) -> dict:
    """编辑单个母/子素材设定并递增该素材修订号。"""
    if not isinstance(patch, dict):
        raise ValueError("patch 必须是对象")
    project_dir = os.path.abspath(os.fspath(project_dir))
    kind, ident, target_ref = _parse_ref(ref)
    _files, file_data, combined = _load(project_dir)
    current = _target_row(combined, kind, ident)
    if current is None:
        raise ValueError(f"找不到资产：{target_ref}")
    if expected_revision not in (None, ""):
        try:
            expected = int(expected_revision)
        except (TypeError, ValueError) as exc:
            raise ValueError("expected_revision 必须是整数") from exc
        try:
            actual = max(1, int(current.get("asset_revision", 1)))
        except (TypeError, ValueError):
            actual = 1
        if expected != actual:
            raise ValueError(f"资产版本已变化：当前 v{actual}，提交的是 v{expected}，请重新读取后再保存")
    before = copy.deepcopy(current)
    prompt_changed = False
    prompt_value = ""
    for key, value in patch.items():
        key = str(key)
        if key in ("id", "ref", "kind", "children_refs"):
            continue
        if key == "prompt":
            prompt_changed = True
            prompt_value = str(value or "").strip()
            key = "sheet_prompt" if kind == "character" else "image_prompt"
        if value in (None, "") and key in ("parent_ref", "relation", "derived_from", "related_refs"):
            current.pop(key, None)
        else:
            current[key] = value
    if prompt_changed:
        _sync_prompt_relations(current, _prompt_refs(prompt_value, combined))
    elif current.get("prompt_refs"):
        # 关系面板只改 related_refs 时，继续保留提示词自动关联。
        _sync_prompt_relations(current, list(current.get("prompt_refs") or []))
    if current == before:
        registry = AssetRegistry(project_dir)
        return {"asset": registry.resolve(target_ref), "affected": {"shots": [], "reasons": {}}}
    bump_asset_revision(current)
    normalized, issues = asset_relations.normalize_asset_relations(combined)
    target, _ = _validate_target(normalized, kind, ident)
    blocking = [issue for issue in issues if issue.get("ref") == target_ref]
    if blocking:
        raise ValueError("资产关系无法保存：" + "；".join(str(x.get("message")) for x in blocking))
    _write_changed(file_data, normalized, {kind}, _snapshot())
    registry = AssetRegistry(project_dir)
    changed = mark_stale_for_asset(project_dir, [target_ref])
    return {"asset": registry.resolve(target_ref), "affected": changed}


def revision(project_dir: str, ref: str) -> int:
    kind, _ident, _normalized = _parse_ref(ref)
    row = AssetRegistry(project_dir).resolve(ref)
    try:
        return max(1, int(row.get("asset_revision", 1)))
    except (TypeError, ValueError):
        return 1


__all__ = ["create_asset", "edit_asset", "revision"]
