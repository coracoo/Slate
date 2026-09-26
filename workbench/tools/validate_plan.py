# -*- coding: utf-8 -*-
"""2D 平面图校验器（plan v1，确定性闸：不合格不落盘）

规范见 workbench/docs/平面图规范-2D-plan-v1.md 第二节。
坐标系：画布原点左上，x 向右、y 向下（俯视直觉），单位米。

错误（阻断）：空图 / 坐标越界或尺寸非正 / id 重复与引用悬空 /
  openings 越界 / paths 时间与点数 / fov 范围。
警告（不阻断）：演员道具在墙外 / 路径穿家具 / 相机在墙外 / prop 重叠 >30%。

用法: python validate_plan.py <plan.json> [--scenes <场景.json>]
  --scenes 提供项目场景清单（素材/场景.json），用于 zones.scene_ref 悬空校验；
  未提供时跳过该子项（规范第二节说明）。exit 0=通过 / 1=有错误。
"""
import sys, os, json, math, argparse

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

EPS = 1e-9
OVERLAP_WARN = 0.30     # prop 重叠面积比警告阈值（占较小 prop 面积）
FOV_MIN, FOV_MAX = 5.0, 170.0
FOV_DEFAULT = 50.0


def _err(code, path, message, severity="error"):
    return {"code": code, "path": path, "message": message, "severity": severity}


# ── 几何小函数（不引新依赖） ──────────────────────────────────────────

def _num(v):
    """转 float；非数值/非有限返回 None。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def point_in_polygon(pt, poly):
    """射线法：pt=[x,y] 是否在多边形 poly=[[x,y],...] 内（含边界算在内）。"""
    x, y = pt
    n = len(poly)
    if n < 3:
        return False
    inside = False
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        # 边界命中
        cross = (x - x1) * (y2 - y1) - (y - y1) * (x2 - x1)
        if abs(cross) < EPS and min(x1, x2) - EPS <= x <= max(x1, x2) + EPS \
                and min(y1, y2) - EPS <= y <= max(y1, y2) + EPS:
            return True
        if (y1 > y) != (y2 > y):
            xi = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if xi > x:
                inside = not inside
    return inside


def rect_corners(center, size, rot_deg):
    """旋转矩形四角（rot=度，逆时针）。返回 4 点列表。"""
    cx, cy = center
    hw, hh = size[0] / 2.0, size[1] / 2.0
    a = math.radians(rot_deg or 0.0)
    ca, sa = math.cos(a), math.sin(a)
    pts = []
    for dx, dy in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)):
        pts.append([cx + dx * ca - dy * sa, cy + dx * sa + dy * ca])
    return pts


def _segs_intersect(p1, p2, p3, p4):
    """两线段是否相交（含贴边）。"""
    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    d1 = cross(p3, p4, p1)
    d2 = cross(p3, p4, p2)
    d3 = cross(p1, p2, p3)
    d4 = cross(p1, p2, p4)
    if ((d1 > EPS and d2 < -EPS) or (d1 < -EPS and d2 > EPS)) and \
       ((d3 > EPS and d4 < -EPS) or (d3 < -EPS and d4 > EPS)):
        return True
    def on(o, a, b, d):
        return abs(d) <= EPS and min(a[0], b[0]) - EPS <= o[0] <= max(a[0], b[0]) + EPS \
            and min(a[1], b[1]) - EPS <= o[1] <= max(a[1], b[1]) + EPS
    return on(p1, p3, p4, d1) or on(p2, p3, p4, d2) or on(p3, p1, p2, d3) or on(p4, p1, p2, d4)


def seg_intersects_rotated_rect(p1, p2, center, size, rot_deg):
    """线段与旋转矩形是否相交：换到矩形局部系后做 AABB 相交。"""
    a = math.radians(rot_deg or 0.0)
    ca, sa = math.cos(-a), math.sin(-a)
    cx, cy = center
    def to_local(p):
        dx, dy = p[0] - cx, p[1] - cy
        return [dx * ca - dy * sa, dx * sa + dy * ca]
    q1, q2 = to_local(p1), to_local(p2)
    hw, hh = size[0] / 2.0, size[1] / 2.0
    # 端点在矩形内
    for q in (q1, q2):
        if abs(q[0]) <= hw + EPS and abs(q[1]) <= hh + EPS:
            return True
    # 与四边相交
    corners = [[-hw, -hh], [hw, -hh], [hw, hh], [-hw, hh]]
    for i in range(4):
        if _segs_intersect(q1, q2, corners[i], corners[(i + 1) % 4]):
            return True
    return False


def prop_aabb(prop):
    """prop 的轴对齐包围盒（circle 按外接正方形；rot 已计入）。"""
    c = prop.get("center") or [0, 0]
    if (prop.get("shape") or "rect") == "circle":
        r = float((prop.get("size") or [0])[0] or 0)
        return c[0] - r, c[1] - r, c[0] + r, c[1] + r
    size = prop.get("size") or [0, 0]
    corners = rect_corners(c, [float(size[0] or 0), float((size[1] if len(size) > 1 else 0) or 0)],
                           _num(prop.get("rot")) or 0.0)
    xs = [p[0] for p in corners]
    ys = [p[1] for p in corners]
    return min(xs), min(ys), max(xs), max(ys)


def _aabb_overlap_ratio(a, b):
    """两 AABB 重叠面积 / 较小面积（近似 prop 重叠比）。"""
    ox = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    oy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ox * oy
    if inter <= 0:
        return 0.0
    aa = max(EPS, (a[2] - a[0]) * (a[3] - a[1]))
    bb = max(EPS, (b[2] - b[0]) * (b[3] - b[1]))
    return inter / min(aa, bb)


# ── 字段收集 ─────────────────────────────────────────────────────────

def _points_of(plan):
    """收集全部需要画布越界校验的点：(x, y, path)。"""
    pts = []
    room = plan.get("room") or {}
    for i, v in enumerate(room.get("walls") or []):
        if isinstance(v, (list, tuple)) and len(v) >= 2:
            pts.append((v[0], v[1], f"room.walls[{i}]"))
    for i, p in enumerate(plan.get("props") or []):
        c = (p or {}).get("center")
        if isinstance(c, (list, tuple)) and len(c) >= 2:
            pts.append((c[0], c[1], f"props[{i}].center"))
    for i, a in enumerate(plan.get("actors") or []):
        c = (a or {}).get("pos")
        if isinstance(c, (list, tuple)) and len(c) >= 2:
            pts.append((c[0], c[1], f"actors[{i}].pos"))
    for i, pth in enumerate(plan.get("paths") or []):
        for j, p in enumerate((pth or {}).get("points") or []):
            if isinstance(p, (list, tuple)) and len(p) >= 2:
                pts.append((p[0], p[1], f"paths[{i}].points[{j}]"))
    for i, cam in enumerate(plan.get("cameras") or []):
        for key in ("pos", "look"):
            c = (cam or {}).get(key)
            if isinstance(c, (list, tuple)) and len(c) >= 2:
                pts.append((c[0], c[1], f"cameras[{i}].{key}"))
    for i, z in enumerate(plan.get("zones") or []):
        r = (z or {}).get("rect")
        if isinstance(r, (list, tuple)) and len(r) >= 4:
            pts.append((r[0], r[1], f"zones[{i}].rect"))
            rx1 = _num(r[0]); ry1 = _num(r[1]); rw = _num(r[2]); rh = _num(r[3])
            if None not in (rx1, ry1, rw, rh):
                pts.append((rx1 + rw, ry1 + rh, f"zones[{i}].rect"))
    return pts


def validate_document(plan, scene_ids=None, scene_id_set=None):
    """校验 plan 文档。scene_ids=可选场景 id/name 集合（zones.scene_ref 允许写 id 或 name）；
    scene_id_set=仅 id 集合，顶层 scene_ref 按规范只认资产 id，不传则退回用 scene_ids 判。"""
    errors, warnings = [], []
    if not isinstance(plan, dict):
        return {"errors": [_err("PLAN_TYPE", "$", "plan 必须是对象")], "warnings": []}

    canvas = plan.get("canvas") or {}
    cw = _num(canvas.get("w"))
    ch = _num(canvas.get("h"))
    if cw is None or ch is None or cw <= 0 or ch <= 0:
        errors.append(_err("CANVAS_SIZE", "canvas", "canvas.w/h 必须是正数（米）"))
        cw = ch = None

    props = [p for p in (plan.get("props") or []) if isinstance(p, dict)]
    actors = [a for a in (plan.get("actors") or []) if isinstance(a, dict)]
    paths = [p for p in (plan.get("paths") or []) if isinstance(p, dict)]
    cameras = [c for c in (plan.get("cameras") or []) if isinstance(c, dict)]
    zones = [z for z in (plan.get("zones") or []) if isinstance(z, dict)]
    room = plan.get("room") if isinstance(plan.get("room"), dict) else None

    # 错误 0：props 与 actors 均为空 = 无效平面图
    if not props and not actors:
        errors.append(_err("EMPTY_PLAN", "$", "props 与 actors 均为空，不是有效平面图"))

    # 错误 1：坐标越界 / 尺寸非正
    if cw is not None:
        for x, y, path in _points_of(plan):
            fx, fy = _num(x), _num(y)
            if fx is None or fy is None:
                errors.append(_err("POINT_INVALID", path, "坐标必须是有限数值"))
            elif fx < -EPS or fx > cw + EPS or fy < -EPS or fy > ch + EPS:
                errors.append(_err("POINT_BOUNDS", path,
                                   f"坐标 ({fx:.2f},{fy:.2f}) 超出画布 {cw:g}x{ch:g}"))
    for i, p in enumerate(props):
        shape = p.get("shape") or "rect"
        size = p.get("size")
        if not isinstance(size, (list, tuple)) or not size:
            errors.append(_err("SIZE_INVALID", f"props[{i}].size", "size 必须是非空数组"))
            continue
        need = 1 if shape == "circle" else 2
        if len(size) < need:
            errors.append(_err("SIZE_INVALID", f"props[{i}].size",
                               f"{shape} 的 size 需要 {need} 个分量"))
            continue
        for v in size[:need]:
            fv = _num(v)
            if fv is None or fv <= 0:
                errors.append(_err("SIZE_INVALID", f"props[{i}].size", "尺寸必须为正数"))
                break
    for i, z in enumerate(zones):
        r = z.get("rect")
        if isinstance(r, (list, tuple)) and len(r) >= 4:
            if (_num(r[2]) or 0) <= 0 or (_num(r[3]) or 0) <= 0:
                errors.append(_err("SIZE_INVALID", f"zones[{i}].rect", "区域宽高必须为正数"))

    # 错误 2：id 重复 / 引用悬空
    actor_ids = []
    for domain, rows in (("props", props), ("actors", actors), ("cameras", cameras), ("zones", zones)):
        seen = set()
        for i, row in enumerate(rows):
            rid = str(row.get("id") or "")
            if not rid:
                errors.append(_err("ID_DUP", f"{domain}[{i}].id", "id 缺失"))
            elif rid in seen:
                errors.append(_err("ID_DUP", f"{domain}[{i}].id", f"id 重复: {rid}"))
            seen.add(rid)
        if domain == "actors":
            actor_ids = [str(a.get("id") or "") for a in actors]
    for i, pth in enumerate(paths):
        ref = str(pth.get("actor") or "")
        if ref and ref not in actor_ids:
            errors.append(_err("REF_DANGLING", f"paths[{i}].actor", f"引用不存在的角色: {ref}"))
    if scene_ids is not None:
        for i, z in enumerate(zones):
            ref = str(z.get("scene_ref") or "")
            if ref and ref not in scene_ids:
                errors.append(_err("REF_DANGLING", f"zones[{i}].scene_ref",
                                   f"引用不存在的场景: {ref}"))
        # 顶层 scene_ref 规范定为"场景资产 id"（平面图规范-2D-plan-v1.md:35/104），
        # 下游 choose_plan 按它匹配底图；此前全链不校验，资产重编号后引用静默悬空。
        top_ref = str(plan.get("scene_ref") or "")
        if top_ref and top_ref not in (scene_id_set if scene_id_set is not None else scene_ids):
            errors.append(_err("SCENE_REF_DANGLING", "scene_ref",
                               f"顶层 scene_ref 未命中场景资产 id: {top_ref}（应为 素材/场景.json 的 id，"
                               f"下游按它匹配底图；改过资产 id 请重新生成平面图）"))

    # 错误 3：openings 越界
    if room:
        walls = room.get("walls") or []
        nw = len(walls)
        seg_lens = []
        for i in range(nw):
            a, b = walls[i], walls[(i + 1) % nw]
            if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)) and len(a) >= 2 and len(b) >= 2:
                ax, ay = _num(a[0]), _num(a[1]); bx, by = _num(b[0]), _num(b[1])
                seg_lens.append(math.hypot(bx - ax, by - ay) if None not in (ax, ay, bx, by) else 0.0)
            else:
                seg_lens.append(0.0)
        for i, op in enumerate(room.get("openings") or []):
            if not isinstance(op, dict):
                continue
            path = f"room.openings[{i}]"
            widx = op.get("wall")
            if not isinstance(widx, int) or isinstance(widx, bool) or widx < 0 or widx >= nw:
                errors.append(_err("OPENING_WALL", path + ".wall",
                                   f"墙段序号越界（共 {nw} 段）: {widx}"))
                continue
            off = _num(op.get("offset"))
            wid = _num(op.get("width"))
            if wid is None or wid <= 0:
                errors.append(_err("SIZE_INVALID", path + ".width", "开口宽度必须为正数"))
            if off is None or off < 0:
                errors.append(_err("OPENING_SPAN", path + ".offset", "offset 必须为非负米数"))
            elif wid is not None and wid > 0 and off + wid > seg_lens[widx] + EPS:
                errors.append(_err("OPENING_SPAN", path,
                                   f"offset+width ({off:g}+{wid:g}) 超出墙段 {widx} 长度 {seg_lens[widx]:g}"))

    # 错误 4：paths 时间与点数
    for i, pth in enumerate(paths):
        pts = pth.get("points") or []
        path = f"paths[{i}]"
        if len(pts) < 2:
            errors.append(_err("PATH_POINTS", path + ".points", "路径点数必须 ≥ 2"))
            continue
        prev_t = None
        for j, p in enumerate(pts):
            t = _num(p[2] if isinstance(p, (list, tuple)) and len(p) >= 3 else None)
            if t is None:
                errors.append(_err("PATH_T", f"{path}.points[{j}]", "路径点缺第三分量 t（秒）"))
                break
            if prev_t is not None and t <= prev_t + EPS:
                errors.append(_err("PATH_T", f"{path}.points[{j}]",
                                   f"t 必须严格递增（{prev_t:g} → {t:g}）"))
                break
            prev_t = t

    # 错误 5：fov 范围（缺省 50 不报错）
    for i, cam in enumerate(cameras):
        if "fov" not in cam or cam.get("fov") is None:
            continue
        fov = _num(cam.get("fov"))
        if fov is None or fov < FOV_MIN or fov > FOV_MAX:
            errors.append(_err("FOV_RANGE", f"cameras[{i}].fov",
                               f"fov 必须在 {FOV_MIN:g}–{FOV_MAX:g}°: {cam.get('fov')}"))

    # ── 警告 ──
    walls = (room or {}).get("walls") or []
    wall_poly = [[_num(v[0]), _num(v[1])] for v in walls
                 if isinstance(v, (list, tuple)) and len(v) >= 2
                 and _num(v[0]) is not None and _num(v[1]) is not None]
    has_room = room is not None and len(wall_poly) >= 3

    # 警告 1：演员/道具中心在墙外
    if has_room:
        for i, a in enumerate(actors):
            pos = a.get("pos")
            if isinstance(pos, (list, tuple)) and len(pos) >= 2 and _num(pos[0]) is not None and _num(pos[1]) is not None:
                if not point_in_polygon([_num(pos[0]), _num(pos[1])], wall_poly):
                    warnings.append(_err("OUTSIDE_ROOM", f"actors[{i}].pos",
                                         f"角色 {a.get('id') or i} 在房间墙外", "warning"))
        for i, p in enumerate(props):
            c = p.get("center")
            if isinstance(c, (list, tuple)) and len(c) >= 2 and _num(c[0]) is not None and _num(c[1]) is not None:
                if not point_in_polygon([_num(c[0]), _num(c[1])], wall_poly):
                    warnings.append(_err("OUTSIDE_ROOM", f"props[{i}].center",
                                         f"道具 {p.get('id') or i} 中心在房间墙外", "warning"))
        # 警告 3：相机在 room 外
        for i, cam in enumerate(cameras):
            pos = cam.get("pos")
            if isinstance(pos, (list, tuple)) and len(pos) >= 2 and _num(pos[0]) is not None and _num(pos[1]) is not None:
                if not point_in_polygon([_num(pos[0]), _num(pos[1])], wall_poly):
                    warnings.append(_err("CAMERA_OUTSIDE", f"cameras[{i}].pos",
                                         f"相机 {cam.get('id') or i} 在房间墙外", "warning"))

    # 警告 2：路径线段穿过 prop 矩形（circle 只按规范查矩形）
    rect_props = [p for p in props if (p.get("shape") or "rect") == "rect"
                  and isinstance(p.get("center"), (list, tuple)) and len(p.get("center") or []) >= 2
                  and isinstance(p.get("size"), (list, tuple)) and len(p.get("size") or []) >= 2]
    for i, pth in enumerate(paths):
        pts = [p for p in (pth.get("points") or []) if isinstance(p, (list, tuple)) and len(p) >= 2]
        for j in range(len(pts) - 1):
            p1 = [_num(pts[j][0]), _num(pts[j][1])]
            p2 = [_num(pts[j + 1][0]), _num(pts[j + 1][1])]
            if None in p1 or None in p2:
                continue
            for prop in rect_props:
                size = [_num(prop["size"][0]), _num(prop["size"][1])]
                center = [_num(prop["center"][0]), _num(prop["center"][1])]
                if None in size or None in center or size[0] <= 0 or size[1] <= 0:
                    continue
                if seg_intersects_rotated_rect(p1, p2, center, size, _num(prop.get("rot")) or 0.0):
                    warnings.append(_err("PATH_THROUGH_PROP", f"paths[{i}]",
                                         f"路径 {pth.get('actor') or i} 第 {j + 1} 段穿过道具 {prop.get('id')}",
                                         "warning"))

    # 警告 4：prop 重叠面积比 > 30%（AABB 近似，旋转矩形/圆按外接盒）
    aabbs = [(p.get("id"), prop_aabb(p)) for p in props
             if isinstance(p.get("center"), (list, tuple)) and isinstance(p.get("size"), (list, tuple))]
    for i in range(len(aabbs)):
        for j in range(i + 1, len(aabbs)):
            ratio = _aabb_overlap_ratio(aabbs[i][1], aabbs[j][1])
            if ratio > OVERLAP_WARN:
                warnings.append(_err("PROP_OVERLAP", f"props[{aabbs[i][0]}]/props[{aabbs[j][0]}]",
                                     f"道具 {aabbs[i][0]} 与 {aabbs[j][0]} 重叠约 {ratio * 100:.0f}%（>{OVERLAP_WARN * 100:.0f}%）",
                                     "warning"))

    return {"errors": errors, "warnings": warnings}


def load_scene_id_set(scenes_path):
    """仅场景 id 集合（顶层 scene_ref 绑定用；zones 另用含 name 的宽集合）。"""
    try:
        rows = json.load(open(scenes_path, encoding="utf-8")).get("scenes") or []
    except Exception:
        return set()
    return {str(r["id"]) for r in rows if isinstance(r, dict) and r.get("id")}


def load_scene_ids(scenes_path):
    """从 素材/场景.json 提取场景 id∪name 集合，供 zones.scene_ref 校验。"""
    try:
        rows = json.load(open(scenes_path, encoding="utf-8")).get("scenes") or []
    except Exception:
        return set()
    ids = set()
    for r in rows:
        if isinstance(r, dict):
            if r.get("id"):
                ids.add(str(r["id"]))
            if r.get("name"):
                ids.add(str(r["name"]))
    return ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("--scenes", default=None, help="素材/场景.json（zones.scene_ref 校验用）")
    a = ap.parse_args()
    if not os.path.isfile(a.plan):
        print(f"[错误] 文件不存在: {a.plan}")
        sys.exit(1)
    try:
        plan = json.load(open(a.plan, encoding="utf-8"))
    except ValueError as exc:
        print(f"[错误] JSON 解析失败: {exc}")
        sys.exit(1)
    scene_ids = load_scene_ids(a.scenes) if a.scenes else None
    scene_id_set = load_scene_id_set(a.scenes) if a.scenes else None
    result = validate_document(plan, scene_ids=scene_ids, scene_id_set=scene_id_set)
    for item in result["warnings"]:
        print("  [警告]", item["path"], item["message"])
    for item in result["errors"]:
        print("  [错误]", item["path"], item["message"])
    if result["errors"]:
        print(f"❌ 校验未通过：{len(result['errors'])} 错误，{len(result['warnings'])} 警告")
        sys.exit(1)
    print(f"✅ 校验通过（{len(result['warnings'])} 警告）")
    sys.exit(0)


if __name__ == "__main__":
    main()
