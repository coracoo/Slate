# -*- coding: utf-8 -*-
"""公网素材出口：配置基础地址，只签发项目内音视频的限时链接。"""
import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from production_studio import inside
from video_profiles import public_url

CONFIG = Path(__file__).resolve().parents[1] / 'media_gateway.json'
EXTENSIONS = {'.mp3','.wav','.m4a','.aac','.flac','.ogg','.mp4','.mov','.mkv','.webm'}


def load():
    if not CONFIG.exists(): return {'base_url':'', 'ttl_seconds':86400}
    return json.loads(CONFIG.read_text('utf-8'))


def save(body):
    base = str(body.get('base_url') or '').strip().rstrip('/')
    if base:
        public_url(base)
        parts = urlsplit(base)
        if parts.query or parts.fragment: raise ValueError('出口地址不能包含查询参数或片段')
        _ = parts.port
    ttl = int(body.get('ttl_seconds') or 86400)
    if not 3600 <= ttl <= 604800: raise ValueError('链接有效期必须为 1 小时至 7 天')
    cfg = {**load(), 'base_url':base, 'ttl_seconds':ttl}
    cfg.setdefault('secret', secrets.token_hex(32))
    temp = CONFIG.with_suffix('.tmp')
    temp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), 'utf-8')
    temp.replace(CONFIG)
    return {k:cfg[k] for k in ('base_url','ttl_seconds')}


def signature(cfg, project, path, expires):
    raw = json.dumps([project,path,int(expires)],ensure_ascii=False,separators=(',',':')).encode()
    return hmac.new(cfg['secret'].encode(),raw,hashlib.sha256).hexdigest()


def signed_url(project, path):
    cfg = load()
    if not cfg.get('base_url'): raise ValueError('请在环境检查配置公网素材出口')
    p = inside(project,path)
    if p.suffix.lower() not in EXTENSIONS: raise ValueError('公网出口仅允许参考音视频')
    rel = p.relative_to(Path(project).resolve()).as_posix()
    name = Path(project).name
    expires = int(time.time()) + cfg['ttl_seconds']
    params = {'project':name,'path':rel,'expires':expires,'signature':signature(cfg,name,rel,expires)}
    return cfg['base_url'] + '/api/public-reference?' + urlencode(params)


def verify(projects_root, params):
    cfg = load()
    name, rel = params.get('project',''), params.get('path','')
    expires = int(params.get('expires',0))
    if not cfg.get('base_url') or not cfg.get('secret') or expires < time.time(): raise ValueError('素材链接未配置或已过期')
    if not hmac.compare_digest(signature(cfg,name,rel,expires),params.get('signature','')): raise ValueError('素材链接签名无效')
    if not name or Path(name).name != name or name in ('.','..'): raise ValueError('项目不合法')
    base = (Path(projects_root)/name).resolve()
    if not base.is_relative_to(Path(projects_root).resolve()): raise ValueError('项目路径越界')
    p = inside(base,rel)
    if p.suffix.lower() not in EXTENSIONS: raise ValueError('文件类型不允许')
    return p
