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
from llm_openai import VendorClient, load_vendors, VendorError, set_billing_project
import prompt_modules as PM

# 旁白/画外音是叙述轨不是角色：统一归一为保留说话人 id "narrator"，
# 不进 actors/actor_refs/asset_refs，不参与站位与生图引用。
from narration import NARRATOR_ALIASES, is_narrator
import skill_lib
import script_repository
import story_units
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


def cmd_expand(proj, vendor, idea, eps_n, episode, allow_gaps=False):
    """创作构想 → 剧集大纲（写 分集.json，schema 与 episodes 一致，后续提取/分镜无缝衔接）；
    --episode E1 时再扩写该集为分场剧本，写 剧本/分集剧本_E1.txt 并并入 剧本.txt 尾部（或建库）。"""
    base = os.path.join(proj, "剧本")
    cli = VendorClient(pick_vendor(vendor))
    idea_path = os.path.join(base, "构想.txt")
    if idea:
        os.makedirs(base, exist_ok=True)
        try:
            import versions as _V
            _V.snapshot(idea_path)
        except Exception:
            pass
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
        sys_p, user_p = PM.outline_prompt(idea, eps_n=eps_n, style_text=skill_lib.style_for(proj, "script"), proj=proj)
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
    # E10 冻结：记录本次大纲/扩写实际注入的拆剧本 skill（id/name/正文 sha 前12位）
    data["skill_snapshot"] = skill_lib.skill_snapshot_for(proj, ["script"])
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
    # ① 第一步锚定过的项目：把该集的最小单元（传记/边界/伏笔/规则/白名单）当硬输入；
    # 未锚定项目 units_txt 为空串，提示词与改造前逐字相同。
    units_txt = story_units.units_block(proj, episode)
    if units_txt:
        print(f"[锚定注入] {episode} 吃最小单元 {len(units_txt)} 字（未锚定则不注入）", flush=True)
    sys_p, user_p = PM.expand_episode_prompt(idea, tgt, prev_s, style_text=skill_lib.style_for(proj, "script"),
                                             proj=proj, units=units_txt)
    script_txt = chat_retry(cli, [{"role": "system", "content": sys_p}, {"role": "user", "content": user_p}],
                            max_tokens=6000).strip()
    marks = re.findall(r"【缺口[:：]\s*([^】]{1,80})】", script_txt)
    if marks:
        print("[缺口] 正文自报缺实体（回第一步把名册补上，别在正文里造）：", flush=True)
        for m in marks:
            print(f"  - {m}", flush=True)
    gaps = story_units.gap_report(proj, episode, script_txt)
    if gaps.get("blocking"):
        print("[缺口] 正文引用了名册之外的人物/场景（① 第一步未登记）：", flush=True)
        for s in gaps.get("missing_scenes") or []:
            print(f"  - 场景「{s['name']}」", flush=True)
        for s in gaps.get("missing_speakers") or []:
            print(f"  - 说话人「{s['name']}」← {s['line']}", flush=True)
        if not allow_gaps:
            print("[中止] 本集正文未落盘。补法：先到 ① 把上述实体登记进名册再重写；"
                  "确实要先留稿用 --allow-gaps 强行落盘。", flush=True)
            sys.exit(2)
        print("[警告] --allow-gaps 已放行，缺口实体未入档，后续 ②③ 不会认它们。", flush=True)
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


def _ep_num(ep_id):
    m = re.match(r"^E(\d+)$", str(ep_id or ""))
    return int(m.group(1)) if m else None


def cmd_units(proj, vendor, eps_n=0, arc_size=6, do_anchor=False, stage="all"):
    """① 第一步：剧本/一句话构想 → 全剧最小单元 → 落盘 → 可选锚定。

    U1 剧情骨架（premise/rules/taboos/分段/推演/实体名册/分集加厚条目/埋线/钩子）
    U2 设定层（人物传记五件套/场景空间限制与动作位/道具使用边界/状态派生）
    集数多时按分段并批出集，防一次输出截断；每批都带"已生成摘要 + 已埋未收的线"。
    stage=story|entity|all：entity 只续跑设定层（补齐缺项用，不重跑骨架，省一次大输出）。
    """
    base = os.path.join(proj, "剧本")
    idea_path = os.path.join(base, "构想.txt")
    idea = open(idea_path, encoding="utf-8").read() if os.path.isfile(idea_path) else ""
    source = full_script_text(proj) or ""
    if not idea.strip() and not source.strip():
        print("[错误] 既无 剧本/构想.txt 也无剧本正文——先写构想或导入剧本"); sys.exit(1)
    if not eps_n:
        try:
            import brief as brief_mod
            eps_n = int((brief_mod.load_brief(proj) or {}).get("total_episodes") or 0)
        except Exception:
            eps_n = 0
    # 已有分集的项目：第一步只给这些集做骨架，绝不按 brief 的目标集数扩集
    #（09_仙 实跑翻过车：6 集的剧被 brief 的 15 集带着编出 E7~E15 的分段，体检 C1 才拦下）
    existing_eps = [str(e.get("id")) for e in story_units.load_units(proj)["episodes"] if e.get("id")]
    if len(existing_eps) >= 2:
        if eps_n and eps_n != len(existing_eps):
            print(f"[校正] 目标集数按现有分集收敛：{eps_n} → {len(existing_eps)}（第一步不新增集，扩集是第二步之后的事）", flush=True)
        eps_n = len(existing_eps)
    arc_size = max(1, min(arc_size, eps_n or arc_size))
    # 既有资产目录：不给它看，LLM 会把"仙恩药"登记成 xianen-yao 而既有档案里是 xianen_hei_yaowan，
    # 一个人物三个 id（09_仙 首跑实测 57 个重复建档）
    known_assets = story_units.roster_catalog(proj)
    if known_assets:
        print(f"[最小单元] 既有资产 {len(known_assets)} 条进目录，要求复用 id", flush=True)
    cli = VendorClient(pick_vendor(vendor))
    st = skill_lib.project_style(proj)
    script_style = skill_lib.style_for(proj, "script") if str(st.get("script") or "").strip() else ""

    def ask(sys_user, stage=""):
        """一次结构化调用；截断/非 JSON 时换更紧的口吻重试一次，仍失败才抛可读错误。

        不再让裸 traceback 冒到 main：这一步之前已经落过盘（骨架/名册），崩得难看会让人以为整批白跑。
        """
        sys_p, user_p = sys_user
        tighten = ("上一轮输出无法解析成合法 JSON。这次只输出一个 JSON 对象：不要解释、不要 markdown 代码栏、"
                   "不要尾逗号；字段值一律短句（每条 ≤40 字），数组宁少勿长，确保 JSON 能闭合。")
        last = None
        txt = ""
        for attempt in (1, 2):
            msgs = [{"role": "system", "content": sys_p}, {"role": "user", "content": user_p}]
            if attempt == 2:
                msgs.append({"role": "assistant", "content": (txt or "")[:1500]})
                msgs.append({"role": "user", "content": tighten})
            try:
                txt = chat_retry(cli, msgs, max_tokens=12000, timeout=720, extra=FAST_THINK)
            except Exception as exc:
                last = f"调用失败：{exc}"
                continue
            try:
                data = parse_json(txt)
            except Exception as exc:
                last = f"JSON 解析失败：{exc}"
                continue
            if isinstance(data, dict) and data:
                return data
            last = "输出不是非空 JSON 对象"
        raise ValueError(f"{stage or '最小单元'} 生成失败（{last}）；已落盘的部分保留，重跑本命令会从未完成的段续算")

    # ── U1 第一批：全剧骨架（分集只出前一段，防一次输出截断）──
    if stage not in ("all", "story"):
        print(f"[最小单元] 跳过 U1（stage={stage}）：骨架已有，只续跑设定层——省一次大输出，也免得重造名册", flush=True)
    else:
        print(f"[最小单元] U1 剧情骨架（正文 {len(source)} 字 / 目标 {eps_n or '不限'} 集 / 每段 {arc_size} 集）", flush=True)
        try:
            data = ask(PM.units_story_prompt(idea, source, eps_n=eps_n, style_text=script_style, proj=proj,
                                             arc_size=arc_size, known=known_assets), "U1 骨架")
        except ValueError as exc:
            # 骨架是后面一切的底，拿不到就没法继续——给可读的中止信息，别把 traceback 甩给用户
            print(f"[中止] {exc}", flush=True)
            return {"ok": False, "incomplete": [str(exc)], "counts": {}}
        roster_out = story_units.apply_story(proj, data)
        print(f"  骨架：分段 {len(data.get('arcs') or [])} 段，实体新建 {len(roster_out['created'])} /"
              f" 沿用既有 {len(roster_out['reused'])}，本批分集 {len(data.get('episodes') or [])} 集", flush=True)

    # ── U1 后续批：按"还没被分段覆盖的集"补齐（段大小=arc_size）──
    # 判据不能用"分段区间里缺不缺集"：LLM 常只回第一段，其余段整段不存在，
    # 那样循环会以为没事干，把剩下 3/4 的集无声漏掉（09_仙 实跑即此形态）。
    failed, skipped = [], set()
    for _round in range(24 if stage in ("all", "story") else 0):
        units = story_units.load_units(proj)
        rows = sorted(units["episodes"], key=lambda x: (_ep_num(x.get("id")), str(x.get("id"))))
        have = {str(e.get("id")) for e in rows}
        arcs = [a for a in (units["outline"].get("arcs") or []) if isinstance(a, dict)]
        wanted = [str(e.get("id")) for e in rows if e.get("id")] or \
                 [f"E{n}" for n in range(1, (eps_n or arc_size) + 1)]
        arc_ids = {str(a.get("id")): a for a in arcs if a.get("id")}

        def thickened(e):
            """算"已加厚"：arc_id 必须真的存在且本集落在该段区间内——
            否则就是分段被后批覆写过，得重新补那一段（09_仙 实测形态）。"""
            arc = arc_ids.get(str(e.get("arc_id") or ""))
            if not arc:
                return False
            lo, hi = _ep_num(arc.get("ep_from")), _ep_num(arc.get("ep_to"))
            here = _ep_num(e.get("id"))
            return lo is not None and hi is not None and here is not None and lo <= here <= hi

        missing = [i for i in wanted
                   if not thickened(next((e for e in rows if str(e.get("id")) == i), {"id": i}))]
        if not missing:
            break
        chunk = missing[:arc_size]
        head = _ep_num(chunk[0])
        arc = next((a for a in arcs
                    if _ep_num(a.get("ep_from")) is not None and _ep_num(a.get("ep_to")) is not None
                    and _ep_num(a.get("ep_from")) <= head <= _ep_num(a.get("ep_to"))),
                   {"id": f"TAIL-{chunk[0]}", "ep_from": chunk[0], "ep_to": chunk[-1],
                    "goal": "补齐未被任何分段覆盖的集（分段表本身要连着补上）"})
        tag = str(arc.get("id"))
        if tag in skipped:
            break
        print(f"[最小单元] U1 补批 {tag}（还有 {len(missing)} 集未加厚，本批 {len(chunk)} 集："
              f"{'、'.join(chunk[:8])}{'…' if len(chunk) > 8 else ''}）", flush=True)
        prev_rows = [e for e in rows if str(e.get("id")) in set(wanted) and e.get("arc_id")][-arc_size:]
        prev_summary = "\n".join(f"- {e.get('id')} {e.get('title', '')}：{str(e.get('summary') or '')[:60]}"
                                 for e in prev_rows)
        try:
            data = ask(PM.units_story_prompt(idea, source, eps_n=eps_n, arc=arc, prev_summary=prev_summary,
                                             open_threads=story_units.open_threads(proj, arc.get("ep_from")),
                                             style_text=script_style, proj=proj,
                                             known=story_units.roster_catalog(proj)), f"U1 批 {tag}")
        except ValueError as exc:
            # 单批失败不拖垮整轮：记下、这段标记跳过，继续补别的段
            failed.append(f"{tag}: {exc}")
            skipped.add(tag)
            continue
        got = story_units.apply_story(proj, data)
        print(f"  本批加厚 {len(data.get('episodes') or [])} 集（名册新建 {len(got['created'])}/沿用 {len(got['reused'])}）",
              flush=True)

    if failed:
        print(f"[最小单元] {len(failed)} 个段没补齐（已落盘的部分保留，重跑本命令只补还缺的段）：", flush=True)
        for f in failed:
            print("  ✗ " + f, flush=True)

    # ── U2：设定层（传记/边界/空间限制/状态派生），按缺项分批续跑 ──
    units = story_units.load_units(proj)
    if units["characters"] or units["scenes"] or units["props"]:
        gap_total = 0
        for _round in range(10):
            pend = story_units.pending_settings(proj, per_round=16)
            if not pend["remaining"]:
                break
            gap_total = pend["remaining"]
            batch = pend["batch"]
            print(f"[最小单元] U2 设定层：还缺 {pend['remaining']} 个实体，本批写 {len(pend['targets'])} 个"
                  f"（人物 {len(batch['characters'])}／场景 {len(batch['scenes'])}／道具 {len(batch['props'])}）",
                  flush=True)
            try:
                # 名册给全量（它们是背景），只把"本批条目"列成任务——不然模型会把没进本批的
                # 既有实体当缺口报（09_仙 实跑把主角芝靖报成"名册里没有"）
                ent = ask(PM.units_entity_prompt({"premise": units["outline"].get("premise"),
                                                  "rules": units["outline"].get("rules"),
                                                  "arcs": units["outline"].get("arcs"),
                                                  "roster": {"characters": units["characters"],
                                                             "scenes": units["scenes"], "props": units["props"]},
                                                  "episodes": units["episodes"],
                                                  "foreshadows": units["foreshadows"]},
                                                 style_text=script_style, proj=proj,
                                                 targets=pend["targets"]), "U2 设定层")
            except ValueError as exc:
                failed.append(f"U2 设定层: {exc}")
                print(f"  ✗ {exc}", flush=True)
                break
            res = story_units.apply_entities(proj, ent)
            gaps = res.get("gaps") or []
            for g in gaps[:20]:
                print(f"  [缺口] {g.get('kind')} {g.get('ref') or ''}：{g.get('need')}", flush=True)
            after = story_units.pending_settings(proj, per_round=0)["remaining"]
            print(f"  本批落设定 {res['applied']} 个，仍缺 {after} 个", flush=True)
            if after >= pend["remaining"]:
                # LLM 这轮没写进任何一个（或只报了缺口）——再跑也是白烧钱，停下来交人工
                print("[U2 停了] 这一批没能减少缺项：剩下的人名/场景请直接在 ① 卡「素材设定」里填。", flush=True)
                break
            gap_total = after
        if gap_total:
            print(f"[U2 收尾] 设定层仍缺 {gap_total} 个实体（① 卡可逐条补齐）", flush=True)

    orphans = story_units.prune_unreferenced_new(proj)
    if orphans["candidates"]:
        print(f"[孤儿建档] 第一步新建却没有任何集/伏笔引用 {len(orphans['candidates'])} 个（不自动删）："
              + "、".join(f"{o['zone']}:{o['id']}「{o['name']}」" for o in orphans["candidates"][:10])
              + ("…" if len(orphans["candidates"]) > 10 else "")
              + "。清理：python workbench/tools/story_units.py prune <项目> --apply", flush=True)

    filled = story_units.fill_refs_from_text(proj, apply_changes=True)
    if filled["episodes"]:
        print(f"[补引用] 从正文反推填了 {len(filled['episodes'])} 集的 cast/scene/prop 引用"
              f"（加厚条目只覆盖到 LLM 想到的那几个，不补反查索引会是瞎的）", flush=True)
    synced = story_units.sync_thread_claims(proj)
    if synced["claimed"]:
        print(f"[对账] 按埋线表回填分集认领 {synced['claimed']} 处"
              + (f"，摘掉表已不认的 {synced['dropped']} 处" if synced["dropped"] else ""), flush=True)
    report = story_units.check(proj)
    print(f"[体检] {'通过' if report['ok'] else '有阻断项'} " +
          "／".join(f"{k} {v}" for k, v in report["counts"].items()), flush=True)
    for item in report["errors"][:30]:
        print(f"  ✗ {item['code']} {item['path']}：{item['message']}", flush=True)
    for item in report["warnings"][:10]:
        print(f"  ! {item['code']} {item['path']}：{item['message']}", flush=True)
    if do_anchor and failed:
        print("[未锚定] 有批次没完成，不锚定——先把缺的段补齐（重跑本命令）再锚，否则权威底是半张。", flush=True)
    elif do_anchor:
        done = story_units.anchor(proj, force=not report["ok"])
        if done.get("ok"):
            print(f"[已锚定] anchor_rev=v{done['anchor_rev']}；第二步逐集扩写将吃这套最小单元", flush=True)
        else:
            print("[未锚定] 体检仍有阻断项，修完再锚（或 --anchor --force）", flush=True)
    else:
        print("[提示] 未锚定：① 页确认各包内容后点「锚定」，或跑 units --anchor", flush=True)
    report["incomplete"] = failed
    return report


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
    from gen_asset_images import _gender_guard, drop_gender_placeholder
    for c in roster:
        sp = str(c.get("sheet_prompt") or "")
        if sp:
            filled = drop_gender_placeholder(sp, {"男": "男性", "女": "女性"}.get(str(c.get("gender")), ""))
            fixed, _ = _gender_guard(c, filled)
            if fixed != sp:
                c["sheet_prompt"] = fixed
    _dump(path, doc)
    print(f"[完成] 角色跨集校准：{n}/{len(roster)} 人更新（gender/锚点/状态轨）")
    return n


def _extract_one(proj, cli, name, text, combined, documents=None, paths=None, keys=None,
                 known=None, style_text="", ep_id=""):
    """单一资产类的提炼单元：一次 LLM 调用 → 门控 → 合并 → 落盘。

    cmd_extract 按人物→场景→道具顺序调用三次（道具依赖前两类的 @ 目录）；
    也可被 /api/extract/one 单独调用，实现最小单元重跑。
    返回更新后的 combined。
    """
    paths = paths or {"人物": os.path.join(proj, "素材", "人物.json"),
                      "场景": os.path.join(proj, "素材", "场景.json"),
                      "道具": os.path.join(proj, "素材", "道具.json")}
    keys = keys or {"人物": "characters", "场景": "scenes", "道具": "props"}
    out = paths[name]
    catalog = _asset_catalog(combined)
    if name == "人物":
        sys_p, user_p = PM.characters_prompt(text, known, style_text=style_text, catalog=catalog)
    elif name == "场景":
        sys_p, user_p = PM.scenes_prompt(text, style_text=style_text, catalog=catalog)
    else:
        sys_p, user_p = PM.props_prompt(text, style_text=style_text, catalog=catalog)
    # ① 第一步锚定过的项目：本步从"发现设定"改为"投影设定"（事实以锚定块为准，只补外观与生图字段）；
    # 未锚定项目 note 为空串，系统提示词与改造前逐字节相同。
    note = story_units.authority_note(proj, name)
    if note:
        sys_p += note
        print(f"[投影模式] {name}：注入 ① 锚定设定 {len(note)} 字，本步只补外观/生图字段", flush=True)
    txt = chat_retry(cli, [{"role": "system", "content": sys_p}, {"role": "user", "content": user_p}],
                     max_tokens=12000, timeout=420, extra=FAST_THINK)
    for gap in re.findall(r"GAP:\s*([^\"\\\n]{1,80})", txt)[:20]:
        print(f"  [回补 ①] {name} 发现锚定块缺口：{gap.strip()}", flush=True)
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
    # 落盘（documents/paths 未传时自读，供单类重跑场景）
    if documents is None:
        try:
            raw = json.load(open(out, encoding="utf-8")) if os.path.isfile(out) else {}
        except (OSError, ValueError):
            raw = {}
        documents = {name: raw if isinstance(raw, dict) else {}}
    doc = documents.setdefault(name, {})
    doc[key] = combined.get(key) or []
    doc["prompt_version"] = PM.PROMPT_VERSION
    if ep_id:
        try:
            ep_doc = json.load(open(os.path.join(proj, "剧本", "分集.json"), encoding="utf-8"))
            doc["script_rev"] = int(ep_doc.get("rev") or 0)
        except Exception:
            pass
    _dump(out, doc)
    print(f"[完成] {name} {len(doc[key])} 条 -> {os.path.basename(out)}")
    return combined


# 设定图构图描述的新旧写法：剥构图必须先剥"整段"，只 replace 关键词会把旧文案里的中文逗号
# 留在接缝上（09_仙 zhijing 的"纯白背景。，纯白背景"就是这么来的），而在已迁移过的档案上
# 再跑一次迁移会把模板叠两遍（同一份提示词里"五视图设定图"出现 2 次）。两者都是幂等缺口。
_LEGACY_LAYOUT_PREFIXES = ("正面、侧面、背面三视图，纯白背景；", "正面、侧面、背面三视图，纯白背景。",
                           "正面、侧面、背面三视图。", "正面、侧面、背面三视图，", "三视图，纯白背景；", "三视图。")


def strip_layout(text):
    """去掉新旧任一构图描述，只留外观事实；重复调用结果不变（幂等）。

    只剥构图、不动事实里的标点：旧写法把关键词 replace 掉，会在接缝留下"。，"和第二个
    "纯白背景"（09_仙 zhijing 就是这样），这里连这些残渣一起收掉。
    """
    out = str(text or "")
    out = out.replace(skill_lib.SHEET_VIEW_LAYOUT_ZH, "")
    for marker in _LEGACY_LAYOUT_PREFIXES + ("正面、侧面、背面三视图", "三视图"):
        out = out.replace(marker, "")
    out = re.sub(r"[，、；;：:]{2,}", "；", out)      # "，；" "；；" 之类折叠
    out = re.sub(r"。，|，。", "。", out)             # 拼接缝上的"。，"
    out = out.strip("；;、， \n")
    out = re.sub(r"^(纯白背景[，、；;：:。\s]*)+", "", out)   # 残在前面的背景描述（模板自带，不需重复）
    return out.strip("；;、， \n")


def regenerate_asset_prompt(proj, kind, ident, vendor=None):
    """单资产提示词重生成：只重写该资产的生图提示词（人物 sheet_prompt / 场景·道具 image_prompt），
    不动外观事实等其他字段。用新构图标准 + 现有外观事实合成，供提示词过时（如旧三视图）时单点更新。"""
    cli = VendorClient(pick_vendor(vendor))
    files = {"character": ("人物.json", "characters", "sheet_prompt"),
             "scene": ("场景.json", "scenes", "image_prompt"),
             "prop": ("道具.json", "props", "image_prompt")}
    filename, key, prompt_key = files[kind]
    doc_path = os.path.join(proj, "素材", filename)
    doc = json.load(open(doc_path, encoding="utf-8"))
    rows = doc.get(key) or []
    target = next((r for r in rows if isinstance(r, dict) and str(r.get("id")) == ident), None)
    if not target: raise ValueError(f"资产不存在：@{kind}:{ident}")
    if kind != "character":
        raise ValueError("场景/道具的 image_prompt 随外观事实生成，暂不支持单独重写；请重新提炼该类")
    old_prompt = str(target.get("sheet_prompt") or "")
    anchor = str(target.get("identity_anchor") or "").strip()
    # 外观事实取旧提示词正文（去掉新旧任一构图描述），权威模板 + 原文事实 → 新提示词
    fact = strip_layout(old_prompt)
    new_prompt = f"{skill_lib.SHEET_VIEW_LAYOUT_ZH}{fact}"
    if len(new_prompt) > 320:
        new_prompt = new_prompt[:317] + "…"
    # 状态资产的 sheet_prompt 同步刷新（含锚点原文 + 差异合成）
    updated_states = 0
    for st in target.get("states") or []:
        if not isinstance(st, dict): continue
        sp = str(st.get("sheet_prompt") or "")
        if sp and ("三视图" in sp or skill_lib.SHEET_VIEW_TITLE_ZH in sp):
            st["sheet_prompt"] = f"{skill_lib.SHEET_VIEW_LAYOUT_ZH}{strip_layout(sp)}"[:320]
            updated_states += 1
    target["sheet_prompt"] = new_prompt
    doc["prompt_version"] = PM.PROMPT_VERSION
    _dump(doc_path, doc)
    print(f"[完成] @{kind}:{ident} 提示词已更新为五视图（状态资产同步 {updated_states} 条）")
    return {"ok": True, "prompt": new_prompt, "states_updated": updated_states}


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
        combined = _extract_one(proj, cli, name, text, combined, documents, paths, keys,
                                known=known if name == "人物" else None, style_text=style_text, ep_id=ep_id)
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
    # 平面图初稿（plan v1）：每个场景资产自动出一张，已有同 scene_ref 的跳过；
    # 失败只警告不阻断——提炼主产物（三件套）已落盘，平面图可稍后在⑥平面推演页补。
    try:
        from gen_plan import draft_missing_scene_plans
        stats = draft_missing_scene_plans(proj, vendor=vendor)
        if stats.get("生成") or stats.get("失败"):
            print(f"[完成] 平面图初稿：生成 {stats['生成']} / 跳过已有 {stats['跳过']} / 失败 {stats['失败']}")
    except Exception as e:
        print(f"[警告] 平面图初稿生成失败（可稍后在⑥平面推演页生成）：{e}")
    # 索引体检：重跑提炼可能整体换 id（档案扩到 56 人、索引还留着旧 15 键），
    # 素材图.json 只增不删 → 孤儿行 + 分镜旧引用悬空。只报不改，删图/改引用归用户。
    try:
        from gen_asset_images import report_orphan_index_rows
        report_orphan_index_rows(proj)
    except Exception as e:
        print(f"[警告] 素材图索引体检未完成（可稍后在②素材页核对）：{e}")

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


def _speaker_id_map(chars):
    """台词说话人 → 角色 id 的解析表：id 与姓名优先，别名只填空位（setdefault）。

    只按姓名精确匹配会让失配的中文称呼直接当 id 落进 actors，下游 ④ 音色绑定
    （voice_assets 按 character_id 精确比）与 ⑦ V 编译会整链查不到。
    """
    mapping = {}
    for c in chars or []:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("id") or "").strip()
        if not cid:
            continue
        for key in (str(c.get("name") or "").strip(), cid):
            if key:
                mapping[key] = cid
    for c in chars or []:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("id") or "").strip()
        if not cid:
            continue
        for alias in (c.get("aliases") or []):
            alias = str(alias).strip()
            if alias:
                mapping.setdefault(alias, cid)
    return mapping


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
    units_txt = story_units.units_block(proj, ep_id)
    if units_txt:
        print(f"[锚定注入] 分镜吃 ① 最小单元 {len(units_txt)} 字（场景限制/道具边界/禁区/待埋伏笔）", flush=True)
    sys_p, user_p = PM.storyboard_prompt(text, chars, scenes, props, mood_text=text,
                                         style_text=skill_lib.style_for(proj, "storyboard"),
                                         units=units_txt)
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
    from production_studio import (default_units, validate_units, shot_list, retain_production, auto_split_units,
                                   fit_speech_budget, SPEECH_RATE, SHOT_DURATION_MIN, SHOT_DURATION_MAX)
    require_prompts(shots)
    for shot in shots:
        normalize_prompts(shot)
        from production_prompts import LLM_FIELDS
        for field in LLM_FIELDS:
            shot[field + '_source'] = 'llm'
    # 契约化：id 去重/补齐、数值兜底、词表外的值回退
    VALID = {"shot_size": ["大远景", "远景", "全景", "中景", "中近景", "近景", "特写", "大特写"],
             "camera_move": ["固定", "推", "拉", "摇", "移", "跟", "甩", "升降", "环绕", "手持", "斯坦尼康", "变焦", "轨道", "无人机", "主观"],
             "angle": ["平视", "俯视", "仰视", "鸟瞰", "虫视", "荷兰角", "过肩", "主观"],
             "cam": ["wide", "two", "cu", "ots"], "scene": ["room", "field"]}
    name2id = _speaker_id_map(chars)
    seen = set()
    for i, s in enumerate(shots, 1):
        sid = str(s.get("id") or f"S{i}")
        k = 2
        while sid in seen:
            sid = f"S{i}_{k}"; k += 1
        seen.add(sid)
        s["id"] = sid
        authored = round(max(SHOT_DURATION_MIN, min(SHOT_DURATION_MAX, float(s.get("dur") or 4))), 2)
        # 台词预算闸：对白镜不得短于"字数÷语速"，与 ⑦ 判官/时间轴共用同一个 SPEECH_RATE。
        s["dur"], need = fit_speech_budget(s, authored)
        if s["dur"] > authored + 1e-9:
            print(f"[提示] {sid} 时长按台词预算从 {authored}s 顶到 {s['dur']}s"
                  f"（{int(round(need * SPEECH_RATE))} 字 ÷ {SPEECH_RATE:g} 字/s 需要 {need}s）")
        if need > SHOT_DURATION_MAX:
            print(f"[警告] {sid} 台词按 {SPEECH_RATE:g} 字/s 需要 {need}s，已超过单镜硬顶 {SHOT_DURATION_MAX:g}s："
                  f"这句要拆到两镜或删词，否则后期字幕会中途消失（时长已顶到上限，不再自动加）")
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
           # E10 冻结：记录本次分镜实际注入的导演风格 skill（id/name/正文 sha 前12位）
           "skill_snapshot": skill_lib.skill_snapshot_for(proj, ["storyboard"]),
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
        # 契约 v3（N81）：宫格为按需人工字段，LLM 不再生成——校验只查视频提示词
        if not str(unit.get('prompt_video') or '').strip():
            raise ValueError('V 缺少视频提示词，已拒绝保存不完整分镜')
        unit['id'] = 'v-' + uuid.uuid4().hex[:12]
        members = shot_list(cfg, unit)
        unit['duration'] = sum(float(s['dur']) for s in members)
        unit['scene_ref'] = members[0].get('scene_ref', '')
        unit['source_hash'] = source_hash(members)
        for field in ('prompt_video', 'prompt_grid', 'negative', 'title'):
            if unit.get(field): unit[field + '_source'] = 'llm'
    pre = len(units)
    units = split_units_by_scene(cfg, units)
    if len(units) != pre:
        print(f"[自愈] {pre} 个 V 中存在跨场景/缺场景引用，已按场景切分为 {len(units)} 个；请到⑦核对各段汇总提示词")
    pre = len(units)
    units = auto_split_units(cfg, units)
    if len(units) != pre:
        print(f"[自愈] {pre} 个 V 中存在超时长分组，已按时长上限自动拆分为 {len(units)} 个；片段继承了原汇总提示词，请到⑦按片段核对重写")
    validate_units(cfg, units)
    cfg['video_units'] = units
    # ① 锚定的创作禁区：命中 detect 词的镜头只告警不阻断（分镜是 LLM 产物，阻断会让创作变抽奖），
    # 结果写进分镜 JSON 顶层 unit_warnings，供 ③ 页与保存回路显示。
    taboo_hits = story_units.taboo_scan(proj, shots)
    if taboo_hits:
        cfg["unit_warnings"] = taboo_hits
        for w in taboo_hits[:10]:
            where = "；".join("{}…{}".format(h["field"], h["word"]) for h in w["hits"])
            print("[禁区告警] {} 违反 {}（{}）：{}".format(w["shot_id"], w["taboo_id"], w["rule"], where), flush=True)
    else:
        cfg.pop("unit_warnings", None)
    if os.path.isfile(out):
        retain_production(json.load(open(out, encoding='utf-8')), cfg)
    _dump(out, cfg)
    print(f"[完成] 分镜 {len(shots)} 镜 -> {out}")
    print(f"[提示] 可直接进白模页渲染 / 3D 页构建 / 平面图生成")


def split_units_by_scene(cfg, units):
    """确定性自愈（N88）：LLM 分组偶发把不同/空 scene_ref 的 S 合进同一 V——validate 必拒。

    按成员 scene_ref 切分：非空同场景连段保留（继承原 V 的提示词等字段）；空 scene_ref 的
    S 自成单镜段。保序、保完整覆盖；切分后首段继承原 id。真正的漏镜/乱序仍交 validate_units
    硬报错——本函数只修"场景混组"这一类可确定性修复的问题。
    """
    sid_scene = {str(x.get("id")): str(x.get("scene_ref") or "") for x in cfg.get("shots") or []}
    fixed = []
    for unit in units:
        ids = [str(i) for i in (unit.get("shot_ids") or [])]
        scenes = [sid_scene.get(i, "") for i in ids]
        if len(ids) <= 1 or (scenes and len(set(scenes)) == 1 and scenes[0]):
            fixed.append(unit)
            continue
        runs = []
        for sid, sc in zip(ids, scenes):
            if sc and runs and runs[-1][0] == sc:
                runs[-1][1].append(sid)
            else:
                runs.append((sc, [sid]))     # 空 scene_ref 自成单镜段
        for k, (_sc, run_ids) in enumerate(runs):
            frag = {key: val for key, val in unit.items()
                    if key not in ("id", "shot_ids", "duration", "scene_ref", "source_hash")}
            import uuid
            frag["id"] = unit.get("id") if k == 0 else "v-" + uuid.uuid4().hex[:12]
            frag["shot_ids"] = run_ids
            frag["scene_ref"] = _sc
            fixed.append(frag)
    return fixed


def collect_plans(proj):
    """扫 推演/平面图_*.plan.json（plan v1）→ 组装/页面共用的清单（mtime 降序，最新在前）。
    每张：{name, scene_ref, counts(props/actors/paths 数), validate_ok, canvas_html, path, mtime}；
    scene_ref = plan 绑定的场景资产 id（--scene 生成时写入，老图没有为 None）；
    canvas_html = 对应 战略图_平面图_<名>.html 存在则填相对路径否则 None。
    validate_plan 缺失/异常时 validate_ok=None（不阻断组装）。"""
    out = []
    for fp in glob.glob(os.path.join(proj, "推演", "平面图_*.plan.json")):
        try:
            plan = json.load(open(fp, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        name = os.path.basename(fp)[len("平面图_"):-len(".plan.json")]
        counts = {k: len(plan.get(k) or []) for k in ("props", "actors", "paths")}
        try:
            from validate_plan import validate_document
            validate_ok = not validate_document(plan)["errors"]
        except Exception:
            validate_ok = None
        canvas = os.path.join(proj, "推演", f"战略图_平面图_{name}.html")
        out.append({"name": name, "scene_ref": plan.get("scene_ref") or None,
                    "counts": counts, "validate_ok": validate_ok,
                    "canvas_html": f"推演/战略图_平面图_{name}.html" if os.path.isfile(canvas) else None,
                    "path": fp, "mtime": os.path.getmtime(fp)})
    out.sort(key=lambda r: -r["mtime"])
    return out


def cmd_assemble(proj, sb_name, with_diagram=False):
    """分镜 -> 创作包 manifest：逐镜脚本 + 白模参考(预演包帧) + AI 平面图(plans) + 用户素材引用。
    with_diagram=False（09-23 起默认）：逐镜平面图（shot_diagram）不再自动生成——
    平面推演主入口已改由 AI 平面图（plan v1，manifest.plans）承接；
    想要逐镜调度图的老流程用 CLI `--with-diagram` 手动打开。"""
    jp = os.path.join(proj, "分镜", sb_name)
    if not os.path.isfile(jp):
        print(f"[错误] 分镜不存在: {jp}"); sys.exit(1)
    cfg = json.load(open(jp, encoding="utf-8"))
    try:
        from actor_pipeline import default_context, hydrate_actor_cards
        cfg["acting_context"] = hydrate_actor_cards(cfg.get("acting_context") or default_context(cfg), proj, cfg)
    except Exception as exc:
        # 演员层上下文没进包 = ⑦/图生视频拿不到已采用表演，且过去这里毫无痕迹
        print(f"[警告] 表演上下文未写入创作包（{type(exc).__name__}: {exc}）"
              f"；资料包仍会出，但逐镜提示词不含演员表演段")
    from artifact_provenance import artifact_hash, is_current
    sb_base = os.path.splitext(sb_name)[0]
    # 逐镜平面图（shot_diagram）：默认跳过（主入口已由 AI 平面图承接），--with-diagram 手动生成
    import subprocess
    diag_dir = os.path.join(proj, "推演", f"平面图_{sb_base}")
    if with_diagram:
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
    # 剧情战略图（每次重组装都刷新——它就是拍摄资料包的"主线视图"）；
    # 项目有 AI 平面图（plan v1）时按分镜 scene_ref 多数镜匹配底图（同场景 S1/S2 共用一张），
    # 无匹配回退最新一张，无 plan 维持原行为。
    plans = collect_plans(proj)
    from plan_adapt import choose_plan
    chosen = choose_plan(plans, cfg.get("shots") or [])
    strat = os.path.join(proj, "推演", f"战略图_{sb_base}.html")
    strat_cmd = [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "strategy_map.py"),
                 jp, "--out", strat]
    if chosen:
        strat_cmd += ["--plan", chosen["path"]]
        from plan_adapt import shot_scene_ref
        _shots = cfg.get("shots") or []
        _hit = sum(1 for s in _shots if shot_scene_ref(s) == str(chosen.get("scene_ref") or ""))
        if _hit:
            print(f"[信息] 战略图底图：{chosen['name']}"
                  f"（scene_ref={chosen.get('scene_ref')}，匹配 {_hit}/{len(_shots)} 镜）")
        else:
            # 曾在零匹配时照样打印 scene_ref=…，与真匹配不可区分：整本可能正用着别的场景的底图
            print(f"[警告] 战略图底图回退最新一张「{chosen['name']}」：与 {len(_shots)} 镜的 "
                  f"scene_ref 零匹配（该图 scene_ref={chosen.get('scene_ref') or '无'}），空间一致性未经校验")
    r = subprocess.run(strat_cmd, capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        print("[警告] 战略图生成失败: " + (r.stderr or r.stdout or "")[-300:])
    # 平面图帧（plan v1 底图叠加逐镜渲染）：供 ⑦ 创作生成 V 编译参考回流与推演页 V 维度展示；
    # fail-soft + 幂等（plan/分镜签名不变直接复用，见 plan_frames.ensure_plan_frames）。
    plan_frames_dir = None
    if chosen:
        try:
            from plan_frames import ensure_plan_frames, frames_dir_for_board
            if ensure_plan_frames(proj, jp, chosen["path"], log=print):
                plan_frames_dir = os.path.relpath(frames_dir_for_board(proj, sb_name), proj).replace(os.sep, "/")
        except Exception as exc:
            print(f"[警告] 平面图帧渲染失败（忽略）: {exc}")
    # 白模参考（预演包干净帧优先）
    previz = os.path.join(proj, "白模", f"预演包_{sb_base}")
    # 用户素材（拉片素材/ 目录按场景关键词引用）
    mats = [f for f in (glob.glob(os.path.join(proj, "拉片素材", "*"))) if os.path.isfile(f)]
    pkg = {"storyboard": "分镜/" + sb_name, "title": cfg.get("title", sb_base),
           "source_hash": artifact_hash(cfg, "prompt", "2"), "artifact_kind": "creation_manifest", "tool_version": "2",
           "strategy_map": f"推演/战略图_{sb_base}.html" if os.path.isfile(strat) else None,
           # 底图来源进包：matched/fallback 与命中镜数要能被 ⑥ 页面和交接物读到，
           # 零匹配回退不该只活在任务日志的 [警告] 里。
           "strategy_plan": (dict(chosen.get("choice") or {}, name=chosen.get("name"),
                                  path=chosen.get("path")) if chosen else None),
           "plan_frames_dir": plan_frames_dir,
           "plans": [{k: p[k] for k in ("name", "scene_ref", "counts", "validate_ok", "canvas_html")} for p in plans],
           "diagram_note": "逐镜平面图（shot_diagram）已转手动（assemble --with-diagram）；默认由 AI 平面图 plans 承接",
           "shots": [], "materials": [os.path.basename(m) for m in mats]}
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
                 # diagram 字段保留兼容旧包：新包默认 None（改由 plans 承接），--with-diagram 才填
                 "diagram": os.path.basename(diag) if with_diagram and os.path.isfile(diag) else None,
                 "white_ref": ref, "prompt": compiled.get("text", ""),
                 "baseline_prompt": compiled.get("baseline_prompt", ""),
                 "mode_used": compiled.get("mode_used", "baseline"),
                 "voice_notes": compiled.get("voice_notes", []),
                 "warnings": compiled.get("warnings", []),
                 "performance_used": bool(compiled.get("performance_used")),
                 "source_hash": compiled.get("source_hash", "")}
        pkg["shots"].append(entry)
    diag_files = sorted(glob.glob(os.path.join(diag_dir, "*.png"))) if with_diagram else []
    out = os.path.join(proj, "推演", f"创作包_{sb_base}", "manifest.json")
    _dump(out, pkg)
    print(f"[完成] 创作包 {len(pkg['shots'])} 镜（AI平面图 {len(plans)} 张 / 拉片素材 {len(mats)} 个"
          + (f" / 逐镜平面图 {len(diag_files)} 张" if with_diagram else "") + "）")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["units", "episodes", "expand", "overview", "extract", "extract-one", "regen-prompt",
                                    "storyboard", "assemble", "reconcile"])
    ap.add_argument("--kind", default=None, help="extract-one: 人物|场景|道具；regen-prompt: character|scene|prop")
    ap.add_argument("--id", dest="asset_id", default=None, help="regen-prompt: 资产 id")
    ap.add_argument("project")
    ap.add_argument("--vendor", default=None)
    ap.add_argument("--episode", default=None, help="集 id（缺省全本）")
    ap.add_argument("--out", default=None)
    ap.add_argument("--storyboard", dest="sb", default=None, help="assemble: 分镜文件名")
    ap.add_argument("--with-diagram", action="store_true",
                    help="assemble: 手动生成逐镜平面图（shot_diagram）；默认跳过，主入口已由 AI 平面图承接")
    ap.add_argument("--idea", default=None, help="expand: 一段话创作构想（缺省读 剧本/构想.txt）")
    ap.add_argument("--eps", type=int, default=None,
                    help="expand: 目标集数（缺省 6）；units: 目标集数（缺省读 brief.total_episodes）")
    ap.add_argument("--arc-size", dest="arc_size", type=int, default=6,
                    help="units: 每段集数（超出即分段并批生成，默认 6）")
    ap.add_argument("--anchor", action="store_true",
                    help="units: 生成完成后立即锚定（体检有阻断项则拒绝）")
    ap.add_argument("--stage", default="all", choices=["all", "story", "entity"],
                    help="units: story=只出剧情骨架，entity=只续跑设定层（补缺项用），all=两步都跑（默认）")
    ap.add_argument("--allow-gaps", dest="allow_gaps", action="store_true",
                    help="expand: 正文引用了名册外实体时也强行落盘（默认中止并上报缺口）")
    a = ap.parse_args()
    proj = os.path.abspath(a.project)
    if not os.path.isdir(proj):
        print(f"[错误] 项目不存在: {proj}"); sys.exit(1)
    set_billing_project(os.path.basename(proj))
    if a.cmd == "units":
        rep = cmd_units(proj, a.vendor, eps_n=a.eps or 0, arc_size=max(1, a.arc_size), do_anchor=a.anchor,
                       stage=a.stage)
        if rep.get("incomplete"):
            sys.exit(1)   # 任务体系据此标失败；已落盘部分保留，重跑只补缺段
    elif a.cmd == "episodes":
        cmd_episodes(proj, a.vendor, a.out or os.path.join(proj, "剧本", "分集.json"))
    elif a.cmd == "expand":
        cmd_expand(proj, a.vendor, a.idea, a.eps if a.eps is not None else 6, a.episode,
                   allow_gaps=a.allow_gaps)
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
    elif a.cmd == "extract-one":
        # 最小单元重跑：单类资产提炼（一次 LLM 调用），保持与整批提炼同一合并/门控/落盘链路
        if not a.kind or a.kind not in ("人物", "场景", "道具"):
            print("[错误] extract-one 需要 --kind 人物|场景|道具"); sys.exit(1)
        ep_doc = json.load(open(os.path.join(proj, "剧本", "分集.json"), encoding="utf-8"))
        script_rev = int(ep_doc.get("rev") or 0)
        text = _episode_text(proj, a.episode)
        if not text: print("[错误] 无剧本文本"); sys.exit(1)
        cli = VendorClient(pick_vendor(a.vendor))
        st = skill_lib.project_style(proj)
        style_text = (skill_lib.style_for(proj, "image") if str(st.get("image") or "").strip() else "").strip()
        # 单类重跑也要读全量既有档案做合并与目录（道具依赖人物/场景 @ 引用）
        base = os.path.join(proj, "素材")
        keys = {"人物": "characters", "场景": "scenes", "道具": "props"}
        combined = {"characters": [], "scenes": [], "props": []}
        for nm, fn in keys.items():
            try: raw = json.load(open(os.path.join(base, fn + ".json"), encoding="utf-8"))
            except Exception: raw = {}
            rows = (raw if isinstance(raw, dict) else {}).get(fn) or []
            combined[fn] = rows if isinstance(rows, list) else []
        relation_mod = getattr(script_repository, "asset_relations", None)
        if relation_mod is not None:
            combined, _ = relation_mod.normalize_asset_relations(combined)
        known = _asset_catalog(combined).get("characters") or None
        _extract_one(proj, cli, a.kind, text, combined,
                     known=known if a.kind == "人物" else None, style_text=style_text, ep_id=a.episode)
    elif a.cmd == "regen-prompt":
        if not a.kind or not a.asset_id:
            print("[错误] regen-prompt 需要 --kind character|scene|prop --id <资产id>"); sys.exit(1)
        regenerate_asset_prompt(proj, a.kind, a.asset_id, vendor=a.vendor)
    elif a.cmd == "storyboard":
        cmd_storyboard(proj, a.vendor, a.episode, a.out)
    elif a.cmd == "assemble":
        if not a.sb:
            print("[错误] assemble 需要 --storyboard 文件名"); sys.exit(1)
        cmd_assemble(proj, a.sb, with_diagram=a.with_diagram)


if __name__ == "__main__":
    main()
