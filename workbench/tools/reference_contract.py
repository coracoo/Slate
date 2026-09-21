# -*- coding: utf-8 -*-
"""参考帧契约工具。

参考帧是视频生产的画面锚点，不等同于白模预演包或平面调度图。
本模块只负责确定性规范化，不调用模型，也不推断未显式选择的参考图。
"""
import os
import re


REFERENCE_ROLES = {"start", "process", "end", "unspecified"}


def _clean(value):
    return str(value or "").strip()


def normalize_reference_row(row):
    """把字符串或对象形式的参考帧统一为可审计的对象。"""
    if isinstance(row, str):
        row = {"path": row}
    if not isinstance(row, dict):
        return None
    path = _clean(row.get("path") or row.get("image") or row.get("file"))
    if not path:
        return None
    role = _clean(row.get("reference_role") or row.get("role") or row.get("moment_role") or "unspecified").lower()
    if role not in REFERENCE_ROLES:
        role = "unspecified"
    result = {
        "path": path,
        "reference_role": role,
        "purpose": _clean(row.get("purpose") or "剧情参考帧"),
    }
    usage = _clean(row.get("usage") or row.get("use"))
    if usage:
        result["usage"] = usage
    time_value = row.get("target_time_seconds")
    if time_value is None:
        time_value = row.get("time_seconds")
    if time_value is not None and _clean(time_value):
        try:
            result["target_time_seconds"] = float(time_value)
        except (TypeError, ValueError):
            pass
    label = _clean(row.get("label") or row.get("name"))
    if label:
        result["label"] = label
    return result


def explicit_reference_rows(shot):
    """只读取镜头显式保存的参考帧，不扫描预演包/平面图目录。"""
    if not isinstance(shot, dict):
        return []
    raw = shot.get("reference_frames")
    if raw is None:
        raw = shot.get("references")
    if raw is None:
        raw = shot.get("reference_refs")
    if isinstance(raw, (str, dict)):
        raw = [raw]
    if not isinstance(raw, list):
        raw = []
    rows = []
    for item in raw:
        normalized = normalize_reference_row(item)
        if normalized:
            rows.append(normalized)
    layout = normalize_reference_row(shot.get("layout_ref"))
    if layout:
        layout["reference_role"] = "unspecified"
        layout["purpose"] = "布局参考"
        rows.append(layout)
    return rows


def resolve_reference_path(project_dir, row):
    """在项目目录内解析显式参考帧，拒绝越界路径。"""
    normalized = normalize_reference_row(row)
    if not normalized:
        return None, None
    raw = normalized["path"]
    candidate = raw if os.path.isabs(raw) else os.path.join(project_dir, raw.replace("/", os.sep))
    path = os.path.realpath(candidate)
    root = os.path.realpath(project_dir)
    try:
        inside = os.path.commonpath([root, path]) == root
    except ValueError:
        inside = False
    if not inside or not os.path.isfile(path):
        return None, normalized
    return path, normalized


def reference_label(row):
    """给模型可读的参考角色说明，保留角色但不伪造 provider 的帧槽位。"""
    normalized = normalize_reference_row(row) or {}
    purpose = normalized.get("purpose") or "剧情参考帧"
    role = normalized.get("reference_role") or "unspecified"
    usage = normalized.get("usage")
    value = f"{purpose}（{role}）"
    if usage:
        value += f"，用途：{usage}"
    if normalized.get("target_time_seconds") is not None:
        value += f"，目标时间：{normalized['target_time_seconds']:g}s"
    return value


_STATIC_ONLY_PREFIXES = (
    "声音与氛围：", "声音：", "音效：", "台词：", "对白：", "台词仅作表演参考：",
)


def strip_video_only_lines(prompt):
    """从静态参考图正文中移除声音/台词字段，保留台词后期叠加的事实。"""
    lines = []
    for line in str(prompt or "").splitlines():
        text = line.strip()
        if any(text.startswith(prefix) for prefix in _STATIC_ONLY_PREFIXES):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


__all__ = [
    "REFERENCE_ROLES", "normalize_reference_row", "explicit_reference_rows",
    "resolve_reference_path", "reference_label", "strip_video_only_lines",
]
