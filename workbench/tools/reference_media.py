# -*- coding: utf-8 -*-
"""项目参考音视频上传、枚举与提交校验。"""
import uuid
from pathlib import Path
from production_studio import inside
from production_media import digest
from video_profiles import public_url

EXTENSIONS = {'audio': {'.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg'},
              'video': {'.mp4', '.mov', '.mkv', '.webm'}}
MAX_BYTES = 100 * 1024 * 1024


def upload(project, kind, name, stream, length):
    if kind not in EXTENSIONS or Path(name).suffix.lower() not in EXTENSIONS[kind]:
        raise ValueError('参考素材类型或扩展名不支持')
    if not 0 < length <= MAX_BYTES: raise ValueError('文件必须非空且不超过 100 MB')
    folder = Path(project) / '素材' / ('参考音频' if kind == 'audio' else '参考视频')
    folder.mkdir(parents=True, exist_ok=True)
    target = inside(project, str(folder / (uuid.uuid4().hex + Path(name).suffix.lower())), exists=False)
    try:
        with target.open('xb') as f:
            left = length
            while left:
                chunk = stream.read(min(left, 1024 * 1024))
                if not chunk: raise ValueError('上传中断，请重新选择文件')
                f.write(chunk); left -= len(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return {'path': target.relative_to(project).as_posix(), 'name': Path(name).name}


def list_media(project, kind):
    if kind not in EXTENSIONS: raise ValueError('素材类型不支持')
    rows = []
    for folder in ('素材', '创作', '拉片素材', '成片'):
        for p in (Path(project) / folder).rglob('*'):
            if p.suffix.lower() not in EXTENSIONS[kind] or not p.is_file() or '.versions' in p.parts: continue
            rel = p.relative_to(project).as_posix()
            inside(project, rel)
            rows.append({'path': rel, 'name': p.name})
    return rows


def resolve(project, values, kind, cfg):
    if not isinstance(values, list): raise ValueError('参考素材必须为列表')
    result = []
    for value in values:
        if not isinstance(value, str): raise ValueError('参考素材必须为 URL 或项目内路径')
        if value.startswith(('https://', 'http://')):
            result.append({'url': public_url(value)}); continue
        p = inside(project, value)
        if p.suffix.lower() not in EXTENSIONS[kind] or not p.is_file() or not p.stat().st_size:
            raise ValueError('参考素材类型不匹配或为空')
        from media_gateway import load
        gateway = bool(load().get('base_url'))
        if cfg.get('id') in ('agnes', 'aliyun') and not gateway:
            raise ValueError('该服务商参考音视频需要公网 URL；请在环境检查配置公网素材出口')
        if not gateway and p.stat().st_size > 45 * 1024 * 1024: raise ValueError('本地参考素材超过 45 MB，请配置公网素材出口')
        result.append({'path': p.relative_to(project).as_posix(), 'sha256': digest(p)})
    return result
