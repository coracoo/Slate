# -*- coding: utf-8 -*-
"""发布前扫描 Git 候选文件；仅报告文件/行号，不打印凭证。历史检查加 --history。"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

try: sys.stdout.reconfigure(encoding='utf-8')
except Exception: pass
ROOT = Path(__file__).resolve().parents[2]
RULES = {
    '私钥': rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
    'JWT': rb'eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{15,}',
    '疑似密钥': rb'\b(?:sk-|ghp_|github_pat_|AKIA)[A-Za-z0-9_-]{20,}',
    '字面量凭证': rb'''(?i)["']?(?:api_key|access_token|client_secret|password)["']?\s*[:=]\s*["'][A-Za-z0-9_./+=-]{20,}["']''',
}
PRIVATE = re.compile(r'(^|/)(?:projects|_vendor|node_modules|exports|jobs|runtime|\.git_backup_[^/]+)(/|$)|(^|/)(?:providers|media_gateway|llm_config|mcp|mcp_runtime|source_manifest|user_cards)\.json(?:\.|$)|(^|/)\.env(?:\.|$)')


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT)


def scan(raw, path):
    count = 0
    for label, pattern in RULES.items():
        for match in re.finditer(pattern, raw):
            print(f'{label}: {path}:{raw[:match.start()].count(bytes([10]))+1}')
            count += 1
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--history', action='store_true')
    args = parser.parse_args()
    count = 0
    files = set(git('ls-files','--cached','--others','--exclude-standard','-z').decode('utf-8').split('\0')) - {''}
    for file in sorted(files):
        path = ROOT/file
        if not path.is_file(): continue
        if PRIVATE.search(file) and '.example' not in file:
            print('私人/运行文件进入候选: '+file); count += 1
        count += scan(path.read_bytes(),file)
    if args.history:
        for line in git('rev-list','--objects','--all').decode('utf-8').splitlines():
            sha, _, path = line.partition(' ')
            if git('cat-file','-t',sha).strip() != b'blob': continue
            if PRIVATE.search(path) and '.example' not in path:
                print(f'历史私人/运行文件: {path} @{sha[:10]}'); count += 1
            count += scan(git('cat-file','-p',sha),path+' @'+sha[:10])
    print(f'检查结束：{len(files)} 个候选文件，{count} 项待核查。扫描通过不等于绝对不存在隐私。')
    return 1 if count else 0


if __name__ == '__main__': raise SystemExit(main())
