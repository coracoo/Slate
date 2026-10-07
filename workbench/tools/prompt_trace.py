# -*- coding: utf-8 -*-
"""在项目私有目录记录实际送入文本模型的提示词调用。"""
import sys
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import datetime
import hashlib
import json
import re
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from error_utils import scrub_error

PROJECTS_DIR = Path(__file__).resolve().parents[2] / "projects"
_SAFE_PROJECT = re.compile(r"^[^/\\:\x00-\x1f.][^/\\:\x00-\x1f]*$")
_SAFE_RUN_ID = re.compile(r"^[0-9]{8}T[0-9]{12}Z-[0-9a-f]{12}$")
_SECRET_KEYS = frozenset({"api_key", "authorization", "access_token", "refresh_token", "cookie", "secret"})
_INPUT_FILES = (
    "剧本/剧本.txt", "剧本/brief.json", "剧本/style.json", "剧本/大纲.json",
    "剧本/埋线.json", "剧本/分集.json", "素材/人物.json", "素材/场景.json", "素材/道具.json",
)


def _input_file_revisions(project):
    """只记录已有项目事实文件的相对路径与内容哈希，不复制资产或凭据。"""
    found = {}
    for relative in _INPUT_FILES:
        path = project / relative
        if path.is_file():
            found[relative.replace("\\", "/")] = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    return found


def _project_path(project_name):
    name = str(project_name or "").strip()
    if not name or name in (".", "..") or not _SAFE_PROJECT.fullmatch(name) or ".." in name:
        return None
    project = (PROJECTS_DIR / name).resolve()
    return project if project.is_dir() and project.parent == PROJECTS_DIR.resolve() else None


def _safe_value(value, key=""):
    if str(key).lower() in _SECRET_KEYS:
        return "[已遮盖]"
    if isinstance(value, dict):
        return {str(k): _safe_value(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_safe_value(item) for item in value]
    if isinstance(value, str):
        if value.startswith("data:image/"):
            digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
            return f"[内联图片 sha256:{digest} 字符数:{len(value)}]"
        if key == "url" and value.startswith(("https://", "http://")):
            parsed = urlsplit(value)
            return scrub_error(urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", "")))
        return scrub_error(value)
    return value


def record_prompt_run(project_name, stage, messages, *, vendor_id, model, sources=None,
                      input_revision="", kind="text", parameters=None):
    """记录一次模型请求；没有安全、已存在的项目目录时返回 None。"""
    project = _project_path(project_name)
    if project is None:
        return None
    cleaned = _safe_value(messages)
    input_files = _input_file_revisions(project)
    source_manifest = {"input_files": input_files}
    if isinstance(sources, dict):
        source_manifest.update(_safe_value(sources))
    elif sources:
        source_manifest["caller"] = _safe_value(sources)
    revision = str(input_revision or "")
    if not revision and input_files:
        raw = json.dumps(input_files, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        revision = "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()
    identity = {
        "stage": str(stage or "chat"), "messages": cleaned,
        "vendor_id": str(vendor_id or ""), "model": str(model or ""),
        "kind": str(kind or "text"), "sources": source_manifest,
        "input_revision": revision,
        "parameters": _safe_value(parameters or {}),
    }
    source = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    now = datetime.datetime.now(datetime.timezone.utc)
    run_id = now.strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:12]
    record = {"run_id": run_id, "created_at": now.isoformat(),
              "fingerprint": "sha256:" + hashlib.sha256(source.encode("utf-8")).hexdigest(), **identity}
    folder = project / "创作" / "提示词调用"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (run_id + ".json")
    with path.open("x", encoding="utf-8") as handle:
        json.dump(record, handle, ensure_ascii=False, indent=2)
    return {"run_id": run_id, "path": str(path), "fingerprint": record["fingerprint"]}


def list_prompt_runs(project_name, limit=20):
    """倒序读取项目内提示词调用摘要；不把完整剧本塞进列表响应。"""
    project = _project_path(project_name)
    if project is None:
        return []
    folder = project / "创作" / "提示词调用"
    if not folder.is_dir():
        return []
    rows = []
    for path in sorted(folder.glob("*.json"), reverse=True)[:max(0, min(int(limit), 100))]:
        if not _SAFE_RUN_ID.fullmatch(path.stem):
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            rows.append({key: record.get(key) for key in
                         ("run_id", "created_at", "stage", "vendor_id", "model", "kind", "fingerprint", "input_revision")})
        except (OSError, ValueError):
            continue
    return rows


def read_prompt_run(project_name, run_id):
    """只允许读取该项目内由本模块生成的单条 JSON。"""
    project = _project_path(project_name)
    if project is None or not _SAFE_RUN_ID.fullmatch(str(run_id or "")):
        return None
    path = project / "创作" / "提示词调用" / (run_id + ".json")
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    except (OSError, ValueError):
        return None
