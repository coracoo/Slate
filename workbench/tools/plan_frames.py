# -*- coding: utf-8 -*-
"""plan_frames.py — 平面图渲染与编译参考回流（平面图=场景资产派生子素材 + V 编译注入的公共底座）

三条能力：
1) 场景平面图派生（任务 A）：plan v1 JSON → 渲染底图 PNG 存 素材/场景/<scene_id>__plan.png，
   并注册进 素材/素材图.json（场景 zone 的 <scene_id>__plan 条目，usage=plan，
   parent_ref/derived_from=@scene:<id>）——资产详情弹窗派生区可见，幂等。
   ensure_scene_plan 一站式：找已有 plan →（可选）LLM 草稿 → 渲染 → 注册，全程 fail-soft。
2) 分镜平面图帧（任务 C 原料）：整板逐镜 PNG（shot_diagram --plan 底图叠加模式）→
   推演/平面图帧_<分镜名>/S##.png，按 plan/分镜 mtime+size 签名幂等，成员帧可按 V 复制别名 Vxx_S##.png。
3) V 编译参考帧（任务 C）：plan_ref_frames 按成员 S 选 plan、确保帧、按预算均匀抽样（必含首尾），
   产出 production_requests 兼容的 refs 记录（path/sha256/purpose/shot_id/frame_role=reference_image）。

用法: python plan_frames.py <项目目录> [--scene 场景id | --all-scenes] [--board 分镜文件] [--no-draft]
退出码 0；stdout 打印每步产物路径。
"""
import sys, os, json, glob, shutil, subprocess, argparse, time
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gen_plan
import plan_adapt
import production_media as _PM
import versions as _V

SHOT_DIAGRAM = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shot_diagram.py")
PLAN_PROMPT = "2D 俯视平面图底图（场景空间布局派生素材，plan v1 渲染）"


# ── 路径约定 ──────────────────────────────────────────────

def plan_png_relpath(scene_id):
    """场景平面图底图的项目相对路径（正斜杠，供 refs/前端 /media 使用）。"""
    return "素材/场景/%s__plan.png" % scene_id


def frames_dir_for_board(project_dir, board_name):
    """分镜平面图帧目录：推演/平面图帧_<分镜名（去扩展名）>/。"""
    base = os.path.splitext(os.path.basename(board_name))[0]
    return os.path.join(project_dir, "推演", "平面图帧_" + base)


def _load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def _sig(path):
    """文件签名 mtime:size（幂等判断用；不存在返回 ''）。"""
    try:
        st = os.stat(path)
        return "%d:%d" % (int(st.st_mtime), st.st_size)
    except OSError:
        return ""


# ── 1) 场景平面图派生 ─────────────────────────────────────

def find_plan_for_scene(project_dir, scene_id):
    """按 scene_ref 在 推演/ 全部 plan 中匹配场景；命中取 mtime 最新；无 → None。"""
    best, best_mt = None, -1.0
    for fp in glob.glob(os.path.join(project_dir, "推演", "平面图_*.plan.json")):
        plan = _load_json(fp)
        if not isinstance(plan, dict):
            continue
        if str(plan.get("scene_ref") or "") == str(scene_id):
            mt = os.path.getmtime(fp)
            if mt > best_mt:
                best, best_mt = fp, mt
    return best


def render_plan_base_png(plan, out_path, width=960, height=720):
    """plan v1 JSON → 单张俯视底图 PNG（复用 shot_diagram 画笔：墙/陈设/区域/走位轨迹/角色点）。
    窗口=底图实体+角色包围盒（空则 [-5,5]²）；无相机不画扇形。"""
    import shot_diagram as SD
    base = {"walls": plan_adapt.plan_walls_world(plan),
            "props": plan_adapt.plan_props_world(plan),
            "zones": plan_adapt.plan_zones_world(plan),
            "paths": plan_adapt.plan_paths_world(plan)}
    actors = plan_adapt.plan_actors_map(plan)
    positions = plan_adapt.plan_actor_positions(plan)
    shot = {"id": "", "scene": "room" if plan.get("room") else "field",
            "_actor_positions": positions}
    xs, zs = [], []
    for x, z in positions.values():
        xs.append(x); zs.append(z)
    for p in base["walls"]:
        xs.append(p[0]); zs.append(p[1])
    for f in base["props"]:
        xs.append(f["pos"][0]); zs.append(f["pos"][1])
    for zn in base["zones"]:
        r = zn.get("rect") or [0, 0, 0, 0]
        xs += [r[0], r[2]]; zs += [r[1], r[3]]
    for pth in base["paths"]:
        for p in pth.get("points") or []:
            xs.append(p[0]); zs.append(p[1])
    if not xs:
        xs, zs = [-5, 5], [-5, 5]
    M = SD.setup((min(xs), max(xs)), (min(zs), max(zs)), width, height)
    img = SD.Image.new("RGB", (width, height))
    dr = SD.ImageDraw.Draw(img, "RGBA")
    SD.draw_shot(dr, width, height, M, shot, actors, None, base=base)
    img.save(out_path)
    return out_path


def ensure_plan_png(project_dir, scene_id, plan_path, log=None):
    """plan → 素材/场景/<sid>__plan.png；mtime 不旧于 plan 直接复用（幂等），覆写前快照。"""
    rel = plan_png_relpath(scene_id)
    out = os.path.join(project_dir, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    if os.path.isfile(out) and os.path.getmtime(out) >= os.path.getmtime(plan_path):
        return rel
    plan = _load_json(plan_path)
    if not isinstance(plan, dict):
        raise ValueError("plan 读取失败: " + plan_path)
    if os.path.isfile(out):
        _V.snapshot(out)
    render_plan_base_png(plan, out)
    if log:
        log("  [plan] 渲染平面图底图 " + rel)
    return rel


def register_plan_derivative(project_dir, scene_id, png_rel, index=None):
    """素材图.json 场景 zone 注册 <sid>__plan 派生条目（幂等：一致返回 False）。
    index 传入时只改内存（gen_asset_images main 末尾统一落盘，避免被其 dump 覆盖）；
    不传则自读自写（带版本快照）。条目带 parent_ref/derived_from=@scene:<sid>、usage=plan，
    source_episode_ids 从场景母条目拷贝（前端按集过滤仍可见）。"""
    own = index is None
    if own:
        import gen_asset_images
        index = gen_asset_images.load_asset_index(project_dir)
    zone = index.setdefault("场景", {})
    ent = zone.get(scene_id) or {}
    scene_name = ent.get("name") or scene_id
    key = scene_id + "__plan"
    want = {"path": png_rel, "name": str(scene_name) + "·平面图", "usage": "plan",
            "prompt": PLAN_PROMPT,
            "parent_ref": "@scene:" + scene_id,
            "derived_from": "@scene:" + scene_id,
            "relation": "derived_from"}
    episodes = ent.get("source_episode_ids")
    if not episodes:
        # 索引母条目可能尚未生成（场景图未出/未入索引）；回退读场景资产档案的集数，
        # 保证派生平面图在前端"按集过滤"下与母资产同可见。
        try:
            with open(os.path.join(project_dir, "素材", "场景.json"), encoding="utf-8") as fh:
                scenes_doc = json.load(fh)
            row = next((s for s in scenes_doc.get("scenes", [])
                        if str(s.get("id")) == scene_id), None)
            episodes = (row or {}).get("source_episode_ids") or (row or {}).get("episodes")
        except (OSError, ValueError, TypeError):
            episodes = None
    if episodes:
        want["source_episode_ids"] = list(episodes)
    cur = zone.get(key)
    if cur and cur.get("path") == want["path"] and cur.get("usage") == "plan":
        return False
    merged = dict(cur or {})
    merged.update(want)
    zone[key] = merged
    if own:
        idx_path = os.path.join(project_dir, "素材", "素材图.json")
        os.makedirs(os.path.dirname(idx_path), exist_ok=True)
        if os.path.isfile(idx_path):
            _V.snapshot(idx_path)
        with open(idx_path, "w", encoding="utf-8") as fh:
            json.dump(index, fh, ensure_ascii=False, indent=1)
    return True


def ensure_scene_plan(project_dir, scene_id, vendor=None, draft=True, gen_chat=None,
                      judge_fn=None, extra_desc=None, log=None):
    """一站式确保场景平面图派生：找 plan →（无且 draft）LLM 草稿 → 渲染 PNG → 注册素材图。
    全程 fail-soft：无厂商/生成失败/渲染失败只告警不抛。返回 {"created","plan","png","registered"}。"""
    log = log or (lambda *a, **k: None)
    out = {"created": False, "plan": None, "png": None, "registered": False, "error": None}
    path = find_plan_for_scene(project_dir, scene_id)
    if path is None and draft:
        try:
            r = gen_plan.generate(project_dir, scene=scene_id, extra_desc=extra_desc,
                                  gen_chat=gen_chat, judge_fn=judge_fn, vendor=vendor, log=log)
            path = r["path"]
            out["created"] = True
        except Exception as exc:
            out["error"] = str(exc)
            log("  [plan] 场景「%s」平面图生成失败（跳过）: %s" % (scene_id, exc))
            return out
    if path is None:
        return out
    out["plan"] = os.path.relpath(path, project_dir).replace(os.sep, "/")
    try:
        rel = ensure_plan_png(project_dir, scene_id, path, log=log)
        out["png"] = rel
        out["registered"] = register_plan_derivative(project_dir, scene_id, rel)
    except Exception as exc:
        log("  [plan] 场景「%s」平面图渲染/注册失败（跳过）: %s" % (scene_id, exc))
    return out


# ── 2) 分镜平面图帧 ───────────────────────────────────────

def ensure_plan_frames(project_dir, board_path, plan_path, member_ids=None, v_label="", log=None):
    """整板逐镜平面图帧（shot_diagram --plan 底图叠加）→ 推演/平面图帧_<分镜名>/。
    幂等：_meta.json 记 plan/分镜签名（mtime:size），签名一致且成员帧齐全直接复用；
    重渲前对旧帧快照。v_label 非空时把成员帧复制别名 <v_label>_<sid>.png（V 维度展示/引用）。
    返回 {shot_id: 项目相对路径}（渲染失败/无成员返回 {}）。"""
    log = log or (lambda *a, **k: None)
    board = _load_json(board_path)
    plan = _load_json(plan_path)
    if not isinstance(board, dict) or not isinstance(plan, dict):
        return {}
    all_ids = [str(s.get("id")) for s in board.get("shots") or [] if s.get("id")]
    keep = [i for i in (member_ids or all_ids) if i in set(all_ids)]
    if not keep:
        return {}
    outdir = frames_dir_for_board(project_dir, os.path.basename(board_path))
    meta_p = os.path.join(outdir, "_meta.json")
    meta = _load_json(meta_p, {}) or {}

    def frame_ok(sid):
        p = os.path.join(outdir, sid + ".png")
        return os.path.isfile(p) and os.path.getsize(p) > 0

    # 规范口径：同场景的镜共用一张底图，跨场景必须换图（平面图规范-2D-plan-v1.md）。
    # 曾整板只喂一张 plan：实测 09_仙 剧本_E1 的 15 镜分属 3 个场景，即使按多数镜匹配底图
    # 仍有 8 镜画在他场墙纸上——而这些图正是 ⑦ 编译 V 时喂给视频模型的空间参考帧。
    groups, order = {}, []
    for sh in board.get("shots") or []:
        if not str(sh.get("id") or ""):
            continue
        key = plan_adapt.shot_scene_ref(sh)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(sh)
    own_scene = str(plan.get("scene_ref") or "")
    plan_for = {}
    for key in order:
        pth = plan_path
        if key and key != own_scene:
            _, cand = choose_board_plan(project_dir, groups[key])
            pth = cand or plan_path
        plan_for[key] = pth
    sigs = ";".join(sorted({f"{os.path.basename(p)}:{_sig(p)}" for p in plan_for.values()}))

    fresh = (meta.get("plan_sigs") == sigs
             and meta.get("board_sig") == _sig(board_path)
             and all(frame_ok(s) for s in keep))
    if not fresh:
        os.makedirs(outdir, exist_ok=True)
        for s in all_ids:
            p = os.path.join(outdir, s + ".png")
            if os.path.isfile(p):
                _V.snapshot(p)
        for key in order:
            ids = [str(sh.get("id")) for sh in groups[key]]
            cmd = [sys.executable, SHOT_DIAGRAM, os.path.abspath(board_path),
                   "--plan", os.path.abspath(plan_for[key]), "--outdir", os.path.abspath(outdir),
                   "--shots", ",".join(ids)]
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace")
            if proc.returncode != 0:
                log("  [plan] 平面图帧渲染失败（%s）: %s"
                    % (key or "未绑定场景", (proc.stderr or proc.stdout or "").strip()[-200:]))
                return {}
            for line in (proc.stdout or "").splitlines():   # 底图不符等告警抬到任务日志
                if "[警告]" in line:
                    log("  " + line.strip())
        with open(meta_p, "w", encoding="utf-8") as fh:
            json.dump({"plans": sorted({os.path.basename(p) for p in plan_for.values()}),
                       "board": os.path.basename(board_path),
                       "plan_sigs": sigs, "board_sig": _sig(board_path),
                       "shots": keep, "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")},
                      fh, ensure_ascii=False, indent=1)
        log("  [plan] 平面图帧 %d 张（按 %d 张场景底图分组）-> %s"
            % (len(all_ids), len(set(plan_for.values())), os.path.relpath(outdir, project_dir)))
    out = {}
    for s in keep:
        src = os.path.join(outdir, s + ".png")
        if not os.path.isfile(src):
            continue
        dst = src
        if v_label:
            dst = os.path.join(outdir, str(v_label) + "_" + s + ".png")
            if (not os.path.isfile(dst)) or os.path.getmtime(dst) < os.path.getmtime(src):
                shutil.copy2(src, dst)
        out[s] = os.path.relpath(dst, project_dir).replace(os.sep, "/")
    return out


# ── 3) V 编译参考帧 ───────────────────────────────────────

def choose_board_plan(project_dir, shots):
    """为分镜选 plan：推演/ 全部 plan 交 plan_adapt.choose_plan（scene_ref 多数镜，回退最新）。
    返回 (plan, 绝对路径)；无 plan 文件 → (None, None)。"""
    cands = []
    for fp in glob.glob(os.path.join(project_dir, "推演", "平面图_*.plan.json")):
        plan = _load_json(fp)
        if isinstance(plan, dict):
            cands.append((os.path.getmtime(fp), plan, fp))
    if not cands:
        return None, None
    cands.sort(key=lambda t: -t[0])   # mtime 降序（choose_plan 同分取新）
    plan = plan_adapt.choose_plan([c[1] for c in cands], shots)
    if plan is None:
        return None, None
    for _, pl, fp in cands:
        if pl is plan:
            return pl, fp
    return None, None


def plan_ref_frames(project_dir, board_name, shots, v_label, budget, log=None):
    """V 编译参考帧：成员 S 的平面图帧按预算抽样（必含首尾；budget=1 只取首），
    产出 production_requests refs 记录。无 plan / 渲染失败 / budget<=0 → []（静默跳过）。"""
    log = log or (lambda *a, **k: None)
    if budget <= 0 or not shots:
        return []
    board_path = os.path.join(project_dir, "分镜", os.path.basename(board_name))
    if not os.path.isfile(board_path):
        return []
    plan, plan_path = choose_board_plan(project_dir, shots)
    if plan is None:
        return []
    member = [str(s.get("id")) for s in shots if s.get("id")]
    frames = ensure_plan_frames(project_dir, board_path, plan_path,
                                member_ids=member, v_label=v_label, log=log)
    if not frames:
        return []
    ordered = [s for s in member if s in frames]
    if not ordered:
        return []
    if len(ordered) > budget:
        if budget == 1:
            keep = ordered[:1]
        else:
            step = (len(ordered) - 1) / float(budget - 1)
            picked = sorted({ordered[int(round(i * step))] for i in range(budget)},
                            key=ordered.index)
            keep = picked
    else:
        keep = ordered
    refs = []
    for sid in keep:
        abs_p = os.path.join(project_dir, frames[sid].replace("/", os.sep))
        try:
            dg = _PM.digest(abs_p)
        except OSError:
            continue
        refs.append({"path": frames[sid], "sha256": dg,
                     "purpose": "平面推演参考帧", "shot_id": sid,
                     "frame_role": "reference_image"})
    return refs


# ── CLI ───────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="平面图派生渲染：场景底图注册 / 分镜平面图帧")
    ap.add_argument("project_dir")
    ap.add_argument("--scene", default=None, help="场景资产 id：确保 plan→渲染→注册（缺省出草稿）")
    ap.add_argument("--all-scenes", action="store_true", help="全部场景资产逐个 ensure")
    ap.add_argument("--board", default=None, help="分镜文件名（分镜/ 下）：渲染整板平面图帧")
    ap.add_argument("--no-draft", action="store_true", help="只渲染已有 plan，不出 LLM 草稿")
    ap.add_argument("--vendor", default=None)
    a = ap.parse_args()
    proj = os.path.abspath(a.project_dir)
    if a.all_scenes or a.scene:
        sids = [a.scene] if a.scene else [
            str(r.get("id") or r.get("name") or "") for r in gen_plan.list_scenes(proj)]
        for sid in [s for s in sids if s]:
            r = ensure_scene_plan(proj, sid, vendor=a.vendor, draft=not a.no_draft, log=print)
            print("[ensure] %s: created=%s png=%s registered=%s" % (sid, r["created"], r["png"], r["registered"]))
    if a.board:
        board_path = os.path.join(proj, "分镜", os.path.basename(a.board))
        board = _load_json(board_path, {}) or {}
        plan, plan_path = choose_board_plan(proj, board.get("shots") or [])
        if plan is None:
            print("[信息] 无可用平面图，跳过分镜帧")
        else:
            frames = ensure_plan_frames(proj, board_path, plan_path, log=print)
            print("[完成] 平面图帧 %d 张" % len(frames))
            for sid, rel in frames.items():
                print("  %s -> %s" % (sid, rel))
    if not (a.all_scenes or a.scene or a.board):
        print("[错误] 需要 --scene/--all-scenes/--board 之一")
        sys.exit(1)


if __name__ == "__main__":
    main()
