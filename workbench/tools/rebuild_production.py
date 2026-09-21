# -*- coding: utf-8 -*-
"""制作链路的可重建操作。

提示词重建只读当前剧本、分镜和全局资产，不调用任何生图/生视频厂商。
媒体生成由创作台另一步显式触发；旧 JSON 和媒体在覆盖前交给 versions
做快照，因此资产设定更新不会静默抹掉历史产物。
"""
from __future__ import annotations

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

import project_store
import prompt_compiler

try:
    import versions
except Exception:  # pragma: no cover - 仅供离线调用时的防御分支
    versions = None


PROMPT_VERSION = "production-chain-v1"


def _snapshot(path):
    return versions.snapshot(path) if versions is not None else None


def _board_files(project_dir: str):
    folder = Path(project_dir) / "分镜"
    if not folder.is_dir():
        return
    for path in sorted(folder.glob("*.json")):
        if path.name.startswith("."):
            continue
        try:
            board = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if isinstance(board, dict) and isinstance(board.get("shots"), list):
            yield path, board


def _episode_matches(path: Path, board: dict, episode: str | None) -> bool:
    if not episode:
        return True
    target = str(episode).strip()
    values = [board.get("episode_id"), board.get("episode"), board.get("ep_id")]
    values.extend([board.get("id"), path.stem])
    for value in values:
        text = str(value or "")
        if text == target or text.endswith("_" + target) or text.endswith(target):
            return True
    return False


def _selected_shot(shot: dict, shot_ids: set[str] | None) -> bool:
    return bool(shot.get("id")) and (not shot_ids or str(shot.get("id")) in shot_ids)


def _compile_pair(project_dir: str, board: dict, shot: dict):
    image = prompt_compiler.compile_stage_prompt(
        "storyboard_image", shot, project_dir, mode="generate", board=board
    )
    video = prompt_compiler.compile_stage_prompt(
        "video", shot, project_dir, mode="generate", board=board
    )
    refs = list(image.get("asset_refs") or video.get("asset_refs") or [])
    revisions = dict(image.get("asset_revisions") or {})
    for ref, revision in (video.get("asset_revisions") or {}).items():
        revisions.setdefault(ref, revision)
    return image, video, refs, revisions


def rebuild_prompts(project_dir: str, episode: str | None = None,
                    shot_ids: list[str] | None = None) -> dict:
    """重建分镜静态参考图和生视频提示词，返回可审计统计。"""
    project_dir = os.path.abspath(os.fspath(project_dir))
    wanted = {str(value).strip() for value in (shot_ids or []) if str(value).strip()}
    result = {"boards": [], "updated_prompts": 0, "skipped": 0,
              "media_calls": 0, "shots": [], "prompt_version": PROMPT_VERSION}
    for path, board in _board_files(project_dir):
        if not _episode_matches(path, board, episode):
            continue
        changed = False
        for shot in board.get("shots") or []:
            if not isinstance(shot, dict) or not _selected_shot(shot, wanted):
                continue
            try:
                image, video, refs, revisions = _compile_pair(project_dir, board, shot)
            except Exception as exc:
                result["skipped"] += 1
                result.setdefault("errors", []).append({"shot_id": str(shot.get("id")), "error": str(exc)})
                continue
            # 作者三类文本是源，组装文本是视图，重建不能改写已编辑或 LLM 输出。
            for key, compiled in (("prompt_image", image), ("prompt_video", video)):
                if not shot.get(key) or shot.get(key + '_source') == 'compiled':
                    shot[key] = compiled.get('content_prompt', '')
                    shot[key + '_source'] = 'compiled'
            shot['compiled_prompts'] = {'image': image.get('content_prompt', ''), 'video': video.get('content_prompt', '')}
            # 旧前端仍读取 prompt；静态参考图是默认的可读版本。
            shot["prompt"] = shot["prompt_image"]
            shot["asset_refs"] = refs
            shot["asset_revisions"] = revisions
            shot["prompt_revisions"] = {
                "storyboard_image": image.get("prompt_revision", ""),
                "video": video.get("prompt_revision", ""),
            }
            shot["prompt_version"] = PROMPT_VERSION
            changed = True
            result["updated_prompts"] += 1
            result["shots"].append({"board": path.name, "shot_id": str(shot["id"]),
                                    "asset_refs": refs, "asset_revisions": revisions})
        if changed:
            def mutate(current, value=board):
                current.clear()
                current.update(value)
            project_store.update_json(str(path), mutate, snapshot=_snapshot)
            result["boards"].append(path.name)
    return result


def rebuild_episode(project_dir: str, episode: str) -> dict:
    """重建一集的所有分镜提示词，不修改分集正文或大纲。"""
    if not str(episode or "").strip():
        raise ValueError("episode 不能为空")
    return rebuild_prompts(project_dir, episode=str(episode).strip())


def affected_shots(project_dir: str, changed_refs: list[str]) -> list[str]:
    """返回资产变更会影响的镜头 id，按分镜文件和镜号稳定排序。"""
    from production_state import mark_stale_for_asset
    return list(mark_stale_for_asset(project_dir, changed_refs).get("shots") or [])


__all__ = ["rebuild_prompts", "rebuild_episode", "affected_shots", "PROMPT_VERSION"]
