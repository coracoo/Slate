# -*- coding: utf-8 -*-
"""资产设定图生图：人物三视图 / 场景概念图 / 道具设定图（跨镜头一致性参考图）

提炼（LLM）→ 生图（image 厂商）。
- 人物：characters[].sheet_prompt（三视图：正面/侧面/背面全身立绘，纯白背景）→ 素材/人物/<id>.png
- 场景：scenes[].image_prompt → 素材/场景/<id>.png
- 道具：props[].image_prompt → 素材/道具/<id>.png
索引：素材/素材图.json {人物:{id:{path,prompt}},场景:{...},道具:{...}} —— 供前端展示与
模拟创作/图生视频把资产图作为参考图（角色一致性的关键）。

参考图规则：
- 母素材（无 parent_ref/derived_from）：母图零参考文生图直出（人物三视图
  纯白底、纯场景无人物、纯道具），不因任何引用缺失而拦截。
- 子素材（parent_ref，如道具 component_of 角色）：以父资产母图为参考改图；
  父图缺失时降级为无参考生成并打印告警。
- derived_from 派生子图：同子素材，以父资产母图为参考改图，缺失同样降级。
- states 状态图：以本资产母图为参考改图。
- related_refs / 提示词内 @token：仅叙事关联，永不进入生图依赖——
  related/parent 互指（A related→B、B parent→A）不构成环，不互相阻塞。

用法: python gen_asset_images.py <项目目录> [--kind character|scene|prop|all] [--id 资产id] [--vendor 厂商id] [--force]
stdout 末行 OUTPUT:<素材根目录>；退出码 0=全部成功 1=部分/全部失败（部分成功也写出已完成的索引）
"""
import sys, os, json, argparse
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from llm_openai import VendorClient, load_vendors, VendorError
from reference_limits import reference_limit
import skill_lib

_FEMALE_MARKERS = ("少女", "女性", "女孩", "女生", "女子", "woman", "girl", "female")
_MALE_MARKERS = ("少年", "男性", "男孩", "男生", "男子", "man", "boy", "male")
_GENDER_SWAP_F2M = {"少女": "少年", "女性": "男性", "女孩": "男孩", "女生": "男生", "女子": "男子",
                    "女高中生": "男高中生", "woman": "man", "girl": "boy", "female": "male"}
_GENDER_SWAP_M2F = {v: k for k, v in _GENDER_SWAP_F2M.items()}


def _gender_guard(item, prompt):
    """锚点一致性哨兵：identity_anchor 的性别与提示词性别词冲突时自动纠正并返回告警。
    性别使用母资产锚点。"""
    anchor = str(item.get("identity_anchor") or "")
    gender = str(item.get("gender") or "")
    wants = None
    if gender in ("男", "女"):
        wants = gender
    elif "男" in anchor and "女" not in anchor:
        wants = "男"
    elif "女" in anchor and "男" not in anchor:
        wants = "女"
    if not wants:
        return prompt, []
    has_f = any(m in prompt for m in _FEMALE_MARKERS)
    has_m = any(m in prompt for m in _MALE_MARKERS)
    if wants == "男" and has_f and not has_m:
        for a_, b_ in _GENDER_SWAP_F2M.items():
            prompt = prompt.replace(a_, b_)
        return prompt, [f"性别哨兵：锚点为男，已把提示词中的女性称谓自动改为男性"]
    if wants == "女" and has_m and not has_f:
        for a_, b_ in _GENDER_SWAP_M2F.items():
            prompt = prompt.replace(a_, b_)
        return prompt, [f"性别哨兵：锚点为女，已把提示词中的男性称谓自动改为女性"]
    return prompt, []


KINDS = {"character": ("人物.json", "characters", "sheet_prompt", "人物"),
         "scene": ("场景.json", "scenes", "image_prompt", "场景"),
         "prop": ("道具.json", "props", "image_prompt", "道具")}
KIND_ZONES = {"character": "人物", "scene": "场景", "prop": "道具"}


def asset_image_mode(vendor_id, image_refs, has_reference=None):
    """返回资产图片请求使用的模式。

    局域网 ComfyUI 的 Z-Image 是文生图工作流，只有实际带有参考图
    （derived_from 父资产母图，或状态图引用的本资产母图）时才切到
    Qwen Image Edit。其它厂商沿用其自身的生图接口，参考图由厂商适配器处理。
    """
    declared = bool(image_refs) if has_reference is None else bool(has_reference)
    return "edit" if str(vendor_id or "").strip() == "local-comfyui" and declared else "generate"


def mode_label(vendor_id, image_mode, has_refs):
    """任务日志用的生图模式标签：按厂商与是否实际带参考图命名。

    ComfyUI 走 Z-Image 文生图 / Qwen 改图双工作流；OpenAI 兼容厂商（doubao
    等）统一走 image 端点，参考图经 image 字段传入即图生图，标签只按是否
    实际带参考图区分，避免把带参考图的请求误标成「生图/Z-Image」。
    """
    if str(vendor_id or "").strip() == "local-comfyui":
        return "改图/模型匹配" if image_mode == "edit" else "生图/模型匹配"
    return f"{'图生图' if has_refs else '文生图'}/{vendor_id}"


def load_asset_index(project):
    """读取项目素材图索引；索引不存在或损坏时返回空结构。"""
    path = os.path.join(os.path.abspath(str(project)), "素材", "素材图.json")
    try:
        with open(path, encoding="utf-8") as fh:
            value = json.load(fh)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def collect_asset_image_plan(project, kind="all", asset_id=None, vendor_id="",
                             strict_dependencies=False):
    """收集待生成资产及其派生参考图，供 CLI 与 HTTP 前置校验共用。

    该函数只读取剧本资产档案和已有索引，不会创建目录、快照或调用模型。
    每项返回 ``kind``、``id``、``refs`` 和根据厂商计算出的 ``mode``。
    参考图来自 parent_ref（子素材父级）与 derived_from 派生链；父图缺失记入
    ``missing_refs`` 供告警展示，生成端会降级为无参考生成，不作为拦截条件。
    """
    proj = os.path.abspath(str(project))
    if kind not in KINDS and kind != "all":
        return []
    index = load_asset_index(proj)
    kinds = list(KINDS) if kind == "all" else [kind]
    plans = []
    for item_kind in kinds:
        src, key, _pfield, _zone = KINDS[item_kind]
        path = os.path.join(proj, "素材", src)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError, TypeError):
            continue
        items = data.get(key) if isinstance(data, dict) else None
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            ident = str(item.get("id") or "").strip()
            if not ident or (asset_id and ident != str(asset_id)):
                continue
            current_ref = f"@{item_kind}:{ident}"
            reference_tokens = _derived_reference_tokens(index, item, current_ref)
            refs = _derived_refs(proj, index, item, current_ref)
            missing_refs = [ref for ref in reference_tokens if not _ref_path(proj, index, ref)]
            plans.append({"kind": item_kind, "id": ident, "refs": refs,
                          "reference_tokens": reference_tokens,
                          "missing_refs": missing_refs,
                          "can_generate": bool(str(item.get(_pfield) or "").strip())})
    # 批量生成时先产出父资产，再产出子图；同层无依赖时保持档案原顺序。
    # 按 parent_ref + derived_from 建边（均为父子层级边）；related_refs 互指不成环。
    by_ref = {f"@{item['kind']}:{item['id']}": i for i, item in enumerate(plans)}
    planned_refs = {
        f"@{item['kind']}:{item['id']}" for item in plans if item["can_generate"]
    }
    ordered = []
    visiting = set()
    visited = set()

    def visit(index):
        if index in visited:
            return
        if index in visiting:
            # derived 链理论上是树；万一成环保留原顺序，避免规划器递归。
            return
        visiting.add(index)
        for ref in plans[index].get("reference_tokens") or []:
            dependency = by_ref.get(str(ref))
            if dependency is not None:
                visit(dependency)
        visiting.discard(index)
        visited.add(index)
        ordered.append(plans[index])

    for index in range(len(plans)):
        visit(index)
    # 参考模式按"实际有图"判定：父图已落盘，或父资产在本批次内排队生成
    # （生成端按拓扑序先产父图）。两者都不满足的派生子图降级为无参考生成。
    for plan in ordered:
        has_ref = bool(plan["refs"]) or any(
            str(ref) in planned_refs for ref in plan["reference_tokens"]
        )
        plan["mode"] = asset_image_mode(vendor_id, plan["refs"], has_ref)
        # 旧本地生成保持“缺父图可降级”的口径；浏览器自动执行采用严格模式，
        # 防止身份敏感派生素材在没有实际父图时退化为文生图。
        plan["can_execute"] = not (strict_dependencies and bool(plan["missing_refs"]))
        plan["execution_state"] = "ready" if plan["can_execute"] else "waiting_dependencies"
    return ordered


def asset_image_needs_generation(path, force=False):
    """补缺模式只在目标图片不存在时生成；force 用于显式重生成。"""
    return bool(force) or not os.path.isfile(path)


def _ref_path(project, index, ref):
    """把 @kind:id 解析为已生成的母素材图片路径。"""
    raw = str(ref or "").strip().lstrip("@")
    if ":" not in raw:
        return ""
    kind, ident = raw.split(":", 1)
    zone = KIND_ZONES.get(kind)
    if not zone or not ident:
        return ""
    record = (index.get(zone) or {}).get(ident) if isinstance(index.get(zone), dict) else None
    rel = record.get("path") if isinstance(record, dict) else ""
    candidate = os.path.join(project, rel.replace("/", os.sep)) if rel else ""
    if candidate and os.path.isfile(candidate):
        return candidate
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        candidate = os.path.join(project, "素材", zone, ident + ext)
        if os.path.isfile(candidate):
            return candidate
    return ""


def _derived_reference_tokens(index, item, current_ref=""):
    """收集子图依赖引用 token：parent_ref（子素材父级）+ derived_from 派生链。

    参考图规则：
    - 母素材（无 parent_ref/derived_from）：母图零参考文生图（三视图/纯场景/纯道具）；
    - 子素材（parent_ref，如道具 component_of 角色）：以父资产母图为参考改图；
    - derived_from 派生子图：以父资产母图为参考改图；
    - related_refs / 提示词 @token：仅叙事关联，永不进入生图依赖（防环）。
    """
    roots = []
    for key in ("derived_from", "parent_ref"):
        value = item.get(key)
        if value:
            roots.append(value)
    tokens = []
    seen = set()
    queue = list(roots)
    while queue:
        normalized = str(queue.pop(0) or "").strip()
        if not normalized or normalized == current_ref or normalized in seen:
            continue
        seen.add(normalized)
        tokens.append(normalized)
        raw = normalized.lstrip("@")
        record = None
        if ":" in raw:
            kind, ident = raw.split(":", 1)
            zone = KIND_ZONES.get(kind)
            records = index.get(zone) if zone else None
            record = records.get(ident) if isinstance(records, dict) else None
        if isinstance(record, dict):
            for key in ("derived_from", "parent_ref"):
                parent = record.get(key)
                if parent:
                    queue.append(parent)
    return tokens


def _derived_refs(project, index, item, current_ref=""):
    """派生链上已落盘的父资产母图路径（按引用顺序去重）。

    只传已有图片；父图缺失由调用方降级为无参考生成，不在此处拦截。
    """
    refs = []
    for ref in _derived_reference_tokens(index, item, current_ref):
        path = _ref_path(project, index, ref)
        if path and path not in refs:
            refs.append(path)
    return refs


def _with_reference_mentions(prompt, reference_tokens):
    """把资产关系以稳定 ``@`` 引用写入实际生图提示词。

    图片文件通过 ``image_refs`` 传给厂商，但仅传二进制参考图会让任务
    日志和索引里的提示词无法审计“这张图为何被引用”。这里补一行极短的
    引用清单，不展开母素材长设定，也不让模型把引用误当成新的动作。
    """
    text = str(prompt or "").strip()
    refs = list(dict.fromkeys(str(ref).strip() for ref in (reference_tokens or []) if str(ref).strip()))
    if not refs:
        return text
    marker = "资产引用（参考图按顺序）："
    line = marker + "、".join(refs)
    if marker in text:
        return text
    return line + ("\n" + text if text else "")


def pick_vendor(vendor_id=None):
    vs = [v for v in load_vendors() if v.get("enabled") and (v.get("models") or {}).get("image")]
    if vendor_id:
        vs = [v for v in vs if v["id"] == vendor_id]
    vs.sort(key=lambda v: not v.get("api_key"))
    if not vs:
        raise VendorError("没有已启用且配置 image 模型的厂商（环境页配置生图模型）")
    return vs[0]["id"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--kind", default="all", choices=["character", "scene", "prop", "all"])
    ap.add_argument("--id", default=None, help="只生成指定资产 id")
    ap.add_argument("--vendor", default=None)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--force", action="store_true", help="强制重生成已存在图片（默认只补缺）")
    ap.add_argument("--states", default="include", choices=["include", "only", "skip"],
                    help="人物状态图口径：include=母图+缺失状态图（默认）；only=只补状态图"
                         "（母图存在不重生成，缺失仍先生成——状态图要拿它当参考）；skip=只生成母图不碰状态图")
    ap.add_argument("--state-id", default=None, help="只生成指定状态 id（配合 --states only 使用）")
    a = ap.parse_args()
    proj = os.path.abspath(a.project)
    if not os.path.isdir(proj):
        print(f"[错误] 项目不存在: {proj}"); sys.exit(1)
    out_root = os.path.join(proj, "素材")
    os.makedirs(out_root, exist_ok=True)
    idx_path = os.path.join(out_root, "素材图.json")
    index = load_asset_index(proj)
    cli = VendorClient(pick_vendor(a.vendor))
    edit_model = (cli.models or {}).get("image_edit") or "未配置"
    print(f"[信息] 生图厂商: {cli.id} / 生图 {cli.model('image')} / 改图 {edit_model}")
    try:
        edit_ref_limit = reference_limit(cli.id, edit_model, "image_edit", getattr(cli, "cfg", None))
    except Exception:
        edit_ref_limit = 3
    kinds = list(KINDS) if a.kind == "all" else [a.kind]
    plans = collect_asset_image_plan(proj, a.kind, a.id, cli.id)
    # 先把本次任务涉及的资产读入内存，再按规划器给出的派生拓扑顺序
    # 处理（derived_from 父资产先于子图生成，跨类型派生也能先产父图）。
    source_items = {}
    for kind in kinds:
        src, key, _pfield, _zone = KINDS[kind]
        path = os.path.join(proj, "素材", src)
        if not os.path.isfile(path):
            print(f"[跳过] {src} 不存在（先在资产提炼页提炼）")
            continue
        try:
            rows = json.load(open(path, encoding="utf-8")).get(key) or []
        except (OSError, ValueError, TypeError) as exc:
            print(f"[跳过] {src} 无法读取：{exc}")
            continue
        for item in rows:
            if not isinstance(item, dict):
                continue
            ident = str(item.get("id") or "").strip()
            if ident and (not a.id or ident == a.id):
                source_items[(kind, ident)] = item
    plans = [plan for plan in plans if (plan["kind"], plan["id"]) in source_items]
    ok_all, fail = True, []
    for plan in plans:
        if a.states == "skip":
            plan["skip_states"] = True
        kind = plan["kind"]
        src, key, pfield, zone = KINDS[kind]
        it = source_items.get((kind, plan["id"]))
        if not it:
            continue
        index.setdefault(zone, {})
        aid = str(it.get("id") or "").strip()
        prompt = str(it.get(pfield) or "").strip()
        if not aid or not prompt:
            if aid:
                print(f"[跳过] {zone}/{aid} 无 {pfield}（重新提炼可补）")
            continue
        prompt, guard_notes = _gender_guard(it, prompt)
        for note in guard_notes:
            print(f"[告警] {zone}/{aid} {note}")
        # 剔空值废词："性别不明"对生图无信息量（异兽/无实体角色本就不需要性别）
        prompt = prompt.replace("性别不明，", "").replace("性别不明", "")
        # 资产级画风覆盖（资产档案 style 字段）优先于项目生图风格
        asset_style = str(it.get("style") or "").strip() or None
        # 三层组装唯一入口（母图/状态图同路）：外观事实 → 画风层 → 类别硬约束；负面=全局基础 ∪ skill 定制
        prompt, negative = skill_lib.compose_asset_image_prompt(
            proj, prompt, skill_id=asset_style, kind=kind, style_prompt=it.get("style_prompt"))
        current_ref = f"@{kind}:{aid}"
        # 母图无参考生成：只有 derived_from 派生链进入参考；parent_ref /
        # related_refs / 提示词 @token 只是关联信息，不影响生图。
        reference_tokens = _derived_reference_tokens(index, it, current_ref)
        all_image_refs = _derived_refs(proj, index, it, current_ref)
        image_refs = all_image_refs[:edit_ref_limit]
        # 模式按实际参考图判定：父图缺失（降级）时回到纯文生图。
        image_mode = asset_image_mode(cli.id, image_refs)
        # 参考图路径是给厂商的输入，@ 引用清单是给模型和任务审计用的
        # 正文；只写实际有图的派生引用，降级缺图不进提示词。
        resolved_tokens = [ref for ref in reference_tokens if _ref_path(proj, index, ref)]
        prompt = _with_reference_mentions(prompt, resolved_tokens)
        missing_refs = [ref for ref in reference_tokens if not _ref_path(proj, index, ref)]
        out_dir = os.path.join(out_root, zone)
        os.makedirs(out_dir, exist_ok=True)
        out = os.path.join(out_dir, aid + ".png")
        # --states only：只补状态图，母图已存在就不重生成；母图缺失仍先生成
        # （状态图要拿母图当参考）。补缺/only 跳过时不再 continue——状态图分支
        # 在母图之后统一执行，是否跳过由 plan["skip_states"] 决定。
        mother_needed = asset_image_needs_generation(out, a.force)
        if (a.states == "only" or a.state_id) and os.path.isfile(out):
            mother_needed = False
        if not mother_needed:
            # 图片已存在时保留文件，顺手修复/刷新索引元数据，不调用模型也不创建版本。
            index[zone][aid] = {"path": f"素材/{zone}/{aid}.png", "prompt": prompt,
                                "name": it.get("name", aid),
                                "parent_ref": it.get("parent_ref"),
                                "relation": it.get("relation"),
                                "derived_from": it.get("derived_from"),
                                "related_refs": it.get("related_refs") or [],
                                "reference_refs": reference_tokens}
            print(f"[跳过] {zone}/{aid} 已存在（补缺模式）")
        else:
            if missing_refs:
                # 派生父图缺失不再硬失败：降级为无参考生成（父图可能尚未生成、
                # 已删除，或父资产本次生成失败）。
                print(f"[告警] {zone}/{aid} 派生母图缺失（{', '.join(missing_refs[:6])}），降级为无参考生成")
            if image_refs:
                prompt = ("以关联资产参考图作为身份、结构、材质和画风锚点，只生成当前素材本身，"
                          "不要复制参考图中的其它动作或额外对象。" + chr(10) + prompt)
            if len(all_image_refs) > edit_ref_limit:
                print(f"[提示] {zone}/{aid} 关系参考图 {len(all_image_refs)} 张，按 {cli.id} 改图输入上限 {edit_ref_limit} 张取前 {edit_ref_limit} 张", flush=True)
            import versions as _V; _V.snapshot(out)
            try:
                m_label = mode_label(cli.id, image_mode, bool(image_refs))
                print(f"[{m_label}] {zone}/{aid}（参考图 {len(image_refs)} 张）...", flush=True)
                cli.generate_image(prompt, out, timeout=a.timeout, negative_prompt=negative,
                                    image_refs=image_refs,
                                    mode=image_mode)
                index[zone][aid] = {"path": f"素材/{zone}/{aid}.png", "prompt": prompt,
                                    "name": it.get("name", aid),
                                    "parent_ref": it.get("parent_ref"),
                                    "relation": it.get("relation"),
                                    "derived_from": it.get("derived_from"),
                                    "related_refs": it.get("related_refs") or [],
                                    "reference_refs": reference_tokens}
                print(f"[完成] {zone}/{aid} -> 素材/{zone}/{aid}.png")
            except Exception as e:
                ok_all = False; fail.append(f"{zone}/{aid}: {e}")
                print(f"[失败] {zone}/{aid}: {e}")
        # ---- 状态资产图：同一角色的剧情阶段变体（锚点+差异），文件 <aid>__<状态id>.png ----
        if kind != "character" or plan.get("skip_states"):
            continue
        states_entry = {}
        for st_item in (it.get("states") or []):
            if not isinstance(st_item, dict):
                continue
            sid = str(st_item.get("id") or "").strip()
            if a.state_id and sid != a.state_id:
                continue
            s_prompt = str(st_item.get("sheet_prompt") or "").strip()
            if not sid or not s_prompt:
                continue
            s_prompt, s_notes = _gender_guard(it, s_prompt)
            for note in s_notes:
                print(f"[告警] {zone}/{aid}#{sid} {note}")
            s_prompt, _s_neg = skill_lib.compose_asset_image_prompt(
                proj, s_prompt, skill_id=asset_style, kind=kind, style_prompt=it.get("style_prompt"))
            s_out = os.path.join(out_dir, f"{aid}__{sid}.png")
            if not asset_image_needs_generation(s_out, a.force):
                states_entry[sid] = {"path": f"素材/{zone}/{aid}__{sid}.png",
                                     "prompt": s_prompt, "label": st_item.get("label", sid)}
                continue
            import versions as _V2; _V2.snapshot(s_out)
            try:
                s_refs = [out] if os.path.isfile(out) else []
                s_mode = asset_image_mode(cli.id, s_refs)
                s_label = mode_label(cli.id, s_mode, bool(s_refs))
                print(f"[{s_label}-状态] {zone}/{aid}#{sid}（{st_item.get('label','')}）...", flush=True)
                cli.generate_image(s_prompt, s_out, timeout=a.timeout, negative_prompt=negative,
                                   image_refs=s_refs, mode=s_mode)
                states_entry[sid] = {"path": f"素材/{zone}/{aid}__{sid}.png",
                                     "prompt": s_prompt, "label": st_item.get("label", sid),
                                     "episodes": st_item.get("episodes") or [],
                                     "camp": st_item.get("camp", "")}
                print(f"[完成] {zone}/{aid}#{sid} -> {aid}__{sid}.png")
            except Exception as e:
                ok_all = False; fail.append(f"{zone}/{aid}#{sid}: {e}")
                print(f"[失败] {zone}/{aid}#{sid}: {e}")
        if states_entry and aid in index.get(zone, {}):
            index[zone][aid]["states"] = states_entry
    json.dump(index, open(idx_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if fail:
        print("[部分失败] " + "；".join(fail[:5]))
    print(f"[完成] 索引 -> {idx_path}")
    print("OUTPUT:" + out_root)
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
