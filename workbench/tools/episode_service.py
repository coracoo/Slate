# -*- coding: utf-8 -*-
"""分集删除服务。

删除分集只修改剧本分集清单，并从全局资产的来源集列表中解除该集关联；
不会删除全局人物、场景、道具、图片或已经生成的分镜，避免破坏其它分集
仍在使用的资产和产物。
"""
from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
CORE = os.path.abspath(os.path.join(HERE, "..", "..", "previs_system", "tools"))
if CORE not in sys.path:
    sys.path.insert(0, CORE)

import project_store


_ASSET_FILES = (
    ("人物.json", "characters"),
    ("场景.json", "scenes"),
    ("道具.json", "props"),
)
_ASSET_INDEX_ZONES = ("人物", "场景", "道具", "character", "characters", "scene", "scenes", "prop", "props")


def _snapshot():
    try:
        import versions
        return versions.snapshot
    except Exception:
        return None


def _read_object(path: Path):
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError(f"无法读取 {path.name}：{exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} 必须是 JSON 对象")
    return value


def _remove_episode_id(item: dict, episode_id: str) -> bool:
    """从一条资产记录的兼容来源字段中移除集号。"""
    changed = False
    for field in ("source_episode_ids", "source_episodes"):
        raw = item.get(field)
        if not isinstance(raw, list):
            continue
        normalized = [str(value).strip() for value in raw if str(value).strip()]
        if episode_id not in normalized:
            continue
        values = [value for value in normalized if value != episode_id]
        if values:
            if values != normalized:
                item[field] = values
                changed = True
        else:
            item.pop(field, None)
            changed = True
    return changed


def _remove_episode_from_index(data: dict, episode_id: str):
    """同步清理素材图索引中的旧来源，避免注册表合并时把已删除分集补回来。

    素材图.json 既有中文对象分区，也兼容英文分区和列表格式；索引只保存
    展示元数据，遇到未知结构时原样保留。
    """
    changed = False
    changed_ids = []
    seen_zones = set()
    for zone in _ASSET_INDEX_ZONES:
        if zone in seen_zones:
            continue
        zone_data = data.get(zone)
        if not isinstance(zone_data, (dict, list)):
            continue
        seen_zones.add(zone)
        if isinstance(zone_data, dict):
            for rid, raw in list(zone_data.items()):
                if not isinstance(raw, dict):
                    continue
                item = copy.deepcopy(raw)
                if not _remove_episode_id(item, episode_id):
                    continue
                zone_data[rid] = item
                changed = True
                changed_ids.append(str(item.get("id") or rid or ""))
        else:
            for index, raw in enumerate(list(zone_data)):
                if not isinstance(raw, dict):
                    continue
                item = copy.deepcopy(raw)
                if not _remove_episode_id(item, episode_id):
                    continue
                zone_data[index] = item
                changed = True
                changed_ids.append(str(item.get("id") or ""))
    return changed, changed_ids


def delete_episode(project_dir: str, episode_id: str) -> dict:
    """删除一个分集，并解除该集对全局资产的来源标记。"""
    root = Path(project_dir).resolve()
    if not root.is_dir():
        raise ValueError("项目目录不存在")
    episode = str(episode_id or "").strip()
    if not episode:
        raise ValueError("episode 不能为空")

    script_dir = root / "剧本"
    episode_path = script_dir / "分集.json"
    book = _read_object(episode_path)
    if not book or not isinstance(book.get("episodes"), list):
        raise ValueError("项目尚未建立分集清单")
    episodes = [item for item in book["episodes"] if isinstance(item, dict)]
    if not any(str(item.get("id") or "").strip() == episode for item in episodes):
        raise ValueError(f"找不到分集：{episode}")
    next_book = copy.deepcopy(book)
    next_book["episodes"] = [
        item for item in next_book["episodes"]
        if not (isinstance(item, dict) and str(item.get("id") or "").strip() == episode)
    ]
    # 分集清单变化即剧本修订：bump rev，下游分镜/资产可据此提示陈旧。
    try:
        next_book["rev"] = int(book.get("rev") or 0) + 1
    except (TypeError, ValueError):
        next_book["rev"] = 1

    # 先计算所有变更，再按文件原子写入；每个被覆写文件都有版本快照。
    changed_assets = []
    changed_asset_seen = set()
    next_assets = []
    for filename, key in _ASSET_FILES:
        path = root / "素材" / filename
        raw = _read_object(path)
        if raw is None:
            continue
        rows = raw.get(key)
        if not isinstance(rows, list):
            continue
        updated = copy.deepcopy(raw)
        touched = 0
        for row in updated[key]:
            if isinstance(row, dict) and _remove_episode_id(row, episode):
                touched += 1
                asset_id = str(row.get("id") or "")
                if asset_id and asset_id not in changed_asset_seen:
                    changed_asset_seen.add(asset_id)
                    changed_assets.append(asset_id)
        if touched:
            next_assets.append((path, key, updated))

    # AssetRegistry merges the index with the three script JSON files.  If only
    # the script file is cleaned, an old source_episode_ids in 素材图.json can
    # be merged back into the in-memory global asset and defeat the filter.
    index_path = root / "素材" / "素材图.json"
    index = _read_object(index_path)
    next_index = copy.deepcopy(index) if index is not None else None
    index_changed = False
    index_ids = []
    if next_index is not None:
        index_changed, index_ids = _remove_episode_from_index(next_index, episode)
        for asset_id in index_ids:
            if asset_id and asset_id not in changed_asset_seen:
                changed_asset_seen.add(asset_id)
                changed_assets.append(asset_id)

    snapshot = _snapshot()
    # 删除分集正文清单；分集剧本文本、分镜和图片留作历史产物，不做物理删除。
    project_store.update_json(
        str(episode_path),
        lambda _current, value=next_book: value,
        snapshot=snapshot,
    )
    for path, key, updated in next_assets:
        project_store.update_json(
            str(path),
            lambda _current, value=updated: value,
            snapshot=snapshot,
        )
    if index_changed and next_index is not None:
        project_store.update_json(
            str(index_path),
            lambda _current, value=next_index: value,
            snapshot=snapshot,
        )

    return {
        "episode": episode,
        "remaining_episodes": [str(item.get("id") or "") for item in next_book["episodes"] if isinstance(item, dict)],
        "assets_unlinked": len(changed_assets),
        "asset_ids": changed_assets,
        "artifacts_retained": True,
    }


__all__ = ["delete_episode"]
