# -*- coding: utf-8 -*-
"""2D 平面图生成（plan v1 第三/四节：LLM 生成 → validate 确定性闸 → 判官语义闸 → 落盘）

链路：场景资产（--scene，从 素材/场景.json 读描述）/ scene_desc（text 厂商）/ keyframe（vision 厂商）
  → 按 plan schema 约束生成 → validate_plan.validate_document（不合格带错误明细重试）
  → judge_plan.judge 三型裁决（打回带原因重生成，与 validate 失败共用封顶 3 次）
  → 落 projects/<项目>/推演/平面图_<名>.plan.json（覆写前 versions.snapshot）。
判官封顶后仍不通过：落盘最后一版"合法"产物并把判官结果记入 plan["_judge"] 与日志；
validate 从未通过则整体失败（不落盘）。
--scene 模式：plan 顶层写 scene_ref=场景 id（下游 strategy_map/assemble 按它匹配底图），
名缺省=场景 id；--all-scenes 为全部场景资产逐个生成（extract 收尾也走这条路出初稿）。

用法: python gen_plan.py <项目目录> [名] [--scene 场景id [--extra-desc 补充]] [--all-scenes [--skip-existing]]
          [--scene-desc 文本 | --desc-file 路径] [--keyframe 图片路径] [--zone 区域id]
          [--vendor 厂商id] [--max-attempts 3]
退出码 0=已落盘（--all-scenes 至少成功一个）/ 1=失败；stdout 末行 OUTPUT:<plan 路径>
"""
import sys, os, json, glob, argparse
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from llm_openai import VendorClient, load_vendors, VendorError
import validate_plan
import judge_plan

FAST_THINK = {"thinking": {"type": "disabled"}}   # 结构化 JSON 任务关思考（creation_pipeline 同款）

PROMPT_SYS = (
    "你是片场平面图设计师。把场景描述转成 2D 平面图 plan.json（俯视，米制）。"
    "只输出 JSON，不要解释、不要 markdown 代码块。"
)
PROMPT_USER = """按下面的 plan v1 schema 输出平面图 JSON（只输出 schema 内字段）：
{{"version":1,"name":"{name}","canvas":{{"w":12.0,"h":9.0}},
 "room":{{"walls":[[x,y]…多边形顶点],"openings":[{{"wall":0,"offset":1.0,"width":1.2,"kind":"door"}}]}},
 "props":[{{"id":"p1","label":"名称","shape":"rect","center":[x,y],"size":[w,h],"rot":0}}],
 "actors":[{{"id":"c","name":"角色名","color":"#e74c3c","pos":[x,y],"facing":270}}],
 "paths":[{{"actor":"c","points":[[x,y,t秒]…],"style":"walk"}}],
 "cameras":[{{"id":"cam1","pos":[x,y],"look":[x,y],"fov":50}}],
 "zones":[{{"id":"z1","label":"区域名","rect":[x,y,w,h],"scene_ref":"场景名"}}]}}
硬性规则：
1. 米制估算；画布原点左上，x 向右、y 向下；任何坐标禁止超出 canvas（0≤x≤w，0≤y≤h）。
2. room.openings.wall 是 0 基墙段序号（段 i = walls[i]→walls[i+1]，末段回卷）；offset+width 不得超出该段长度。
3. shape=rect 时 size=[w,h]，circle 时 size=[r]；尺寸必须为正。facing/rot 单位度。
4. paths 的 t 严格递增、点数 ≥ 2；style=walk|run|ride；paths.actor 必须引用已定义的演员 id。
5. cameras.fov 取 5–170°。props/actors 至少其一非空。zones.scene_ref 只引用场景描述里出现的场景名。
6. 陈设贴墙/合理落地，路径绕开家具；按描述摆门窗方位。
{zone_hint}场景描述：
{desc}
{feedback}
只输出 JSON 对象。"""


def pick_vendor(kind, vendor_id=None):
    """providers.json 第一个已启用且配置指定 kind 模型的厂商（有 key 优先）。"""
    vs = [v for v in load_vendors() if v.get("enabled") and (v.get("models") or {}).get(kind)]
    if vendor_id:
        vs = [v for v in vs if v["id"] == vendor_id]
    vs.sort(key=lambda v: not v.get("api_key"))
    if not vs:
        raise VendorError(f"没有已启用且配置 {kind} 模型的厂商（检查 providers.json / ⑦ 环境页）")
    return vs[0]["id"]


def parse_json(txt):
    """LLM 输出 → dict（parse_structured 容错：截断/尾逗号；不完整拒收）。"""
    from llm_result import parse_structured
    parsed = parse_structured(str(txt))
    if not parsed.get("complete"):
        raise ValueError("LLM 输出 JSON 不完整: " + "; ".join(parsed.get("repair_notes") or []))
    if not isinstance(parsed.get("data"), dict):
        raise ValueError("LLM 输出 JSON 顶层必须是对象")
    return parsed["data"]


def list_scenes(project_dir):
    """素材/场景.json 的场景资产行清单（无文件/解析失败返回 []）。"""
    path = os.path.join(project_dir, "素材", "场景.json")
    try:
        rows = json.load(open(path, encoding="utf-8")).get("scenes") or []
    except (OSError, ValueError):
        return []
    return [r for r in rows if isinstance(r, dict)]


def scene_desc_text(row):
    """场景资产行 → 平面图描述文本：名称 + geometry 各行 + 时间/光线/室内外 + ① 锚定的空间约束。
    不带 image_prompt（那里面烘着画风词，与平面图几何无关）。"""
    parts = [f"场景：{row.get('name') or row.get('id')}"]
    for g in row.get("geometry") or []:
        parts.append(str(g))
    meta = []
    if row.get("time"):
        meta.append(f"时间:{row['time']}")
    if row.get("light"):
        meta.append(f"光线:{row['light']}")
    if row.get("interior") is not None:
        meta.append("室内" if row.get("interior") else "室外")
    if meta:
        parts.append("（" + "，".join(meta) + "）")
    # ① 第一步锚定的设定层：墙门窗的取舍与动作位排布要服从它（未锚定项目两键皆空，描述不变）
    if str(row.get("spatial_limit") or "").strip():
        parts.append(f"空间对行动的限制：{row['spatial_limit']}——门/窗/通道的可开可堵必须支持这条。")
    slots = [str(s).strip() for s in (row.get("action_slots") or []) if str(s).strip()]
    if slots:
        parts.append("必须留出的可复用动作位置：" + "、".join(slots))
    return "\n".join(parts)


def load_scene(project_dir, scene_id):
    """按 id → name → aliases 顺序命中场景资产行，返回 (row, 描述文本)；未命中 (None, "")。"""
    sid = str(scene_id or "")
    for row in list_scenes(project_dir):
        if str(row.get("id") or "") == sid or str(row.get("name") or "") == sid:
            return row, scene_desc_text(row)
        if sid in [str(a) for a in (row.get("aliases") or [])]:
            return row, scene_desc_text(row)
    return None, ""


def build_messages(name, desc, zone=None, feedback="", keyframe_part=None):
    """组 prompt；keyframe_part 非空时走 vision（图片 grounding 出坐标）。"""
    user = PROMPT_USER.format(
        name=name, desc=desc or "（以图片内容为准）",
        zone_hint=(f"7. 本次只生成区域 {zone} 范围内的陈设/角色/机位。\n" if zone else ""),
        feedback=("上轮问题（必须全部修正）：\n" + feedback + "\n") if feedback else "")
    if keyframe_part is not None:
        user = "参考关键帧画面，grounding 出平面图坐标。\n" + user
        return [{"role": "system", "content": PROMPT_SYS},
                {"role": "user", "content": [{"type": "text", "text": user}, keyframe_part]}]
    return [{"role": "system", "content": PROMPT_SYS},
            {"role": "user", "content": user}]


def generate(project_dir, name=None, scene_desc=None, keyframe=None, zone=None,
             vendor=None, max_attempts=3, gen_chat=None, judge_fn=None, log=print,
             scene=None, extra_desc=None):
    """生成闭环。gen_chat(messages)->str 可注入（测试/复用连接）；
    judge_fn(plan, scene_desc)->dict 可注入。返回 {"ok","path","plan","judge","attempts","warnings"}。
    scene=场景资产 id：描述取自 素材/场景.json（extra_desc 追加补充），name 缺省=场景 id，
    落盘 plan 顶层写 scene_ref（供下游按场景匹配底图）。"""
    if scene:
        row, desc = load_scene(project_dir, scene)
        if row is None:
            raise ValueError(f"场景资产未命中: {scene}（检查 素材/场景.json 的 id/name/aliases）")
        # 一律归一到资产 id：曾按请求 token 写 scene_ref（token 可能是 name/alias），
        # 与文件名用的 row.id 双口径，下次提炼把该场景判为"未覆盖"并覆写同名文件，
        # 导致分镜里的 @scene: 引用集体悬空。
        scene = str(row.get("id") or scene)
        scene_desc = desc + (("\n补充描述：" + str(extra_desc)) if extra_desc else "")
        name = name or str(row.get("id") or row.get("name") or scene)
    if not name:
        raise ValueError("需要平面图名称或 --scene")
    keyframe_part = None
    kind = "text"
    if keyframe:
        kf = keyframe if os.path.isabs(keyframe) else os.path.join(project_dir, keyframe)
        if not os.path.isfile(kf):
            raise ValueError(f"关键帧不存在: {keyframe}")
        keyframe_part = VendorClient.image_part(kf)
        kind = "vision"
    if gen_chat is None:
        cli = VendorClient(pick_vendor(kind, vendor))
        log(f"[信息] LLM 厂商: {cli.id} / {cli.model(kind)}（{kind}）")
        gen_chat = lambda msgs: cli.chat(msgs, kind=kind, max_tokens=4000, timeout=420,
                                         temperature=0.4, extra=FAST_THINK)
    if judge_fn is None:
        judge_fn = lambda plan: judge_plan.judge(plan, scene_desc=scene_desc or "", log=log)
    scenes_path = os.path.join(project_dir, "素材", "场景.json")
    scene_ids = validate_plan.load_scene_ids(scenes_path) if os.path.isfile(scenes_path) else None

    feedback = ""
    last_valid = None        # 最后一版过 validate 的候选
    last_judge = None
    for attempt in range(1, max_attempts + 1):
        log(f"[生成] 第 {attempt}/{max_attempts} 轮")
        txt = gen_chat(build_messages(name, scene_desc, zone, feedback, keyframe_part))
        try:
            plan = parse_json(txt)
        except ValueError as exc:
            feedback = f"- 输出不可解析：{exc}"
            log(f"[打回] {feedback}")
            continue
        plan.setdefault("version", 1)
        plan["name"] = name
        if scene:
            plan["scene_ref"] = str(scene)
        vr = validate_plan.validate_document(plan, scene_ids=scene_ids,
                                            scene_id_set=validate_plan.load_scene_id_set(scenes_path)
                                            if os.path.isfile(scenes_path) else None)
        if vr["errors"]:
            feedback = "\n".join(f"- [{e['path']}] {e['message']}" for e in vr["errors"][:12])
            log(f"[打回] validate {len(vr['errors'])} 错误：{vr['errors'][0]['message']} 等")
            continue
        for w in vr["warnings"]:
            log(f"[警告] {w['path']} {w['message']}")
        last_valid = plan
        jr = judge_fn(plan)
        jr["warnings"] = vr["warnings"]
        last_judge = jr
        if jr["ok"]:
            break
        feedback = "\n".join(f"- {r}" for r in jr["reasons"])
    else:
        pass

    if last_valid is None:
        raise ValueError(f"{max_attempts} 轮生成均未通过 validate，未落盘")
    judged_ok = bool(last_judge and last_judge["ok"])
    last_valid["_judge"] = {
        "ok": judged_ok,
        "backend": (last_judge or {}).get("backend"),
        "reasons": (last_judge or {}).get("reasons") or [],
        "attempts": attempt,
    }
    if not judged_ok:
        log(f"[警告] 判官 {max_attempts} 轮仍未通过，落盘最后一版合法产物（可人工编辑修复）")
    import plan_adapt
    out = plan_adapt.save_plan(project_dir, name, last_valid)
    log(f"[完成] 平面图 -> {out}（判官 {'通过' if judged_ok else '未通过'}，{attempt} 轮）")
    return {"ok": True, "path": out, "plan": last_valid, "judge": last_judge,
            "attempts": attempt, "judged_ok": judged_ok}


def existing_scene_refs(project_dir):
    """推演/ 下已有平面图的 scene_ref 集合（同场景一张即可，重名不再重复出稿）。"""
    covered = set()
    for fp in glob.glob(os.path.join(project_dir, "推演", "平面图_*.plan.json")):
        try:
            plan = json.load(open(fp, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if plan.get("scene_ref"):
            covered.add(str(plan["scene_ref"]))
    return covered


def draft_missing_scene_plans(project_dir, vendor=None, log=print, gen_chat=None, judge_fn=None,
                              overwrite=False):
    """为还没有平面图的场景资产出初稿（extract 收尾自动调用；--all-scenes 同路）。
    fail-soft：无可用 text 厂商打一条信息直接返回；逐场景 try/except 只警告不阻断。
    overwrite=False 时已有 scene_ref 平面图的场景跳过。返回 {"生成","跳过","失败"} 计数。"""
    rows = list_scenes(project_dir)
    scene_ids = []
    for r in rows:
        sid = str(r.get("id") or r.get("name") or "")
        if sid and sid not in scene_ids:
            scene_ids.append(sid)
    if not scene_ids:
        log("[信息] 无场景资产，跳过平面图初稿")
        return {"生成": 0, "跳过": 0, "失败": 0}
    covered = set() if overwrite else existing_scene_refs(project_dir)
    todo = [s for s in scene_ids if s not in covered]
    if not todo:
        log(f"[信息] {len(scene_ids)} 个场景均已有平面图，跳过初稿")
        return {"生成": 0, "跳过": len(scene_ids), "失败": 0}
    if gen_chat is None:
        try:
            cli = VendorClient(pick_vendor("text", vendor))
        except VendorError as exc:
            log(f"[信息] 平面图初稿跳过：{exc}")
            return {"生成": 0, "跳过": len(scene_ids), "失败": 0}
        log(f"[信息] 平面图初稿厂商: {cli.id} / {cli.model('text')}")
        gen_chat = lambda msgs: cli.chat(msgs, kind="text", max_tokens=4000, timeout=420,
                                         temperature=0.4, extra=FAST_THINK)
    stats = {"生成": 0, "跳过": len(scene_ids) - len(todo), "失败": 0}
    import plan_frames
    for sid in todo:
        try:
            # 生成后顺手渲染底图 PNG + 注册素材图派生（plan_frames 内 fail-soft，不影响计数语义）
            r = plan_frames.ensure_scene_plan(project_dir, sid, draft=True,
                                              gen_chat=gen_chat, judge_fn=judge_fn, log=log)
            if r["created"]:
                stats["生成"] += 1
            elif r.get("error"):
                stats["失败"] += 1
            else:
                stats["跳过"] += 1   # 理论上 covered 已拦；并发/边界下兜底不重复计数
        except Exception as exc:
            stats["失败"] += 1
            log(f"[警告] 场景「{sid}」平面图初稿失败（可稍后在⑥平面推演页生成）：{exc}")
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project_dir")
    ap.add_argument("name", nargs="?", default=None)
    ap.add_argument("--scene", default=None, help="场景资产 id：描述取自 素材/场景.json，plan 写 scene_ref，名缺省=场景 id")
    ap.add_argument("--extra-desc", default=None, help="--scene 的补充描述（追加在场景描述后）")
    ap.add_argument("--all-scenes", action="store_true", help="为全部场景资产逐个生成（名字=场景 id）")
    ap.add_argument("--skip-existing", action="store_true", help="--all-scenes 时跳过已有平面图的场景（默认全部重生成）")
    ap.add_argument("--scene-desc", default=None)
    ap.add_argument("--desc-file", default=None)
    ap.add_argument("--keyframe", default=None)
    ap.add_argument("--zone", default=None)
    ap.add_argument("--vendor", default=None)
    ap.add_argument("--max-attempts", type=int, default=3)
    a = ap.parse_args()
    proj = os.path.abspath(a.project_dir)
    if a.all_scenes:
        stats = draft_missing_scene_plans(proj, vendor=a.vendor, overwrite=not a.skip_existing)
        print(f"[完成] 全部场景平面图：生成 {stats['生成']} / 跳过 {stats['跳过']} / 失败 {stats['失败']}")
        sys.exit(0 if stats["生成"] or stats["跳过"] else 1)
    desc = a.scene_desc
    if not desc and a.desc_file and os.path.isfile(a.desc_file):
        desc = open(a.desc_file, encoding="utf-8").read()
    if a.scene:
        extra = a.extra_desc or desc   # --scene 与 --scene-desc 同给时后者当补充描述
        try:
            r = generate(proj, a.name, keyframe=a.keyframe, zone=a.zone, vendor=a.vendor,
                         max_attempts=a.max_attempts, scene=a.scene, extra_desc=extra)
        except (ValueError, VendorError) as exc:
            print(f"[错误] {exc}")
            sys.exit(1)
        print("OUTPUT:" + r["path"])
        return
    if not a.name:
        print("[错误] 需要 平面图名称 或 --scene/--all-scenes 之一")
        sys.exit(1)
    if not desc and not a.keyframe:
        print("[错误] 需要 --scene-desc/--desc-file 或 --keyframe 之一")
        sys.exit(1)
    try:
        r = generate(proj, a.name, scene_desc=desc,
                     keyframe=a.keyframe, zone=a.zone, vendor=a.vendor,
                     max_attempts=a.max_attempts)
    except (ValueError, VendorError) as exc:
        print(f"[错误] {exc}")
        sys.exit(1)
    print("OUTPUT:" + r["path"])


if __name__ == "__main__":
    main()
