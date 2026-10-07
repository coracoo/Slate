# -*- coding: utf-8 -*-
"""项目资产关系归一化与层级索引。

资产的长设定仍保存在人物/场景/道具三份 JSON 中，本模块只负责把
资产之间的结构关系变成稳定的 ``@kind:id`` 引用，供提炼页、分镜和
生图/生视频提示词共同消费。关系字段不展开资产正文，避免提示词再次
把整份档案拼进模型请求。
"""
from __future__ import annotations

import copy
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

KINDS = ("character", "scene", "prop")
RELATIONS = {
    "component_of", "variant_of", "derived_from", "located_in",
    "used_with", "contains", "related_to",
}
_KIND_ALIASES = {
    "character": "character", "characters": "character", "actor": "character", "actors": "character", "人物": "character", "角色": "character",
    "scene": "scene", "scenes": "scene", "location": "scene", "locations": "scene", "场景": "scene",
    "prop": "prop", "props": "prop", "object": "prop", "objects": "prop", "道具": "prop",
}
_KEYS = {"character": "characters", "scene": "scenes", "prop": "props"}
_PROMPT_REF_RE = re.compile(
    r"@([A-Za-z\u4e00-\u9fff][A-Za-z0-9_:/\-\u4e00-\u9fff]*)"
)


def _canon(value):
    return "".join(ch.lower() for ch in str(value or "") if ch.isalnum())


def asset_ref(kind, ident):
    """返回统一的项目内资产引用。"""
    return f"@{kind}:{ident}"


def _kind(value):
    raw = str(value or "").strip()
    return _KIND_ALIASES.get(raw.lower()) or _KIND_ALIASES.get(raw)


def _rows(data):
    rows = {kind: [] for kind in KINDS}
    if not isinstance(data, dict):
        return rows
    for kind, key in _KEYS.items():
        value = data.get(key) or data.get(kind) or []
        if isinstance(value, dict):
            value = [dict(item, id=rid) for rid, item in value.items() if isinstance(item, dict)]
        rows[kind] = [item for item in value if isinstance(item, dict) and str(item.get("id") or "").strip()]
    return rows


def _make_lookup(rows):
    exact = {}
    loose = {}
    for kind, values in rows.items():
        for item in values:
            ident = str(item.get("id") or "").strip()
            ref = asset_ref(kind, ident)
            exact[ref] = ref
            for state in item.get('states') or []:
                if isinstance(state, dict) and state.get('id') and not state.get('output_asset_ref'):
                    state_ref = ref + '#' + str(state['id'])
                    exact[state_ref] = state_ref
            names = [ident, item.get("name"), item.get("display_name")]
            aliases = item.get("aliases") or item.get("alias") or []
            names.extend(aliases if isinstance(aliases, list) else [aliases])
            for name in names:
                key = _canon(name)
                if key:
                    loose.setdefault((kind, key), set()).add(ref)
                    loose.setdefault((None, key), set()).add(ref)
    return exact, loose


def _resolve(value, lookup, default_kind=None):
    """解析 id、名称、@kind:id；返回 (ref, reason)。"""
    if isinstance(value, dict):
        value = value.get("ref") or value.get("asset_ref") or value.get("id") or value.get("name") or ""
    raw = str(value or "").strip()
    if not raw:
        return None, None
    if raw.startswith("@"):
        raw = raw[1:]
    kind = None
    ident = raw
    if ":" in raw:
        head, ident = raw.split(":", 1)
        kind = _kind(head)
        if not kind:
            return None, f"未知资产类型：{head}"
    elif "/" in raw:
        head, ident = raw.split("/", 1)
        kind = _kind(head)
    kind = kind or _kind(default_kind)
    ident = str(ident or "").strip()
    if not ident:
        return None, "资产引用缺少 id"
    if kind:
        direct = asset_ref(kind, ident)
        if direct in lookup[0]:
            return direct, None
        if '#' in ident:
            return None, f"找不到派生状态：{value}"
        matches = lookup[1].get((kind, _canon(ident)), set())
    else:
        matches = lookup[1].get((None, _canon(ident)), set())
    if len(matches) == 1:
        return next(iter(matches)), None
    if not matches:
        return None, f"找不到资产：{value}"
    return None, f"资产引用有歧义：{value}"


def _as_list(value):
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def extract_asset_refs(text, data):
    """从素材提示词中提取可解析的全局资产引用。

    提示词只保存 ``@kind:id`` 这样的稳定引用，资产长设定不会在这里
    展开。名称、别名形式的引用也会按当前三件套解析成规范 ref；画风
    ``@style`` 不属于三件套关系，因此不会写入资产关系字段。
    """
    rows = _rows(data)
    exact, loose = _make_lookup(rows)
    lookup = (exact, loose)
    result = []
    seen = set()
    for match in _PROMPT_REF_RE.finditer(str(text or "")):
        token = "@" + match.group(1)
        if ":" not in token and "/" not in token:
            continue
        ref, _reason = _resolve(token, lookup)
        if ref and ref not in seen:
            seen.add(ref)
            result.append(ref)
    return result


def normalize_asset_relations(data):
    """归一化三件套中的关系字段，返回 ``(data, issues)``。

    支持 LLM 常见的 ``owner``、``parent``、``variant_of``、``base_ref``
    等输入别名；未知引用会进入 issues，并被清除，避免产生不可解析的
    分镜引用。父级只允许形成无环树，兄弟关联保存在 related_refs。
    """
    out = copy.deepcopy(data if isinstance(data, dict) else {})
    rows = _rows(out)
    exact, loose = _make_lookup(rows)
    lookup = (exact, loose)
    issues = []
    for kind, values in rows.items():
        for item in values:
            ident = str(item.get("id") or "").strip()
            self_ref = asset_ref(kind, ident)
            parent_value = item.get("parent_ref")
            parent_cleared = 'parent_ref' in item and parent_value in (None, '')
            display_locked = parent_cleared or 'parent_ref' in (item.get('locked_fields') or [])
            if parent_value in (None, "") and not parent_cleared:
                parent_value = item.get("owner_ref")
            if parent_value in (None, "") and not parent_cleared:
                parent_value = item.get("parent")
            if parent_value in (None, "") and kind == "prop" and not parent_cleared:
                parent_value = item.get("owner")
            parent, reason = _resolve(parent_value, lookup, "character" if kind == "prop" else None)
            if parent and '#' in parent:
                reason = "展示归属应选择母素材；派生图请填入生成参考"
            # 道具 owner 既可能是角色，也可能是场景陈设；先按角色解析，失败后再按场景解析。
            if reason and kind == "prop" and parent_value not in (None, ""):
                parent, reason = _resolve(parent_value, lookup, "scene")
            if reason:
                issues.append({"ref": self_ref, "field": "parent_ref", "message": reason})
                parent = None
            if parent == self_ref:
                issues.append({"ref": self_ref, "field": "parent_ref", "message": "资产不能挂载到自身"})
                parent = None
            if parent:
                item["parent_ref"] = parent
                relation = str(item.get("relation") or "").strip()
                default_relation = "located_in" if parent.startswith("@scene:") else ("component_of" if kind == "prop" else "contains")
                item["relation"] = relation if relation in RELATIONS else default_relation
            elif "parent_ref" in item:
                if parent_cleared:
                    item['parent_ref'] = None
                else:
                    item.pop("parent_ref", None)
                if item.get("relation") in ("component_of", "contains", "located_in"):
                    item.pop("relation", None)

            derived_value = item.get("derived_from")
            if derived_value in (None, ""):
                derived_value = item.get("base_ref")
            if derived_value in (None, ""):
                derived_value = item.get("variant_of")
            derived, reason = _resolve(derived_value, lookup, kind)
            if reason:
                issues.append({"ref": self_ref, "field": "derived_from", "message": reason})
                derived = None
            if derived and derived.split('#', 1)[0] == self_ref:
                issues.append({"ref": self_ref, "field": "derived_from", "message": "资产不能派生自自身"})
                derived = None
            if derived:
                item["derived_from"] = derived
                derived_parent = derived.split('#', 1)[0]
                # 旧档案可由派生来源推导默认层级；人工明确的展示归属独立保留。
                if kind == "scene" and derived.startswith("@scene:") and not parent and not display_locked:
                    # 场景派生状态与人物子素材使用相同层级，保留原 ID 和引用。
                    parent = derived_parent
                    item["parent_ref"] = parent
                    item["relation"] = "derived_from"
                elif (kind == "prop" and derived.startswith("@prop:")
                        and (not parent or parent.startswith("@character:")) and not display_locked):
                    parent = derived_parent
                    item["parent_ref"] = parent
                    item["relation"] = "derived_from"
                elif not item.get("relation"):
                    item["relation"] = "variant_of"
            elif "derived_from" in item:
                item.pop("derived_from", None)

            related = []
            raw_related = item.get("related_refs")
            for relation in _as_list(raw_related) + _as_list(item.get("relations")):
                if isinstance(relation, dict):
                    candidate = relation.get("ref") or relation.get("asset_ref") or relation.get("target") or relation.get("id")
                else:
                    candidate = relation
                ref, reason = _resolve(candidate, lookup)
                if reason:
                    issues.append({"ref": self_ref, "field": "related_refs", "message": reason})
                    continue
                if ref and ref != self_ref and ref not in related:
                    related.append(ref)
            if related:
                item["related_refs"] = related
            elif "related_refs" in item:
                item.pop("related_refs", None)
            item.pop("relations", None)

    by_ref = {asset_ref(kind, str(item.get("id"))): item for kind, values in rows.items() for item in values}
    # 父子树最多两层。派生关系的来源可能本身已经是子素材（例如“残余
    # 蛛丝”派生自“蛛丝缓冲茧”，而缓冲茧又派生自炸弹）；为了不形成
    # 第三级，把显示层级提升到来源的母素材，同时保留 derived_from 作为
    # 精确血缘。这样前端树和拖拽规则都只需处理母素材→子素材两层。
    for current_ref, item in by_ref.items():
        if item.get("relation") != "derived_from":
            continue
        parent = item.get("parent_ref")
        parent_item = by_ref.get(parent)
        if not parent_item or not parent_item.get("parent_ref"):
            continue
        seen = {current_ref}
        ancestor = parent_item.get("parent_ref")
        while ancestor and ancestor not in seen:
            seen.add(ancestor)
            ancestor_item = by_ref.get(ancestor)
            if not ancestor_item or not ancestor_item.get("parent_ref"):
                break
            ancestor = ancestor_item.get("parent_ref")
        if ancestor and ancestor in by_ref and ancestor != current_ref:
            item["parent_ref"] = ancestor

    # 只对 parent_ref 做环检测。发现环时断开当前节点边，保留其余关系。
    for ref, item in by_ref.items():
        seen = {ref}
        cursor = item.get("parent_ref")
        while cursor:
            if cursor in seen:
                issues.append({"ref": ref, "field": "parent_ref", "message": "资产父子关系存在环，已断开"})
                item.pop("parent_ref", None)
                if item.get("relation") == "component_of":
                    item.pop("relation", None)
                break
            seen.add(cursor)
            cursor = by_ref.get(cursor, {}).get("parent_ref")

    return out, issues


def build_relation_index(records):
    """为注册表记录补充 children_refs，不把派生字段写回剧本 JSON。"""
    rows = [row for row in (records or []) if isinstance(row, dict) and row.get("ref")]
    children = {row["ref"]: [] for row in rows}
    for row in rows:
        parent = row.get("parent_ref")
        if parent in children and row["ref"] not in children[parent]:
            children[parent].append(row["ref"])
    for row in rows:
        row["children_refs"] = sorted(children.get(row["ref"], []))
    return rows


__all__ = ["KINDS", "RELATIONS", "asset_ref", "extract_asset_refs", "normalize_asset_relations", "build_relation_index"]
