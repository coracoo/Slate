# -*- coding: utf-8 -*-
"""GitHub 更新检查 / 安全拉取 / 回退（git CLI，stdlib only）。

- check()：fetch upstream 后对比 behind/ahead，结果缓存 10 分钟。
  F09：fetch 强制 BatchMode，避免守护/服务环境下 SSH 卡在口令或 host key 提示。
- apply()：安全拉取——工作区必须干净；记录 prev_head 到 update_state.json；
  打 backup-<ts> 分支锚点（F08：成功后顺手清理 30 天前的 backup 分支）；
  merge --ff-only（拒绝非快进，绝不产生冲突态）；
  F07：本地有未推送提交（ahead>0）且远端也有更新时拒绝并给出中文指引；
  F06：dist 不入库——拉到的提交动了前端源码时返回 needs_build 提示手动重建。
- rollback()：reset --hard 回 update_state.json 记录的 prev_head。
- F11：apply/rollback 互斥锁，防并发操作。
- 应用/回滚后需重启进程生效：server 退出 → keepalive 守护自动拉起新代码。
"""
import json
import os
import subprocess
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATE_PATH = Path(__file__).resolve().parents[1] / 'update_state.json'
_cache = {'at': 0.0, 'data': None}
_OP = threading.Lock()                    # F11：apply/rollback 互斥
BACKUP_KEEP_SECONDS = 30 * 86400          # F08：backup 分支保留 30 天
_WEB_FILES = ('workbench/web/package.json', 'workbench/web/package-lock.json')


def _git(*args, timeout=40, env=None):
    r = subprocess.run(['git', *args], cwd=str(ROOT), capture_output=True, text=True,
                       encoding='utf-8', errors='replace', timeout=timeout, env=env)
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
        # F09：BatchMode 防止 SSH 在无交互环境（keepalive/服务）里挂起等口令
        _git('fetch', 'origin', '--quiet', timeout=60,
             env={**os.environ, 'GIT_SSH_COMMAND': 'ssh -o BatchMode=yes'})
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


def _needs_build(changed):
    """F06：拉到的提交动了前端源码/依赖清单 → dist 需手动重建（dist 不入库）。"""
    return any(p.startswith('workbench/web/src/') or p in _WEB_FILES for p in changed)


def _prune_backups(now=None):
    """F08：清理 30 天前的 backup-YYYYMMDD_HHMMSS 锚点分支；失败不阻断更新。"""
    try:
        cutoff = (now or time.time()) - BACKUP_KEEP_SECONDS
        for line in _git('branch', '--list', 'backup-*').splitlines():
            name = line.strip().lstrip('* ').strip()
            try:
                ts = time.mktime(time.strptime(name, 'backup-%Y%m%d_%H%M%S'))
            except ValueError:
                continue
            if ts < cutoff:
                _git('branch', '-D', name)
    except Exception:
        pass


def apply():
    if not _OP.acquire(blocking=False):
        raise RuntimeError('已有更新/回滚操作进行中，请稍候')
    try:
        if _dirty(): raise RuntimeError('工作区有未提交改动，先提交或还原再更新（保护你的本地修改）')
        prev = _git('rev-parse', 'HEAD')
        applied = check(force=True)
        if not applied.get('ok'): raise RuntimeError(applied.get('err') or '无法读取远端')
        if not applied.get('behind'): return {'ok': True, 'updated': False, 'note': '已是最新'}
        # F07：本地有未推送提交时 ff-only 必然失败，提前给出可行动的中文指引
        if applied.get('ahead'):
            raise RuntimeError(f"本地有 {applied['ahead']} 个未推送提交，直接更新会分叉；请先 git push 或备份本地提交后再更新")
        tag = 'backup-' + time.strftime('%Y%m%d_%H%M%S')
        _git('branch', '-f', tag, prev)
        _git('merge', '--ff-only', f"origin/{applied['branch']}")
        changed = [p for p in _git('diff', '--name-only', prev, 'HEAD').splitlines() if p]
        state = {'prev_head': prev, 'backup_branch': tag, 'applied_at': time.strftime('%Y-%m-%dT%H:%M:%S')}
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
        _prune_backups()
        needs_build = _needs_build(changed)
        note = '更新完成，重启进程后生效'
        if needs_build:
            note += '；本次含前端源码改动，请先在仓库根执行 npm ci && npm run build，否则前端仍是旧版'
        return {'ok': True, 'updated': True, 'from': prev[:9], 'to': _git('rev-parse', 'HEAD')[:9],
                'backup': tag, 'needs_build': needs_build, 'note': note}
    finally:
        _OP.release()


def rollback():
    if not _OP.acquire(blocking=False):
        raise RuntimeError('已有更新/回滚操作进行中，请稍候')
    try:
        if _dirty(): raise RuntimeError('工作区有未提交改动，先提交或还原再回滚')
        if not STATE_PATH.is_file(): raise RuntimeError('没有可回滚的更新记录')
        state = json.loads(STATE_PATH.read_text(encoding='utf-8'))
        prev = state.get('prev_head')
        if not prev: raise RuntimeError('更新记录缺少 prev_head')
        _git('reset', '--hard', prev)
        STATE_PATH.unlink()
        return {'ok': True, 'rolled_back_to': prev[:9], 'note': '回滚完成，重启进程后生效'}
    finally:
        _OP.release()
