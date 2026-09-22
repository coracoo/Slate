# -*- coding: utf-8 -*-
"""Slate 常驻守护：拉起 server.py 并在其退出后自动重启（退避+冷却），避免挂机期间失联。

用法（替代直接跑 server.py）：
    ./.venv/Scripts/python.exe workbench/keepalive.py 8775

- 崩溃/被杀自动拉起，退避 0s→5s→15s→30s→60s（封顶）；连续快速失败进入 120s 冷却并日志告警。
- 优雅停止：创建 workbench/STOP 文件（守护检测到后不再重启并自行退出）；删除文件重新运行即恢复。
- 每次启动/退出写 workbench/logs/keepalive.log（时间、退出码、运行时长），崩溃留痕可查。
"""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
STOP = HERE / 'STOP'
LOG = HERE / 'logs' / 'keepalive.log'
BACKOFF = [0, 5, 15, 30, 60]
FAST_FAIL = 10          # 运行不足此秒数视为快速失败
COOLDOWN = 120          # 连续快速失败冷却


def log(msg):
    LOG.parent.mkdir(parents=True, exist_ok=True)
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


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else '8775'
    # 单实例锁：占用 port+1；第二个守护（或换端口的其它守护冲突时）直接退出，杜绝双守护抢端口
    guard = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        guard.bind(('127.0.0.1', int(port) + 1))
        guard.listen(0)
    except OSError:
        print(f'[keepalive] 端口 {int(port)+1} 已被占用——已有守护在运行，本次退出', flush=True)
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
            code = proc.wait()
        except KeyboardInterrupt:
            log('收到 Ctrl+C，停止 server 与守护')
            proc.terminate()
            return
        uptime = time.time() - started
        log(f'server 退出：returncode={code}，运行 {uptime:.0f}s')
        # F14：exit 0 且秒退基本是主动拒绝启动（版本守卫/参数错误），与崩溃分开标注
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
