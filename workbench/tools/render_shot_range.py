# -*- coding: utf-8 -*-
"""白模阅片：按镜头区间渲染 storyboard 子集（AI 短片分析工作台 阶段一）
用法: python render_shot_range.py <storyboard.json> --shots S3-S8 [--outdir <目录>] [--engine auto|dialogue|previs]
说明:
  - --shots 支持 S3-S8（按 id）、3-8（按 index）、S3（单镜）；id 与 index 可混用
  - 契约自动判别: 镜头含 cam/pos+look -> dialogue_engine；含 mode -> previs_engine
  - 产物: <outdir>/<slug>_S3-S8.mp4（mp4v 渲染后经 ffmpeg 转 H.264）
stdout 末行打印 OUTPUT:<绝对路径>（H.264 转换不可用时回退打印 mp4v 原片路径）
退出码: 0=成功 1=失败
"""
import sys, os, json, re, glob, argparse, subprocess, shutil
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

VIDEO = os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

def find_ffmpeg():
    p = shutil.which("ffmpeg")
    if p: return p
    cands = glob.glob(os.path.expandvars(
        r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_*\ffmpeg-*\bin\ffmpeg.exe"))
    for c in cands:
        if os.path.isfile(c): return c
    return None

def find_tool(name):
    for d in [os.path.join(VIDEO, ".codex", "skills", "video-previs", "scripts"),
              os.path.join(VIDEO, "previs_system", "tools"),
              os.path.join(VIDEO, "previs_system", "engine")]:
        f = os.path.join(d, name)
        if os.path.isfile(f): return f
    return None

def expand_range(spec, shots):
    """'S3-S8' / '3-8' / 'S3' -> 子集数组。"""
    def tok(s):
        s = s.strip()
        mm = re.fullmatch(r"[Ss]?(\d+)", s)
        return int(mm.group(1)) if mm else None
    ids = {s.get("id"): s for s in shots}
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part: continue
        if "-" in part[1:]:
            a0, b0 = part.split("-", 1)
            ia, ib = tok(a0), tok(b0)
            if ia is None or ib is None:
                print(f"[错误] 无法解析区间: {part}"); return None
            for i in range(ia, ib + 1):
                key = f"S{i:02d}"
                s = ids.get(key) or ids.get(f"S{i}") or (shots[i - 1] if 1 <= i <= len(shots) else None)
                if s is None:
                    if i > len(shots):
                        if i == ia:
                            print(f"[错误] 起点超出范围: 分镜只有 {len(shots)} 个镜头，S{i} 不存在"); return None
                        print(f"[提示] 区间上界截断到 S{len(shots)}（分镜共 {len(shots)} 个镜头）"); break
                    print(f"[错误] 找不到镜头 S{i}"); return None
                if s not in out: out.append(s)
        else:
            if part in ids:
                s = ids[part]
            else:
                i = tok(part)
                s = shots[i - 1] if i is not None and 1 <= i <= len(shots) else None
            if s is None:
                print(f"[错误] 找不到镜头: {part}"); return None
            if s not in out: out.append(s)
    return out

def pick_engine(sub, prefer):
    if prefer != "auto": return prefer
    if any("cam" in s or ("pos" in s and "look" in s) for s in sub): return "dialogue"
    if any("mode" in s for s in sub): return "previs"
    return "dialogue"

def slug_of(story_path):
    return os.path.splitext(os.path.basename(story_path))[0]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("storyboard")
    ap.add_argument("--shots", required=True, help="如 S3-S8 / 3-8 / S3,S5 / 2-4")
    ap.add_argument("--outdir", default=None, help="输出目录（默认 storyboard 同目录）")
    ap.add_argument("--engine", default="auto", choices=["auto", "dialogue", "previs"])
    a = ap.parse_args()
    sb = os.path.abspath(a.storyboard)
    if not os.path.isfile(sb):
        print(f"[错误] 分镜 JSON 不存在: {sb}"); sys.exit(1)
    cfg = json.load(open(sb, encoding="utf-8"))
    shots = cfg.get("shots")
    if not isinstance(shots, list) or not shots:
        print("[错误] storyboard 缺少 shots"); sys.exit(1)
    sub = expand_range(a.shots, shots)
    if not sub: sys.exit(1)
    eng = pick_engine(sub, a.engine)
    engine = find_tool("dialogue_engine.py" if eng == "dialogue" else "previs_engine.py")
    if not engine:
        print(f"[错误] 引擎缺失: {eng}"); sys.exit(1)
    outdir = os.path.abspath(a.outdir) if a.outdir else os.path.dirname(sb)
    os.makedirs(outdir, exist_ok=True)

    subcfg = dict(cfg); subcfg["shots"] = sub
    # previs 契约的走位 path 是绝对时间轴：切片后把 c 的 path 平移到子集时间原点，
    # 否则演员会停在切片前的位置（dialogue 契约用每镜 staging，无此问题）
    if eng == "previs":
        fi = shots.index(sub[0])
        off_t = sum(float(s.get("dur", 0)) for s in shots[:fi])
        if off_t > 0:
            ac = subcfg.get("actors", {}).get("c")
            pth = (ac or {}).get("path")
            if pth:
                shifted = [[max(0.0, float(p[0]) - off_t)] + list(p[1:]) for p in pth]
                zeros = [p for p in shifted if p[0] == 0.0]
                rest = [p for p in shifted if p[0] > 0.0]
                head = zeros[-1] if zeros else [[0.0] + list(pth[0][1:])]
                head = [0.0] + head[1:]
                ac["path"] = [head] + rest
    slug = slug_of(sb)
    tag = a.shots.replace(",", "+")
    tmp = os.path.join(outdir, "_tmp_%s_%s.json" % (slug, tag.replace("-", "_")))
    json.dump(subcfg, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    raw = os.path.join(outdir, "_tmp_%s_%s.mp4" % (slug, tag.replace("-", "_")))
    print(f"渲染 {len(sub)} 镜（{eng} 引擎）: {[s.get('id') for s in sub]}")
    r = subprocess.run([sys.executable, engine, tmp, raw], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=1800)
    if r.returncode != 0 or not os.path.isfile(raw):
        print(f"[错误] 渲染失败:\n{(r.stdout or '')[-1500:]}\n{(r.stderr or '')[-1500:]}"); sys.exit(1)
    try: os.remove(tmp)
    except OSError: pass

    ff = find_ffmpeg()
    final = os.path.join(outdir, f"{slug}_{tag}.mp4")
    import versions as _V; _V.snapshot(final)
    if ff:
        r2 = subprocess.run([ff, "-y", "-i", raw, "-c:v", "libx264", "-pix_fmt", "yuv420p",
                             "-movflags", "+faststart", final],
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
        if r2.returncode == 0 and os.path.isfile(final):
            try: os.remove(raw)
            except OSError: pass
        else:
            print("[警告] H.264 转码失败，保留 mp4v 原片")
            final = raw
    else:
        print("[警告] 未找到 ffmpeg，保留 mp4v 编码")
        final = raw
    print(f"完成: {final}")
    print(f"OUTPUT:{os.path.abspath(final)}")

if __name__ == "__main__":
    main()
