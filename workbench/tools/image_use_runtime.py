# -*- coding: utf-8 -*-
"""上游 image-use 单图执行器；产物只写指定暂存目录，不直接推断资产归属。"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import uuid
from PIL import Image
from image_use_process import run_command

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'runtime' / 'image-use'
CHROME_VERSION = 'v1.5.125'
_LOCK = threading.Lock()
UPSTREAM = 'https://github.com/leeguooooo/image-use'
EXTENSION = 'https://chromewebstore.google.com/detail/chrome-use/knfcmbamhjmaonkfnjhldjedeobeafmk'

def paths():
    script = RUNTIME / 'image-use'
    chrome = RUNTIME / CHROME_VERSION / 'chrome-use.exe'
    return script, chrome

def status():
    script, chrome = paths()
    verification={}
    try: verification=json.loads((RUNTIME/'verification.json').read_text(encoding='utf-8'))
    except (OSError,ValueError): pass
    return {'installed': script.is_file() and chrome.is_file(), 'busy': _LOCK.locked(),
            'verification':verification,
            'script': str(script), 'chrome_use': str(chrome), 'upstream': UPSTREAM,
            'extension_url': EXTENSION,
            'extension_download': f'https://raw.githubusercontent.com/leeguooooo/chrome-use/{CHROME_VERSION}/extensions/ab-connect.zip',
            'install_command': f'python "{ROOT / "tools" / "install_image_use.py"}"',
            'note': '安装检查不代表浏览器已登录；首次安装后启用上游扩展并登录 ChatGPT。'}

def generate_image(prompt, out_path, refs=None, timeout=900, **kwargs):
    script, chrome = paths()
    if not status()['installed']:
        raise RuntimeError('请先在环境检查中安装 image-use / chrome-use')
    if not _LOCK.acquire(blocking=False):
        raise RuntimeError('chrome-use 正在执行另一张图片；请等待，禁止并发复用浏览器')
    try:
        # 文件锁跨服务进程共享；进程退出由系统释放，不依赖删除锁文件。
        with (RUNTIME/'generation.lock').open('a+b') as lock:
            if lock.tell()==0: lock.write(b'0'); lock.flush()
            lock.seek(0)
            if os.name=='nt':
                import msvcrt
                try: msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
                except OSError: raise RuntimeError('另一进程正在运行 image-use，不能同时生成')
            else:
                import fcntl
                fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            return _generate(prompt, Path(out_path), refs or [], timeout, script, chrome)
    finally:
        _LOCK.release()

def _generate(prompt, output, refs, timeout, script, chrome):
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise RuntimeError('输出文件已存在，请对账或导入已有结果，不能覆盖后重绘')
    references = [str(Path(path).resolve(strict=True)) for path in refs]
    request_path = output.with_suffix('.request.json')
    if request_path.exists():
        raise RuntimeError('此请求已有执行记录；结果未知时禁止自动重发')
    request = {'script':str(script), 'prompt':str(prompt), 'references':references,
               'output':str(output), 'timeout':int(timeout), 'session':'previs-' + uuid.uuid4().hex[:12]}
    # x 模式同时是一次性发送标记；进程崩溃后不会自动重绘。
    with request_path.open('x', encoding='utf-8') as file:
        json.dump(request, file, ensure_ascii=False, indent=2)
    env = dict(os.environ, PYTHONIOENCODING='utf-8', IMAGE_USE_BACKEND='web')
    env['PATH'] = str(chrome.parent) + os.pathsep + env.get('PATH','')
    try:
        log_path=output.with_suffix('.log')
        with log_path.open('wb') as log:
            process=subprocess.Popen([sys.executable,'-u',str(ROOT/'tools'/'image_use_entry.py'),str(request_path)],
                stdout=log,stderr=log,stdin=subprocess.DEVNULL,env=env,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try: process.wait(timeout=int(timeout)+60)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=10)
                raise RuntimeError('image-use 超时；请核对保留的会话，禁止自动重新发送')
        if process.returncode:
            raise RuntimeError('image-use 已停止，未自动重试：' + log_path.read_text(encoding='utf-8',errors='replace')[-700:])
        with Image.open(output) as image:
            image.verify()
        with Image.open(output) as image:
            if min(image.size) < 512:
                raise RuntimeError('上游返回图片过小，保留暂存，不能计为原图完成')
        return str(output)
    except Exception as exc:
        output.with_suffix('.error.txt').write_text(str(exc), encoding='utf-8')
        raise
