# -*- coding: utf-8 -*-
"""剧本创作管线：本地剧本 → LLM 分集 → 人物/场景/道具 → 分镜(dialogue契约) → 创作包

数据模型（全部落在既有项目结构下，复用白模/3D/预演包链路）：
  projects/<项目>/剧本/剧本.txt          本地剧本文本（用户导入）
  projects/<项目>/剧本/分集.json         {"episodes":[{id,title,start,end,hook,cliff,summary,...}]}
  projects/<项目>/素材/人物.json         {"characters":[...]}（兼作白模 actors 源）
  projects/<项目>/素材/场景.json         {"scenes":[...]}
  projects/<项目>/素材/道具.json         {"props":[...]}
  projects/<项目>/分镜/剧本_<集id>.json  dialogue 契约分镜（可直接渲染白模/3D/env）

子命令:
  episodes   全剧本 -> 分集（LLM，输出锚点在原文中的位置）
  expand     创作构想 -> 剧集大纲(=分集.json，从0生成)；--episode 指定集再扩写为分场剧本原文
  extract    指定集(或全本) -> 人物+场景+道具（三次专职提示词调用）
  storyboard 指定集 -> 分镜 JSON（知识注入+受控词表，直接可渲染）
  assemble   分镜 -> 创作包（推演/创作包_<分镜名>/manifest.json：逐镜脚本/平面图/白模参考/用户素材）
公共: 429/超时指数退避重试（5/10/20/40s），配合 server 看门狗（每30分钟巡检卡死任务）。
"""
import sys, os, json, re, time, argparse, glob
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
CORE_TOOLS = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "previs_system", "tools"))
if CORE_TOOLS not in sys.path:
    sys.path.insert(0, CORE_TOOLS)
from llm_openai import VendorClient, load_vendors, VendorError
import prompt_modules as PM

# 旁白/画外音是叙述轨不是角色：统一归一为保留说话人 id "narrator"，
# 不进 actors/actor_refs/asset_refs，不参与站位与生图引用。
from narration import NARRATOR_ALIASES, is_narrator
import skill_lib
import script_repository
import project_store
from llm_result import parse_structured
from validate_dialogue import validate_document

RETRYABLE = ("429", "超时", "timeout", "Timeout", "连接失败", "请求异常", "HTTP 5", "rate")


FAST_THINK = {"thinking": {"type": "disabled"}}   # 结构化 JSON 任务关思考：doubao-seed 系提速数倍
STORYBOARD_MAX_TOKENS = 24000  # 三类提示词与 V 分组同轮输出，避免截断


def chat_retry(cli, messages, max_tokens=4000, timeout=420, tries=4, extra=None):
    """429/超时指数退避；全部失败抛 VendorError。extra 失败（HTTP 4xx 参数拒绝）自动去参兜底。"""
    wait = 5
    use_extra = extra
    for i in range(tries):
        try:
            return cli.chat(messages, kind="text", max_tokens=max_tokens, timeout=timeout,
                            temperature=0.4, extra=use_extra)
        except VendorError as e:
            msg = str(e)
            if use_extra and msg.startswith("HTTP 4"):
                use_extra = None   # 厂商不认 thinking 参数：去掉后原样重试
                continue
            if i == tries - 1 or not any(w.lower() in msg.lower() for w in RETRYABLE):
                raise
            print(f"[重试] {msg[:80]} -> {wait}s 后第 {i+2} 次", flush=True)
            time.sleep(wait)
            wait = min(60, wait * 2)


def parse_json(txt):
    """解析完整的 JSON 对象；拒绝截断结果和非对象数据。"""
    # 统一结构化解析器会区分可修复尾逗号和真正截断；不完整结果不能进入正式产物。
    parsed = parse_structured(txt)
    if not parsed.get("complete"):
        raise ValueError("LLM 输出 JSON 不完整: " + "; ".join(parsed.get("repair_notes") or []))
    if not isinstance(parsed.get("data"), dict):
        raise ValueError("LLM 输出 JSON 顶层必须是对象")
    return parsed["data"]


def pick_vendor(vendor_id=None):
    vs = [v for v in load_vendors() if v.get("enabled") and (v.get("models") or {}).get("text")]
    if vendor_id:
        vs = [v for v in vs if v["id"] == vendor_id]
    vs.sort(key=lambda v: not v.get("api_key"))
    if not vs:
        raise VendorError("没有已启用且配置 text 模型的厂商")
    return vs[0]["id"]


def script_path(proj):
    return os.path.join(proj, "剧本", "剧本.txt")


def anchor_locate(script, anchor, fallback):
    """把 LLM 给的原文锚点定位为字符偏移（找不到则顺序回退）。"""
    if anchor and anchor in script:
        return script.index(anchor)
    if fallback and fallback in script:
        return script.index(fallback)
    return -1


def cmd_expand(proj, vendor, idea, eps_n, episode):
    """创作构想 → 剧集大纲（写 分集.json，schema 与 episodes 一致，后续提取/分镜无缝衔接）；
    --episode E1 时再扩写该集为分场剧本，写 剧本/分集剧本_E1.txt 并并入 剧本.txt 尾部（或建库）。"""
    base = os.path.join(proj, "剧本")
    cli = VendorClient(pick_vendor(vendor))
    idea_path = os.path.join(base, "构想.txt")
    if idea:
        os.makedirs(base, exist_ok=True)
        open(idea_path, "w", encoding="utf-8").write(idea)
    elif os.path.isfile(idea_path):
        idea = open(idea_path, encoding="utf-8").read()
    if not idea or len(idea.strip()) < 5:
        print("[错误] 缺少创作构想（--idea 一段话 或 剧本/构想.txt）"); sys.exit(1)
    outline_path = os.path.join(base, "大纲.json")
    prev_main = None
    if os.path.isfile(outline_path):
        try:
            prev_main = json.load(open(outline_path, encoding="utf-8")).get("main_line")
        except Exception:
            pass
    if prev_main:
        idea = idea + "[既有主线（保持一致）]" + prev_main
    # 指定单集时复用既有大纲，避免重新生成并清空已扩写集。
    if episode and os.path.isfile(outline_path):
        try:
            data = json.load(open(outline_path, encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"既有大纲无法读取：{exc}")
    else:
        sys_p, user_p = PM.outline_prompt(idea, eps_n=eps_n, style_text=skill_lib.style_for(proj, "script"))
        txt = chat_retry(cli, [{"role": "system", "content": sys_p}, {"role": "user", "content": user_p}],
                         max_tokens=12000, timeout=420, extra=FAST_THINK)
        data = parse_json(txt)
    eps = data.get("episodes") or []
    if not eps:
        print("[错误] 大纲无 episodes: " + str(locals().get("txt", ""))[:300]); sys.exit(1)
    old_ep_data = {}
    old_ep_path = os.path.join(base, "分集.json")
    if os.path.isfile(old_ep_path):
        try:
            old_ep_data = {str(x.get("id")): x for x in json.load(open(old_ep_path, encoding="utf-8")).get("episodes", [])}
        except (OSError, ValueError):
            old_ep_data = {}
    for i, e in enumerate(eps, 1):
        e.setdefault("id", f"E{i}")
        e["char_start"] = 0; e["char_end"] = 0   # 从0生成：无原文锚点，text 由扩写填充
        if str(e["id"]) in old_ep_data:
            e["text"] = old_ep_data[str(e["id"])].get("text") or ""
    data["idea"] = idea
    data["prompt_version"] = PM.PROMPT_VERSION
    _dump(outline_path, data)
    # 画风不再自动落 style.json：visual_style 只留在大纲里作参考，
    # 视觉画风统一到资产提炼页手选（故事链路不携带视觉画风）。
    print(f"[画风] 大纲 visual_style「{data.get('visual_style') or '(未给)'}」仅存档参考；生图风格请到资产提炼页选择")
    print(f"[完成] 大纲 {len(eps)} 集 -> {outline_path}")
    print(f"  主线: {data.get('main_line','')}")
    for e in eps:
        print(f"  {e['id']} {e.get('title','')} ({e.get('duration_min')}min) {e.get('summary','')[:36]}")
    # 同步 分集.json（带 text 字段的兼容视图；未扩写集 text 为空）
    for e in eps:
        e.setdefault("text", "")
    epjson = os.path.join(base, "分集.json")
    if episode is None:
        _dump_episodes(epjson, {"episodes": eps, "prompt_version": PM.PROMPT_VERSION, "mode": "generated"})
        print("OUTPUT:" + epjson)
        return
    # 扩写指定集
    tgt = next((e for e in eps if e["id"] == episode), None)
    if tgt is None:
        print(f"[错误] 大纲中没有 {episode}"); sys.exit(1)
    idx = eps.index(tgt)
    prev_s = eps[idx-1].get("summary") if idx > 0 else None
    sys_p, user_p = PM.expand_episode_prompt(idea, tgt, prev_s, style_text=skill_lib.style_for(proj, "script"))
    script_txt = chat_retry(cli, [{"role": "system", "content": sys_p}, {"role": "user", "content": user_p}],
                            max_tokens=6000).strip()
    tgt["text"] = script_txt
    tgt["char_start"] = 0; tgt["char_end"] = len(script_txt)
    _dump_episodes(epjson, {"episodes": eps, "prompt_version": PM.PROMPT_VERSION, "mode": "generated"})
    ep_txt = os.path.join(base, f"分集剧本_{episode}.txt")
    import versions as _V
    _V.snapshot(ep_txt)   # 覆写前快照旧稿，保留可回滚历史
    open(ep_txt, "w", encoding="utf-8").write(script_txt)
    # 若 剧本.txt 不存在（纯创作项目）则以扩写文本建库；存在则不动（用户可能混编）
    if not os.path.isfile(script_path(proj)):
        open(script_path(proj), "w", encoding="utf-8").write(script_txt)
        print("[信息] 已写入 剧本/剧本.txt（原库为空，供台词/白模链路使用）")
    print(f"[完成] {episode} 扩写 {len(script_txt)} 字 -> {ep_txt}")
    print("OUTPUT:" + ep_txt)


def full_script_text(proj):
    """剧本正文权威源：generated 模式=按集序聚合分集正文；imported/无标记=剧本.txt。"""
    ej = os.path.join(proj, "剧本", "分集.json")
    if os.path.isfile(ej):
        try:
            d = json.load(open(ej, encoding="utf-8"))
            if d.get("mode") == "generated":
                parts = [(e.get("id", ""), e.get("text") or "") for e in d.get("episodes") or []]
                parts = [(i, t) for i, t in parts if t.strip()]
                if parts:
                    return "\n\n".join("【%s】\n%s" % (i, t) for i, t in parts)
        except Exception:
            pass
    sp = script_path(proj)
    return open(sp, encoding="utf-8").read() if os.path.isfile(sp) else ""


def merge_episode_overview(episode, overview):
    """把概要字段合并到分集对象，明确保留原有剧本文本。"""
    if not isinstance(episode, dict) or not isinstance(overview, dict):
        return episode
    for key in ("summary", "hook", "cliff"):
        value = str(overview.get(key) or "").strip()
        if value:
            episode[key] = value
    for key in ("cast_refs", "scene_refs", "key_asset_refs"):
        value = overview.get(key)
        if isinstance(value, list):
            episode[key] = [str(item).strip() for item in value if str(item).strip()]
    return episode


def _overview_refs(proj, overview):
    """只保留项目资产库中存在的分集引用，避免概要生成虚构资产。"""
    try:
        from asset_registry import AssetRegistry
        registry = AssetRegistry(proj)
        valid = {row.get("ref") for row in registry.list() if row.get("ref")}
    except Exception:
        valid = set()
    out = dict(overview or {})
    for key in ("cast_refs", "scene_refs", "key_asset_refs"):
        values = out.get(key)
        if not isinstance(values, list):
            out[key] = []
            continue
        out[key] = [str(value).strip() for value in values if str(value).strip() in valid]
    return out


def cmd_overview(proj, vendor, ep_id=None):
    """生成分集影评式概要和资产索引，不修改分集正文。"""
    ep_path = os.path.join(proj, "剧本", "分集.json")
    if not os.path.isfile(ep_path):
        print("[错误] 缺少 剧本/分集.json（先分集或从0生成大纲）"); sys.exit(1)
    data = json.load(open(ep_path, encoding="utf-8"))
    episodes = data.get("episodes") or []
    targets = [e for e in episodes if not ep_id or str(e.get("id")) == str(ep_id)]
    if ep_id and not targets:
        print(f"[错误] 分集 {ep_id} 不存在"); sys.exit(1)
    cli = VendorClient(pick_vendor(vendor))
    st = skill_lib.project_style(proj)
    style_text = str(st.get("anchor") or "")
    try:
        from asset_registry import AssetRegistry
        catalog = "\n".join(f"{row['ref']} {row.get('name','')}" for row in AssetRegistry(proj).list() if row.get("kind") != "style")
    except Exception:
        catalog = ""
    for entry in targets:
        text = str(entry.get("text") or "").strip()
        if not text:
            print(f"[跳过] {entry.get('id')} 无分集正文"); continue
        sys_p, user_p = PM.episode_overview_prompt(text, entry, style_text=style_text)
        user_p += "\n[项目可用资产引用（只能从中选择）]\n" + catalog
        result = parse_json(chat_retry(cli, [{"role": "system", "content": sys_p}, {"role": "user", "content": user_p}], max_tokens=12000, timeout=420, extra=FAST_THINK))
        merge_episode_overview(entry, _overview_refs(proj, result))
        print(f"[完成] {entry.get('id')} 概要已更新", flush=True)
    data["prompt_version"] = PM.PROMPT_VERSION
    _dump_episodes(ep_path, data)
    print(f"OUTPUT:{ep_path}")


def cmd_episodes(proj, vendor, out):
    script = full_script_text(proj)
    if len(script.strip()) < 80:
        print(f"[错误] 剧本正文不足（generated=聚合分集正文 / imported=剧本.txt）"); sys.exit(1)
    if len(script) < 80:
        print("[错误] 剧本过短（<80 字）"); sys.exit(1)
    cli = VendorClient(pick_vendor(vendor))
    sys_p, user_p = PM.episodes_prompt(script)
    txt = chat_retry(cli, [{"role": "system", "content": sys_p}, {"role": "user", "content": user_p}],
                     max_tokens=12000, timeout=420, extra=FAST_THINK)
    data = parse_json(txt)
    eps = data.get("episodes") or []
    # 锚点 -> 字符偏移（顺序容错）
    cursor = 0
    for e in eps:
        i = anchor_locate(script[cursor:], e.get("start"), None)
        e["char_start"] = cursor + i if i >= 0 else cursor
        j = anchor_locate(script[e["char_start"]:], e.get("end"), None)
        e["char_end"] = e["char_start"] + j + len(str(e.get("end") or "")) if j >= 0 else len(script)
        cursor = max(cursor, e["char_start"])
        e["text"] = script[e["char_start"]:e["char_end"]]
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    _dump_episodes(out, {"episodes": eps, "prompt_version": PM.PROMPT_VERSION})
    print(f"[完成] 分集 {len(eps)} 集 -> {out}")
    for e in eps:
        print(f"  {e.get('id')} {e.get('title')} ({e.get('duration_min')}min) {e.get('summary','')[:40]}")
    print("OUTPUT:" + out)


def _episode_text(proj, ep_id):
    if ep_id:
        d = json.load(open(os.path.join(proj, "剧本", "分集.json"), encoding="utf-8"))
        if not any(str(e.get("id")) == str(ep_id) for e in d.get("episodes") or []):
            print(f"[错误] 分集 {ep_id} 不存在"); sys.exit(1)
    return script_repository.load_script(proj, ep_id)


def _dump(path, data):
    import versions as _V
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    def replace(current):
        current.clear(); current.update(data)
    project_store.update_json(path, replace, create_default={}, snapshot=_V.snapshot)
    print("OUTPUT:" + path)


def _rev_of(path):
    """读 JSON 文件的 rev 修订号（缺省 0）。"""
    try:
        return int(json.load(open(path, encoding="utf-8")).get("rev") or 0)
    except Exception:
        return 0


def _dump_episodes(path, data):
    """写 分集.json：每次覆写自动 bump rev 修订号，供分镜/资产追溯对齐。"""
    data = dict(data)
    data["rev"] = _rev_of(path) + 1
    _dump(path, data)


def _asset_catalog(data):
    """给提炼 LLM 的轻量全局目录；不把人物/场景长设定塞回提示词。"""
    catalog = {"characters": [], "scenes": [], "props": []}
    if not isinstance(data, dict):
        return catalog
    for key in catalog:
        rows = data.get(key) or []
        if isinstance(rows, dict):
            rows = [dict(value, id=rid) for rid, value in rows.items() if isinstance(value, dict)]
        for item in rows if isinstance(rows, list) else []:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            compact = {"id": item.get("id"), "name": item.get("name"),
                       "aliases": item.get("aliases") or []}
            if key == "props":
                compact.update({"kind": item.get("kind"), "parent_ref": item.get("parent_ref"),
                                "relation": item.get("relation"), "derived_from": item.get("derived_from")})
            elif key == "scenes":
                compact.update({"time": item.get("time"), "interior": item.get("interior")})
            else:
                compact["role"] = item.get("role")
            catalog[key].append(compact)
    return catalog


def _canon_asset_value(value):
    return "".join(ch.lower() for ch in str(value or "") if ch.isalnum())


def _resolve_catalog_ref(value, catalog, default_kind=None):
    """把 LLM 偶尔输出的名称/别名解析成目录中的稳定 @ 引用。"""
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.startswith("@") and ":" in raw:
        head, ident = raw[1:].split(":", 1)
        kind = {"人物": "character", "场景": "scene", "道具": "prop"}.get(head, head)
        if kind in ("character", "scene", "prop"):
            raw = ident
            default_kind = kind
    groups = (("characters", "character"), ("scenes", "scene"), ("props", "prop"))
    needle = _canon_asset_value(raw)
    if not needle:
        return ""
    matches = []
    for key, kind in groups:
        if default_kind and kind != default_kind:
            continue
        for item in catalog.get(key) or []:
            values = [item.get("id"), item.get("name")]
            aliases = item.get("aliases") or []
            values.extend([aliases] if isinstance(aliases, str) else aliases)
            if any(needle == _canon_asset_value(value) for value in values if value):
                matches.append(f"@{kind}:{item.get('id')}")
    return matches[0] if len(set(matches)) == 1 else ""


def _evidence_index(text):
    return {row["id"]: row["text"] for row in PM.evidence_rows(text)}


def _evidence_ids(item, evidence):
    values = item.get("evidence_ids") or item.get("evidence_refs") or []
    if isinstance(values, str):
        values = re.split(r"[,，、;；\s]+", values)
    out = []
    for value in values if isinstance(values, (list, tuple, set)) else []:
        ident = str(value or "").strip().upper()
        if ident in evidence and ident not in out:
            out.append(ident)
    # 兼容模型只返回 evidence_text 的情况，但仍要求该文本能在证据索引中定位。
    text = str(item.get("evidence_text") or item.get("evidence") or "").strip()
    if not out and text:
        compact = "".join(text.split())
        for ident, source in evidence.items():
            if compact and compact in "".join(str(source).split()):
                out.append(ident)
                break
    return out


def _gate_extracted_data(name, data, text, catalog):
    """在写入项目资产前做证据门控和关系语义修正。

    LLM 负责从原文找候选；这里拒绝无原文证据的候选，并把 owner（动作发起者）
    与 parent_ref（身份继承）分开。这样偶发的“女主使用复合物→挂到女主下面”
    不会进入正式资产表。
    """
    key = {"人物": "characters", "场景": "scenes", "道具": "props"}.get(name)
    if not key or not isinstance(data, dict):
        return data, []
    evidence = _evidence_index(text)
    accepted, rejected = [], []
    for raw in data.get(key) or []:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        ids = _evidence_ids(item, evidence)
        if not ids:
            rejected.append(f"{item.get('name') or item.get('id') or '未命名'}：缺少可回溯原文证据")
            continue
        item["evidence_ids"] = ids
        item["evidence_status"] = "verified"
        if key == "props":
            kind = str(item.get("kind") or "叙事").strip()
            derived = _resolve_catalog_ref(item.get("derived_from"), catalog, "prop")
            parent = _resolve_catalog_ref(item.get("parent_ref"), catalog)
            if derived:
                # 由道具派生的复合物永远挂到真实道具，制造者只保留 owner。
                item["derived_from"] = derived
                item["parent_ref"] = derived
                item["relation"] = "derived_from"
                if kind == "叙事":
                    item["kind"] = "组件"
            elif parent:
                item["parent_ref"] = parent
            # “叙事道具 + 角色 parent”是最常见的 owner/parent 混淆，清掉错误层级；
            # 后续 normalize 会保留真正的 owner 或将其判为独立叙事道具。
            if str(item.get("kind") or kind).strip() == "叙事" and str(item.get("parent_ref") or "").startswith("@character:"):
                item.pop("parent_ref", None)
                item.pop("relation", None)
        accepted.append(item)
    out = dict(data)
    out[key] = accepted
    return out, rejected


def reconcile_characters(proj, cli, full_text):
    """合并后的跨集字段校准：读 人物.json → 单次 LLM 补 gender/anchor/states → 按并集合并回写。"""
    import script_repository
    path = os.path.join(proj, "素材", "人物.json")
    if not os.path.isfile(path):
        return 0
    doc = json.load(open(path, encoding="utf-8"))
    roster = doc.get("characters") or []
    if not roster:
        return 0
    sys_p, user_p = PM.character_reconcile_prompt(roster, full_text)
    txt = chat_retry(cli, [{"role": "system", "content": sys_p},
                           {"role": "user", "content": user_p}],
                     max_tokens=12000, timeout=420, extra=FAST_THINK)
    rows = (parse_json(txt).get("characters")) or []
    by_id = {str(r.get("id")): r for r in rows if isinstance(r, dict) and r.get("id")}
    n = 0
    for c in roster:
        fix = by_id.get(str(c.get("id")))
        if not fix:
            continue
        changed = False
        for k in ("gender", "identity_anchor"):
            v = fix.get(k)
            if v and str(v).strip() and str(v) != str(c.get(k) or ""):
                c[k] = str(v).strip(); changed = True
        if fix.get("states") is not None:
            merged = script_repository._merge_states(c.get("states"), fix.get("states"))
            if merged != (c.get("states") or []):
                c["states"] = merged; changed = True
        n += 1 if changed else 0
    # 本地自愈：顶层 sheet_prompt 与性别锚点矛盾时按 gender 修正称谓（不调 LLM）
    from gen_asset_images import _gender_guard
    for c in roster:
        sp = str(c.get("sheet_prompt") or "")
        if sp:
            fixed, _ = _gender_guard(c, sp.replace("性别不明", {"男": "男性", "女": "女性"}.get(str(c.get("gender")), "")))
            if fixed != sp:
                c["sheet_prompt"] = fixed
    _dump(path, doc)
    print(f"[完成] 角色跨集校准：{n}/{len(roster)} 人更新（gender/锚点/状态轨）")
    return n


def cmd_extract(proj, vendor, ep_id):
    ep_json_path = os.path.join(proj, "剧本", "分集.json")
    if not os.path.isfile(ep_json_path):
        print("[错误] 缺少 剧本/分集.json：资产提炼必须关联已有剧本分集（先到①剧本分集页分集或生成大纲）"); sys.exit(1)
    try:
        ep_doc = json.load(open(ep_json_path, encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"[错误] 剧本/分集.json 无法读取：{exc}"); sys.exit(1)
    if not (ep_doc.get("episodes") or []):
        print("[错误] 剧本/分集.json 没有任何分集：先到①剧本分集页分集或生成大纲"); sys.exit(1)
    script_rev = int(ep_doc.get("rev") or 0)
    text = _episode_text(proj, ep_id)
    if not text:
        print("[错误] 无剧本文本（先 episodes 或导入剧本）"); sys.exit(1)
    cli = VendorClient(pick_vendor(vendor))
    base = os.path.join(proj, "素材")
    paths = {"人物": os.path.join(base, "人物.json"),
             "场景": os.path.join(base, "场景.json"),
             "道具": os.path.join(base, "道具.json")}
    keys = {"人物": "characters", "场景": "scenes", "道具": "props"}
    documents = {}
    combined = {"characters": [], "scenes": [], "props": []}
    for name, out in paths.items():
        try:
            raw = json.load(open(out, encoding="utf-8")) if os.path.isfile(out) else {}
        except (OSError, ValueError) as exc:
            raise ValueError(f"既有{name}资产无法读取：{exc}")
        documents[name] = raw if isinstance(raw, dict) else {}
        rows = documents[name].get(keys[name]) or []
        combined[keys[name]] = rows if isinstance(rows, list) else []
    # 先把历史档案归一化，再把目录交给 LLM。否则旧数据中“derived_from=道具、
    # parent_ref=角色”的错误关系会继续成为模型的错误示范，下一次提炼会复制它。
    relation_mod = getattr(script_repository, "asset_relations", None)
    if relation_mod is not None:
        combined, relation_issues = relation_mod.normalize_asset_relations(combined)
        if relation_issues:
            print(f"[关系预处理] 已修正/清理 {len(relation_issues)} 条历史引用", flush=True)
    # 只给人物提炼传轻量既有档案；完整目录会在每次调用前按当前 combined 重建。
    known = _asset_catalog(combined).get("characters") or None
    st = skill_lib.project_style(proj)
    # 画风唯一来源 = 生图风格 skill；未选（自动）则不注入画风指令。
    sel_img = str(st.get("image") or "").strip()
    style_text = (skill_lib.style_for(proj, "image") if sel_img else "").strip()
    if style_text:
        print(f"[画风] 提炼使用: 生图风格 {sel_img}")
    # 依次提炼：人物和场景先进入当前目录，道具再读取这两类真实 @ 引用，
    # 避免三次独立调用各自臆造“女主/教室/炸弹”的父级。
    for name in ("人物", "场景", "道具"):
        out = paths[name]
        catalog = _asset_catalog(combined)
        if name == "人物":
            sys_p, user_p = PM.characters_prompt(text, known, style_text=style_text, catalog=catalog)
        elif name == "场景":
            sys_p, user_p = PM.scenes_prompt(text, style_text=style_text, catalog=catalog)
        else:
            sys_p, user_p = PM.props_prompt(text, style_text=style_text, catalog=catalog)
        txt = chat_retry(cli, [{"role": "system", "content": sys_p}, {"role": "user", "content": user_p}],
                         max_tokens=12000, timeout=420, extra=FAST_THINK)
        try:
            data = parse_json(txt)
        except ValueError:
            # 截断兜底：收紧字段长度重试一次
            hint = (
                "注意：只输出完整合法 JSON，不要 markdown 或解释。"
                "每条记录必须有能在原文证据索引中定位的 evidence_ids；父级只能从项目已有 @ 引用目录选择；"
                + ("道具数组最多 12 条；每个对象字段必须精简；只保留明确出场且有动作/交接/破坏/使用/特写的关键资产；"
                   "红色眼眸、头发、白色双马尾、肤色、五官、身形、普通制服细节不得输出为道具；"
                   "触角/触须只有明确独立动作且有特写或视线锁定时才可作为角色组件，否则留在人物外观；"
                   "owner/parent_ref 必须指向实际角色，不得臆造蚁族/蛛族；但由道具派生的包裹/损坏/变形复合物，parent_ref 必须与 derived_from 指向同一个真实道具，制造者只写 owner；同一项圈/蛛腿/蛛丝跨集只保留一个母素材；"
                   "actions 最多 3 条且每条不超过 24 字；image_prompt 不超过 120 字；宁缺毋滥。"
                   if name == "道具" else
                   "只保留原文明确出现的单个人物/单一时空，群体不建档；sheet_prompt 和 geometry 字段保持精简。")
            )
            txt = chat_retry(cli, [{"role": "system", "content": sys_p + hint},
                                   {"role": "user", "content": user_p}],
                             max_tokens=12000, timeout=420, extra=FAST_THINK)
            try:
                data = parse_json(txt)
            except ValueError as retry_exc:
                raise ValueError(f"{name} JSON 仍不完整：{retry_exc}") from retry_exc
        data, rejected = _gate_extracted_data(name, data, text, catalog)
        if rejected:
            print(f"[提炼门控] {name} 丢弃 {len(rejected)} 条无原文证据候选：{'；'.join(rejected[:4])}", flush=True)
        data["prompt_version"] = PM.PROMPT_VERSION
        key = keys[name]
        # 三类资产在同一个 combined 中按顺序合并，关系归一化可以跨类型解析
        # owner/parent_ref/derived_from；本次提炼不会覆盖其它类别。
        combined = script_repository.merge_assets(combined, {key: data.get(key) or []}, ep_id)
    # 保留各文件其它顶层元数据，只替换对应数组；关系字段已经在 combined 中统一归一化。
    for name, out in paths.items():
        key = keys[name]
        doc = documents[name]
        doc[key] = combined.get(key) or []
        doc["prompt_version"] = PM.PROMPT_VERSION
        doc["script_rev"] = script_rev
        _dump(out, doc)
        print(f"[完成] {name} {len(doc[key])} 条 -> {os.path.basename(out)}")
    # 跨集校准：按集提炼看不到全剧弧线（gender 多数票/阵营轨迹），合并后单次 LLM 只补
    # gender/identity_anchor/states 三个跨集字段，输出小无截断风险。
    try:
        reconcile_characters(proj, cli, text)
    except Exception as e:
        print(f"[警告] 角色跨集校准失败（可重跑 extract 或单独调 reconcile）：{e}")

def _storyboard_completion(cli, system_prompt, user_prompt):
    """生成并解析分镜 JSON；截断时用压缩约束自动重试一次。"""
    messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]
    text = chat_retry(cli, messages, max_tokens=STORYBOARD_MAX_TOKENS, timeout=720, extra=FAST_THINK)
    parsed = parse_structured(text)
    if parsed.get("complete"):
        return parsed, text
    repair_hint = (
        "上一次分镜 JSON 输出不完整。请基于同一剧本重新输出一个完整合法 JSON 对象；"
        "最多 20 镜，每镜 prompt 100~160 字，negative 最多 4 条，字段值用短句，禁止 markdown 和解释。"
    )
    retry_messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt + "\n\n" + repair_hint},
    ]
    retry_text = chat_retry(cli, retry_messages, max_tokens=STORYBOARD_MAX_TOKENS, timeout=720, extra=FAST_THINK)
    return parse_structured(retry_text), retry_text


def _normalise_refs(values, kind, records, text=""):
    """把模型给出的名字、ID和 @ 引用归一为存在的单体资产引用。"""
    rows=[x for x in (records or []) if isinstance(x, dict) and x.get("id")]
    by_id={str(x.get("id")): x for x in rows}
    by_name={str(x.get("name")): x for x in rows if x.get("name")}
    refs=[]
    def add(value):
        raw=str(value or "").strip()
        if not raw: return
        if raw.startswith("@"):
            head, ident=(raw[1:].split(":",1)+[""])[:2] if ":" in raw[1:] else ("", raw[1:])
            if head and head != kind: return
            raw=ident or raw
        rec=by_id.get(raw) or by_name.get(raw)
        if rec:
            ref=f"@{kind}:{rec['id']}"
            if ref not in refs: refs.append(ref)
    for value in values or []: add(value)
    text=str(text or "")
    for token in re.findall(rf"@{kind}:([\w-]+)", text): add(token)
    # 名称匹配按长度倒序，避免“学生”先吃掉“佐藤陆”等更长名称。
    for rec in sorted(rows, key=lambda x: len(str(x.get("name") or "")), reverse=True):
        name=str(rec.get("name") or "").strip()
        if name and name in text: add(name)
    return refs


def _pick_scene_ref(shot, scenes, text):
    values=[shot.get("scene_ref"), shot.get("scene_asset"), shot.get("location_id"), shot.get("location")]
    raw_scene=str(shot.get("scene") or "").strip().lower()
    if raw_scene not in ("room", "field", "", "indoor", "outdoor"):
        values.append(shot.get("scene"))
    refs=_normalise_refs(values, "scene", scenes, text)
    if refs: return refs[0]
    # 没有显式场景时按名称/别名和室内外兜底；不再写入无效的 @scene:room。
    rows=[x for x in (scenes or []) if isinstance(x, dict) and x.get("id")]
    for rec in rows:
        if str(rec.get("name") or "") and str(rec.get("name")) in str(text or ""):
            return "@scene:"+str(rec["id"])
    if len(rows)==1: return "@scene:"+str(rows[0]["id"])
    for rec in rows:
        if raw_scene == "room" and rec.get("interior") is True: return "@scene:"+str(rec["id"])
        if raw_scene == "field" and rec.get("interior") is False: return "@scene:"+str(rec["id"])
    return ""


def cmd_storyboard(proj, vendor, ep_id, out=None):
    text = _episode_text(proj, ep_id)
    if not text:
        print("[错误] 无剧本文本"); sys.exit(1)
    base = os.path.join(proj, "剧本")
    chars = json.load(open(os.path.join(proj, "素材", "人物.json"), encoding="utf-8")).get("characters", []) \
        if os.path.isfile(os.path.join(proj, "素材", "人物.json")) else []
    chars = [c for c in chars if not script_repository.is_collective_asset(c)]
    chars = [c for c in chars if not is_narrator(c.get("id")) and not is_narrator(c.get("name"))]
    scenes = json.load(open(os.path.join(proj, "素材", "场景.json"), encoding="utf-8")).get("scenes", []) \
        if os.path.isfile(os.path.join(proj, "素材", "场景.json")) else []
    props = json.load(open(os.path.join(proj, "素材", "道具.json"), encoding="utf-8")).get("props", []) \
        if os.path.isfile(os.path.join(proj, "素材", "道具.json")) else []
    props = [p for p in props if script_repository.is_asset_prop(p)]
    cli = VendorClient(pick_vendor(vendor))
    import knowledge as _K
    hits = _K.query(text, k=4)
    if hits:
        print("[知识垫上下文] " + "；".join(f"{h['skill']}（{h['source']}）" for h in hits))
    sys_p, user_p = PM.storyboard_prompt(text, chars, scenes, props, mood_text=text,
                                         style_text=skill_lib.style_for(proj, "storyboard"))
    parsed, txt = _storyboard_completion(cli, sys_p, user_p)
    if not parsed["complete"]:
        print("[错误] LLM 分镜 JSON 不完整，已拒绝写入：" + "; ".join(parsed.get("repair_notes") or []))
        sys.exit(1)
    data = parsed["data"]
    if not isinstance(data, dict):
        print("[错误] LLM 分镜 JSON 顶层必须是对象，已拒绝写入")
        sys.exit(1)
    shots = data.get("shots") or []
    if not shots:
        print("[错误] LLM 未产出 shots:\n" + txt[:400]); sys.exit(1)
    from production_prompts import require_prompts, normalize_prompts, source_hash
    from production_studio import default_units, validate_units, shot_list, retain_production
    require_prompts(shots)
    for shot in shots:
        normalize_prompts(shot)
        for field in ('prompt_image', 'prompt_video', 'prompt_grid'):
            shot[field + '_source'] = 'llm'
    # 契约化：id 去重/补齐、数值兜底、词表外的值回退
    VALID = {"shot_size": ["大远景", "远景", "全景", "中景", "中近景", "近景", "特写", "大特写"],
             "camera_move": ["固定", "推", "拉", "摇", "移", "跟", "甩", "升降", "环绕", "手持", "斯坦尼康", "变焦", "轨道", "无人机", "主观"],
             "angle": ["平视", "俯视", "仰视", "鸟瞰", "虫视", "荷兰角", "过肩", "主观"],
             "cam": ["wide", "two", "cu", "ots"], "scene": ["room", "field"]}
    name2id = {str(c.get("name")): c.get("id") for c in chars if c.get("name") and c.get("id")}
    seen = set()
    for i, s in enumerate(shots, 1):
        sid = str(s.get("id") or f"S{i}")
        k = 2
        while sid in seen:
            sid = f"S{i}_{k}"; k += 1
        seen.add(sid)
        s["id"] = sid
        s["dur"] = round(max(1.5, min(15.0, float(s.get("dur") or 4))), 2)
        for k2, vals in VALID.items():
            if s.get(k2) not in vals:
                s[k2] = {"cam": "wide", "scene": "room"}.get(k2, vals[0])
        for L in s.get("lines") or []:
            L["speaker"] = name2id.get(L.get("speaker"), L.get("speaker"))
            if is_narrator(L.get("speaker")):
                L["speaker"] = "narrator"
            L["at"] = round(max(0.0, float(L.get("at") or 0)), 2)
            L["dur"] = round(max(0.8, float(L.get("dur") or 2)), 2)
            if L["at"] + L["dur"] > s["dur"]:
                print(f"[警告] {s['id']} 台词超镜（at={L['at']} dur={L['dur']} > 镜长 {s['dur']}），已夹取到镜内："
                      f"{str(L.get('line') or L.get('text') or '')[:20]}", flush=True)
                L["at"] = round(max(0.0, s["dur"] - 0.8), 2)
                L["dur"] = round(max(0.8, s["dur"] - L["at"]), 2)
        if s.get("speaker") in name2id:
            s["speaker"] = name2id[s["speaker"]]
        if is_narrator(s.get("speaker")):
            s["speaker"] = "narrator"
        fact_text = " ".join(str(s.get(k) or "") for k in ("prompt", "content", "action", "location", "scene"))
        raw_actors = s.get("actor_refs") if isinstance(s.get("actor_refs"), list) else []
        raw_actors += [sp for sp in [s.get("speaker")] + [L.get("speaker") for L in (s.get("lines") or []) if isinstance(L, dict)]
                       if sp and not is_narrator(sp)]
        actor_refs = _normalise_refs(raw_actors, "character", chars, fact_text)
        s["actor_refs"] = actor_refs
        scene_ref = _pick_scene_ref(s, scenes, fact_text)
        if scene_ref: s["scene_ref"] = scene_ref
        else: s.pop("scene_ref", None)
        prop_refs = _normalise_refs(s.get("prop_refs") if isinstance(s.get("prop_refs"), list) else [], "prop", props, fact_text)
        s["prop_refs"] = prop_refs
        # 这是可审计的关联资产清单；图像输入的厂商上限在 resolve_shot_refs 单独处理。
        asset_refs=[]
        for ref in ([scene_ref] if scene_ref else []) + actor_refs + prop_refs:
            if ref and ref not in asset_refs: asset_refs.append(ref)
        s["asset_refs"] = asset_refs
        prompt = str(s.get("prompt") or "").strip()
        if asset_refs:
            asset_line = "资产引用：" + "、".join(asset_refs)
            if asset_line not in prompt:
                s["prompt"] = (prompt + "\n" if prompt else "") + asset_line
        s["move"] = "·".join(x for x in (s.get("shot_size"), s.get("camera_move"), s.get("angle")) if x)
    cfg = {"project": os.path.basename(proj).rstrip("/") + "_剧本" + (f"_{ep_id}" if ep_id else ""),
           "title": (f"剧本创作 · {ep_id}" if ep_id else "剧本创作 · 全本"),
           "w": 960, "h": 540, "fps": 24, "set": {}, "env": {},
           "prompt_version": PM.PROMPT_VERSION,
           "script_rev": _rev_of(os.path.join(base, "分集.json")),
           "actors": {c["id"]: {"name": c.get("name", c["id"]),
                                "shirt": [(160, 60, 60), (40, 90, 160), (60, 140, 90), (150, 120, 50),
                                          (120, 70, 150), (60, 150, 150)][i % 6],
                                "pos": [[-1.7, -0.9], [1.8, 0.3], [1.7, -0.9], [0.0, 2.2],
                                        [-2.5, 1.4], [2.5, 1.6]][i % 6], "static": True}
                      for i, c in enumerate(chars[:6])} if chars else {},
           "shots": shots}
    # 角色自愈：台词中出现但资产里没有的说话人 → 自动进 actors（配色/站位顺延），
    # 保证未跑资产提炼时分镜也自洽可渲染
    PALETTE = [(160, 60, 60), (40, 90, 160), (60, 140, 90), (150, 120, 50),
               (120, 70, 150), (60, 150, 150), (180, 100, 40), (100, 100, 110)]
    POS = [[-1.7, -0.9], [1.8, 0.3], [1.7, -0.9], [0.0, 2.2], [-2.5, 1.4], [2.5, 1.6],
           [-2.6, -1.7], [2.6, -1.8], [-3.3, 0.6], [3.3, 0.9]]
    used = set()
    for sh in shots:
        if sh.get("speaker") and not is_narrator(sh["speaker"]):
            used.add(sh["speaker"])
        for L in sh.get("lines") or []:
            if L.get("speaker") and not is_narrator(L["speaker"]):
                used.add(L["speaker"])
    for i, sp in enumerate(sorted(used)):
        if sp not in cfg["actors"]:
            cfg["actors"][sp] = {"name": sp, "shirt": PALETTE[len(cfg["actors"]) % len(PALETTE)],
                                 "pos": POS[len(cfg["actors"]) % len(POS)], "static": True}
    validation = validate_document(cfg)
    if validation["errors"]:
        details = "；".join(f"{e.get('code')}: {e.get('message')}" for e in validation["errors"][:8])
        print("[错误] 分镜契约校验失败，已拒绝写入：" + details)
        sys.exit(1)
    for warning in validation.get("warnings") or []:
        print(f"[警告] {warning.get('code')}: {warning.get('message')}")
    out = out or os.path.join(proj, "分镜", f"剧本_{ep_id or '全本'}.json")
    import uuid
    units = data.get('video_units') or []
    if not units: raise ValueError('LLM 未返回 V 分镜视频汇总，已保留原文件；请重新生成')
    for unit in units:
        if not str(unit.get('prompt_video') or '').strip() or not str(unit.get('prompt_grid') or '').strip():
            raise ValueError('V 缺少视频或宫格提示词，已拒绝保存不完整分镜')
        unit['id'] = 'v-' + uuid.uuid4().hex[:12]
        members = shot_list(cfg, unit)
        unit['duration'] = sum(float(s['dur']) for s in members)
        unit['scene_ref'] = members[0].get('scene_ref', '')
        unit['source_hash'] = source_hash(members)
        for field in ('prompt_video', 'prompt_grid', 'negative', 'title'): unit[field + '_source'] = 'llm'
    validate_units(cfg, units)
    cfg['video_units'] = units
    if os.path.isfile(out):
        retain_production(json.load(open(out, encoding='utf-8')), cfg)
    _dump(out, cfg)
    print(f"[完成] 分镜 {len(shots)} 镜 -> {out}")
    print(f"[提示] 可直接进白模页渲染 / 3D 页构建 / 平面图生成")


def cmd_assemble(proj, sb_name):
    """分镜 -> 创作包 manifest：逐镜脚本 + 平面图 + 白模参考(预演包帧) + 用户素材引用。"""
    jp = os.path.join(proj, "分镜", sb_name)
    if not os.path.isfile(jp):
        print(f"[错误] 分镜不存在: {jp}"); sys.exit(1)
    cfg = json.load(open(jp, encoding="utf-8"))
    try:
        from actor_pipeline import default_context, hydrate_actor_cards
        cfg["acting_context"] = hydrate_actor_cards(cfg.get("acting_context") or default_context(cfg), proj, cfg)
    except Exception:
        pass
    from artifact_provenance import artifact_hash, is_current
    sb_base = os.path.splitext(sb_name)[0]
    # 平面图（无则生成）
    import subprocess
    diag_dir = os.path.join(proj, "推演", f"平面图_{sb_base}")
    diagram_hash = artifact_hash(cfg, "diagram", "2")
    diagram_meta = os.path.join(diag_dir, "_provenance.json")
    old_diagram_hash = ""
    try:
        old_diagram_hash = json.load(open(diagram_meta, encoding="utf-8")).get("source_hash", "")
    except (OSError, ValueError, TypeError):
        pass
    if not glob.glob(os.path.join(diag_dir, "*.png")) or old_diagram_hash != diagram_hash:
        r = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "shot_diagram.py"),
                            jp, "--outdir", diag_dir], capture_output=True, text=True, encoding="utf-8")
        if r.returncode != 0:
            print("[警告] 平面图生成失败: " + (r.stderr or r.stdout or "")[-300:])
        else:
            json.dump({"source_hash": diagram_hash, "artifact_kind": "diagram", "tool_version": "2"},
                      open(diagram_meta, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    # 剧情战略图（每次重组装都刷新——它就是创意包的"主线视图"）
    strat = os.path.join(proj, "推演", f"战略图_{sb_base}.html")
    r = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "strategy_map.py"),
                        jp, "--out", strat], capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        print("[警告] 战略图生成失败: " + (r.stderr or r.stdout or "")[-300:])
    # 白模参考（预演包干净帧优先）
    previz = os.path.join(proj, "白模", f"预演包_{sb_base}")
    # 用户素材（拉片素材/ 目录按场景关键词引用）
    mats = [f for f in (glob.glob(os.path.join(proj, "拉片素材", "*"))) if os.path.isfile(f)]
    pkg = {"storyboard": "分镜/" + sb_name, "title": cfg.get("title", sb_base),
           "source_hash": artifact_hash(cfg, "prompt", "2"), "artifact_kind": "creation_manifest", "tool_version": "2",
           "strategy_map": f"推演/战略图_{sb_base}.html" if os.path.isfile(strat) else None,
           "shots": [], "materials": [os.path.basename(m) for m in mats]}
    diag_files = sorted(glob.glob(os.path.join(diag_dir, "*.png")))
    from prompt_compiler import compile_shot
    for i, sh in enumerate(cfg.get("shots") or [], 1):
        sid = str(sh.get("id", f"S{i}"))
        ref = None
        pf = os.path.join(previz, sid + ".png")
        if os.path.isfile(pf):
            ref = "白模/" + os.path.basename(previz) + f"/{sid}.png"
        diag = os.path.join(diag_dir, sid + ".png")
        compiled = compile_shot(cfg, sid, mode="stateful", media_type="video")
        entry = {"id": sid, "dur": sh.get("dur"), "move": sh.get("move", ""),
                 "scene": sh.get("scene", "room"),
                 "action": sh.get("action", ""),
                 "script": [{"speaker": L.get("speaker"), "text": L.get("line")}
                            for L in (sh.get("lines") or [])],
                 "diagram": os.path.basename(diag) if os.path.isfile(diag) else None,
                 "white_ref": ref, "prompt": compiled.get("text", ""),
                 "baseline_prompt": compiled.get("baseline_prompt", ""),
                 "mode_used": compiled.get("mode_used", "baseline"),
                 "voice_notes": compiled.get("voice_notes", []),
                 "warnings": compiled.get("warnings", []),
                 "performance_used": bool(compiled.get("performance_used")),
                 "source_hash": compiled.get("source_hash", "")}
        pkg["shots"].append(entry)
    out = os.path.join(proj, "推演", f"创作包_{sb_base}", "manifest.json")
    _dump(out, pkg)
    print(f"[完成] 创作包 {len(pkg['shots'])} 镜（平面图 {len(diag_files)} 张 / 白模参考见 white_ref / 拉片素材 {len(mats)} 个）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["episodes", "expand", "overview", "extract", "storyboard", "assemble", "reconcile"])
    ap.add_argument("project")
    ap.add_argument("--vendor", default=None)
    ap.add_argument("--episode", default=None, help="集 id（缺省全本）")
    ap.add_argument("--out", default=None)
    ap.add_argument("--storyboard", dest="sb", default=None, help="assemble: 分镜文件名")
    ap.add_argument("--idea", default=None, help="expand: 一段话创作构想（缺省读 剧本/构想.txt）")
    ap.add_argument("--eps", type=int, default=6, help="expand: 目标集数（默认 6）")
    a = ap.parse_args()
    proj = os.path.abspath(a.project)
    if not os.path.isdir(proj):
        print(f"[错误] 项目不存在: {proj}"); sys.exit(1)
    if a.cmd == "episodes":
        cmd_episodes(proj, a.vendor, a.out or os.path.join(proj, "剧本", "分集.json"))
    elif a.cmd == "expand":
        cmd_expand(proj, a.vendor, a.idea, a.eps, a.episode)
    elif a.cmd == "overview":
        cmd_overview(proj, a.vendor, a.episode)
    elif a.cmd == "reconcile":
        # 只跑角色跨集校准（不重跑三件套提炼）——修 gender/anchor/states
        cli = VendorClient(pick_vendor(a.vendor))
        text = _episode_text(proj, a.episode)
        reconcile_characters(proj, cli, text)
    elif a.cmd == "extract":
        # 全本提炼按分集执行再合并，避免一次性把长剧本塞给模型导致 JSON 截断。
        # 显式 --episode 仍只处理指定集，便于单集重跑。
        if not a.episode:
            ep_path = os.path.join(proj, "剧本", "分集.json")
            try:
                ep_rows = json.load(open(ep_path, encoding="utf-8")).get("episodes", []) if os.path.isfile(ep_path) else []
            except Exception:
                ep_rows = []
            ep_ids = [str(row.get("id")) for row in ep_rows if isinstance(row, dict) and row.get("id")]
            if len(ep_ids) > 1:
                failed = []
                for eid in ep_ids:
                    print(f"[资产提炼] 分集 {eid}/{len(ep_ids)}", flush=True)
                    try:
                        cmd_extract(proj, a.vendor, eid)
                    except SystemExit as exc:
                        failed.append((eid, f"退出码 {exc.code}"))
                    except Exception as exc:
                        failed.append((eid, str(exc)))
                if failed:
                    print("[资产提炼] 部分分集失败：" + "；".join(f"{eid}: {err}" for eid, err in failed), flush=True)
                    sys.exit(1)
            else:
                cmd_extract(proj, a.vendor, a.episode)
        else:
            cmd_extract(proj, a.vendor, a.episode)
    elif a.cmd == "storyboard":
        cmd_storyboard(proj, a.vendor, a.episode, a.out)
    elif a.cmd == "assemble":
        if not a.sb:
            print("[错误] assemble 需要 --storyboard 文件名"); sys.exit(1)
        cmd_assemble(proj, a.sb)


if __name__ == "__main__":
    main()
