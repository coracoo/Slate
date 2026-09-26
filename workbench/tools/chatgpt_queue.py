# -*- coding: utf-8 -*-
"""ChatGPT 生成队列：复用创作清单建立只读生图任务。"""
import datetime
import hashlib
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CORE = os.path.abspath(os.path.join(HERE, "..", "..", "previs_system", "tools"))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
if CORE not in sys.path:
    sys.path.insert(0, CORE)

import creation_store
import project_store
import prompt_assembler
import prompt_compiler
import skill_lib
import gen_asset_images

try:
    import asset_registry
except Exception:
    asset_registry = None

try:
    import versions
except Exception:
    versions = None


class QueueError(ValueError):
    """队列请求不符合项目或分镜契约。"""


def _snapshot(path):
    return versions.snapshot(path) if versions is not None else None


def _manifest_path(project_dir):
    return os.path.join(os.path.abspath(os.fspath(project_dir)), "创作", "creation.json")


def _load_board(project_dir, board_name):
    name = os.path.basename(str(board_name or ""))
    if not name.lower().endswith(".json") or name != str(board_name or ""):
        raise QueueError("分镜名称不合法")
    path = os.path.realpath(os.path.join(project_dir, "分镜", name))
    root = os.path.realpath(os.path.join(project_dir, "分镜"))
    if os.path.commonpath([root, path]) != root or not os.path.isfile(path):
        raise QueueError("分镜不存在")
    try:
        with open(path, encoding="utf-8") as fh:
            board = json.load(fh)
    except (OSError, ValueError) as exc:
        raise QueueError(f"分镜读取失败：{exc}") from exc
    if not isinstance(board, dict) or not isinstance(board.get("shots"), list):
        raise QueueError("分镜格式无效")
    return name, board


def _safe_piece(value, fallback="X"):
    text = str(value or "").strip()
    text = re.sub(r"[^A-Za-z0-9_-]+", "", text)
    return text or fallback


def _shot_piece(value):
    raw = str(value or "").strip()
    match = re.fullmatch(r"[Ss](\d+)", raw)
    if match:
        return "S%02d" % int(match.group(1))
    return _safe_piece(raw, "S01")


def _episode(board_name):
    match = re.search(r"(?:剧本[_-]?)?([Ee]\d+)", str(board_name or ""))
    return match.group(1).upper() if match else _safe_piece(os.path.splitext(str(board_name or ""))[0], "E1")


def _project_code(project_dir):
    name = os.path.basename(os.path.normpath(project_dir))
    match = re.match(r"(\d+)", name)
    return match.group(1) if match else _safe_piece(name, "PROJECT")


def _next_version(items, board_name, shot_id, task_type):
    versions_seen = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        if (item.get("delivery") == "chatgpt_queue" and item.get("board") == board_name
                and str(item.get("shot_id")) == str(shot_id)
                and item.get("task_type") == task_type):
            try:
                versions_seen.append(int((item.get("output_spec") or {}).get("version") or 0))
            except (TypeError, ValueError):
                pass
    return max(versions_seen or [0]) + 1


def _next_asset_version(items, asset_ref, task_type):
    seen = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        if (item.get("delivery") == "chatgpt_queue" and item.get("asset_ref") == asset_ref
                and item.get("task_type") == task_type):
            try:
                seen.append(int((item.get("output_spec") or {}).get("version") or 0))
            except (TypeError, ValueError):
                pass
    return max(seen or [0]) + 1


def _new_id():
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def queue_shots(project_dir, board_name, shot_ids, task_type="reference_image"):
    """把指定分镜镜头装配为 ChatGPT 队列任务并原子写入创作清单。"""
    project_dir = os.path.abspath(os.fspath(project_dir))
    if task_type not in ("reference_image", "storyboard_image", "keyframe_image"):
        raise QueueError("不支持的 ChatGPT 任务类型")
    board_name, board = _load_board(project_dir, board_name)
    wanted = [str(x).strip() for x in (shot_ids or []) if str(x).strip()]
    if not wanted:
        raise QueueError("至少选择一个镜头")
    by_id = {str(s.get("id")): s for s in board.get("shots", []) if isinstance(s, dict) and s.get("id")}
    missing = [sid for sid in wanted if sid not in by_id]
    if missing:
        raise QueueError("镜头不存在：" + ", ".join(missing))
    manifest = _manifest_path(project_dir)
    created = []

    def mutate(data):
        if not isinstance(data, dict):
            raise project_store.InvalidDocument("creation.json 顶层必须是对象")
        items = data.setdefault("items", [])
        if not isinstance(items, list):
            raise project_store.InvalidDocument("creation.json.items 必须是数组")
        for shot_id in wanted:
            shot = by_id[shot_id]
            compiled = prompt_compiler.compile_stage_prompt(
                "storyboard_image", shot, project_dir, board=board
            )
            bundle = {
                "prompt_assembled": compiled.get("content_prompt", ""),
                "negative": compiled.get("negative_prompt", ""),
                "prompt_json": compiled.get("prompt_json"),
                "asset_refs": compiled.get("asset_refs") or [],
                "asset_context": compiled.get("asset_context") or {},
                "prompt_stage": compiled.get("stage", "storyboard_image"),
                "prompt_system": compiled.get("system_prompt", ""),
                "prompt_revision": compiled.get("prompt_revision", ""),
                "asset_revisions": compiled.get("asset_revisions") or {},
            }
            ref_shot = prompt_assembler.reference_shot_for_prompt(
                shot, bundle.get("prompt_assembled", ""), "image"
            )
            refs = prompt_assembler.resolve_shot_refs(
                ref_shot, project_dir, actors=board.get("actors"), board_name=board_name,
                max_refs=10, board=board, media_type="image"
            )
            version = _next_version(items, board_name, shot_id, task_type)
            ep = _episode(board_name)
            filename = f"{_project_code(project_dir)}_{ep}_{_shot_piece(shot_id)}_REF_v{version:03d}.png"
            item_id = _new_id()
            while any(isinstance(old, dict) and old.get("id") == item_id for old in items):
                time.sleep(0.001)
                item_id = _new_id()
            item = creation_store.make_item(
                item_id, "image", bundle.get("prompt_assembled", ""), board=board_name,
                shot_id=shot_id, prompt_user=str(shot.get("prompt_image") or shot.get("prompt") or ""),
                prompt_assembled=bundle.get("prompt_assembled", ""), negative=bundle.get("negative", ""),
                refs=refs, vendor_id="chatgpt", asset_context=bundle.get("asset_context"),
                prompt_json=bundle.get("prompt_json"), asset_refs=bundle.get("asset_refs"),
                prompt_stage=bundle.get("prompt_stage"), prompt_system=bundle.get("prompt_system"),
                prompt_revision=bundle.get("prompt_revision"), asset_revisions=bundle.get("asset_revisions")
            )
            item.update({
                "delivery": "chatgpt_queue", "task_type": task_type, "status": "queued",
                "output_spec": {"asset_id": os.path.splitext(filename)[0], "filename": filename, "version": version}
            })
            items.append(item)
            created.append(item)

    project_store.update_json(manifest, mutate, create_default={"items": []}, snapshot=_snapshot)
    return created


def _read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            value = json.load(fh)
        return value if value is not None else default
    except (OSError, ValueError, TypeError):
        return default


def _asset_source(project_dir, row, state_id=""):
    """读取资产档案中的设定图提示词，保持 ChatGPT 与本地生图共用资产语料。

    人物设定图过时校验：sheet_prompt 仍是旧三视图文本（含"三视图"且无 45 度侧脸/五视图特征）
    时直接打回，提示重新提炼——旧构图提示词生成的图会与新版素材链不一致。
    """
    kind = str(row.get("kind") or "")
    ident = str(row.get("id") or "")
    files = {"character": ("人物.json", "characters", "sheet_prompt"),
             "scene": ("场景.json", "scenes", "image_prompt"),
             "prop": ("道具.json", "props", "image_prompt")}
    filename, key, prompt_key = files.get(kind, ("", "", ""))
    data = _read_json(os.path.join(project_dir, "素材", filename), {}) if filename else {}
    rows = data.get(key) if isinstance(data, dict) else []
    if isinstance(rows, dict):
        rows = [dict(value, id=rid) for rid, value in rows.items() if isinstance(value, dict)]
    for item in rows or []:
        if isinstance(item, dict) and str(item.get("id")) == ident:
            if kind == "character":
                raw_prompt = str(item.get(prompt_key) or "").strip()
                if raw_prompt and "三视图" in raw_prompt and "45度" not in raw_prompt and "45 度" not in raw_prompt:
                    raise QueueError(
                        f"@character:{ident} 的设定图提示词仍是旧版三视图构图。"
                        f"请在「② 素材生成」重新提炼（或重新生成提示词）为五视图构图后，再重新提交本任务")
            if state_id:
                state = next((value for value in item.get("states") or []
                              if isinstance(value, dict) and str(value.get("id")) == str(state_id)), None)
                if not state:
                    raise QueueError(f"人物状态不存在：@{kind}:{ident}#{state_id}")
                prompt = str(state.get("sheet_prompt") or "").strip()
                if not prompt:
                    base = str(item.get(prompt_key) or item.get("prompt") or "").strip()
                    diff = str(state.get("look_diff") or state.get("label") or "").strip()
                    prompt = "；".join(value for value in (base, f"状态差异：{diff}" if diff else "") if value)
                merged = dict(item)
                merged["state"] = dict(state)
                merged["state_id"] = str(state_id)
                return prompt, merged
            return str(item.get(prompt_key) or item.get("prompt") or "").strip(), item
    index = _read_json(os.path.join(project_dir, "素材", "素材图.json"), {})
    zone = {"character": "人物", "scene": "场景", "prop": "道具"}.get(kind, "")
    indexed = (index.get(zone) or {}).get(ident) if isinstance(index, dict) and isinstance(index.get(zone), dict) else None
    return (str((indexed or {}).get("prompt") or "").strip(), indexed if isinstance(indexed, dict) else {})


def _parse_asset_request(value):
    state_id = ""
    if isinstance(value, dict):
        state_id = str(value.get("state_id") or "").strip()
        value = value.get("ref") or ("@%s:%s" % (value.get("kind"), value.get("id")))
    ref = str(value or "").strip()
    if "#" in ref:
        ref, suffix = ref.rsplit("#", 1)
        state_id = state_id or suffix.strip()
    return ref, state_id


def resolve_asset_execution_plan(project_dir, asset_ref, state_id=""):
    """按本地生图同一依赖规则解析 ChatGPT 资产任务的真实参考图。"""
    ref, parsed_state = _parse_asset_request({"ref": asset_ref, "state_id": state_id})
    state_id = parsed_state
    if not ref.startswith("@") or ":" not in ref:
        raise QueueError("资产引用格式无效")
    kind, ident = ref[1:].split(":", 1)
    if state_id:
        if kind != "character":
            raise QueueError("只有人物资产支持状态图")
        _prompt, source_record = _asset_source(project_dir, {"kind": kind, "id": ident}, state_id)
        registry = asset_registry.AssetRegistry(project_dir) if asset_registry is not None else None
        try:
            source = registry.resolve(ref) if registry is not None else {}
        except Exception:
            source = {}
        mother_rel = str(source.get("path") or f"素材/人物/{ident}.png").replace("\\", "/")
        mother_abs = os.path.join(project_dir, mother_rel.replace("/", os.sep))
        present = os.path.isfile(mother_abs)
        state = source_record.get("state") or {}
        revision_payload = json.dumps(state, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return {
            "refs": [{
                "path": mother_rel,
                "purpose": f"人物母图身份参考：{source_record.get('name') or ident}",
                "reference_role": "identity_anchor",
                "asset_ref": ref,
            }] if present else [],
            "reference_tokens": [ref],
            "missing_refs": [] if present else [ref],
            "execution_state": "ready" if present else "waiting_dependencies",
            "can_execute": present,
            "target_path": f"素材/人物/{ident}__{_safe_piece(state_id)}.png",
            "source_revision": f"{int(source.get('asset_revision') or 1)}:{hashlib.sha256(revision_payload).hexdigest()[:12]}",
        }
    plans = gen_asset_images.collect_asset_image_plan(
        project_dir, kind, ident, "chatgpt", strict_dependencies=True
    )
    plan = next((row for row in plans if row.get("kind") == kind and row.get("id") == ident), None)
    if not plan:
        raise QueueError(f"资产规划不存在：{ref}")
    registry = asset_registry.AssetRegistry(project_dir) if asset_registry is not None else None
    references = []
    for dependency in plan.get("reference_tokens") or []:
        try:
            parent = registry.resolve(dependency) if registry is not None else {}
        except Exception:
            parent = {}
        rel_path = str(parent.get("path") or "").replace("\\", "/")
        absolute = os.path.join(project_dir, rel_path.replace("/", os.sep)) if rel_path else ""
        if not absolute or not os.path.isfile(absolute):
            continue
        references.append({
            "path": rel_path,
            "purpose": f"父资产参考图：{parent.get('name') or dependency}",
            "reference_role": "identity_anchor",
            "asset_ref": dependency,
        })
    zone = {"character": "人物", "scene": "场景", "prop": "道具"}.get(kind, "")
    source = registry.resolve(ref) if registry is not None else {}
    return {
        "refs": references,
        "reference_tokens": list(plan.get("reference_tokens") or []),
        "missing_refs": list(plan.get("missing_refs") or []),
        "execution_state": str(plan.get("execution_state") or "ready"),
        "can_execute": bool(plan.get("can_execute", True)),
        "target_path": f"素材/{zone}/{ident}.png",
        "source_revision": int(source.get("asset_revision") or 1),
    }


def _refresh_queued_asset_item(project_dir, item):
    """未完成的资产任务随当前资产档案和生图风格更新，任务 ID 保持不变。"""
    if item.get("task_type") != "asset_image" or item.get("status") != "queued":
        return item
    ref, state_id = _parse_asset_request({
        "ref": item.get("base_asset_ref") or item.get("asset_ref"),
        "state_id": item.get("asset_state_id"),
    })
    if not ref.startswith("@") or ":" not in ref:
        return item
    kind, ident = ref[1:].split(":", 1)
    prompt, source = _asset_source(project_dir, {"kind": kind, "id": ident}, state_id)
    if not prompt:
        raise QueueError(f"已排队资产缺少提示词：{ref}")
    prompt, negative = skill_lib.compose_asset_image_prompt(
        project_dir, prompt, skill_id=(source or {}).get("style") if isinstance(source, dict) else None,
        kind=kind, style_prompt=(source or {}).get("style_prompt") if isinstance(source, dict) else None)
    item = dict(item)
    item.update(prompt=prompt, prompt_user=prompt, prompt_assembled=prompt, negative=negative)
    execution = resolve_asset_execution_plan(project_dir, ref, state_id)
    item.update({key: execution[key] for key in (
        "refs", "reference_tokens", "missing_refs", "execution_state",
        "can_execute", "source_revision"
    )})
    prompt_json = dict(item.get("prompt_json") or {})
    prompt_json["source_record"] = source
    item["prompt_json"] = prompt_json
    item["asset_context"] = {"assets": [source]}
    return item


def queue_assets(project_dir, asset_refs, task_type="asset_image"):
    """把资产提炼页选中的人物/场景/道具加入同一 ChatGPT 队列。"""
    project_dir = os.path.abspath(os.fspath(project_dir))
    if task_type != "asset_image":
        raise QueueError("不支持的 ChatGPT 资产任务类型")
    wanted = []
    for value in asset_refs or []:
        ref, state_id = _parse_asset_request(value)
        key = (ref, state_id)
        if ref and key not in wanted:
            wanted.append(key)
    if not wanted:
        raise QueueError("至少选择一个资产")
    if asset_registry is None:
        raise QueueError("资产注册表模块缺失")
    registry = asset_registry.AssetRegistry(project_dir)
    records = []
    for ref, state_id in wanted:
        try:
            row = registry.resolve(ref)
        except Exception as exc:
            raise QueueError(f"资产不存在：{ref}") from exc
        if row.get("kind") not in ("character", "scene", "prop"):
            raise QueueError(f"不支持加入队列的资产类型：{ref}")
        if state_id:
            _asset_source(project_dir, row, state_id)
        records.append((row, ref, state_id))
    manifest = _manifest_path(project_dir)
    created = []

    def mutate(data):
        if not isinstance(data, dict):
            raise project_store.InvalidDocument("creation.json 顶层必须是对象")
        items = data.setdefault("items", [])
        if not isinstance(items, list):
            raise project_store.InvalidDocument("creation.json.items 必须是数组")
        for row, base_ref, state_id in records:
            task_ref = f"{base_ref}#{state_id}" if state_id else base_ref
            execution = resolve_asset_execution_plan(project_dir, base_ref, state_id)
            prompt, source = _asset_source(project_dir, row, state_id)
            if not prompt:
                prompt = f"{row.get('name') or row.get('id')} 的资产设定图；保持稳定身份、材质、比例和画风。"
            prompt, negative = skill_lib.compose_asset_image_prompt(
                project_dir, prompt, skill_id=row.get("style"), kind=str(row.get("kind") or "character"),
                style_prompt=row.get("style_prompt"))
            version = _next_asset_version(items, task_ref, task_type)
            kind = _safe_piece(row.get("kind"), "asset").upper()
            ident = _safe_piece(row.get("id"), "ITEM")
            state_piece = f"_{_safe_piece(state_id)}" if state_id else ""
            filename = f"{_project_code(project_dir)}_ASSET_{kind}_{ident}{state_piece}_v{version:03d}.png"
            item_id = _new_id()
            while any(isinstance(old, dict) and old.get("id") == item_id for old in items):
                time.sleep(0.001)
                item_id = _new_id()
            refs = execution["refs"]
            item = creation_store.make_item(
                item_id, "image", prompt, prompt_user=prompt, prompt_assembled=prompt,
                negative=negative,
                refs=refs, vendor_id="chatgpt", asset_context={"assets": [source]},
                asset_refs=[base_ref], prompt_json={"source": "资产提炼", "asset_ref": base_ref,
                                                     "state_id": state_id, "source_record": source}
            )
            item.update({
                "delivery": "chatgpt_queue", "source_mode": "资产提炼", "task_type": task_type,
                "asset_ref": task_ref, "base_asset_ref": base_ref,
                "asset_state_id": state_id, "asset_kind": row.get("kind"), "asset_id": row.get("id"),
                "reference_tokens": execution["reference_tokens"],
                "missing_refs": execution["missing_refs"],
                "execution_state": execution["execution_state"],
                "can_execute": execution["can_execute"],
                "source_revision": execution["source_revision"],
                "status": "queued", "output_spec": {
                    "asset_id": os.path.splitext(filename)[0], "filename": filename, "version": version,
                    "target_path": execution["target_path"]
                }
            })
            items.append(item)
            created.append(item)

    project_store.update_json(manifest, mutate, create_default={"items": []}, snapshot=_snapshot)
    return created


def list_jobs(project_dir, board=None, status=None, source=None, asset_kind=None, limit=100):
    """列出 ChatGPT 队列摘要。"""
    path = _manifest_path(project_dir)
    if not os.path.isfile(path):
        return []
    data = project_store.read_json(path)[0]
    rows = []
    for item in data.get("items", []) if isinstance(data, dict) else []:
        if not isinstance(item, dict) or item.get("delivery") != "chatgpt_queue":
            continue
        if board and item.get("board") != board:
            continue
        if source and item.get("source_mode") != source:
            continue
        if asset_kind and item.get("asset_kind") != asset_kind:
            continue
        if status and item.get("status") != status:
            continue
        spec = item.get("output_spec") or {}
        rows.append({"id": item.get("id"), "board": item.get("board", ""), "shot_id": item.get("shot_id", ""),
                     "task_type": item.get("task_type", "reference_image"), "status": item.get("status", "queued"),
                     "filename": spec.get("filename", ""), "source_mode": item.get("source_mode", "创作"),
                     "asset_ref": item.get("asset_ref", ""), "asset_kind": item.get("asset_kind", "")})
    return rows[:max(1, min(int(limit or 100), 1000))]


def get_job(project_dir, item_id):
    path = _manifest_path(project_dir)
    if not os.path.isfile(path):
        raise QueueError("创作清单不存在")
    data = project_store.read_json(path)[0]
    for item in data.get("items", []) if isinstance(data, dict) else []:
        if isinstance(item, dict) and item.get("id") == item_id and item.get("delivery") == "chatgpt_queue":
            return _refresh_queued_asset_item(project_dir, item)
    raise QueueError("ChatGPT 任务不存在")


def refresh_queued_asset_jobs(project_dir):
    """风格切换后刷新磁盘上的待生成资产任务；已完成记录不变。"""
    path = _manifest_path(project_dir)
    if not os.path.isfile(path):
        return 0
    updated = [0]

    def mutate(data):
        items = data.get("items") or []
        for i, item in enumerate(items):
            if not isinstance(item, dict) or item.get("delivery") != "chatgpt_queue":
                continue
            refreshed = _refresh_queued_asset_item(project_dir, item)
            if refreshed != item:
                items[i] = refreshed
                updated[0] += 1

    project_store.update_json(path, mutate, snapshot=_snapshot)
    return updated[0]


def get_export_spec(jobs):
    rows = []
    for job in jobs or []:
        spec = job.get("output_spec") or {}
        rows.append({"job_id": job.get("id"), "shot_id": job.get("shot_id", ""),
                     "task_type": job.get("task_type", "reference_image"),
                     "filename": spec.get("filename", ""), "asset_id": spec.get("asset_id", ""),
                     "version": spec.get("version", 1), "source_mode": job.get("source_mode", "创作"),
                     "asset_ref": job.get("asset_ref", ""), "target_path": spec.get("target_path", "")})
    project = str((jobs or [{}])[0].get("board") or "project").rsplit(".", 1)[0]
    return {"zip_name": f"{_safe_piece(project)}_CHATGPT.zip", "root_folder": f"{_safe_piece(project)}_CHATGPT",
            "images_folder": "images", "manifest_name": "manifest.json", "jobs": rows}
