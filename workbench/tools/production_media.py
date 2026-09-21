# -*- coding: utf-8 -*-
"""本地媒体引用、尾帧、关键帧宫格与集视频合成。"""
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from production_studio import inside


def digest(path):
    with open(path, 'rb') as fh: return hashlib.file_digest(fh, 'sha256').hexdigest()


def binary(name):
    found = shutil.which(name)
    if found: return found
    root = Path(os.environ.get('LOCALAPPDATA', '')) / 'Microsoft/WinGet/Packages'
    choices = list(root.glob(f'Gyan.FFmpeg_*/ffmpeg-*/bin/{name}.exe'))
    if not choices: raise ValueError(f'未找到 {name}')
    return str(choices[0])


def run(args, timeout=300):
    result = subprocess.run(args, capture_output=True, timeout=timeout, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode: raise ValueError(result.stderr.decode('utf-8', 'replace')[-2000:])
    return result


def probe(path):
    return json.loads(run([binary('ffprobe'), '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)]).stdout)


def binding(project, item_id, output_index=0, expected_type=None):
    from production_studio import project_store
    data, _ = project_store.read_json(Path(project) / '创作/creation.json')
    item = next((i for i in data.get('items', []) if i.get('id') == item_id), None)
    if not item or item.get('status') != 'done': raise ValueError('只能采用已完成的产出')
    if expected_type and item.get('type') != expected_type: raise ValueError('产出类型不符')
    try: value = item['outputs'][int(output_index)]
    except (IndexError, KeyError, ValueError): raise ValueError('产出文件不存在')
    raw = value.get('path') if isinstance(value, dict) else value
    # 历史 outputs 使用 workspace 相对路径。
    raw = str(raw or '').replace('\\', '/')
    prefix = 'projects/' + Path(project).name + '/'
    if raw.startswith(prefix): raw = raw[len(prefix):]
    path = inside(project, raw)
    return {'item_id': item_id, 'output_index': int(output_index), 'path': path.relative_to(Path(project).resolve()).as_posix(),
            'sha256': digest(path), 'source_hash': item.get('source_hash', ''), 'board': item.get('board', ''),
            'shot_id': item.get('shot_id', ''), 'unit_id': item.get('unit_id', ''), 'type': item.get('type')}


def bound_path(project, ref):
    path = inside(project, ref['path'])
    if digest(path) != ref.get('sha256'): raise ValueError('已采用素材文件版本改变，请重新采用')
    return path


def tail_frame(project, source):
    """解码最后一小段，取实际最后显示帧；不使用 duration - 1/fps 估算。"""
    src = bound_path(project, source)
    folder = Path(project) / '素材/视频衔接' / source['sha256']
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / 'last.png'
    metadata = folder / 'frame.json'
    if not metadata.is_file():
        info = probe(src)
        with tempfile.TemporaryDirectory(dir=folder) as tmp:
            temp = Path(tmp) / 'last.png'
            result = run([binary('ffmpeg'), '-hide_banner', '-sseof', '-3', '-i', str(src), '-map', '0:v:0',
                          '-vf', 'showinfo', '-fps_mode', 'passthrough', '-update', '1', '-y', str(temp)])
            if not temp.is_file(): raise ValueError('尾段没有解码到有效视频帧')
            from PIL import Image, ImageStat
            with Image.open(temp) as im: mean = sum(ImageStat.Stat(im.convert('RGB')).mean) / 3
            if mean < 4: raise ValueError('最后一帧为近黑画面，请选择另一段已完成的视频作为衔接来源')
            os.replace(temp, out)
            pts = re.findall(r'pts_time:([\d.\-]+)', result.stderr.decode('utf-8', 'replace'))
            meta = {'source': source, 'streams': info.get('streams'), 'format': info.get('format'),
                    'decoded_tail_pts': pts[-1] if pts else None, 'path': out.relative_to(Path(project)).as_posix(), 'sha256': digest(out)}
            metadata.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
    return json.loads(metadata.read_text(encoding='utf-8'))


def make_grid(project, refs, description=''):
    """宫格只排版已采用的真实关键帧，绝不重新绘制格内内容。"""
    if not refs: raise ValueError('没有已采用的关键帧')
    from PIL import Image, ImageOps
    paths = [bound_path(project, r) for r in refs]
    key = hashlib.sha256(json.dumps([r['sha256'] for r in refs]).encode()).hexdigest()[:24]
    folder = Path(project) / '素材/故事板'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (key + '.png')
    cols = math.ceil(math.sqrt(len(refs))); rows = math.ceil(len(refs) / cols)
    canvas = Image.new('RGB', (640 * cols, 360 * rows), '#101522')
    for i, src in enumerate(paths):
        with Image.open(src) as im:
            canvas.paste(ImageOps.contain(im.convert('RGB'), (640, 360)), (i % cols * 640, i // cols * 360))
    canvas.save(path)
    path.with_suffix('.json').write_text(json.dumps({'references': refs, 'prompt_grid': description,
        'layout': '按 S 顺序，从左到右、从上到下', 'columns': cols, 'rows': rows}, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'path': path.relative_to(Path(project)).as_posix(), 'sha256': digest(path), 'purpose': 'S 关键帧故事板，格序为镜头顺序'}


def resolve_master_spec(streams):
    """从已采用片段推导交付母版规格（纯函数，便于回归）。

    画布取向按多数画幅投票，再在该取向内取像素面积最大者定基准——竖屏多数就出竖屏母版，
    高分辨率片段不被降档；帧率取全体最大值统一时间基。只升不降：小片段 letterbox/pad 黑边，
    绝不裁切、绝不拉伸。
    """
    parsed = []
    for s in streams:
        w, h = int(s.get('width') or 0), int(s.get('height') or 0)
        if w <= 0 or h <= 0: raise ValueError('片段缺少有效分辨率，无法推导母版规格')
        rate = str(s.get('avg_frame_rate') or s.get('r_frame_rate') or '0/1')
        try:
            num, _, den = rate.partition('/')
            fps = float(num) / float(den or 1)
        except (TypeError, ZeroDivisionError, ValueError):
            fps = 0.0
        parsed.append((w, h, fps))
    portrait = sum(1 for w, h, _ in parsed if h > w)
    want_portrait = portrait > len(parsed) / 2
    same = [(w, h, f) for w, h, f in parsed if (h > w) == want_portrait] or parsed
    w, h, _ = max(same, key=lambda t: t[0] * t[1])
    fps = max(f for _, _, f in parsed) or 24.0
    return {'w': w - w % 2, 'h': h - h % 2, 'fps': round(fps, 3)}


def concatenate(project, refs, out, spec='proxy'):
    """拼接已采用片段。

    spec='proxy'：预览代理，统一 1280×720@24（低成本快速看片）。
    spec='master' 或 {'w','h','fps'}：交付母版——按片段推导（或显式给定）规格，绝不把高规格
    素材降档；少数取向片段 pad 黑边，音频在拼接后整段响度归一（EBU R128 / -16 LUFS）。
    """
    if not refs: raise ValueError('请先采用本集的 V 视频')
    with tempfile.TemporaryDirectory(dir=Path(out).parent) as tmp:
        infos = []
        for ref in refs:
            info = probe(bound_path(project, ref))
            video_stream = next((s for s in info.get('streams', []) if s.get('codec_type') == 'video'), {})
            if not video_stream: raise ValueError('片段没有视频轨，停止拼接')
            infos.append((ref, info, video_stream))
        if spec == 'master':
            spec = resolve_master_spec(v for _, _, v in infos)
        if spec == 'proxy':
            w, h, fps = 1280, 720, 24
        else:
            w, h, fps = int(spec['w']), int(spec['h']), float(spec['fps'])
        clips = []
        for i, (ref, info, video_stream) in enumerate(infos):
            src = bound_path(project, ref)
            seconds = float(video_stream.get('duration') or info.get('format', {}).get('duration') or 0)
            if seconds <= 0: raise ValueError('无法确定视频片段时长，停止拼接')
            audio = any(s.get('codec_type') == 'audio' for s in info.get('streams', []))
            dst = Path(tmp) / f'{i}.mp4'
            args = [binary('ffmpeg'), '-v', 'error', '-i', str(src)]
            if not audio: args += ['-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo']
            args += ['-map', '0:v:0', '-map', '0:a:0' if audio else '1:a:0', '-vf',
                     f'scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps:g}',
                     '-c:v', 'libx264', '-preset', 'fast', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-ar', '48000', '-ac', '2', '-t', str(seconds), '-shortest', '-y', str(dst)]
            run(args, 1800); clips.append(dst)
        listing = Path(tmp) / 'concat.txt'
        listing.write_text('\n'.join(f"file '{p.name}'" for p in clips), encoding='utf-8')
        final_args = [binary('ffmpeg'), '-v', 'error', '-f', 'concat', '-safe', '0', '-i', str(listing),
                      '-movflags', '+faststart', '-y', str(out)]
        if spec != 'proxy':
            # 交付母版：视频流直通（分段已统一规格），音频整段响度归一后重编码
            final_args[9:9] = ['-c:v', 'copy', '-af', 'loudnorm=I=-16:TP=-1.5:LRA=11', '-c:a', 'aac', '-b:a', '192k']
        else:
            final_args[9:9] = ['-c', 'copy']
        run(final_args, 1800)
    return probe(out)
