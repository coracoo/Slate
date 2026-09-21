# -*- coding: utf-8 -*-
"""Slate 账号体系（单管理员，stdlib only）。

- 口令 scrypt 加盐哈希，凭据与签名密钥存 workbench/auth.json（gitignore，绝不入库）。
- 会话=HMAC-SHA256 签名 cookie（token=exp.signature），无服务端会话表，重启不掉线。
- 登录限速：连续失败 5 次锁 60 秒（内存计数，按来源 IP）。
- 首次使用：未配置口令时 setup() 允许设置——第一个设置密码的人成为管理员。
"""
import hashlib
import hmac
import json
import os
import secrets
import time

AUTH_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'auth.json')
SESSION_TTL = 30 * 24 * 3600
COOKIE = 'slate_session'
LOCK_LIMIT, LOCK_WINDOW = 5, 60

# F04：登录限速是内存计数，进程重启即清零——局域网单管理员场景可接受；
# cookie 未加 Secure：服务走 HTTP 局域网，若日后上 HTTPS 反代需补 Secure 标记。
_failures = {}


def _path():
    return os.path.abspath(AUTH_PATH)


def _load():
    try:
        with open(_path(), encoding='utf-8') as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save(data):
    from pathlib import Path
    p = Path(_path())
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(p) + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, p)
    try:
        os.chmod(p, 0o600)   # 凭据文件仅属主可读
    except OSError:
        pass


def _hash(password, salt):
    return hashlib.scrypt(password.encode('utf-8'), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1).hex()


def configured():
    data = _load()
    return bool(data.get('password_hash') and data.get('salt') and data.get('secret'))


def setup(password):
    if not isinstance(password, str) or len(password) < 6:
        raise ValueError('密码至少 6 位')
    if configured():
        raise ValueError('管理员口令已配置，请登录后修改')
    salt = secrets.token_hex(16)
    _save({'salt': salt, 'password_hash': _hash(password, salt), 'secret': secrets.token_hex(32),
           'created_at': time.strftime('%Y-%m-%dT%H:%M:%S')})
    return login(password)


def login(password, client=''):
    if not configured():
        raise ValueError('尚未配置管理员口令')
    key = str(client)
    now = time.time()
    state = _failures.get(key) or {'n': 0, 'until': 0}
    if state['until'] > now:
        raise ValueError(f'尝试过频，请 {int(state["until"] - now) + 1}s 后再试')
    data = _load()
    ok = hmac.compare_digest(_hash(password or '', data['salt']), data['password_hash'])
    if not ok:
        state['n'] += 1
        if state['n'] >= LOCK_LIMIT:
            state.update(n=0, until=now + LOCK_WINDOW)
        _failures[key] = state
        raise ValueError('口令错误' + ('' if state['n'] else '，尝试过频已锁定 60s'))
    _failures.pop(key, None)
    return issue()


def change(old, new):
    data = _load()
    if not configured():
        return setup(new)
    if not hmac.compare_digest(_hash(old or '', data['salt']), data['password_hash']):
        raise ValueError('原口令错误')
    if not isinstance(new, str) or len(new) < 6:
        raise ValueError('新密码至少 6 位')
    salt = secrets.token_hex(16)
    # F03：改密同时轮换会话签名密钥并清空撤销名单——旧会话全部立即失效
    data.update(salt=salt, password_hash=_hash(new, salt), secret=secrets.token_hex(32), revoked={})
    _save(data)
    return issue()


def issue():
    data = _load()
    exp = int(time.time()) + SESSION_TTL
    token = f'{exp}.{hmac.new(bytes.fromhex(data["secret"]), str(exp).encode(), hashlib.sha256).hexdigest()}'
    return token


def verify(token):
    if not configured() or not token: return False
    try:
        exp, sig = str(token).split('.', 1)
        if int(exp) < time.time(): return False
        data = _load()
        return hmac.compare_digest(sig, hmac.new(bytes.fromhex(data['secret']), exp.encode(), hashlib.sha256).hexdigest())
    except Exception:
        return False


def logout(token):
    """签名会话无状态：把该 token 加入撤销名单（带过期时间，随文件清理）。

    F05：撤销键是 exp 秒级时间戳——同一秒签发的会话会一起被撤销，
    单管理员场景无碍；改密（F03）会轮换 secret 使全部会话失效。
    """
    if not token: return
    data = _load()
    revoked = {k: v for k, v in (data.get('revoked') or {}).items() if float(k) > time.time()}
    revoked[str(token).split('.', 1)[0]] = True
    data['revoked'] = revoked
    _save(data)


def verify_not_revoked(token):
    if not verify(token): return False
    data = _load()
    exp = str(token).split('.', 1)[0]
    return not (data.get('revoked') or {}).get(exp)
