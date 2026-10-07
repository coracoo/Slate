# -*- coding: utf-8 -*-
"""RH 原生接口 CLI：制作槽之外的工具、3D、工作流和 AI 应用共用官方目录。"""
import argparse
import json
import sys
from pathlib import Path

from llm_openai import VendorClient, VendorError
from runninghub_catalog import catalog
from runninghub_client import RunningHubClient

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass


def main():
    parser = argparse.ArgumentParser(description='RH 原生接口；未知提交结果禁止自动重发')
    parser.add_argument('--providers', help='指定 providers.json；默认使用工作台配置')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--list', action='store_true', help='列出官方接口目录')
    group.add_argument('--endpoint', help='接口路径或标准模型 ID')
    group.add_argument('--task-id', help='查询此前提交的任务')
    group.add_argument('--upload', help='上传本地素材，返回 URL')
    group.add_argument('--upload-lora', help='上传 LoRA，返回 RHLoraLoader 可用的 fileName')
    parser.add_argument('--for-workflow', action='store_true', help='上传时返回 LoadImage 文件名；查询时使用旧工作流协议')
    parser.add_argument('--body-file', help='UTF-8 JSON 参数文件；不要在文件中写 API Key')
    parser.add_argument('--wait', action='store_true', help='等待任务完成，不重复提交')
    parser.add_argument('--timeout', type=int, default=3600, help='轮询等待上限，默认 3600 秒')
    parser.add_argument('--out', help='结果保存目录；下载全部文件，保留各自格式')
    args = parser.parse_args()
    if args.list:
        for row in catalog()['apis']:
            if not row['deprecated']:
                print(row['path'], row['label'])
        return 0
    client = VendorClient('runninghub', providers_json=args.providers, check_enabled=not bool(args.task_id))
    adapter = RunningHubClient(client)
    if args.upload:
        print(adapter.upload(args.upload,workflow=args.for_workflow))
        return 0
    if args.upload_lora:
        print(adapter.upload_lora(args.upload_lora))
        return 0
    if args.task_id:
        data = adapter.query(args.task_id,legacy=args.for_workflow)
    else:
        if not args.body_file:
            parser.error('--endpoint 需要 --body-file')
        payload = json.loads(Path(args.body_file).read_text(encoding='utf-8-sig'))
        data = adapter.call(args.endpoint, payload)
    nested = data.get('data') if isinstance(data.get('data'),dict) else {}
    task_id = str(data.get('taskId') or nested.get('taskId') or '')
    if task_id:
        print('RH 任务 ID：' + task_id, flush=True)
    if args.wait and task_id:
        legacy = args.for_workflow or bool(args.endpoint and args.endpoint.startswith('/task/openapi/'))
        adapter.wait(data, interval=5, max_wait=args.timeout,legacy=legacy)
        data = adapter.query(task_id,legacy=legacy)
    if args.out and data.get('status') == 'SUCCESS':
        folder = Path(args.out)
        folder.mkdir(parents=True, exist_ok=True)
        for i, row in enumerate(data.get('results') or []):
            if row.get('url'):
                suffix = str(row.get('outputType') or 'bin').lstrip('.')
                if not suffix.isalnum() or len(suffix) > 8:
                    suffix = 'bin'
                print(adapter.output({'taskId':task_id,'results':[row]}, out_path=folder/f'result_{i+1}.{suffix}'))
        return 0
    print(json.dumps(adapter.public_result(data), ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (VendorError, ValueError, OSError) as exc:
        print('[失败] ' + str(exc), file=sys.stderr)
        raise SystemExit(1)
