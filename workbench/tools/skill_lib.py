# -*- coding: utf-8 -*-
"""影视创作策略库：拆剧本、镜头方法、画风与表演方法。

文件使用简化 frontmatter 与 Markdown 正文；正文是模型提示词，不执行 Agent 工具或步骤。目录：
  workbench/skills/
    directing/   导演风格（target: storyboard，注入分镜提示词）
    acting/      演员风格（target: acting，注入结构化表演请求）
    image-style/ 生图风格（target: image，注入资产设定图与创作生图提示词）
    script/      拆剧本（target: script，注入分集大纲与扩写提示词）
frontmatter 字段：id/name/category/target/dimension/enabled/description；正文=注入给模型的创作方法。
置 enabled: false 停用；正文可编辑。自定义策略的维度必须与 target 匹配。

项目级选择：projects/<项目>/剧本/style.json 保存旧 target 单选与各独立维度选择。
  - 选中的策略注入对应模型调用；"auto"/缺失=不注固定策略。
  - E10 起显性选择：style_for/image_skill_id 只吃 style.json 显式值，不再隐式推导；
    读取入口经 ensure_explicit_defaults 幂等补默认（唯一启用者冻结为显式值，否则写 "auto"）。
  - server /api/script/data 返回 style；剧本/分镜/资产页有维度下拉，Skill 中心页(/skills)管理库。

API:
  list_skills()                     -> [ {id,name,category,target,enabled,builtin,description,path} ]
  load_skill_text(id)               -> 正文（含 frontmatter 剥离）
  style_for(project, target)        -> 该项目该注入点显式选中的 skill 正文（未选/"auto"/停用 -> ""）
  ensure_explicit_defaults(project) -> 补全 style.json 缺失 target 的显式默认值（幂等，不覆盖已有选择）
  skill_snapshot_for(project, targets, overrides=None) -> 本次实际注入的 skill 快照 {target:{id,name,sha}}
  save_skill(id, text) / create_skill(...) / delete_skill(id)
"""
import sys, os, json, re, hashlib
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

VIDEO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SKILLS_DIR = os.path.join(VIDEO, "workbench", "skills")

FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.S)

_READ_SCOPE = ContextVar('skill_read_scope', default=None)


@contextmanager
def read_scope():
    """一次资产读取共用策略目录；请求结束即失效，编辑后下一次读取即时生效。"""
    if _READ_SCOPE.get() is not None:
        yield
        return
    token = _READ_SCOPE.set({})
    try:
        yield
    finally:
        _READ_SCOPE.reset(token)


def _parse(path):
    cache = _READ_SCOPE.get()
    key = ('parse', path)
    if cache is not None and key in cache:
        return deepcopy(cache[key])
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
    result = meta, body.strip()
    if cache is not None:
        cache[key] = result
    return deepcopy(result)


def list_skills():
    cache = _READ_SCOPE.get()
    key = ('list', SKILLS_DIR)
    if cache is not None and key in cache:
        return deepcopy(cache[key])
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
                        "dimension": meta.get("dimension", ""),
                        "enabled": meta["enabled"], "builtin": meta["builtin"],
                        "description": meta.get("description", ""), "path": f"{cat}/{f}",
                        "negative": meta.get("negative", "")})
    if cache is not None:
        cache[key] = out
    return deepcopy(out)


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


def create_skill(category, name, target, description, text, dimension=""):
    """新建项目策略文本；维度不匹配时拒绝写入。"""
    categories = {"script": "script", "directing": "storyboard",
                  "image-style": "image", "acting": "acting"}
    if category not in categories or target != categories[category]:
        raise ValueError("Skill 类别与注入目标不匹配")
    if dimension and dimension not in DIMENSIONS_BY_TARGET.get(target, ()):
        raise ValueError("Skill 维度与注入目标不匹配")
    name = str(name).replace("\r", " ").replace("\n", " ").strip()
    description = str(description).replace("\r", " ").replace("\n", " ").strip()
    if not name or not str(text).strip():
        raise ValueError("Skill 名称与正文不能为空")
    cd = os.path.join(SKILLS_DIR, category)
    os.makedirs(cd, exist_ok=True)
    sid = "custom-" + re.sub(r"[^\w]+", "-", name.lower()).strip("-")[:30]
    p = os.path.join(cd, sid + ".md")
    n = 2
    while os.path.isfile(p):
        p = os.path.join(cd, f"{sid}-{n}.md"); n += 1
    fm = (f"---\nid: {os.path.splitext(os.path.basename(p))[0]}\nname: {name}\n"
          f"category: {category}\ntarget: {target}\nenabled: true\nbuiltin: false\n"
          f"dimension: {dimension}\ndescription: {description}\n---\n")
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
    return _save_project_style(proj, style, replace=True)


def patch_project_style(proj, patch):
    """在文件锁内只合并本次选择的维度，保留其它页面已保存的选择。"""
    return _save_project_style(proj, patch, replace=False)


def _save_project_style(proj, values, *, replace):
    if not isinstance(values, dict) or any(not isinstance(value, str) for value in values.values()):
        raise ValueError('风格选择必须是文本值的对象')
    core = os.path.join(VIDEO, 'previs_system', 'tools')
    if core not in sys.path:
        sys.path.insert(0, core)
    import project_store
    import versions
    p = os.path.join(proj, "剧本", "style.json")
    def update(current):
        if replace:
            current.clear()
        current.update(values)
    saved, _revision = project_store.update_json(p, update, create_default={}, snapshot=versions.snapshot)
    return saved


# style.json 中"自动"的显式值（E10）：与缺失键同义（仅知识库驱动），但不会被默认填入改写
AUTO_VALUE = "auto"

# 参与默认填入的注入点兜底集合（实际 target 以 Skill 库为准，这里是库为空时的保底）
KNOWN_TARGETS = ("script", "storyboard", "image", "acting")
DIMENSIONS_BY_TARGET = {
    "script": ("script_structure", "script_pacing", "script_continuity"),
    "storyboard": ("storyboard_camera", "storyboard_keyframe", "storyboard_motion"),
    "image": ("image_visual", "image_identity"),
    "acting": ("acting_style",),
}


def ensure_explicit_defaults(proj, *, persist_defaults=True):
    """E10 显性选择 + 默认填入：把 style.json 缺失的 target 冻结成显式值。

    规则：某 target 无显式选择（缺键/空串）时，按旧隐式规则解析——该 target 恰好
    一个启用 skill 则取其 id，多个启用或无启用则写 "auto"；只补缺失键，绝不覆盖
    已有显式选择（含用户手选的 "auto"）。写回仅在项目目录真实存在且内容有变化时
    发生（幂等；之后增删/启停 Skill 不再让项目选择飘移）。返回完整 style dict。
    persist_defaults=False 只在内存解析缺省选择，不写文件或版本快照，供冻结输入消费。
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
    if persist_defaults and changed and os.path.isdir(proj):
        # 只在真实项目目录落盘；读取入口不允许因写盘失败而中断
        try:
            core = os.path.join(VIDEO, 'previs_system', 'tools')
            if core not in sys.path:
                sys.path.insert(0, core)
            import project_store
            import versions
            def fill_defaults(current):
                for target, value in style.items():
                    if not str(current.get(target) or '').strip():
                        current[target] = value
            style, _revision = project_store.update_json(
                os.path.join(proj, '剧本', 'style.json'), fill_defaults, create_default={}, snapshot=versions.snapshot)
        except Exception:
            pass
    return style


def selected_skills_for(proj, target, *, persist_defaults=True):
    """解析目标槽位的旧选择与独立维度选择；新维度覆盖同维度旧选择。"""
    style = ensure_explicit_defaults(proj, persist_defaults=persist_defaults)
    rows = {row["id"]: row for row in list_skills() if row.get("enabled") and row.get("target") == target}
    chosen = {}
    legacy = str(style.get(target) or "").strip()
    if legacy in rows:
        dimension = str(rows[legacy].get("dimension") or target)
        chosen[dimension] = (target, rows[legacy])
    for dimension in DIMENSIONS_BY_TARGET.get(target, ()):
        if dimension not in style:
            continue
        selected = str(style.get(dimension) or "").strip()
        chosen.pop(dimension, None)
        row = rows.get(selected)
        if row and row.get("dimension") == dimension:
            chosen[dimension] = (dimension, row)
    order = DIMENSIONS_BY_TARGET.get(target, ())
    return [chosen[key] for key in (*order, target) if key in chosen]


def style_for(proj, target, *, persist_defaults=True):
    """该项目 target 注入点（script/storyboard/image/acting）当前应注入的 skill 正文串。
    E10 起只吃 style.json 的显式选择（首次读取由 ensure_explicit_defaults 把旧隐式
    默认冻结成显式值）；未选择/"auto"=仅知识库驱动，不注入任何 skill 正文。
    persist_defaults=False 使用同样的选择规则，但不修改冻结输入目录。"""
    bodies = [str(load_skill_text(row["id"]) or "").strip()
              for _, row in selected_skills_for(proj, target, persist_defaults=persist_defaults)]
    bodies = [body for body in bodies if body]
    return "\n\n".join(bodies) + "\n" if bodies else ""


def storyboard_generation_context(proj, *, persist_defaults=True):
    """分镜生成的实际方法与画风：每维度一项，文本与快照使用同一份正文。

    延续显式选择规则；auto、停用和无效选择不补选。旧无维度导演策略视为镜头语言，
    新镜头语言选择优先；image 只取画风，不把资产身份/五视图方法带进剧情画面。
    """
    labels = {"storyboard_camera": "镜头语言与节奏", "storyboard_keyframe": "冻结关键画面方法",
              "storyboard_motion": "连续运动方法", "image_visual": "项目唯一画风"}
    selected = selected_skills_for(proj, "storyboard", persist_defaults=persist_defaults)
    selected += [(key, row) for key, row in selected_skills_for(
        proj, "image", persist_defaults=persist_defaults)
        if (row.get("dimension") or "") in ("image_visual", "")]
    style = project_style(proj)
    if not isinstance(style, dict):
        style = {}
    parts, snapshot = [], {}
    used_dimensions, used_ids, used_bodies = set(), set(), set()
    for key, row in selected:
        dimension = row.get("dimension") or ("image_visual" if row["target"] == "image" else "storyboard_camera")
        # 显式关闭新维度也必须压住旧无维度策略，不能借旧字段重新注入。
        if not row.get("dimension") and dimension in style:
            continue
        if dimension not in labels or dimension in used_dimensions or row["id"] in used_ids:
            continue
        body = str(load_skill_text(row["id"]) or "").strip()
        if dimension == "image_visual":
            body = _visual_style_body(body)
        if not body or body in used_bodies:
            continue
        parts.append(f"【{labels[dimension]}：{row.get('name') or row['id']}】\n{body}")
        snapshot[key] = {"id": row["id"], "name": row.get("name") or row["id"],
                         "sha": hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]}
        used_dimensions.add(dimension)
        used_ids.add(row["id"])
        used_bodies.add(body)
    return {"text": "\n\n".join(parts), "snapshot": snapshot}


def image_skill_id(proj, override=None):
    """资产生图实际使用的 image skill id：资产级覆盖（资产档案 style 字段）优先，
    其次项目显式选择（E10 与 style_for 同一口径：只吃 style.json，"auto"/未选 -> ""）。"""
    ov = str(override or "").strip()
    if ov:
        return ov
    sel = str(ensure_explicit_defaults(proj).get("image") or "").strip()
    return "" if sel == AUTO_VALUE else sel


def image_visual_skill_id(proj, override=None):
    """只返回画风 Skill；身份锚点方法不得成为剧情帧的画风引用。"""
    if override:
        for row in list_skills():
            if row["id"] == override and row.get("target") == "image" and row.get("enabled"):
                return override if row.get("dimension") != "image_identity" else ""
        return ""
    for _, row in selected_skills_for(proj, "image"):
        if row.get("dimension") in ("image_visual", ""):
            return row["id"]
    return ""


def image_identity_method_text(proj, override=None):
    """资产人物图专用身份方法；不供镜头关键帧或视频消费。"""
    if override:
        for row in list_skills():
            if row["id"] == override and row.get("dimension") == "image_identity" and row.get("enabled"):
                return str(load_skill_text(override) or "").strip()
    for _, row in selected_skills_for(proj, "image"):
        if row.get("dimension") == "image_identity":
            return str(load_skill_text(row["id"]) or "").strip()
    return ""


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
    out = {}
    for target in targets:
        override = str((overrides or {}).get(target) or "").strip()
        if override:
            snap = skill_meta_snapshot(override, target)
            if snap:
                out[target] = snap
            continue
        for key, row in selected_skills_for(proj, target):
            snap = skill_meta_snapshot(row["id"], target)
            if snap:
                out[key] = snap
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

# 人物设定图五段构图的唯一文本；用画面范围明确各视图。
SHEET_VIEW_TITLE_ZH = "五视图设定图（一张图内从左到右五段）"
SHEET_VIEW_PANELS_ZH = ("①脸部正面与脖子特写；②脸部45度左侧脸与脖子特写；"
                        "③正面全身像——画面从颈部开始到脚底，完整呈现躯干、双腿与双脚；"
                        "④侧面全身像——画面从颈部开始到脚底，完整呈现身体侧面、双腿与双脚；⑤严格背面全身像（含头部背面）")
SHEET_VIEW_LAYOUT_ZH = f"{SHEET_VIEW_TITLE_ZH}：{SHEET_VIEW_PANELS_ZH}。纯白背景。"
# 英文对照与中文使用相同的正向画面范围。
SHEET_VIEW_LAYOUT_EN = ("character reference sheet, five panels in one image arranged left to right, plain white background: "
                        "panel 1 face and neck front close-up; panel 2 face and neck 45-degree profile close-up; "
                        "panel 3 front body view framed from the neck to the soles, torso, legs and feet fully visible; "
                        "panel 4 side body view framed from the neck to the soles, torso, legs and feet fully visible; "
                        "panel 5 full back view including the back of the head.")

# 类别硬约束：拼在最终提示词末尾并声明不可覆盖，保证不被外观/画风文本冲淡
ASSET_KIND_CONSTRAINTS = {
    "effect": "硬性构图约束：单张完整的显现或特效画面，按描述保留环境、光效与非实体形态；不是人物设定图，不画人物五视图、人体模板或道具陈列图。来源角色仅表示故事归属，不要求其肉身出镜。",
    "character": (f"硬性构图约束（最高优先级，不可被任何其他描述覆盖）：画面为同一角色的五视图拼版——{SHEET_VIEW_PANELS_ZH}"
                  "——五段从左到右排列在同一画面内，不出现其他角色或无关人物。"
                  f"EN: {SHEET_VIEW_LAYOUT_EN}"),
    "scene": "硬性构图约束（最高优先级，不可被任何其他描述覆盖）：纯场景空镜，画面中不出现任何人物、角色、人形剪影、面部或肢体。",
    "prop": "硬性构图约束（最高优先级，不可被任何其他描述覆盖）：只有该道具单个主体居中，画面中不出现任何人物、角色或人形剪影。",
}
ASPECT_CONSTRAINT = ("硬性画幅约束（不可被任何其他描述覆盖）：画面严格为 16:9 横构图（宽高比 16:9，"
                     "strictly 16:9 landscape aspect ratio），禁止方形/竖构图输出。")


def strip_character_layout(text):
    """剥离已知设定图构图模板，保留外观正文；兼容历史三视图和五视图。"""
    out = str(text or "").replace(SHEET_VIEW_LAYOUT_ZH, "")
    out = re.sub(r"五视图设定图(?:（一张图内从左到右五段）)?[：:]①.*?⑤严格背面全身像（含头部背面）[。；]?\s*(?:纯白背景[。；]?)?", "", out, flags=re.S)
    out = re.sub(r"五视图设定图[：:]脸部正面特写[、，]45度左侧脸特写[、，]不带头部正面全身[、，]不带头部侧面全身[、，]严格背面全身[。；]?", "", out)
    out = re.sub(r"(?:同一角色[的：:]?)?(?:正面[、，/]侧面[、，/]背面)?三视图(?:[，、；： ]*纯白背景)?[，；。]?", "", out)
    out = re.sub(r"五视图设定图(?:（一张图内从左到右五段）)?[：:]", "", out)
    out = re.sub(r"[，、；;：:]{2,}", "；", out)
    out = re.sub(r"。，|，。", "。", out).lstrip("；;、，。 \n").rstrip("；;、， \n")
    return re.sub(r"^(纯白背景[，、；;：:。\s]*)+", "", out).strip("；;、， \n")


def normalize_character_sheet(text):
    """人物图描绘统一显示构图；空描绘不伪造为已完成。"""
    return SHEET_VIEW_LAYOUT_ZH + strip_character_layout(text) if str(text or "").strip() else ""


def compose_asset_negative(proj, skill_id=None, kind=None):
    """负面提示词统一入口：全局基础 ∪（剧情帧类 kind 追加剧情帧专属）∪ 画风 skill 定制。
    kind 为 character/scene/prop/None（资产设定图）时不带"额外角色/重复角色"禁令（E11）；
    kind 命中 STORY_FRAME_KINDS（剧情关键帧/画格/视频首帧类）才并入 STORY_FRAME_NEGATIVE。"""
    parts = [ASSET_BASE_NEGATIVE]
    if str(kind or "").strip().lower() in STORY_FRAME_KINDS:
        parts.append(STORY_FRAME_NEGATIVE)
    selected = image_visual_skill_id(proj, skill_id)
    neg = skill_negative(selected) if selected else ""
    if neg:
        parts.append(neg)
    return ",".join(parts)


def _visual_style_body(raw):
    """共享画风段提取，避免把 Skill 的资产三视图说明投射到剧情帧。"""
    quoted = re.search(r'追加[^“”"]{0,40}[“"](.+?)[”"]', str(raw or ""), re.S)
    return (quoted.group(1) if quoted else str(raw or "")).strip()


def resolve_asset_style_text(proj, skill_id=None, style_prompt=None):
    """画风层文本与来源。优先级：资产 style_prompt 自由文本 > 资产 style(skill id) > 项目选择。
    返回 (style_text, source)：source ∈ asset_text|asset_skill|project|none。"""
    sp = str(style_prompt or "").strip()
    if sp:
        return sp, "asset_text"
    selected = image_visual_skill_id(proj, skill_id)
    if not selected:
        return "", "none"
    raw = str(load_skill_text(selected) or "")
    # “追加——”与引号之间允许换行/破折号，也允许一小段说明词（cinematic-real 写作
    # “追加到生图提示词末尾——”）；取不到引号时仍回退整篇正文。
    text = _visual_style_body(raw)
    return text, ("asset_skill" if str(skill_id or "").strip() else "project")


def compose_asset_image_prompt(proj, source_prompt, skill_id=None, kind="character", style_prompt=None):
    """资产生图最终提示词与负面词的唯一组装入口（母图/状态图/子图同路）。
    分层：外观事实（资产档案，提取层零画风词）→ 画风层（resolve_asset_style_text）
    → 类别硬约束（末尾、声明不可覆盖）。负面按 kind 组装（E11：资产图不带剧情帧禁令）。
    返回 (final_prompt, negative)。"""
    content = str(source_prompt or "").strip()
    if kind == "character":
        content = strip_character_layout(content)
    style_text, _src = resolve_asset_style_text(proj, skill_id, style_prompt)
    parts = [content] if content else []
    if style_text:
        parts.append("当前唯一生图画风（仅在画风维度内优先，不能改写主体身份、结构或构图）：" + style_text
                     + "\n资产描述只提供主体身份、服装、结构、颜色和场景事实；"
                       "其中与当前画风冲突的绘画媒介、笔触、渲染词一律忽略。")
    if str(kind or "").strip() == "character":
        identity_method = image_identity_method_text(proj, skill_id)
        if identity_method:
            parts.append("人物身份方法（仅用于资产设定图）：" + identity_method)
    constraint = ASSET_KIND_CONSTRAINTS.get(str(kind or "").strip())
    if constraint:
        parts.append(constraint)
    parts.append(ASPECT_CONSTRAINT)
    return "\n".join(parts), compose_asset_negative(proj, skill_id, kind=kind)


if __name__ == "__main__":
    for s in list_skills():
        print(f"[{s['category']}] {s['id']} ({s['target']}) {'✓' if s['enabled'] else '✗'} {s['name']} — {s['description'][:30]}")
