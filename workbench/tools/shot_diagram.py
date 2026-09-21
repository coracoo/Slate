# -*- coding: utf-8 -*-
"""平面运镜+行动图：dialogue 分镜 JSON -> 逐镜俯视调度图（PNG）

参考片场平面图惯例（参考视频同款）：点=角色（角色色标记），扇形=相机视场，
箭头=运镜方向与角色走位。输出 <项目>/推演/平面图_<分镜名>/S##.png。

绘制内容（每镜）：
  - 地面：room 灰 / field 沙色；坐标网格（米）
  - 角色：圆点（shirt 色）+ 名字；与前镜相比的位置变化画虚线走位箭头（含画外→入场）
  - 相机：位置三角 + fov 扇形视线 + look 方向线；运镜箭头（推/拉=沿视线、摇=弧、移=横移、环绕=弧线）
用法: python shot_diagram.py <分镜.json> [--outdir 目录] [--w 800 --h 600]
"""
import sys, os, json, math, argparse
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
from PIL import Image, ImageDraw, ImageFont
from shot_presence import actor_positions
from strategy_map import load_scenes, shot_scene_id

FONT = r"C:\Windows\Fonts\msyh.ttc"


def setup(ax, az, w, h):
    """世界窗口(米) -> 画布映射：x 左右，z 纵深。窗口由相机/角色包围盒外扩。"""
    x0, x1 = ax[0] - 2.5, ax[1] + 2.5
    z0, z1 = az[0] - 2.5, az[1] + 2.5
    span = max(x1 - x0, (z1 - z0) * w / h)
    cx, cz = (x0 + x1) / 2, (z0 + z1) / 2
    x0, x1 = cx - span / 2, cx + span / 2
    z0, z1 = cz - span * h / w / 2, cz + span * h / w / 2
    def M(x, z):
        return ((x - x0) / (x1 - x0) * w, h - (z - z0) / (z1 - z0) * h)
    return M


def draw_shot(dr, W, H, M, shot, actors, prev_pos, fov_def=48):
    fld = (shot.get("scene") == "field")
    dr.rectangle((0, 0, W, H), fill=(196, 180, 152) if fld else (168, 170, 174))
    for gx in range(-20, 21, 2):
        p1, p2 = M(gx, -60), M(gx, 60)
        dr.line((*p1, *p2), fill=(0, 0, 0, 22), width=1)
    for gz in range(-60, 61, 2):
        p1, p2 = M(-30, gz), M(30, gz)
        dr.line((*p1, *p2), fill=(0, 0, 0, 22), width=1)
    try:
        fN = ImageFont.truetype(FONT, 15)
        fS = ImageFont.truetype(FONT, 12)
    except Exception:
        fN = fS = ImageFont.load_default()
    pos, look = shot.get("pos"), shot.get("look")
    # 相机 fov 扇形
    if pos and look:
        cx, cz = pos[0], pos[2]
        la, lz = look[0], look[2]
        ang = math.atan2(la - cx, lz - cz)   # 世界系：x 左右 z 纵深
        fov = math.radians(float(shot.get("fov") or fov_def))
        R = 7.0
        pL = M(cx + R * math.sin(ang - fov / 2), cz + R * math.cos(ang - fov / 2))
        pR = M(cx + R * math.sin(ang + fov / 2), cz + R * math.cos(ang + fov / 2))
        pc = M(cx, cz)
        dr.polygon([pc, pL, pR], fill=(56, 189, 248, 46))
        dr.line((*pc, *M(la, lz)), fill=(14, 116, 187), width=2)
        dr.ellipse((pc[0] - 6, pc[1] - 6, pc[0] + 6, pc[1] + 6), fill=(14, 116, 187))
        dr.text((pc[0] + 8, pc[1] - 20), shot.get("id", ""), font=fN, fill=(12, 74, 110))
        # 运镜箭头
        mv = str(shot.get("camera_move") or "")
        arr = None
        if mv == "推":
            arr = (pc, M(cx + 2.2 * math.sin(ang), cz + 2.2 * math.cos(ang)))
        elif mv == "拉":
            arr = (M(cx + 2.2 * math.sin(ang), cz + 2.2 * math.cos(ang)), pc)
        elif mv in ("移", "轨道", "斯坦尼康", "手持"):
            per = ang + math.pi / 2
            arr = (pc, M(cx + 2.0 * math.sin(per), cz + 2.0 * math.cos(per)))
        elif mv == "摇":
            arr = (pc, M(cx + 2.6 * math.sin(ang + math.pi / 5), cz + 2.6 * math.cos(ang + math.pi / 5)))
        elif mv == "环绕":
            t = ang + math.pi * 0.7
            arr = (pc, M(cx + 2.6 * math.sin(t), cz + 2.6 * math.cos(t)))
        if arr:
            dr.line((*arr[0], *arr[1]), fill=(234, 88, 12), width=3)
            ax_, ay_ = arr[1]
            dr.ellipse((ax_ - 4, ay_ - 4, ax_ + 4, ay_ + 4), fill=(234, 88, 12))
    # 角色 + 走位箭头
    positions = shot.get('_actor_positions', actor_positions(shot,actors))
    for aid, (x,z) in positions.items():
        ac = actors[aid]
        off = False
        px, py = M(x, z)
        col = tuple(ac.get("shirt") or (180, 180, 180))
        if off:   # 画外角色：空心+降透明度观感（用浅色描边）
            dr.ellipse((px - 7, py - 7, px + 7, py + 7), outline=(150, 150, 150), width=2)
        else:
            dr.ellipse((px - 9, py - 9, px + 9, py + 9), fill=col, outline=(30, 30, 30))
        dr.text((px + 10, py - 9), ac.get("name", aid), font=fN, fill=(20, 20, 20))
        # 走位箭头：前镜在场且位置变化 > 0.4m
        if prev_pos and aid in prev_pos and not off:
            ox, oz = prev_pos[aid]
            if math.hypot(x - ox, z - oz) > 0.4:
                p0 = M(ox, oz)
                dr.line((*p0, px, py), fill=(22, 130, 90), width=3)
                mx, my = (p0[0] + px) / 2, (p0[1] + py) / 2
                dr.ellipse((mx - 4, my - 4, mx + 4, my + 4), fill=(22, 130, 90))
        elif prev_pos is not None and aid in st and st[aid] is not None and False:
            pass
    # 台词条
    lines = shot.get("lines") or []
    if lines:
        txt = " / ".join(f"{l.get('speaker')}:{str(l.get('line'))[:14]}" for l in lines[:2])
        dr.rectangle((0, H - 26, W, H), fill=(15, 23, 42, 200))
        dr.text((8, H - 24), txt[:80], font=fS, fill=(226, 232, 240))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("storyboard")
    ap.add_argument("--outdir", default=None)
    a = ap.parse_args()
    jp = os.path.abspath(a.storyboard)
    cfg = json.load(open(jp, encoding="utf-8"))
    shots = cfg.get("shots") or []
    actors = cfg.get("actors") or {}
    if not shots:
        print("[错误] 无 shots"); sys.exit(1)
    base = os.path.splitext(os.path.basename(jp))[0]
    outdir = a.outdir or os.path.join(os.path.dirname(os.path.dirname(jp)), "推演", "平面图_" + base)
    os.makedirs(outdir, exist_ok=True)
    W, H = 800, 600
    scenes = load_scenes(jp)
    prev = None
    n = 0
    for i, sh in enumerate(shots, 1):
        # 窗口：本镜全部实体 + 走位起点
        xs, zs = [], []
        positions = actor_positions(sh,actors,scenes.get(shot_scene_id(sh,scenes)))
        sh['_actor_positions'] = positions
        for x,z in positions.values(): xs.append(x); zs.append(z)
        if sh.get("pos"):
            xs.append(sh["pos"][0]); zs.append(sh["pos"][2])
        if sh.get("look"):
            xs.append(sh["look"][0]); zs.append(sh["look"][2])
        if not xs:
            xs, zs = [-5, 5], [-5, 5]
        M = setup((min(xs), max(xs)), (min(zs), max(zs)), W, H)
        img = Image.new("RGB", (W, H))
        dr = ImageDraw.Draw(img, "RGBA")
        draw_shot(dr, W, H, M, sh, actors, prev)
        sid = str(sh.get("id", f"S{i}"))
        _out = os.path.join(outdir, sid + ".png")
        import versions as _V; _V.snapshot(_out)
        img.save(_out)
        # 供下一镜走位箭头：在场角色当前位置
        prev = positions
        n += 1
    print(f"[完成] 平面图 {n} 张 -> {outdir}")
    print("OUTPUT:" + outdir)


if __name__ == "__main__":
    main()
