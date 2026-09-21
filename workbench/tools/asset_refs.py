# -*- coding: utf-8 -*-
"""项目内资产引用解析。

API 层统一使用项目相对路径；媒体展示路径由前端单独加 projects/<项目>/ 前缀。
"""
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def _clean(value):
    return str(value or "").replace("\\", "/").strip().lstrip("/")


def normalize_project_ref(project_dir, ref):
    """将项目绝对路径或 projects/<name>/ 前缀归一为项目相对路径。"""
    project_dir = os.path.abspath(project_dir)
    raw = str(ref or "")
    if os.path.isabs(raw):
        real = os.path.realpath(raw)
        root = os.path.realpath(project_dir)
        if os.path.commonpath([root, real]) != root:
            raise ValueError("参考图不在项目目录内")
        return os.path.relpath(real, root).replace(os.sep, "/")
    value = _clean(raw)
    name = os.path.basename(os.path.normpath(project_dir)).replace("\\", "/")
    prefix = "projects/" + _clean(name) + "/"
    if value.lower().startswith(prefix.lower()):
        value = value[len(prefix):]
    elif value.lower().startswith("projects/"):
        # 仅剥离 projects/<当前项目>/，其他项目名不能被猜测或吞掉。
        head, sep, tail = value[9:].partition("/")
        if sep and head.lower() == name.lower():
            value = tail
    candidate = os.path.realpath(os.path.join(project_dir, value))
    root = os.path.realpath(project_dir)
    if os.path.commonpath([root, candidate]) != root:
        raise ValueError("参考图不在项目目录内")
    return os.path.relpath(candidate, root).replace(os.sep, "/")


def _existing(project_dir, rel):
    if not rel:
        return None
    path = os.path.realpath(os.path.join(project_dir, rel))
    root = os.path.realpath(project_dir)
    return path if os.path.commonpath([root, path]) == root and os.path.isfile(path) else None


def resolve_actor_ref(project_dir, actor_id, actor_name=""):
    """优先按素材图索引和角色 id 解析，兼容旧拉片素材/角色参考路径。"""
    project_dir = os.path.abspath(project_dir)
    # 新版统一注册表优先，旧目录/索引作为兼容回退。
    try:
        from asset_registry import AssetRegistry
        registry = AssetRegistry(project_dir)
        for raw in (actor_id, actor_name):
            if not raw:
                continue
            try:
                row = registry.resolve("@character:" + str(raw))
                found = _existing(project_dir, row.get("path"))
                if found:
                    return found
            except Exception:
                pass
    except Exception:
        pass
    index_path = os.path.join(project_dir, "素材", "素材图.json")

    try:
        with open(index_path, encoding="utf-8") as fh:
            data = json.load(fh)
        for zone in ("人物", "character", "characters"):
            record = (data.get(zone) or {}).get(str(actor_id))
            if isinstance(record, dict):
                found = _existing(project_dir, record.get("path"))
                if found:
                    return found
    except (OSError, ValueError, TypeError):
        pass
    for stem in (str(actor_id or ""), str(actor_name or "")):
        if not stem:
            continue
        for rel in (f"素材/人物/{stem}.png", f"拉片素材/角色参考/{stem}.png",
                    f"拉片素材/角色参考/{stem}.jpg", f"拉片素材/角色参考/{stem}.jpeg"):
            found = _existing(project_dir, rel)
            if found:
                return found
    return None


def resolve_ref(project_dir, ref):
    """解析任意项目相对引用，缺失返回 None。"""
    try:
        return _existing(project_dir, normalize_project_ref(project_dir, ref))
    except ValueError:
        return None

