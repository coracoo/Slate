# -*- coding: utf-8 -*-
"""剧本与项目级资产的确定性读取、合并工具。

逐集正文不再依赖首次生成的剧本.txt；项目级资产按稳定 id 合并，已锁定字段保持不变。
"""
import json
import os
import re
import sys

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


def _read(path, default):
    if not os.path.isfile(path):
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def load_script(project, episode_id=None):
    """读取项目剧本；generated 模式按分集顺序拼接有效正文。"""
    project = os.path.abspath(project)
    book = _read(os.path.join(project, "剧本", "分集.json"), {})
    mode = book.get("mode") or _read(os.path.join(project, "剧本", "source.json"), {}).get("mode")
    episodes = book.get("episodes") or []
    if episode_id:
        for ep in episodes:
            if str(ep.get("id")) == str(episode_id):
                return str(ep.get("text") or "")
        return ""
    if mode == "generated" or episodes:
        parts = []
        for ep in episodes:
            text = str(ep.get("text") or "").strip()
            if text:
                label = str(ep.get("title") or ep.get("id") or "").strip()
                parts.append((f"【{ep.get('id', '')} {label}】\n" if label else "") + text)
        if parts:
            return "\n\n".join(parts) + "\n"
    path = os.path.join(project, "剧本", "剧本.txt")
    try:
        return open(path, encoding="utf-8").read() if os.path.isfile(path) else ""
    except OSError:
        return ""


def _merge_states(old_states, new_states):
    """状态资产按 id 并集合并：分集提炼各报本集所见状态，轨迹跨集累积不覆盖。

    同 id 状态：episodes 并集；其余字段以新值为准（后集通常描述更完整的变体）。"""
    merged = {}
    for st in list(old_states or []) + list(new_states or []):
        if not isinstance(st, dict) or not st.get("id"):
            continue
        sid = str(st["id"])
        if sid in merged:
            eps = list(merged[sid].get("episodes") or [])
            for ep in st.get("episodes") or []:
                if str(ep) not in eps:
                    eps.append(str(ep))
            row = dict(st)
            if eps:
                row["episodes"] = eps
            merged[sid] = row
        else:
            merged[sid] = dict(st)
    return list(merged.values())


def _merge_item(old, new, episode_id):
    """合并单个资产，locked_fields 防止提炼覆盖人工确认内容。"""
    out = dict(old or {})
    locked = set(out.get("locked_fields") or [])
    for key, value in (new or {}).items():
        if key == "states":
            continue  # 状态轨在循环外按 id 并集合并，防止后集覆盖前集
        if key in locked or key in ('voice_binding', 'voice_variants'):
            continue
        if value is not None and value != "":
            out[key] = value
    new_states = (new or {}).get("states")
    if new_states:
        out["states"] = _merge_states(out.get("states"), new_states)
    eps = list(out.get("source_episode_ids") or [])
    if episode_id and episode_id not in eps:
        eps.append(episode_id)
    if eps:
        out["source_episode_ids"] = eps
    return out


def _asset_name_key(value):
    """用于跨集去重的人物/场景名称键；不用于道具。"""
    text = str(value or "").strip().lower()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text)


def _looks_like_scene_owner(value):
    """提取结果中的 owner 只允许用场景 id 推断 located_in。"""
    raw = str(value or "").strip().lower()
    return raw.startswith(("@scene:", "scene_", "scene-", "loc_", "loc-", "sc_", "sc-")) or "scene" in raw


def _prepare_incoming_item(key, item):
    """清理 LLM 的兼容字段，避免普通叙事道具误挂到人物母素材下。

    kind 明确为叙事且没有 parent_ref/relation/derived_from 时，owner 只是一段
    剧情使用说明，不应被当成素材继承关系；场景 owner 仍保留，供关系归一化
    推断 located_in。旧数据未写 kind 时保持原行为，以兼容已有导入和测试。
    """
    out = dict(item or {})
    if key == "props" and "kind" in out and str(out.get("kind") or "").strip() == "叙事":
        has_explicit_relation = bool(out.get("parent_ref") or out.get("relation") or out.get("derived_from"))
        if not has_explicit_relation and out.get("owner") and not _looks_like_scene_owner(out.get("owner")):
            out.pop("owner", None)
    return out


# 这些是“群体描述”，不能作为可复用的单人角色资产。群演、师生和路人
# 由分镜提示词中的质量约束生成；只有剧本明确给出姓名或甲乙等可区分个体时
# 才进入人物资产表。
_COLLECTIVE_NAME_TERMS = (
    "五人组", "六人组", "小组", "团队", "集体", "师生", "全班", "班级",
    "同学们", "学生们", "老师们", "教师们", "人群", "群众", "众人", "路人",
    "群演", "大军", "士兵们", "守卫们", "男女学生", "男学生", "女学生",
)
_COLLECTIVE_ID_TERMS = {
    "wurenxiaozu", "liurenxiaozu", "shisheng", "quantongban", "nanxuesheng",
    "nvxuesheng", "xuesheng", "renqun", "qunzhong", "zhongren", "luren",
}


def is_collective_asset(item):
    """判断人物/道具记录是否是不可引用的群体占位记录。"""
    if not isinstance(item, dict):
        return False
    if item.get("is_collective") is True:
        return True
    name = str(item.get("name") or "").strip()
    ident = re.sub(r"[^a-z0-9]", "", str(item.get("id") or "").lower())
    if ident in _COLLECTIVE_ID_TERMS:
        return True
    if name.endswith("们") or name in ("学生", "教师", "师生", "同学", "人群"):
        return True
    return any(term in name for term in _COLLECTIVE_NAME_TERMS)


def is_asset_prop(item):
    """叙事道具或挂在母素材下的关联子素材。"""
    if not isinstance(item, dict) or not item.get("asset_required", True):
        return False
    kind = str(item.get("kind") or "叙事").strip()
    return kind == "叙事" or bool(item.get("parent_ref") or item.get("relation") or item.get("derived_from")) or kind in ("关联素材", "服饰", "配饰", "组件")


_PROP_APPEARANCE_TERMS = (
    "眼眸", "眼睛", "瞳孔", "赤瞳", "眼色", "长发", "短发", "黑发", "白发", "银发",
    "发型", "头发", "头部", "马尾", "双马尾", "单马尾", "高马尾", "低马尾",
    "发辫", "辫子", "发束", "发饰", "肤色", "皮肤", "五官", "眉眼", "脸型", "脸部", "身形",
    "体型", "身材", "面容", "发色", "长睫毛", "呆毛", "触角发",
)
_PROP_FAMILY_PATTERNS = (
    ("mimic_collar", ("项圈", "拟态圈", "仿生圈")),
    ("spider_legs", ("蜘蛛步足", "蛛形步足", "蛛腿", "蛛足", "蛛肢", "蛛脚")),
    ("spider_silk", ("蛛丝", "蜘蛛丝", "白亮丝", "雪白丝")),
)


def _prop_text(item):
    return " ".join(str(item.get(k) or "") for k in ("name", "image_prompt", "shot_hint"))


def _prop_family(item):
    name = str(item.get("name") or "")
    # 中文提取结果会出现“八只蜘蛛足/蜘蛛节肢/银灰蛛腿”等同义写法，
    # 只要明确是蛛类肢体就归入同一个蛛腿母素材。
    if "蛛" in name and any(term in name for term in ("足", "腿", "肢", "脚")):
        return "spider_legs"
    for family, terms in _PROP_FAMILY_PATTERNS:
        if any(term in name for term in terms):
            if family == "spider_silk" and any(term in name for term in ("手帕", "缓冲茧", "防护网", "残余")):
                continue
            return family
    return None


def _prop_should_keep(item):
    """资产提炼的硬门槛：只保留有镜头价值或能稳定复用的独立素材。"""
    if not isinstance(item, dict) or not item.get("asset_required", True):
        return False
    name = str(item.get("name") or "").strip()
    if not name:
        return False
    text = _prop_text(item)
    kind = str(item.get("kind") or "叙事").strip()
    actions = item.get("actions") or []
    has_action = bool(actions) or bool(item.get("shot_hint"))
    # 兼容早期 JSON：未写 kind 但 owner 明确指向角色的记录，
    # 视为角色子素材，交给关系归一化补齐 parent_ref。
    legacy_owner_child = ("kind" not in item and bool(item.get("owner"))
                          and not _looks_like_scene_owner(item.get("owner")))
    child = (kind in ("关联素材", "服饰", "配饰", "组件")
             or bool(item.get("parent_ref") or item.get("relation") or item.get("derived_from")
                     or legacy_owner_child))
    # 发型、五官等永远属于人物外观；“触角/触须”还必须带角色父级，
    # 否则模型可能把它误报成不明种族的独立叙事道具。
    if any(term in name for term in _PROP_APPEARANCE_TERMS):
        return False
    if ("触角" in name or "触须" in name) and not child:
        return False
    family = _prop_family(item)
    if child:
        # 关联子素材必须可挂到母素材；固定身体组件只保留身份锚点，其他服饰/
        # 配饰仍需出现在动作或特写中，避免把每个外观名词都拆成一张图。
        if not (item.get("parent_ref") or item.get("owner") or item.get("derived_from")):
            return False
        # 触角/触须是角色本体外观，只有剧本把它写成独立表演点（伸缩、
        # 颤动、折断、脱落等）且给出特写/视线关注时，才建立子素材。
        # 单纯“头顶有触角”不得生成一张道具图。
        if ("触角" in name or "触须" in name) and not _has_independent_appearance_focus(item):
            return False
        if family:
            return True
        if legacy_owner_child:
            return True
        return has_action
    # 普通剧情道具必须有明确动作或镜头关注，不能只因为名词在正文出现。
    return has_action


def _has_independent_appearance_focus(item):
    """判断角色外观组件是否真的需要独立锚定。

    外观词本身不是道具；只有动作和镜头关注共同出现时，才允许成为
    character 的组件子素材。这样“触角颤动特写”可保留，而“头顶有触角”
    会回到人物 appearance。
    """
    if not isinstance(item, dict):
        return False
    shot_hint = str(item.get("shot_hint") or "").strip()
    if not shot_hint:
        return False
    text = " ".join(str(x or "") for x in (item.get("actions") or [])) + " " + shot_hint
    return bool(re.search(r"(特写|近景|锁定|注视|视线|颤|抖|摆|伸|缩|收|展开|折断|断裂|脱落|掉落|勾|缠|喷)", text))


def _compact_prop_families(items):
    """同一母素材下的项圈/蛛腿/蛛丝只保留一个母子节点，其余 ID 存 aliases。"""
    kept = []
    family_index = {}
    for raw in items:
        if not _prop_should_keep(raw):
            continue
        item = dict(raw)
        family = _prop_family(item)
        parent = str(item.get("parent_ref") or item.get("owner") or "").strip()
        key = (family, parent) if family else None
        if key and key in family_index:
            target = kept[family_index[key]]
            aliases = list(target.get("aliases") or [])
            aid = str(item.get("id") or "").strip()
            if aid and aid != str(target.get("id") or "") and aid not in aliases:
                aliases.append(aid)
            for alias in item.get("aliases") or []:
                if alias and alias not in aliases and alias != str(target.get("id") or ""):
                    aliases.append(alias)
            if aliases:
                target["aliases"] = aliases
            actions = list(target.get("actions") or [])
            for action in item.get("actions") or []:
                if action not in actions and len(actions) < 3:
                    actions.append(action)
            if actions:
                target["actions"] = actions
            if not target.get("shot_hint") and item.get("shot_hint"):
                target["shot_hint"] = item.get("shot_hint")
            episodes = list(target.get("source_episode_ids") or [])
            for episode in item.get("source_episode_ids") or []:
                if episode not in episodes:
                    episodes.append(episode)
            if episodes:
                target["source_episode_ids"] = episodes
            related = list(target.get("related_refs") or [])
            for ref in item.get("related_refs") or []:
                if ref not in related:
                    related.append(ref)
            if related:
                target["related_refs"] = related
            continue
        family_index[key] = len(kept) if key else -1
        kept.append(item)
    return kept


def merge_assets(existing, incoming, episode_id=None):
    """按 characters/scenes/props 的稳定 id 合并项目资产。"""
    result = dict(existing or {})
    for key in ("characters", "scenes", "props"):
        old_items = list(result.get(key) or [])
        if key == "characters":
            old_items = [x for x in old_items if not is_collective_asset(x)]
        elif key == "props":
            old_items = [x for x in old_items if is_asset_prop(x)]
        new_items = [_prepare_incoming_item(key, x) for x in list((incoming or {}).get(key) or [])]
        if key == "characters":
            # LLM 偶尔会把“五人组/师生们”写成一个角色，直接丢弃，避免
            # 后续分镜把它当作单张角色参考图。
            new_items = [x for x in new_items if not is_collective_asset(x)]
        elif key == "props":
            new_items = [x for x in new_items if is_asset_prop(x)]
            new_items = _compact_prop_families(new_items)
        by_id = {str(x.get("id")): (i, x) for i, x in enumerate(old_items) if x.get("id")}
        # 人物/场景是项目级母素材：不同集里同名记录必须合并，保留最先出现
        # 的稳定 id，其余 id 写入 aliases，供 @引用和父子关系解析。
        by_name = {}
        if key in ("characters", "scenes"):
            for i, old in enumerate(old_items):
                name_key = _asset_name_key(old.get("name"))
                if name_key and name_key not in by_name:
                    by_name[name_key] = (i, old)
        merged = list(old_items)
        for item in new_items:
            aid = str(item.get("id") or "").strip()
            if not aid:
                continue
            if aid in by_id:
                index, old = by_id[aid]
                merged[index] = _merge_item(old, item, episode_id)
            elif key in ("characters", "scenes") and _asset_name_key(item.get("name")) in by_name:
                index, old = by_name[_asset_name_key(item.get("name"))]
                canonical_id = str(old.get("id") or "").strip()
                merged_item = _merge_item(old, item, episode_id)
                # _merge_item 会复制 incoming 的 id，这里恢复项目母素材的首个稳定 id。
                merged_item["id"] = canonical_id or str(merged_item.get("id") or aid)
                aliases = list(merged_item.get("aliases") or [])
                if aid != str(merged_item.get("id") or "") and aid not in aliases:
                    aliases.append(aid)
                for alias in item.get("aliases") or []:
                    if alias and alias not in aliases and alias != str(merged_item.get("id") or ""):
                        aliases.append(alias)
                if aliases:
                    merged_item["aliases"] = aliases
                merged[index] = merged_item
                by_id[aid] = (index, merged_item)
            else:
                added = _merge_item(item, {}, episode_id)
                merged.append(added)
                by_id[aid] = (len(merged) - 1, added)
                if key in ("characters", "scenes"):
                    name_key = _asset_name_key(added.get("name"))
                    if name_key and name_key not in by_name:
                        by_name[name_key] = (len(merged) - 1, added)
        if key == "props":
            merged = _compact_prop_families(merged)
        result[key] = merged
    if asset_relations is not None:
        result, _ = asset_relations.normalize_asset_relations(result)
        # 关系归一化会清除不存在的 parent_ref；组件/服饰/配饰若因此失去
        # 母素材，不能继续以“子素材”身份出现在前端或提示词注册表中。
        child_kinds = {"关联素材", "服饰", "配饰", "组件"}
        result["props"] = [
            item for item in (result.get("props") or [])
            if str(item.get("kind") or "叙事").strip() not in child_kinds
            or item.get("parent_ref") or item.get("derived_from")
        ]
        # 孤立组件被移除后，再归一化一次，清掉其它资产指向它们的 related_refs。
        result, _ = asset_relations.normalize_asset_relations(result)
    return result


def stable_id(name, prefix="item"):
    """为缺少 id 的新资产生成稳定的 ASCII id。"""
    value = re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-") or prefix
    return f"{prefix}-{value}"
