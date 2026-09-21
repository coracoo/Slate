# -*- coding: utf-8 -*-
"""通用产出版本管理：最新文件保持原位，覆写前快照到 .versions/（供版本切换/回滚）

约定：目录内最新版永远是原路径（所有既有引用/manifest/前端路径零改动）；
历史版本存同目录 `.versions/<文件名>.<YYYYMMDD_HHMMSS>.<扩展名>`，每文件保留最近 20 份。

API:
  snapshot(path)            覆写前调用：已有文件则快照（无文件则什么都不做）
  list_versions(path)       -> [{"ts","path"(绝对),"rel"(相对 VIDEO)},{最新在首位为当前}]
  restore(path, ts)         回滚：先 snapshot 当前，再把所选版本复制回原位
用法（任何产出工具在覆写前一行接入）:
  import versions as V
  V.snapshot(out); open(out,"w").write(...)
"""
import sys, os, shutil, re, glob, time, hashlib
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

VIDEO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _rel(path):
    """相对 VIDEO 的正斜杠路径；跨盘（如 C:/ 临时目录）回落绝对路径，不抛 ValueError。"""
    try:
        return os.path.relpath(path, VIDEO).replace("\\", "/")
    except ValueError:
        return os.path.abspath(path).replace("\\", "/")
KEEP = 20
TS = "%Y%m%d_%H%M%S"


def _versions_dir(path):
    return os.path.join(os.path.dirname(path), ".versions")


def _is_version_file(path):
    return os.path.sep + ".versions" + os.path.sep in path


def _sha256(path):
    """计算文件内容指纹，用于阻止同一图片被重复登记为版本。"""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _history_sort_key(path):
    """历史快照排序键：取文件名内嵌的时间戳 <名>.<YYYYMMDD_HHMMSS>[_n].<扩展名>。
    snapshot 用 copy2 保留原文件 mtime，mtime 并不代表快照时序，不能用它排序。
    无时间戳的同名文件排最旧（reverse 时垫底、优先被 prune）。"""
    m = re.search(r"\.(\d{8}_\d{6}(?:_\d+)?)(?:\.[^.]+)$", os.path.basename(path))
    return m.group(1) if m else ""


def _history_files(path):
    base, ext = os.path.splitext(os.path.basename(path))
    return sorted(
        glob.glob(os.path.join(_versions_dir(path), f"{base}.*{ext}")),
        key=_history_sort_key,
        reverse=True,
    )


def _dedupe_history(path):
    """历史目录按内容去重，保留每种内容最新的一份。"""
    files = _history_files(path)
    seen = set()
    for old in files:
        try:
            digest = _sha256(old)
        except OSError:
            continue
        if digest in seen:
            try:
                os.remove(old)
            except OSError:
                pass
        else:
            seen.add(digest)
    return _history_files(path)


def snapshot(path):
    """覆写前快照；内容未变化时不再创建重复历史版本。"""
    path = os.path.abspath(path)
    if not os.path.isfile(path) or _is_version_file(path):
        return None
    vd = _versions_dir(path)
    os.makedirs(vd, exist_ok=True)
    current_hash = _sha256(path)
    history = _dedupe_history(path)
    # 当前文件已经有同内容历史快照，说明这是重复写入/重复恢复，保留已有快照即可。
    for old in history:
        try:
            if _sha256(old) == current_hash:
                _prune(path)
                return old
        except OSError:
            continue
    base, ext = os.path.splitext(os.path.basename(path))
    ts = time.strftime(TS)
    dst = os.path.join(vd, f"{base}.{ts}{ext}")
    n = 2
    while os.path.exists(dst):
        dst = os.path.join(vd, f"{base}.{ts}_{n}{ext}"); n += 1
    shutil.copy2(path, dst)
    _prune(path)
    return dst


def _prune(path):
    """每文件只保留最近 KEEP 份历史（按文件名时间戳，理由见 _history_sort_key）。"""
    for old in _history_files(path)[KEEP:]:
        try: os.remove(old)
        except Exception: pass


def list_versions(path):
    """版本清单（当前最新在首位）；相同内容只展示一次。"""
    path = os.path.abspath(path)
    out = []
    current_hash = None
    if os.path.isfile(path):
        try:
            current_hash = _sha256(path)
        except OSError:
            pass
        out.append({"ts": time.strftime(TS, time.localtime(os.path.getmtime(path))),
                    "path": path,
                    "rel": _rel(path),
                    "current": True})
    if not _is_version_file(path):
        seen = set()
        for v in _dedupe_history(path):
            try:
                digest = _sha256(v)
            except OSError:
                continue
            # 当前原位内容已经代表这一版本，避免 UI 出现“最新 + 同图历史”。
            if digest == current_hash or digest in seen:
                continue
            seen.add(digest)
            m = re.search(r"\.(\d{8}_\d{6}(?:_\d+)?)(?:\.[^.]+)$", os.path.basename(v))
            out.append({"ts": m.group(1) if m else time.strftime(TS, time.localtime(os.path.getmtime(v))),
                        "path": v,
                        "rel": _rel(v),
                        "current": False})
    return out


def restore(path, ts):
    """回滚到指定 ts 版本：先快照当前，再覆盖。"""
    path = os.path.abspath(path)
    tgt = None
    for v in list_versions(path):
        if not v["current"] and v["ts"] == ts:
            tgt = v["path"]; break
    if not tgt:
        raise FileNotFoundError(f"版本不存在: {ts}")
    snapshot(path)
    shutil.copy2(tgt, path)
    return path


if __name__ == "__main__":
    import json
    if len(sys.argv) >= 3 and sys.argv[1] == "list":
        print(json.dumps(list_versions(sys.argv[2]), ensure_ascii=False, indent=1))
    elif len(sys.argv) >= 4 and sys.argv[1] == "restore":
        restore(sys.argv[2], sys.argv[3]); print("[OK] 已回滚")
    else:
        print(__doc__)
