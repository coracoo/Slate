# -*- coding: utf-8 -*-
"""模型媒体输入编码；URL 直传，其余使用真实 MIME，拒绝空文件。"""
import base64
import mimetypes
from pathlib import Path


def encode(ref):
    value = str(ref)
    if value.startswith(('https://','http://','data:','oss://')): return value
    p = Path(value)
    if not p.is_file() or not p.stat().st_size: raise ValueError('参考素材不存在或为空')
    if p.stat().st_size > 45 * 1024 * 1024: raise ValueError('参考素材过大，请使用公网 URL')
    mime = mimetypes.guess_type(value)[0] or 'application/octet-stream'
    return f'data:{mime};base64,' + base64.b64encode(p.read_bytes()).decode('ascii')
