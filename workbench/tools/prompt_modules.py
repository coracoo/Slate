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

PROMPT_VERSION = "1.6"

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


# 对白密度 → 扩写语速档位（字/分钟）：低密度少台词多动作，高密度台词驱动
DIALOGUE_RATE = {"低": (120, 160), "中": (180, 220), "高": (240, 300)}


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

def episodes_prompt(script_text):
    """长剧本 → 分集。规则：按叙事弧切分，每集有独立钩子与落点。"""
    sys_p = f"""你是资深剧集结构编剧（v{PROMPT_VERSION}）。职责：把电影/短剧剧本切分成"集"。
切分不是均匀切片，而是找到叙事弧的自然断点。

硬约束：
1. 每集时长目标 3~15 分钟（短剧）或一个完整叙事段落（电影按幕切）。
2. 每集必须有：独立的戏剧钩子（开场 30 秒内建立张力）+ 落点（结尾悬念或情绪落点）。
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

def expand_episode_prompt(idea, entry, prev_summary=None, style_text=None, proj=None):
    """大纲条目 → 该集分场剧本文本（场景标题+动作描述+台词，即 剧本.txt 格式）。
    proj 传入且 brief.json 存在时按制作规格生成（对白密度字数档位/题材基调）；否则维持缺省文案。"""
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
1. 格式：每场以【场景：地点／日或夜】开头；动作描述用第三人称现在时；台词直接写"角色名：台词内容"。
2. 只写这一集的内容，开场 3 句内建立本集钩子，结尾落在 cliff 上。
3. 台词口语化、符合人物身份；禁止旁白吐槽、禁止"旁白："。
4. {rate_rule}
5. 关键道具必须出现在动作描述里（资产提炼会消费）。
{_brief_tone_block(brief)}{f'''拆剧本 skill（本项目节奏契约）：
{style_text}''' if style_text else ''}

输出：纯剧本文本，不要 JSON、不要解释、不要标题。"""
    user_p = f"[创作构想]\n{idea}\n[本集大纲]\n{json.dumps(entry, ensure_ascii=False)}" + \
             (f"\n[上一集梗概]\n{prev_summary}" if prev_summary else "")
    return sys_p, user_p


# ---------- 2. 人物 ----------

def characters_prompt(episode_text, known=None, style_text=None, catalog=None):
    """剧本文本 → 人物档案。硬信息优先、不给角色编造设定。"""
    kn = _knowledge(episode_text, k=2)
    sys_p = f"""剧集人物总监（v{PROMPT_VERSION}）。职责：从剧本提取全部出场人物并建档，
供分镜白模的站位/服色/景别决策使用。

硬约束：
1. 只提取剧本中明确出现或被对话直接指称的单个人物；不脑补设定。
2. 严禁创建“五人组”“师生们”“全班同学”“路人/群演”“学生们”等集体角色记录；群体只在分镜中逐个 @ 已命名人物，未命名群演由质量提示约束生成。
2b. **旁白/叙述者/画外音/解说不是人物**，严禁为其建档；剧本中“旁白：…”类内容属叙述轨（后期配音用），不产生角色记录、不进 evidence 统计。
3. 每个可复用人物必须有独立姓名或明确区分代号（如守卫甲/乙），输出 is_collective:false。
4. 主角判定写依据（出场次数/驱动剧情），不拍脑袋；每条记录必须有 evidence_ids，
   只能引用下方证据索引中的 EV 编号，不得凭世界观常识补人。
5. 外貌/服装只在剧本提到时填写；没提到填 null——但 voice/sheet_prompt 是创作必需，允许基于人设合理设计。
6. sheet_prompt 是角色三视图的生图提示词：要求同一角色正面/侧面/背面全身立绘、纯白背景、
   写明年龄感/体型/发型/服装/配色/材质与时代感——供生图模型产出跨镜头一致性的角色设定图。
   **只写外观事实**：禁止出现画风/媒介/笔触/渲染类词汇（如"赛璐璐/水彩/写实/3D渲染/胶片感"）——画风由生成时的风格层统一注入，不烘进资产档案。
7. acting 是演员角色卡的稳定基线：依据本集剧本填写 personality、goal、relationship、
   expression_rules、arc_stage；只能写剧本或大纲已有依据，推断内容在 source 中标记为 design_proposal。
8. 单集最多输出 24 个单人角色；sheet_prompt 不超过 220 字；acting 各字段不超过 60 字；只输出 JSON。
9. **gender 必填**（男|女|不明）：依据原文人称指代判定——以首次出场描写为锚、全文指代多数为证；两者冲突时按首次出场判定并在 basis 注明"原文指代存在矛盾"。禁止凭名字气质/题材联想猜性别。
10. **identity_anchor 身份锚点**（≤60字）：全剧永不变的外貌底座——性别、年龄段、体型、发色、肤色等
    生理特征。剧情中会变的（服装/伤情/阵营/发型改造）一律不写入锚点。
11. **states 状态资产**：角色在剧情中外观/立场确有阶段性变化时输出（≤4 个），
    每个 {{"id":"<角色id>_S1","label":"状态名(≤6字)","episodes":["出现的集号/场名"],"look_diff":"与锚点的差异(≤40字：服装/伤情/持物)","camp":"敌方|友方|中立|不明","sheet_prompt":"锚点原文+差异合成后的该状态三视图提示词(≤220字，同样只写外观事实、禁止画风词)"}}。
    外观立场无变化的角色 states 为空数组。sheet_prompt（顶层）= 默认/戏份最重状态的版本。

输出 JSON：{{"characters":[{{"id":"pinyin_id","name":"姓名","role":"主角|配角|群演","is_collective":false,
"gender":"男|女|不明",
"identity_anchor":"身份锚点：全剧不变的生理底座（性别/年龄段/体型/发色/肤色），≤60字",
"states":[],
"basis":"主角判定依据","appearance":{{"age":null,"look":"外貌(剧本提及才填)","outfit":"服装(同前)"}},
"acting":{{"personality":"稳定性格","goal":"当前目标","relationship":"与主要人物的关系",
"expression_rules":"表达与反应习惯","arc_stage":"本集成长阶段"}},
"voice":"音色描述：音高/语速/质感/口音（配音与 TTS 选型用）",
"lens":"镜头倾向：这类角色常用什么景别与机位拍（如弱势者多用仰视近景）",
"sheet_prompt":"角色三视图生图提示词（中文，含'正面、侧面、背面三视图'字样与全部外观细节）",
"dialogue_count":数字,"first_scene":"首次出场场景名","evidence_ids":["EV001"],
"parent_ref":null,"relation":null,"derived_from":null,"related_refs":[]}}]}}
{f"\n可参考的既有档案（合并而非重复创建，优先复用其中 id）：{json.dumps(known, ensure_ascii=False)}" if known else ""}
{_catalog_block(catalog)}
{_selfcheck(["每个 id 唯一且为小写拼音/英文", "appearance 字段剧本没提就是 null，不许编", "每条人物记录至少有一个能在原文定位的 evidence_ids", "五人组/师生们/全班等群体不输出为人物", "旁白/叙述者/画外音/解说绝不输出为人物", "sheet_prompt 只写外观事实，不含画风/媒介/渲染词（画风由生成时风格层注入）", "gender 与 identity_anchor 一致（锚点里的性别词=gender）；states 的 sheet_prompt 必须包含 identity_anchor 原文且性别一致", "states 只描述阶段性变化（阵营/服装/伤情），生理特征不得变"])}"""
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
“触角/触须”默认也是角色外观：只写“长在头顶/身上有触角”时留在人物 appearance；只有剧本明确写出伸缩、颤动、折断、脱落等独立表演，并且存在特写/视线锁定，才允许作为角色子素材。此时 owner 必须是实际角色，parent_ref 必须是“@character:角色id”，不得擅自把“蚁族/蛛族”等种属改写。
角色的可分离身体组件、拟态部件、武器、重要配饰，以及剧情明确关注的服饰才作为子素材：owner 填角色 id，parent_ref 使用“@character:角色id”，kind 使用“关联素材/服饰/配饰/组件”。同一概念跨集、跨状态只保留一个母素材（例如只保留一个项圈、一个蜘蛛步足、一个蛛丝母素材），颜色、损坏、收起/展开等变化写入 actions 或 shot_hint，不得重复建条目。
由另一个道具加工、包裹、损坏或变形得到的临时复合物（例如“蛛丝缓冲茧”由“遥控炸弹”派生）不是角色组件：derived_from 写真实的“@prop:母道具”，parent_ref 同样挂到该道具，relation 使用 derived_from；制造/使用它的角色只写入 owner，不得把它挂到角色下面。与同镜协同的其它道具放 related_refs，不要伪造为父级。
owner 是“谁持有/制造/使用”，只表示剧情动作发起者；parent_ref 是“继承谁的身份/结构”，只表示素材层级；两者永远分开。parent_ref、derived_from、related_refs 只能填写项目目录中已有的稳定 @ 引用，不能凭空造“蚁族/蛛族/五人组”等父节点。普通剧情道具只记录真正被镜头关注的物件，kind 使用“叙事”，parent_ref 必须为 null；owner 仅在明确属于场景时填写场景 id。单集最多提取 12 条，宁缺毋滥；actions 最多 3 条、每条不超过 24 字；image_prompt 不超过 120 字；shot_hint 不超过 40 字。只输出 JSON，不要解释文字。
关系判定例子（必须遵守）：错误：白咲蛛绪用蛛丝包住遥控炸弹 → owner=白咲蛛绪、parent_ref=@character:baixiaozhuxu；正确：owner=baixiaozhuxu、derived_from=@prop:yaokong_zhadan、parent_ref=@prop:yaokong_zhadan、relation=derived_from。错误：正文写“红色眼眸/白色双马尾”就建道具；正确：写入人物 appearance。错误：同一蜘蛛步足在不同集生成多个 ID；正确：复用目录中的 @prop 母素材，把颜色/收起/展开写入 actions 或 shot_hint。

输出 JSON：{{"props":[{{"id":"pinyin_id","name":"道具名或关联素材名","kind":"叙事|关联素材|服饰|配饰|组件","asset_required":true,
"owner":"归属人物id或场景id","parent_ref":null,"relation":"component_of|located_in|derived_from|used_with|null","derived_from":null,"related_refs":[],"actions":["涉及该道具的动作，如'掷于阶下'"],"evidence_ids":["EV001"],"focus_type":"动作|交接|破坏|特写|独立结构",
"image_prompt":"道具设定图生图提示词（中文：单个主体居中+材质/做旧/年代感+纯色背景，120字内；只写物件事实，禁止画风/媒介/渲染词——画风由生成时的风格层统一注入）",
"shot_hint":"需要特写/交接镜头才填，否则 null"}}]}}
{_catalog_block(catalog)}
{_selfcheck(["每条都是本集明确出场且有动作/交接/破坏/使用/特写的关键资产", "每条至少引用一个能在原文定位的 evidence_ids，并填写 focus_type", "红色眼眸、发型、肤色、五官、身形、普通制服细节不得作为道具", "同一概念跨集只保留一个母素材，状态变化写 actions/shot_hint，不重复建项圈/蛛腿/蛛丝", "不输出陈设道具、环境构件或泛称", "叙事道具 parent_ref 必须为 null；只有关联素材/服饰/配饰/组件允许填写 owner/parent_ref", "derived_from 为 @prop 时 parent_ref 必须与其完全相同，制造者只写 owner", "关联子素材必须填写 owner/parent_ref 并说明与母素材的关系", "剧本没出现或只是一闪而过的资产不提取", "image_prompt 只写物件事实，不含画风/媒介/渲染词（画风由生成时风格层注入）"])}"""
    # 提取层不注入画风（style_text 保留参数仅为签名兼容）：物件事实与画风分层
    return sys_p, "[原文]\n" + str(episode_text or "") + "\n\n" + _evidence_block(episode_text)


# ---------- 5. 分镜（创作线核心） ----------

def storyboard_prompt(episode_text, characters, scenes, props=None, mood_text=None, style_text=None):
    """剧本+人物+场景 → dialogue 契约分镜。注入拉片知识库参考片例 + 导演风格 skill。"""
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
    scenes_txt = json.dumps([{
        "ref": "@scene:" + str(s.get("id") or ""),
        "id": s.get("id"), "name": s.get("name"), "light": s.get("light")
    } for s in (scenes or []) if s.get("id")], ensure_ascii=False)
    props_txt = json.dumps([{
        "ref": "@prop:" + str(p.get("id") or ""),
        "id": p.get("id"), "name": p.get("name"), "kind": p.get("kind"), "parent_ref": p.get("parent_ref")
    } for p in (props or []) if p.get("id")],
                           ensure_ascii=False) if props else "[]"
    sys_p = f"""电影预演分镜师（v{PROMPT_VERSION}）。把剧本段落拆成可执行的对话契约分镜 JSON，
下游是确定性渲染引擎（2D 白模/Blender 3D），你输出的每个数字都会被直接执行。

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
5. action 写"谁+做什么"（会被白模姿态消费：骑马/持枪/起身…），prompt 写给生图模型的自然语言主提示词（供图生视频）。
6. prompt 必须优先写成 JSON 上方可直接投喂模型的连续中文画面描述，100~160 字：先写 16:9 横构图、场景、景别、角度与动作阶段，再写画面中每个角色的相对位置、动作、目标和视线，最后写背景、画风和光线。
7. prompt 只保留本镜可见动作与行为；不得复制人物/场景/道具档案的外观长描述。为了让动作可辨，只允许保留与本镜动作直接相关的最小特征（例如“八条步足保持下坠姿态”）。
8. prompt 末尾必须给出本镜专用“禁止：”清单，覆盖错误机位/景别、错误动作阶段、额外角色、文字水印和不应出现的特效；静态图明确写“台词由后期叠加，不生成对白框、字幕和文字”。
9. 为避免输出截断，单集最多 20 镜；每镜 prompt 控制在 100~160 字，negative 最多 4 条，其余字段用短句。
10. actor_refs 必须逐个引用本镜可见的具名人物（@character:id），禁止使用“五人组/师生们/全班同学”等群体资产。场景必须填写 scene_ref（@scene:id）；多人或群演镜必须在 prompt 中加入质量提示：五官、发型、体型、服饰和表情彼此有差异，符合当前场景。
{f'''
拉片知识库参考片例（本工作区从真实影片归纳；同气氛直接参考其镜头模式）：
{kn}''' if kn else ''}
{f'''
导演风格 skill（本项目的风格契约，镜头语言/色彩/节奏按此执行）：
{style_text}''' if style_text else ''}
输出 JSON：{{"shots":[{{"id":"S1","dur":4.0,"shot_size":"近景","camera_move":"固定","angle":"平视",
"transition":"硬切","cam":"cu","scene":"room","light":"夜晚-烛光",
"pos":[x,y,z],"look":[x,y,z],"speaker":"人物id",
"content":"本镜内容一句话（谁在哪做什么，剧情视角）",
"action":"谁做什么（可执行的动作，供白模姿态）",
"sound":"声音层（台词外的环境声/音效/音乐提示，≤20字）",
"rig":"固定|手持|滑轨|轨道|斯坦尼康|无人机|稳定器",
"lens":"镜头焦距mm（特写85/中景50/全景35/大远景24 为基线，按气质微调）",
"lighting":"光影一句话（主光方向/明暗比/色温）",
"prompt":"自然语言生图主提示词（100~160字，JSON 上方直接投喂；含构图、角色位置/动作/视线、背景、画风、光线和本镜禁止清单；人物资产只用 @ 引用，不复制外观档案）",
"negative":["本镜专用禁止项，逐条写清错误机位/动作/特效/文字"],"scene_ref":"@scene:id或空","actor_refs":["@character:id"],"prop_refs":["@prop:id"],"lines":[{{"at":0.5,"dur":2.0,"speaker":"人物id（旁白固定 narrator）","line":"台词"}}]}}]}}
rig 受控词表：固定/手持/滑轨/轨道/斯坦尼康/无人机/稳定器（camera_move=手持→rig 手持；移/轨道→滑轨或轨道；环绕→滑轨；升降/无人机→无人机；甩→手持；其余→固定）。
{_selfcheck(["台词全部来自原文，一字不改", "相邻镜 cam 相同且 speaker 相同时合并", "每镜 pos/look 是合法三元数组",
"每镜 rig/lens/sound/lighting/content 不得缺省", "lens 与景别匹配（特写用 85mm 段、大远景用 24mm 段）",
"prompt 为 100~160 字自然语言画面描述，且先于 JSON 作为主提示词使用", "prompt 写出所有在场角色的位置/动作/视线，但不展开资产档案", "negative 含错误动作阶段和文字禁令"])}"""
    user_p = f"[人物]\n{chars_txt}\n[场景]\n{scenes_txt}\n[叙事道具]\n{props_txt}\n[剧本]\n{episode_text}"
    return _emit("storyboard", sys_p, kn), user_p


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
    return _emit("actor_prepare", sys_p), user_p


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
    return _emit("actor_perform", sys_p), json.dumps(request or {}, ensure_ascii=False)

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
- states：角色确有阶段性外观/立场变化时输出 ≤4 个：
  {"id":"<角色id>_S1","label":"≤6字","episodes":["E2"],"look_diff":"与锚点的差异≤40字","camp":"敌方|友方|中立|不明","sheet_prompt":"锚点原文+差异合成的三视图提示词≤220字"}。
  弧线型角色必须拆状态（如"反派→盟友"两状态）；全剧无变化输出空数组。
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
# 覆盖层：workbench/skills/system/<id>.md 存在 → 整体替换内置系统提示词；
#   正文支持 {{knowledge}} 占位（运行时替换为拉片知识库检索结果）。
# 拆片线工具（analyze_film/attribute_speakers/fix_transcript/gen_scene_env）经 sys_for() 消费。

import os as _os

_SYS_DIR = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                         "skills", "system")


def _override(sid):
    p = _os.path.join(_SYS_DIR, sid + ".md")
    return open(p, encoding="utf-8").read() if _os.path.isfile(p) else None


def _emit(sid, sys_p, kn=""):
    ov = _override(sid)
    if ov is not None:
        out = ov.replace("{{knowledge}}", kn or "")
        if sid in ("storyboard", "actor_prepare", "actor_perform") and "@character:<id>" not in out:
            out += chr(10) + ASSET_REFERENCE_RULES
        return out
    return sys_p


def sys_for(sid, builtin_text, **vals):
    """拆片线工具入口：override 存在则用之（支持 {{key}} 占位替换），否则用内置文本。"""
    t = _override(sid)
    if t is None:
        t = builtin_text
    for k, v in vals.items():
        t = t.replace("{{" + k + "}}", str(v))
    return t


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
    return SCENE_ENV_SYS


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
    {"id": "expand", "name": "分集扩写", "target": "script",
     "desc": "大纲条目→分场剧本原文（180~220字/分钟）",
     "preview": lambda: expand_episode_prompt("示例构想", {"id": "E1", "title": "示例", "summary": "示例梗概", "hook": "钩子", "cliff": "落点", "duration_min": 4})[0]},
    {"id": "characters", "name": "人物提炼", "target": "assets",
     "desc": "人物档案+音色+镜头倾向+三视图提示词",
     "preview": lambda: characters_prompt("示例剧本文本，角色A对角色B说话。")},
    {"id": "scenes", "name": "场景提炼", "target": "assets",
     "desc": "场景清单+白模几何要点+概念图提示词",
     "preview": lambda: scenes_prompt("示例剧本文本。")},
    {"id": "props", "name": "道具提炼", "target": "assets",
     "desc": "道具清单（叙事/陈设）+设定图提示词",
     "preview": lambda: props_prompt("示例剧本文本。")},
    {"id": "panel_draft", "name": "故事版画格描述", "target": "image",
     "desc": "只从画格事实生成静态画面 JSON；画风与负面词由编译器负责",
     "preview": lambda: PANEL_DRAFT_SYS.replace("{{style_positive}}", "项目画风")},
    {"id": "storyboard", "name": "分镜生成", "target": "storyboard",
     "desc": "对话契约分镜（受控词表+知识库+导演风格注入）",
     "preview": lambda: storyboard_prompt("示例剧本文本。", [], [])[0]},
    {"id": "transitions", "name": "转场优化", "target": "storyboard",
     "desc": "按气氛目标优化转场序列",
     "preview": lambda: transitions_prompt([], "示例气氛")[0]},
    {"id": "fill", "name": "拉片解构填充", "target": "analysis",
     "desc": "三帧视觉分析→景别/运镜/角度/转场/台词/提示词（vision）",
     "preview": lambda: FILL_SYS.replace("{{vocab}}", "(词表在运行时注入)")},
    {"id": "attribute", "name": "台词人物归属", "target": "analysis",
     "desc": "按镜头关键帧判断谁说的（vision）",
     "preview": lambda: ATTRIBUTE_SYS.format(t0="0.0", t1="3.0", lines="0: 示例台词", roster="['角色A']")},
    {"id": "attribute_norm", "name": "角色别名归一", "target": "analysis",
     "desc": "归属结果的角色名合并",
     "preview": lambda: ATTRIBUTE_NORM_SYS.format(names="['老者','左侧老者']")},
    {"id": "fixasr", "name": "ASR 台词纠错", "target": "analysis",
     "desc": "同音/近音错字订正（行数不变）",
     "preview": lambda: FIXASR_SYS},
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
            text = _override(e["id"]) if _override(e["id"]) is not None else str(e["preview"]())
        except Exception as ex:
            text = f"(预览失败: {ex})"
        out.append({"id": e["id"], "name": e["name"], "category": "系统提示词",
                    "target": e["target"], "enabled": True, "builtin": True,
                    "description": e["desc"] + ("（已自定义覆盖）" if _override(e["id"]) is not None else ""),
                    "overridden": _override(e["id"]) is not None, "text": text})
    return out


# 运行时覆盖接线：创作线 builder 全部经过 _emit（override 存在则替换系统提示词）
def _wrap(sid, fn):
    def w(*a, **kw):
        sys_p, user_p = fn(*a, **kw)
        kn = kw.get("knowledge", "")
        if not kn and kw.get("mood_text"):
            try:
                kn = _knowledge(kw["mood_text"], k=3)
            except Exception:
                kn = ""
        result = _emit(sid, sys_p, kn)
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





