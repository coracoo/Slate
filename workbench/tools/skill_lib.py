# -*- coding: utf-8 -*-
"""影视创作 Skill 库：拆剧本 / 导演风格 / 生图风格（SKILL.md 开放格式，自动注入管线）

格式与 Claude Agent Skills 同构（YAML frontmatter + markdown 正文），目录：
  workbench/skills/
    directing/   导演风格（target: storyboard，注入分镜提示词）
    acting/      演员风格（target: acting，注入结构化表演请求）
    image-style/ 生图风格（target: image，注入资产设定图与创作生图提示词）
    script/      拆剧本（target: script，注入分集大纲与扩写提示词）
frontmatter 字段：id/name/category/target/enabled/description；正文=注入给 LLM 的风格指令。
用户/社区可新增 .md（拷入即生效）；置 enabled: false 停用；正文可自由编辑。

项目级选择：projects/<项目>/剧本/style.json {"storyboard":"realism-cold","image":"cinematic-real","script":"auto"}
  - 选中的 skill 注入对应 LLM 调用；"auto"/缺失=自动（只用知识库，不注固定风格）。
  - E10 起显性选择：style_for/image_skill_id 只吃 style.json 显式值，不再隐式推导；
    读取入口经 ensure_explicit_defaults 幂等补默认（唯一启用者冻结为显式值，否则写 "auto"）。
  - server /api/script/data 返回 style；分镜提示词页/资产提炼页有下拉，Skill 中心页(/skills)管理库。

API:
  list_skills()                     -> [ {id,name,category,target,enabled,builtin,description,path} ]
  load_skill_text(id)               -> 正文（含 frontmatter 剥离）
  style_for(project, target)        -> 该项目该注入点显式选中的 skill 正文（未选/"auto"/停用 -> ""）
  ensure_explicit_defaults(project) -> 补全 style.json 缺失 target 的显式默认值（幂等，不覆盖已有选择）
  skill_snapshot_for(project, targets, overrides=None) -> 本次实际注入的 skill 快照 {target:{id,name,sha}}
  save_skill(id, text) / create_skill(...) / delete_skill(id)
"""
import sys, os, json, re, hashlib
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

VIDEO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SKILLS_DIR = os.path.join(VIDEO, "workbench", "skills")

FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.S)


def _parse(path):
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    m = FM_RE.match(raw)
    meta, body = {}, raw
    if m:
        body = m.group(2)
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
    meta["enabled"] = str(meta.get("enabled", "true")).lower() != "false"
    meta["builtin"] = str(meta.get("builtin", "false")).lower() == "true"
    return meta, body.strip()


def list_skills():
    out = []
    if not os.path.isdir(SKILLS_DIR):
        return out
    for cat in sorted(os.listdir(SKILLS_DIR)):
        cd = os.path.join(SKILLS_DIR, cat)
        if not os.path.isdir(cd):
            continue
        for f in sorted(os.listdir(cd)):
            if not f.endswith(".md"):
                continue
            p = os.path.join(cd, f)
            try:
                meta, body = _parse(p)
            except Exception:
                continue
            out.append({"id": meta.get("id", f[:-3]), "name": meta.get("name", f[:-3]),
                        "category": meta.get("category", cat), "target": meta.get("target", ""),
                        "enabled": meta["enabled"], "builtin": meta["builtin"],
                        "description": meta.get("description", ""), "path": f"{cat}/{f}",
                        "negative": meta.get("negative", "")})
    return out


def load_skill_text(skill_id):
    for s in list_skills():
        if s["id"] == skill_id:
            return _parse(os.path.join(SKILLS_DIR, s["path"]))[1]
    return ""


def save_skill(skill_id, text):
    """编辑已存在 skill 的正文（frontmatter 原样保留）；不存在则报错。
    frontmatter 直接沿用原文块，不经过 _parse 重建——enabled/builtin 是
    _parse 注入的计算字段，重建会把布尔值写回文件并污染原键集合。"""
    for s in list_skills():
        if s["id"] == skill_id:
            p = os.path.join(SKILLS_DIR, s["path"])
            with open(p, encoding="utf-8") as f:
                raw = f.read()
            m = FM_RE.match(raw)
            fm = ("---\n" + m.group(1) + "\n---\n") if m else ""
            with open(p, "w", encoding="utf-8") as f:
                f.write(fm + "\n" + text.strip() + "\n")
            return True
    return False


def create_skill(category, name, target, description, text):
    """新建自定义 skill（category 即子目录；id 由 name 拼音化简化处理）。"""
    cd = os.path.join(SKILLS_DIR, category)
    os.makedirs(cd, exist_ok=True)
    sid = "custom-" + re.sub(r"[^\w]+", "-", name.lower()).strip("-")[:30]
    p = os.path.join(cd, sid + ".md")
    n = 2
    while os.path.isfile(p):
        p = os.path.join(cd, f"{sid}-{n}.md"); n += 1
    fm = (f"---\nid: {os.path.splitext(os.path.basename(p))[0]}\nname: {name}\n"
          f"category: {category}\ntarget: {target}\nenabled: true\nbuiltin: false\n"
          f"description: {description}\n---\n")
    with open(p, "w", encoding="utf-8") as f:
        f.write(fm + "\n" + text.strip() + "\n")
    return os.path.splitext(os.path.basename(p))[0]


def delete_skill(skill_id):
    for s in list_skills():
        if s["id"] == skill_id and not s["builtin"]:
            os.remove(os.path.join(SKILLS_DIR, s["path"]))
            return True
    return False


def toggle_skill(skill_id, enabled):
    for s in list_skills():
        if s["id"] == skill_id:
            p = os.path.join(SKILLS_DIR, s["path"])
            with open(p, encoding="utf-8") as f:
                raw = f.read()
            val = "true" if enabled else "false"
            new, n = re.subn(r"(?m)^enabled:.*$", f"enabled: {val}", raw)
            if n == 0:
                # 原文件没有 enabled 行：插入 frontmatter（无 frontmatter 则新建一个），
                # 不能静默返回成功。
                m = FM_RE.match(raw)
                if m:
                    new = "---\n" + m.group(1) + f"\nenabled: {val}\n---\n" + m.group(2)
                else:
                    new = f"---\nenabled: {val}\n---\n\n" + raw
            with open(p, "w", encoding="utf-8") as f:
                f.write(new)
            return True
    return False


def skill_negative(skill_id):
    """skill frontmatter 的负面提示词（逗号分隔）。"""
    for s in list_skills():
        if s["id"] == skill_id:
            return s.get("negative", "")
    return ""


def project_style(proj):
    """项目级风格选择（缺省 None=自动）。"""
    p = os.path.join(proj, "剧本", "style.json")
    if os.path.isfile(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception: pass
    return {}


def set_project_style(proj, style):
    p = os.path.join(proj, "剧本", "style.json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    try:
        import versions as _V
        _V.snapshot(p)
    except Exception:
        pass
    with open(p, "w", encoding="utf-8") as f:
        json.dump(style, f, ensure_ascii=False, indent=1)


# style.json 中"自动"的显式值（E10）：与缺失键同义（仅知识库驱动），但不会被默认填入改写
AUTO_VALUE = "auto"

# 参与默认填入的注入点兜底集合（实际 target 以 Skill 库为准，这里是库为空时的保底）
KNOWN_TARGETS = ("script", "storyboard", "image", "acting")


def ensure_explicit_defaults(proj):
    """E10 显性选择 + 默认填入：把 style.json 缺失的 target 冻结成显式值。

    规则：某 target 无显式选择（缺键/空串）时，按旧隐式规则解析——该 target 恰好
    一个启用 skill 则取其 id，多个启用或无启用则写 "auto"；只补缺失键，绝不覆盖
    已有显式选择（含用户手选的 "auto"）。写回仅在项目目录真实存在且内容有变化时
    发生（幂等；之后增删/启停 Skill 不再让项目选择飘移）。返回完整 style dict。
    """
    style = project_style(proj)
    if not isinstance(style, dict):
        style = {}
    else:
        style = dict(style)
    skills = list_skills()
    targets = sorted({str(s.get("target") or "") for s in skills if s.get("target")} | set(KNOWN_TARGETS))
    changed = False
    for target in targets:
        if str(style.get(target) or "").strip():
            continue  # 已有显式选择（skill id 或 "auto"）：不动
        enabled = [s for s in skills if s["target"] == target and s["enabled"]]
        style[target] = enabled[0]["id"] if len(enabled) == 1 else AUTO_VALUE
        changed = True
    if changed and os.path.isdir(proj):
        # 只在真实项目目录落盘；读取入口不允许因写盘失败而中断
        try:
            set_project_style(proj, style)
        except Exception:
            pass
    return style


def style_for(proj, target):
    """该项目 target 注入点（script/storyboard/image/acting）当前应注入的 skill 正文串。
    E10 起只吃 style.json 的显式选择（首次读取由 ensure_explicit_defaults 把旧隐式
    默认冻结成显式值）；未选择/"auto"=仅知识库驱动，不注入任何 skill 正文。"""
    sel = str(ensure_explicit_defaults(proj).get(target) or "").strip()
    if not sel or sel == AUTO_VALUE:
        return ""
    for s in list_skills():
        if s["id"] == sel and s["target"] == target and s["enabled"]:
            return load_skill_text(s["id"]) + "\n"
    return ""


def image_skill_id(proj, override=None):
    """资产生图实际使用的 image skill id：资产级覆盖（资产档案 style 字段）优先，
    其次项目显式选择（E10 与 style_for 同一口径：只吃 style.json，"auto"/未选 -> ""）。"""
    ov = str(override or "").strip()
    if ov:
        return ov
    sel = str(ensure_explicit_defaults(proj).get("image") or "").strip()
    return "" if sel == AUTO_VALUE else sel


def image_skill_text(proj, override=None):
    """与 image_skill_id 对应的 skill 正文；显式 id 直接读，项目选择走 style_for 口径。"""
    sid = image_skill_id(proj, override)
    if not sid:
        return ""
    if override:
        return str(load_skill_text(sid) or "")
    return str(style_for(proj, "image") or "")


def skill_meta_snapshot(skill_id, target=None):
    """单个 skill 的冻结快照 {"id","name","sha"}（sha=正文 sha256 前 12 位，E10 追溯用）。
    skill 不存在、已停用或与 target 不符时返回 None（视为未注入）。"""
    sid = str(skill_id or "").strip()
    if not sid:
        return None
    for s in list_skills():
        if s["id"] != sid:
            continue
        if not s["enabled"] or (target and s["target"] != target):
            return None
        body = load_skill_text(sid)
        return {"id": sid, "name": s.get("name") or sid,
                "sha": hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]}
    return None


def skill_snapshot_for(proj, targets, overrides=None):
    """本次生成实际注入的 skill 快照 {target: {"id","name","sha"}}（E10 任务创建冻结）。

    auto/未注入的 target 不出现在结果里（style.json 里有显式值可查）。overrides 可给
    某 target 传资产级覆盖 id（如资产档案 style 字段）；覆盖值为空串时回落项目显式选择。
    """
    style = ensure_explicit_defaults(proj)
    out = {}
    for target in targets:
        sid = str((overrides or {}).get(target) or "").strip()
        if not sid:
            sel = str(style.get(target) or "").strip()
            sid = "" if (not sel or sel == AUTO_VALUE) else sel
        snap = skill_meta_snapshot(sid, target)
        if snap:
            out[target] = snap
    return out


# 负面提示词分两层（E11 修复，唯一来源；与画风 skill 定制负面取并集）：
# ① 全局基础负面 ASSET_BASE_NEGATIVE——对资产设定图与剧情帧都安全的通用禁令。
#    不再含"多人/重复角色"：角色三视图就是同一主体在一张图里并列三个角度，
#    这类禁令与 ASSET_KIND_CONSTRAINTS.character 的"同一主体三视图"硬约束正面冲突，
#    图像模型会把三视图拉成单人或拒绝生成。
# ② 剧情帧专属负面 STORY_FRAME_NEGATIVE——只进剧情关键帧/画格/视频首帧类产物。
#    按 shot-prompt-v1 规范（docs/shot-prompt-v1-资产引用.md）只禁"额外角色/重复角色"，
#    不禁"多人"——多角色与群像镜头是合法构图，禁"多人"会误删三人/群像画面。
ASSET_BASE_NEGATIVE = "文字,水印,边框,画框,畸形手指,多余肢体,肢体交叉错乱,面部变形"
STORY_FRAME_NEGATIVE = "额外角色,重复角色"

# 剧情帧类 kind：compose_asset_negative 仅对这些 kind 并入剧情帧专属负面；
# character/scene/prop 等资产设定图 kind 永远不带（三视图/空镜/单道具的正约束已够）
STORY_FRAME_KINDS = frozenset({"frame", "keyframe", "panel", "shot", "video_frame", "story_frame"})

# 人物设定图五段构图的**唯一权威文本**。以前这四段文字在 ①类别硬约束 ②② 提炼提示词 ③单资产重生成
# 三处各抄一份，改一处就分叉（本次就是"迁移幂等"只在其中一处加了守卫）。
# ③④ 用正面表述（"画面自领口往下"）而不是"不带头部"：图像模型对中文否定句服从度极低，
# 而且"不带头部"会被读成"不带头盔"；⑤ 补"含头部背面"，免得模型把背面也画成无头。
SHEET_VIEW_TITLE_ZH = "五视图设定图（一张图内从左到右五段）"
SHEET_VIEW_PANELS_ZH = ("①脸部正面与脖子特写；②脸部45度左侧脸与脖子特写；"
                        "③无头躯干正面像——画面自领口往下，颈部以上不入画；"
                        "④无头躯干侧面像——同样止于领口；⑤严格背面全身像（含头部背面）")
SHEET_VIEW_LAYOUT_ZH = f"{SHEET_VIEW_TITLE_ZH}：{SHEET_VIEW_PANELS_ZH}。纯白背景。"
# 英文对照：图像模型对纯中文指令服从度低（本仓老坑，画风禁令因此也是中英双语）。
SHEET_VIEW_LAYOUT_EN = ("character reference sheet, five panels in one image arranged left to right, plain white background: "
                        "panel 1 face and neck front close-up; panel 2 face and neck 45-degree profile close-up; "
                        "panel 3 headless torso front view cropped at the collar, nothing above the neck in this panel; "
                        "panel 4 headless torso side view, also cropped at the collar; "
                        "panel 5 full back view including the back of the head.")

# 类别硬约束：拼在最终提示词末尾并声明不可覆盖，保证不被外观/画风文本冲淡
ASSET_KIND_CONSTRAINTS = {
    "character": (f"硬性构图约束（最高优先级，不可被任何其他描述覆盖）：画面为同一角色的五视图拼版——{SHEET_VIEW_PANELS_ZH}"
                  "——五段从左到右排列在同一画面内，不出现其他角色或无关人物。"
                  f"EN: {SHEET_VIEW_LAYOUT_EN}"),
    "scene": "硬性构图约束（最高优先级，不可被任何其他描述覆盖）：纯场景空镜，画面中不出现任何人物、角色、人形剪影、面部或肢体。",
    "prop": "硬性构图约束（最高优先级，不可被任何其他描述覆盖）：只有该道具单个主体居中，画面中不出现任何人物、角色或人形剪影。",
}
ASPECT_CONSTRAINT = ("硬性画幅约束（不可被任何其他描述覆盖）：画面严格为 16:9 横构图（宽高比 16:9，"
                     "strictly 16:9 landscape aspect ratio），禁止方形/竖构图输出。")


def compose_asset_negative(proj, skill_id=None, kind=None):
    """负面提示词统一入口：全局基础 ∪（剧情帧类 kind 追加剧情帧专属）∪ 画风 skill 定制。
    kind 为 character/scene/prop/None（资产设定图）时不带"额外角色/重复角色"禁令（E11）；
    kind 命中 STORY_FRAME_KINDS（剧情关键帧/画格/视频首帧类）才并入 STORY_FRAME_NEGATIVE。"""
    parts = [ASSET_BASE_NEGATIVE]
    if str(kind or "").strip().lower() in STORY_FRAME_KINDS:
        parts.append(STORY_FRAME_NEGATIVE)
    selected = image_skill_id(proj, skill_id)
    neg = skill_negative(selected) if selected else ""
    if neg:
        parts.append(neg)
    return ",".join(parts)


def resolve_asset_style_text(proj, skill_id=None, style_prompt=None):
    """画风层文本与来源。优先级：资产 style_prompt 自由文本 > 资产 style(skill id) > 项目选择。
    返回 (style_text, source)：source ∈ asset_text|asset_skill|project|none。"""
    sp = str(style_prompt or "").strip()
    if sp:
        return sp, "asset_text"
    selected = image_skill_id(proj, skill_id)
    if not selected:
        return "", "none"
    raw = image_skill_text(proj, skill_id)
    # “追加——”与引号之间允许换行/破折号，也允许一小段说明词（cinematic-real 写作
    # “追加到生图提示词末尾——”）；取不到引号时仍回退整篇正文。
    quoted = re.search(r'追加[^“”"]{0,40}[“"](.+?)[”"]', raw, re.S)
    text = (quoted.group(1) if quoted else raw).strip()
    return text, ("asset_skill" if str(skill_id or "").strip() else "project")


def compose_asset_image_prompt(proj, source_prompt, skill_id=None, kind="character", style_prompt=None):
    """资产生图最终提示词与负面词的唯一组装入口（母图/状态图/子图同路）。
    分层：外观事实（资产档案，提取层零画风词）→ 画风层（resolve_asset_style_text）
    → 类别硬约束（末尾、声明不可覆盖）。负面按 kind 组装（E11：资产图不带剧情帧禁令）。
    返回 (final_prompt, negative)。"""
    content = str(source_prompt or "").strip()
    style_text, _src = resolve_asset_style_text(proj, skill_id, style_prompt)
    parts = [content] if content else []
    if style_text:
        parts.append("当前唯一生图画风（最高优先级）：" + style_text
                     + "\n资产描述只提供主体身份、服装、结构、颜色和场景事实；"
                       "其中与当前画风冲突的绘画媒介、笔触、渲染词一律忽略。")
    constraint = ASSET_KIND_CONSTRAINTS.get(str(kind or "").strip())
    if constraint:
        parts.append(constraint)
    parts.append(ASPECT_CONSTRAINT)
    return "\n".join(parts), compose_asset_negative(proj, skill_id, kind=kind)


if __name__ == "__main__":
    for s in list_skills():
        print(f"[{s['category']}] {s['id']} ({s['target']}) {'✓' if s['enabled'] else '✗'} {s['name']} — {s['description'][:30]}")
