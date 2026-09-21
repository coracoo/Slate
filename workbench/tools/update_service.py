# -*- coding: utf-8 -*-
"""GitHub 更新检查 / 安全拉取 / 回退（git CLI，stdlib only）。

- check()：fetch upstream 后对比 behind/ahead，结果缓存 10 分钟。
- apply()：安全拉取四步——工作区必须干净；记录 prev_head 到 update_state.json；
  打 backup-<ts> 分支锚点；merge --ff-only（拒绝非快进，绝不产生冲突态）。
- rollback()：reset --hard 回 update_state.json 记录的 prev_head。
- 应用/回滚后需重启进程生效：server 退出 → keepalive 守护自动拉起新代码。
"""
import json
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATE_PATH = Path(__file__).resolve().parents[1] / 'update_state.json'
_cache = {'at': 0.0, 'data': None}


def _git(*args, timeout=40):
    r = subprocess.run(['git', *args], cwd=str(ROOT), capture_output=True, text=True,
                       encoding='utf-8', errors='replace', timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or 'git 失败').strip()[:300])
    return r.stdout.strip()


def _branch():
    return _git('rev-parse', '--abbrev-ref', 'HEAD') or 'main'


def check(force=False):
    if not force and _cache['data'] and time.time() - _cache['at'] < 600:
        return _cache['data']
    try:
        _git('remote', 'get-url', 'origin')
    except Exception:
        result = {'ok': False, 'supported': False, 'err': '未配置 git remote origin'}
        _cache.update(at=time.time(), data=result)
        return result
    try:
        _git('fetch', 'origin', '--quiet', timeout=60)
        branch = _branch()
        upstream = f'origin/{branch}'
        behind = int(_git('rev-list', '--count', f'HEAD..{upstream}') or 0)
        ahead = int(_git('rev-list', '--count', f'{upstream}..HEAD') or 0)
        log = _git('log', f'HEAD..{upstream}', '--oneline', '-8') if behind else ''
        result = {'ok': True, 'supported': True, 'branch': branch, 'behind': behind,
                  'ahead': ahead, 'commits': [l for l in log.splitlines() if l.strip()]}
    except Exception as e:
        result = {'ok': False, 'supported': True, 'err': str(e)}
    _cache.update(at=time.time(), data=result)
    return result


def _dirty():
    return bool(_git('status', '--porcelain'))


def apply():
    if _dirty(): raise RuntimeError('工作区有未提交改动，先提交或还原再更新（保护你的本地修改）')
    prev = _git('rev-parse', 'HEAD')
    tag = 'backup-' + time.strftime('%Y%m%d_%H%M%S')
    _git('branch', '-f', tag, prev)
    applied = check(force=True)
    if not applied.get('ok'): raise RuntimeError(applied.get('err') or '无法读取远端')
    if not applied.get('behind'): return {'ok': True, 'updated': False, 'note': '已是最新'}
    _git('merge', '--ff-only', f"origin/{applied['branch']}")
    state = {'prev_head': prev, 'backup_branch': tag, 'applied_at': time.strftime('%Y-%m-%dT%H:%M:%S')}
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'ok': True, 'updated': True, 'from': prev[:9], 'to': _git('rev-parse', 'HEAD')[:9],
            'backup': tag, 'note': '更新完成，重启进程后生效'}


def rollback():
    if _dirty(): raise RuntimeError('工作区有未提交改动，先提交或还原再回滚')
    if not STATE_PATH.is_file(): raise RuntimeError('没有可回滚的更新记录')
    state = json.loads(STATE_PATH.read_text(encoding='utf-8'))
    prev = state.get('prev_head')
    if not prev: raise RuntimeError('更新记录缺少 prev_head')
    _git('reset', '--hard', prev)
    STATE_PATH.unlink()
    return {'ok': True, 'rolled_back_to': prev[:9], 'note': '回滚完成，重启进程后生效'}
