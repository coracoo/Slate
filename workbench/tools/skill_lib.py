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

项目级选择：projects/<项目>/剧本/style.json {"导演风格":"realism-cold","生图风格":"cinematic-real","拆剧本":null}
  - 选中的 skill 注入对应 LLM 调用；null=自动（只用知识库，不注固定风格）。
  - server /api/script/data 返回 style；分镜提示词页/资产提炼页有下拉，Skill 中心页(/skills)管理库。

API:
  list_skills()                -> [ {id,name,category,target,enabled,builtin,description,path} ]
  load_skill_text(id)          -> 正文（含 frontmatter 剥离）
  style_for(project, target)   -> 该项目该注入点当前选中的 skill 正文（未选/停用 -> ""）
  save_skill(id, text) / create_skill(...) / delete_skill(id)
"""
import sys, os, json, re
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
    os.makedirs(os.path.join(proj, "剧本"), exist_ok=True)
    with open(os.path.join(proj, "剧本", "style.json"), "w", encoding="utf-8") as f:
        json.dump(style, f, ensure_ascii=False, indent=1)


def style_for(proj, target):
    """该项目 target 注入点（script/storyboard/image）当前应注入的 skill 正文串。
    项目未选 -> 取该 target 下唯一启用的（若恰好一个）；多个启用则不注入（避免误配）。"""
    sel = project_style(proj).get(target)
    skills = [s for s in list_skills() if s["target"] == target and s["enabled"]]
    if sel:
        return "".join(load_skill_text(s["id"]) + "\n" for s in skills if s["id"] == sel)
    if len(skills) == 1:
        return load_skill_text(skills[0]["id"]) + "\n"
    return ""


def image_skill_id(proj, override=None):
    """资产生图实际使用的 image skill id：资产级覆盖（资产档案 style 字段）优先，其次项目选择。"""
    ov = str(override or "").strip()
    if ov:
        return ov
    return str(project_style(proj).get("image") or "").strip()


def image_skill_text(proj, override=None):
    """与 image_skill_id 对应的 skill 正文；显式 id 直接读，项目选择走 style_for 口径。"""
    sid = image_skill_id(proj, override)
    if not sid:
        return ""
    if override:
        return str(load_skill_text(sid) or "")
    return str(style_for(proj, "image") or "")


# 资产负面提示词全局基础（唯一来源；与画风 skill 定制负面取并集）
ASSET_BASE_NEGATIVE = "文字,水印,边框,画框,多人,重复角色,畸形手指,多余肢体,肢体交叉错乱,面部变形"

# 类别硬约束：拼在最终提示词末尾并声明不可覆盖，保证不被外观/画风文本冲淡
ASSET_KIND_CONSTRAINTS = {
    "character": "硬性构图约束（最高优先级，不可被任何其他描述覆盖）：画面中只有该角色同一主体的三视图，不出现其他角色或无关人物。",
    "scene": "硬性构图约束（最高优先级，不可被任何其他描述覆盖）：纯场景空镜，画面中不出现任何人物、角色、人形剪影、面部或肢体。",
    "prop": "硬性构图约束（最高优先级，不可被任何其他描述覆盖）：只有该道具单个主体居中，画面中不出现任何人物、角色或人形剪影。",
}


def compose_asset_negative(proj, skill_id=None):
    """资产负面提示词统一入口：全局基础 ∪ 画风 skill 定制。"""
    parts = [ASSET_BASE_NEGATIVE]
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
    # “追加——”与引号之间允许换行（ghibli-soft 等 skill 的实际排版），只取引号内指令
    quoted = re.search(r'追加[\s—–-]*[“"](.+?)[”"]', raw, re.S)
    text = (quoted.group(1) if quoted else raw).strip()
    return text, ("asset_skill" if str(skill_id or "").strip() else "project")


def compose_asset_image_prompt(proj, source_prompt, skill_id=None, kind="character", style_prompt=None):
    """资产生图最终提示词与负面词的唯一组装入口（母图/状态图/子图同路）。
    分层：外观事实（资产档案，提取层零画风词）→ 画风层（resolve_asset_style_text）
    → 类别硬约束（末尾、声明不可覆盖）。返回 (final_prompt, negative)。"""
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
    return "\n".join(parts), compose_asset_negative(proj, skill_id)


if __name__ == "__main__":
    for s in list_skills():
        print(f"[{s['category']}] {s['id']} ({s['target']}) {'✓' if s['enabled'] else '✗'} {s['name']} — {s['description'][:30]}")
