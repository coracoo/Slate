# -*- coding: utf-8 -*-
"""后台启动工作台；保留追加日志并检查子进程是否真正启动。"""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path


def main():
    if sys.version_info < (3, 12):
        raise SystemExit("Slate 需要 Python 3.12+，请使用项目 .venv 中的 python.exe 启动。")
    wd = Path(__file__).resolve().parent
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8775
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            raise SystemExit(f"端口 {port} 已被占用，未启动新进程。请检查前台服务或守护进程。")
    log_path = wd / "server.log"
    # 使用同一个解释器；Windows 隐藏窗口，标准输出与异常均追加到日志。
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] 后台启动：{sys.executable}；端口 {port}\n")
        log.flush()
        flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS if os.name == "nt" else 0
        proc = subprocess.Popen([sys.executable, "-u", str(wd / "server.py"), str(port)],
                                cwd=wd, stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
        for _ in range(50):
            if proc.poll() is not None:
                raise SystemExit(f"启动失败（退出码 {proc.returncode}），日志：{log_path}")
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", port)) == 0:
                    print(f"已启动 PID {proc.pid}：http://127.0.0.1:{port}；日志：{log_path}")
                    return
            time.sleep(0.2)
        raise SystemExit(f"启动尚未确认，进程 PID {proc.pid} 仍保留；请查看 {log_path}，不要重复启动。")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    main()
