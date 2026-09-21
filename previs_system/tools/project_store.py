# -*- coding: utf-8 -*-
"""提供项目 JSON 的跨进程安全读改写入口。

文件更新遵循同一条路径：锁内重新读取、检查内容 revision、写同目录临时
文件并 flush/fsync，最后用 ``os.replace`` 原子替换。快照由调用方以回调
注入，避免本模块依赖 workbench 的版本管理实现。
"""
import copy
import hashlib
import json
import os
import tempfile
import threading
import time
from contextlib import contextmanager

try:
    import msvcrt
except ImportError:  # pragma: no cover - 仅在非 Windows 使用
    msvcrt = None

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows 没有 fcntl
    fcntl = None

import sys
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


LOCK_WAIT_SECONDS = 10.0
_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


class RevisionConflict(RuntimeError):
    """文件在本次更新前已经变化，或锁等待超时。"""


class InvalidDocument(ValueError):
    """文件不是可接受的 UTF-8 JSON 对象。"""


def _absolute(path):
    return os.path.abspath(os.fspath(path))


def _canonical_bytes(data):
    """把 JSON 对象编码为 revision 与落盘共同使用的规范字节。"""
    if not isinstance(data, dict):
        raise InvalidDocument("JSON 根节点必须是对象")
    try:
        text = json.dumps(
            data,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise InvalidDocument(f"JSON 对象无法规范化: {exc}") from exc
    return text.encode("utf-8")


def _revision(canonical):
    return hashlib.sha256(canonical).hexdigest()


def _read_unlocked(path):
    """在调用方已持有锁时读取，保留解析失败前的原文件。"""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except FileNotFoundError:
        raise
    try:
        text = raw.decode("utf-8")
        data = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise InvalidDocument(f"JSON 文件损坏，未回退为空清单: {path}: {exc}") from exc
    canonical = _canonical_bytes(data)
    return data, _revision(canonical)


def _thread_lock_for(path):
    key = os.path.normcase(_absolute(path))
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        return lock


def _lock_file_path(path):
    # Windows 对部分包含中文长路径的持久 .lock 文件会返回
    # ``[Errno 22] Invalid argument``（尤其是旧版本留下的锁文件）。
    # 锁文件只需要与目标文件同目录且稳定唯一，使用 ASCII 名称可以
    # 避免这个平台差异，同时不改变目标 JSON 的路径和内容。
    digest = hashlib.sha256(_absolute(path).encode("utf-8")).hexdigest()[:24]
    return os.path.join(os.path.dirname(_absolute(path)), f".codex_lock_{digest}.lck")


def _lock_nonblocking(fh):
    if os.name == "nt":
        if msvcrt is None:  # pragma: no cover - 防御性分支
            raise RuntimeError("Windows 锁模块不可用")
        fh.seek(0)
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    if fcntl is None:  # pragma: no cover - 防御性分支
        raise RuntimeError("当前平台没有文件锁实现")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except (BlockingIOError, OSError):
        return False


def _unlock(fh):
    try:
        if os.name == "nt":
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


@contextmanager
def _exclusive(path):
    """获得进程内线程锁与跨进程文件锁。锁文件持久存在。"""
    path = _absolute(path)
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)
    thread_lock = _thread_lock_for(path)
    with thread_lock:
        lock_path = _lock_file_path(path)
        with open(lock_path, "a+b") as fh:
            # msvcrt.locking 不能锁定空区间，因此锁文件始终至少一个字节。
            if os.path.getsize(lock_path) == 0:
                fh.write(b"0")
                fh.flush()
            deadline = time.monotonic() + LOCK_WAIT_SECONDS
            acquired = False
            while time.monotonic() < deadline:
                if _lock_nonblocking(fh):
                    acquired = True
                    break
                time.sleep(0.01)
            if not acquired:
                raise RevisionConflict(f"文件锁等待超时: {path}")
            try:
                yield
            finally:
                _unlock(fh)


def _fsync_directory(parent):
    """在支持目录 fsync 的系统上把 rename 一并刷盘。"""
    if os.name == "nt":
        return
    try:
        fd = os.open(parent, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _atomic_write(path, data, snapshot):
    path = _absolute(path)
    parent = os.path.dirname(path) or "."
    encoded = _canonical_bytes(data)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{os.path.basename(path)}.", suffix=".tmp", dir=parent
    )
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(encoded)
            fh.flush()
            os.fsync(fh.fileno())
        if snapshot is not None and os.path.isfile(path):
            snapshot(path)
        os.replace(temporary, path)
        _fsync_directory(parent)
    finally:
        # replace 成功后临时路径已经不存在；失败时删除临时文件不会触碰旧版。
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return _revision(encoded)


def read_json(path: str) -> tuple[dict, str]:
    """读取 JSON 对象并返回 ``(data, revision)``。"""
    path = _absolute(path)
    if not os.path.isdir(os.path.dirname(path) or "."):
        raise FileNotFoundError(path)
    with _exclusive(path):
        return _read_unlocked(path)


def update_json(path: str, mutate, *, expected_revision: str | None = None,
                create_default: dict | None = None, snapshot=None) -> tuple[dict, str]:
    """锁内读改写 JSON，并返回 ``(新对象, 新 revision)``。

    ``mutate`` 可原地修改对象，也可返回一个新的对象。网络调用必须在
    调用此函数之前完成；锁只覆盖本地的读、校验、快照与原子替换。
    """
    path = _absolute(path)
    parent = os.path.dirname(path) or "."
    if not os.path.isdir(parent):
        if create_default is None:
            raise FileNotFoundError(path)
        os.makedirs(parent, exist_ok=True)
    if not callable(mutate):
        raise TypeError("mutate 必须是可调用对象")

    with _exclusive(path):
        if os.path.isfile(path):
            data, current_revision = _read_unlocked(path)
        else:
            if create_default is None:
                raise FileNotFoundError(path)
            if expected_revision is not None:
                raise RevisionConflict(f"文件尚不存在，revision 已失效: {path}")
            data = copy.deepcopy(create_default)
            _canonical_bytes(data)
            current_revision = None

        if expected_revision is not None and expected_revision != current_revision:
            raise RevisionConflict(
                f"revision 冲突: expected={expected_revision}, current={current_revision}"
            )
        result = mutate(data)
        if result is not None:
            if not isinstance(result, dict):
                raise InvalidDocument("mutate 必须返回 JSON 对象或 None")
            data = result
        new_revision = _atomic_write(path, data, snapshot)
        return data, new_revision
