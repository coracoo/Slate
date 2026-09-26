# -*- coding: utf-8 -*-
"""剧本最小单元（① 第一步的产物）：读写 · 锚定 · 体检 · 反查索引 · 扩写注入块。

最小单元分落各既有 json 包，不建聚合大文件：
  剧本/大纲.json   加厚：premise highlights[] sources[] rules[] taboos[] pressure{} arcs[] throughline[] causality[]
  剧本/分集.json   每集加厚：arc_id beats[] fs_plant[] fs_pay[] state_derive[] relation_shift[]
  剧本/埋线.json   新：foreshadows[]{id,plant,set_in,form,pay_in,payoff,refs[],status} hooks[]{id,beat,question,ep}
  素材/人物.json   加厚：bio_language bio_crack bio_pressure bio_address bio_arc relations[]
  素材/场景.json   加厚：spatial_limit action_slots[]
  素材/道具.json   加厚：usage_boundary

子命令:
  show     <项目>                     打印最小单元概览（各包已有键与条数）
  check    <项目>                     确定性体检 C1-C9，exit 1=有阻断项
  anchor   <项目> [--force]           体检通过后给各包盖 anchor_rev（+1）并快照
  unanchor <项目>                     撤锚（回到"未锚定"，注入点自动退回旧行为）
  index    <项目> [--ref @kind:id]    反查索引：某素材被哪几集/哪几段引用（首次出场与关键集次只派生不落档）
  gaps     <项目> --episode E1        用已锚定白名单校验某集正文的场名与说话人（缺口上报）
  block    <项目> --episode E1        打印该集的扩写注入块（供调试，第二步由 cmd_expand 调用）

设计口径：跨集坐标（首次出场/关键集次）一律由反查派生，禁止写回素材档案造成多份真相；
无 anchor_rev 的项目所有注入点返回空串，行为与改造前逐字一致。
"""
import sys, os, json, re, argparse
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from narration import NARRATOR_ALIASES, is_narrator

UNITS_VERSION = 1
OUTLINE_KEYS = ("premise", "highlights", "sources", "rules", "taboos",
                "pressure", "arcs", "throughline", "causality")
EPISODE_KEYS = ("arc_id", "beats", "fs_plant", "fs_pay", "state_derive", "relation_shift")
CHAR_KEYS = ("bio_language", "bio_crack", "bio_pressure", "bio_address", "bio_arc", "relations")
SCENE_KEYS = ("spatial_limit", "action_slots")
PROP_KEYS = ("usage_boundary",)
BIO_LIMIT = 60
_EP_RE = re.compile(r"^E\d+$")


def _p(proj, *parts):
    return os.path.join(os.path.abspath(proj), *parts)


def path_outline(proj): return _p(proj, "剧本", "大纲.json")
def path_episodes(proj): return _p(proj, "剧本", "分集.json")
def path_threads(proj): return _p(proj, "剧本", "埋线.json")


def _read(path, default):
    if not os.path.isfile(path):
        return dict(default) if isinstance(default, dict) else list(default)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return dict(default) if isinstance(default, dict) else list(default)
    return data if isinstance(data, type(default)) else (dict(default) if isinstance(default, dict) else list(default))


def _read_asset(proj, name):
    """素材包 -> (文件路径, 顶层键, 条目列表)。"""
    key = {"人物": "characters", "场景": "scenes", "道具": "props"}[name]
    path = _p(proj, "素材", f"{name}.json")
    doc = _read(path, {})
    rows = doc.get(key) or []
    return path, key, (rows if isinstance(rows, list) else [])


def _snapshot(path):
    try:
        import versions
        versions.snapshot(path)
    except Exception as exc:
        print(f"[快照跳过] {os.path.basename(path)}: {exc}", flush=True)


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    _snapshot(path)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)


# ────────────────────────── 读 ──────────────────────────

def load_units(proj):
    """一次读齐全部最小单元。缺文件返回空默认，调用方不必判 None。"""
    outline = _read(path_outline(proj), {})
    eps_doc = _read(path_episodes(proj), {})
    eps = [e for e in (eps_doc.get("episodes") or []) if isinstance(e, dict)]
    threads = _read(path_threads(proj), {})
    chars_path, _, chars = _read_asset(proj, "人物")
    scenes_path, _, scenes = _read_asset(proj, "场景")
    props_path, _, props = _read_asset(proj, "道具")
    return {
        "outline": outline, "episodes": eps, "episodes_doc": eps_doc,
        "threads": threads,
        "foreshadows": [f for f in (threads.get("foreshadows") or []) if isinstance(f, dict)],
        "hooks": [h for h in (threads.get("hooks") or []) if isinstance(h, dict)],
        "characters": [c for c in chars if isinstance(c, dict)],
        "scenes": [s for s in scenes if isinstance(s, dict)],
        "props": [p for p in props if isinstance(p, dict)],
        "paths": {"outline": path_outline(proj), "episodes": path_episodes(proj),
                  "threads": path_threads(proj), "characters": chars_path,
                  "scenes": scenes_path, "props": props_path},
    }


def anchor_rev(proj):
    """锚定版本号；未锚定返回 0（所有注入点据此跳过）。"""
    doc = _read(path_outline(proj), {})
    return int(doc.get("anchor_rev") or 0)


def is_anchored(proj):
    return anchor_rev(proj) > 0


# ────────────────────────── 反查索引 ──────────────────────────

def _refs_of_episode(ep):
    out = []
    for key in ("cast_refs", "scene_refs", "key_asset_refs"):
        out += [str(r) for r in (ep.get(key) or []) if str(r or "").strip()]
    return out


def _ep_sort_key(ep_id):
    m = re.match(r"^E(\d+)$", str(ep_id or ""))
    return int(m.group(1)) if m else 10 ** 6


def build_index(proj):
    """素材反查表：{"@character:x": {episodes, arcs, first_ep, key_eps, foreshadows, states}}

    只派生、不落素材档案——首次出场集与关键集次有两个写入方就会分叉（参考件的前科）。
    """
    u = load_units(proj)
    eps = sorted(u["episodes"], key=lambda e: _ep_sort_key(e.get("id")))
    idx = {}

    def slot(ref):
        ref = str(ref or "").strip()
        if not ref:
            return None
        if ":" not in ref:
            return None
        return idx.setdefault(ref, {"ref": ref, "episodes": [], "arcs": [],
                                    "foreshadows": [], "state_eps": {}, "used_by": []})

    for ep in eps:
        ep_id = str(ep.get("id") or "")
        for ref in _refs_of_episode(ep):
            row = slot(ref)
            if row is None or (ep_id and ep_id not in row["episodes"]):
                if row is not None and ep_id:
                    row["episodes"].append(ep_id)
            if row is not None and ep.get("arc_id"):
                if ep["arc_id"] not in row["arcs"]:
                    row["arcs"].append(str(ep["arc_id"]))
        for sd in (ep.get("state_derive") or []):
            if not isinstance(sd, dict):
                continue
            row = slot(sd.get("ref") or sd.get("owner_ref"))
            if row is not None and ep_id:
                sid = str(sd.get("state_id") or sd.get("label") or "state")
                row["state_eps"].setdefault(sid, [])
                if ep_id not in row["state_eps"][sid]:
                    row["state_eps"][sid].append(ep_id)
    for fs in u["foreshadows"]:
        for ref in (fs.get("refs") or []):
            row = slot(ref)
            if row is not None:
                row["foreshadows"].append(str(fs.get("id") or ""))
                for which in ("set_in", "pay_in"):
                    ep_id = str(fs.get(which) or "")
                    if ep_id and ep_id not in row["episodes"]:
                        row["episodes"].append(ep_id)
    for ref, row in idx.items():
        row["episodes"].sort(key=_ep_sort_key)
        row["first_ep"] = row["episodes"][0] if row["episodes"] else None
        row["key_eps"] = list(row["episodes"])
        for sid in row["state_eps"]:
            row["state_eps"][sid].sort(key=_ep_sort_key)
    return idx


def asset_index_for(proj, ref):
    return build_index(proj).get(str(ref or "").strip()) or None


# ────────────────────────── 体检 ──────────────────────────

def _item(code, path, message, severity):
    return {"code": code, "path": path, "message": message, "severity": severity}


def _norm_name(value):
    text = re.sub(r"\s+", "", str(value or ""))
    return re.split(r"[／/（(]", text)[0].strip()


def check(proj):
    """确定性体检 C1-C9。errors=阻断（不得锚定），warnings=记录不阻断。"""
    u = load_units(proj)
    errors, warnings = [], []
    outline, eps = u["outline"], u["episodes"]
    ep_ids = [str(e.get("id") or "") for e in eps]
    ep_id_set = set(x for x in ep_ids if x)

    def known_refs():
        # 每次调用都重读六个 json，30 集项目会放大成几百次 IO —— 整个 check 只解析一次。
        try:
            from asset_registry import AssetRegistry
            return {str(r.get("ref")) for r in AssetRegistry(proj).list()}
        except Exception:
            return None
    refs_cache = {"loaded": False, "known": None}

    def valid_ref(ref):
        ref = str(ref or "").strip()
        if not ref or ":" not in ref:
            return False
        if not refs_cache["loaded"]:
            refs_cache["known"] = known_refs()
            refs_cache["loaded"] = True
        known = refs_cache["known"]
        return bool(known) and ref in known

    # C1 分段
    arcs = [a for a in (outline.get("arcs") or []) if isinstance(a, dict)]
    covered = []
    for a in arcs:
        for which in ("ep_from", "ep_to"):
            val = str(a.get(which) or "")
            if not _EP_RE.match(val):
                errors.append(_item("ARC_EP", f"arcs[{a.get('id')}].{which}",
                                    f"集号须为 E+数字（现值「{val or '空'}」）", "error"))
            elif val not in ep_id_set:
                errors.append(_item("ARC_EP_MISSING", f"arcs[{a.get('id')}].{which}",
                                    f"{val} 不在 分集.json 里", "error"))
            else:
                covered.append(_ep_sort_key(val))
    if arcs and len(arcs) > 1:
        spans = sorted((_ep_sort_key(a.get("ep_from")), _ep_sort_key(a.get("ep_to")))
                       for a in arcs if a.get("ep_from") and a.get("ep_to"))
        for (f1, t1), (f2, t2) in zip(spans, spans[1:]):
            if f2 <= t1:
                errors.append(_item("ARC_OVERLAP", "arcs", f"分段集区间重叠：{f1}-{t1} 与 {f2}-{t2}", "error"))
    # 每一集都必须落在某一段里：分段表被后批覆写成一两段时，剩下的集会"没人管"却仍带着旧 arc_id
    if arcs:
        spans = [(_ep_sort_key(a.get("ep_from")), _ep_sort_key(a.get("ep_to"))) for a in arcs
                 if a.get("ep_from") and a.get("ep_to")]
        for e in eps:
            here = _ep_sort_key(e.get("id"))
            if not any(lo <= here <= hi for lo, hi in spans):
                errors.append(_item("ARC_GAP", f"分集[{e.get('id')}]",
                                    "该集不在任何分段的区间内（分段表不完整或被覆写），第一步会漏掉它", "error"))

    # C2/C3 伏笔配对
    seen_fs = set()
    for fs in u["foreshadows"]:
        fid = str(fs.get("id") or "")
        if fid and fid in seen_fs:
            errors.append(_item("FS_DUP", f"foreshadows[{fid}]", "伏笔 id 重复", "error"))
        seen_fs.add(fid)
        ends = {}
        for which in ("set_in", "pay_in"):
            val = str(fs.get(which) or "")
            if not _EP_RE.match(val):
                errors.append(_item("FS_EP", f"foreshadows[{fid}].{which}",
                                    f"集号须为 E+数字（现值「{val or '空'}」）", "error"))
            elif val not in ep_id_set:
                errors.append(_item("FS_EP_MISSING", f"foreshadows[{fid}].{which}",
                                    f"{val} 不在 分集.json 里", "error"))
            else:
                ends[which] = _ep_sort_key(val)
        if len(ends) == 2 and ends["pay_in"] <= ends["set_in"]:
            errors.append(_item("FS_ORDER", f"foreshadows[{fid}]", "收在线早于或等于埋在线", "error"))
        if not str(fs.get("plant") or "").strip():
            warnings.append(_item("FS_EMPTY", f"foreshadows[{fid}]", "伏笔没有内容描述", "warn"))
        for ref in (fs.get("refs") or []):
            if not valid_ref(ref):
                errors.append(_item("FS_REF", f"foreshadows[{fid}].refs", f"引用不到素材：{ref}", "error"))

    # C4 引用完整性（含每集正查索引）
    for e in eps:
        eid = str(e.get("id") or "")
        arc_id = str(e.get("arc_id") or "")
        if arc_id and arcs and arc_id not in {str(a.get("id")) for a in arcs}:
            errors.append(_item("EP_ARC_REF", f"分集[{eid}].arc_id", f"分段 {arc_id} 不存在", "error"))
        for key in ("cast_refs", "scene_refs", "key_asset_refs"):
            for ref in (e.get(key) or []):
                if not valid_ref(ref):
                    errors.append(_item("EP_REF", f"分集[{eid}].{key}", f"引用不到素材：{ref}", "error"))
        # C5 与埋线表双向对账
        declared = {str(f.get("id")) for f in u["foreshadows"]}
        for key, code in (("fs_plant", "FS_PLANT_ORPHAN"), ("fs_pay", "FS_PAY_ORPHAN")):
            for fid in (e.get(key) or []):
                if str(fid) not in declared:
                    warnings.append(_item(code, f"分集[{eid}].{key}", f"埋线.json 里没有 {fid}", "warn"))
        for fs in u["foreshadows"]:
            fid = str(fs.get("id") or "")
            for which, key in (("set_in", "fs_plant"), ("pay_in", "fs_pay")):
                if str(fs.get(which) or "") == eid and fid and fid not in {str(x) for x in (e.get(key) or [])}:
                    warnings.append(_item("FS_UNMATCHED", f"分集[{eid}].{key}",
                                          f"埋线表把 {fid} 的{'埋' if key == 'fs_plant' else '收'}点标在本集，本集未认领", "warn"))
        # C6 状态派生指向
        for sd in (e.get("state_derive") or []):
            if not isinstance(sd, dict):
                warnings.append(_item("SD_SHAPE", f"分集[{eid}].state_derive", "状态派生条目不是对象", "warn"))
                continue
            if not valid_ref(sd.get("ref") or sd.get("owner_ref")):
                warnings.append(_item("SD_REF", f"分集[{eid}].state_derive",
                                      f"派生指向的素材不存在：{sd.get('ref') or sd.get('owner_ref')}", "warn"))

    # 埋线表里声明了但没有任何集引用
    if u["foreshadows"]:
        referenced = set()
        for e in eps:
            referenced |= {str(x) for x in (e.get("fs_plant") or [])} | {str(x) for x in (e.get("fs_pay") or [])}
        for fs in u["foreshadows"]:
            fid = str(fs.get("id") or "")
            if fid and fid not in referenced:
                warnings.append(_item("FS_UNCLAIMED", f"foreshadows[{fid}]", "没有任何分集认领这条线", "warn"))

    # 钩子落点
    for h in u["hooks"]:
        val = str(h.get("ep") or "")
        if not _EP_RE.match(val):
            errors.append(_item("HK_EP", f"hooks[{h.get('id')}].ep", f"集号须为 E+数字（现值「{val or '空'}」）", "error"))
        elif val not in ep_id_set:
            errors.append(_item("HK_EP_MISSING", f"hooks[{h.get('id')}].ep", f"{val} 不在 分集.json 里", "error"))

    # C7 传记与边界缺项
    # 老项目一行一条会刷出上百条噪音，体检卡直接不可读——聚合成每包一条，缺项明细只在 message 里举几个例。
    def _aggregate(items, code, path, label, fmt=lambda x: x):
        rows = [x for x in items if x]
        if not rows:
            return
        sample = "、".join(map(str, rows[:6])) + ("…" if len(rows) > 6 else "")
        warnings.append(_item(code, path, f"{label} {len(rows)} 项：{sample}", "warn"))

    _aggregate([c.get("id") for c in u["characters"]
                if not any(str(c.get(k) or "").strip() for k in CHAR_KEYS[:5])],
               "BIO_EMPTY", "人物", "传记缺项（语言风格/破绽/被逼急/称呼/弧光全空）")
    over = [c.get("id") for c in u["characters"]
            if any(len(str(c.get(k) or "")) > BIO_LIMIT * 3 for k in CHAR_KEYS[:5])]
    _aggregate(over, "BIO_LONG", "人物", f"传记超 {BIO_LIMIT * 3} 字、建议压到要点")
    _aggregate([s.get("id") for s in u["scenes"]
                if not str(s.get("spatial_limit") or "").strip()],
               "SCENE_LIMIT", "场景", "没写空间对行动的限制")
    _aggregate([p.get("id") for p in u["props"]
                if not str(p.get("usage_boundary") or "").strip()],
               "PROP_BOUNDARY", "道具", "没写使用边界（何时不生效）")

    # C8 锚定后被自动改写（locked_fields 命中记录）
    for key, rows in (("人物", u["characters"]), ("场景", u["scenes"]), ("道具", u["props"])):
        for row in rows:
            locked = row.get("locked_overridden") or []
            if locked:
                warnings.append(_item("LOCK_HIT", f"{key}[{row.get('id')}].locked_overridden",
                                      "以下人工锁定字段被生成流程碰到，需人工复核：" + "、".join(map(str, locked)), "warn"))
    # C9 死条款
    for t in (outline.get("taboos") or []):
        if isinstance(t, dict) and not (t.get("detect") or []):
            warnings.append(_item("TABOO_UNCHECKABLE", f"taboos[{t.get('id')}]",
                                  "没有 detect 关键词，这条禁区无法自动检查", "warn"))

    # C10 状态匹配键的可解析性：集号 / 场景档案名与别名 / 剧本里出现过的场名，三者之一才可能匹配上。
    # （场名比场景名更细，是并行流有意支持的口径——只认场景档案会把合法值误报成一片）
    scene_names = set()
    for s in u["scenes"]:
        for alias in [s.get("name")] + list(s.get("aliases") or []):
            norm = _norm_name(alias)
            if norm:
                scene_names.add(norm)
    for e in eps:
        for line in str(e.get("text") or "").splitlines():
            m = _SCENE_RE.match(line.strip())
            if m:
                whole = _norm_name(m.group(1))
                if len(whole) >= 2:
                    scene_names.add(whole)      # "青芜山上游·炼金坊暗管" 这种整串也是合法键
                for piece in re.split(r"[·、/／]", whole):
                    if len(piece) >= 2:
                        scene_names.add(piece)
    bad_keys = []
    for c in u["characters"]:
        for st in (c.get("states") or []):
            if not isinstance(st, dict):
                continue
            for val in (st.get("episodes") or []):
                text = str(val or "").strip()
                if not text:
                    continue
                if _EP_RE.match(text) or text in scene_names or _norm_name(text) in scene_names:
                    continue
                bad_keys.append(f"{c.get('id')}/{st.get('id')}={text}")
    if bad_keys:
        sample = "、".join(bad_keys[:6]) + ("…" if len(bad_keys) > 6 else "")
        warnings.append(_item("STATE_KEY_UNRESOLVABLE", "states[].episodes",
                              f"{len(bad_keys)} 个状态匹配键既不是 E+数字也不是已知场名，"
                              f"生图选图时永远匹配不上：{sample}", "warn"))

    # C11 疑似重复建档：同区里名字互相包含（09_仙 首跑一次造出 57 个近义实体，靠这个报出来）
    dup_pairs = []
    for zone, rows in (("人物", u["characters"]), ("场景", u["scenes"]), ("道具", u["props"])):
        names = [(_norm_name(r.get("name")), str(r.get("id")), str(r.get("name") or ""))
                 for r in rows if _norm_name(r.get("name"))]
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                a, b = names[i][0], names[j][0]
                if a == b or min(len(a), len(b)) < 3:
                    continue
                if a in b or b in a:
                    dup_pairs.append(f"{zone}:{names[i][1]}「{names[i][2]}」≈{names[j][1]}「{names[j][2]}」")
    if dup_pairs:
        warnings.append(_item("DUP_ASSET", "素材",
                              f"{len(dup_pairs)} 对名字互相包含，疑似同一实体被重复建档（并档前请人工确认，"
                              f"程序不自动并）：" + "、".join(dup_pairs[:8]) + ("…" if len(dup_pairs) > 8 else ""),
                              "warn"))

    # C12 同区 id 重复：AssetRegistry 以后者覆盖前者，素材图索引与分镜引用会串
    for zone, rows in (("人物", u["characters"]), ("场景", u["scenes"]), ("道具", u["props"])):
        seen, dup = {}, []
        for r in rows:
            rid = str(r.get("id") or "")
            if not rid:
                continue
            if rid in seen:
                dup.append(f"{zone}:{rid}「{seen[rid]}」+「{r.get('name')}」")
            else:
                seen[rid] = str(r.get("name") or "")
        if dup:
            errors.append(_item("ID_DUP", f"素材/{zone}.json",
                                f"同一 id 出现多条（索引与引用会串到错的那条），先并档再锚定：" + "、".join(dup),
                                "error"))

    # C13 孤儿建档：名册里建了、但没有任何集或伏笔引用它（09_仙 首跑一次造出 79 条这类）
    referenced = set()
    for e in eps:
        referenced |= {str(r) for r in _refs_of_episode(e)}
        for sd in (e.get("state_derive") or []):
            if isinstance(sd, dict):
                referenced.add(str(sd.get("ref") or sd.get("owner_ref")))
    for fs in u["foreshadows"]:
        referenced |= {str(r) for r in (fs.get("refs") or [])}
    orphans = []
    for zone, kind, rows in (("人物", "character", u["characters"]), ("场景", "scene", u["scenes"]),
                             ("道具", "prop", u["props"])):
        for row in rows:
            if not row.get("id"):
                continue
            ref = f"@{kind}:{row['id']}"
            if ref in referenced:
                continue
            tag = "（第一步新造）" if row.get("source") == "story_units" else ""
            orphans.append(f"{zone}:{row['id']}「{row.get('name')}」{tag}")
    if orphans:
        made = [o for o in orphans if "（第一步新造）" in o]
        legacy = len(orphans) - len(made)
        msg = (f"{len(orphans)} 个实体不被任何集或伏笔引用（其中第一步新造 {len(made)}、既有档案 {legacy}）："
               f"要么挂进相应集（① 卡分集加厚里加引用），要么删档；程序不自动删。新造未用清单前 8 个："
               + "、".join((made or orphans)[:8]) + ("…" if len(made or orphans) > 8 else ""))
        warnings.append(_item("UNREF_ASSET", "素材", msg, "warn"))

    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "counts": {"episodes": len(eps), "arcs": len(arcs),
                       "foreshadows": len(u["foreshadows"]), "hooks": len(u["hooks"]),
                       "characters": len(u["characters"]), "scenes": len(u["scenes"]),
                       "props": len(u["props"])}}


# ────────────────────────── 锚定 ──────────────────────────

def save_threads(proj, data):
    """写 剧本/埋线.json（覆写前快照，走通用版本层）。"""
    doc = dict(_read(path_threads(proj), {}))
    doc.update(data or {})
    doc.setdefault("units_version", UNITS_VERSION)
    _write(path_threads(proj), doc)
    return path_threads(proj)


def anchor(proj, force=False):
    """体检 -> 盖 anchor_rev。未过体检默认拒绝（--force 放行但把缺项记进 anchored_with）。"""
    report = check(proj)
    if not report["ok"] and not force:
        return {"ok": False, "report": report}
    stamp = {"anchored_at": time_now(), "anchored_with": {"errors": len(report["errors"]),
                                                          "warnings": len(report["warnings"])}}
    touched = []
    outline = _read(path_outline(proj), {})
    if not outline:
        return {"ok": False, "report": report, "reason": "缺少 剧本/大纲.json，先跑第一步生成"}
    outline["anchor_rev"] = int(outline.get("anchor_rev") or 0) + 1
    outline["units_version"] = UNITS_VERSION
    outline.update(stamp)
    _write(path_outline(proj), outline)
    touched.append(path_outline(proj))

    eps_doc = _read(path_episodes(proj), {})
    if eps_doc.get("episodes") is not None:
        eps_doc["anchor_rev"] = outline["anchor_rev"]
        _write(path_episodes(proj), eps_doc)
        touched.append(path_episodes(proj))

    threads = _read(path_threads(proj), {})
    if threads:
        threads["anchor_rev"] = outline["anchor_rev"]
        _write(path_threads(proj), threads)
        touched.append(path_threads(proj))

    for name in ("人物", "场景", "道具"):
        path, key, rows = _read_asset(proj, name)
        if not rows or not os.path.isfile(path):
            continue
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        doc["anchor_rev"] = outline["anchor_rev"]
        _write(path, doc)
        touched.append(path)
    return {"ok": True, "anchor_rev": outline["anchor_rev"], "report": report, "files": touched}


def unanchor(proj):
    """撤锚：抹掉各包 anchor_rev，扩写与 ② 的注入点随即退回改造前行为。"""
    removed = []
    for path in (path_outline(proj), path_episodes(proj), path_threads(proj)):
        doc = _read(path, {})
        if isinstance(doc, dict) and doc and doc.get("anchor_rev") is not None:
            doc["anchor_rev"] = 0
            _write(path, doc)
            removed.append(path)
    for name in ("人物", "场景", "道具"):
        path, key, rows = _read_asset(proj, name)
        if not os.path.isfile(path):
            continue
        with open(path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
        if isinstance(doc, dict) and doc.get("anchor_rev") is not None:
            doc["anchor_rev"] = 0
            _write(path, doc)
            removed.append(path)
    return {"ok": True, "files": removed}


def time_now():
    import datetime
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ────────────────────────── 扩写注入块（第二步） ──────────────────────────

def _fmt_list(items):
    return "\n".join(f"- {x}" for x in items if str(x or "").strip())


def units_block(proj, ep_id):
    """本集扩写的硬输入块；未锚定返回空串（调用方据此完全不改变旧行为）。"""
    if not is_anchored(proj):
        return ""
    u = load_units(proj)
    ep = next((e for e in u["episodes"] if str(e.get("id")) == str(ep_id)), None)
    if ep is None:
        return ""
    idx = build_index(proj)
    blocks = []

    outline = u["outline"]
    rules = [f"{r.get('text', '')}" for r in (outline.get("rules") or []) if isinstance(r, dict)]
    taboos = [f"{t.get('rule', '')}" for t in (outline.get("taboos") or []) if isinstance(t, dict)]
    if rules:
        blocks.append("【全剧规则（能力边界，不得违背）】\n" + _fmt_list(rules))
    if taboos:
        blocks.append("【创作禁区】\n" + _fmt_list(taboos))

    arc_id = str(ep.get("arc_id") or "")
    arc = next((a for a in (outline.get("arcs") or []) if isinstance(a, dict) and str(a.get("id")) == arc_id), None)
    if arc:
        blocks.append("【本段阶段目标】%s：%s\n【本段释放的信息】%s" % (
            arc.get("id"), arc.get("goal", ""), "；".join(str(x) for x in (arc.get("release") or []))))

    beats = ep.get("beats") or []
    if beats:
        blocks.append("【本集节拍】" + " -> ".join(str(b) for b in beats))

    cast = []
    for ref in (ep.get("cast_refs") or []):
        c = next((x for x in u["characters"] if f"@character:{x.get('id')}" == str(ref)), None)
        if not c:
            continue
        lines = [f"{c.get('name')}（{ref}）"]
        for k, label in (("bio_arc", "弧光"), ("bio_language", "语言风格"), ("bio_crack", "说话的破绽"),
                         ("bio_pressure", "被逼急时怎么做"), ("bio_address", "称呼规则")):
            if str(c.get(k) or "").strip():
                lines.append(f"  {label}：{c[k]}")
        cast.append("\n".join(lines))
    if cast:
        blocks.append("【出场人物锚定】\n" + "\n".join(cast))

    scenes = []
    for ref in (ep.get("scene_refs") or []):
        s = next((x for x in u["scenes"] if f"@scene:{x.get('id')}" == str(ref)), None)
        if not s:
            continue
        lines = [f"{s.get('name')}（{ref}）"]
        if str(s.get("spatial_limit") or "").strip():
            lines.append(f"  空间对行动的限制：{s['spatial_limit']}")
        if s.get("action_slots"):
            lines.append("  可复用动作位置：" + "；".join(str(x) for x in s["action_slots"]))
        scenes.append("\n".join(lines))
    if scenes:
        blocks.append("【场景锚定】\n" + "\n".join(scenes))

    props = []
    for ref in (ep.get("key_asset_refs") or []):
        p = next((x for x in u["props"] if f"@prop:{x.get('id')}" == str(ref)), None)
        if not p:
            continue
        lines = [f"{p.get('name')}（{ref}）"]
        if str(p.get("usage_boundary") or "").strip():
            lines.append(f"  使用边界：{p['usage_boundary']}")
        props.append("\n".join(lines))
    if props:
        blocks.append("【关键道具锚定】\n" + "\n".join(props))

    plant = [f for f in u["foreshadows"] if str(f.get("set_in")) == str(ep_id)]
    pay = [f for f in u["foreshadows"] if str(f.get("pay_in")) == str(ep_id)]
    if plant:
        blocks.append("【本集要埋的线】\n" + _fmt_list(
            [f"{f.get('id')}：{f.get('plant')}（形态：{f.get('form') or '未定'}；收在 {f.get('pay_in')}）" for f in plant]))
    if pay:
        blocks.append("【本集要收的线】\n" + _fmt_list(
            [f"{f.get('id')}：{f.get('payoff') or f.get('plant')}（埋在 {f.get('set_in')}）" for f in pay]))
    unclaimed = [f for f in u["foreshadows"]
                 if str(f.get("pay_in") or "") and _ep_sort_key(f.get("pay_in")) >= _ep_sort_key(ep_id)
                 and _ep_sort_key(f.get("set_in")) <= _ep_sort_key(ep_id)
                 and str(f.get("set_in")) != str(ep_id) and str(f.get("pay_in")) != str(ep_id)
                 and str(f.get("status") or "open") != "paid"]
    if unclaimed:
        blocks.append("【已埋未收（别丢）】" + "、".join(
            f"{f.get('id')}→{f.get('pay_in')}" for f in unclaimed[:12]))

    derive = ep.get("state_derive") or []
    if derive:
        blocks.append("【本集形象/立场变化】\n" + _fmt_list(
            [f"{d.get('ref') or d.get('owner_ref')}：{d.get('label') or d.get('state_id')}"
             f"（{d.get('look_diff') or ''}）" for d in derive if isinstance(d, dict)]))
    if ep.get("relation_shift"):
        blocks.append("【本集关系变化】\n" + _fmt_list(ep.get("relation_shift")))

    whitelist = sorted(set(_refs_of_episode(ep)) | {f.get("ref") for f in derive if isinstance(f, dict)})
    if whitelist:
        blocks.append("【可引用素材白名单（只准用这些，缺就停下上报）】\n" + "、".join(whitelist))
    return "\n\n".join(blocks)


# ────────────────────────── 缺口上报（引用白名单闸） ──────────────────────────

_SCENE_RE = re.compile(r"^【场景[:：]\s*([^】]+?)】")
_SPEAK_RE = re.compile(r"^\s*([^\s：/【】（）()]{1,12})\s*[:：]")


def gap_report(proj, ep_id, text=None):
    """用锚定白名单校验该集正文的「场名」与「说话人」，返回缺口清单（不阻断，交调用方决定）。

    只吃扩写格式强制产生的两类结构（【场景：地点／日或夜】与行首「角色名：」），
    不做专名猜测——宁漏不误报，误报会让人工回补变成噪音。
    """
    if not is_anchored(proj):
        return {"enabled": False, "missing_scenes": [], "missing_speakers": [], "ep_id": str(ep_id or "")}
    u = load_units(proj)
    if text is None:
        ep = next((e for e in u["episodes"] if str(e.get("id")) == str(ep_id)), None)
        text = str((ep or {}).get("text") or "")
    scene_names = set()
    for s in u["scenes"]:
        for alias in [s.get("name")] + list(s.get("aliases") or []):
            norm = _norm_name(alias)
            if norm:
                scene_names.add(norm)
    char_names = set()
    for c in u["characters"]:
        for alias in [c.get("name")] + list(c.get("aliases") or []):
            norm = _norm_name(alias)
            if norm:
                char_names.add(norm)
    missing_scenes, missing_speakers, seen = [], [], set()
    for line in (text or "").splitlines():
        m = _SCENE_RE.match(line.strip())
        if m:
            raw = m.group(1)
            head = _norm_name(re.split(r"[:：]", raw)[0])
            loc = _norm_name(head)
            if loc and loc not in scene_names and loc not in seen:
                seen.add(loc)
                missing_scenes.append({"name": loc, "as_written": raw.strip()})
            continue
        m = _SPEAK_RE.match(line)
        if not m:
            continue
        who = _norm_name(m.group(1))
        if not who or is_narrator(who) or who in char_names or who in seen:
            continue
        seen.add(who)
        if "：" in line or ":" in line:
            missing_speakers.append({"name": who, "line": line.strip()[:60]})
    return {"enabled": True, "ep_id": str(ep_id or ""),
            "missing_scenes": missing_scenes, "missing_speakers": missing_speakers,
            "blocking": bool(missing_scenes or missing_speakers)}


# ────────────────────────── 写入（第一步产物落盘） ──────────────────────────

def _slug(value, fallback="item"):
    """实体 id：小写 ascii 连字符；中文名走拼音不可得时退回稳定哈希，保证同一名字每次同一 id。"""
    text = str(value or "").strip()
    ascii_slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if ascii_slug:
        return ascii_slug[:56]
    try:
        from script_repository import stable_id
        return stable_id(text, prefix=fallback)
    except Exception:
        import hashlib
        return f"{fallback}-{hashlib.md5(text.encode('utf-8')).hexdigest()[:8]}"


def _name_keys(row):
    keys = set()
    for alias in [row.get("name")] + list(row.get("aliases") or []):
        norm = _norm_name(alias)
        if norm:
            keys.add(norm)
    return keys


def _same_family(a, b):
    """两个名字算不算"同一个东西的不同写法"：完全相等，或互为前后缀且短的那个够长。

    只用于 id 已经撞车时的兜底判定（`猎仙使` vs `白袍猎仙使`）——
    纯包含关系不当建新档的依据（`青芜山山脚` 与 `青芜山山脚黑河滩` 可能是两处），
    那种交给体检 DUP_ASSET 报出来让人并档。
    """
    a, b = str(a or ""), str(b or "")
    if not a or not b:
        return False
    if a == b:
        return True
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= 3 and (long.endswith(short) or long.startswith(short))


def merge_roster(proj, roster):
    """名册 -> 素材三件套骨架档案。同名资产沿用既有 id（防 N93 那类换 id 造成的索引孤儿）。

    返回 remap：LLM 提议的 ref -> 档案里的真实 ref。后续所有引用必须过一遍它——
    不然"名册里叫 hero、档案里叫 old_hero"就会造出一堆悬空引用。
    """
    roster = roster or {}
    created, reused, remap = [], [], {}
    plan = [("人物", "characters", "character", {"one_line": "basis"}),
            ("场景", "scenes", "scene", {"one_line": "layout_note"}),
            ("道具", "props", "prop", {"one_line": "shot_hint"})]
    for name, list_key, kind, field_map in plan:
        rows_in = [r for r in (roster.get(list_key) or []) if isinstance(r, dict) and str(r.get("name") or "").strip()]
        if not rows_in:
            continue
        path, key, existing = _read_asset(proj, name)
        by_name = {}
        by_id = {}
        for row in existing:
            for nk in _name_keys(row):
                by_name.setdefault(nk, row)
            if row.get("id"):
                by_id[str(row["id"])] = row
        changed = False
        for item in rows_in:
            nk = _norm_name(item.get("name"))
            new_id = _slug(item.get("id") or item.get("name"), fallback=kind)
            proposed = f"@{kind}:{new_id}"
            # 同名先认（改名不换 id 是底线），其次同 id 也算同物（LLM 复用 id 但改了译名）
            hit = by_name.get(nk) or by_id.get(new_id)
            if hit is not None:
                # 撞 id 且名字同族＝同一物的另一种叫法：把叫法登记成别名，绝不另起一条
                aliases = [str(a) for a in (hit.get("aliases") or []) if str(a).strip()]
                call = str(item.get("name") or "").strip()
                if call and call != str(hit.get("name") or "") and call not in aliases:
                    aliases.append(call)
                    hit["aliases"] = aliases
                    changed = True
                actual = f"@{kind}:{hit.get('id')}"
                reused.append(actual)
                remap[proposed] = actual
                by_name.setdefault(nk, hit)
                continue
            new_row = {"id": proposed.split(":", 1)[1],
                       "name": str(item["name"]).strip(),
                       "basis": str(item.get("one_line") or "").strip() or "第一步锚定名册",
                       "source": "story_units"}
            if kind == "character":
                new_row.update({"role": item.get("role") or "次要", "gender": item.get("gender") or "不明",
                                "is_collective": bool(item.get("is_collective")), "states": []})
            elif kind == "scene":
                new_row.update({"interior": bool(item.get("interior")), "time": item.get("time") or "日"})
            else:
                new_row.update({"kind": item.get("kind") or "叙事", "asset_required": True})
            for src, dst in field_map.items():
                if str(item.get(src) or "").strip():
                    new_row[dst] = str(item[src]).strip()
            existing.append(new_row)
            by_name.setdefault(nk, new_row)
            by_id[new_id] = new_row          # 同批内第二条撞 id 也必须走复用，不能再 append 一条同 id
            created.append(proposed)
            remap[proposed] = proposed
            changed = True
        if changed:
            doc = _read(path, {})
            doc[key] = existing
            _write(path, doc)
    return {"created": created, "reused": reused, "remap": remap}


def _remap_refs(value, remap):
    """把 LLM 提议的 ref 换成档案里的真实 ref（只改这一处，全链引用才不会分叉）。"""
    if isinstance(value, str):
        return remap.get(value.strip(), value)
    if isinstance(value, list):
        return [_remap_refs(v, remap) for v in value]
    if isinstance(value, dict):
        return {k: _remap_refs(v, remap) for k, v in value.items()}
    return value


def _remap_stored_refs(proj, remap):
    """把此前各批已落盘的分集/埋线里的旧引用一起换正——分批生成时前批写的是旧 id。"""
    if not remap or all(k == v for k, v in remap.items()):
        return 0
    touched = 0
    eps_doc = _read(path_episodes(proj), {})
    rows = [e for e in (eps_doc.get("episodes") or []) if isinstance(e, dict)]
    for row in rows:
        before = json.dumps(row, ensure_ascii=False, sort_keys=True)
        for key in ("cast_refs", "scene_refs", "key_asset_refs", "state_derive"):
            if row.get(key):
                row[key] = _remap_refs(row[key], remap)
        for rel in (row.get("relations") or []):
            if isinstance(rel, dict):
                _remap_refs(rel, remap)
        if json.dumps(row, ensure_ascii=False, sort_keys=True) != before:
            touched += 1
    if touched:
        eps_doc["episodes"] = rows
        _write(path_episodes(proj), eps_doc)
    threads = _read(path_threads(proj), {})
    fs_rows = [f for f in (threads.get("foreshadows") or []) if isinstance(f, dict)]
    fs_changed = False
    for fs in fs_rows:
        before = json.dumps(fs, ensure_ascii=False, sort_keys=True)
        if fs.get("refs"):
            fs["refs"] = _remap_refs(fs["refs"], remap)
        if json.dumps(fs, ensure_ascii=False, sort_keys=True) != before:
            fs_changed = True
    if fs_changed:
        threads["foreshadows"] = fs_rows
        _write(path_threads(proj), threads)
    return touched


def _merge_by_id(existing, incoming, key_name="id"):
    """按 id 并批合并列表：后一批只能补/改自己那条，不能把别批的整段抹掉。

    并批生成（每段一次 LLM）时，若用整键覆写，最后一批只回它那一 段，
    前面的分段/规则/禁区全没了，分集里的 arc_id 立刻指向不存在的分段。
    """
    out = [r for r in (existing or []) if isinstance(r, dict)]
    index = {str(r.get(key_name)): i for i, r in enumerate(out) if r.get(key_name)}
    for row in (incoming or []):
        if not isinstance(row, dict):
            continue
        rid = str(row.get(key_name) or "")
        if rid and rid in index:
            slot = index[rid]
            merged = dict(out[slot])
            merged.update(row)
            for k, v in list(merged.items()):        # 后批留空不该擦掉前批的有效值
                if v is None or v == "" or v == []:
                    merged[k] = out[slot].get(k)
            out[slot] = merged
        else:
            out.append(row)
            if rid:
                index[rid] = len(out) - 1
    return out


def apply_story(proj, data):
    """U1 产物落盘：名册建档 -> 引用转正 -> 大纲加厚键 -> 分集加厚条目（不碰正文）-> 埋线.json。"""
    data = data or {}
    roster_out = merge_roster(proj, data.get("roster"))
    remap = roster_out.get("remap") or {}
    _remap_stored_refs(proj, remap)
    for ep in (data.get("episodes") or []):
        if not isinstance(ep, dict):
            continue
        for key in ("cast_refs", "scene_refs", "key_asset_refs", "state_derive"):
            if ep.get(key):
                ep[key] = _remap_refs(ep[key], remap)
    for fs in (data.get("foreshadows") or []):
        if isinstance(fs, dict) and fs.get("refs"):
            fs["refs"] = _remap_refs(fs["refs"], remap)

    outline = _read(path_outline(proj), {})
    for k in OUTLINE_KEYS:
        if not data.get(k):
            continue
        if k in ("arcs", "rules", "taboos", "sources"):
            outline[k] = _merge_by_id(outline.get(k), data[k])
        else:
            outline[k] = data[k]
    outline.setdefault("units_version", UNITS_VERSION)
    _write(path_outline(proj), outline)

    eps_doc = _read(path_episodes(proj), {})
    existing = [e for e in (eps_doc.get("episodes") or []) if isinstance(e, dict)]
    by_id = {str(e.get("id")): e for e in existing}
    for ep in (data.get("episodes") or []):
        if not isinstance(ep, dict) or not str(ep.get("id") or ""):
            continue
        eid = str(ep["id"])
        row = by_id.get(eid)
        if row is None:
            row = {"id": eid}
            by_id[eid] = row
            existing.append(row)
        for k, v in ep.items():
            if k == "text":
                continue          # 正文是第二步的产物，第一步永不覆写
            if v is None or v == "" or v == []:
                continue
            row[k] = v
    eps_doc["episodes"] = existing
    eps_doc["units_version"] = UNITS_VERSION
    _write(path_episodes(proj), eps_doc)

    threads = _read(path_threads(proj), {})
    merged_fs = {str(f.get("id")): f for f in (threads.get("foreshadows") or []) if isinstance(f, dict)}
    for fs in (data.get("foreshadows") or []):
        if not isinstance(fs, dict) or not str(fs.get("id") or ""):
            continue
        fid = str(fs["id"])
        old = merged_fs.get(fid)
        if old and str(old.get("status") or "") == "paid":
            fs["status"] = "paid"          # 人工标记的已回收不被生成流程改回 open
        merged_fs[fid] = {**(old or {}), **fs}
    threads["foreshadows"] = list(merged_fs.values())
    merged_hk = {str(h.get("id")): h for h in (threads.get("hooks") or []) if isinstance(h, dict)}
    for hk in (data.get("hooks") or []):
        if isinstance(hk, dict) and str(hk.get("id") or ""):
            merged_hk[str(hk["id"])] = {**(merged_hk.get(str(hk["id"])) or {}), **hk}
    threads["hooks"] = list(merged_hk.values())
    threads.setdefault("units_version", UNITS_VERSION)
    _write(path_threads(proj), threads)
    return roster_out


def apply_entities(proj, data):
    """U2 产物落盘：传记五件套 / 场景限制与动作位 / 道具边界 / 关系 / 状态派生。

    只认 ref 命中名册的条目；命不中的进 gaps 回第一步，绝不静默新建实体。
    """
    data = data or {}
    applied, gaps = [], [g for g in (data.get("gaps") or []) if isinstance(g, dict)]

    def find(rows, ref):
        ref = str(ref or "").strip()
        for row in rows:
            if f"@{row.get('_kind')}:{row.get('id')}" == ref:
                return row
        return None

    docs = {}
    for name, key, kind in (("人物", "characters", "character"), ("场景", "scenes", "scene"), ("道具", "props", "prop")):
        path, doc_key, rows = _read_asset(proj, name)
        for row in rows:
            row["_kind"] = kind
        docs[name] = (path, doc_key, rows)
    index = {"character": docs["人物"][2], "scene": docs["场景"][2], "prop": docs["道具"][2]}

    for item in (data.get("characters") or []):
        row = find(index["character"], item.get("ref"))
        if row is None:
            gaps.append({"kind": "character", "ref": item.get("ref"), "need": "名册里没有这个人物，设定无处可写"})
            continue
        for k in CHAR_KEYS:
            if item.get(k):
                row[k] = item[k]
        applied.append(row.get("id"))
    for item in (data.get("scenes") or []):
        row = find(index["scene"], item.get("ref"))
        if row is None:
            gaps.append({"kind": "scene", "ref": item.get("ref"), "need": "名册里没有这个场景"})
            continue
        for k in SCENE_KEYS:
            if item.get(k):
                row[k] = item[k]
        applied.append(row.get("id"))
    for item in (data.get("props") or []):
        row = find(index["prop"], item.get("ref"))
        if row is None:
            gaps.append({"kind": "prop", "ref": item.get("ref"), "need": "名册里没有这个道具"})
            continue
        if item.get("usage_boundary"):
            row["usage_boundary"] = item["usage_boundary"]
        applied.append(row.get("id"))

    # 状态派生：写进人物的 states[]（episodes 用集号，与 ② 的集号/场名混填口径兼容）
    derive = data.get("state_derive") or []
    by_char = {}
    for d in derive:
        if isinstance(d, dict) and str(d.get("ref") or ""):
            by_char.setdefault(str(d["ref"]), []).append(d)
    for ref, rows in by_char.items():
        row = find(index["character"], ref)
        if row is None:
            gaps.append({"kind": "character", "ref": ref, "need": "状态派生指向不存在的人物"})
            continue
        states = [s for s in (row.get("states") or []) if isinstance(s, dict)]
        for d in rows:
            sid = str(d.get("state_id") or f"{row.get('id')}_S{len(states) + 1}")
            hit = next((s for s in states if str(s.get("id")) == sid), None)
            payload = {"id": sid}
            if d.get("label"):
                payload["label"] = str(d["label"])
            if d.get("look_diff"):
                payload["look_diff"] = str(d["look_diff"])
            if d.get("camp"):
                payload["camp"] = str(d["camp"])
            ep = str(d.get("ep") or "")
            if hit is None:
                hit = payload
                hit["episodes"] = [ep] if ep else []
                states.append(hit)
            else:
                hit.update(payload)
                if ep and ep not in (hit.get("episodes") or []):
                    hit.setdefault("episodes", []).append(ep)
        row["states"] = states

    for name, (path, doc_key, rows) in docs.items():
        cleaned = []
        for row in rows:
            row.pop("_kind", None)      # 临时索引键不写回档案
            cleaned.append(row)
        doc = _read(path, {})
        doc[doc_key] = cleaned
        _write(path, doc)
    return {"applied": len(set(applied)), "gaps": gaps}


def open_threads(proj, ep_id=None):
    """已埋未收的线（供分段并批时告诉 LLM 手上还欠哪些收点）。"""
    u = load_units(proj)
    key = _ep_sort_key(ep_id) if ep_id else 0
    return [f for f in u["foreshadows"]
            if _ep_sort_key(f.get("set_in")) <= key or not ep_id]


def roster_catalog(proj):
    """给 U1 的既有资产目录（"ref | 名称（别名）"）——不喂它就会让 LLM 给同一人物另起 id。"""
    u = load_units(proj)
    rows = []
    for kind, key in (("character", "characters"), ("scene", "scenes"), ("prop", "props")):
        for row in u[key]:
            if not row.get("id"):
                continue
            aliases = [a for a in (row.get("aliases") or []) if str(a).strip()]
            name = str(row.get("name") or "")
            if not name and not aliases:
                continue
            rows.append(f"@{kind}:{row['id']} | {name or '(无名)'}" +
                        (f"（也叫：{'、'.join(map(str, aliases[:4]))}）" if aliases else ""))
    return sorted(rows)


# ────────────────────────── ② 投影约束（素材提炼只补外观/生图字段） ──────────────────────────

AUTHORITY_NOTE = """

【已锚定设定（① 第一步权威）——本步职责是投影，不是发现】
1. 下列设定事实（性别/身份锚点/弧光/语言风格与称呼/道具何时不生效/场景对行动的限制/既定状态计划）
   以本块为准，**禁止新增或改写**；原文与本块冲突时按本块写，并在 basis 注明"原文与锚定设定存在矛盾"。
2. 你只负责补外观与生图字段：appearance / voice / lens / sheet_prompt / image_prompt / geometry /
   layout / states 的 look_diff 与该状态的五视图提示词。
3. 本块里没有、而原文确实需要的新实体或新设定：照旧输出该条目，但在其 basis 里以 "GAP:" 前缀写清缺什么，
   交回 ① 补名册——不要当成自己的发挥空间。
{payload}
"""


def _prune(row):
    """去掉空值：给 LLM 一堆 null 只会诱导它照抄 null，也白烧 token。"""
    return {k: v for k, v in row.items()
            if v not in (None, "", [], {}) and str(v).strip() != "None"}


def authority_payload(proj, name):
    """② 提炼用：该资产类的锚定设定切片（只给设定事实，不给外观长描述）。"""
    u = load_units(proj)
    out = []
    if name == "人物":
        for c in u["characters"]:
            row = {"ref": f"@character:{c.get('id')}", "name": c.get("name"), "gender": c.get("gender"),
                   "identity_anchor": c.get("identity_anchor")}
            for k in CHAR_KEYS:
                if str(c.get(k) or "").strip():
                    row[k] = c.get(k)
            if c.get("states"):
                row["states_planned"] = [_prune({"id": s.get("id"), "label": s.get("label"),
                                                 "episodes": s.get("episodes") or [],
                                                 "look_diff": s.get("look_diff")})
                                         for s in c["states"] if isinstance(s, dict)]
            out.append(_prune(row))
    elif name == "场景":
        for s in u["scenes"]:
            out.append(_prune({"ref": f"@scene:{s.get('id')}", "name": s.get("name"),
                               "spatial_limit": s.get("spatial_limit"),
                               "action_slots": s.get("action_slots") or [],
                               "time": s.get("time"), "interior": s.get("interior")}))
    elif name == "道具":
        for p in u["props"]:
            out.append(_prune({"ref": f"@prop:{p.get('id')}", "name": p.get("name"),
                               "usage_boundary": p.get("usage_boundary"), "kind": p.get("kind")}))
    return out


def authority_note(proj, name):
    """② 提炼的投影约束块；未锚定/无设定返回空串（老项目行为逐字不变）。"""
    if not is_anchored(proj):
        return ""
    rows = authority_payload(proj, name)
    if not rows:
        return ""
    return AUTHORITY_NOTE.replace("{payload}", json.dumps(rows, ensure_ascii=False)[:24000])


# ────────────────────────── 人工修订（① 卡行内编辑） ──────────────────────────

EPISODE_EDIT_KEYS = ("title", "summary", "hook", "cliff", "duration_min", "arc_id", "beats",
                     "fs_plant", "fs_pay", "cast_refs", "scene_refs", "key_asset_refs",
                     "state_derive", "relation_shift")
ASSET_EDIT_KEYS = CHAR_KEYS + SCENE_KEYS + PROP_KEYS + ("identity_anchor", "gender", "role", "usage", "basis")
OUTLINE_EDIT_KEYS = ("premise", "highlights", "sources", "rules", "taboos", "pressure",
                     "arcs", "throughline", "causality", "main_line", "genre", "visual_style")


def _bump_script_rev(proj):
    """分集层被人工改动也要让下游知道——沿用 creation_pipeline._dump_episodes 这个唯一写入口，
    不在这里另起一套 rev 逻辑（否则 vN 过期判定会出现两个写入方）。"""
    try:
        import creation_pipeline
        eps_doc = _read(path_episodes(proj), {})
        creation_pipeline._dump_episodes(path_episodes(proj), eps_doc)
    except Exception as exc:
        print(f"[rev 未更新] {exc}", flush=True)


def edit_episode(proj, ep_id, fields):
    fields = fields or {}
    rejected = [k for k in fields if k not in EPISODE_EDIT_KEYS]
    eps_doc = _read(path_episodes(proj), {})
    rows = [e for e in (eps_doc.get("episodes") or []) if isinstance(e, dict)]
    row = next((e for e in rows if str(e.get("id")) == str(ep_id)), None)
    if row is None:
        return {"ok": False, "err": f"分集 {ep_id} 不存在", "rejected": rejected}
    applied = {}
    for k, v in fields.items():
        if k in EPISODE_EDIT_KEYS:
            row[k] = v
            applied[k] = v
    eps_doc["episodes"] = rows
    _write(path_episodes(proj), eps_doc)
    _bump_script_rev(proj)
    return {"ok": True, "applied": list(applied), "rejected": rejected,
            "note": "正文 text 不在这里改：它由第二步扩写产出" }


def edit_asset(proj, zone, ident, fields, lock=None):
    """素材设定行内改；带 lock 的字段写进 locked_fields，之后任何生成流程都不得覆盖。"""
    if zone not in ("人物", "场景", "道具"):
        return {"ok": False, "err": "zone 须为 人物|场景|道具"}
    path, key, rows = _read_asset(proj, zone)
    row = next((r for r in rows if str(r.get("id")) == str(ident)), None)
    if row is None:
        return {"ok": False, "err": f"{zone} {ident} 不存在"}
    fields = fields or {}
    applied, rejected = [], []
    for k, v in fields.items():
        if k in ASSET_EDIT_KEYS:
            row[k] = v
            applied.append(k)
        else:
            rejected.append(k)
    locked = {str(x) for x in (row.get("locked_fields") or [])}
    locked |= {str(x) for x in (lock or [])} | {k for k in applied if (lock or [])}
    if locked:
        row["locked_fields"] = sorted(locked)
    doc = _read(path, {})
    doc[key] = rows
    _write(path, doc)
    return {"ok": True, "applied": applied, "rejected": rejected, "locked_fields": row.get("locked_fields") or []}


def edit_outline(proj, fields):
    fields = fields or {}
    doc = _read(path_outline(proj), {})
    applied = []
    for k, v in fields.items():
        if k in OUTLINE_EDIT_KEYS:
            doc[k] = v
            applied.append(k)
    doc.setdefault("units_version", UNITS_VERSION)
    _write(path_outline(proj), doc)
    return {"ok": True, "applied": applied,
            "rejected": [k for k in fields if k not in OUTLINE_EDIT_KEYS]}


def edit_threads(proj, foreshadows=None, hooks=None):
    """埋线/钩子表整体替换（覆写前快照）；只接数组，避免半条记录造成配对分叉。"""
    doc = _read(path_threads(proj), {})
    if isinstance(foreshadows, list):
        doc["foreshadows"] = [f for f in foreshadows if isinstance(f, dict)]
    if isinstance(hooks, list):
        doc["hooks"] = [h for h in hooks if isinstance(h, dict)]
    doc.setdefault("units_version", UNITS_VERSION)
    _write(path_threads(proj), doc)
    rep = check(proj)
    return {"ok": True, "report": {"errors": rep["errors"], "warnings": rep["warnings"]}}


SHOT_TEXT_FIELDS = ("content", "action", "sound", "lighting", "prompt_image", "prompt_video", "prompt_grid")


def _shot_texts(shot):
    texts = []
    for key in SHOT_TEXT_FIELDS:
        val = shot.get(key)
        if isinstance(val, str) and val.strip():
            texts.append((key, val))
    for line in (shot.get("lines") or []):
        # dialogue 契约的台词键是 line（at/dur/speaker/line）；不扫 negative——那是禁令清单，命中即误报。
        if isinstance(line, dict) and str(line.get("line") or "").strip():
            texts.append(("lines", str(line.get("line"))))
    if isinstance(shot.get("prompt"), str) and shot["prompt"].strip():
        texts.append(("prompt", shot["prompt"]))
    return texts


def taboo_scan(proj, shots):
    """③/⑤ 的负约束闸：命中锚定 taboos[].detect 的镜头只告警不阻断。

    不阻断的理由：分镜是 LLM 产物，block 会把正常创作变成抽奖；口径与 validate_analysis
    对受控词表的处理一致（同一仓库既有政策）。未锚定或无 detect 词 → 返回空列表，行为不变。
    """
    if not is_anchored(proj):
        return []
    taboos = [t for t in (load_units(proj)["outline"].get("taboos") or [])
              if isinstance(t, dict) and (t.get("detect") or [])]
    if not taboos:
        return []
    out = []
    for shot in (shots or []):
        if not isinstance(shot, dict):
            continue
        pairs = _shot_texts(shot)
        for t in taboos:
            hits = []
            for field, text in pairs:
                for word in (t.get("detect") or []):
                    w = str(word or "").strip()
                    if w and w in text:
                        hits.append({"field": field, "word": w, "snippet": text.strip()[:80]})
            if hits:
                out.append({"code": "TABOO", "severity": str(t.get("level") or "warn"),
                            "shot_id": str(shot.get("id") or ""), "taboo_id": str(t.get("id") or ""),
                            "rule": str(t.get("rule") or ""), "hits": hits[:5]})
    return out


# ────────────────────────── 数据修复（确定性，不碰 LLM） ──────────────────────────

def sync_thread_claims(proj):
    """把埋线表的 set_in/pay_in 回填成分集里的 fs_plant/fs_pay（双向对账的另一半）。

    并批生成时分集条目与伏笔表来自不同批，常见"表说埋在 E3、E3 自己不认领"；
    这是纯机械认领，不需要 LLM，也不改任何创作内容。
    """
    u = load_units(proj)
    eps = [e for e in (u["episodes"] or []) if isinstance(e, dict)]
    by_id = {str(e.get("id")): e for e in eps}
    touched, dropped = 0, 0
    for key, which in (("fs_plant", "set_in"), ("fs_pay", "pay_in")):
        for fs in u["foreshadows"]:
            fid, ep = str(fs.get("id") or ""), str(fs.get(which) or "")
            if not fid or ep not in by_id:
                continue
            row = by_id[ep]
            have = [str(x) for x in (row.get(key) or [])]
            if fid not in have:
                row[key] = have + [fid]
                touched += 1
    for e in eps:  # 集里认领了表上已经没有的线 → 摘掉
        declared = {str(f.get("id")) for f in u["foreshadows"]}
        for key in ("fs_plant", "fs_pay"):
            rows = [str(x) for x in (e.get(key) or [])]
            keep = [x for x in rows if x in declared]
            if len(keep) != len(rows):
                e[key] = keep
                dropped += len(rows) - len(keep)
                touched += 1
    if touched:
        doc = _read(path_episodes(proj), {})
        doc["episodes"] = eps
        _write(path_episodes(proj), doc)
    return {"claimed": touched, "dropped": dropped}


def collapse_duplicate_ids(proj, apply_changes=False):
    """同 id 多条 → 并成一条（保留先出现的那条的字段，用后面的条目补空字段）。

    只并在 id 完全相同的情况（引用双方指向同一个键，并了不会改指错人）；
    名字像但 id 不同的（DUP_ASSET 那种）留给人工判断，程序不猜。
    """
    report = {}
    for name, key, kind in (("人物", "characters", "character"), ("场景", "scenes", "scene"), ("道具", "props", "prop")):
        path, doc_key, rows = _read_asset(proj, name)
        keep, merged, index = [], [], {}
        for row in rows:
            rid = str(row.get("id") or "")
            if rid and rid in index:
                target = keep[index[rid]]
                # 被并掉那条的名字不能蒸发——它是别名，正文/台词里还会那样叫
                lost = str(row.get("name") or "").strip()
                aliases = [str(a) for a in (target.get("aliases") or []) if str(a).strip()]
                if lost and lost != str(target.get("name") or "") and lost not in aliases:
                    aliases.append(lost)
                    target["aliases"] = aliases
                for k, v in row.items():
                    if v in (None, "", [], {}) or k in ("id", "locked_fields", "name", "aliases"):
                        continue
                    if target.get(k) in (None, "", [], {}):
                        target[k] = v
                merged.append(f"{kind}:{rid}←「{row.get('name')}」")
                continue
            if rid:
                index[rid] = len(keep)
            keep.append(row)
        report[name] = {"before": len(rows), "after": len(keep), "merged": merged}
        if merged and apply_changes:
            doc = _read(path, {})
            doc[doc_key] = keep
            _write(path, doc)
    return report


def fill_refs_from_text(proj, ep_id=None, apply_changes=False):
    """从已有正文反推引用，把 cast_refs/scene_refs/key_asset_refs 填上（不覆写已有的）。

    为什么需要：U1 分批出加厚条目时，cast_refs 只覆盖到 LLM 当批想到的那几个，
    09_仙 实测 111 个实体里 77 个"没人引用"——不是实体多余，是引用没填全，
    于是反查索引与影响面全是瞎的。正文里「角色名：」与【场景：X／…】是现成的真凭据。
    """
    u = load_units(proj)
    char_by, scene_by, prop_by = {}, {}, {}
    for row in u["characters"]:
        for alias in [row.get("name")] + list(row.get("aliases") or []):
            if _norm_name(alias):
                char_by[_norm_name(alias)] = f"@character:{row.get('id')}"
    for row in u["scenes"]:
        for alias in [row.get("name")] + list(row.get("aliases") or []):
            if _norm_name(alias):
                scene_by[_norm_name(alias)] = f"@scene:{row.get('id')}"
    for row in u["props"]:
        for alias in [row.get("name")] + list(row.get("aliases") or []):
            if _norm_name(alias):
                prop_by[_norm_name(alias)] = f"@prop:{row.get('id')}"
    def _lookup(table, value):
        """先精确命中，再退一步按"同一个东西的不同叫法"命中（芝靖军师≈芝靖、白袍猎仙使≈猎仙使）。

        这里比建档判定宽松（短到 2 字也认）：正文常只写「什长」「老仙」，而档案登记成
        「梁军什长」「被俘老仙」——引用挂不上，反查索引与影响面就会漏掉这些集。
        建档去重不走这条宽松口径（那里误并的代价更大）。
        """
        hit = table.get(value)
        if hit or not value:
            return hit
        for key, ref in table.items():
            if not key:
                continue
            if value.endswith(key) or value.startswith(key) or key.endswith(value) or key.startswith(value):
                if min(len(key), len(value)) >= 2:
                    return ref
        return None

    touched, rows_out = [], []
    for ep in u["episodes"]:
        eid = str(ep.get("id") or "")
        if ep_id and eid != str(ep_id):
            continue
        text = str(ep.get("text") or "")
        if not text.strip():
            continue
        found_scenes, found_chars, found_props = [], [], []
        for line in text.splitlines():
            m = _SCENE_RE.match(line.strip())
            if m:
                head = _norm_name(re.split(r"[:：]", m.group(1))[0])
                ref = _lookup(scene_by, head) or _lookup(scene_by, _norm_name(m.group(1)))
                if ref and ref not in found_scenes:
                    found_scenes.append(ref)
                continue
            # 道具在正文里是"被动作提及"，不是说话人也不是场名——必须扫每一行
            # （09_仙 实跑：玄黑猎仙旗/九字铁牌/猎仙仙曹录 全在动作行里，却因只扫台词行而成了孤儿建档）
            for token, pref in prop_by.items():
                if len(token) >= 3 and token in line and pref not in found_props:
                    found_props.append(pref)
            m = _SPEAK_RE.match(line)
            if not m:
                continue
            who = _norm_name(m.group(1))
            if not who or is_narrator(who):
                continue
            ref = _lookup(char_by, who)
            if ref and ref not in found_chars:
                found_chars.append(ref)
        add = {"cast_refs": found_chars, "scene_refs": found_scenes, "key_asset_refs": found_props}
        changed = {}
        for key, refs in add.items():
            have = [str(r) for r in (ep.get(key) or [])]
            new = [r for r in refs if r not in have]
            if new:
                changed[key] = new
        if changed:
            touched.append({"id": eid, **{k: v for k, v in changed.items()}})
            if apply_changes:
                for k, v in changed.items():
                    ep[k] = [str(r) for r in (ep.get(k) or [])] + v
        rows_out.append(ep)
    if apply_changes and touched:
        doc = _read(path_episodes(proj), {})
        doc["episodes"] = rows_out
        _write(path_episodes(proj), doc)
    return {"episodes": touched, "applied": bool(apply_changes and touched)}


def missing_fields(kind, row):
    """某实体还缺哪些设定字段（U2 分批与 ① 卡都按这个口径判断"填齐没有"）。"""
    if kind == "character":
        return [k for k in CHAR_KEYS[:5] if not str(row.get(k) or "").strip()]
    if kind == "scene":
        out = []
        if not str(row.get("spatial_limit") or "").strip():
            out.append("spatial_limit")
        if not (row.get("action_slots") or []):
            out.append("action_slots")
        return out
    return [] if str(row.get("usage_boundary") or "").strip() else ["usage_boundary"]


def pending_settings(proj, per_round=24):
    """还缺设定层的实体（人物看传记五件套、场景看空间限制与动作位、道具看使用边界）。

    U2 一轮写不完 111 个实体是必然的——这个函数就是让 cmd_units 能分批续跑的依据。
    """
    u = load_units(proj)
    flat = []
    for kind, key in (("character", "characters"), ("scene", "scenes"), ("prop", "props")):
        zone = {"character": "人物", "scene": "场景", "prop": "道具"}[kind]
        for row in u[key]:
            gaps = missing_fields(kind, row)
            if gaps:
                flat.append({"ref": f"@{kind}:{row.get('id')}", "kind": zone, "id": str(row.get("id")),
                             "name": row.get("name"), "missing": gaps, "_kind": kind})
    batch = {"characters": [], "scenes": [], "props": []}
    key_map = {"character": "characters", "scene": "scenes", "prop": "props"}
    rows_by = {
        "character": {str(r.get("id")): r for r in u["characters"]},
        "scene": {str(r.get("id")): r for r in u["scenes"]},
        "prop": {str(r.get("id")): r for r in u["props"]},
    }
    for item in flat[:per_round]:
        batch[key_map[item["_kind"]]].append(rows_by[item["_kind"]][item["id"]])
    return {"remaining": len(flat), "batch": batch, "targets": [{k: v for k, v in t.items() if k != "_kind"}
                                                               for t in flat[:per_round]]}


def unreferenced_new(proj):
    """第一步新建、但没被任何集/伏笔/state_derive 引用的实体＝过度建档，可删（默认只报告）。"""
    u = load_units(proj)
    referenced = set()
    for e in u["episodes"]:
        referenced |= {str(r) for r in _refs_of_episode(e)}
        for sd in (e.get("state_derive") or []):
            if isinstance(sd, dict):
                referenced.add(str(sd.get("ref") or sd.get("owner_ref")))
    for fs in u["foreshadows"]:
        referenced |= {str(r) for r in (fs.get("refs") or [])}
    out = []
    for name, key, kind, rows in (("人物", "characters", "character", u["characters"]),
                                  ("场景", "scenes", "scene", u["scenes"]),
                                  ("道具", "props", "prop", u["props"])):
        for row in rows:
            if row.get("source") != "story_units" or not row.get("id"):
                continue
            if f"@{kind}:{row['id']}" in referenced:
                continue
            out.append({"zone": name, "key": key, "id": str(row["id"]), "name": row.get("name")})
    return out


def prune_unreferenced_new(proj, apply_changes=False, ids=None):
    """删掉过度建档的新实体（只删 source=story_units 的，既有档案一律不碰）。"""
    victims = unreferenced_new(proj)
    if ids:
        wanted = set(ids)
        victims = [v for v in victims if v["id"] in wanted]
    if apply_changes and victims:
        for zone in ("人物", "场景", "道具"):
            path, key, rows = _read_asset(proj, zone)
            drop = {v["id"] for v in victims if v["zone"] == zone}
            if not drop:
                continue
            doc = _read(path, {})
            doc[key] = [r for r in rows if str(r.get("id")) not in drop]
            _write(path, doc)
    return {"removed": len(victims) if apply_changes else 0, "candidates": victims}


# ────────────────────────── CLI ──────────────────────────

def cmd_show(proj):
    u = load_units(proj)
    outline = u["outline"]
    print(f"锚定：{'v' + str(anchor_rev(proj)) if is_anchored(proj) else '未锚定（注入点不生效）'}")
    print("大纲加厚：" + ("、".join(k for k in OUTLINE_KEYS if outline.get(k)) or "无"))
    print(f"分集 {len(u['episodes'])} 集；加厚已填：" +
          ("、".join(sorted({k for e in u["episodes"] for k in EPISODE_KEYS if e.get(k)})) or "无"))
    print(f"埋线：伏笔 {len(u['foreshadows'])} 条 / 钩子 {len(u['hooks'])} 条")
    print(f"人物 {len(u['characters'])}（传记已填 "
          f"{sum(1 for c in u['characters'] if any(str(c.get(k) or '').strip() for k in CHAR_KEYS[:5]))}）"
          f" / 场景 {len(u['scenes'])}（限制已填 "
          f"{sum(1 for s in u['scenes'] if str(s.get('spatial_limit') or '').strip())}）"
          f" / 道具 {len(u['props'])}（边界已填 "
          f"{sum(1 for p in u['props'] if str(p.get('usage_boundary') or '').strip())}）")
    return u


def main():
    ap = argparse.ArgumentParser(description="剧本最小单元：锚定 · 体检 · 反查索引")
    ap.add_argument("cmd", choices=["show", "check", "anchor", "unanchor", "index", "gaps", "block",
                                    "sync", "dedupe", "fillrefs", "prune"])
    ap.add_argument("project")
    ap.add_argument("--apply", dest="do_apply", action="store_true",
                    help="dedupe：真的并档写盘（缺省只报告，不碰数据）")
    ap.add_argument("--episode", default=None, help="gaps/block：集 id（如 E1）")
    ap.add_argument("--ref", default=None, help="index：只看某个 @kind:id")
    ap.add_argument("--force", action="store_true", help="anchor：体检有阻断项也强行盖锚（缺项记入 anchored_with）")
    ap.add_argument("--ids", default=None, help="prune：只处理这些 id（逗号分隔），缺省处理全部候选")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    a = ap.parse_args()
    proj = os.path.abspath(a.project)
    if not os.path.isdir(proj):
        print(f"[错误] 项目不存在: {proj}"); sys.exit(1)

    if a.cmd == "show":
        cmd_show(proj)
    elif a.cmd == "check":
        rep = check(proj)
        if a.json:
            print(json.dumps(rep, ensure_ascii=False, indent=1))
        else:
            print(f"[体检] {'通过' if rep['ok'] else '有阻断项'} "
                  f"（集 {rep['counts']['episodes']}／段 {rep['counts']['arcs']}／伏笔 {rep['counts']['foreshadows']}"
                  f"／钩子 {rep['counts']['hooks']}／人物 {rep['counts']['characters']}"
                  f"／场景 {rep['counts']['scenes']}／道具 {rep['counts']['props']}）")
            for it in rep["errors"]:
                print(f"  ✗ {it['code']} {it['path']}：{it['message']}")
            for it in rep["warnings"]:
                print(f"  ! {it['code']} {it['path']}：{it['message']}")
        sys.exit(0 if rep["ok"] else 1)
    elif a.cmd == "anchor":
        res = anchor(proj, force=a.force)
        if not res.get("ok"):
            print("[拒绝锚定] 体检未通过，先修下列各项（或 --force 放行）：")
            for it in (res.get("report") or {}).get("errors", []):
                print(f"  ✗ {it['code']} {it['path']}：{it['message']}")
            if res.get("reason"):
                print(f"  [原因] {res['reason']}")
            sys.exit(1)
        print(f"[已锚定] anchor_rev=v{res['anchor_rev']}，覆写前已快照 {len(res['files'])} 个文件")
        for it in (res.get("report") or {}).get("warnings", []):
            print(f"  ! {it['code']} {it['path']}：{it['message']}")
    elif a.cmd == "unanchor":
        res = unanchor(proj)
        print(f"[已撤锚] {len(res['files'])} 个文件回到未锚定；扩写与素材提炼退回原行为")
    elif a.cmd == "sync":
        print(json.dumps(sync_thread_claims(proj), ensure_ascii=False))
    elif a.cmd == "dedupe":
        res = collapse_duplicate_ids(proj, apply_changes=a.do_apply)
        for zone, info in res.items():
            print(f"{zone}: {info['before']} → {info['after']} 条"
                  + ("；已并：" + "、".join(info["merged"]) if info["merged"] else "；无同 id 重复"))
        if not a.do_apply and any(i["merged"] for i in res.values()):
            print("[dry-run] 未写盘。确认无误后加 --apply（覆写前自动快照到 .versions）")
    elif a.cmd == "fillrefs":
        res = fill_refs_from_text(proj, ep_id=a.episode, apply_changes=a.do_apply)
        for row in res["episodes"]:
            print(f"{row['id']}: " + "；".join(f"{k}+{len(v)}" for k, v in row.items() if k != "id"))
        if not res["episodes"]:
            print("引用已填全（或没有可比对的正文）")
        elif not a.do_apply:
            print("[dry-run] 未写盘。确认后加 --apply")
    elif a.cmd == "prune":
        ids = [s.strip() for s in str(a.ids or "").split(",") if s.strip()] or None
        res = prune_unreferenced_new(proj, apply_changes=a.do_apply, ids=ids)
        print(f"候选 {len(res['candidates'])} 个，" + (f"已删 {res['removed']} 个" if a.do_apply else "未删（dry-run）"))
        for o in res["candidates"][:20]:
            print(f"  - {o['zone']}:{o['id']}「{o['name']}」")
        if res["candidates"] and not a.do_apply:
            print("[dry-run] 加 --apply 才写盘（只删 source=story_units 的新建条目，既有档案一律不碰）")
    elif a.cmd == "index":
        idx = build_index(proj)
        if a.ref:
            row = idx.get(a.ref)
            print(json.dumps(row or {"ref": a.ref, "found": False}, ensure_ascii=False, indent=1))
        else:
            print(json.dumps(list(idx.values()), ensure_ascii=False, indent=1))
    elif a.cmd == "gaps":
        if not a.episode:
            print("[错误] gaps 需要 --episode"); sys.exit(1)
        rep = gap_report(proj, a.episode)
        print(json.dumps(rep, ensure_ascii=False, indent=1))
        sys.exit(2 if rep.get("blocking") else 0)
    elif a.cmd == "block":
        if not a.episode:
            print("[错误] block 需要 --episode"); sys.exit(1)
        txt = units_block(proj, a.episode)
        print(txt or "（未锚定或无该集：注入块为空）")


if __name__ == "__main__":
    main()
