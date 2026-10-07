# -*- coding: utf-8 -*-
"""RH 模型/工作流任务、素材上传、结果持久化。只提交一次，查询失败保留任务 ID。"""
import base64
import copy
import hashlib
import json
import math
import mimetypes
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from runninghub_catalog import operation, properties, field, image_field, edit_operation, configured_parameters


def fail(message):
    from llm_openai import VendorError
    raise VendorError(str(message))


def validate(value, schema, name='请求体'):
    """检查明确声明的类型、枚举、数量与区间，拒绝丢弃参考素材。"""
    if schema.get('nullable') and value is None:
        return
    kind = schema.get('type')
    valid = {'object': isinstance(value, dict), 'array': isinstance(value, list),
             'string': isinstance(value, str), 'integer': type(value) == int,
             'number': type(value) in (int, float), 'boolean': type(value) == bool,
             'null': value is None}
    if kind in valid and not valid[kind]:
        fail(f'RH {name} 类型须为 {kind}')
    if 'enum' in schema and value not in schema['enum']:
        fail(f'RH {name} 不在官方允许值中：{schema["enum"]}')
    if isinstance(value, dict):
        for key in schema.get('required', []):
            if key not in value:
                fail(f'RH 缺少参数 {name}.{key}；请在该能力的附加参数 JSON 中填写')
        props = schema.get('properties', {})
        for key, val in value.items():
            if key not in props and schema.get('additionalProperties') is False:
                fail(f'RH 未声明参数 {name}.{key}')
            if key in props:
                validate(val, props[key], name + '.' + key)
    elif isinstance(value, list):
        if len(value) < schema.get('minItems', 0) or len(value) > schema.get('maxItems', math.inf):
            fail(f'RH {name} 数量超出允许范围，最多 {schema.get("maxItems", "未声明")} 项')
        for i, val in enumerate(value):
            validate(val, schema.get('items', {}), f'{name}[{i}]')
    elif isinstance(value, str):
        if len(value) < schema.get('minLength', 0) or len(value) > schema.get('maxLength', math.inf):
            fail(f'RH {name} 文本长度超出允许范围')
    elif type(value) in (int, float):
        if not math.isfinite(value) or value < schema.get('minimum', -math.inf) or value > schema.get('maximum', math.inf):
            fail(f'RH {name} 超出官方允许区间')
        if schema.get('multipleOf') and not math.isclose(value / schema['multipleOf'], round(value / schema['multipleOf']), abs_tol=1e-7):
            fail(f'RH {name} 不符合步长 {schema["multipleOf"]}')
    for child in schema.get('allOf', []):
        validate(value, child, name)


def converted(value, schema):
    """调用层通用值变为当前 API 的类型和枚举拼写，未知值仍交给校验拒绝。"""
    kind = schema.get('type')
    if kind == 'string' and type(value) in (int, float):
        value = str(int(value)) if float(value).is_integer() else str(value)
    elif kind == 'string' and type(value) == bool and set(schema.get('enum', [])) == {'true', 'false'}:
        value = str(value).lower()
    elif kind == 'integer' and type(value) in (int, float) and float(value).is_integer():
        value = int(value)
    if isinstance(value, str) and 'enum' in schema:
        match = [v for v in schema['enum'] if str(v).lower() == value.lower()]
        if len(match) == 1:
            value = match[0]
    return value


class RunningHubClient:
    def __init__(self, client):
        self.client = client
        self.base = client.base.rstrip('/')
        if self.base.endswith('/openapi/v2'):
            self.base = self.base[:-len('/openapi/v2')]
        self.uploads = {}

    def check(self):
        if not self.base or not self.client.cfg.get('api_key'):
            fail('RH 请先配置 Base URL 和企业级共享 API Key')

    def public_result(self, value):
        """原生模板接口可能回显真实 Key，展示到浏览器/CLI 前替换凭据。"""
        if isinstance(value,dict): return {k:self.public_result(v) for k,v in value.items()}
        if isinstance(value,list): return [self.public_result(v) for v in value]
        key = self.client.cfg.get('api_key')
        return value.replace(key,'***') if isinstance(value,str) and key else value

    @staticmethod
    def checked(data):
        if not isinstance(data, dict):
            fail('RH 响应不是 JSON 对象')
        if data.get('code') not in (None, 0, '0', 200, '200') or data.get('errorCode') not in (None, '', 0, '0'):
            fail('RH ' + str(data.get('errorCode') or data.get('code')) + '：' + str(data.get('errorMessage') or data.get('message') or data.get('msg') or '请求失败') + (f'（任务 {data["taskId"]}）' if data.get('taskId') else ''))
        return data

    DIRECT_PATHS = ('/openapi/v2/run/',)   # 工作流直跑不在模型目录内，允许直发

    def call(self, endpoint, payload, timeout=180):
        """目录内原生 JSON 接口，含工作流、AI 应用、工具、3D 和任务管理。"""
        self.check()
        if not any(str(endpoint).startswith(p) for p in self.DIRECT_PATHS):
            try:
                row = operation(endpoint)
            except ValueError as exc:
                fail(exc)
        else:
            row = {'id': str(endpoint), 'path': str(endpoint), 'method': 'POST', 'content_type': 'application/json',
                   'schema': {'type': 'object'}, 'properties': {}}
        body = copy.deepcopy(payload)
        if not isinstance(body, dict):
            fail('RH 原生请求体须为 JSON 对象')
        schema = row['schema']
        if 'apiKey' in properties(row) or row['path'].startswith(('/task/openapi/', '/api/openapi/')):
            body['apiKey'] = self.client.cfg['api_key']
        validate(body, schema)
        if row['content_type'] != 'application/json':
            fail('RH 文件上传请使用 upload 方法，不可按 JSON 提交')
        if row['method'] == 'GET':
            return self.checked(self.client._get(self.base + row['path'], timeout, params=body))
        return self.checked(self.client._post(self.base + row['path'], body, timeout))

    def upload(self, source, timeout=120, *, workflow=False):
        self.check()
        source = str(source)
        if not workflow and source.startswith(('http://', 'https://')):
            return source
        cache_key = (workflow, source)
        if cache_key in self.uploads:
            return self.uploads[cache_key]
        mime = mimetypes.guess_type(source)[0] or 'application/octet-stream'
        if source.startswith('data:'):
            try:
                head, encoded = source.split(',', 1)
                mime = head[5:].split(';', 1)[0]
                raw = base64.b64decode(encoded, validate=True)
            except (ValueError, TypeError):
                fail('RH Base64 参考素材无效')
            filename = 'reference' + (mimetypes.guess_extension(mime) or '.bin')
        else:
            path = Path(source)
            if not path.is_file():
                fail(f'RH 参考素材不存在：{source}')
            raw = path.read_bytes()
            # 保留后缀，用 ASCII 文件名避免 Windows/服务端 multipart 编码差异。
            filename = 'reference' + path.suffix
        if not raw:
            fail('RH 参考素材为空')
        boundary = 'slate-' + uuid.uuid4().hex
        prefix = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                  f'Content-Type: {mime}\r\n\r\n').encode()
        fields = ''
        if workflow:
            fields = ''.join(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'
                             for key, value in [('apiKey', self.client.cfg['api_key']), ('fileType', 'input')])
        body = fields.encode() + prefix + raw + f'\r\n--{boundary}--\r\n'.encode()
        headers = self.client._headers()
        headers['Content-Type'] = 'multipart/form-data; boundary=' + boundary
        endpoint = '/task/openapi/upload' if workflow else '/openapi/v2/media/upload/binary'
        request = urllib.request.Request(self.base + endpoint, data=body, headers=headers, method='POST')
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                data = self.checked(json.loads(response.read().decode('utf-8')))
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            fail(f'RH 上传参考素材失败：{exc}')
        url = (data.get('data') or {}).get('fileName' if workflow else 'download_url')
        if not isinstance(url, str) or not url or not workflow and not url.startswith(('http://', 'https://')):
            fail('RH 上传成功响应缺少 ' + ('fileName' if workflow else 'download_url'))
        self.uploads[cache_key] = url
        return url

    def upload_lora(self, source, timeout=180):
        """LoRA 使用 MD5 申请签名地址，再流式 PUT；签名存储请求不携带 RH Key。"""
        path = Path(source)
        if not path.is_file(): fail('RH LoRA 文件不存在')
        md5 = hashlib.md5()
        with path.open('rb') as fh:
            while block := fh.read(1024 * 1024): md5.update(block)
        data = self.call('/api/openapi/getLoraUploadUrl', {'loraName':path.name, 'md5Hex':md5.hexdigest()}, timeout)
        info = data.get('data') or {}
        url, name = info.get('url'), info.get('fileName')
        if not name or not isinstance(url,str) or not url.startswith(('http://','https://')):
            fail('RH LoRA 上传凭证缺少 url/fileName')
        try:
            with path.open('rb') as fh:
                request = urllib.request.Request(url, data=fh, method='PUT',
                    headers={'Content-Type':'application/octet-stream','Content-Length':str(path.stat().st_size)})
                with urllib.request.urlopen(request, timeout=timeout) as response: response.read()
        except (urllib.error.URLError,TimeoutError,OSError) as exc:
            fail(f'RH LoRA 上传失败：{exc}')
        return name

    def query(self, task_id, timeout=30, *, legacy=False):
        if not str(task_id or '').strip():
            fail('RH 查询需要 taskId')
        if legacy:
            status = self.call('/task/openapi/status', {'taskId':str(task_id)}, timeout).get('data')
            if status not in ('QUEUED','RUNNING','FAILED','SUCCESS'):
                fail(f'RH 工作流任务 {task_id} 返回未知状态，请检查官方任务记录')
            result = {'taskId':str(task_id), 'status':status, 'results':[]}
            if status == 'SUCCESS':
                rows = self.call('/task/openapi/outputs', {'taskId':str(task_id)}, timeout).get('data') or []
                result['results'] = [dict(url=r.get('fileUrl'),outputType=r.get('fileType'),nodeId=r.get('nodeId')) for r in rows]
            return result
        return self.call('/openapi/v2/query', {'taskId': str(task_id)}, timeout)

    def wait(self, submitted, *, interval=5, max_wait=3600, expected='', out_path=None, legacy=False):
        nested = submitted.get('data') if isinstance(submitted.get('data'), dict) else {}
        legacy = legacy or bool(nested.get('taskId') and 'taskStatus' in nested)
        task_id = str(submitted.get('taskId') or nested.get('taskId') or '')
        if not task_id:
            fail('RH 创建响应未返回 taskId，结果未知；禁止自动重发，请查 RH 任务记录')
        self.client.last_request = dict(provider='runninghub', task_id=task_id,
                                       query_url=self.base + ('/task/openapi/status' if legacy else '/openapi/v2/query'),
                                       query_protocol='workflow' if legacy else 'standard')
        callback = getattr(self.client, 'on_task_submitted', None)
        if callable(callback):
            callback(self.client.last_request)
        deadline = time.monotonic() + max_wait
        data = submitted
        while True:
            status = str(data.get('status') or (data.get('data') or {}).get('taskStatus') or '').upper()
            if status in ('FAILED', 'CANCELLED', 'CANCELED', 'ERROR'):
                fail(f'RH 任务 {task_id} 失败：{data.get("errorMessage") or data.get("failedReason") or status}')
            if status == 'SUCCESS':
                self.client.last_usage = data.get('usage')
                self.client.last_request['status'] = status
                return self.output(data, out_path=out_path, expected=expected)
            if time.monotonic() >= deadline:
                fail(f'RH 轮询超时，任务 ID {task_id} 已保留；可继续查询，不要重新生成')
            if interval:
                time.sleep(min(interval, max(0, deadline - time.monotonic())))
            try:
                data = self.query(task_id, timeout=min(30, max(1, deadline - time.monotonic())),legacy=legacy)
            except Exception as exc:
                fail(f'RH 查询任务 {task_id} 失败，任务 ID 已保留，可继续查询：{exc}')

    @staticmethod
    def output(data, *, out_path=None, expected=''):
        extensions = {'image': {'png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp'},
                      'video': {'mp4', 'mov', 'webm', 'mkv'}, 'audio': {'mp3', 'wav', 'flac', 'aac', 'ogg', 'm4a'}}
        rows = data.get('results') or []
        selected = []
        for row in rows:
            url = row.get('url') or ''
            suffix = str(row.get('outputType') or Path(urllib.parse.urlparse(url).path).suffix).lower().lstrip('.')
            if url and (not expected or suffix in extensions.get(expected, set()) or suffix == expected):
                selected.append(row)
        if not selected:
            if not expected and any(row.get('text') for row in rows):
                return '\n'.join(str(row['text']) for row in rows if row.get('text'))
            labels = {'image': '图片', 'video': '视频', 'audio': '音频'}
            fail(f'RH 任务 {data.get("taskId", "")} 已成功，但未返回可用{labels.get(expected, "文件")}结果')
        if not out_path:
            return selected[0]['url']
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        saved = []
        for i, row in enumerate(selected):
            target = out if i == 0 else out.with_name(f'{out.stem}_{i + 1}{out.suffix}')
            temp = target.with_name(target.name + '.download-' + uuid.uuid4().hex)
            try:
                with urllib.request.urlopen(row['url'], timeout=120) as response, temp.open('wb') as dest:
                    while chunk := response.read(1024 * 1024):
                        dest.write(chunk)
                if not temp.stat().st_size:
                    fail('RH 下载结果为空')
                os.replace(temp, target)
                saved.append(str(target))
            except Exception as exc:
                fail(f'RH 任务 {data.get("taskId", "")} 已成功，下载失败，可重新查询下载：{exc}')
            finally:
                temp.unlink(missing_ok=True)
        return saved[0]

    def generate(self, row, payload, *, expected, out_path, timeout=180, interval=5, max_wait=3600):
        # 清除上一调用状态。生成提交后的错误绝不由生图通用重试层重发。
        self.client.last_request = None
        validate(payload, row['schema'])
        submitted = self.call(row['path'], payload, timeout)
        return self.wait(submitted, interval=interval, max_wait=max_wait, expected=expected, out_path=out_path)


def options(client, kind, extra):
    try:
        configured = configured_parameters(client.cfg, kind)
    except ValueError as exc:
        fail(exc)
    return {**copy.deepcopy(configured), **dict(extra or {})}


def put(payload, p, key, value):
    if not key:
        fail('RH 当前接口不接收该参数，不能静默丢弃')
    payload[key] = converted(value, p[key])


def finish_payload(payload, p, extra):
    for key, value in extra.items():
        if key not in p:
            fail(f'RH 当前接口未声明参数 {key}')
        if key in payload:
            fail(f'RH 附加参数不能覆盖已编译的 {key}')
        payload[key] = converted(value, p[key])
    # 只补明确的控制值，不注入官方示例的剧情、图片、台词或账号内容。
    for key, schema in p.items():
        if key not in payload and 'default' in schema:
            payload[key] = copy.deepcopy(schema['default'])
    return payload


def _is_workflow_model(model):
    """workflow:<id> 走 v2 AI App/工作流直跑（个人 key 可用通道）。"""
    return str(model or '').startswith('workflow:')

def _run_workflow(client, prompt, refs, out_path, model, timeout, interval, max_wait, extra, expected):
    """v2 AI App API（个人 key 通道）：POST /openapi/v2/run/ai-app/{webappId}。

    模型槽填 workflow:<webappId>（AI 应用页面 URL 数字）。nodeInfoList 组装规则（extra 里配）：
    - node_info_list：可选的节点初值；通用请求的提示词与参考素材仍须显式声明映射，覆盖同节点初值；
    - prompt_node {nodeId,fieldName}：提示词注入目标（该 App 的文本输入节点）；
    - image_nodes [{nodeId,fieldName},…]：参考图注入目标列表，本地文件经
      /openapi/v2/media/upload/binary 上传得 download_url 后按序填入；
    - video_nodes / audio_nodes：同上，视频/音频参考；
    - parameter_nodes：请求参数名到 {nodeId,fieldName} 的映射（例如 ratio、size）；
      请求携带的每个参数都必须映射，值原样传入，不推断远端节点类型或自动转换；
    - extra_fields [{nodeId,fieldName,fieldValue},…]：其余任意节点参数（开关/数值/COMBO 选项）；
    - instance_type：default(24G)/plus(48G)/ultra(84G)；use_personal_queue：bool。
    轮询 POST /openapi/v2/query；产物 URL 有效期 24h，落盘即转存。
    """
    app_id = str(model).split(':', 1)[1].strip()
    if not app_id:
        fail('RH 工作流模型缺少 AI 应用 ID')
    opts = dict(extra or {})
    adapter = RunningHubClient(client)

    def target_key(target):
        if (not isinstance(target, dict) or not str(target.get('nodeId') or '').strip()
                or not isinstance(target.get('fieldName'), str) or not target['fieldName'].strip()):
            fail('RH 工作流节点映射必须显式填写 nodeId 和非空 fieldName')
        return str(target['nodeId']), target['fieldName']

    node_info = opts.pop('node_info_list', None) or []
    if not isinstance(node_info, list):
        fail('RH node_info_list 须为节点数组')
    for node in node_info:
        target_key(node)
        if 'fieldValue' not in node:
            fail('RH 工作流节点初值缺少 fieldValue')
    bindings = []
    if prompt:
        bindings.append((opts.pop('prompt_node', None), prompt, False))
    for sources, key in ((refs or [], 'image_nodes'),
            (opts.pop('video_refs', None) or [], 'video_nodes'),
            (opts.pop('audio_refs', None) or [], 'audio_nodes')):
        targets = opts.pop(key, None) or []
        if not isinstance(targets, list) or len(sources) > len(targets):
            fail(f'RH 工作流 {key} 节点映射不足，不能丢弃参考素材')
        bindings.extend((target, source, True) for source, target in zip(sources, targets))
    extra_fields = opts.pop('extra_fields', None) or []
    if not isinstance(extra_fields, list):
        fail('RH extra_fields 须为节点数组')
    for node in extra_fields:
        target_key(node)
        if 'fieldValue' not in node:
            fail('RH 工作流附加节点缺少 fieldValue')
        bindings.append((node, node['fieldValue'], False))
    payload = {'instanceType': opts.pop('instance_type', 'default'),
               'usePersonalQueue': str(opts.pop('use_personal_queue', False)).lower()}
    parameter_nodes = opts.pop('parameter_nodes', None) or {}
    if not isinstance(parameter_nodes, dict):
        fail('RH parameter_nodes 须为参数名到节点映射的 JSON 对象')
    missing_parameters = [str(key) for key in opts if key not in parameter_nodes]
    if missing_parameters:
        fail('RH 工作流缺少参数节点映射：' + '、'.join(sorted(missing_parameters))
             + '；请在该能力的 api_parameters.parameter_nodes 中补齐后再生成')
    bindings.extend((parameter_nodes[key], value, False) for key, value in opts.items())
    keys = [target_key(target) for target, _value, _upload in bindings]
    if len(keys) != len(set(keys)):
        fail('RH 工作流节点映射重复，不能覆盖提示词、参考素材或请求参数')
    # 先完整校验，再上传；失败的映射不得先把部分素材发往远端。
    for (target, value, upload), key in zip(bindings, keys):
        node_info = [node for node in node_info if target_key(node) != key]
        node_info.append({'nodeId': key[0], 'fieldName': key[1],
                          'fieldValue': adapter.upload(value) if upload else value})
    adapter.client.last_request = None
    payload['nodeInfoList'] = node_info
    submitted = adapter.call(f'/openapi/v2/run/ai-app/{app_id}', payload, timeout)
    return adapter.wait(submitted, interval=interval, max_wait=max_wait, expected=expected, out_path=out_path)


def image(client, prompt, refs, out_path, model, timeout, extra, negative, mode):
    mode = str(mode or 'generate').lower()
    kind = 'image_edit' if mode == 'edit' or refs else 'image'
    selected = model or client.models.get(kind) or client.models.get('image')
    if kind == 'image_edit' and not refs:
        fail('RH 改图需要至少一张参考素材')
    if _is_workflow_model(selected):
        slot = 'image' if selected == client.models.get('image') and selected != client.models.get('image_edit') else kind
        opts = options(client, slot, extra)
        interval = float(opts.pop('poll_interval', 5))
        max_wait = float(opts.pop('poll_max', max(timeout, 3600)))
        if negative:
            prompt += '\n禁止：' + str(negative)
        return _run_workflow(client, prompt, refs, out_path, selected, timeout, interval, max_wait, opts, 'image')
    try:
        row = edit_operation(selected) if refs or mode == 'edit' else operation(selected)
    except ValueError as exc:
        fail(exc)
    p = properties(row)
    opts = options(client, kind, extra)
    interval = float(opts.pop('poll_interval', 5))
    max_wait = float(opts.pop('poll_max', max(timeout, 3600)))
    payload = {}
    prompt_key = field(p, 'prompt', 'text_prompt', 'text')
    if not prompt_key:
        fail('RH 该接口需原生参数调用，未声明通用提示词字段')
    if negative:
        nkey = field(p, 'negativePrompt', 'negative_prompt')
        if nkey:
            payload[nkey] = str(negative)
        else:
            prompt += '\n禁止：' + str(negative)
    payload[prompt_key] = prompt
    ratio = opts.pop('ratio', None)
    size = opts.pop('size', None)
    resolution = opts.pop('image_size', None)
    ratio_key = field(p, 'aspectRatio', 'ratio', 'aspect_ratio')
    if ratio and ratio_key:
        put(payload, p, ratio_key, ratio)
    elif (ratio or size) and 'width' in p and 'height' in p:
        matched = re.fullmatch(r'(\d+)x(\d+)', str(size or ''))
        if matched:
            width, height = map(int, matched.groups())
        else:
            a, b = map(int, str(ratio or '1:1').split(':'))
            edge = 2048
            width, height = (edge, edge * b / a) if a >= b else (edge * a / b, edge)
        for key, value in [('width', width), ('height', height)]:
            step = p[key].get('multipleOf', 1)
            payload[key] = int(round(value / step) * step)
        # RH resolution 优先于 width/height，省略默认 resolution 才能保留画幅。
    elif ratio or size:
        fail('RH 当前图片接口不支持所选画幅/尺寸，请改用支持画幅的型号')
    if resolution:
        put(payload, p, field(p, 'resolution'), resolution)
    if 'outputFormat' in p and 'png' in p['outputFormat'].get('enum', []) and str(out_path).lower().endswith('.png'):
        payload['outputFormat'] = 'png'
    for key in ('maxImages', 'imageNum', 'numImages'):
        if key in p and key not in opts:
            payload[key] = 1
    ref_key = image_field(p)
    if refs:
        if not ref_key:
            fail('RH 当前图片接口不接收参考素材')
        payload[ref_key] = list(refs) if p[ref_key].get('type') == 'array' else refs[0]
        if p[ref_key].get('type') != 'array' and len(refs) != 1:
            fail('RH 当前改图接口只接受一张参考素材')
    finish_payload(payload, p, opts)
    if 'width' in payload and 'height' in payload and not resolution and 'resolution' not in opts:
        payload.pop('resolution', None)
    adapter = RunningHubClient(client)
    validate(payload, row['schema'])
    if refs:
        encoded = [adapter.upload(r) for r in refs]
        payload[ref_key] = encoded if p[ref_key].get('type') == 'array' else encoded[0]
    return adapter.generate(row, payload, expected='image', out_path=out_path, timeout=timeout, interval=interval, max_wait=max_wait)


def video(client, prompt, refs, out_path, model, timeout, interval, max_wait, extra, mode, first, last):
    if _is_workflow_model(model):
        return _run_workflow(client, prompt, refs, out_path, model, timeout, interval, max_wait, extra, 'video')
    try:
        row = operation(model)
    except ValueError as exc:
        fail(exc)
    p = properties(row)
    opts = options(client, 'video', extra)
    payload = {field(p, 'prompt', 'text_prompt'): prompt}
    audios = opts.pop('audio_refs', [])
    videos = opts.pop('video_refs', [])
    duration = opts.pop('duration')
    put(payload, p, 'duration', duration)
    resolution = opts.pop('resolution', None)
    if resolution and resolution != 'model-default':
        put(payload, p, field(p, 'resolution'), resolution)
    ratio = opts.pop('ratio', None)
    if ratio and ratio != 'adaptive':
        put(payload, p, field(p, 'ratio', 'aspectRatio', 'aspect_ratio'), ratio)
    elif ratio == 'adaptive' and field(p, 'ratio', 'aspectRatio', 'aspect_ratio'):
        put(payload, p, field(p, 'ratio', 'aspectRatio', 'aspect_ratio'), ratio)
    if 'generate_audio' in opts:
        put(payload, p, field(p, 'generateAudio', 'generateAudioSwitch', 'enableAudio'), opts.pop('generate_audio'))
    fkey = field(p, 'firstFrameUrl', 'firstImageUrl')
    lkey = field(p, 'lastFrameUrl', 'lastImageUrl', 'endImageUrl')
    ikey = image_field(p)
    if mode in ('first_frame', 'first_last'):
        put(payload, p, fkey or ikey, first)
        if last:
            put(payload, p, lkey, last)
    elif refs:
        put(payload, p, ikey, refs)
    if audios:
        put(payload, p, 'audioUrls', audios)
    if videos:
        put(payload, p, 'videoUrls', videos)
    finish_payload(payload, p, opts)
    validate(payload, row['schema'])
    adapter = RunningHubClient(client)
    for key in (fkey, lkey, ikey, 'audioUrls', 'videoUrls'):
        if key and key in payload:
            payload[key] = [adapter.upload(r) for r in payload[key]] if isinstance(payload[key], list) else adapter.upload(payload[key])
    return adapter.generate(row, payload, expected='video', out_path=out_path, timeout=timeout, interval=interval, max_wait=max_wait)


def audio(client, kind, text, out_path, model, timeout, extra):
    try:
        row = operation(model or client.model(kind))
    except ValueError as exc:
        fail(exc)
    p = properties(row)
    opts = options(client, kind, extra)
    interval = float(opts.pop('poll_interval', 5))
    max_wait = float(opts.pop('poll_max', max(timeout, 3600)))
    payload = {field(p, 'text', 'prompt'): text}
    voice = opts.pop('voice', None)
    voice_settings = opts.pop('voice_setting', None)
    if isinstance(voice_settings, dict):
        opts.update(voice_settings)
    if voice:
        put(payload, p, field(p, 'voice_id', 'voiceId'), voice)
    if not voice and kind == 'speech' and 'voice_id' in p:
        configured = (client.cfg.get('extra') or {}).get('voice_id')
        if configured and 'voice_id' not in opts:
            payload['voice_id'] = configured
    finish_payload(payload, p, opts)
    return RunningHubClient(client).generate(row, payload, expected='audio', out_path=out_path, timeout=timeout, interval=interval, max_wait=max_wait)
