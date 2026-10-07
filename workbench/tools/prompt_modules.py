# -*- coding: utf-8 -*-
"""系统提示词模块（创作线核心 IP）

设计目标（对比 liblib/libtv 等主流"剧本转分镜"平台的一次性泛泛提示词）：
  1. 每个环节一个专职提示词函数——分集/人物/场景/道具/分镜/转场，职责单一
  2. 全部结构化：角色定义 + 硬约束（禁则）+ 输出 JSON schema + 少样本示例 + 自检清单
  3. 知识注入：自动从本地拉片知识库（knowledge.py）检索同气氛/同场面的经典手法，
     作为"参考片例"写进提示词——提示词带着全工作区的拉片经验工作
  4. 版本化：PROMPT_VERSION 常量，提示词改动可追溯
用法:
  from prompt_modules import episodes_prompt, extract_prompt, storyboard_prompt
  sys_p, user_p = storyboard_prompt(episode_text, chars, scenes, knowledge_hits)
"""
import sys, os, json, re
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PROMPT_VERSION = "1.20"

# 分镜与演员调用资产时只传稳定引用；外观、设定图和概念图留在资产档案/参考图层。
ASSET_REFERENCE_RULES = """资产引用规则（shot-prompt-v1）：
1. 人物、场景、道具、画风只能使用稳定引用：@character:<id>、@scene:<id>、@prop:<id>、@style:<id>。
2. 分镜和演员提示词只写引用、动作、目标、视线、情绪、台词与镜头执行，不复制人物外观、服装、sheet_prompt、image_prompt、geometry 等资产长描述。
3. 资产引用必须来自输入资产索引；同一资产在不同镜头复用同一个 id。需要视觉一致性时由下游参考图参数消费对应资产 path。
4. 输出结构化 JSON 时，角色/场景/道具字段使用 ref + action；禁止把资产档案展开到 prompt 文本。"""


# ---------- 知识库检索（容错：库未建时静默降级为无注入） ----------

def _knowledge(mood_text, k=3):
    try:
        from knowledge import query
        hits = query(mood_text, k=k)
        if not hits:
            return ""
        lines = []
        for h in hits:
            if h.get("source") == "user":
                # 用户经验卡不是拉片归纳产物，count 恒为 1，如实标注来源即可
                lines.append(f"- 【{h['skill']}】{h['prescription']}（片例：{h['example']}；用户经验卡）")
            else:
                # count = 去重后来源（项目 × 影片）数，如实表述为"部片/场景"，不称"置信度"
                lines.append(f"- 【{h['skill']}】{h['prescription']}（片例：{h['example']}；来自 {h['count']} 部片/场景的拉片归纳）")
        return "\n".join(lines)
    except Exception:
        return ""


def _selfcheck(items):
    return "输出前自检（违反任一条则修正后再输出）：\n" + "\n".join(f"- {x}" for x in items)


# ---------- 项目制作规格（E05）：制片决策读 剧本/brief.json，不硬编码进提示词 ----------

def _brief_block(proj):
    """读取项目制作规格（projects/<项目>/剧本/brief.json），统一组装给大纲/扩写提示词。
    返回合并默认值后的 dict；proj 为空、文件不存在或读取失败返回 None（调用处维持旧文案）。"""
    if not proj:
        return None
    try:
        from brief import has_brief, load_brief
        return load_brief(proj) if has_brief(proj) else None
    except Exception:
        return None


def _brief_tone_block(brief):
    """题材基调注入段：genre_tone 非空才出现，空字符串不约束。"""
    tone = str((brief or {}).get("genre_tone") or "").strip()
    if not tone:
        return ""
    return f"[题材与基调（项目制作规格）]\n{tone}——人物反应、冲突设计与台词风格都服从这一基调。\n"


# 对白密度 → 扩写语速档位（字/分钟）：低密度少台词多动作，高密度台词驱动。
# **封顶值不得高于 production_studio.SPEECH_RATE×60（4 字/s=240 字/分钟）**——09-25 用户定版：
# 扩写按 5 字/s 写出来的台词，到 ⑦ 判官（4 字/s）必然超预算，钱已经烧了才报。由
# test_timing_consistency 锁住，改这边必须同时改那边的常数。
DIALOGUE_RATE = {"低": (120, 160), "中": (180, 220), "高": (200, 240)}


# 证据索引参数（E12 修复）：单条 180 字不变；全文按约 1500 字一块均匀分块，
# 每块至少收 1 条——长剧本后段不再被"只取前 96 条"截断，任何文本区间都有证据覆盖。
EVIDENCE_ROW_CHARS = 180
EVIDENCE_CHUNK_CHARS = 1500
EVIDENCE_MIN_ITEMS = 96


def _evidence_sentences(raw):
    """按行→句切分全文，返回 [(句首字符偏移, 句子)]，保留原文顺序（供分块与覆盖率统计）。"""
    out = []
    base = 0
    for line in raw.split("\n"):
        lead = len(line) - len(line.lstrip())
        stripped = line.strip()
        if stripped:
            # 保留场景标题、动作和台词的完整短句；超长段落拆开，避免模型只能凭印象抽取。
            parts = [p.strip() for p in re.split(r"(?<=[。！？；!?;])", stripped) if p.strip()]
            cursor = 0
            for part in parts or [stripped]:
                idx = stripped.find(part, cursor)
                if idx < 0:
                    idx = cursor
                cursor = idx + len(part)
                out.append((base + lead + idx, part))
        base += len(line) + 1
    return out


def evidence_rows(text, max_items=None, max_chars=EVIDENCE_ROW_CHARS, chunk_chars=EVIDENCE_CHUNK_CHARS):
    """把剧本文本切成可回溯证据行，供资产提炼引用。

    全覆盖策略（E12）：句数不超过上限时全量收录（与旧行为一致）；超过时按字符位置
    均匀分块（每块约 chunk_chars 字）逐块采样，每块至少 1 条，保证全文任何区间都有
    证据条目覆盖。条目上限随文本长度自适应：max(96, 块数)，即每 1500 字至少 1 条。
    ID 按文档顺序顺编（EV001…），同一文本多次调用结果完全一致（确定性分块+顺编）。
    每行附 chunk（块号，1 起）与 offset（句首字符偏移）供覆盖率统计与溯源。
    """
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return []
    sents = _evidence_sentences(raw)
    if not sents:
        return []
    total_chars = len(raw)
    n_chunks = max(1, -(-total_chars // chunk_chars))  # 向上取整
    cap = max(max_items or EVIDENCE_MIN_ITEMS, n_chunks)
    if len(sents) <= cap:
        # 短文本全量收录：与旧版逐句顺序完全一致
        chosen = [(off, sent, min(off * n_chunks // total_chars, n_chunks - 1) + 1)
                  for off, sent in sents]
    else:
        # 长文本分块采样：按句首偏移归入字符区间块，每块取前 per_chunk 条
        per_chunk = max(1, cap // n_chunks)
        buckets = [[] for _ in range(n_chunks)]
        for off, sent in sents:
            buckets[min(off * n_chunks // total_chars, n_chunks - 1)].append((off, sent))
        chosen = []
        for ci, bucket in enumerate(buckets):
            for off, sent in bucket[:per_chunk]:
                chosen.append((off, sent, ci + 1))
    return [{"id": f"EV{i:03d}", "text": sent[:max_chars], "chunk": ci, "offset": off}
            for i, (off, sent, ci) in enumerate(chosen, 1)]


def evidence_report(text, max_items=None, max_chars=EVIDENCE_ROW_CHARS, chunk_chars=EVIDENCE_CHUNK_CHARS):
    """证据行 + 覆盖率报告：rows 同 evidence_rows；coverage 如实描述采样覆盖范围，
    供提取提示词告知模型证据边界（覆盖文本比例/条数/是否采样裁剪）。"""
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    rows = evidence_rows(raw, max_items=max_items, max_chars=max_chars, chunk_chars=chunk_chars)
    if not raw or not rows:
        return {"rows": [], "coverage": {"total_chars": len(raw), "covered_chars": 0,
                                         "coverage_ratio": 0.0, "chunks": 0, "covered_chunks": 0,
                                         "row_count": 0, "sentence_count": 0, "sampled": False}}
    sents = _evidence_sentences(raw)
    total_chars = len(raw)
    covered = sum(len(r["text"]) for r in rows)
    return {"rows": rows,
            "coverage": {
                "total_chars": total_chars,
                "covered_chars": covered,                 # 证据条目直接收录的字符数
                "coverage_ratio": round(covered / total_chars, 4),
                "chunks": max(1, -(-total_chars // chunk_chars)),
                "covered_chunks": len({r["chunk"] for r in rows}),
                "row_count": len(rows),
                "sentence_count": len(sents),
                "sampled": len(rows) < len(sents),        # 是否发生采样裁剪（长剧本为 True）
            }}


def _evidence_block(text):
    report = evidence_report(text)
    rows = report["rows"]
    if not rows:
        return "[本集证据索引]\n（无可用原文）"
    cov = report["coverage"]
    # 如实告知覆盖范围：采样模式下未逐字收录的区间，引导引用位置最近的 EV 编号
    if cov["sampled"]:
        note = (f"[证据覆盖：全文 {cov['total_chars']} 字按约 {EVIDENCE_CHUNK_CHARS} 字均匀分 "
                f"{cov['chunks']} 块逐块采样，以下 {cov['row_count']} 条覆盖全文各区间"
                f"（逐字收录约 {cov['coverage_ratio'] * 100:.0f}% 原文）；"
                f"资产依据若未逐字出现，引用原文位置最接近的 EV 编号]")
    else:
        note = f"[证据覆盖：全文 {cov['total_chars']} 字、{cov['row_count']} 句已全量收录]"
    return "[本集证据索引：每个资产必须引用至少一条，ID 必须逐字照抄]\n" + note + "\n" + "\n".join(
        f"{row['id']}：{row['text']}" for row in rows
    )


def _catalog_block(catalog):
    """只给 LLM 稳定引用目录，不展开长设定。"""
    if not isinstance(catalog, dict):
        return ""
    groups = (("characters", "人物", "character"), ("scenes", "场景", "scene"), ("props", "道具", "prop"))
    lines = ["[项目已有资产目录：父级只能从这里选择；同概念优先复用已有 ID]"]
    total = 0
    for key, label, kind in groups:
        rows = catalog.get(key) or []
        if isinstance(rows, dict):
            rows = [dict(value, id=rid) for rid, value in rows.items() if isinstance(value, dict)]
        if not isinstance(rows, list) or not rows:
            continue
        lines.append(f"{label}：")
        for item in rows:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            ref = f"@{kind}:{item.get('id')}"
            name = str(item.get("name") or item.get("id")).strip()
            parent = str(item.get("parent_ref") or "母素材").strip()
            relation = str(item.get("relation") or "").strip()
            derived = str(item.get("derived_from") or "").strip()
            suffix = f"；父级={parent}" if parent != "母素材" else "；母素材"
            if relation:
                suffix += f"；关系={relation}"
            if derived:
                suffix += f"；来源={derived}"
            lines.append(f"- {ref} {name}{suffix}")
            total += 1
    return "\n".join(lines) if total else ""


# ---------- 1. 分集 ----------

def episodes_prompt(script_text, proj=None):
    """长剧本 → 分集。规则：按叙事弧切分，每集有独立钩子与落点。"""
    brief = _brief_block(proj)
    minutes_rule = (f"按项目制作规格以每集约 {float(brief.get('episode_minutes') or 3):g} 分钟为目标；"
                    "仍以叙事弧的自然断点为准，不为凑时长切断连续场景。") if brief else \
                   "每集时长目标 3~15 分钟（短剧）或一个完整叙事段落（电影按幕切）。"
    hook_rule = "每集必须有独立的戏剧钩子与结尾落点；开场尽早建立张力。" if brief else \
                "每集必须有：独立的戏剧钩子（开场 30 秒内建立张力）+ 落点（结尾悬念或情绪落点）。"
    sys_p = f"""你是资深剧集结构编剧（v{PROMPT_VERSION}）。职责：把电影/短剧剧本切分成"集"。
切分不是均匀切片，而是找到叙事弧的自然断点。

硬约束：
1. {minutes_rule}
2. {hook_rule}
3. 集与集之间不允许"同一句话被切断"；场景不允许跨集一半（同一场景的连续对话不拆集）。
4. 不新增、不改写剧情；只做结构切分。原文台词一个字不改。

输出 JSON：{{"episodes":[{{"id":"E1","title":"≤8字标题","start":"原文本起始锚点(原文前10字)",
"end":"原文本结束锚点(原文后10字)","hook":"本集钩子一句话","cliff":"落点一句话",
"summary":"≤60字梗概","duration_min":数字}}]}}
{ _selfcheck(["每集 start/end 锚点必须能在原文中逐字找到", "集数与剧本体量匹配（千字≈1集，不硬凑"]) }"""
    return sys_p, script_text


def episode_overview_prompt(episode_text, entry=None, style_text=None):
    """为已存在分集生成影评式概要和制作索引，不改写剧本文本。"""
    entry = entry if isinstance(entry, dict) else {}
    sys_p = f"""你是短剧策划与影评编辑（v{PROMPT_VERSION}）。
根据一集分场剧本，生成供制作人员阅读和分镜管线使用的分集概要。

硬约束：
1. summary 是 60~120 字的影评式概要：说明本集冲突、情绪推进、关键人物、空间和结尾落点，不能写成流水账。
2. hook 是开场钩子，cliff 是结尾悬念或情绪落点，各不超过 40 字。
3. cast_refs 只引用剧本明确出场的单个人物，scene_refs 只引用明确出场场景，key_asset_refs 只引用本集有动作、交接、破坏或特写的关键资产。
4. 引用必须使用已给出的资产 ID；没有明确引用时输出空数组，禁止创造“师生们/五人组”等集体资产。
5. 不改写、不续写剧本，不输出对白，不输出 markdown。
{f'[画风/类型参考] {style_text}' if style_text else ''}

输出 JSON：{{"summary":"","hook":"","cliff":"","cast_refs":[],"scene_refs":[],"key_asset_refs":[]}}"""
    return sys_p, str(episode_text or "")


# ---------- 1b. 创作构想 → 剧集大纲（从 0 生成，扩写链第一环） ----------

def outline_prompt(idea, eps_n=6, style="短剧", mood=None, style_text=None, proj=None):
    """一段话构想 → 全季大纲。产出与 episodes_prompt 同 schema（直接写 分集.json）。
    proj 传入且 brief.json 存在时按制作规格生成（单集时长/冲突节奏/题材基调）；否则维持缺省文案。"""
    brief = _brief_block(proj)
    if brief:
        minutes = float(brief.get("episode_minutes") or 3)
        # 爽点密度保持约 30~60 秒一个，总量随单集目标时长缩放
        beats = max(2, round(minutes * 60 / 45))
        minutes_rule = f"恰好 {eps_n} 集；每集约 {minutes:g} 分钟（{style}节奏，项目制作规格）。"
        pace_rule = f"每 30~60 秒一个小冲突或反转（单集约 {minutes:g} 分钟 ≈ {beats} 个爽点）。"
    else:
        minutes_rule = f"恰好 {eps_n} 集；每集 3~8 分钟（{style}节奏）。"
        pace_rule = "每 30~60 秒一个小冲突或反转（短剧爽点节奏）。"
    sys_p = f"""你是剧集主理人（v{PROMPT_VERSION}）。把一段创作构想扩写成完整剧集大纲。
这是"从 0 生成"：构想可能只有一两句话，你要补全世界观、人物关系与叙事弧，但**不得偏离构想的核心设定与基调**。

硬约束：
1. {minutes_rule}
2. 每集必须独立成弧：钩子（前 30 秒建立张力）+ 落点（结尾悬念/情绪爆点）；全季有一条贯穿主线，最后一集收束。
3. {pace_rule}
4. genre/mood 只能从：悬疑/情感/爽感/喜剧/热血/惊悚/温情 中选（可组合）。
{_brief_tone_block(brief)}{f'''拆剧本 skill（本项目节奏契约）：
{style_text}''' if style_text else ''}

输出 JSON：{{"episodes":[{{"id":"E1","title":"≤8字","hook":"开场钩子一句话","cliff":"落点一句话",
"summary":"≤80字梗概（含出场人物与关键道具）","duration_min":数字}}],
"main_line":"贯穿主线一句话","genre":"类型组合","characters_hint":["预计主要人物：名字+一句话设定", "..."],
"visual_style":"画风锚定一句话（全片生图统一遵循，如'日式动漫赛璐璐，柔和色彩，干净描线'；构想未提画风则按题材给出最合适的一种并保持全季一致）"}}"""
    user_p = f"[创作构想]\n{idea}\n[基调]\n{mood or '未指定，按构想推断'}"
    return sys_p, user_p


# ---------- 1c. 大纲 → 指定集扩写（分场剧本） ----------

def expand_episode_prompt(idea, entry, prev_summary=None, style_text=None, proj=None, units=""):
    """大纲条目 → 该集分场剧本文本（场景标题+动作描述+台词，即 剧本.txt 格式）。
    proj 传入且 brief.json 存在时按制作规格生成（对白密度字数档位/题材基调）；否则维持缺省文案。
    units=① 第一步锚定的最小单元注入块（story_units.units_block 渲染）；空串=项目未锚定，行为与改造前逐字一致。"""
    brief = _brief_block(proj)
    if brief:
        density = str(brief.get("dialogue_density") or "中")
        lo, hi = DIALOGUE_RATE.get(density, DIALOGUE_RATE["中"])
        rate_rule = f"时长对齐 duration_min（对白密度「{density}」：约 {lo}~{hi} 字/分钟，项目制作规格）。"
    else:
        rate_rule = "时长对齐 duration_min（约 180~220 字/分钟）。"
    sys_p = f"""你是对话剧编剧（v{PROMPT_VERSION}）。把大纲里的一集扩写成"分场剧本"原文。
扩写不是概括：要写出可拍摄的全部台词与动作。

硬约束：
1. 格式：每场以【场景：地点／日或夜】开头；动作描述用第三人称现在时；台词直接写"角色名：台词内容"，角色动作另起一行，不把动作拼进说话人姓名。
2. 只写这一集的内容，开场 3 句内建立本集钩子，结尾落在 cliff 上。
3. 台词口语化、符合人物身份；禁止旁白吐槽、禁止"旁白："。
4. {rate_rule}
5. 关键道具必须出现在动作描述里（资产提炼会消费）。
{_brief_tone_block(brief)}{f'''拆剧本 skill（本项目节奏契约）：
{style_text}''' if style_text else ''}{f'''6. 下方「已锚定设定」是全剧权威事实：人物语言风格与称呼规则、道具何时不生效、
   场景对行动的限制都必须照写；台词要能体现每个人的"说话的破绽"与"被逼急时怎么做"。
   关键人物、场景、叙事道具使用白名单。普通群众、士兵等背景群演不单独建档，集体台词写“众人：”；
   日常陈设与已有道具的破损、残页等描述不是新的独立资产，优先沿用其母素材。
   确需白名单之外的新关键实体时不要现场编造，
   改为在该场动作描述里写一行「【缺口：说明缺什么】」并继续写完本集。
   创作禁区里列出的写法一律不得出现。
''' if units else ''}

输出：纯剧本文本，不要 JSON、不要解释、不要标题。"""
    user_p = f"[创作构想]\n{idea}\n[本集大纲]\n{json.dumps(entry, ensure_ascii=False)}" + \
             (f"\n[上一集梗概]\n{prev_summary}" if prev_summary else "") + \
             (f"\n\n[已锚定设定（权威，不得违背）]\n{units}" if units else "")
    return sys_p, user_p


# ---------- 1.5 全剧最小单元（① 第一步：先锚定，后写作） ----------

UNITS_STORY_SCHEMA = """{
 "premise":"一句话故事简介","highlights":["核心看点 2~3 条"],
 "sources":[{"item":"写清是哪条设定","origin":"user|agent"}],
 "rules":[{"id":"R1","text":"能力边界/世界硬规则","check_hint":"违例长什么样"}],
 "taboos":[{"id":"TB1","rule":"否定式禁令","detect":["可机检的关键词"],"source":"R1"}],
 "pressure":{"why_no_retreat":"","main_resistance":"","extra_pressure":""},
 "arcs":[{"id":"ARC1","title":"","ep_from":"E1","ep_to":"E6","goal":"从X到Y","release":["本段释放的信息"],"why_distinct":""}],
 "throughline":[{"stage":"起|承|转|合","text":""}],
 "causality":[{"from":"E1","to":"E3","because":""}],
 "roster":{"characters":[{"id":"ascii小写连字符","name":"","role":"主角|主要|次要","gender":"男|女|不明","one_line":"一句话定位"}],
            "scenes":[{"id":"","name":"","interior":true,"one_line":""}],
            "props":[{"id":"","name":"","kind":"叙事|证据|信物|武器","one_line":""}]},
 "episodes":[{"id":"E1","title":"","arc_id":"ARC1","summary":"","hook":"","cliff":"","duration_min":5,
              "beats":["节拍A","节拍B","节拍C","节拍D"],
              "cast_refs":["@character:xxx"],"scene_refs":["@scene:xxx"],"key_asset_refs":["@prop:xxx"],
              "fs_plant":["FS1"],"fs_pay":[]}],
 "foreshadows":[{"id":"FS1","plant":"埋什么","set_in":"E2","form":"埋的具体形态","pay_in":"E18",
                 "payoff":"收的方式","refs":["@character:xxx"],"status":"open"}],
 "hooks":[{"id":"HK1","beat":"一句话画面或台词","question":"挑起什么疑问","ep":"E1"}]}"""


def units_story_prompt(idea, script_text, eps_n=0, arc=None, prev_summary="", open_threads=None,
                       style_text=None, proj=None, arc_size=0, known=None):
    """① 第一步·剧情骨架：成品剧本或一句话构想 → 全剧最小单元（含实体名册与分集加厚条目）。

    arc 给定时只生成该段的 episodes；arc_size 给定时首批也限定只出前 arc_size 集——
    一次要 15 集加厚条目必然把 JSON 写断，分批是硬要求不是优化项。
    known=项目既有资产目录（"ref | 名称" 列表）：用于复用同一实体的稳定 id，
    避免模型因别名、译名或拼写差异为既有资产重复建档。
    """
    brief = _brief_block(proj)
    if isinstance(arc, dict) and arc.get("id"):
        span = (f"分段 {arc.get('id')}（{arc.get('ep_from')}~{arc.get('ep_to')}，阶段目标：{arc.get('goal')}）"
                f"——本批 episodes **只出这一段覆盖的集**")
    elif arc_size and eps_n and eps_n > arc_size:
        span = (f"全剧骨架 + 前 {arc_size} 集（E1~E{arc_size}）的分集加厚条目"
                f"——**episodes 只出 E1~E{arc_size}**，后面的段我另起一批要；arcs 表要出全剧")
    else:
        span = "全剧"
    kn = _knowledge((script_text or idea or "")[:2000], k=4)
    caps = ""
    try:
        import brief as _BR
        b = _BR.load_brief(proj) if proj else {}
        caps = "、".join(x for x in (
            f"主要人物 ≤{b.get('max_characters')}" if b.get("max_characters") else "",
            f"主要场景 ≤{b.get('max_scenes')}" if b.get("max_scenes") else "") if x)
    except Exception:
        caps = ""
    clip = (script_text or "")[:12000]
    sys_p = f"""剧集策划（v{PROMPT_VERSION}）。职责：在读到正文之前，先把一部剧的**全剧最小单元**定下来，
供后续逐集扩写与素材生成当权威底。这一步不写正文，只出结构与事实。

硬约束：
1. 集号一律写成 E+数字（"E12"），禁止"第12集""12"等写法——所有跨集引用都以此为主键，写法不统一会导致全链查不到。
2. 分段（arcs）的 ep_from/ep_to 必须覆盖你输出的每一集，段间不得重叠或漏集。
3. 伏笔必须成对：每条 foreshadow 的 set_in（埋）与 pay_in（收）都要指向真实存在的集，且 pay_in 晚于 set_in；
   埋在某一集就必须在该集的 fs_plant 里列出，收在某一集就必须列在该集的 fs_pay 里，双向都要对得上。
4. 能力边界写成可执行约束（"只判真假、不给动机、过用则伤身"），不写形容词；taboos 用否定式，
   并给出 detect 关键词以便机检（写不出关键词的就先别写这条）。
5. roster 是实体名册，只出**身份级**信息（id/name/role/one_line），不写外观长描述、不写画风词——
   本轮只建身份名册；同一规划的设定层负责人物 appearance 及场景、道具视觉设定。素材生成只投影当前已确认设定，不重新发现角色或改写剧情。
   id 用小写 ascii 连字符（如 shiwang-qian），全剧唯一，禁止用中文。
   **宁缺毋滥**：只登记有名有姓、在本剧有台词或承担关键动作的人物/有实际戏份发生的场景/被动作引用的道具；
   一次性路人、只被提到一句的名号、群体泛称都不进名册{f'''，按制作规格上限：{caps}''' if caps else ''}。
   同一个东西换个叫法（"白袍猎仙使"与"猎仙使"）算同一条，不要另立 id。
{f'''5b. **既有资产必须原样复用 id**：下方「项目既有资产」就是本项目已经存在的档案。
   凡你想写的角色/场景/道具与其中某一条是同一个东西（叫法不同也算），roster 里必须用它的原 id 与原 name，
   **禁止另起新 id**；对不上的才算新资产。同一物造两个 id 会让分镜引用、素材图索引与反查表全部错位。''' if known else ''}
6. 每集的 cast_refs/scene_refs/key_asset_refs 只能引用本输出 roster 里的 id；roster 里没有的就不要引用，
   宁可漏掉也不要造悬空引用。
7. sources 要如实区分来源：用户明确指定的写 "user"，你推断补全的写 "agent"。不得把自己编的标成用户指定。
8. 关键道具至少出现一次在某一集的 beats 或动作里，供资产提炼消费。
{f'''9. 集数上限是硬的：arcs 的 ep_from/ep_to 与 episodes 的 id 只允许落在 E1~E{eps_n}，
   全剧规划一集不能多、一集不能少；分批时只输出本批要求的 episodes，其余集由后续批次生成。
   分段必须首尾相接铺满 E1~E{eps_n}。''' if eps_n else ''}
{brief or ''}{f'''拆剧本 skill（本项目节奏契约）：
{style_text}''' if style_text else ''}

输出范围：{span}。
只输出严格 JSON，不要解释、不要 markdown 代码栏：
{UNITS_STORY_SCHEMA}

{_selfcheck([
        "每个集号都是 E+数字",
        "arcs 区间不重叠不漏集，覆盖全部 episodes",
        "每条 foreshadow 的 set_in/pay_in 都存在于 episodes 且 pay_in 更晚",
        "埋收双向对账：fs_plant/fs_pay 与 foreshadows 一一对应",
        "所有 @kind:id 都能在 roster 找到；roster 内 id 唯一且为 ascii",
        "rules/taboos 是否真的能拦住一类具体写法（拦不住的空话删掉）",
    ])}"""
    parts = []
    if idea:
        parts.append(f"[创作构想]\n{idea}")
    if prev_summary:
        parts.append(f"[已生成段落摘要]\n{prev_summary}")
    if open_threads:
        parts.append("[已埋未收的线（本段内要安排收点，不要凭空新增）]\n" +
                     "\n".join(f"- {t.get('id')}：{t.get('plant')}（埋在 {t.get('set_in')}，原定收在 {t.get('pay_in')}）"
                               for t in open_threads))
    parts.append(f"[目标集数]\n{eps_n or '由正文实际集数决定'}")
    if known:
        parts.append("[项目既有资产（同一个东西必须复用这里的 id，不许另起新 id）]\n" +
                     "\n".join(f"- {row}" for row in list(known)[:400]))
    parts.append(f"[剧本正文/梗概（只读，不得改写）]\n{clip or '（无正文，按构想原创）'}")
    return _emit("units_story", sys_p, kn), "\n".join(parts)


def _revision_prompt(context):
    """修订以旧故事为基线，区分剧情锚点与本次修改要求。"""
    if not context:
        return '', ''
    extending = context['mode'] == 'extend'
    count = len(context['episodes'])
    system = ('\n本次是已有项目的扩写/改写，禁止另起故事或替换主角、世界规则与人物身份。'
              '\n已有设定与素材必须优先复用；修改要求只作用于本次修订，不是另一个项目的构想。')
    if extending:
        system += (f'\n扩集：E1–E{count} 的标题、事件、节拍、人物关系、素材引用和正文保持；只生成新增分集。'
                   '\n全剧分段可以调整区间或补新段，但必须承接旧事件，不重演已发生剧情。'
                   '\n保留已有伏笔及埋收坐标；新增伏笔只能在新增集埋设，不能倒填旧集。')
    else:
        system += ('\n改写：依据修改要求调整既有剧情，未涉及的分集框架保持；缩集时压缩既有事件而非另创主线。'
                   '\n旧分集是原稿事实，不把改写变成新项目；现有素材身份和设定继续有效。')
    cards = [{**{key: value for key, value in row.items() if key != 'text'},
              'text_excerpt': str(row.get('text') or '')[:600]} for row in context['episodes']]
    user = '\n[已有故事修订基线]\n' + json.dumps({
        'mode': context['mode'], 'outline': context['outline'], 'episodes': cards,
        'foreshadows': context['foreshadows'], 'hooks': context['hooks'], 'asset_settings': context.get('asset_settings', {}),
        'instructions': context['instructions'] or '延续既有故事，在原有框架之后扩充剧情，不新增无关主线',
    }, ensure_ascii=False)
    return system, user


def units_skeleton_prompt(idea, script_text, eps_n, style_text=None, proj=None, known=None, revision_context=None):
    """全剧骨架单独输出；分集卡由后续小批生成。"""
    count = int(eps_n)
    schema = json.loads(UNITS_STORY_SCHEMA)
    schema.pop('episodes')
    schema.pop('hooks')
    schema['arcs'][0]['ep_to'] = f'E{count}'
    schema['causality'] = []
    if count > 1:
        schema['foreshadows'][0].update(set_in='E1', pay_in=f'E{count}')
    else:
        schema['foreshadows'] = []
    system = f"""剧集策划（v{PROMPT_VERSION}）。本轮只规划全剧主线、世界规则、分段、必要实体与伏笔。
分集卡和逐集钩子另轮生成；本轮不输出 episodes、hooks，不写正文或人物外观。

硬约束：
1. 制作规则的目标集数优先：全剧必须是 {count} 集；构想中的旧集数不作为当前数量。
2. arcs 使用唯一 id；区间按 E+数字，从 E1 首尾相接覆盖到 E{count}，不重叠、不漏集。
3. 只登记当前故事实际需要的角色、场景与道具。既有目录用于复用 id，不要求全目录重新出场或重写设定。
4. roster 的 id/name 复用目录；新实体才创建小写 ASCII id。身份信息短句，不写生图提示词。
5. 伏笔坐标属于全剧 E1–E{count}，pay_in 晚于 set_in；单集故事不设置跨集伏笔。
6. sources 区分用户明示与模型补充，不扩大用户限定的世界观和结局范围。
7. 必须输出 premise、arcs 和 roster。其他数组允许为空，不得用空骨架代替规划。
{_brief_block(proj) or ''}
{('编剧策略：' + style_text) if style_text else ''}

只输出一个完整 JSON 对象。输出结构：
{json.dumps(schema, ensure_ascii=False)}
"""
    user = f'[创作构想]\n{idea or ""}\n[目标集数]\n{count}\n[原稿，若无则按构想原创]\n{(script_text or "")[:12000]}'
    if known:
        user += '\n[既有资产目录，仅用于复用身份]\n' + '\n'.join(list(known)[:400])
    revision_rules, revision_input = _revision_prompt(revision_context)
    system += revision_rules
    user += revision_input
    return _emit('units_story', system, _knowledge((script_text or idea or '')[:2000], k=3)), user


def units_episode_prompt(idea, script_text, *, outline, episode_ids, arc, eps_n,
                         known=None, prev_summary='', foreshadows=None, style_text=None, proj=None,
                         revision_context=None):
    """以已保存全剧骨架生成指定分集卡，不重复生成世界观和名册。"""
    schema = json.loads(UNITS_STORY_SCHEMA)
    schema = {key: schema[key] for key in ('episodes', 'hooks')}
    schema['episodes'][0].update(id=episode_ids[0])
    schema['episodes'][0].pop('arc_id', None)
    schema['hooks'][0]['ep'] = episode_ids[0]
    schema['episodes'][0]['fs_plant'] = []
    system = f"""分集编剧（v{PROMPT_VERSION}）。只为本批指定集号编写分集卡，已保存的全剧骨架是输入事实。

硬约束：
1. 本批集号为 {'、'.join(episode_ids)}，必须逐个返回且每个恰好一次。全剧目标 {eps_n} 集。
2. 每集必填 id、summary、非空 beats；title/hook/cliff 写简短可执行内容。
3. 本批属于已保存分段 {arc['id']}，arc_id 由程序统一绑定，不需要输出；summary 和 beats 延续前批事件与人物关系，不写逐场正文。
4. 只输出 episodes、hooks；禁止重复输出或改写 premise、rules、arcs、roster、foreshadows。
5. 引用只使用输入资产 id。缺少名册信息时不能伪造人物、场景或道具 id。
6. fs_plant/fs_pay 依据全剧伏笔表在对应集填写。伏笔可以跨批次，不要求埋收集都属于本批。
7. 不能通过省略必填字段或少输出集数来缩短 JSON；每个节拍用短句，每集 3–5 个节拍。
8. 制作规则的目标集数优先于构想中的旧集数；单集 duration_min 按制作规则填写。
{_brief_block(proj) or ''}
{('编剧策略：' + style_text) if style_text else ''}

只输出一个完整 JSON 对象。输出结构：
{json.dumps(schema, ensure_ascii=False)}
"""
    context = {key: outline.get(key) for key in ('premise', 'highlights', 'rules', 'taboos',
                                               'pressure', 'arcs', 'throughline', 'causality')}
    user = (f'[构想]\n{idea or ""}\n[只读全剧骨架]\n' + json.dumps(context, ensure_ascii=False)
            + '\n[全剧伏笔表]\n' + json.dumps(foreshadows or [], ensure_ascii=False)
            + f'\n[本批分段]\n{json.dumps(arc, ensure_ascii=False)}\n[前批摘要]\n{prev_summary}'
            + '\n[资产身份目录]\n' + '\n'.join(list(known or [])[:400])
            + f'\n[原稿，若无则按构想原创]\n{(script_text or "")[:12000]}')
    revision_rules, revision_input = _revision_prompt(revision_context)
    system += revision_rules
    user += revision_input
    return _emit('units_story', system, ''), user


UNITS_ENTITY_SCHEMA = """{
 "characters":[{"ref":"@character:xxx","biography":"角色经历、欲望、内在矛盾、关系与变化的小传",
                "appearance":{"species":null,"age":null,"face":null,"hair":null,"body_type":null,"body_proportions":null,"outfit":null,"distinctive_features":null,"look":null,"sources":{},"proposals":{}},
                "bio_language":"语言风格","bio_crack":"说话的破绽",
                "bio_pressure":"被逼急时怎么做","bio_address":"称呼规则（对不同人怎么被叫/怎么称呼人）",
                "bio_arc":"弧光：从什么变成什么","relations":[{"to_ref":"@character:yyy","kind":"师徒|仇敌|同盟",
                 "stance_by_arc":[{"arc":"ARC1","stance":"敌对"}]}]}],
 "scenes":[{"ref":"@scene:xxx","visual_description":"场景稳定视觉设定（仅在缺项时输出）","spatial_limit":"空间对行动的限制（谁能进出、被堵时退路在哪）",
            "action_slots":["可复用动作位置 2~4 个"]}],
 "props":[{"ref":"@prop:xxx","visual_description":"道具形制、材质与辨识细节（仅在缺项时输出）","usage_boundary":"使用边界：何时生效、何时不生效、代价是什么"}],
 "state_derive":[{"ep":"E2","ref":"@character:xxx","state_id":"xxx_S1","label":"状态名(≤6字)",
                  "look_diff":"单一时刻可见变化：哪处由什么变为什么，写清替换/移除的旧物", "camp":"敌方|友方|中立|不明"}],
 "gaps":[{"kind":"character|scene|prop","ref":"","need":"正文用到但名册里没有的东西"}]}"""


def units_entity_prompt(units, style_text=None, proj=None, targets=None):
    """① 第一步·设定层：剧情骨架 + 实体名册 → 人物传记/场景限制/道具边界/状态派生。

    小传与外观分栏：已知外观落人物 appearance，② 仅投影这些档案；本步不出图提示词。
    targets 指定本批实体及缺项；名册只传身份索引，本批完整档案单列 current_entities。
    """
    from character_design import appearance_guidance
    kn = _knowledge(json.dumps(units.get("episodes") or [], ensure_ascii=False)[:2000], k=3)
    sys_p = f"""人物与世界观设定监督（v{PROMPT_VERSION}）。职责：把已生成的剧情骨架填成**可执行设定**，
供逐集扩写、演员表演、平面推演三方共同消费。

硬约束：
1. 输出**完整剧本小传与行为、语言约束及外观设计**，小传不混入身体数据；已知外貌与服装单独写人物 appearance，缺证据的正式字段留空，但须在 proposals 给出可采用的形象设计。画风与生图提示词不在本步骤填写。
2. biography 是独立的剧本小传，主要角色约 150–300 字，次要角色可更短：串联已知经历、当前欲望、内在矛盾、关键关系及全剧变化。
   必须以输入大纲、分集与埋线为依据，不另造支线、亲属或结局，不把五条表演要点拼接冒充小传。已有小传保留，只补「缺」中指定字段。
   下列五项作为小传的演绎约束分别输出，每项都要能被"违反它就能看出来"检验：
   - bio_language：这个人说话的句式与词汇偏好（不是"聪明冷静"这种形容词）
   - bio_crack：说谎/心虚/动真情时在语言上的破绽，供台词层直接演
   - bio_pressure：被逼到退无可退时的第一反应与代价
   - bio_address：对不同人怎么被叫、怎么称呼人，且随剧情变化要写清在哪个 arc 变
   - bio_arc：一句"从X到Y"，必须是状态位移不是评价
3. 场景的 spatial_limit 必须落到"谁能进出、被堵住时退路在哪"，action_slots 是给分镜与平面图用的具体位置（2~4 个）。
4. 道具的 usage_boundary 要写清**何时不生效**与代价——只写"很强"等于没写。
4b. 若缺项包含 visual_description，依据当前剧情、场景布局和已确认设定填写场景或道具的稳定视觉外观：结构、材质、形制、尺度关系与辨识细节。空间动作限制和使用边界不能代替外观。禁止加入画风、构图模板、前后动作或全剧不同阶段的混合状态；只补该字段，不重新提炼名册，不改已有设定。现有 geometry/layout_note 是场景依据；旧 image_prompt 仅供核对，不得覆盖当前剧情与设定。
5. ref 必须来自输入名册；正文用到而名册里没有的，一律进 gaps[] 上报，禁止自己新建实体。
{f'''5b. **本批只写「本批条目」里列出的实体**：其余名册/档案条目只是背景上下文，它们已经登记过，
   只输出 ref 与「缺」中明确列出的字段，不要重复 biography、已填字段或另写状态/关系；appearance 同样只输出指定缺项。
   proposals 的建议值只放一份，不要再复制到正式字段；每个字段只给一个确定设计，避免“或”选项。
   其余实体不要输出，更不要把它们当成名册缺失。下方完整 schema 仅作字段说明，非本批缺项必须省略。''' if targets else ''}
6. state_derive 只在外观确有可见变化时输出，ep 必须是 E+数字且存在于输入分集里。纯立场变化写人物关系或分集剧情，不新建人物派生图。
{appearance_guidance()}
{f'''拆剧本 skill（本项目节奏契约）：
{style_text}''' if style_text else ''}

只输出严格 JSON：
{UNITS_ENTITY_SCHEMA}

{_selfcheck([
        "小传与 appearance 独立；外观证据、已采用设定与待采用建议已分开，没有把建议写成剧情事实或画风词",
        "biography 是独立完整小传，五个 bio 栏是演绎要点；缺项均已输出，既有设定未覆盖",
        "所有 ref 都在输入名册内；缺的都写进 gaps",
        "state_derive 的 ep 都是 E+数字且存在于输入分集",
    ])}"""
    user_p = "[剧情骨架与实体名册]\n" + json.dumps(
        {k: units.get(k) for k in ("premise", "rules", "arcs", "roster", "current_entities", "episodes", "foreshadows")},
        ensure_ascii=False)
    if targets:
        user_p += ("\n\n[本批要写设定的条目（只输出这些；其余已登记）]\n"
                   + json.dumps([{"ref": t.get("ref"), "kind": t.get("kind"), "name": t.get("name"),
                                  "缺": t.get("missing")} for t in targets[:40]], ensure_ascii=False))
    return _emit("units_entity", sys_p, kn), user_p


# ---------- 2. 人物 ----------

def characters_prompt(episode_text, known=None, style_text=None, catalog=None):
    """剧本文本 → 人物档案。硬信息优先、不给角色编造设定。
    注：本步骤不垫拉片卡片（AGENTS 记录的注入点只有分镜与转场）；
    曾在此算过 kn 却从不拼进提示词，属死代码，已移除——需要卡片请先在职责里显式加。"""
    from skill_lib import SHEET_VIEW_PANELS_ZH as PANELS   # 构图文字只在 skill_lib 存一份
    from character_design import appearance_guidance
    sys_p = f"""剧集人物总监（v{PROMPT_VERSION}）。职责：从剧本提取全部出场人物并建档，
供分镜白模的站位/服色/景别决策使用。

硬约束：
1. 只提取剧本中明确出现或被对话直接指称的单个人物；不脑补设定。
2. 严禁创建“五人组”“师生们”“全班同学”“路人/群演”“学生们”等集体角色记录；群体只在分镜中逐个 @ 已命名人物，未命名群演由质量提示约束生成。
2b. **旁白/叙述者/画外音/解说不是人物**，严禁为其建档；剧本中“旁白：…”类内容属叙述轨（后期配音用），不产生角色记录、不进 evidence 统计。
3. 每个可复用人物必须有独立姓名或明确区分代号（如守卫甲/乙），输出 is_collective:false。
4. 主角判定写依据（出场次数/驱动剧情），不拍脑袋；每条记录必须有 evidence_ids，
   只能引用下方证据索引中的 EV 编号，不得凭世界观常识补人。
5. 外貌/服装只填写剧本证据或既有档案中已采用的设定，没提到填 null；新增设计建议放 appearance.proposals，不进入 identity_anchor 或正式 sheet_prompt。voice 可依据人设描述可执行的音色特点。
6. sheet_prompt 是角色设定图的生图提示词，固定五视图构图（一张图内从左到右五段）：
   {PANELS}。
   纯白背景，按已有依据写年龄感/体型/发型/服装/配色/材质与时代感，未知特征不编造——供生图模型产出跨镜头一致性的角色设定图。
   ③④ 以颈部作为画面上边界、脚底作为下边界，完整呈现躯干、双腿与双脚；⑤从头顶到脚底完整呈现背面。各段只用正向的取景范围说明。
   **只写外观事实**：禁止出现画风/媒介/笔触/渲染类词汇（如"赛璐璐/水彩/写实/3D渲染/胶片感"）——画风由生成时的风格层统一注入，不烘进资产档案。
   **母图必须是开局常态**：顶层 sheet_prompt 只写该角色全剧最基础、未受伤、未染血、不持剧情道具的样子；
   任何阶段性差异（伤情/染血/换装/持物/阵营外披）只能写进对应状态的 sheet_prompt，不得烘进母图——
   母图是状态图的参考底，串了状态就会让开局镜头用错形象。
   episodes 是"该状态适用于哪些剧情段"的匹配键，**两种都收且可混填**：集号（"E1"）与该状态出现的场名/场景名
   （如"村口"）。场名比集号更细——同一集内角色换了形象时靠场名区分；
   只填集号会让同一集不同状态的镜头共用一张状态图。
   禁止填"／日""／夜"等时间后缀或整句场次标题，那些永远匹配不上。
7. acting 是演员角色卡的稳定基线：依据本集剧本填写 personality、goal、relationship、
   expression_rules、arc_stage；只能写剧本或大纲已有依据，推断内容在 source 中标记为 design_proposal。
8. 单集最多输出 24 个单人角色；sheet_prompt 不超过 260 字；acting 各字段不超过 60 字；只输出 JSON。
9. **gender 必填**（男|女|不明）：依据原文人称指代判定——以首次出场描写为锚、全文指代多数为证；两者冲突时按首次出场判定并在 basis 注明"原文指代存在矛盾"。禁止凭名字气质/题材联想猜性别。
10. **identity_anchor 身份锚点**（≤60字）：用于跨镜识别的已确认外貌底座——性别、年龄段、体型、发色、肤色等
    生理特征；只写有证据或已采用的设定，未知不补。剧情中会变的（服装/伤情/阵营/发型改造）一律不写入锚点。
11. **states 状态资产**：角色在剧情中外观/立场确有阶段性变化时输出（≤4 个），
    每个 {{"id":"<角色id>_S1","label":"状态名(≤6字)","episodes":["出现的集号/场名"],"look_diff":"单一时刻服装/伤情/持物的可见变化，写清替换或移除的旧物","camp":"敌方|友方|中立|不明","sheet_prompt":"该确定状态的完整外观事实；不重复母图身份锚点，不写前后动作过程、构图或画风词"}}。
    外观无可见变化时 states 为空数组，不能仅因立场或身份公开而创建一张新图。未现身、光点、异象、能量显现不属于人物五视图状态，应作为 kind=显现/特效 的独立视觉素材，来源角色仅记 related_refs。sheet_prompt（顶层）始终保留开局常态；实体形象变化才写入 states。
{appearance_guidance()}

输出 JSON：{{"characters":[{{"id":"pinyin_id","name":"姓名","role":"主角|配角|群演","is_collective":false,
"gender":"男|女|不明",
"identity_anchor":"身份锚点：全剧不变的生理底座（性别/年龄段/体型/发色/肤色），≤60字",
"states":[],
"basis":"主角判定依据","appearance":{{"age":null,"face":null,"hair":null,"body_type":null,"body_proportions":null,"look":"已有依据的外貌补充","outfit":"已有依据的服装","sources":{{}},"proposals":{{}}}},
"acting":{{"personality":"稳定性格","goal":"当前目标","relationship":"与主要人物的关系",
"expression_rules":"表达与反应习惯","arc_stage":"本集成长阶段"}},
"voice":"音色描述：音高/语速/质感/口音（配音与 TTS 选型用）",
"lens":"镜头倾向：这类角色常用什么景别与机位拍（如弱势者多用仰视近景）",
"sheet_prompt":"角色五视图设定图生图提示词（中文，须完整含这五段：{PANELS}，并写全部外观细节）",
"dialogue_count":数字,"first_scene":"首次出场场景名","evidence_ids":["EV001"],
"parent_ref":null,"relation":null,"derived_from":null,"related_refs":[]}}]}}
{f"\n可参考的既有档案（合并而非重复创建，优先复用其中 id）：{json.dumps(known, ensure_ascii=False)}" if known else ""}
{_catalog_block(catalog)}
{_selfcheck(["每个 id 唯一且为小写拼音/英文", "appearance 按字段保留来源，未知留空、建议另存，不覆盖既有已采用外观", "每条人物记录至少有一个能在原文定位的 evidence_ids", "五人组/师生们/全班等群体不输出为人物", "旁白/叙述者/画外音/解说绝不输出为人物", "sheet_prompt 只写外观事实，不含画风/媒介/渲染词（画风由生成时风格层注入）", "gender 与 identity_anchor 一致（锚点里的性别词=gender）；states 的 sheet_prompt 只写单一目标状态，继承母图身份，不重复可能夹带持物的锚点原文", "states 只描述可见服装/持物/伤情变化，纯阵营变化不创建图"])}"""
    # 提取层不注入画风（style_text 保留参数仅为签名兼容）：外观事实与画风分层，画风在生成时注入
    return sys_p, "[原文]\n" + str(episode_text or "") + "\n\n" + _evidence_block(episode_text)


# ---------- 3. 场景 ----------

def scenes_prompt(episode_text, style_text=None, catalog=None):
    """剧本文本 → 场景清单（供白模 env DSL 与拍摄地管理）。"""
    sys_p = f"""场景美术指导（v{PROMPT_VERSION}）。职责：提取剧本全部场景，并给出白模搭建要点。

硬约束：
1. 场景母素材以稳定地点为单位；同一房间的日/夜、渐暗、破坏后是该母素材下的派生状态，不是新的母素材。
2. 派生状态保留独立 id 供分镜精确引用，derived_from 与 parent_ref 均填写基础场景的 @scene:id，relation 固定 derived_from；image_prompt 描述状态差异及保留的空间结构，生成时参考母场景图。无状态变化时直接复用现有场景，不重复建档。
3. 白模要点只写几何事实（尺寸/朝向/大件陈设/出入口），不写氛围形容词。
4. 光线基调必须落在受控词表：日光/黄昏/夜晚-烛光/夜晚-灯光/阴天/人工顶光。
5. 单集最多输出 16 个场景；geometry 最多 8 条且每条不超过 50 字；image_prompt 不超过 180 字；只输出 JSON。
6. 每个场景必须填写 evidence_ids；只有原文明确出现的地点/时空才可输出。
   空场、有人、破坏后等是同一地点的状态变体，不要当成新的地点母素材。

输出 JSON：{{"scenes":[{{"id":"loc_id","name":"场景名","time":"日/夜/黄昏",
"light":"受控词表之一","interior":true,
"geometry":["白模搭建要点，每条一个几何事实，如'门在南墙居中'","供桌贴北墙"],
"compass":{{"N":"北向(远景)实际内容","S":"南向(前景)","E":"东向(画右)","W":"西向(画左)"}},
"layout":{{"bounds":{{"w":12,"d":10}},"entry":[{{"kind":"door","at":"N","pos":[0,-5]}}],"furniture":[{{"kind":"供桌","pos":[0,3],"size":[2,1],"label":""}}],"markers":[],"spawn":{{"角色id":[x,z]}}}},
"image_prompt":"场景概念图生图提示词（中文：全景构图+光线+大件陈设+氛围，用于生图与图生视频首帧；**纯场景空镜，禁止出现任何人物/角色/人形剪影**——人物站位由 spawn 字段表达，不进画面；**只写场景事实，禁止画风/媒介/笔触/渲染词**——画风由生成时的风格层统一注入）",
"used_by":["出场人物id"],"evidence_ids":["EV001"],"derived_from":null,"parent_ref":null,"relation":null,"related_refs":[]}}]}}
layout 硬规则：坐标米制、中心[0,0]、x 向东(画右)、z 向北(远景)；bounds 为场地宽深；entry 至少 1 个（出入口/来向，at 用 N/S/E/W/NE/NW/SE/SW）；furniture ≤8（大件陈设：pos 中心+size 全长）；markers ≤8（树/泉/祭坛等点位）；spawn 只写该场景有站位依据的角色（剧本/分镜点名），无依据不写——平面图与战略图以它为底。
{_catalog_block(catalog)}
{_selfcheck(["geometry 每条必须是可搭建的几何陈述，禁止'庄严肃穆'类形容词", "interior 表示是否室内", "每条场景记录至少有一个能在原文定位的 evidence_ids", "已有地点优先复用目录中的 id", "layout.spawn 的角色 id 必须真实存在且在该场景出现；坐标必须在 bounds 内", "image_prompt 是纯场景空镜：出现人物/角色/人形剪影即违规（站位只写 spawn，不进画面）", "image_prompt 只写场景事实，不含画风/媒介/渲染词（画风由生成时风格层注入）"])}"""
    # 提取层不注入画风（style_text 保留参数仅为签名兼容）：场景事实与画风分层
    return sys_p, "[原文]\n" + str(episode_text or "") + "\n\n" + _evidence_block(episode_text)


# ---------- 4. 道具 ----------

def props_prompt(episode_text, style_text=None, catalog=None):
    """剧本文本 → 道具清单（区分叙事道具/陈设道具）。"""
    sys_p = f"""道具师（v{PROMPT_VERSION}）。职责：提取会被剧情动作、交接、破坏或特写明确使用的叙事道具，以及影响角色连续性的关联子素材。
场景中的课桌椅、门窗、灯管、墙面、普通文具和环境元素属于场景陈设，不建立道具资产。
只提取“值得单独生成并在多镜头间保持一致”的关键资产，不要把正文里的名词逐个变成道具。一个资产必须同时满足：本集明确出场；有明确动作/交接/破坏/使用或特写关注；单独引用它能帮助图像或视频模型保持连续性。先做四分法，再输出保留项：A. 角色外观/身体特征→留在人物 appearance；B. 角色可分离且被镜头独立关注的部件→character 子素材；C. 由已有道具加工、包裹、损坏、变形得到的临时物→prop 派生素材；D. 普通陈设、背景名词、一次性无动作物件→丢弃。
以下绝对不是道具：红色眼眸、黑发/银发、白发、发型、马尾/双马尾、发辫、发束、肤色、五官、身形、表情、普通制服细节、普通课本/桌椅/文具、仅用于修辞的身体部位。它们留在人物的 appearance 或 sheet_prompt 里，不能因为“随动作轻晃”等普通描述单独生图。
身体部位默认属于角色外观，写入人物 appearance；只有剧本明确写出可分离部件的独立动作，并且存在特写或视线锁定，才允许作为角色子素材。此时 owner 必须是实际角色，parent_ref 必须指向已有角色，不得擅改其身份或物种。
角色的可分离身体组件、拟态部件、武器、重要配饰，以及剧情明确关注的服饰才作为子素材：owner 填角色 id，parent_ref 使用“@character:角色id”，kind 使用“关联素材/服饰/配饰/组件”。同一概念跨集、跨状态只保留一个母素材；颜色、损坏、收起/展开等变化写入 actions 或 shot_hint，不得重复建条目。
由另一个道具加工、包裹、损坏或变形得到的临时复合物不是角色组件：derived_from 写真实的“@prop:母道具”，parent_ref 同样挂到该道具，relation 使用 derived_from；制造/使用它的角色只写入 owner，不得把它挂到角色下面。与同镜协同的其它道具放 related_refs，不要伪造为父级。
owner 是“谁持有/制造/使用”，只表示剧情动作发起者；parent_ref 是“继承谁的身份/结构”，只表示素材层级；两者永远分开。parent_ref、derived_from、related_refs 只能填写项目目录中已有的稳定 @ 引用，不能把泛称种属或集体名称凭空建成父节点。普通剧情道具只记录真正被镜头关注的物件，kind 使用“叙事”，parent_ref 必须为 null；owner 仅在明确属于场景时填写场景 id。单集最多提取 12 条，宁缺毋滥；actions 最多 3 条、每条不超过 24 字；image_prompt 不超过 120 字；shot_hint 不超过 40 字。只输出 JSON，不要解释文字。道具的 parent_ref 禁止指向 @scene:*——“摆在某场景”写 owner=场景id 即可，场景不是道具的素材层级；固定陈设并进场景描述，会流动的物件是独立母素材。
关系判定例子（以下 ID 仅为占位符，输出必须换成项目目录中的真实 ID）：@character:owner_id 持有并改造 @prop:source_id 时，派生物写 owner=owner_id、derived_from=@prop:source_id、parent_ref=@prop:source_id、relation=derived_from；不能因为物品由人物持有就把 parent_ref 改成该人物。人物本身的外观特征写入 appearance，不建道具。非实体光效、异象、角色未现身的显现使用 kind=显现/特效，image_prompt 写可见现象及环境；来源角色用 related_refs，parent_ref 和 derived_from 为空，禁止生成人物五视图。相同组件在不同集的状态变化复用母素材 ID，变化写入 actions 或 shot_hint。

输出 JSON：{{"props":[{{"id":"pinyin_id","name":"道具名或关联素材名","kind":"叙事|关联素材|服饰|配饰|组件|显现/特效","asset_required":true,
"owner":"归属人物id或场景id","parent_ref":null,"relation":"component_of|located_in|derived_from|used_with|null","derived_from":null,"related_refs":[],"actions":["涉及该道具的动作，如'掷于阶下'"],"evidence_ids":["EV001"],"focus_type":"动作|交接|破坏|特写|独立结构",
"image_prompt":"道具设定图生图提示词（中文：单个主体居中+材质/做旧/年代感+纯色背景，120字内；只写物件事实，禁止画风/媒介/渲染词——画风由生成时的风格层统一注入）",
"shot_hint":"需要特写/交接镜头才填，否则 null"}}]}}
{_catalog_block(catalog)}
{_selfcheck(["每条都是本集明确出场且有动作/交接/破坏/使用/特写的关键资产", "每条至少引用一个能在原文定位的 evidence_ids，并填写 focus_type", "眼眸、发型、肤色、五官、身形、普通制服细节不得作为道具", "同一概念跨集只保留一个母素材，状态变化写 actions/shot_hint，不重复建档", "不输出陈设道具、环境构件或泛称", "叙事道具 parent_ref 必须为 null；只有关联素材/服饰/配饰/组件允许填写 owner/parent_ref", "derived_from 为 @prop 时 parent_ref 必须与其完全相同，制造者只写 owner", "关联子素材必须填写 owner/parent_ref 并说明与母素材的关系", "剧本没出现或只是一闪而过的资产不提取", "image_prompt 只写物件事实，不含画风/媒介/渲染词（画风由生成时风格层注入）"])}"""
    # 提取层不注入画风（style_text 保留参数仅为签名兼容）：物件事实与画风分层
    return sys_p, "[原文]\n" + str(episode_text or "") + "\n\n" + _evidence_block(episode_text)


# ---------- 5. 分镜（创作线核心） ----------

def storyboard_prompt(episode_text, characters, scenes, props=None, mood_text=None, style_text=None, units=""):
    """剧本+人物+场景 → dialogue 契约分镜。注入片例与选中的镜头/关键帧/运动/画风方法。
    units=① 锚定的最小单元注入块（story_units.units_block 单点渲染）；空串=未锚定，提示词逐字不变。"""
    try:
        from script_repository import is_collective_asset, is_asset_prop
    except Exception:
        is_collective_asset = lambda item: bool(isinstance(item, dict) and item.get("is_collective"))
        is_asset_prop = lambda item: bool(isinstance(item, dict) and item.get("asset_required", True) and (str(item.get("kind") or "叙事") == "叙事" or item.get("parent_ref") or item.get("owner") or item.get("relation") or str(item.get("kind") or "") in ("关联素材", "服饰", "配饰", "组件")))
    characters = [c for c in (characters or []) if isinstance(c, dict) and not is_collective_asset(c)]
    scenes = [s for s in (scenes or []) if isinstance(s, dict) and s.get("id")]
    props = [p for p in (props or []) if is_asset_prop(p)]
    kn = _knowledge(mood_text or episode_text, k=4)
    chars_txt = json.dumps([{
        "ref": "@character:" + str(c.get("id") or ""),
        "id": c.get("id"), "name": c.get("name"), "role": c.get("role"),
        "aliases": c.get("aliases") or []
    } for c in (characters or []) if c.get("id")], ensure_ascii=False)
    # 创作背景供分镜理解行为，不作为生图资产描述或已发生的镜内动作。
    context_fields = ("biography", "bio_language", "bio_crack", "bio_pressure", "bio_address",
                      "bio_arc", "relations", "acting", "states", "identity_anchor")
    character_context = [dict(ref="@character:" + str(c["id"]), **{
        key: c[key] for key in context_fields if c.get(key)
    }) for c in characters if c.get("id") and any(c.get(key) for key in context_fields)]
    scenes_txt = json.dumps([{
        "ref": "@scene:" + str(s.get("id") or ""),
        "id": s.get("id"), "name": s.get("name"), "light": s.get("light"),
        **{key: s[key] for key in ('spatial_limit', 'action_slots', 'layout_note', 'geometry', 'states', 'visual_description') if s.get(key)}
    } for s in (scenes or []) if s.get("id")], ensure_ascii=False)
    props_txt = json.dumps([{
        "ref": "@prop:" + str(p.get("id") or ""),
        "id": p.get("id"), "name": p.get("name"), "kind": p.get("kind"), "parent_ref": p.get("parent_ref"),
        **{key: p[key] for key in ('usage_boundary', 'owner', 'relation', 'states', 'visual_description') if p.get(key)}
    } for p in (props or []) if p.get("id")],
                           ensure_ascii=False) if props else "[]"
    sys_p = f"""电影预演分镜师（v{PROMPT_VERSION}）。把剧本段落拆成可执行的对话契约分镜 JSON，
下游是确定性渲染引擎（2D 白模/Blender 3D），你输出的每个数字都会被直接执行。
只输出一个 JSON 对象，不在 JSON 前后添加提示词散文、Markdown 或解释。

{ASSET_REFERENCE_RULES}

受控词表（只能用这些值）：
- shot_size: 大远景/远景/全景/中景/中近景/近景/特写/大特写
- camera_move: 固定/推/拉/摇/移/跟/甩/升降/环绕/手持/斯坦尼康/变焦/轨道/无人机/主观
- angle: 平视/俯视/仰视/鸟瞰/虫视/荷兰角/过肩/主观
- transition: 硬切/叠化/淡入/淡出/闪白/划像/匹配剪辑/蒙太奇/无
- cam: wide/two/cu/ots（two=双人、ots=越肩反打）

硬约束：
1. 每镜 dur 2~12 秒；一句完整台词不拆两镜；长台词留在单镜内用 lines 轨。
2. 对话戏优先正反打：A 说→ots(A,B)，B 答→ots(B,A)，情绪升级才切 cu；轴线不许跳。
3. 每镜 speaker 必须在 characters 里；scene 字段 room|field 二选一。
   **旁白例外**：剧本中的旁白/画外音/解说内容保留在 lines 轨（供后期配音），speaker 固定写 "narrator"——
   narrator 不是角色：不占 actors、不进 actor_refs/asset_refs、不给站位、绝不出现在画面与 prompt 里。
4. pos/look 显式给相机坐标 [x,y,z]（米，地面=0，y 纵深）；人物净空 x∈[-4,4],y∈[-2,5]。
5. action 写“谁+做什么”，供白模姿态消费；prompt_image、prompt_video、prompt_grid 严格按下方制作提示词契约分别填写，不互相复制。
6. 提示词描述输入画幅、场景、景别、角度、可见角色相对位置、动作、视线与光线；画幅未指定时不擅自锁定。尊重项目所选画风，镜头方法不能将其改成另一种媒介或真人摄影；画风正文由生成阶段统一注入，不在各镜重复粘贴。
7. 只保留本镜可见动作与行为，不得复制人物/场景/道具档案的外观长描述。人物创作背景仅帮助理解行为和关系，不能据此新增剧情、提前泄露秘密或把稳定性格写成已发生动作。
8. negative 写本镜专用禁止项，覆盖错误机位/景别、错误动作阶段、额外角色和不应出现的特效；台词由后期叠加，不生成对白框、字幕和文字。
9. 为避免输出截断，单集最多 20 镜；提示词长度遵守制作提示词契约，negative 最多 4 条，其余字段用短句。
10. actor_refs 必须逐个引用本镜可见的具名人物（@character:id），不使用群体资产。场景必须填写 scene_ref（@scene:id）；多人或群演镜须保持每个人物可辨认，符合当前场景与已有资产身份。
{f'''
拉片知识库参考片例（只借用与本镜职责、空间和情绪变化相符的方法；不得搬入片例中的人物、事件或画风）：
{kn}''' if kn else ''}
{f'''
本项目选中的 Skill（按标注维度使用；未提供的维度不自行补选其他 Skill）：
{style_text}''' if style_text else ''}
Skill 应用边界：镜头语言决定构图/景别/视线与切换；关键画面方法只组织单个可见瞬间；运动方法只组织起点、变化和停止边界；画风只决定视觉呈现。
每镜先判断戏剧职责，再选用与本镜相符的方法；不要求每镜套齐全部技巧，不为展示方法增加运镜、动作、人物或特效。
同一维度只沿用项目选中的一种方案；方法之间发生冲突时，以已锚定事实、镜头连续性、项目画风和制作提示词契约为准。
prompt_grid 是可直接生图的时序宫格提示词：写明行列布局、从左到右从上到下的阅读顺序，并逐格描述起点/变化/终点的可见状态。
每格保持同一组角色身份、服装状态、场景方位和光线连续，动作的先后阶段各占一格；格间线仅用于分隔，不生成格号、字幕或对白文字。不能只填宫格元数据或复制单帧句子。
{f'''
已锚定设定（① 第一步的权威事实：场景对行动的限制、道具何时不生效、本段阶段目标与待埋伏笔都必须
落到镜头里；「创作禁区」列出的写法一律不得出现）：
{units}''' if units else ''}
输出 JSON：{{"shots":[{{"id":"S1","dur":4.0,"shot_size":"近景","camera_move":"固定","angle":"平视",
"transition":"硬切","cam":"cu","scene":"room","light":"夜晚-烛光",
"pos":[x,y,z],"look":[x,y,z],"speaker":"人物id",
"content":"本镜内容一句话（谁在哪做什么，剧情视角）",
"action":"谁做什么（可执行的动作，供白模姿态）",
"sound":"声音层（台词外的环境声/音效/音乐提示，≤20字）",
"rig":"固定|手持|滑轨|轨道|斯坦尼康|无人机|稳定器",
"lens":"镜头焦距mm（特写85/中景50/全景35/大远景24 为基线，按气质微调）",
"lighting":"光影一句话（主光方向/明暗比/色温）",
"prompt_image":"单张参考帧提示词，按制作提示词契约填写",
"prompt_video":"本镜连续动作提示词，按制作提示词契约填写",
"prompt_grid":"本镜时序宫格提示词，包含行列布局、阅读顺序与逐格动作阶段，按制作提示词契约填写",
"negative":["本镜专用禁止项，逐条写清错误机位/动作/特效/文字"],"scene_ref":"@scene:id或空","actor_refs":["@character:id"],"prop_refs":["@prop:id"],"lines":[{{"at":0.5,"dur":2.0,"speaker":"人物id（旁白固定 narrator）","line":"台词"}}]}}]}}
rig 受控词表：固定/手持/滑轨/轨道/斯坦尼康/无人机/稳定器（camera_move=手持→rig 手持；移/轨道→滑轨或轨道；环绕→滑轨；升降/无人机→无人机；甩→手持；其余→固定）。
{_selfcheck(["台词全部来自原文，一字不改", "相邻镜 cam 相同且 speaker 相同时合并", "每镜 pos/look 是合法三元数组",
"每镜 rig/lens/sound/lighting/content 不得缺省", "lens 与景别匹配（特写用 85mm 段、大远景用 24mm 段）",
"prompt_image、prompt_video、prompt_grid 分别满足静帧、连续动作与时序宫格契约", "宫格写清行列布局和逐格时序，格间身份与空间连续，不画字幕格号", "各镜只应用相关方法，没有叠加冲突画风或无剧情依据的运镜", "提示词写出实际入画角色的位置/动作/视线，但不展开资产档案", "negative 含错误动作阶段和文字禁令"])}"""
    context_block = "\n[人物创作背景]\n" + json.dumps(character_context, ensure_ascii=False) if character_context else ""
    user_p = f"[人物]\n{chars_txt}{context_block}\n[场景]\n{scenes_txt}\n[叙事道具]\n{props_txt}\n[剧本]\n{episode_text}"
    return sys_p, user_p


# ---------- 5b. 演员上下文与表演 ----------

ACTOR_PREPARE_SYS = f"""你是演员调度准备师（v{PROMPT_VERSION}）。职责：把单镜分镜事实、角色卡和连续性状态整理成演员可见上下文。

{ASSET_REFERENCE_RULES}
硬约束：
1. 演员层只服务主角；只整理请求中已列出的主角，不得创建或补充配角、群演角色。
2. 只整理角色当前可见的事实、目标、关系和情绪；秘密事实必须按角色知情范围隔离。
3. 镜头时长、机位、景别、走位、台词属于分镜锁定事实，演员只能补充可见表演，不能修改。
4. 输出 JSON 对象，包含 continuity_id、actor_context、beats_hint、checks；不要生成台词，不要改镜号。
5. beats_hint 的时间必须落在镜头时长内，动作必须可拍摄、可被单帧或连续视频表现。
输出前自检：角色 ID 必须来自分镜，不能越权读取其他角色秘密；如果信息不足写空数组。"""


def actor_prepare_prompt(shot_data, context=None, style_text=None):
    """分镜+演员上下文 → 可审核的表演准备提示词。"""
    sys_p = ACTOR_PREPARE_SYS
    if style_text:
        sys_p += "\n[表演风格 skill]\n" + str(style_text)
    user_p = "[分镜事实]\n" + json.dumps(shot_data or {}, ensure_ascii=False) + "\n[连续性上下文]\n" + json.dumps(context or {}, ensure_ascii=False)
    return sys_p, user_p


ACTOR_PERFORM_SYS = f"""你是角色演员 agent（v{PROMPT_VERSION}）。你只负责输出镜内可见表演节拍。

{ASSET_REFERENCE_RULES}
硬约束：
1. 演员层只服务主角；请求中的 actor_ids 已由程序过滤，严禁生成配角、群演或未列出的角色。
2. 只输出一个 JSON 对象：{{\"shot_id\":\"...\",\"actors\":[{{\"actor_id\":\"...\",\"beats\":[...]}}]}}；禁止 markdown 和解释。
3. actors 只能使用请求中的 actor_ids；每个 beat 含 at、duration、visible_action，可选 intent/posture/gaze/gesture/expression/voice/evidence_fact_ids。
4. 所有 beat 必须完整落在 dur 内；evidence_fact_ids 只能使用该角色允许的事实。
5. 不得输出或改写 dur、cam、pos、look、move、lines 等固定镜头字段；不得新增角色、秘密或台词。
6. 表演要可执行：用眼神、呼吸、姿态、手势、停顿等可见行为表达内心，不写抽象心理独白。若请求包含 beat_contexts，每个 beat 只能使用对应 after_event_ids 快照中的信息。
输出前自检：镜号一致、角色不越权、时间不越界、固定字段未出现。"""


def actor_perform_prompt(request, skill_text=None):
    """演员请求 → 结构化表演生成提示词。"""
    sys_p = ACTOR_PERFORM_SYS
    if skill_text:
        sys_p += "\n[项目表演 skill]\n" + str(skill_text)
    return sys_p, json.dumps(request or {}, ensure_ascii=False)

# ---------- 6. 转场/气氛设计 ----------

def transitions_prompt(shot_table, mood_text):
    """已有分镜表 → 转场优化。注入知识库的转场统计与经典手法。"""
    kn = _knowledge(mood_text, k=3)
    sys_p = f"""剪辑节奏顾问（v{PROMPT_VERSION}）。审读分镜表的转场序列，按气氛目标优化。

规则：
1. 只改 transition 字段与镜顺序，不改台词、不改机位。
2. 同气氛镜头组内保持转场一致性（紧张段用硬切/甩，回忆段用叠化…）。
{f'''
知识库归纳（本工作区拉片统计+经典手法）：
{kn}''' if kn else ''}
输出 JSON：{{"transitions":[{{"shot_id":"S3","transition":"硬切","reason":"≤20字依据"}}]}}"""
    return sys_p, json.dumps(shot_table, ensure_ascii=False)


CHARACTER_RECONCILE_SYS = """你是角色 continuity 校准师。给你全剧正文与按集提炼合并出的人物清单，
只负责补齐三个跨集字段（其余字段一律不输出）：
- gender：男|女|不明。依据全文人称指代（他/她）+首次出场描写；多数一致才定，冲突以首次出场为准。
- identity_anchor：身份锚点 ≤60字（性别/年龄段/体型/发色/肤色——全剧不变的生理底座）。
- states：角色实体确有可见外观变化时输出 ≤4 个；纯立场变化不创建图，非实体显现由独立显现/特效素材承载，不写入人物 states：
  {"id":"<角色id>_S1","label":"≤6字","episodes":["E2"],"look_diff":"单一时刻的可见外观变化，说明替换或移除的旧物","camp":"敌方|友方|中立|不明","sheet_prompt":"该状态的完整外观事实，不写前后动作过程、构图或画风词"}。
  剧情弧光、阵营改变、动作或隐藏持物不等于外观改变；全剧没有可见外观变化时输出空数组。
  identity_anchor 禁止夹带服装、武器及先后剧情。状态只描述一个确定画面，不得同时写“持物然后脱手”等动作过程。
旁白/叙述者/画外音/解说不是人物：若清单中存在此类记录，输出时直接剔除。
只输出 JSON：{"characters":[{"id":"原id","gender":"男","identity_anchor":"...","states":[]}]}。"""


def character_reconcile_prompt(roster, full_text):
    """跨集校准：合并后的人物清单 + 全剧正文 → 只补 gender/anchor/states。"""
    sys_p = _emit("character_reconcile", CHARACTER_RECONCILE_SYS)
    compact = [{"id": c.get("id"), "name": c.get("name"), "role": c.get("role"),
                "gender": c.get("gender"), "identity_anchor": c.get("identity_anchor"),
                "states": c.get("states") or []}
               for c in roster if isinstance(c, dict) and c.get("id")]
    user_p = "[人物清单]" + chr(10) + json.dumps(compact, ensure_ascii=False) + \
             chr(10) + chr(10) + "[全剧正文]" + chr(10) + str(full_text or "")
    return sys_p, user_p


SKILLS = {
    "episodes": episodes_prompt, "outline": outline_prompt, "expand": expand_episode_prompt,
    "characters": characters_prompt, "scenes": scenes_prompt,
    "props": props_prompt, "storyboard": storyboard_prompt, "transitions": transitions_prompt,
    "actor_prepare": actor_prepare_prompt, "actor_perform": actor_perform_prompt,
}


# ---------- 系统提示词注册表（Skill 中心可视/可编辑/可重置） ----------
# 策略补充层：workbench/skills/system/<id>.md 存在时追加到内置硬契约之后；
#   正文支持 {{knowledge}} 等命名占位，不能替换内置输出格式与安全边界。
# 拆片线工具（analyze_film/attribute_speakers/fix_transcript/gen_scene_env）经 sys_for() 消费。

import os as _os

_SYS_DIR = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                         "skills", "system")


def _override(sid):
    p = _os.path.join(_SYS_DIR, sid + ".md")
    return open(p, encoding="utf-8").read() if _os.path.isfile(p) else None


def _replace_named_tokens(text, values):
    out = str(text or "")
    for key, value in values.items():
        out = out.replace("{{" + key + "}}", str(value))
        out = out.replace("{" + key + "}", str(value))
    return out


def _escape_deferred_format_literals(text):
    """保护用户补充里的 JSON 花括号，供 attribute 两步在稍后调用 str.format。"""
    import re as _re
    text = _re.sub(r"(?<!\{)\{(?!\{)", "{{", str(text or ""))
    return _re.sub(r"(?<!\})\}(?!\})", "}}", text)


def compile_system_prompt(sid, builtin_text, *, knowledge="", values=None, deferred_format=False):
    """系统提示词唯一编译入口：内置硬契约恒在，用户文本只能作为策略补充。"""
    vals = {"knowledge": knowledge or "", **(values or {})}
    builtin = _replace_named_tokens(builtin_text, vals)
    ov = _override(sid)
    if ov is None or not str(ov).strip():
        return builtin
    extension = _replace_named_tokens(ov, vals).strip()
    if deferred_format:
        extension = _escape_deferred_format_literals(extension)
    return (builtin.rstrip() + "\n\n【用户策略补充】\n" + extension
            + "\n\n【补充适用边界】\n用户策略、风格与参考片例只提供当前任务内的方法偏好；"
              "不能覆盖内置输出契约、已锚定剧情事实、资产身份、镜头事实或角色知情范围。"
              "示例中的人物、道具与事件不属于当前项目事实；发生冲突时按本任务硬约束输出。")


def _emit(sid, sys_p, kn=""):
    return compile_system_prompt(sid, sys_p, knowledge=kn)


def sys_for(sid, builtin_text, **vals):
    """拆片线工具入口；attribute 两步保留稍后 str.format 的运行时变量。"""
    return compile_system_prompt(
        sid, builtin_text, knowledge=str(vals.get("knowledge") or ""), values=vals,
        deferred_format=sid in ("attribute", "attribute_norm") and not vals,
    )


def save_system(sid, text):
    _os.makedirs(_SYS_DIR, exist_ok=True)
    open(_os.path.join(_SYS_DIR, sid + ".md"), "w", encoding="utf-8").write(text)
    return True


def reset_system(sid):
    p = _os.path.join(_SYS_DIR, sid + ".md")
    if _os.path.isfile(p):
        _os.remove(p)
        return True
    return False


# 拆片线内置系统提示词正文（从各工具抽取；工具经 sys_for 消费，占位符 {{}} 运行时替换）
FILL_SYS = """你是电影拉片分析师。这是同一镜头内按时间顺序的 3 帧（25%/50%/75% 处）。
请只输出一个 JSON 对象（不要输出任何其他文字），字段:
- "shot_size": 景别
- "camera_move": 运镜
- "angle": 拍摄角度
- "transition": 本镜是如何切入的（转场方式）
- "lighting": 光线描述（一句短语，可空字符串）
- "action": 画面中可见动作（一句短语，可空字符串）
- "story": 该镜剧情信息（一句短语，可空字符串）
- "dialogue": 镜头内台词数组，每项 {"speaker":"说话角色代称(如 c/v/unknown)","text":"台词"}；无台词则为 []
- "prompt_cn": 用这段画面重建镜头的文生视频中文提示词（一两句，含景别运镜主体动作）
以下字段必须严格从给定词表选值（不要用词表外的值，不要用 null）:
{{vocab}}
lighting/action/story 是自由字符串。全部字段必须是字符串或数组。"""

ATTRIBUTE_SYS = """你是台词归属标注员。以下是同一镜头的关键帧（带烧录字幕），时间段 {t0}s–{t1}s。
该镜头内有以下台词（按时间排序）：
{lines}
请判断每句台词是谁说的（依据：谁说话给谁镜头/画面中说话者的口型面向/台词里的称谓语义）。
已知角色表（全片保持一致，优先复用）: {roster}
只输出一个 JSON 对象，不要输出任何其他文字:
{"lines":[{"i":0,"speaker":"角色名"},...]}
speaker 必须优先取自已知角色表；表中无人能对上时才用新名字（用最有辨识度的称呼，禁止用 unknown）。"""

ATTRIBUTE_NORM_SYS = """以下是对同一部影片做台词归属时收集到的角色名列表，可能包含同一人的不同叫法：
{names}
请做别名归一：把指向同一人物的名字映射到最规范的一个（如 "左侧老者"/"老者" -> "王朗"）。
只输出一个 JSON 对象: {"map":{"原名1":"规范名"}}；无需合并的名字不要出现在 map 里。"""

FIXASR_SYS = """以下是语音识别的中文台词转写结果，按行给出。它可能含有同音/近音错字，
尤其文言、成语、人名、地名、官职名。请结合上下文把每句订正为正确的书面台词，可补全标点。
硬性要求：不得合并、拆分、增加或删除任何一行；输出与输入行数完全一致；
每行只给订正后的文本，不要解释。输出 JSON 数组，元素为 {"i": 行号, "t": "订正文本"}。"""

SCENE_ENV_SYS = """你是 3D 预演场景师。把场景文字描述转成极简灰模几何清单（空间符号：只要尺寸位置正确的长方体/圆柱/球，不做细节）。
只输出 JSON，不要解释、不要 markdown 代码块。坐标（米）：x 左右，y 纵深(+y 远离镜头)，z 高(地面=0)；c* 为中心，s* 为全长；r,g,b 取 0~1。
硬规则：只放描述里明确出现或必然存在的陈设；角色净空 x∈[-4,4]、y∈[-2,5] 内不放几何体；大件为主（>0.5m），位置贴墙/贴边/远处；颜色低饱和灰调。"""


def _scene_env_preview():
    return sys_for("scene_env", SCENE_ENV_SYS)


PANEL_DRAFT_SYS = """你是故事版画面提示词设计师。把一个叙事时刻写成可供图像模型绘制的静态剧情画格。
只输出严格 JSON：{"panel_id":"...","visual_description":"...","local_negative":["..."],"used_refs":["@character:..."]}。
保持画格给定的景别、角度、画幅、可见角色、姿态与视线；不得把前后动作阶段合成一张图。
只使用 visible_refs 内的 @资产引用，不展开人物外观、设定图、三视图或整个角色卡。
不输出对白框、字幕和画面文字；通用画风与禁令由后续编译器加入。
本项目画风策略：{{style_positive}}"""


SYSTEM_SKILLS = [
    {"id": "episodes", "name": "拆分集", "target": "script",
     "desc": "成品剧本按叙事弧切分（钩子/落点/原文锚点）",
     "preview": lambda: episodes_prompt("【场景：示例／日】\n示例动作。\n角色A：台词。" * 3)[0]},
    {"id": "outline", "name": "构想→大纲", "target": "script",
     "desc": "一段话从0生成剧集大纲（主线/类型/人物提示）",
     "preview": lambda: outline_prompt("示例构想一句话", 6)[0]},
    {"id": "units_story", "name": "全剧最小单元·剧情骨架", "target": "script",
     "desc": "剧本/构想→大纲加厚+分段+推演+埋线+实体名册+分集加厚条目（E\\d+ 主键）",
     "preview": lambda: units_story_prompt("示例构想", "【场景：示例／日】\n示例动作。\n角色A：台词。", 6)[0]},
    {"id": "units_entity", "name": "全剧最小单元·设定层", "target": "script",
     "desc": "骨架→剧本小传、演绎与外观设计、场景视觉与空间限制、道具视觉与使用边界",
     "preview": lambda: units_entity_prompt({"premise": "示例", "rules": [], "arcs": [], "roster": {},
                                             "episodes": [], "foreshadows": []})[0]},
    {"id": "expand", "name": "分集扩写", "target": "script",
     "desc": "大纲条目→分场剧本原文（180~220字/分钟）",
     "preview": lambda: expand_episode_prompt("示例构想", {"id": "E1", "title": "示例", "summary": "示例梗概", "hook": "钩子", "cliff": "落点", "duration_min": 4})[0]},
    {"id": "characters", "name": "人物提炼", "target": "assets",
     "desc": "人物档案+音色+镜头倾向+五视图提示词",
     "preview": lambda: characters_prompt("示例剧本文本，角色A对角色B说话。")},
    {"id": "scenes", "name": "场景提炼", "target": "assets",
     "desc": "场景清单+白模几何要点+概念图提示词",
     "preview": lambda: scenes_prompt("示例剧本文本。")},
    {"id": "props", "name": "道具提炼", "target": "assets",
     "desc": "道具清单（叙事/陈设）+设定图提示词",
     "preview": lambda: props_prompt("示例剧本文本。")},
    {"id": "panel_draft", "name": "故事版画格描述", "target": "image",
     "desc": "只从画格事实生成静态画面 JSON；画风与负面词由编译器负责",
     "preview": lambda: sys_for("panel_draft", PANEL_DRAFT_SYS, style_positive="项目画风")},
    {"id": "storyboard", "name": "分镜生成", "target": "storyboard",
     "desc": "对话契约分镜（受控词表+知识库+导演风格注入）",
     "preview": lambda: storyboard_prompt("示例剧本文本。", [], [])[0]},
    {"id": "transitions", "name": "转场优化", "target": "storyboard",
     "desc": "按气氛目标优化转场序列",
     "preview": lambda: transitions_prompt([], "示例气氛")[0]},
    {"id": "fill", "name": "拉片解构填充", "target": "analysis",
     "desc": "三帧视觉分析→景别/运镜/角度/转场/台词/提示词（vision）",
     "preview": lambda: sys_for("fill", FILL_SYS, vocab="(词表在运行时注入)")},
    {"id": "attribute", "name": "台词人物归属", "target": "analysis",
     "desc": "按镜头关键帧判断谁说的（vision）",
     "preview": lambda: sys_for("attribute", ATTRIBUTE_SYS, t0="0.0", t1="3.0", lines="0: 示例台词", roster="['角色A']")},
    {"id": "attribute_norm", "name": "角色别名归一", "target": "analysis",
     "desc": "归属结果的角色名合并",
     "preview": lambda: sys_for("attribute_norm", ATTRIBUTE_NORM_SYS, names="['老者','左侧老者']")},
    {"id": "fixasr", "name": "ASR 台词纠错", "target": "analysis",
     "desc": "同音/近音错字订正（行数不变）",
     "preview": lambda: sys_for("fixasr", FIXASR_SYS)},
    {"id": "scene_env", "name": "3D 场景陈设 DSL", "target": "scene3d",
     "desc": "场景描述→env 几何清单（box/cyl/sphere）",
     "preview": _scene_env_preview},
    {"id": "actor_prepare", "name": "演员上下文准备", "target": "acting",
     "desc": "分镜事实+角色状态→隔离的演员可见上下文",
     "preview": lambda: actor_prepare_prompt({"id": "S1", "dur": 4}, {})[0]},
    {"id": "actor_perform", "name": "演员表演生成", "target": "acting",
     "desc": "按角色知情范围生成镜内可见表演节拍",
     "preview": lambda: actor_perform_prompt({"shot_id": "S1", "actor_ids": ["a"], "dur": 4})[0]},]


def list_system_skills():
    out = []
    for e in SYSTEM_SKILLS:
        try:
            text = str(e["preview"]())
        except Exception as ex:
            text = f"(预览失败: {ex})"
        out.append({"id": e["id"], "name": e["name"], "category": "系统提示词",
                    "target": e["target"], "enabled": True, "builtin": True,
                    "description": e["desc"] + ("（已自定义覆盖）" if _override(e["id"]) is not None else ""),
                    "overridden": _override(e["id"]) is not None, "text": text})
    return out


# 运行时接线：创作线 builder 全部经过 _emit（用户文本追加在内置硬契约之后）
def _wrap(sid, fn):
    def w(*a, **kw):
        sys_p, user_p = fn(*a, **kw)
        result = _emit(sid, sys_p)
        if sid == 'storyboard':
            from production_prompts import CONTRACT
            result += '\n' + CONTRACT
        return result, user_p
    w.__name__ = fn.__name__
    return w


for _sid, _fn in (("episodes", episodes_prompt), ("outline", outline_prompt),
                  ("expand", expand_episode_prompt), ("characters", characters_prompt),
                  ("scenes", scenes_prompt), ("props", props_prompt),
                  ("storyboard", storyboard_prompt), ("transitions", transitions_prompt),
                  ("actor_prepare", actor_prepare_prompt), ("actor_perform", actor_perform_prompt)):
    globals()[_fn.__name__] = _wrap(_sid, _fn)

SKILLS.update({
    "episodes": globals()["episodes_prompt"], "outline": globals()["outline_prompt"],
    "expand": globals()["expand_episode_prompt"], "characters": globals()["characters_prompt"],
    "scenes": globals()["scenes_prompt"], "props": globals()["props_prompt"],
    "storyboard": globals()["storyboard_prompt"], "transitions": globals()["transitions_prompt"],
    "actor_prepare": globals()["actor_prepare_prompt"], "actor_perform": globals()["actor_perform_prompt"],
})


# ---------- 教材 → 经验卡片抽取（导演/表演教材、笔记、文章） ----------

CARD_EXTRACT_SYS = """你是影视创作手法提炼师。把导演/表演/剪辑教材片段转成"经验卡片"——
每张卡 = 一个可执行的镜头手法，供创作 LLM 在命中同类场面时直接执行。

硬约束：
1. 只提炼**可操作**的镜头/调度/表演手法（写明机位、景别、时机、动作），拒绝空泛理论
   （"电影是光的艺术"这类不收）。
2. 每张卡：skill 手法名（≤10字）、trigger 触发场面词（3~6个，逗号分隔，是"什么场面该用它"）、
   prescription 镜头处方（2~3句，具体到执行）、example 教材中的出处或提及的片例（可空）。
3. 优先提取教材中给出**具体案例**的手法；一条手法只出一张卡，不重复。
4. 输出 JSON：{"cards":[{"skill":"...","trigger":"a,b,c","prescription":"...","example":"..."}]}，最多 {max_n} 张。

输出前自检：每张卡的 prescription 里必须有名词性的执行要素（机位/景别/时长/动作之一），否则丢弃。"""


def card_extract_prompt(text, max_n=8):
    """教材片段 → 经验卡片抽取（注册表 id=card_extract）。"""
    sys_p = CARD_EXTRACT_SYS.replace("{max_n}", str(max_n))
    return _emit("card_extract", sys_p), text


def textbook_extract_preview():
    return CARD_EXTRACT_SYS.replace("{max_n}", "8")


SYSTEM_SKILLS.append({"id": "character_reconcile", "name": "角色跨集校准", "target": "assets",
                      "desc": "合并后补 gender/identity_anchor/states（跨集字段）",
                      "preview": lambda: CHARACTER_RECONCILE_SYS})
SYSTEM_SKILLS.append({"id": "card_extract", "name": "教材→经验卡", "target": "script",
                      "desc": "导演/表演教材片段→经验卡片（手法/触发/处方）",
                      "preview": textbook_extract_preview})





def repair_episode_prompt(episode, text, report, units, catalogue, instructions=''):
    """正文局部修正：继承既有事实，不发现或创建新素材。"""
    system = (
        '你是剧本正文校对编辑。修复给定校验缺口，输出完整修正正文，不输出说明、JSON或代码块。'
        '只修改解决缺口所必需的文字，保持分场、剧情顺序、动作、台词含义、伏笔和人物生死事实。'
        '使用已登记角色的姓名和别名，不把未知追兵强行替换成已死角色或主角。'
        '没有独立姓名、个人小传或独立剧情的追兵、守卫、士兵、民夫等群演，保留其衣饰与动作描述，'
        '将台词署名规范为追兵、守卫、士兵、民夫或众人；无需为群演创建人物档案。'
        '具备独立身份或承担关键证词的新人物不能降格为群演以绕过校验。'
        '禁止新增实体、补造设定、删除关键剧情或随意移除缺口标记。'
        '真正无法在现有规划内解决的内容保留原文及【缺口：具体原因】，交用户确认。'
    )
    user = ('【分集规划】\n' + json.dumps(episode, ensure_ascii=False) +
            '\n【已确认设定】\n' + units + '\n【已有素材目录】\n' +
            json.dumps(catalogue, ensure_ascii=False) + '\n【校验缺口】\n' +
            json.dumps(report, ensure_ascii=False) + '\n【原稿】\n' + text)
    if instructions:
        user += '\n【用户确认的修正要求】\n' + instructions
    return _emit('script_repair', system), user


SYSTEM_SKILLS.append({'id': 'script_repair', 'name': '正文缺口修正', 'target': 'script',
    'desc': '按既有规划局部校对正文，不新增素材或改变剧情事实',
    'preview': lambda: repair_episode_prompt({'id':'E1'}, '示例正文', {}, '', {})[0]})
