# -*- coding: utf-8 -*-
"""视频局部修补（重做片段）：锚点帧抽取、时间窗口校验与三段拼回。

场景：一段已采用的 V 视频（如 15s）中间 6–8s 需要重做——
抽 6s/8s 两帧作为首尾锚点走首尾帧模式生成新片段，再把
「原片[0,t0] + 新片段 + 原片[t1,末]」三段拼回，产出带锚点信息的新候选。

纯命令行可跑：
  python redo_segment.py anchors <原片> --t0 6 --t1 8 --outdir 锚点目录
  python redo_segment.py splice  <原片> <片段> --t0 6 --t1 8 --out out.mp4

依赖 ffmpeg/ffprobe（复用 production_media 的 binary/run/probe 定位与调用）。
"""
import argparse
import math
import sys
import tempfile
from pathlib import Path

from production_media import binary, probe, run

try: sys.stdout.reconfigure(encoding='utf-8')
except Exception: pass

# 时间参数容差（秒）：吸收浮点输入与末点取整误差；越界在容差内夹取到边界，超出才报错。
WINDOW_TOLERANCE = 0.05


def video_duration(path):
    """读取视频时长（秒）；无法确定时报错而不是猜。"""
    info = probe(path)
    value = (info.get('format') or {}).get('duration')
    try: seconds = float(value)
    except (TypeError, ValueError): seconds = 0.0
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError(f'无法确定视频时长：{path}')
    return seconds


def validate_window(duration, t0, t1, tol=WINDOW_TOLERANCE):
    """校验并归一化修补窗口，规则：0 ≤ t0 < t1 ≤ 时长。

    容差内的贴边（如 t0=-0.01、t1=时长+0.02）夹取到边界；其余越界直接报错。
    返回 (t0, t1) 归一化值。
    """
    for name, value in (('时长', duration), ('t0', t0), ('t1', t1)):
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
            raise ValueError(f'修补{name}必须是有限数值（秒）')
    duration, t0, t1 = float(duration), float(t0), float(t1)
    if duration <= 0: raise ValueError('原片时长必须为正数')
    if t0 < -tol: raise ValueError(f'修补起点不能为负：{t0:g}s')
    if t1 > duration + tol: raise ValueError(f'修补终点 {t1:g}s 超出原片时长 {duration:g}s')
    t0 = min(max(t0, 0.0), duration)
    t1 = min(max(t1, 0.0), duration)
    if t1 - t0 <= 0: raise ValueError(f'修补起点必须小于终点（归一化后 {t0:g}s ≥ {t1:g}s）')
    return t0, t1


def _grab_frame(video, ts, out):
    """精确抽 ts 秒处一帧为 JPG；贴末点抽不到时回退到文件尾帧。"""
    run([binary('ffmpeg'), '-v', 'error', '-ss', f'{ts:.3f}', '-i', str(video),
         '-frames:v', '1', '-q:v', '2', '-y', str(out)])
    if out.is_file() and out.stat().st_size: return
    # -ss 落在有效帧之后（末点=时长）时 ffmpeg 正常退出但不产图，回退取尾部真实帧。
    run([binary('ffmpeg'), '-v', 'error', '-sseof', '-0.1', '-i', str(video),
         '-frames:v', '1', '-q:v', '2', '-y', str(out)])
    if not out.is_file() or not out.stat().st_size:
        raise ValueError(f'无法抽取 {ts:g}s 锚点帧：{video}')


def extract_anchor_frames(video, t0, t1, outdir):
    """抽 t0/t1 两帧 JPG 到 outdir：{'head': Path, 'tail': Path}；已存在非空文件直接复用。"""
    video = Path(video)
    if not video.is_file(): raise ValueError(f'原片不存在：{video}')
    total = video_duration(video)
    t0, t1 = validate_window(total, t0, t1)
    folder = Path(outdir)
    folder.mkdir(parents=True, exist_ok=True)
    head, tail = folder / 'head.jpg', folder / 'tail.jpg'
    if not (head.is_file() and head.stat().st_size): _grab_frame(video, t0, head)
    if not (tail.is_file() and tail.stat().st_size): _grab_frame(video, t1, tail)
    return {'head': head, 'tail': tail}


def _normalize_part(src, dst, w, h, fps, audio, *, seek=None, length=None):
    """把一段素材归一化为统一中间段：scale/pad 到目标画布 + 统一帧率 + aac 48k 立体声。

    全部中间段同规格编码后 concat 直通拷贝才稳定；无音轨的段补静音。
    """
    cmd = [binary('ffmpeg'), '-v', 'error']
    if seek is not None: cmd += ['-ss', f'{seek:.3f}']   # 重编码前置 seek 帧级精确
    cmd += ['-i', str(src)]
    if not audio: cmd += ['-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo']
    cmd += ['-map', '0:v:0', '-map', '0:a:0' if audio else '1:a:0', '-vf',
            f'scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps:g}',
            '-c:v', 'libx264', '-preset', 'fast', '-crf', '18', '-pix_fmt', 'yuv420p',
            '-c:a', 'aac', '-ar', '48000', '-ac', '2']
    if length is not None: cmd += ['-t', f'{length:.3f}']
    if not audio: cmd += ['-shortest']
    cmd += ['-y', str(dst)]
    run(cmd, 1800)


def splice_back(original, segment, t0, t1, out):
    """三段拼回：原片[0,t0] + 新片段 + 原片[t1,末]，编码规格对齐原片。

    分辨率/帧率 probe 原片，片段不足或超出都 scale/pad 到原片画布（绝不裁切拉伸）；
    原片两段沿用原声，片段自带音轨用片段、没有则补静音；返回 probe(out)。
    """
    original, segment, out = Path(original), Path(segment), Path(out)
    if not original.is_file(): raise ValueError(f'原片不存在：{original}')
    if not segment.is_file(): raise ValueError(f'修补片段不存在：{segment}')
    info = probe(original)
    stream = next((s for s in info.get('streams', []) if s.get('codec_type') == 'video'), None)
    if not stream: raise ValueError('原片没有视频轨，无法拼回')
    total = float((info.get('format') or {}).get('duration') or stream.get('duration') or 0)
    t0, t1 = validate_window(total, t0, t1)
    w, h = int(stream['width']), int(stream['height'])
    w, h = w - w % 2, h - h % 2   # 编码器要求偶数边
    rate = str(stream.get('avg_frame_rate') or stream.get('r_frame_rate') or '0/1')
    try:
        num, _, den = rate.partition('/'); fps = float(num) / float(den or 1)
    except (TypeError, ZeroDivisionError, ValueError):
        fps = 0.0
    if not math.isfinite(fps) or fps <= 0: fps = 24.0
    original_audio = any(s.get('codec_type') == 'audio' for s in info.get('streams', []))
    segment_info = probe(segment)
    if not any(s.get('codec_type') == 'video' for s in segment_info.get('streams', [])):
        raise ValueError('修补片段没有视频轨，无法拼回')
    segment_audio = any(s.get('codec_type') == 'audio' for s in segment_info.get('streams', []))
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out.parent) as tmp:
        clips = []
        # 起点贴 0 时省略头部原片段，终点贴末时省略尾部原片段。
        if t0 > WINDOW_TOLERANCE:
            part = Path(tmp) / f'{len(clips)}.mp4'
            _normalize_part(original, part, w, h, fps, original_audio, length=t0)
            clips.append(part)
        part = Path(tmp) / f'{len(clips)}.mp4'
        _normalize_part(segment, part, w, h, fps, segment_audio)
        clips.append(part)
        if t1 < total - WINDOW_TOLERANCE:
            part = Path(tmp) / f'{len(clips)}.mp4'
            _normalize_part(original, part, w, h, fps, original_audio, seek=t1)
            clips.append(part)
        listing = Path(tmp) / 'concat.txt'
        listing.write_text('\n'.join(f"file '{p.name}'" for p in clips), encoding='utf-8')
        run([binary('ffmpeg'), '-v', 'error', '-f', 'concat', '-safe', '0', '-i', str(listing),
             '-c', 'copy', '-movflags', '+faststart', '-y', str(out)], 1800)
    if not out.is_file() or not out.stat().st_size: raise ValueError('拼回未产出有效文件')
    return probe(out)


def main(argv=None):
    parser = argparse.ArgumentParser(description='视频局部修补：锚点帧抽取与三段拼回')
    sub = parser.add_subparsers(dest='cmd', required=True)
    a = sub.add_parser('anchors', help='抽 t0/t1 两帧锚点 JPG')
    a.add_argument('video'); a.add_argument('--t0', type=float, required=True)
    a.add_argument('--t1', type=float, required=True); a.add_argument('--outdir', required=True)
    s = sub.add_parser('splice', help='原片[0,t0] + 片段 + 原片[t1,末] 三段拼回')
    s.add_argument('original'); s.add_argument('segment')
    s.add_argument('--t0', type=float, required=True); s.add_argument('--t1', type=float, required=True)
    s.add_argument('--out', required=True)
    args = parser.parse_args(argv)
    try:
        if args.cmd == 'anchors':
            frames = extract_anchor_frames(args.video, args.t0, args.t1, args.outdir)
            print(f"[完成] 锚点帧：{frames['head']} / {frames['tail']}")
        else:
            info = splice_back(args.original, args.segment, args.t0, args.t1, args.out)
            print(f"[完成] 拼回 {args.out}（{(info.get('format') or {}).get('duration')}s）")
    except ValueError as exc:
        print('[错误] ' + str(exc)); return 1
    return 0


if __name__ == '__main__': sys.exit(main())
