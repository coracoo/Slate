# -*- coding: utf-8 -*-
"""ChatGPT 浏览器执行 HTTP 接口的纯函数适配层。"""
import base64
import binascii

import chatgpt_queue
import chatgpt_runs


class ApiError(ValueError):
    """可安全返回给 HTTP 客户端的接口错误。"""

    def __init__(self, status, message):
        super().__init__(str(message))
        self.status = int(status)
        self.message = str(message)


def _body(value):
    if not isinstance(value, dict):
        raise ApiError(400, "请求体必须是 JSON 对象")
    return value


def _required(value, name):
    text = str(value or "").strip()
    if not text:
        raise ApiError(400, f"{name} 必填")
    return text


def _call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except chatgpt_runs.RunAuthError as exc:
        raise ApiError(401, str(exc)) from exc
    except chatgpt_runs.ResultValidationError as exc:
        raise ApiError(422, str(exc)) from exc
    except chatgpt_runs.RunStateError as exc:
        raise ApiError(409, str(exc)) from exc
    except (chatgpt_runs.RunError, chatgpt_queue.QueueError, FileNotFoundError) as exc:
        raise ApiError(400, str(exc)) from exc


def create_run(project_dir, request):
    body = _body(request)
    job_ids = body.get("job_ids")
    if not isinstance(job_ids, list):
        raise ApiError(400, "job_ids 必须是数组")
    options = body.get("options") or {}
    if not isinstance(options, dict):
        raise ApiError(400, "options 必须是对象")
    return _call(chatgpt_runs.create_run, project_dir, job_ids, options)


def get_run(project_dir, request):
    body = _body(request)
    return _call(chatgpt_runs.get_run, project_dir, _required(body.get("run_id"), "run_id"))


def claim_next(project_dir, request, token):
    body = _body(request)
    return _call(
        chatgpt_runs.claim_next, project_dir, _required(body.get("run_id"), "run_id"), token
    )


def record_event(project_dir, request, token):
    body = _body(request)
    return _call(
        chatgpt_runs.record_event,
        project_dir,
        _required(body.get("run_id"), "run_id"),
        _required(body.get("attempt_id"), "attempt_id"),
        token,
        _required(body.get("event_id"), "event_id"),
        _required(body.get("event_type"), "event_type"),
        body.get("payload") or {},
    )


def decode_image_base64(value):
    text = str(value or "").strip()
    if not text:
        raise ApiError(400, "image_base64 必填")
    if text.startswith("data:"):
        if "," not in text:
            raise ApiError(400, "image_base64 data URL 不合法")
        text = text.split(",", 1)[1]
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ApiError(400, "image_base64 不是合法 Base64") from exc


def stage_result(project_dir, request, token, content=None):
    body = _body(request)
    image = bytes(content) if isinstance(content, (bytes, bytearray)) else decode_image_base64(body.get("image_base64"))
    expected = body.get("expected_revision")
    if expected is None:
        raise ApiError(400, "expected_revision 必填")
    return _call(
        chatgpt_runs.stage_result,
        project_dir,
        _required(body.get("run_id"), "run_id"),
        _required(body.get("attempt_id"), "attempt_id"),
        token,
        image,
        _required(body.get("file_name"), "file_name"),
        body.get("metadata") or {},
        expected,
    )


def import_result(project_dir, request, token):
    body = _body(request)
    return _call(
        chatgpt_runs.import_staged_result,
        project_dir,
        _required(body.get("run_id"), "run_id"),
        _required(body.get("attempt_id"), "attempt_id"),
        token,
    )


def control_run(project_dir, request, token):
    body = _body(request)
    return _call(
        chatgpt_runs.set_run_control,
        project_dir,
        _required(body.get("run_id"), "run_id"),
        token,
        _required(body.get("action"), "action"),
        str(body.get("reason") or ""),
    )


def open_reference(project_dir, run_id, attempt_id, reference_id, token):
    return _call(
        chatgpt_runs.open_reference,
        project_dir,
        _required(run_id, "run_id"),
        _required(attempt_id, "attempt_id"),
        _required(reference_id, "reference_id"),
        token,
    )
