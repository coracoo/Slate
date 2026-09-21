# -*- coding: utf-8 -*-
"""Blender CLI 渲染 + 合片：blender -b x.blend -a 渲染 PNG 序列后，ffmpeg 合成 mp4。
用法: python blender_render_wrap.py <file.blend> [--fps 24]
  1. 调 blender -b <blend> -a 渲染帧序列（输出目录为 blend 同级的 render_gen/，gen_*.py 的约定）
  2. 渲染成功后用 ffmpeg 把 render_gen/f_%04d.png 合成 <blend同名>_预览.mp4（H.264，播放器兼容）
  3. 末行打印 RENDER -> <mp4 绝对路径>（前端从任务日志解析产物）
退出码: 0=成功 1=blender 渲染失败 2=ffmpeg 合成失败
"""
import sys, os, glob, argparse, subprocess

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.dirname(os.path.abspath(__file__))  # workbench/tools
sys.path.insert(0, os.path.join(ROOT, ".."))
from server import find_blender  # 复用 blender 探测


def find_ffmpeg():
    for c in ["ffmpeg"] + glob.glob(os.path.expandvars(
            r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_*\ffmpeg-*\bin\ffmpeg.exe")):
        try:
            subprocess.run([c, "-version"], capture_output=True)
            return c
        except Exception:
            continue
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("blend")
    ap.add_argument("--fps", type=int, default=24)
    a = ap.parse_args()
    blend = os.path.abspath(a.blend)
    if not os.path.isfile(blend):
        print(f"[错误] blend 不存在: {blend}"); sys.exit(1)
    exe = find_blender()
    if not exe:
        print("[错误] 未找到 blender.exe"); sys.exit(1)
    outdir = os.path.join(os.path.dirname(blend), "render_gen")

    print(f"[1/2] Blender 渲染帧序列 -> {outdir}", flush=True)
    r = subprocess.run([exe, "-b", blend, "-a"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, encoding="utf-8", errors="replace")
    tail = (r.stdout or "")[-3000:]
    if tail: print(tail, flush=True)
    if r.returncode != 0:
        print(f"[错误] Blender 渲染失败，退出码 {r.returncode}", flush=True); sys.exit(1)

    frames = sorted(glob.glob(os.path.join(outdir, "f_*.png")))
    if not frames:
        print(f"[错误] 渲染完成但 {outdir} 下没有 f_*.png", flush=True); sys.exit(2)
    print(f"[2/2] {len(frames)} 帧 -> ffmpeg 合成 mp4", flush=True)
    ff = find_ffmpeg()
    if not ff:
        print("[错误] 未找到 ffmpeg", flush=True); sys.exit(2)
    mp4 = os.path.splitext(blend)[0] + "_预览.mp4"
    cmd = [ff, "-y", "-framerate", str(a.fps), "-i", os.path.join(outdir, "f_%04d.png"),
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", mp4]
    r2 = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, encoding="utf-8", errors="replace")
    if r2.returncode != 0:
        print("[错误] ffmpeg 合成失败: " + (r2.stdout or "")[-500:], flush=True); sys.exit(2)
    print(f"RENDER -> {mp4}", flush=True)


if __name__ == "__main__":
    main()
