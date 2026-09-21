# -*- coding: utf-8 -*-
"""演员读剧本后台任务：请求快照与厂商配置分离，不在命令行携带密钥。"""
import json
import sys
from pathlib import Path
from actor_pipeline import prepare_context, _read_board
from error_utils import scrub_error


def execute(request_path):
    request = json.loads(Path(request_path).read_text(encoding='utf-8'))
    board = request['board_path']
    if _read_board(board)[1] != request['revision']:
        raise ValueError('分镜已变更，请刷新后重新准备演员上下文')
    callback = None
    if request.get('vendor_id'):
        from llm_openai import VendorClient
        config = json.loads((Path(__file__).resolve().parents[1]/'providers.json').read_text(encoding='utf-8'))
        vendor = next((v for v in config['vendors'] if v['id'] == request['vendor_id'] and v.get('enabled') and v.get('models',{}).get('text')), None)
        if vendor is None: raise ValueError('准备厂商不存在、未启用或未配置文本模型')
        client = VendorClient.from_config(vendor)
        callback = lambda messages: {'content':client.chat(messages,kind='text',max_tokens=5000,timeout=420,temperature=0.25)}
    result = prepare_context(board,request.get('shot_ids'),request.get('context'),call_llm=callback)
    print('OUTPUT:' + result['path'])
    if result.get('status') != 'ready':
        raise ValueError('演员准备失败：' + '；'.join(scrub_error(e.get('message','')) for e in result.get('errors',[])))
    return result


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    try: execute(sys.argv[1])
    except Exception as exc:
        print(scrub_error(exc),file=sys.stderr)
        raise SystemExit(1)
