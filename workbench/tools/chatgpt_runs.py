# -*- coding: utf-8 -*-
"""ChatGPT 浏览器执行运行库：固定范围、持久状态、暂存和幂等导入。"""
import datetime
import glob
import hashlib
import io
import json
import mimetypes
import os
import re
import secrets
import sys
import threading
import urllib.parse

from PIL import Image, UnidentifiedImageError

HERE = os.path.dirname(os.path.abspath(__file__))
CORE = os.path.abspath(os.path.join(HERE, "..", "..", "previs_system", "tools"))
for path in (HERE, CORE):
    if path not in sys.path:
        sys.path.insert(0, path)

import chatgpt_import
import chatgpt_queue
import project_store


class RunError(ValueError):
    """运行请求不符合契约。"""


class RunAuthError(PermissionError):
    """运行令牌无效。"""


class RunStateError(RunError):
    """运行状态转换非法。"""


class ResultValidationError(RunError):
    """生成结果未通过基础文件检查。"""


_LOCK = threading.RLock()
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]+$")
_TERMINAL = {"imported", "failed", "cancelled"}
_TRANSITIONS = {
    "waiting_dependencies": {"ready", "paused", "cancelled"},
    "ready": {"uploading", "paused", "failed", "cancelled"},
    "uploading": {"preparing", "paused", "failed", "cancelled"},
    "preparing": {"generating", "paused", "failed", "cancelled"},
    "generating": {"staged", "needs_review", "paused", "failed", "cancelled"},
    "staged": {"validating", "importing", "needs_review", "paused", "failed", "cancelled"},
    "validating": {"importing", "needs_review", "paused", "failed", "cancelled"},
    "needs_review": {"validating", "importing", "failed", "cancelled"},
    "importing": {"staged", "imported", "needs_review", "failed"},
    "paused": {"ready", "uploading", "preparing", "generating", "staged", "validating", "cancelled"},
    "imported": set(), "failed": set(), "cancelled": set(),
}


def _now():
    return datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds")


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha(value):
    raw = value if isinstance(value, (bytes, bytearray)) else _canonical(value)
    return hashlib.sha256(bytes(raw)).hexdigest()


def _check_id(value, label):
    text = str(value or "")
    if not text or not _SAFE_ID.fullmatch(text):
        raise RunError(f"{label} 不合法")
    return text


def _root(project_dir):
    root = os.path.abspath(os.fspath(project_dir))
    if not os.path.isdir(root):
        raise RunError("项目目录不存在")
    return root


def _run_dir(project_dir, run_id):
    return os.path.join(_root(project_dir), "创作", "chatgpt_runs", _check_id(run_id, "run_id"))


def _run_path(project_dir, run_id):
    return os.path.join(_run_dir(project_dir, run_id), "run.json")


def _attempt_path(project_dir, run_id, attempt_id):
    return os.path.join(_run_dir(project_dir, run_id), "attempts", _check_id(attempt_id, "attempt_id") + ".json")


def _read(path):
    try:
        return project_store.read_json(path)[0]
    except FileNotFoundError as exc:
        raise RunError("运行记录不存在") from exc


def _write(path, value):
    value = dict(value)
    return project_store.update_json(path, lambda _old: value, create_default={})[0]


def _public_attempt(attempt):
    if not isinstance(attempt, dict):
        return None
    public = {key: value for key, value in attempt.items() if key not in {"internal_references", "staging_path"}}
    public["references"] = [
        {key: value for key, value in row.items() if key not in {"path", "absolute_path"}}
        for row in attempt.get("references") or []
    ]
    return public


def _public_run(project_dir, run):
    public = {key: value for key, value in run.items() if key not in {"token_hash", "event_ids"}}
    attempt_id = str(run.get("current_attempt_id") or "")
    if attempt_id:
        try:
            public["current_attempt"] = _public_attempt(_read(_attempt_path(project_dir, run["run_id"], attempt_id)))
        except RunError:
            public["current_attempt"] = None
    public["counts"] = {
        "total": len(run.get("job_ids") or []),
        "imported": int(run.get("cursor") or 0),
        "remaining": max(0, len(run.get("job_ids") or []) - int(run.get("cursor") or 0)),
    }
    return public


def _auth(run, token):
    actual = _sha(str(token or "").encode("utf-8"))
    if not secrets.compare_digest(str(run.get("token_hash") or ""), actual):
        raise RunAuthError("运行令牌无效")


def _expect_revision(run, expected_revision):
    if expected_revision is None:
        return
    try:
        wanted = int(expected_revision)
    except (TypeError, ValueError) as exc:
        raise RunStateError("expected_revision 不合法") from exc
    if wanted != int(run.get("revision") or 0):
        raise RunStateError(
            f"运行版本已变化：期望 {wanted}，当前 {int(run.get('revision') or 0)}"
        )


def create_run(project_dir, job_ids, options=None):
    with _LOCK:
        return _create_run(project_dir, job_ids, options)


def _create_run(project_dir, job_ids, options=None):
    root = _root(project_dir)
    wanted = []
    for value in job_ids or []:
        item_id = str(value or "").strip()
        if item_id and item_id not in wanted:
            wanted.append(item_id)
    if not wanted or len(wanted) > 200:
        raise RunError("job_ids 必须包含 1–200 个任务")
    runs_root = os.path.join(root, "创作", "chatgpt_runs")
    for path in glob.glob(os.path.join(runs_root, "*", "run.json")):
        existing = _read(path)
        if existing.get("status") in {"done", "failed", "cancelled"}:
            continue
        duplicated = [item_id for item_id in wanted if item_id in (existing.get("job_ids") or [])]
        if duplicated:
            raise RunStateError(
                f"任务已属于未结束运行 {existing.get('run_id')}：{'、'.join(duplicated)}"
            )
    for item_id in wanted:
        job = chatgpt_queue.get_job(root, item_id)
        if job.get("status") != "queued":
            raise RunError(f"任务不是 queued：{item_id}")
    run_id = "run-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "-" + secrets.token_hex(3)
    token = secrets.token_urlsafe(24)
    now = _now()
    opts = {
        "vision_validation": False,
        "auto_import": True,
        "max_retries": 1,
        **(options if isinstance(options, dict) else {}),
    }
    batches = [wanted[index:index + 10] for index in range(0, len(wanted), 10)]
    run = {
        "schema_version": "1.0", "run_id": run_id,
        "project": os.path.basename(os.path.normpath(root)),
        "job_ids": wanted, "batches": batches, "options": opts,
        "cursor": 0, "status": "ready", "pause_reason": "",
        "current_attempt_id": "", "revision": 1,
        "token_hash": _sha(token.encode("utf-8")), "event_ids": [],
        "created_at": now, "updated_at": now,
    }
    os.makedirs(os.path.join(_run_dir(root, run_id), "attempts"), exist_ok=True)
    os.makedirs(os.path.join(_run_dir(root, run_id), "staging"), exist_ok=True)
    _write(_run_path(root, run_id), run)
    return {**_public_run(root, run), "run_token": token}


def get_run(project_dir, run_id):
    return _public_run(project_dir, _read(_run_path(project_dir, run_id)))


def _reference_snapshot(project_dir, refs):
    root = _root(project_dir)
    rows = []
    for index, source in enumerate(refs or [], 1):
        rel = str(source.get("path") or "").replace("\\", "/").strip()
        absolute = os.path.realpath(os.path.join(root, rel.replace("/", os.sep)))
        if not rel or os.path.commonpath([root, absolute]) != root or not os.path.isfile(absolute):
            raise RunStateError(f"参考图缺失：{rel or source.get('asset_ref') or index}")
        with open(absolute, "rb") as fh:
            digest = _sha(fh.read())
        rows.append({
            "reference_id": f"R{index}", "file_name": os.path.basename(absolute),
            "purpose": str(source.get("purpose") or "参考图"),
            "reference_role": str(source.get("reference_role") or "reference"),
            "asset_ref": str(source.get("asset_ref") or ""),
            "path": rel, "sha256": digest,
        })
    return rows


def _job_snapshot(job, references=None):
    contract = dict(job.get("generation_contract") or {})
    contract.setdefault("aspect_ratio", str(job.get("aspect_ratio") or "16:9"))
    contract.setdefault("count", 1)
    snapshot = {
        "prompt_assembled": str(job.get("prompt_assembled") or job.get("prompt") or ""),
        "negative": str(job.get("negative") or ""),
        "generation_contract": contract,
        "output_spec": dict(job.get("output_spec") or {}),
        "source_revision": job.get("source_revision"),
        "task_type": str(job.get("task_type") or ""),
        "shot_id": str(job.get("shot_id") or ""),
        "asset_ref": str(job.get("asset_ref") or ""),
        "asset_state_id": str(job.get("asset_state_id") or ""),
    }
    if references is not None:
        snapshot["references"] = [{
            "asset_ref": str(row.get("asset_ref") or ""),
            "path": str(row.get("path") or ""),
            "purpose": str(row.get("purpose") or ""),
            "reference_role": str(row.get("reference_role") or ""),
            "sha256": str(row.get("sha256") or ""),
        } for row in references]
    return snapshot


def _build_attempt(project_dir, run, job):
    attempt_id = f"{run['run_id']}-a{int(run.get('cursor') or 0) + 1:03d}"
    references = _reference_snapshot(project_dir, job.get("refs") or [])
    prompt_snapshot = _job_snapshot(job, references)
    contract = prompt_snapshot["generation_contract"]
    phase = "waiting_dependencies" if job.get("missing_refs") else "ready"
    now = _now()
    return {
        "schema_version": "1.0", "attempt_id": attempt_id,
        "run_id": run["run_id"], "job_id": job.get("id"), "phase": phase,
        "prompt_assembled": prompt_snapshot["prompt_assembled"],
        "negative": prompt_snapshot["negative"],
        "generation_contract": contract, "output_spec": prompt_snapshot["output_spec"],
        "prompt_sha256": _sha(prompt_snapshot), "source_revision": job.get("source_revision"),
        "task_type": prompt_snapshot["task_type"], "shot_id": prompt_snapshot["shot_id"],
        "asset_ref": prompt_snapshot["asset_ref"], "asset_state_id": prompt_snapshot["asset_state_id"],
        "reference_tokens": list(job.get("reference_tokens") or []),
        "missing_refs": list(job.get("missing_refs") or []),
        "references": references, "result": None, "vision": {"status": "not_run"},
        "events": [], "created_at": now, "updated_at": now,
    }


def claim_next(project_dir, run_id, token):
    with _LOCK:
        path = _run_path(project_dir, run_id)
        run = _read(path)
        _auth(run, token)
        current = str(run.get("current_attempt_id") or "")
        if current:
            attempt = _read(_attempt_path(project_dir, run_id, current))
            if attempt.get("phase") == "waiting_dependencies":
                job = chatgpt_queue.get_job(project_dir, attempt.get("job_id"))
                refreshed = _build_attempt(project_dir, run, job)
                if refreshed.get("phase") == "ready":
                    refreshed.update({
                        "attempt_id": attempt["attempt_id"],
                        "created_at": attempt.get("created_at") or refreshed["created_at"],
                        "events": list(attempt.get("events") or []),
                        "dependency_refreshed_at": _now(),
                    })
                    _write(_attempt_path(project_dir, run_id, current), refreshed)
                    run.update(status="ready", pause_reason="", updated_at=_now())
                    run["revision"] = int(run.get("revision") or 0) + 1
                    _write(path, run)
                    return _public_attempt(refreshed)
            if attempt.get("phase") not in _TERMINAL:
                return _public_attempt(attempt)
        cursor = int(run.get("cursor") or 0)
        jobs = run.get("job_ids") or []
        if cursor >= len(jobs):
            run.update(status="done", current_attempt_id="", pause_reason="", updated_at=_now())
            run["revision"] = int(run.get("revision") or 0) + 1
            _write(path, run)
            return None
        job = chatgpt_queue.get_job(project_dir, jobs[cursor])
        attempt = _build_attempt(project_dir, run, job)
        _write(_attempt_path(project_dir, run_id, attempt["attempt_id"]), attempt)
        run.update(
            current_attempt_id=attempt["attempt_id"], status=attempt["phase"],
            pause_reason=("缺少父资产参考图：" + "、".join(attempt["missing_refs"])) if attempt["missing_refs"] else "",
            updated_at=_now(),
        )
        run["revision"] = int(run.get("revision") or 0) + 1
        _write(path, run)
        return _public_attempt(attempt)


def _transition(attempt, target):
    current = str(attempt.get("phase") or "")
    if target == current:
        return
    if target not in _TRANSITIONS.get(current, set()):
        raise RunStateError(f"非法状态转换：{current} → {target}")
    if target == "paused" and current != "paused":
        attempt["resume_phase"] = current
    attempt["phase"] = target
    attempt["updated_at"] = _now()


def record_event(project_dir, run_id, attempt_id, token, event_id, event_type, payload=None):
    with _LOCK:
        run_path = _run_path(project_dir, run_id)
        run = _read(run_path)
        _auth(run, token)
        event_id = _check_id(event_id, "event_id")
        if event_id in (run.get("event_ids") or []):
            return _public_attempt(_read(_attempt_path(project_dir, run_id, attempt_id)))
        attempt_path = _attempt_path(project_dir, run_id, attempt_id)
        attempt = _read(attempt_path)
        if attempt.get("attempt_id") != run.get("current_attempt_id"):
            raise RunStateError("attempt 不是当前运行任务")
        body = payload if isinstance(payload, dict) else {}
        if event_type == "phase":
            _transition(attempt, str(body.get("phase") or ""))
        elif event_type == "send_unknown":
            _transition(attempt, "paused")
            attempt["pause_reason"] = str(body.get("reason") or "发送结果不明，需要对账")
        elif event_type == "upload_diagnostic":
            # 只保存计数和状态，禁止图片字节、网页正文或身份凭证进入日志。
            keys = ("scopeFound", "thumbnailCount", "loadedCount", "uploading", "sendReady", "error", "freshSelection", "expectedCount")
            body = {key: body[key] for key in keys if type(body.get(key)) in (bool, int, float)}
            attempt["upload_diagnostic"] = body
        elif event_type == "vision_passed":
            attempt["vision"] = {"status": "passed", "reasons": list(body.get("reasons") or [])}
        elif event_type == "vision_failed":
            _transition(attempt, "needs_review")
            attempt["vision"] = {"status": "failed", "reasons": list(body.get("reasons") or [])}
        else:
            raise RunError("未知事件类型")
        event = {"event_id": event_id, "type": event_type, "payload": body,
                 "phase": attempt.get("phase"), "at": _now()}
        attempt.setdefault("events", []).append(event)
        _write(attempt_path, attempt)
        run.setdefault("event_ids", []).append(event_id)
        run["event_ids"] = run["event_ids"][-500:]
        run.update(status=attempt["phase"], pause_reason=str(attempt.get("pause_reason") or ""), updated_at=_now())
        run["revision"] = int(run.get("revision") or 0) + 1
        _write(run_path, run)
        events_path = os.path.join(_run_dir(project_dir, run_id), "events.jsonl")
        with open(events_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
        return _public_attempt(attempt)


def _expected_ratio(contract):
    raw = str((contract or {}).get("aspect_ratio") or "").strip()
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*[:/]\s*(\d+(?:\.\d+)?)\s*", raw)
    if not match or float(match.group(2)) == 0:
        return None
    return float(match.group(1)) / float(match.group(2))


def open_reference(project_dir, run_id, attempt_id, reference_id, token):
    """读取已冻结 attempt 的参考图，并在返回前复核内容哈希。"""
    with _LOCK:
        run = _read(_run_path(project_dir, run_id))
        _auth(run, token)
        attempt = _read(_attempt_path(project_dir, run_id, attempt_id))
        if attempt.get("run_id") != run.get("run_id"):
            raise RunStateError("attempt 不属于当前运行")
        reference = next(
            (row for row in attempt.get("references") or [] if row.get("reference_id") == reference_id),
            None,
        )
        if reference is None:
            raise RunError("参考图不存在")
        root = _root(project_dir)
        rel = str(reference.get("path") or "").replace("/", os.sep)
        absolute = os.path.realpath(os.path.join(root, rel))
        if os.path.commonpath([root, absolute]) != root or not os.path.isfile(absolute):
            raise RunStateError("参考图已缺失，运行快照不可继续")
        with open(absolute, "rb") as fh:
            content = fh.read()
        if _sha(content) != reference.get("sha256"):
            raise RunStateError("参考图内容已变化，运行快照已过期")
        content_type = mimetypes.guess_type(absolute)[0] or "application/octet-stream"
        return {
            "content": content,
            "content_type": content_type,
            "file_name": str(reference.get("file_name") or os.path.basename(absolute)),
            "sha256": reference.get("sha256"),
        }


def stage_result(project_dir, run_id, attempt_id, token, content, file_name, metadata=None,
                 expected_revision=None):
    with _LOCK:
        run_path = _run_path(project_dir, run_id)
        run = _read(run_path)
        _auth(run, token)
        _expect_revision(run, expected_revision)
        attempt_path = _attempt_path(project_dir, run_id, attempt_id)
        attempt = _read(attempt_path)
        if attempt.get("phase") == "needs_review" and attempt.get("result"):
            return {**attempt["result"], "phase": "needs_review", "reason": attempt.get("review_reason", "")}
        if attempt.get("phase") not in {"generating", "staged", "validating"}:
            raise RunStateError("当前状态不接受生成结果")
        if not isinstance(content, (bytes, bytearray)) or not content:
            raise ResultValidationError("图片文件为空")
        try:
            with Image.open(io.BytesIO(bytes(content))) as image:
                image.verify()
            with Image.open(io.BytesIO(bytes(content))) as image:
                width, height = image.size
                image_format = str(image.format or "").upper()
        except (OSError, ValueError, UnidentifiedImageError) as exc:
            raise ResultValidationError("结果不是可解码图片") from exc
        if width < 256 or height < 256:
            raise ResultValidationError("结果尺寸过小，疑似缩略图")
        expected = _expected_ratio(attempt.get("generation_contract"))
        if expected and abs(width / height - expected) / expected > 0.12:
            raise ResultValidationError("结果画幅与任务要求不匹配")
        digest = _sha(bytes(content))
        old = attempt.get("result") or {}
        if old.get("sha256") == digest:
            return {**old, "phase": attempt.get("phase"), "idempotent": True}
        duplicate_job = ""
        attempts_root = os.path.join(_root(project_dir), "创作", "chatgpt_runs")
        for other_path in glob.glob(os.path.join(attempts_root, "*", "attempts", "*.json")):
            if os.path.abspath(other_path) == os.path.abspath(attempt_path):
                continue
            other = _read(other_path)
            if (other.get("result") or {}).get("sha256") == digest and other.get("job_id") != attempt.get("job_id"):
                duplicate_job = str(other.get("job_id") or "")
                break
        extension = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}.get(image_format, ".img")
        staging_path = os.path.join(_run_dir(project_dir, run_id), "staging", attempt_id + extension)
        temporary = staging_path + ".tmp"
        with open(temporary, "wb") as fh:
            fh.write(bytes(content)); fh.flush(); os.fsync(fh.fileno())
        os.replace(temporary, staging_path)
        response_locator = str((metadata or {}).get("response_locator") or "")
        if response_locator:
            try:
                parts = urllib.parse.urlsplit(response_locator)
                response_locator = urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
            except ValueError:
                response_locator = ""
        result = {
            "file_name": os.path.basename(str(file_name or "result" + extension)),
            "sha256": digest, "mime": Image.MIME.get(image_format, "application/octet-stream"),
            "width": width, "height": height,
            "capture_method": str((metadata or {}).get("capture_method") or "unknown"),
            "response_locator": response_locator,
            "staged_at": _now(),
        }
        attempt["result"] = result
        attempt["staging_path"] = staging_path
        if duplicate_job:
            _transition(attempt, "needs_review")
            attempt["review_reason"] = f"结果与另一任务 {duplicate_job} 完全相同"
            run.update(status="needs_review", pause_reason=attempt["review_reason"])
        else:
            _transition(attempt, "staged")
            run.update(status="staged", pause_reason="")
        _write(attempt_path, attempt)
        run["updated_at"] = _now(); run["revision"] = int(run.get("revision") or 0) + 1
        _write(run_path, run)
        return {**result, "phase": attempt["phase"], "reason": attempt.get("review_reason", ""), "idempotent": False}


def import_staged_result(project_dir, run_id, attempt_id, token):
    with _LOCK:
        run_path = _run_path(project_dir, run_id)
        run = _read(run_path)
        _auth(run, token)
        attempt_path = _attempt_path(project_dir, run_id, attempt_id)
        attempt = _read(attempt_path)
        if attempt.get("phase") == "imported":
            return {"status": "done", "idempotent": True, "job_id": attempt.get("job_id")}
        if attempt.get("phase") == "importing":
            _transition(attempt, "staged")
            attempt["import_recovered_at"] = _now()
            _write(attempt_path, attempt)
            run.update(status="staged", pause_reason="检测到中断的导入，正在安全恢复", updated_at=_now())
            run["revision"] = int(run.get("revision") or 0) + 1
            _write(run_path, run)
        if attempt.get("phase") not in {"staged", "validating"}:
            raise RunStateError("只有已暂存且通过门槛的结果可以导入")
        if run.get("options", {}).get("vision_validation") and (attempt.get("vision") or {}).get("status") != "passed":
            raise RunStateError("视觉复核尚未通过")
        job = chatgpt_queue.get_job(project_dir, attempt["job_id"])
        reference_changed = False
        try:
            current_references = _reference_snapshot(project_dir, job.get("refs") or [])
            frozen_references = [{
                "asset_ref": str(row.get("asset_ref") or ""),
                "path": str(row.get("path") or ""),
                "sha256": str(row.get("sha256") or ""),
            } for row in attempt.get("references") or []]
            current_identity = [{
                "asset_ref": str(row.get("asset_ref") or ""),
                "path": str(row.get("path") or ""),
                "sha256": str(row.get("sha256") or ""),
            } for row in current_references]
            reference_changed = frozen_references != current_identity
        except RunStateError:
            current_references = []
            reference_changed = True
        if reference_changed or _sha(_job_snapshot(job, current_references)) != attempt.get("prompt_sha256"):
            _transition(attempt, "needs_review")
            attempt["review_reason"] = (
                "参考图内容已变化，暂存结果仍对应旧参考图，请人工复核"
                if reference_changed else "任务内容已变化，暂存结果仍对应旧快照，请人工复核"
            )
            _write(attempt_path, attempt)
            run.update(status="needs_review", pause_reason=attempt["review_reason"], updated_at=_now())
            run["revision"] = int(run.get("revision") or 0) + 1
            _write(run_path, run)
            return {"status": "needs_review", "reason": attempt["review_reason"],
                    "job_id": attempt.get("job_id")}
        _transition(attempt, "importing")
        _write(attempt_path, attempt)
        run.update(status="importing", updated_at=_now())
        _write(run_path, run)
        spec = attempt.get("output_spec") or {}
        image_key = "images/" + str(spec.get("filename") or attempt["result"]["file_name"])
        with open(attempt["staging_path"], "rb") as fh:
            content = fh.read()
        manifest = {
            "schema_version": "1.0", "generator": "chatgpt",
            "project": os.path.basename(os.path.normpath(project_dir)),
            "assets": [{
                "job_id": attempt["job_id"], "shot_id": attempt.get("shot_id", ""),
                "task_type": attempt.get("task_type"), "version": spec.get("version"),
                "file": image_key,
            }],
        }
        try:
            imported = chatgpt_import.import_package(
                project_dir, manifest, {image_key: content}, transaction_id=attempt_id
            )
        except Exception as exc:
            _transition(attempt, "staged")
            attempt["import_error"] = str(exc)
            _write(attempt_path, attempt)
            run.update(status="staged", pause_reason="导入失败，可安全重试", updated_at=_now())
            run["revision"] = int(run.get("revision") or 0) + 1
            _write(run_path, run)
            raise
        _transition(attempt, "imported")
        attempt["import_result"] = imported; attempt["completed_at"] = _now()
        _write(attempt_path, attempt)
        run["cursor"] = int(run.get("cursor") or 0) + 1
        run["current_attempt_id"] = ""
        run["status"] = "done" if run["cursor"] >= len(run.get("job_ids") or []) else "ready"
        run["pause_reason"] = ""; run["updated_at"] = _now(); run["revision"] = int(run.get("revision") or 0) + 1
        _write(run_path, run)
        return {"status": "done", "idempotent": False, "job_id": attempt["job_id"], **imported}


def set_run_control(project_dir, run_id, token, action, reason=""):
    with _LOCK:
        path = _run_path(project_dir, run_id)
        run = _read(path); _auth(run, token)
        current_id = str(run.get("current_attempt_id") or "")
        attempt = _read(_attempt_path(project_dir, run_id, current_id)) if current_id else None
        if action == "pause":
            if attempt and attempt.get("phase") not in _TERMINAL:
                _transition(attempt, "paused"); attempt["pause_reason"] = str(reason or "用户暂停")
                _write(_attempt_path(project_dir, run_id, current_id), attempt)
            run.update(status="paused", pause_reason=str(reason or "用户暂停"))
        elif action == "resume":
            if attempt and attempt.get("phase") == "paused":
                target = str(attempt.get("resume_phase") or "ready")
                _transition(attempt, target); attempt["pause_reason"] = ""
                _write(_attempt_path(project_dir, run_id, current_id), attempt)
                run.update(status=target, pause_reason="")
            else:
                run.update(status="ready", pause_reason="")
        elif action == "cancel":
            if attempt and attempt.get("phase") not in _TERMINAL:
                _transition(attempt, "cancelled")
                _write(_attempt_path(project_dir, run_id, current_id), attempt)
            run.update(status="cancelled", pause_reason=str(reason or "用户取消"))
        else:
            raise RunError("不支持的控制动作")
        run.pop("stop_requested", None)
        run["updated_at"] = _now(); run["revision"] = int(run.get("revision") or 0) + 1
        _write(path, run)
        return _public_run(project_dir, run)
