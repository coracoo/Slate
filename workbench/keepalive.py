# -*- coding: utf-8 -*-
"""Slate 常驻守护：拉起 server.py 并在其退出后自动重启（退避+冷却），避免挂机期间失联。

用法（替代直接跑 server.py）：
    ./.venv/Scripts/python.exe workbench/keepalive.py 8775

- 崩溃/被杀自动拉起，退避 0s→5s→15s→30s→60s（封顶）；连续快速失败进入 120s 冷却并日志告警。
- 优雅停止：创建 workbench/STOP 文件——server 运行期间也会轮询（约 2s），发现后先停 server
  再退出守护，不再重启；删除文件重新运行即恢复。
- 单实例：锁 runtime/keepalive.lock 首字节（msvcrt.locking，进程死亡 OS 自动释放）；
  不再用 port+1 端口试探（8776 被无关进程占用不再误伤）。
- 每次启动/退出写 workbench/logs/keepalive.log（时间、退出码、运行时长），>1MB 自动轮转
  为 keepalive.log.1（只保留 1 份备份），崩溃留痕可查。
"""
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
STOP = HERE / 'STOP'
LOG = HERE / 'logs' / 'keepalive.log'
LOCK = HERE / 'runtime' / 'keepalive.lock'
LOG_MAX = 1024 * 1024     # 超过 1MB 轮转（P06）
BACKOFF = [0, 5, 15, 30, 60]
FAST_FAIL = 10          # 运行不足此秒数视为快速失败
COOLDOWN = 120          # 连续快速失败冷却
POLL_SEC = 2            # server 运行期轮询 STOP 的间隔（P02）


def log(msg):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    try:
        # 轮转（P06）：>1MB 时 keepalive.log → keepalive.log.1（只保留 1 份备份，简单即可）
        if LOG.exists() and LOG.stat().st_size > LOG_MAX:
            os.replace(LOG, LOG.parent / (LOG.name + '.1'))
    except OSError:
        pass
    stamp = time.strftime('%Y-%m-%d %H:%M:%S')
    with open(LOG, 'a', encoding='utf-8') as fh:
        fh.write(f'[{stamp}] {msg}\n')
    try:
        # pythonw 下 sys.stdout 为 None，print 会抛错打死守护（09-22 实锤：pythonw 守护
        # 写完"拉起"日志即消失，server 沦为孤儿无人看护）——仅前台运行时打印
        if sys.stdout is not None:
            print(f'[keepalive {stamp}] {msg}', flush=True)
    except Exception:
        pass


def acquire_guard():
    """守护单实例锁（P05）：msvcrt.locking 非阻塞锁 runtime/keepalive.lock 首字节，
    替代原 port+1 端口试探（8776 被无关进程占用会误判"已有守护"而退出）。
    进程死亡 OS 自动释放，无 stale；非 Windows 兜底 fcntl，都没有则放弃互斥不阻断。
    返回锁句柄（须持有到进程结束）；已有守护持锁返回 None。"""
    try:
        LOCK.parent.mkdir(parents=True, exist_ok=True)
        fh = open(LOCK, 'a+b')
    except OSError:
        return False        # 连锁文件都打不开：放弃互斥但不阻断启动
    try:
        # 锁第 64 字节（远离文件头）：文件头的 PID 文本在持锁期间仍可读，便于排查
        fh.seek(64)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            try:
                import fcntl
            except ImportError:
                return fh
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    # 锁内写入 PID 便于排查（截断到锁位之前，避免误清锁字节）
    fh.seek(0)
    fh.write(str(os.getpid()).encode('ascii'))
    fh.truncate(32)
    fh.flush()
    return fh


def terminate_tree(proc):
    """停止 server 子进程：Windows 用 taskkill /T 连子进程树一起杀，失败退化 terminate。"""
    try:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/T', '/F', '/PID', str(proc.pid)],
                           capture_output=True, timeout=15)
        else:
            proc.terminate()
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else '8775'
    # 单实例锁（P05）：已有守护在跑直接退出，杜绝双守护抢端口；该提示走 log()（自带 pythonw 防护）
    guard = acquire_guard()
    if guard is None:
        log(f'已有 keepalive 守护在运行（锁文件 runtime/keepalive.lock），本次退出')
        return
    python = sys.executable
    server = str(HERE / 'server.py')
    log(f'守护启动：{python} {server} {port}')
    backoff_i = 0
    fast_fails = 0
    while True:
        if STOP.exists():
            log('检测到 STOP 文件，守护退出（删除 STOP 后重新运行 keepalive 即恢复）')
            return
        started = time.time()
        proc = subprocess.Popen([python, server, port], cwd=str(HERE.parent))
        log(f'拉起 server pid={proc.pid}')
        try:
            # P02：运行期也轮询 STOP（阻塞 wait 改为 ~2s poll），发现后先停 server 再结束守护
            while True:
                code = proc.poll()
                if code is not None:
                    break
                if STOP.exists():
                    log('检测到 STOP 文件（server 运行期），停止 server 并退出守护')
                    terminate_tree(proc)
                    proc.wait()
                    return
                time.sleep(POLL_SEC)
        except KeyboardInterrupt:
            log('收到 Ctrl+C，停止 server 与守护')
            terminate_tree(proc)
            return
        uptime = time.time() - started
        log(f'server 退出：returncode={code}，运行 {uptime:.0f}s')
        # F14：exit 0 且秒退基本是主动拒绝启动（版本守卫/参数错误/实例锁占用），与崩溃分开标注
        if code == 0 and uptime < FAST_FAIL:
            log('退出码 0 且秒退：疑似主动拒绝启动（检查 Python 须 3.12 / 端口占用 / 配置），非崩溃')
        if uptime >= FAST_FAIL:
            fast_fails = 0
            backoff_i = 0
        else:
            fast_fails += 1
            backoff_i = min(backoff_i + 1, len(BACKOFF) - 1)
            if fast_fails >= 5:
                log(f'连续 {fast_fails} 次快速失败，冷却 {COOLDOWN}s（请查看 server 输出与 logs/ 排查）')
                time.sleep(COOLDOWN)
                fast_fails = 0
        delay = BACKOFF[backoff_i]
        if delay:
            time.sleep(delay)


if __name__ == '__main__':
    main()
