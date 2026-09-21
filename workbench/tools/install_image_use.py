# -*- coding: utf-8 -*-
"""安装上游程序及 Chrome Native Messaging；扩展授权仍由浏览器完成。"""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from image_use_process import run_command
from image_use_runtime import RUNTIME, EXTENSION, CHROME_VERSION
IMAGE_COMMIT='1df8a9d6234825bdee6797b6df6b022ebce9fc5a'

def fetch(url):
    request=urllib.request.Request(url,headers={'User-Agent':'Previs-Workbench-Installer'})
    with urllib.request.urlopen(request,timeout=90) as response:
        return response.read()

def install():
    RUNTIME.mkdir(parents=True,exist_ok=True)
    commit=IMAGE_COMMIT
    script=fetch(f'https://raw.githubusercontent.com/leeguooooo/image-use/{commit}/image-use')
    # 固定提交并记录哈希；同时保存上游许可证。
    license_=fetch(f'https://raw.githubusercontent.com/leeguooooo/image-use/{commit}/LICENSE')
    release=json.loads(fetch('https://api.github.com/repos/leeguooooo/chrome-use/releases/tags/'+CHROME_VERSION))
    assets={a['name']:a['browser_download_url'] for a in release['assets']}
    name='chrome-use-win32-x64.tar.gz'
    archive=fetch(assets[name]); expected=fetch(assets[name+'.sha256']).decode().split()[0]
    if hashlib.sha256(archive).hexdigest()!=expected:
        raise RuntimeError('chrome-use 下载校验失败')
    with tempfile.TemporaryDirectory() as folder:
        target=Path(folder)/name; target.write_bytes(archive)
        with tarfile.open(target) as tar:
            members=[m for m in tar.getmembers() if Path(m.name).name=='chrome-use.exe' and m.isfile()]
            if len(members)!=1: raise RuntimeError('上游压缩包结构不符')
            binary=tar.extractfile(members[0]).read()
        binary_path=RUNTIME/CHROME_VERSION/'chrome-use.exe'
        binary_path.parent.mkdir(exist_ok=True)
        if not binary_path.exists() or hashlib.sha256(binary_path.read_bytes()).digest()!=hashlib.sha256(binary).digest():
            binary_path.write_bytes(binary)
    (RUNTIME/'image-use').write_bytes(script)
    (RUNTIME/'LICENSE.image-use').write_bytes(license_)
    (RUNTIME/'versions.json').write_text(json.dumps({'image_use_commit':commit,
        'image_use_sha256':hashlib.sha256(script).hexdigest(),'chrome_use':release['tag_name'],
        'chrome_use_archive_sha256':expected},indent=2),encoding='utf-8')
    result=run_command([str(binary_path),'extension','install'],timeout=40)
    if result.returncode: raise RuntimeError(result.stderr or result.stdout)
    print('安装完成。请在 Chrome 安装/启用上游扩展，然后登录 ChatGPT：\n'+EXTENSION)

if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    install()
