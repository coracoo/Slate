# -*- coding: utf-8 -*-
"""Windows 子进程文件传输：避免 chrome-use 常驻子进程继承 PIPE 导致不退出。"""
import subprocess
import tempfile


def run_command(command, timeout=30, input_text=None, env=None):
    with tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        if input_text is not None:
            source.write(input_text.encode('utf-8')); source.seek(0)
        process = subprocess.Popen(command, stdin=source, stdout=out, stderr=err, env=env,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            process.wait(timeout=max(1, timeout))
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=10)
            raise
        out.seek(0); err.seek(0)
        return subprocess.CompletedProcess(command, process.returncode,
            out.read().decode('utf-8', 'replace'), err.read().decode('utf-8', 'replace'))
