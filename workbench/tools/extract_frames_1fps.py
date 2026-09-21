# -*- coding: utf-8 -*-
"""ffmpeg fps=1 均匀抽帧（AI 短片分析工作台 阶段一）
用法: python extract_frames_1fps.py <视频> --out <目录>
产物: <out>/f_%04d.jpg（t=N-1 秒，即第 1 帧对应 t=0）+ <out>/frames_manifest.json
      manifest: {"source":..., "fps":1, "duration":..., "frames":[{"t":0,"file":"f_0001.jpg"},...]}
说明: 用 ffprobe 拿 duration 交叉校验帧数量（容忍末帧缺失/尾帧凑整）。
退出码: 0=成功 1=失败
"""
import sys, os, json, glob, argparse, subprocess, shutil
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

def find(prog):
    p = shutil.which(prog)
    if p: return p
    for c in glob.glob(os.path.expandvars(
            r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_*\ffmpeg-*\bin\%s.exe" % prog)):
        if os.path.isfile(c): return c
    return None

def probe_duration(ffprobe, video):
    try:
        r = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=nw=1:nk=1", video],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        return float(r.stdout.strip())
    except Exception:
        return 0.0

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video"); ap.add_argument("--out", required=True)
    a = ap.parse_args()
    video = os.path.abspath(a.video)
    if not os.path.isfile(video):
        print(f"[错误] 视频不存在: {video}"); sys.exit(1)
    ff = find("ffmpeg"); fp = find("ffprobe")
    if not ff:
        print("[错误] 未找到 ffmpeg"); sys.exit(1)
    out = os.path.abspath(a.out); os.makedirs(out, exist_ok=True)
    r = subprocess.run([ff, "-y", "-i", video, "-vf", "fps=1", "-q:v", "3",
                        os.path.join(out, "f_%04d.jpg")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
    if r.returncode != 0:
        print(f"[错误] ffmpeg 抽帧失败: {(r.stderr or '')[-400:]}"); sys.exit(1)
    files = sorted(f for f in os.listdir(out) if f.startswith("f_") and f.endswith(".jpg"))
    dur = probe_duration(fp, video) if fp else 0.0
    expect = int(dur + 0.5) if dur > 0 else len(files)  # fps=1 大致每秒 1 帧
    frames = [{"t": i, "file": files[i]} for i in range(len(files))]
    if expect and abs(len(files) - expect) > 2:
        print(f"[警告] 帧数({len(files)}) 与时长推定({expect}s) 偏差较大，请人工核对")
    man = {"source": video, "fps": 1, "duration": round(dur, 3), "frames": frames}
    json.dump(man, open(os.path.join(out, "frames_manifest.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"完成: {len(files)} 帧 (时长 {dur:.1f}s) -> {out}")
    print(f"OUTPUT:{os.path.join(out, 'frames_manifest.json')}")

if __name__ == "__main__":
    main()
