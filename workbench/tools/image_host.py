# -*- coding: utf-8 -*-
"""公网图床适配器：把本地参考图（宫格图/关键帧/资产图）上传到公网图床，返回 Agnes 等只认公网 URL 的厂商可拉取的直链。

支持三种后端（media_gateway.json 的 image_host 段配置）：
- {"type":"imgbb","api_key":..}   # imgbb.com——国内直连可达，免费注册拿 key（推荐国内用户）
- {"type":"imgur","client_id":..,"proxy":?}  # 标准 Imgur API v3，最通用（开源默认）；国内需代理（可选 proxy:"http://127.0.0.1:7890"）
- {"type":"webdav","base_url":"https://图床静态站","webdav_url":"http://webdav服务","username":..,"password":..,
   "remote_dir":"/images"}    # 自建：上传走 WebDAV PUT，直链= base_url+remote_dir+/{year}/{month}/{name}

无 image_host 配置时抛中文错误指路（环境检查「填写公网素材出口」弹窗里配）。
上传产物按内容哈希命名幂等：同图重传直接复用已存在 URL。
"""
import base64
import json
import re
import time
import urllib.request
import urllib.error
from pathlib import Path

from media_gateway import load as gateway_load


def host_config():
    cfg = gateway_load()
    host = cfg.get('image_host') if isinstance(cfg.get('image_host'), dict) else None
    return host or None


def _http(url, *, method='GET', headers=None, data=None, timeout=60, expect_json=True, proxy=None):
    req = urllib.request.Request(url, data=data, method=method, headers={'User-Agent': 'Slate/1.0', **(headers or {})})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({'http': proxy, 'https': proxy})) if proxy else urllib.request.build_opener()
    with opener.open(req, timeout=timeout) as r:
        raw = r.read()
        return (json.loads(raw.decode('utf-8', 'replace')) if expect_json else raw), r.status


def upload_image(local_path, cfg=None):
    """上传本地图片到公网图床，返回直链 URL。按内容哈希幂等（同图同 URL）。"""
    host = cfg or host_config()
    if not host:
        raise ValueError('未配置公网图床：请在环境检查「填写公网素材出口」弹窗配置 image_host（webdav 或 imgur）')
    src = Path(local_path)
    if not src.is_file():
        raise ValueError(f'待上传图片不存在：{src}')
    digest = _sha256(src)
    # 幂等名：内容哈希前 16 位 + 原扩展名——同图重传 URL 不变，Agnes 侧重试安全
    name = digest[:16] + src.suffix.lower()
    kind = str(host.get('type') or '').lower()
    if kind == 'webdav':
        return _upload_webdav(src, name, host)
    if kind == 'imgur':
        return _upload_imgur(src, digest, host)
    if kind == 'imgbb':
        return _upload_imgbb(src, digest, host)
    if kind == 'cloudflare':
        return _upload_cloudflare(src, digest, host)
    raise ValueError(f'未知图床类型 {kind}（支持 imgbb / cloudflare / imgur / webdav）')


def _upload_webdav(src, name, host):
    t = time.localtime()
    remote_dir = str(host.get('remote_dir') or '/images').rstrip('/')
    rel = f"{t.tm_year}/{t.tm_mon:02d}/{name}"
    dest = f"{str(host['webdav_url']).rstrip('/')}{remote_dir}/{rel}"
    auth = base64.b64encode(f"{host.get('username','')}:{host.get('password','')}".encode()).decode()
    req = urllib.request.Request(dest, data=src.read_bytes(), method='PUT',
                                 headers={'Authorization': 'Basic ' + auth,
                                          'Content-Type': 'image/' + (src.suffix.lstrip('.').lower() or 'png'),
                                          'Overwrite': 'T'})
    with urllib.request.urlopen(req, timeout=120) as r:
        if r.status not in (200, 201, 204):
            raise ValueError(f'WebDAV 上传失败 HTTP {r.status}')
    public = str(host['base_url']).rstrip('/') + remote_dir + '/' + rel
    return public


def _upload_imgur(src, digest, host):
    cache = _imgur_cache()
    cache_key = _cache_key(host, digest)
    if cache_key in cache:
        return cache[cache_key]
    boundary = '----slate' + digest[:12]
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{src.name}"\r\n'
            f'Content-Type: application/octet-stream\r\n\r\n').encode() + src.read_bytes() + f'\r\n--{boundary}--\r\n'.encode()
    data, status = _http('https://api.imgur.com/3/image', method='POST', data=body,
                         headers={'Authorization': 'Client-ID ' + host['client_id'],
                                  'Content-Type': 'multipart/form-data; boundary=' + boundary},
                         proxy=str(host.get('proxy') or '') or None)
    link = (data.get('data') or {}).get('link')
    if not link:
        raise ValueError('Imgur 未返回直链：' + json.dumps(data, ensure_ascii=False)[:150])
    _imgur_cache_write(cache_key, link)
    return link


def _upload_imgbb(src, digest, host):
    """imgbb.com：国内直连可达的免费图床（api.imgbb.com/1/upload，免费注册拿 api_key）。"""
    import urllib.parse
    cache = _imgur_cache()
    cache_key = _cache_key(host, digest)
    if cache_key in cache:
        return cache[cache_key]
    body = urllib.parse.urlencode({'key': host['api_key'], 'image': base64.b64encode(src.read_bytes()).decode()}).encode()
    data, _ = _http('https://api.imgbb.com/1/upload', method='POST', data=body,
                    headers={'Content-Type': 'application/x-www-form-urlencoded'})
    url = (data.get('data') or {}).get('url')
    if not url:
        raise ValueError('imgbb 未返回直链：' + json.dumps(data, ensure_ascii=False)[:150])
    _imgur_cache_write(cache_key, url)
    return url


def _upload_cloudflare(src, digest, host):
    """Cloudflare Images：POST /accounts/{account_id}/images/v1（Bearer API Token）。
    直链取 result.variants[0]（需 Images 公共交付开启）；国内经 Cloudflare CDN 通常可达。"""
    cache = _imgur_cache()
    cache_key = _cache_key(host, digest)
    if cache_key in cache:
        return cache[cache_key]
    account = str(host.get('account_id') or '').strip()
    token = str(host.get('api_token') or '').strip()
    if not account or not token:
        raise ValueError('Cloudflare 图床需填写 account_id 与 api_token')
    boundary = '----slate' + digest[:12]
    CR = chr(13); LF = chr(10)
    head = ('--' + boundary + CR + LF
            + 'Content-Disposition: form-data; name="requireSignedURLs"' + CR + LF + CR + LF
            + 'false' + CR + LF
            + '--' + boundary + CR + LF
            + 'Content-Disposition: form-data; name="file"; filename="' + src.name + '"' + CR + LF
            + 'Content-Type: application/octet-stream' + CR + LF + CR + LF)
    tail = CR + LF + '--' + boundary + '--' + CR + LF
    data, _ = _http('https://api.cloudflare.com/client/v4/accounts/' + account + '/images/v1', method='POST',
                    data=head.encode() + src.read_bytes() + tail.encode(),
                    headers={'Authorization': 'Bearer ' + token,
                             'Content-Type': 'multipart/form-data; boundary=' + boundary})
    result = (data.get('result') or {})
    variants = result.get('variants') or []
    url = str(variants[0]) if variants else ''
    if not url:
        raise ValueError('Cloudflare 未返回直链（检查 Images 公共交付配置）：' + json.dumps(data, ensure_ascii=False)[:150])
    _imgur_cache_write(cache_key, url)
    return url


_CACHE_FILE = Path(__file__).resolve().parents[1] / 'runtime' / 'imgur_uploads.json'


def _cache_key(host, digest):
    """图床类型、账户及访问配置变化后重新上传；键只保存配置摘要。"""
    import hashlib
    encoded = json.dumps(host, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    fingerprint = hashlib.sha256(encoded).hexdigest()
    return 'host-v1:' + fingerprint + ':' + digest


def _imgur_cache():
    try:
        return json.loads(_CACHE_FILE.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _imgur_cache_write(digest, link):
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        cache = _imgur_cache()
        cache[digest] = link
        _CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding='utf-8')
    except Exception:
        pass


def _sha256(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()
