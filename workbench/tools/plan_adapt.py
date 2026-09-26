# -*- coding: utf-8 -*-
"""plan v1 → 世界坐标转换（strategy_map / shot_diagram 的 --plan 输入模式共用）

plan 画布：原点左上，x 向右、y 向下，单位米（见 docs/平面图规范-2D-plan-v1.md）。
渲染世界系（战略图/平面图俯视画布）：x 向右、z 纵深向上、场地中心为原点。
转换：wx = x - w/2，wz = h/2 - y —— 保持 plan 的"上"画在画布上方。
"""
import sys, os, json, math
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def _safe_name(name):
    """平面图名清洗：只允许单层文件名（与 server.safe_proj 同口径）。"""
    name = str(name or "").replace("/", "").replace("\\", "")
    name = os.path.basename(name)
    return name if name and name not in (".", "..") else ""


def plan_path(project_dir, name):
    """plan v1 落盘约定：projects/<项目>/推演/平面图_<名>.plan.json（名经清洗，非法返回 None）。"""
    nm = _safe_name(name)
    if not nm:
        return None
    return os.path.normpath(os.path.join(project_dir, "推演", "平面图_" + nm + ".plan.json"))


def save_plan(project_dir, name, plan, snapshot=True):
    """plan 数据文件唯一写入口（gen_plan / server plan/save / 将来编辑页同路）：
    覆写前 versions.snapshot，UTF-8 indent=1 落盘。返回落盘路径；名非法抛 ValueError。"""
    path = plan_path(project_dir, name)
    if not path:
        raise ValueError("平面图名不合法: %r" % (name,))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if snapshot and os.path.isfile(path):
        import versions as _V
        _V.snapshot(path)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(plan, fh, ensure_ascii=False, indent=1)
    return path


def load_plan(path):
    """读 plan.json（不存在/解析失败返回 None，调用方自行报错）。"""
    try:
        with open(path, encoding="utf-8") as fh:
            plan = json.load(fh)
        return plan if isinstance(plan, dict) else None
    except Exception:
        return None


def shot_scene_ref(shot):
    """分镜 shot 的场景资产 id：scene_ref='@scene:loc_x' → 'loc_x'；无 → ''。"""
    v = str((shot or {}).get("scene_ref") or "")
    return v[len("@scene:"):] if v.startswith("@scene:") else ""


def choose_plan(plans, shots):
    """从候选 plans（mtime 降序）为分镜选底图：按 scene_ref 匹配镜数最多者胜
    （plans 已按 mtime 降序，同分自然取新）；全部无匹配/无 scene_ref 回退 plans[0]。
    plans 为空返回 None。

    返回值上盖 `choice`={mode, score, total}：零匹配回退是**有意保留的行为**（老项目 scene_ref
    填充率为 0，硬拦会让一批项目当场出不了图），但"这是回退"必须能传到产物与页面上，
    不能只活在任务日志里。**必须原地盖、返回同一个对象**：`plan_frames.choose_board_plan`
    靠身份比较（`pl is plan`）把选中的 plan 映射回文件路径，返回副本会让它恒定取不到路径、
    平面图参考帧整批静默消失（本轮改初版就踩过，靠既有 plan_frames 用例逼出来）。
    """
    if not plans:
        return None
    best, best_score = plans[0], 0
    for p in plans:
        ref = str(p.get("scene_ref") or "")
        if not ref:
            continue
        score = sum(1 for s in (shots or []) if shot_scene_ref(s) == ref)
        if score > best_score:
            best, best_score = p, score
    best["choice"] = {"mode": "matched" if best_score else "fallback",
                      "score": best_score, "total": len(shots or [])}
    return best


def pw(plan, x, y):
    """plan 画布坐标 → 世界 (x, z)。"""
    canvas = plan.get("canvas") or {}
    w = float(canvas.get("w") or 12.0)
    h = float(canvas.get("h") or 9.0)
    return float(x) - w / 2.0, h / 2.0 - float(y)


def _hex_rgb(color, default=(180, 180, 180)):
    """#rrggbb → (r,g,b)；非法值给默认灰。"""
    s = str(color or "").lstrip("#")
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except Exception:
        return default


def plan_walls_world(plan):
    """room 墙多边形 → 世界坐标点列（无 room 返回 []）。"""
    room = plan.get("room") or {}
    out = []
    for v in room.get("walls") or []:
        if isinstance(v, (list, tuple)) and len(v) >= 2:
            out.append(list(pw(plan, v[0], v[1])))
    return out


def plan_props_world(plan):
    """props → 世界坐标陈设清单 [{id,label,shape,pos:[x,z],size,rot}]。
    rot 取负：plan 里逆时针角度在 y 向下的画布上，换到 z 向上世界系后视觉方向相反。"""
    out = []
    for p in plan.get("props") or []:
        if not isinstance(p, dict):
            continue
        c = p.get("center")
        if not isinstance(c, (list, tuple)) or len(c) < 2:
            continue
        x, z = pw(plan, c[0], c[1])
        out.append({"id": p.get("id"), "label": p.get("label") or p.get("id"),
                    "shape": p.get("shape") or "rect", "pos": [x, z],
                    "size": list(p.get("size") or []), "rot": -(float(p.get("rot") or 0.0))})
    return out


def plan_zones_world(plan):
    """zones rect [x,y,w,h] → 世界 [x0,z0,x1,z1]。"""
    out = []
    for zn in plan.get("zones") or []:
        if not isinstance(zn, dict):
            continue
        r = zn.get("rect")
        if not isinstance(r, (list, tuple)) or len(r) < 4:
            continue
        x0, z1 = pw(plan, r[0], r[1])
        x1, z0 = pw(plan, r[0] + r[2], r[1] + r[3])
        out.append({"id": zn.get("id"), "label": zn.get("label") or zn.get("id"),
                    "rect": [x0, z0, x1, z1], "scene_ref": zn.get("scene_ref")})
    return out


def plan_openings_world(plan):
    """openings（wall 段 + offset）→ 世界坐标出入口点 [{pos:[x,z],kind,at}]，供底图画门/窗。"""
    room = plan.get("room") or {}
    walls = room.get("walls") or []
    out = []
    for op in room.get("openings") or []:
        if not isinstance(op, dict):
            continue
        wi = op.get("wall")
        if not isinstance(wi, int) or wi < 0 or wi >= len(walls):
            continue
        a, b = walls[wi], walls[(wi + 1) % len(walls)]
        seg = math.hypot(b[0] - a[0], b[1] - a[1])
        if seg <= 0:
            continue
        t = min(1.0, max(0.0, float(op.get("offset") or 0.0) / seg))
        x = a[0] + (b[0] - a[0]) * t
        y = a[1] + (b[1] - a[1]) * t
        wx, wz = pw(plan, x, y)
        out.append({"pos": [wx, wz], "kind": op.get("kind") or "door", "width": op.get("width") or 1.0})
    return out


def plan_cameras_world(plan):
    """cameras → 白模 3D 坐标 {id,pos:[x,1.6,z],look:[x,1.4,z],fov}（机位高 1.6，视点 1.4）。"""
    out = []
    for cam in plan.get("cameras") or []:
        if not isinstance(cam, dict):
            continue
        pos, look = cam.get("pos"), cam.get("look")
        if not (isinstance(pos, (list, tuple)) and len(pos) >= 2):
            continue
        x, z = pw(plan, pos[0], pos[1])
        if isinstance(look, (list, tuple)) and len(look) >= 2:
            lx, lz = pw(plan, look[0], look[1])
        else:
            lx, lz = 0.0, 0.0
        out.append({"id": cam.get("id"), "pos": [x, 1.6, z], "look": [lx, 1.4, lz],
                    "fov": float(cam.get("fov") or 50)})
    return out


def plan_actor_positions(plan):
    """actors → {id: (x, z)} 世界坐标站位。"""
    out = {}
    for a in plan.get("actors") or []:
        if not isinstance(a, dict) or not a.get("id"):
            continue
        pos = a.get("pos")
        if isinstance(pos, (list, tuple)) and len(pos) >= 2:
            out[str(a["id"])] = pw(plan, pos[0], pos[1])
    return out


def plan_actors_map(plan):
    """actors → 战略图/平面图 actors 表 {id: {name, shirt}}（shirt 由 color 转 RGB）。"""
    out = {}
    for a in plan.get("actors") or []:
        if not isinstance(a, dict) or not a.get("id"):
            continue
        out[str(a["id"])] = {"name": a.get("name") or a["id"], "shirt": list(_hex_rgb(a.get("color")))}
    return out


def plan_paths_world(plan):
    """paths → [{actor, points:[[x,z,t]…], style}]（世界坐标，供描走位轨迹）。"""
    out = []
    for pth in plan.get("paths") or []:
        if not isinstance(pth, dict):
            continue
        pts = []
        for p in pth.get("points") or []:
            if isinstance(p, (list, tuple)) and len(p) >= 3:
                x, z = pw(plan, p[0], p[1])
                pts.append([x, z, float(p[2])])
        if len(pts) >= 2:
            out.append({"actor": pth.get("actor"), "points": pts, "style": pth.get("style") or "walk"})
    return out


def plan_scene(plan):
    """整图 → strategy_map 场景字典（layout DSL 同构：bounds/furniture/entry/markers + walls/zones 扩展）。"""
    canvas = plan.get("canvas") or {}
    return {"id": "_plan", "name": plan.get("name") or "平面图", "interior": bool(plan.get("room")),
            "layout": {"bounds": {"w": float(canvas.get("w") or 12.0),
                                   "d": float(canvas.get("h") or 9.0)},
                       "walls": plan_walls_world(plan),
                       "furniture": plan_props_world(plan),
                       "entry": plan_openings_world(plan),
                       "zones": plan_zones_world(plan),
                       "paths": plan_paths_world(plan)}}
