# -*- coding: utf-8 -*-
"""项目级资产注册表与 @资产 引用解析。

注册表只暴露稳定引用、显示名、用途和图片路径；人物/场景/道具的长设定
仍留在素材资产档案中，不会被镜头提示词自动展开。
"""
import json
import os
import sys
from pathlib import Path

try:
    from script_repository import is_collective_asset
except Exception:
    def is_collective_asset(item):
        return bool(isinstance(item, dict) and item.get("is_collective"))

try:
    import asset_relations
except Exception:
    try:
        import importlib.util as _asset_rel_import
        _asset_rel_spec = _asset_rel_import.spec_from_file_location("asset_relations", os.path.join(os.path.dirname(__file__), "asset_relations.py"))
        asset_relations = _asset_rel_import.module_from_spec(_asset_rel_spec)
        _asset_rel_spec.loader.exec_module(asset_relations)
    except Exception:
        asset_relations = None

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


class AssetReferenceError(ValueError):
    """资产引用无法解析或类型不匹配。"""


_KIND_ALIASES = {
    "character": "character", "characters": "character", "actor": "character", "actors": "character", "人物": "character",
    "scene": "scene", "scenes": "scene", "location": "scene", "locations": "scene", "场景": "scene",
    "prop": "prop", "props": "prop", "object": "prop", "objects": "prop", "道具": "prop",
    "style": "style", "styles": "style", "画风": "style",
}
_KIND_FILES = {
    "character": ("人物.json", ("characters", "人物", "actors"), "人物", "角色"),
    "scene": ("场景.json", ("scenes", "场景", "locations"), "场景", "场景"),
    "prop": ("道具.json", ("props", "道具", "objects", "items"), "道具", "道具"),
}
_INDEX_ZONES = {
    "character": ("人物", "character", "characters"),
    "scene": ("场景", "scene", "scenes"),
    "prop": ("道具", "prop", "props"),
}


def _canon(value):
    return "".join(ch.lower() for ch in str(value or "") if ch.isalnum())


def _kind(value):
    return _KIND_ALIASES.get(str(value or "").strip().lower()) or _KIND_ALIASES.get(str(value or "").strip())


def normalize_asset_ref(ref, default_kind=None):
    """把 @人物:hero、@character/hero 归一为 @character:hero。"""
    raw = str(ref or "").strip()
    if not raw:
        raise AssetReferenceError("资产引用不能为空")
    if raw.startswith("@"):
        raw = raw[1:].strip()
    kind = None
    ident = raw
    if ":" in raw:
        head, ident = raw.split(":", 1)
        kind = _kind(head)
        if not kind:
            raise AssetReferenceError(f"未知资产类型：{head}")
    elif "/" in raw:
        head, ident = raw.split("/", 1)
        possible = _kind(head)
        if possible:
            kind = possible
        else:
            ident = raw
    if kind is None and default_kind:
        kind = _kind(default_kind)
        if not kind:
            raise AssetReferenceError(f"未知资产类型：{default_kind}")
    ident = str(ident or "").strip()
    if not ident:
        raise AssetReferenceError("资产引用缺少 ID")
    return f"@{kind}:{ident}" if kind else ident


class AssetRegistry:
    """从项目资产档案和素材图索引构造稳定的只读注册表。"""

    def __init__(self, project_dir):
        self.project_dir = Path(project_dir).resolve()
        if not self.project_dir.is_dir():
            raise AssetReferenceError(f"项目目录不存在：{self.project_dir}")
        self._records = {}
        from skill_lib import read_scope
        from asset_repository import locks
        with read_scope(), locks(self.project_dir, ['素材/素材图.json']):
            self._load()

    def _read_json(self, path):
        try:
            with open(path, encoding="utf-8") as fh:
                value = json.load(fh)
            return value if isinstance(value, (dict, list)) else {}
        except (OSError, ValueError, TypeError):
            return {}

    def _index_records(self):
        data = self._read_json(self.project_dir / "素材" / "素材图.json")
        out = {kind: {} for kind in _KIND_FILES}
        if not isinstance(data, dict):
            return out
        for kind, zones in _INDEX_ZONES.items():
            for zone in zones:
                zone_data = data.get(zone)
                if isinstance(zone_data, dict):
                    for rid, value in zone_data.items():
                        if isinstance(value, dict):
                            item = dict(value)
                            item.setdefault("id", rid)
                            out[kind][str(item.get("id"))] = item
                elif isinstance(zone_data, list):
                    for value in zone_data:
                        if isinstance(value, dict) and value.get("id"):
                            out[kind][str(value.get("id"))] = dict(value)
        return out

    def _load(self):
        index = self._index_records()
        self._generated = index
        ledger = self._read_json(self.project_dir/'素材/.work/提交记录.json')
        self._changes = ledger.get('assets', {}) if isinstance(ledger, dict) else {}
        pending = {kind: [] for kind in _KIND_FILES}
        for kind, (filename, keys, zone, default_usage) in _KIND_FILES.items():
            data = self._read_json(self.project_dir / "素材" / filename)
            rows = []
            if isinstance(data, list):
                rows = data
            elif isinstance(data, dict):
                for key in keys:
                    value = data.get(key)
                    if isinstance(value, list):
                        rows = value
                        break
                    if isinstance(value, dict):
                        rows = [dict(item, id=rid) for rid, item in value.items() if isinstance(item, dict)]
                        break
            merged = {}
            for item in rows:
                if isinstance(item, dict) and item.get("id"):
                    merged[str(item["id"])] = dict(item)
            for rid, item in index.get(kind, {}).items():
                authoritative = str(rid) in merged
                if not authoritative and (self.project_dir / '素材' / filename).is_file():
                    continue
                base = merged.setdefault(str(rid), {})
                base['_archive_present'] = authoritative
                for key, value in item.items():
                    if authoritative and key in ('prompt', 'sheet_prompt', 'image_prompt', 'visual_description', 'appearance', 'identity_anchor', 'description'):
                        continue
                    if not base.get(key) and value:
                        base[key] = value
            for rid, item in merged.items():
                if kind == "character" and is_collective_asset(item):
                    continue
                pending[kind].append(item)
        if asset_relations is not None:
            pending, _ = asset_relations.normalize_asset_relations(pending)
        for kind, (_, _, zone, default_usage) in _KIND_FILES.items():
            for item in pending.get(kind, []):
                self._add_record(kind, item, zone, default_usage)
        from visual_asset_prompt import uses_planning_settings
        if uses_planning_settings(self.project_dir):
            from story_units import referenced_assets
            active = referenced_assets(self.project_dir)
            for ref, row in self._records.items():
                row['in_use'] = ref in active
        if asset_relations is not None:
            rows = asset_relations.build_relation_index(list(self._records.values()))
            self._records = {row["ref"]: row for row in rows if row.get("ref")}
        self._load_style()

    def _add_record(self, kind, item, zone, default_usage):
        ident = str(item.get("id") or "").strip()
        if not ident:
            return
        aliases = item.get("aliases") or item.get("alias") or []
        if isinstance(aliases, str):
            aliases = [aliases]
        aliases = [str(x).strip() for x in aliases if str(x).strip()]
        name = str(item.get("name") or ident).strip()
        raw_path = item.get("path") or item.get("image") or ""
        path = self._project_relative(raw_path)
        if not path:
            for ext in (".png", ".jpg", ".jpeg", ".webp"):
                candidate = self.project_dir / "素材" / zone / (ident + ext)
                if candidate.is_file():
                    path = candidate.relative_to(self.project_dir).as_posix()
                    break
        ref = f"@{kind}:{ident}"
        # 提示词是资产详情弹窗的稳定摘要；三件套仍保留完整字段，
        # 注册表只暴露当前类型对应的一条生图提示词，避免前端猜字段。
        from visual_asset_prompt import visual_contract
        generated = self._generated.get(kind, {}).get(ident, {})
        status = visual_contract(kind, item, project=self.project_dir, image=generated, has_image=bool(path))
        status['archive_present'] = item.get('_archive_present', True)
        prompt = status['subject']
        self._records[ref] = {
            "ref": ref,
            "kind": kind,
            "id": ident,
            "name": name,
            "path": path,
            "usage": str(item.get("usage") or item.get("kind") or default_usage),
            "aliases": aliases,
            "prompt": str(prompt or "").strip(),
            "prompt_editable": status['editable_prompt'],
            "visual_status": {key: value for key, value in status.items() if key not in ('subject', 'editable_prompt')},
            "asset_revision": self._revision(item.get("asset_revision")),
            "media_settings_revision": generated.get('settings_revision'),
            "settings_source": item.get('settings_source'),
            "settings_updated_at": item.get('settings_updated_at'),
            "settings_change": self._changes.get(ref),
        }
        for field in ("parent_ref", "relation", "derived_from", "related_refs", "source_episode_ids", "style", "style_prompt", "visual_description"):
            value = item.get(field)
            if value not in (None, "", []):
                self._records[ref][field] = value
        if isinstance(item.get("states"), list):
            # 状态资产（派生状态）随注册表暴露给资产提炼页；path 指向已生成的
            # 状态图 <角色id>__<状态id>.png，未生成时为空串由前端显示占位。
            exposed = []
            for st in item["states"]:
                if not isinstance(st, dict):
                    continue
                if st.get('output_asset_ref'):
                    continue
                sid = str(st.get("id") or "").strip()
                if not sid:
                    continue
                spath = ""
                indexed_path = str(((generated.get('states') or {}).get(sid) or {}).get('path') or '')
                indexed_file = (self.project_dir / indexed_path).resolve()
                if indexed_path and indexed_file.is_relative_to(self.project_dir) and indexed_file.is_file():
                    spath = indexed_file.relative_to(self.project_dir).as_posix()
                for ext in (".png", ".jpg", ".jpeg", ".webp"):
                    if spath:
                        break
                    candidate = self.project_dir / "素材" / zone / (f"{ident}__{sid}" + ext)
                    if candidate.is_file():
                        spath = candidate.relative_to(self.project_dir).as_posix()
                        break
                exposed.append({
                    "id": sid,
                    "label": str(st.get("label") or sid),
                    "look_diff": str(st.get("look_diff") or ""),
                    "camp": str(st.get("camp") or ""),
                    "episodes": [str(e) for e in (st.get("episodes") or [])],
                    "path": spath,
                    "visual_status": {key: value for key, value in visual_contract(
                        kind, item, st, project=self.project_dir,
                        image=(generated.get('states') or {}).get(sid, {}), has_image=bool(spath)).items()
                        if key not in ('subject', 'editable_prompt')},
                })
            if exposed:
                self._records[ref]["states"] = exposed

    @staticmethod
    def _revision(value):
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return 1

    def _project_relative(self, raw):
        if not raw:
            return ""
        value = str(raw).replace("\\", "/").strip()
        try:
            path = Path(value)
            if path.is_absolute():
                path = path.resolve()
            else:
                path = (self.project_dir / value).resolve()
            if os.path.commonpath([str(self.project_dir), str(path)]) != str(self.project_dir):
                return ""
            return path.relative_to(self.project_dir).as_posix() if path.is_file() else ""
        except (OSError, ValueError):
            return ""

    def _load_style(self):
        data = self._read_json(self.project_dir / "剧本" / "style.json")
        if not isinstance(data, dict):
            return
        ident = str(data.get("image") or "").strip()
        # E10：显式 "auto"（仅知识库、不注 skill）不是真实画风资产，禁止注册成 @style 记录
        if ident and ident != "auto":
            self._records[f"@style:{ident}"] = {
                "ref": f"@style:{ident}", "kind": "style", "id": ident,
                "name": ident, "path": "", "usage": "画风 Skill", "aliases": [],
            }

    def list(self, kind=None):
        wanted = _kind(kind) if kind else None
        rows = [dict(value) for value in self._records.values() if not wanted or value["kind"] == wanted]
        return sorted(rows, key=lambda value: (value["kind"], value["id"]))

    def resolve(self, ref):
        raw = str(ref or '').strip()
        if '#' in raw:
            base, state_id = raw.split('#', 1)
            mother = self.resolve(base)
            state = next((s for s in mother.get('states') or [] if s['id'] == state_id), None)
            if state is None:
                raise AssetReferenceError(f"找不到派生状态：{ref}")
            return {**mother, 'ref': mother['ref']+'#'+state_id,
                    'name': mother['name']+' · '+state['label'], 'path': state.get('path') or '',
                    'state_id': state_id, 'parent_ref': mother['ref'],
                    'visual_status': state.get('visual_status') or {}}
        normalized = normalize_asset_ref(ref)
        if normalized in self._records:
            return dict(self._records[normalized])
        wanted_kind = normalized[1:].split(":", 1)[0] if normalized.startswith("@") and ":" in normalized else None
        needle = _canon(normalized.split(":", 1)[-1] if ":" in normalized else normalized)
        matches = []
        for row in self._records.values():
            if wanted_kind and row["kind"] != wanted_kind:
                continue
            candidates = [row["id"], row["name"], *row.get("aliases", [])]
            if any(_canon(value) == needle for value in candidates):
                matches.append(row)
        if len(matches) == 1:
            return dict(matches[0])
        if len(matches) > 1:
            raise AssetReferenceError(f"资产引用有歧义：{ref}")
        raise AssetReferenceError(f"找不到资产：{ref}")


def resolve_asset_refs(project_dir, refs):
    """解析并去重一组引用，返回安全的注册表记录。"""
    registry = AssetRegistry(project_dir)
    out, seen = [], set()
    for ref in refs or []:
        row = registry.resolve(ref)
        if row["ref"] not in seen:
            seen.add(row["ref"])
            out.append(row)
    return out


__all__ = ["AssetRegistry", "AssetReferenceError", "normalize_asset_ref", "resolve_asset_refs"]
