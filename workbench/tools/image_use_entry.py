# -*- coding: utf-8 -*-
"""运行原版 image-use；只适配 Windows 进程 I/O，不实现网页选择器或私有 API。"""
import json
import runpy
import sys
from image_use_process import run_command

def main():
    request = json.loads(open(sys.argv[1], encoding='utf-8').read())
    namespace = runpy.run_path(request['script'], run_name='previs_image_use_upstream')
    globals_ = namespace['main'].__globals__
    error = globals_['GatewayError']
    def command(ab, *args, session, timeout, profile=None, input_text=None):
        cmd = [ab] + (['--profile', profile] if profile else []) + list(args) + ['--session', session]
        result = run_command(cmd, timeout=max(5, timeout), input_text=input_text)
        if result.returncode:
            raise error('chrome-use 执行失败：' + (result.stderr or result.stdout)[-500:])
        if args and args[0]=='open':
            # relay 默认后台标签；ChatGPT 的交互提交要求页面前置。沿用上游浏览器命令。
            front=run_command([ab,'bringToFront','--session',session],timeout=10)
            if front.returncode: raise error('无法前置 ChatGPT 标签页，未继续发送')
        return result.stdout
    globals_['_ab'] = command
    # 强制 web；上游 auto 可能回退 Codex，此入口始终禁止回退。
    sys.argv = [request['script'], request['prompt'], '--backend', 'web', '--profile', 'relay',
                '--project', '', '--session', request['session'], '--keep-tab', '--keep-conversation',
                '--timeout', str(request['timeout']), '-o', request['output']]
    for path in request['references']:
        sys.argv += ['-i', path]
    return namespace['main']()

if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    raise SystemExit(main())
