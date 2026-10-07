# -*- coding: utf-8 -*-
"""资产设定图生图：人物五视图设定图 / 场景概念图 / 道具设定图（跨镜头一致性参考图）

提炼（LLM）→ 生图（image 厂商）。
- 人物：characters[].sheet_prompt（五视图设定图：脸部正面/45度侧特写 + 从颈部到脚底的正面/侧面全身 + 含头部的背面全身，纯白背景）→ 素材/人物/<id>.png
- 场景：scenes[].image_prompt → 素材/场景/<id>.png
- 道具：props[].image_prompt → 素材/道具/<id>.png
索引：素材/素材图.json {人物:{id:{path,prompt}},场景:{...},道具:{...}} —— 供前端展示与
模拟创作/图生视频把资产图作为参考图（角色一致性的关键）。

参考图规则：
- 母素材（无 parent_ref/derived_from）：母图零参考文生图直出（人物五视图
  纯白底、纯场景无人物、纯道具），不因任何引用缺失而拦截。
- 子素材（parent_ref，如道具 component_of 角色）：以父资产母图为参考改图；
  父图缺失时降级为无参考生成并打印告警。
- derived_from 派生子图：同子素材，以父资产母图为参考改图，缺失同样降级。
- states 状态图：以本资产母图为参考改图。
- related_refs / 提示词内 @token：仅叙事关联，永不进入生图依赖——
  related/parent 互指（A related→B、B parent→A）不构成环，不互相阻塞。

用法: python gen_asset_images.py <项目目录> [--kind character|scene|prop|all] [--id 资产id] [--vendor 厂商id] [--workers N] [--force]
stdout 末行 OUTPUT:<素材根目录>；退出码 0=全部成功 1=部分/全部失败（部分成功也写出已完成的索引）
"""
import sys, os, json, argparse, re, copy
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from llm_openai import VendorClient, load_vendors, VendorError, set_billing_project
from reference_limits import reference_limit
import skill_lib
from visual_asset_prompt import subject_prompt, generation_kind, state_output_notice
from character_sheet_mask import mask_character_sheet

_FEMALE_MARKERS = ("少女", "女性", "女孩", "女生", "女子", "woman", "girl", "female")
_MALE_MARKERS = ("少年", "男性", "男孩", "男生", "男子", "man", "boy", "male")
_GENDER_SWAP_F2M = {"少女": "少年", "女性": "男性", "女孩": "男孩", "女生": "男生", "女子": "男子",
                    "女高中生": "男高中生", "woman": "man", "girl": "boy", "female": "male"}
_GENDER_SWAP_M2F = {v: k for k, v in _GENDER_SWAP_F2M.items()}


# "性别不明/性别未提及"这类空信息词在生图里没有价值，要剔；但剔的时候必须把它带的
# "的/，/、"一起带走，否则档案里会留下"的成年人"这种主语残缺的句子
# （09_仙 沈砚 的母图提示词就是这么坏的：状态图写"性别不明的成年人"，母图只剩"的成年人"）。
GENDER_PLACEHOLDERS = ("性别不明的", "性别不明，", "性别不明、", "性别不明",
                       "性别未提及的", "性别未提及，", "性别未提及")


def drop_gender_placeholder(text, replacement=""):
    """删掉或替换性别占位词；replacement 为空时连连接词一起剥净，保证句子不残缺。"""
    out = str(text or "")
    for marker in GENDER_PLACEHOLDERS:
        tail = (replacement + "的") if (replacement and marker.endswith("的")) else \
               (replacement + "，") if (replacement and marker.endswith("，")) else \
               (replacement + "、") if (replacement and marker.endswith("、")) else replacement
        out = out.replace(marker, tail)
    out = re.sub(r"[，、；;]{2,}", "；", out)
    return out.strip("；;、， \n")


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


def skipped_image_entry(previous, descriptive, desired_spec):
    """图片未重生成时保留真实生成溯源，只把当前目标记录为待更新规格。"""
    previous = copy.deepcopy(previous) if isinstance(previous, dict) else {}
    entry = previous
    entry.update(copy.deepcopy(descriptive))
    entry["desired_spec"] = copy.deepcopy(desired_spec)
    reasons = []
    if not previous.get("prompt"):
        entry.setdefault("generation_status", "unknown_legacy")
        reasons.append("历史图片缺少生成提示词记录")
    elif previous.get("prompt") != desired_spec.get("prompt"):
        reasons.append("当前提示词与历史生成提示词不同")
    if previous.get("skill_snapshot") != desired_spec.get("skill_snapshot"):
        reasons.append("当前 Skill 与历史生成 Skill 不同")
    if previous.get("reference_refs") != desired_spec.get("reference_refs"):
        reasons.append("当前参考资产与历史生成参考不同")
    if reasons:
        entry["stale"] = True
        entry["stale_reasons"] = reasons
    else:
        entry.pop("stale", None)
        entry.pop("stale_reasons", None)
    return entry


def archive_ids(project, kind):
    """资产档案里的 id 集合（读不到或没这栏时返回 None = 无法判断，不误报）。"""
    fname, key = KINDS[kind][0], KINDS[kind][1]
    path = os.path.join(os.path.abspath(str(project)), "素材", fname)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    rows = doc.get(key) if isinstance(doc, dict) else doc
    if not isinstance(rows, list):
        return None
    return {str(r.get("id")) for r in rows if isinstance(r, dict) and r.get("id")}


def find_orphan_index_rows(project):
    """索引里有图、资产档案里已无该记录的条目。

    重跑 ② 提炼时 LLM 会换 id（如 quantongban → xueshenga），而 素材图.json 只增不删，
    于是留下"图还在、档案已无此人"的孤儿行；分镜继续 @character:旧id 引用时，
    身份锚点在 prompt_assembler 那层就已静默失效。返回 [{kind, id, path}]。
    """
    zone_of = {v[3]: k for k, v in KINDS.items()}
    index = load_asset_index(project)
    out = []
    for zone, kind in zone_of.items():
        ids = archive_ids(project, kind)
        if ids is None:
            continue
        for key, ent in (index.get(zone) or {}).items():
            base = str(key).split("__", 1)[0]        # 状态图/派生图/平面图按母 id 归位
            if base in ids:
                continue
            path = ent.get("path") if isinstance(ent, dict) else None
            if path:
                out.append({"kind": kind, "zone": zone, "id": str(key), "path": str(path)})
    return out


def report_orphan_index_rows(project):
    """提炼收尾的确定性体检：只报不改（删图/补档都归用户判断）。"""
    orphans = find_orphan_index_rows(project)
    for row in orphans:
        print(f"[警告] 素材图索引孤儿：{row['path']} 对应的{row['zone']}档案已不存在"
              f"（重跑提炼换过 id？分镜若仍引用 @{row['kind']}:{row['id']} 将拿不到身份锚点）")
    if orphans:
        print(f"[提示] 共 {len(orphans)} 条索引行在档案里已无对应资产；确认后可在 ② 删除对应素材图，"
              f"或把分镜引用改指新 id")
    return orphans


def delete_asset_image(project, kind, asset_id):
    """删除子素材图（N89）：快照到 .versions（可恢复）→ 删文件与 sidecar → 清索引条目。

    kind: character|scene|prop；asset_id 为索引键（母图 id、状态 `<母id>__<状态id>`、
    派生子图/平面图 `<id>__plan` 等）。返回删除的相对路径。
    """
    zones = {"character": "人物", "scene": "场景", "prop": "道具"}
    zone = zones.get(kind)
    if not zone:
        raise ValueError(f"未知的素材类型: {kind}")
    index = load_asset_index(project)
    ent = (index.get(zone) or {}).get(asset_id)
    if not ent or not ent.get("path"):
        raise ValueError(f"索引中没有该素材图：{zone}/{asset_id}")
    if ent.get("usage") == "plan":
        raise ValueError("平面图由 plan.json 驱动（确定性渲染缓存），不支持独立删除——"
                         "请到 ⑥ 平面推演 编辑/重渲染覆盖，PNG 会自动重新生成")
    rel = str(ent["path"]).replace("\\", "/")
    abs_path = os.path.join(os.path.abspath(str(project)), rel)
    if not os.path.isfile(abs_path):
        raise ValueError(f"文件不存在: {rel}")
    try:
        import versions
        versions.snapshot(abs_path)   # 删除前快照，可从版本面板恢复
    except Exception:
        pass
    os.remove(abs_path)
    for side in (abs_path + ".comfy-task.json",):
        if os.path.isfile(side):
            try: os.remove(side)
            except OSError: pass
    index.setdefault(zone, {}).pop(asset_id, None)
    idx_path = os.path.join(os.path.abspath(str(project)), "素材", "素材图.json")
    if os.path.isfile(idx_path):
        try:
            import versions
            versions.snapshot(idx_path)
        except Exception:
            pass
    with open(idx_path, "w", encoding="utf-8") as fh:
        json.dump(index, fh, ensure_ascii=False, indent=1)
    return rel


def collect_asset_image_plan(project, kind="all", asset_id=None, vendor_id="",
                             strict_dependencies=False, asset_refs=None):
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
            if asset_refs is not None and current_ref not in asset_refs:
                continue
            reference_tokens = _derived_reference_tokens(index, item, current_ref)
            refs = _derived_refs(proj, index, item, current_ref)
            missing_refs = [ref for ref in reference_tokens if not _ref_path(proj, index, ref)]
            from visual_asset_prompt import visual_contract
            visual = visual_contract(item_kind, item, project=proj)
            generation_prompt = visual['subject']
            plans.append({"kind": item_kind, "id": ident, "refs": refs,
                          "reference_tokens": reference_tokens,
                          "missing_refs": missing_refs,
                          "visual_validation": visual,
                          "can_generate": bool(generation_prompt) and visual['ready']})
    if asset_refs is not None:
        missing = set(asset_refs) - {f"@{p['kind']}:{p['id']}" for p in plans}
        if missing:
            raise ValueError("素材不存在或不在生成范围：" + "、".join(sorted(missing)))
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
            dependency = by_ref.get(str(ref).split('#', 1)[0])
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
            str(ref).split('#', 1)[0] in planned_refs for ref in plan["reference_tokens"]
        )
        plan["mode"] = asset_image_mode(vendor_id, plan["refs"], has_ref)
        # 旧本地生成保持“缺父图可降级”的口径；浏览器自动执行采用严格模式，
        # 防止身份敏感派生素材在没有实际父图时退化为文生图。
        plan["can_execute"] = plan['can_generate'] and not (strict_dependencies and bool(plan["missing_refs"]))
        plan["execution_state"] = ('waiting_settings' if not plan['can_generate'] else
                                   'ready' if plan['can_execute'] else 'waiting_dependencies')
    return ordered


def plan_waves(plans):
    """把 collect_asset_image_plan 的线性拓扑序压成「依赖深度相同的分波」。

    波内互不依赖 → 可并发；子图（parent_ref/derived_from）一定排在父资产之后的波，
    等父图落盘后才会去取参考图。父资产不在本批次（已生成/被 --id 过滤）时按 0 波算，
    与旧的「缺父图降级无参考」口径一致。派生链有环时退回当前深度，不死递归。
    """
    by_key = {f"{p['kind']}:{p['id']}": p for p in plans}
    memo = {}

    def depth_of(plan, stack):
        key = f"{plan['kind']}:{plan['id']}"
        if key in memo:
            return memo[key]
        if key in stack:
            return 0
        stack.add(key)
        value = 0
        for ref in plan.get("reference_tokens") or []:
            parent = by_key.get(str(ref).lstrip("@").split('#', 1)[0])
            if parent is not None and parent is not plan:
                value = max(value, depth_of(parent, stack) + 1)
        stack.discard(key)
        memo[key] = value
        return value

    grouped = {}
    for p in plans:
        grouped.setdefault(depth_of(p, set()), []).append(p)
    return [grouped[k] for k in sorted(grouped)]


def asset_image_needs_generation(path, force=False):
    """补缺模式只在目标图片不存在时生成；force 用于显式重生成。"""
    return bool(force) or not os.path.isfile(path)


def _ref_path(project, index, ref):
    """解析母图或 @kind:id#state_id 派生图的实际路径。"""
    raw = str(ref or "").strip().lstrip("@")
    if ":" not in raw:
        return ""
    kind, ident = raw.split(":", 1)
    ident, separator, state_id = ident.partition('#')
    zone = KIND_ZONES.get(kind)
    if not zone or not ident:
        return ""
    record = (index.get(zone) or {}).get(ident) if isinstance(index.get(zone), dict) else None
    if separator:
        record = (record.get('states') or {}).get(state_id) if isinstance(record, dict) else None
    rel = record.get("path") if isinstance(record, dict) else ""
    candidate = os.path.join(project, rel.replace("/", os.sep)) if rel else ""
    if candidate and os.path.isfile(candidate):
        return candidate
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        filename = ident + '__' + state_id if separator else ident
        candidate = os.path.join(project, "素材", zone, filename + ext)
        if os.path.isfile(candidate):
            return candidate
    return ""


def _derived_reference_tokens(index, item, current_ref=""):
    """明确生成来源优先；未单独指定时跟随展示母图，不递归注入展示祖先。"""
    exact_source = str(item.get('derived_from') or '').strip()
    if exact_source:
        return [exact_source] if exact_source != current_ref else []
    roots = []
    parent = str(item.get('parent_ref') or '').strip()
    if parent and not (item.get('kind') == '显现/特效' and parent.startswith('@character:')):
        roots.append(parent)
    # 场景中明确关联的常驻主体仍可作为辅助参考，人物与道具不隐式互拉图片。
    if item.get('kind') == 'scene':
        roots.extend(str(ref) for ref in item.get('related_refs') or [] if ref)
    return list(dict.fromkeys(ref for ref in roots if ref and ref != current_ref))


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
    # 不再按"有 api_key 优先"排序：local-comfyui 这类本地通道天然无 key，旧排序把它沉底、
    # 把付费云端顶成默认（用户选 comfyui 却跑到 RunningHub 即此）。默认取环境页启用顺序的第一家。
    if not vs:
        raise VendorError("没有已启用且配置 image 模型的厂商（环境页配置生图模型）")
    return vs[0]["id"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--kind", default="all", choices=["character", "scene", "prop", "all"])
    ap.add_argument("--id", default=None, help="只生成指定资产 id")
    ap.add_argument("--asset-ref", action="append", default=None, help="本批生成的素材引用，可重复指定")
    ap.add_argument("--vendor", default=None)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--workers", type=int, default=4,
                    help="云端厂商生图并发数（默认 4，上限 8；本机 ComfyUI 与 ChatGPT 网页队列自动降为 1）")
    ap.add_argument("--force", action="store_true", help="强制重生成已存在图片（默认只补缺）")
    ap.add_argument("--states", default="include", choices=["include", "only", "skip"],
                    help="人物状态图口径：include=母图+缺失状态图（默认）；only=只补状态图"
                         "（母图存在不重生成，缺失仍先生成——状态图要拿它当参考）；skip=只生成母图不碰状态图")
    ap.add_argument("--state-id", default=None, help="只生成指定状态 id（配合 --states only 使用）")
    a = ap.parse_args()
    proj = os.path.abspath(a.project)
    if not os.path.isdir(proj):
        print(f"[错误] 项目不存在: {proj}"); sys.exit(1)
    set_billing_project(os.path.basename(proj))
    out_root = os.path.join(proj, "素材")
    # 画幅统一走项目规格（brief.aspect_ratio，缺省 16:9）：母图与状态图共用，提前解析
    from brief import aspect_ratio_of
    from create_media import image_size_for_aspect, image_ratio_for_aspect
    _aspect = aspect_ratio_of(proj)
    os.makedirs(out_root, exist_ok=True)
    idx_path = os.path.join(out_root, "素材图.json")
    index = load_asset_index(proj)
    cli = VendorClient(pick_vendor(a.vendor))
    cli.billing_project = os.path.basename(proj)
    edit_model = (cli.models or {}).get("image_edit") or "未配置"
    print(f"[信息] 生图厂商: {cli.id} / 生图 {cli.model('image')} / 改图 {edit_model}")
    try:
        edit_ref_limit = reference_limit(cli.id, edit_model, "image_edit", getattr(cli, "cfg", None))
    except Exception:
        edit_ref_limit = 3
    kinds = list(KINDS) if a.kind == "all" else [a.kind]
    plans = collect_asset_image_plan(proj, a.kind, a.id, cli.id, asset_refs=a.asset_ref)
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
    fail = []
    def do_plan(plan):
        if a.states == "skip":
            plan["skip_states"] = True
        kind = plan["kind"]
        src, key, pfield, zone = KINDS[kind]
        it = source_items.get((kind, plan["id"]))
        if not it:
            return
        index.setdefault(zone, {})
        aid = str(it.get("id") or "").strip()
        from visual_asset_prompt import require_visual_settings
        visual = require_visual_settings(kind, it, project=proj)
        prompt = visual['subject']
        if not aid or not prompt:
            if aid:
                print(f"[跳过] {zone}/{aid} 缺少可用外观设定，请在角色设定或视觉素材中补充")
            return
        prompt, guard_notes = _gender_guard(it, prompt)
        for note in guard_notes:
            print(f"[告警] {zone}/{aid} {note}")
        # 剔空值废词："性别不明"对生图无信息量（异兽/无实体角色本就不需要性别）
        prompt = drop_gender_placeholder(prompt)
        # 资产级画风覆盖（资产档案 style 字段）优先于项目生图风格
        asset_style = str(it.get("style") or "").strip() or None
        # E10 冻结：记录本资产实际注入的 image skill 快照（id/name/正文 sha 前12位）。
        # style_prompt 自由文本优先于 skill（此时 skill 未注入，记空不写）；
        # 资产级 style 覆盖优先、空则回落项目显式选择，与 resolve_asset_style_text 同口径。
        asset_skill_snap = ({} if str(it.get("style_prompt") or "").strip()
                            else skill_lib.skill_snapshot_for(proj, ["image"],
                                                              overrides={"image": asset_style or ""}))
        # 三层组装唯一入口（母图/状态图同路）：外观事实 → 画风层 → 类别硬约束；负面=全局基础 ∪ skill 定制
        prompt, negative = skill_lib.compose_asset_image_prompt(
            proj, prompt, skill_id=asset_style, kind=generation_kind(kind, it), style_prompt=it.get("style_prompt"))
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
        # 索引条目整块重写会连带丢掉已归档的 states 子表（图还在盘上，但分镜再也选不到
        # 「反派期/盟友期」这类状态图）——先摘出来，两处写入都带回去。
        _kept_states = (index.get(zone, {}).get(aid) or {}).get("states")
        _kept_states = _kept_states if isinstance(_kept_states, dict) and _kept_states else None
        mother_needed = asset_image_needs_generation(out, a.force)
        if (a.states == "only" or a.state_id) and os.path.isfile(out):
            mother_needed = False
        if mother_needed and any('#' in ref for ref in missing_refs):
            raise ValueError('指定的派生参考图尚未生成：' + '、'.join(missing_refs) + '；请先生成对应派生图')
        if not mother_needed:
            # 图片已存在时不得把旧像素伪装成由当前提示词/Skill 生成；当前目标单列为 desired_spec。
            index[zone][aid] = skipped_image_entry(
                index.get(zone, {}).get(aid),
                {"path": f"素材/{zone}/{aid}.png", "name": it.get("name", aid),
                 "parent_ref": it.get("parent_ref"), "relation": it.get("relation"),
                 "derived_from": it.get("derived_from"), "related_refs": it.get("related_refs") or [],
                 **({"states": _kept_states} if _kept_states else {})},
                {"prompt": prompt, "visual_source_hash": visual['source_hash'], "reference_refs": reference_tokens,
                 "skill_snapshot": asset_skill_snap},
            )
            print(f"[跳过] {zone}/{aid} 已存在（补缺模式）")
        else:
            if missing_refs:
                # 派生父图缺失不再硬失败：降级为无参考生成（父图可能尚未生成、
                # 已删除，或父资产本次生成失败）。
                print(f"[告警] {zone}/{aid} 派生母图缺失（{', '.join(missing_refs[:6])}），降级为无参考生成")
            if image_refs:
                prompt = ("以关联资产参考图作为身份、结构、材质和画风锚点，只生成当前素材本身，"
                          "不要复制参考图中的其它动作或额外对象。" + chr(10) + prompt)
            # 引用 tag：related_refs 的 @ 引用显式写进提示词（审计可读 + 模型感知关联物身份）
            related_tokens = [str(r) for r in (it.get("related_refs") or []) if str(r).strip()]
            if related_tokens and related_tokens != reference_tokens:
                prompt = _with_reference_mentions(prompt, related_tokens)
            if len(all_image_refs) > edit_ref_limit:
                print(f"[提示] {zone}/{aid} 关系参考图 {len(all_image_refs)} 张，按 {cli.id} 改图输入上限 {edit_ref_limit} 张取前 {edit_ref_limit} 张", flush=True)
            import versions as _V; _V.snapshot(out)
            try:
                m_label = mode_label(cli.id, image_mode, bool(image_refs))
                print(f"[{m_label}] {zone}/{aid}（参考图 {len(image_refs)} 张）...", flush=True)
                cli.generate_image(prompt, out, timeout=a.timeout, negative_prompt=negative,
                                    image_refs=image_refs,
                                    mode=image_mode,
                                    extra={"size": image_size_for_aspect(_aspect), "ratio": image_ratio_for_aspect(_aspect)})
                head_mask = mask_character_sheet(out) if kind == 'character' else None
                index[zone][aid] = {"path": f"素材/{zone}/{aid}.png", "prompt": prompt,
                                    "settings_revision": it.get('asset_revision', 1),
                                    "visual_source_hash": visual['source_hash'],
                                    **({'head_mask': head_mask} if head_mask else {}),
                                    "name": it.get("name", aid),
                                    "parent_ref": it.get("parent_ref"),
                                    "relation": it.get("relation"),
                                    "derived_from": it.get("derived_from"),
                                    "related_refs": it.get("related_refs") or [],
                                    "reference_refs": reference_tokens,
                                    **({"states": _kept_states} if _kept_states else {}),
                                    **({"skill_snapshot": asset_skill_snap} if asset_skill_snap else {})}
                print(f"[完成] {zone}/{aid} -> 素材/{zone}/{aid}.png")
            except Exception as e:
                fail.append(f"{zone}/{aid}: {e}")
                print(f"[失败] {zone}/{aid}: {e}")
        # ---- 场景平面图派生：场景母图就位后自动确保 plan → 底图 PNG → 素材图派生注册 ----
        # fail-soft：无厂商/生成失败只告警；注册只改内存 index，随本轮统一落盘（行尾 dump）。
        if kind == "scene" and os.path.isfile(out):
            try:
                import plan_frames as _PF
                _pr = _PF.ensure_scene_plan(proj, aid, log=print)
                if _pr.get("png"):
                    _PF.register_plan_derivative(proj, aid, _pr["png"], index=index)
            except Exception as _e:
                print(f"[告警] 场景「{aid}」平面图派生失败（不影响资产生图）: {_e}")
        # ---- 状态资产图：同一角色的剧情阶段变体（锚点+差异），文件 <aid>__<状态id>.png ----
        if plan.get("skip_states"):
            return
        states_entry = {}
        for st_item in (it.get("states") or []):
            if not isinstance(st_item, dict):
                continue
            if st_item.get('output_asset_ref'):
                if a.state_id == str(st_item.get('id')):
                    raise ValueError(state_output_notice(it, st_item))
                continue
            sid = str(st_item.get("id") or "").strip()
            if a.state_id and sid != a.state_id:
                continue
            if not sid or not (st_item.get('look_diff') or st_item.get('sheet_prompt') or st_item.get('label')):
                continue
            try:
                state_visual = require_visual_settings(kind, it, st_item, project=proj)
            except ValueError as exc:
                fail.append(f"{zone}/{aid}#{sid}: {exc}")
                print(f"[派生待修改] {exc}", flush=True)
                continue
            s_prompt = state_visual['subject']
            s_prompt, s_notes = _gender_guard(it, s_prompt)
            for note in s_notes:
                print(f"[告警] {zone}/{aid}#{sid} {note}")
            s_prompt, _s_neg = skill_lib.compose_asset_image_prompt(
                proj, s_prompt, skill_id=asset_style, kind=kind, style_prompt=it.get("style_prompt"))
            s_out = os.path.join(out_dir, f"{aid}__{sid}.png")
            if not asset_image_needs_generation(s_out, a.force):
                # 补缺跳过也要带时间锚定字段，否则一次「补齐全素材图」就把
                # episodes/camp 清零，分镜再也选不到对应阶段的形象。
                previous_state = ((_kept_states or {}).get(sid) if isinstance(_kept_states, dict) else None)
                states_entry[sid] = skipped_image_entry(
                    previous_state,
                    {"path": f"素材/{zone}/{aid}__{sid}.png", "label": st_item.get("label", sid),
                     "episodes": st_item.get("episodes") or [], "camp": st_item.get("camp", "")},
                    {"prompt": s_prompt, "visual_source_hash": state_visual['source_hash'], "reference_refs": [current_ref],
                     "skill_snapshot": asset_skill_snap},
                )
                continue
            import versions as _V2; _V2.snapshot(s_out)
            try:
                s_refs = [out] if os.path.isfile(out) else []
                s_mode = asset_image_mode(cli.id, s_refs)
                s_label = mode_label(cli.id, s_mode, bool(s_refs))
                print(f"[{s_label}-状态] {zone}/{aid}#{sid}（{st_item.get('label','')}）...", flush=True)
                cli.generate_image(s_prompt, s_out, timeout=a.timeout, negative_prompt=negative,
                                   image_refs=s_refs, mode=s_mode,
                                   extra={"size": image_size_for_aspect(_aspect), "ratio": image_ratio_for_aspect(_aspect)})
                head_mask = mask_character_sheet(s_out) if kind == 'character' else None
                states_entry[sid] = {"path": f"素材/{zone}/{aid}__{sid}.png",
                                     "settings_revision": it.get('asset_revision', 1),
                                     "visual_source_hash": state_visual['source_hash'],
                                     'head_mask': head_mask,
                                     "prompt": s_prompt, "label": st_item.get("label", sid),
                                     "episodes": st_item.get("episodes") or [],
                                     "camp": st_item.get("camp", ""),
                                     **({"skill_snapshot": asset_skill_snap} if asset_skill_snap else {})}
                print(f"[完成] {zone}/{aid}#{sid} -> {aid}__{sid}.png")
            except Exception as e:
                fail.append(f"{zone}/{aid}#{sid}: {e}")
                print(f"[失败] {zone}/{aid}#{sid}: {e}")
        if states_entry and aid in index.get(zone, {}):
            # 按状态 id 并集写回：单状态重生成不得抹掉同角色其它状态的索引
            _prev_states = index[zone][aid].get("states")
            _merged = dict(_prev_states) if isinstance(_prev_states, dict) else {}
            _merged.update(states_entry)
            index[zone][aid]["states"] = _merged
    # ---- 依赖分波执行：派生子图必须等父资产母图落盘 ----
    # 子素材（parent_ref/derived_from）拿父图当参考改图，父图还在同批次排队时
    # 文件还不存在，会被降级成文生图、身份锚点散掉——所以按依赖深度分波，
    # 只有同一波内互不依赖的资产才并发。本机 ComfyUI/ChatGPT 网页队列压回 1 路。
    from vendor_concurrency import parallel_cap
    waves = plan_waves(plans)

    def _run_one(plan):
        # 线程局部记账上下文不会由主线程继承；场景平面图等派生调用也需要项目归属。
        set_billing_project(os.path.basename(proj))
        try:
            do_plan(plan)
        except Exception as e:
            # 单张意外不拖垮整批：已经付过费的图必须留在索引里
            zone = KINDS[plan["kind"]][3]
            fail.append(f"{zone}/{plan['id']}: {e}")
            print(f"[失败] {zone}/{plan['id']} 意外中断：{e}", flush=True)
        finally:
            set_billing_project("")

    cap = parallel_cap(cli.id, a.workers)
    print(f"[信息] 生图并发 {cap} 路（厂商 {cli.id}；本机 ComfyUI 与 ChatGPT 网页队列恒为 1 路）"
          f"，{len(plans)} 项分 {len(waves)} 波执行", flush=True)
    from concurrent.futures import ThreadPoolExecutor
    for wi, batch in enumerate(waves):
        if cap <= 1 or len(batch) <= 1:
            for p in batch:
                _run_one(p)
            continue
        print(f"[并发] 第 {wi + 1}/{len(waves)} 波：{len(batch)} 项 × {min(cap, len(batch))} 路", flush=True)
        with ThreadPoolExecutor(max_workers=min(cap, len(batch))) as ex:
            list(ex.map(_run_one, batch))
    try:
        import versions as _V
        _V.snapshot(idx_path)
    except Exception:
        pass
    json.dump(index, open(idx_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if fail:
        print("[部分失败] " + "；".join(fail[:5]))
    print(f"[完成] 索引 -> {idx_path}")
    print("OUTPUT:" + out_root)
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
