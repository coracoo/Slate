# -*- coding: utf-8 -*-
"""拉片知识库：把本地拉片成果归纳成可检索的"手法技能"（skill 体系）

数据源: projects/*/拉片/*/analysis.json（受控词表齐全的解构镜头表）
产物  : previs_system/knowledge/skills.json
        条目 = {id, skill 手法名, trigger 触发气氛/场面关键词, prescription 处方(典型镜头模式),
                example 片例(项目+镜号), count 归纳自多少部片/场景（同一影片多版本去重后的来源数）}

归纳器（确定性规则，非 LLM——知识库必须可复现、可增量化重建）：
  1. 对话正反打  ：连续镜 cam=cu/ots 且 speaker 交替 → 处方"ots(A,B)/ots(B,A) 交替，情绪升级切 cu"
  2. 紧张/快节奏 ：段内平均 dur<3.5s 且 手持/甩/特写占比高 → 处方"短切+手持特写+插入反应镜"
  3. 对峙/两人张力：两名说话人交替且中间穿插 wide → 处方"wide 定场 → 正反打推进 → wide 释放"
  4. 大场面定场  ：大远景/远景/鸟瞰开头且 >=2 镜 → 处方"大远景定场(1.5-4s) → 切人物视角"
  5. 转场统计    ：transition 词表分布（硬切/叠化/…各自占比与常用搭配）
查询  : query(text, k) —— trigger 关键词命中计分，返回 top-k 条（供 prompt_modules 注入）
CLI   : python knowledge.py build | python knowledge.py query "紧张 争吵"
"""
import sys, os, json, glob, re
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# project_store 位于 previs_system/tools；照抄其他 tools 模块的路径接入方式。
CORE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "previs_system", "tools"))
if CORE not in sys.path:
    sys.path.insert(0, CORE)
import project_store

VIDEO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
KB = os.path.join(VIDEO, "previs_system", "knowledge", "skills.json")

TRIGGERS = {
    "对话正反打": ["对话", "争吵", "争执", "谈判", "质问", "对骂", "辩论", "对峙", "说服", "规劝", "骂"],
    "紧张快切": ["紧张", "危急", "追逐", "枪战", "打斗", "惊慌", "危机", "生死", "爆炸", "逃跑"],
    "两人对峙": ["对峙", "对骂", "谈判", "决战", "叫阵", "隔空", "针锋相对"],
    "大场面定场": ["大战", "军阵", "战场", "城市", "全景", "开幕", "开场", "大场面", "环境"],
    "转场语汇": ["转场", "衔接", "过渡"],
}


def _split_analysis_path(path):
    """解析 analysis.json 路径 → (项目名, 版本目录名)。
    标准结构 projects/<项目>/拉片/<版本>/analysis.json：以末尾的"拉片"目录为锚点取项目名，
    兼容 Windows 反斜杠 / POSIX 正斜杠与仓库根目录深度差异；异常结构回退到末尾四级解析。"""
    parts = [p for p in str(path).replace("\\", "/").split("/") if p]
    ver = parts[-2] if len(parts) >= 2 else ""
    proj = ""
    for i in range(len(parts) - 1, 0, -1):
        if parts[i] == "拉片":
            proj = parts[i - 1]
            break
    if not proj and len(parts) >= 4:
        proj = parts[-4]
    return proj, ver


def _load_all_shots():
    """全部拉片解构 → [(project, version, shots, meta)]；meta 为整份 analysis 文档，
    供 build() 读取 source/name/created_at 做多版本归并。"""
    out = []
    for aj in glob.glob(os.path.join(VIDEO, "projects", "*", "拉片", "*", "analysis.json")):
        try:
            d = json.load(open(aj, encoding="utf-8"))
            if isinstance(d.get("shots"), list) and d["shots"]:
                proj, ver = _split_analysis_path(aj)
                out.append((proj, ver, d["shots"], d))
        except Exception:
            continue
    return out


def _film_key(proj, ver, meta):
    """归纳来源键（项目 × 影片维度）：同一影片的多个分析版本（拉片/<名>/、<名>_v2/…）只算一个来源。
    优先 analysis.json 的 source（影片源文件，取 basename 免疫绝对/相对路径差异）；
    缺失时用 name 或版本目录名，并去掉 _vN 版本后缀。"""
    meta = meta if isinstance(meta, dict) else {}
    src = str(meta.get("source") or "").strip()
    if src:
        return proj + "|src|" + os.path.basename(src.replace("\\", "/"))
    nm = str(meta.get("name") or ver or "").strip()
    nm = re.sub(r"_v\d+$", "", nm)
    return proj + "|name|" + nm


def _version_sort_key(meta, ver):
    """版本新旧排序键：created_at（ISO 字符串可直接比）优先，其次 version 字段与目录名。"""
    meta = meta if isinstance(meta, dict) else {}
    return (str(meta.get("created_at") or ""), str(meta.get("version") or ""), str(ver or ""))


def _vocab(shot, field):
    """读取受控字段（shot_size/camera_move/angle/transition）的有效值。
    「不确定」（证据不足的合法出口）与空值一律返回 None：该镜在此字段上无证据，
    归纳统计时跳过——既不计入命中，也不充当反例（如不打断正反打交替段）。"""
    v = str(shot.get(field) or "").strip()
    return None if v in ("", "不确定") else v


def _seq(spans):
    """最长说话人交替段长度。spans 是完整说话人标识列表（调用方传入角色字符串），
    按整值比较——此前对字符串再取 [0] 只比首字符，char_a/char_b、角色甲/角色乙 会被误判为同一人。"""
    best = 0
    run = 1
    for i in range(1, len(spans)):
        if spans[i] and spans[i - 1] and spans[i] != spans[i - 1]:
            run += 1
            best = max(best, run)
        else:
            run = 1
    return best


def build():
    """扫描全部拉片，归纳手法条目（确定性规则）。
    同一影片的多个分析版本先归并为一个来源（保留最新一版参与归纳），
    count 统计去重后的来源（项目 × 影片）数，不再按版本/出现次数重复累加。
    受控字段（shot_size/camera_move/angle/transition）为「不确定」/空的镜头
    经 _vocab() 归一为无证据镜，不进统计（不命中、不作反例）。"""
    src_of = {}   # skill -> set(来源键)
    ex = {}

    def add(skill, src_key, example):
        src_of.setdefault(skill, set()).add(src_key)
        ex.setdefault(skill, []).append(example)

    # 多版本归并：同来源键只保留最新一版（created_at 优先，退化为 version 字段与目录名）
    films = {}
    for proj, ver, shots, meta in _load_all_shots():
        key = _film_key(proj, ver, meta)
        old = films.get(key)
        if old is None or _version_sort_key(meta, ver) > _version_sort_key(old[3], old[1]):
            films[key] = (proj, ver, shots, meta)

    for key in sorted(films):
        proj, ver, shots, _meta = films[key]
        # 1) 对话正反打：连续带台词镜中说话人交替，且景别为 cu/ots/two
        dlg = [(s.get("dialogue") and s["dialogue"][0].get("speaker"), s) for s in shots]
        dlg = [(sp, s) for sp, s in dlg if sp]
        run, cur = [], None
        for sp, s in dlg:
            size = _vocab(s, "shot_size")
            if size is None:
                continue  # 景别无证据：跳过本镜，不累计也不打断交替段
            if size in ("近景", "特写", "中近景", "中景"):
                run.append((sp, s))
            else:
                if len(run) >= 3 and _seq([r[0] for r in run]) >= 2:
                    add("对话正反打", key, f"{proj}/{ver} " + "→".join(r[1]["id"] for r in run[:4]))
                run = []
        if len(run) >= 3 and _seq([r[0] for r in run]) >= 2:
            add("对话正反打", key, f"{proj}/{ver} " + "→".join(r[1]["id"] for r in run[:4]))

        # 2) 紧张快切：连续 3+ 短镜(dur<3.5)且含手持/甩/特写
        i = 0
        while i < len(shots):
            j = i
            while j < len(shots) and float(shots[j].get("duration") or 99) < 3.5:
                j += 1
            seg = shots[i:j]
            if len(seg) >= 3:
                mv = " ".join(str(s.get("camera_move", "")) for s in seg)
                sz = " ".join(str(s.get("shot_size", "")) for s in seg)
                if ("手持" in mv or "甩" in mv) or ("特写" in sz):
                    add("紧张快切", key, f"{proj}/{ver} {seg[0]['id']}~{seg[-1]['id']}({len(seg)}镜均<3.5s)")
            i = max(j, i + 1)

        # 3) 两人对峙：两名说话人交替 + 中间穿插全景/远景
        for a in range(len(shots) - 3):
            w = shots[a:a + 4]
            sps = [x["dialogue"][0].get("speaker") for x in w if x.get("dialogue")]
            wides = sum(1 for x in w if str(x.get("shot_size", "")) in ("全景", "远景", "大远景"))
            if len(set(sps)) >= 2 and wides >= 1 and len(sps) >= 2:
                add("两人对峙", key, f"{proj}/{ver} {w[0]['id']}~{w[-1]['id']}(交替+{wides}全景)")
                break

        # 4) 大场面定场：前三镜内出现大远景/远景/鸟瞰
        for s in shots[:3]:
            if str(s.get("shot_size", "")) in ("大远景", "远景") or s.get("angle") == "鸟瞰":
                add("大场面定场", key, f"{proj}/{ver} {s['id']}({s.get('shot_size')}/{s.get('angle')})")
                break

        # 5) 转场语汇：只统计"非默认"转场（硬切/无是缺省值，统计它们不是知识）；
        #    「不确定」/空值是证据不足的无证据镜，跳过不计
        tr = {}
        for s in shots:
            t = _vocab(s, "transition")
            if t in (None, "无", "硬切"):
                continue
            tr[t] = tr.get(t, 0) + 1
        for t, c in sorted(tr.items(), key=lambda kv: -kv[1]):
            if c >= 2:
                add("转场语汇", key, f"{proj}/{ver} 「{t}」×{c}次")

        # 6) 仰视压迫：仰视镜数量成规模（权力/压迫的镜头语言）
        ups = [s for s in shots if s.get("angle") == "仰视"]
        if len(ups) >= 5:
            add("仰视压迫", key, f"{proj}/{ver} 仰视×{len(ups)}镜（如 {ups[0]['id']}）")

        # 7) 长镜台词轨：≥8s 且多句台词的长镜（一镜承载完整对话回合）
        longs = [s for s in shots if float(s.get("duration") or 0) >= 8 and len(s.get("dialogue") or []) >= 2]
        if len(longs) >= 2:
            add("长镜台词轨", key, f"{proj}/{ver} {longs[0]['id']}({longs[0]['duration']}s/{len(longs[0]['dialogue'])}句)等{len(longs)}镜")

    # 合成处方
    PRE = {
        "对话正反打": "A说→ots(A,B)，B答→ots(B,A)严格交替；情绪升级处切 cu 单人特写；轴线固定不越",
        "紧张快切": "镜头长度压到 1.5~3s；手持/甩跟运动主体；每 2~3 镜插入反应特写；节奏逐镜加速",
        "两人对峙": "wide 定场交代空间关系 → 正反打推进对峙 → wide 全景释放张力；全景放在情绪转折点",
        "大场面定场": "大远景 1.5~4s 定场（环境+规模感）→ 切人物中景接情绪；鸟瞰用于展示格局",
        "转场语汇": "叠化=时间流逝/回忆；淡入淡出=章节边界；闪白=冲击/惊醒；划像=场景转换；匹配剪辑=转场的高级形态",
        "仰视压迫": "表现权威/压迫/审判感：相机降到 0.5m 仰拍，被摄者居画面上部；连续多镜保持仰角形成权力语境",
        "长镜台词轨": "一镜 8s+ 承载完整对话回合（lines 按时间逐条上），表演/走位在镜内推进；省切换、保沉浸，用于谈判/训话/告白",
    }
    TRIG2 = dict(TRIGGERS)
    TRIG2.update({"仰视压迫": ["仰视", "压迫", "权威", "审判", "权力", "训话", "威严"],
                  "长镜台词轨": ["长镜头", "训话", "谈判", "告白", "独白", "沉浸", "一镜"]})
    skills = []
    for skill, keys in src_of.items():
        skills.append({
            "id": re.sub(r"\s+", "", skill), "skill": skill,
            "trigger": TRIG2.get(skill, TRIGGERS.get(skill, [skill])),
            "prescription": PRE.get(skill, skill),
            "count": len(keys),
            "example": "；".join(ex[skill][:3]),
        })
    os.makedirs(os.path.dirname(KB), exist_ok=True)
    json.dump({"version": 1, "note": "本地拉片知识库：build 于 %s，数据源 projects/*/拉片/*/analysis.json" %
               __import__("time").strftime("%Y-%m-%d %H:%M"), "skills": skills},
              open(KB, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[完成] 知识库 {len(skills)} 条 -> {KB}")
    print("OUTPUT:" + KB)
    return skills


UCARD = os.path.join(VIDEO, "previs_system", "knowledge", "user_cards.json")


def load():
    if os.path.isfile(KB):
        try:
            return json.load(open(KB, encoding="utf-8")).get("skills") or []
        except Exception:
            return []
    return []


def load_user():
    """用户手动沉淀的经验卡片（拉片心得/自创手法），与自动归纳分层存放。"""
    if os.path.isfile(UCARD):
        try:
            return json.load(open(UCARD, encoding="utf-8")).get("cards") or []
        except Exception:
            return []
    return []


def save_card(skill, trigger, prescription, example="", cid=None):
    """新增/更新用户卡片（cid 给定=更新）。返回卡片 id。
    写盘走 project_store.update_json（锁内读改写 + revision + 原子替换），不再裸 json.dump。"""
    trig = [t.strip() for t in re.split(r"[,，;；\s]+", trigger or "") if t.strip()]
    nid = cid

    def mutate(doc):
        cards = doc.setdefault("cards", [])
        nonlocal nid
        if cid:
            for c in cards:
                if c["id"] == cid:
                    c.update(skill=skill, trigger=trig, prescription=prescription,
                             example=example, source="user")
                    return
        nid = "u" + __import__("time").strftime("%m%d%H%M%S")
        ids = {c["id"] for c in cards}
        n = 2
        while nid in ids:
            nid = f"u{__import__('time').strftime('%m%d%H%M%S')}_{n}"; n += 1
        cards.append({"id": nid, "skill": skill, "trigger": trig, "prescription": prescription,
                      "example": example, "count": 1, "source": "user"})

    project_store.update_json(UCARD, mutate, create_default={"cards": []})
    return nid


def delete_card(cid):
    """删除用户卡片；cid 不存在返回 False（不再静默成功）。"""
    found = []

    def mutate(doc):
        cards = doc.setdefault("cards", [])
        found.append(any(c["id"] == cid for c in cards))
        doc["cards"] = [c for c in cards if c["id"] != cid]

    if not os.path.isfile(UCARD):
        return False
    project_store.update_json(UCARD, mutate, create_default={"cards": []})
    return bool(found and found[0])


def query(text, k=4):
    """按 trigger 关键词命中计分，返回 top-k 条目（自动归纳 + 用户卡片合并，用户卡加权）。"""
    if not text:
        return []
    pool = []
    for c in load_user():
        pool.append({"id": c["id"], "skill": c["skill"], "trigger": c.get("trigger", []),
                     "prescription": c.get("prescription", ""), "example": c.get("example", ""),
                     "count": c.get("count", 1), "source": "user"})
    for s in load():
        pool.append({"id": s["id"], "skill": s["skill"], "trigger": s.get("trigger", []),
                     "prescription": s.get("prescription", ""), "example": s.get("example", ""),
                     "count": s.get("count", 0), "source": "auto"})
    scored = []
    for e in pool:
        score = sum(1 for w in e.get("trigger", []) if w and w in text)
        if score:
            scored.append((score + (2 if e.get("source") == "user" else 0), e))
    scored.sort(key=lambda x: (-x[0], -x[1].get("count", 0)))
    return [dict(id=h["id"], skill=h["skill"], prescription=h["prescription"], example=h["example"],
                 count=h.get("count", 0), source=h.get("source", "auto")) for _, h in scored[:k]]


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "build":
        build()
    elif len(sys.argv) >= 3 and sys.argv[1] == "query":
        for h in query(" ".join(sys.argv[2:])):
            print(f"【{h['skill']}】{h['prescription']}\n  片例: {h['example']}")
    else:
        print(__doc__)
