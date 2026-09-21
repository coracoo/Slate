# -*- coding: utf-8 -*-
"""ChatGPT 生成包安全导入器。"""
import datetime
import io
import json
import os
import shutil
import stat
import tempfile
import zipfile
import sys
import uuid
from PIL import Image, UnidentifiedImageError

HERE = os.path.dirname(os.path.abspath(__file__))
CORE = os.path.abspath(os.path.join(HERE, "..", "..", "previs_system", "tools"))
if CORE not in sys.path:
    sys.path.insert(0, CORE)
import project_store

try:
    import versions
except Exception:
    versions = None

VIDEO_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
ALLOWED_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


class ImportError(ValueError):
    """生成包校验或导入失败。"""


def _atomic_bytes(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".recover.", suffix=".tmp", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def _transaction_root(root):
    return os.path.join(root, "创作", ".chatgpt_import_transactions")


def _recover_transaction(root, transaction_dir):
    journal_path = os.path.join(transaction_dir, "journal.json")
    if not os.path.isfile(journal_path):
        return False
    try:
        journal = project_store.read_json(journal_path)[0]
    except Exception:
        return False
    if journal.get("state") != "prepared":
        return False
    for entry in reversed(journal.get("entries") or []):
        rel = _safe_rel(entry.get("path"))
        target = os.path.realpath(os.path.join(root, rel.replace("/", os.sep)))
        if os.path.commonpath([root, target]) != root:
            raise ImportError("事务恢复目标越界")
        if entry.get("existed"):
            backup_rel = _safe_rel(entry.get("backup"))
            backup = os.path.realpath(os.path.join(transaction_dir, backup_rel.replace("/", os.sep)))
            if os.path.commonpath([transaction_dir, backup]) != transaction_dir or not os.path.isfile(backup):
                raise ImportError("事务恢复备份缺失")
            with open(backup, "rb") as fh:
                _atomic_bytes(target, fh.read())
        else:
            try:
                os.remove(target)
            except FileNotFoundError:
                pass
    journal["state"] = "recovered"
    journal["recovered_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    project_store.update_json(journal_path, lambda _old: journal, create_default={})
    return True


def _recover_transactions(root):
    base = _transaction_root(root)
    if not os.path.isdir(base):
        return
    for name in os.listdir(base):
        transaction_dir = os.path.join(base, name)
        if os.path.isdir(transaction_dir):
            _recover_transaction(root, transaction_dir)


def _begin_transaction(root, transaction_id, paths):
    safe = "".join(ch for ch in str(transaction_id or "") if ch.isalnum() or ch in "_.-")
    if not safe:
        safe = "import-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    transaction_dir = os.path.join(_transaction_root(root), safe + "-" + uuid.uuid4().hex)
    backup_dir = os.path.join(transaction_dir, "backups")
    os.makedirs(backup_dir, exist_ok=True)
    entries = []
    seen = set()
    for index, target in enumerate(paths):
        absolute = os.path.realpath(target)
        if absolute in seen:
            continue
        seen.add(absolute)
        if os.path.commonpath([root, absolute]) != root:
            raise ImportError("事务目标越界")
        rel = os.path.relpath(absolute, root).replace(os.sep, "/")
        existed = os.path.isfile(absolute)
        backup_rel = ""
        if existed:
            backup_rel = f"backups/{index:03d}.bin"
            shutil.copyfile(absolute, os.path.join(transaction_dir, backup_rel.replace("/", os.sep)))
        entries.append({"path": rel, "existed": existed, "backup": backup_rel})
    journal = {
        "schema_version": "1.0", "state": "prepared",
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "entries": entries,
    }
    project_store.update_json(os.path.join(transaction_dir, "journal.json"),
                              lambda _old: journal, create_default={})
    return transaction_dir


def _target_image_bytes(content, target):
    """稳定资产路径为 PNG；浏览器下载的 JPEG/WebP 在导入时转为真实 PNG。"""
    if not target.lower().endswith(".png"):
        return content
    if not (content.startswith(b"\xff\xd8") or content[:4] == b"RIFF" and content[8:12] == b"WEBP"):
        return content
    try:
        with Image.open(io.BytesIO(content)) as image:
            output = io.BytesIO()
            image.convert("RGBA" if "A" in image.getbands() else "RGB").save(output, format="PNG")
            return output.getvalue()
    except (OSError, ValueError, UnidentifiedImageError) as exc:
        raise ImportError("图片格式无法转换为 PNG") from exc


def _safe_rel(value):
    raw = str(value or "").replace("\\", "/").strip()
    if not raw or raw.startswith("/") or len(raw) >= 2 and raw[1] == ":":
        raise ImportError("文件路径必须是项目内相对路径")
    parts = [part for part in raw.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise ImportError("文件路径包含非法的 ..")
    if not parts:
        raise ImportError("文件路径为空")
    return "/".join(parts)


def _unpack(package):
    if not isinstance(package, (bytes, bytearray)):
        raise ImportError("ZIP 输入必须是二进制")
    try:
        zf = zipfile.ZipFile(io.BytesIO(package))
    except zipfile.BadZipFile as exc:
        raise ImportError("ZIP 文件损坏") from exc
    files = {}
    seen_names = set()
    try:
        for info in zf.infolist():
            name = _safe_rel(info.filename)
            if name in seen_names:
                raise ImportError("ZIP 存在重复文件名")
            seen_names.add(name)
            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode):
                raise ImportError("ZIP 不允许包含符号链接")
            if info.is_dir():
                continue
            files[name] = zf.read(info)
    finally:
        zf.close()
    manifest_raw = files.pop("manifest.json", None)
    if manifest_raw is None:
        raise ImportError("ZIP 缺少 manifest.json")
    try:
        manifest = json.loads(manifest_raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ImportError("manifest.json 不是有效 JSON") from exc
    return manifest, files


def _read_manifest(manifest_or_zip, files):
    if isinstance(manifest_or_zip, dict):
        return manifest_or_zip, dict(files or {})
    if isinstance(manifest_or_zip, (bytes, bytearray)):
        return _unpack(manifest_or_zip)
    raise ImportError("manifest 必须是对象或 ZIP 二进制")


def _load_data(project_dir):
    path = os.path.join(os.path.abspath(os.fspath(project_dir)), "创作", "creation.json")
    if not os.path.isfile(path):
        raise ImportError("创作清单不存在")
    return path, project_store.read_json(path)[0]


def validate_manifest(project_dir, manifest, files):
    """校验 manifest 与现有队列条目，返回待写入行。"""
    if not isinstance(manifest, dict) or manifest.get("schema_version") != "1.0":
        raise ImportError("不支持的 manifest schema_version")
    if manifest.get("generator") != "chatgpt":
        raise ImportError("manifest.generator 必须是 chatgpt")
    root = os.path.abspath(os.fspath(project_dir))
    expected_project = os.path.basename(os.path.normpath(root))
    if manifest.get("project") and manifest.get("project") != expected_project:
        raise ImportError("manifest 项目与当前项目不匹配")
    assets = manifest.get("assets")
    if not isinstance(assets, list) or not assets:
        raise ImportError("manifest.assets 不能为空")
    path, data = _load_data(root)
    items = {str(item.get("id")): item for item in data.get("items", []) if isinstance(item, dict)}
    seen_jobs, seen_files, rows = set(), set(), []
    file_map = {str(k).replace("\\", "/"): v for k, v in (files or {}).items()}
    for asset in assets:
        if not isinstance(asset, dict):
            raise ImportError("manifest.assets 含无效条目")
        job_id = str(asset.get("job_id") or "")
        if not job_id or job_id in seen_jobs:
            raise ImportError("manifest 存在重复或空 job_id")
        seen_jobs.add(job_id)
        item = items.get(job_id)
        if not item or item.get("delivery") != "chatgpt_queue":
            raise ImportError(f"ChatGPT 任务不存在：{job_id}")
        if asset.get("shot_id") and str(asset.get("shot_id")) != str(item.get("shot_id")):
            raise ImportError(f"任务镜头不匹配：{job_id}")
        if asset.get("task_type") and asset.get("task_type") != item.get("task_type"):
            raise ImportError(f"任务类型不匹配：{job_id}")
        try:
            version = int(asset.get("version", 0))
            expected_version = int((item.get("output_spec") or {}).get("version", 0))
        except (TypeError, ValueError) as exc:
            raise ImportError(f"版本字段无效：{job_id}") from exc
        if version != expected_version:
            raise ImportError(f"任务版本不匹配：{job_id}")
        rel = _safe_rel(asset.get("file"))
        if rel == "manifest.json" or not rel.startswith("images/"):
            raise ImportError("生成文件必须位于 images/ 目录")
        if os.path.splitext(rel)[1].lower() not in ALLOWED_EXTS:
            raise ImportError("只接受 PNG、JPEG、WebP 图片")
        if rel in seen_files:
            raise ImportError("manifest 存在重复文件")
        seen_files.add(rel)
        if rel not in file_map:
            raise ImportError(f"ZIP 缺少文件：{rel}")
        content = file_map[rel]
        if not isinstance(content, (bytes, bytearray)) or not content:
            raise ImportError(f"图片文件为空：{rel}")
        rows.append({"asset": asset, "item": item, "rel": rel, "content": bytes(content)})
    return rows


def import_package(project_dir, manifest_or_zip, files=None, transaction_id=None):
    # 自动回库与手工导入共享锁，活跃事务不会被另一个请求当成崩溃恢复。
    root = os.path.realpath(os.fspath(project_dir))
    with project_store._exclusive(os.path.join(root, "创作", ".chatgpt-import-guard")):
        return _import_package(project_dir, manifest_or_zip, files, transaction_id)


def _import_package(project_dir, manifest_or_zip, files=None, transaction_id=None):
    """导入 ZIP 或 manifest+文件映射，全部成功后才改写创作清单。"""
    root = os.path.abspath(os.fspath(project_dir))
    _recover_transactions(root)
    manifest, file_map = _read_manifest(manifest_or_zip, files)
    rows = validate_manifest(root, manifest, file_map)
    manifest_path, _ = _load_data(root)
    target_rows = []
    index_path = os.path.join(root, "素材", "素材图.json")
    plans = []
    for row in rows:
        item_id = str(row["item"]["id"])
        filename = os.path.basename(row["rel"])
        spec = row["item"].get("output_spec") or {}
        target_rel = str(spec.get("target_path") or "").replace("\\", "/").strip()
        if target_rel:
            target_dir = os.path.join(root, os.path.dirname(target_rel.replace("/", os.sep)))
            target = os.path.realpath(os.path.join(root, target_rel.replace("/", os.sep)))
        else:
            target_dir = os.path.join(root, "创作", item_id)
            target = os.path.realpath(os.path.join(target_dir, filename))
        if os.path.commonpath([root, target]) != root:
            raise ImportError("目标文件越界")
        plans.append((row, target_dir, target))
    asset_rows = [row for row in rows if (row["item"].get("output_spec") or {}).get("target_path")]
    transaction_paths = [target for _row, _target_dir, target in plans]
    if asset_rows:
        transaction_paths.append(index_path)
    transaction_paths.append(manifest_path)
    transaction_dir = _begin_transaction(root, transaction_id, transaction_paths)
    try:
        for row, target_dir, target in plans:
            item_id = str(row["item"]["id"])
            os.makedirs(target_dir, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".chatgpt.", suffix=".tmp", dir=target_dir)
            with os.fdopen(fd, "wb") as fh:
                fh.write(_target_image_bytes(row["content"], target))
                fh.flush()
                os.fsync(fh.fileno())
            if versions is not None and os.path.isfile(target):
                versions.snapshot(target)
            os.replace(tmp, target)
            try:
                rel_media = os.path.relpath(target, VIDEO_ROOT).replace(os.sep, "/")
            except ValueError:
                # 测试目录或外置项目可能位于不同盘符；生产项目仍使用 VIDEO 根相对路径。
                rel_media = target.replace(os.sep, "/")
            target_rows.append((row["item"]["id"], rel_media))

        # 资产队列落到稳定素材路径后同步索引；注册表与后续派生任务立即可见。
        if asset_rows:
            def mutate_index(data):
                if not isinstance(data, dict):
                    data = {}
                zones = {"character": "人物", "scene": "场景", "prop": "道具"}
                for row in asset_rows:
                    item = row["item"]
                    zone = zones.get(str(item.get("asset_kind") or ""))
                    ident = str(item.get("asset_id") or "")
                    target_rel = str((item.get("output_spec") or {}).get("target_path") or "").replace("\\", "/")
                    if not zone or not ident or not target_rel:
                        continue
                    assets = (item.get("asset_context") or {}).get("assets") or []
                    source = assets[0] if assets and isinstance(assets[0], dict) else {}
                    state_id = str(item.get("asset_state_id") or "")
                    zone_data = data.setdefault(zone, {})
                    if state_id:
                        parent = zone_data.setdefault(ident, {
                            "name": str(source.get("name") or ident),
                        })
                        mother_rel = f"素材/人物/{ident}.png"
                        if os.path.isfile(os.path.join(root, mother_rel.replace("/", os.sep))):
                            parent.setdefault("path", mother_rel)
                            parent.setdefault("prompt", str(source.get("sheet_prompt") or source.get("prompt") or ""))
                        state_source = source.get("state") if isinstance(source.get("state"), dict) else {}
                        parent.setdefault("states", {})[state_id] = {
                            "path": target_rel,
                            "prompt": str(item.get("prompt_assembled") or item.get("prompt") or ""),
                            "name": str(state_source.get("label") or state_id),
                        }
                    else:
                        zone_data[ident] = {
                            "path": target_rel,
                            "prompt": str(item.get("prompt_assembled") or item.get("prompt") or ""),
                            "name": str(source.get("name") or ident),
                            **({"states": zone_data.get(ident, {}).get("states")}
                               if isinstance(zone_data.get(ident, {}).get("states"), dict) else {}),
                        }
                return data
            project_store.update_json(index_path, mutate_index, create_default={})

        def mutate(data):
            by_id = {str(item.get("id")): item for item in data.get("items", []) if isinstance(item, dict)}
            now = datetime.datetime.now().isoformat(timespec="seconds")
            for item_id, rel_media in target_rows:
                item = by_id.get(str(item_id))
                if not item:
                    raise ImportError(f"导入期间任务消失：{item_id}")
                item.setdefault("outputs", [])
                if rel_media not in item["outputs"]:
                    item["outputs"].append(rel_media)
                item["status"] = "done"
                item["updated_at"] = now

        project_store.update_json(manifest_path, mutate)
    except Exception:
        _recover_transaction(root, transaction_dir)
        raise
    # 成功提交必须先落盘；清理中断不能让下次启动回滚已成功的导入。
    def mark_committed(journal):
        journal["state"] = "committed"
        return journal
    project_store.update_json(os.path.join(transaction_dir, "journal.json"), mark_committed)
    return {"imported": len(target_rows), "items": [item_id for item_id, _ in target_rows]}
