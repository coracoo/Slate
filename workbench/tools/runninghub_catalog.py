# -*- coding: utf-8 -*-
"""RH 官方接口快照与能力解析；不通过相似模型名猜测协议。"""
import json
import re
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def catalog():
    return json.loads(Path(__file__).with_name('runninghub_apis.json').read_text(encoding='utf-8'))


def operation(model):
    name = str(model or '').strip()
    path = name if name.startswith('/') else '/openapi/v2/' + name
    row = next((r for r in catalog()['apis'] if r['path'] == path), None)
    if not row:
        raise ValueError(f'RH 未收录接口 {name}，请使用官方目录中的完整模型路径')
    if row['deprecated']:
        raise ValueError(f'RH 接口已弃用：{row["label"]}')
    return row


def properties(row):
    return row.get('schema', {}).get('properties', {})


def field(props, *names):
    return next((n for n in names if n in props), '')


def image_field(props):
    return field(props, 'imageUrls', 'imageUrl', 'image', 'image_url', 'referenceImages')


def configured_parameters(cfg, kind):
    """读取能力槽参数，原生模型和工作流使用同一份配置。"""
    configured = (cfg.get('extra') or {}).get('api_parameters') or {}
    if isinstance(configured, str):
        try:
            configured = json.loads(configured)
        except ValueError as exc:
            raise ValueError('RH 附加参数不是有效 JSON') from exc
    if not isinstance(configured, dict) or not isinstance(configured.get(kind, {}), dict):
        raise ValueError('RH 附加参数须为 JSON 对象，按能力槽分类')
    return configured.get(kind, {})


def reference_count(row):
    p = properties(row)
    key = image_field(p)
    if key:
        return int(p[key].get('maxItems', 1)) if p[key].get('type') == 'array' else 1
    return int(bool(field(p, 'firstFrameUrl', 'firstImageUrl'))) + int(bool(field(p, 'lastFrameUrl', 'lastImageUrl', 'endImageUrl')))


def model_options(kind=None):
    """制作入口只列可直接编译的生成接口；其余接口通过原生请求入口调用。"""
    result = []
    for row in catalog()['apis']:
        p = properties(row)
        if row['deprecated'] or not row['kind'] or row['content_type'] != 'application/json':
            continue
        if kind and row['kind'] != kind:
            continue
        if not field(p, 'prompt', 'text', 'text_prompt'):
            continue
        if row['kind'] == 'video' and not video_profile(row):
            continue
        result.append({'id': row['id'], 'label': row['label'], 'kind': row['kind'],
                       'reference_limit': reference_count(row), 'documentation_url': row['documentation_url']})
    return result


def edit_operation(model):
    row = operation(model)
    if image_field(properties(row)):
        return row
    family = row['path'].rsplit('/', 1)[0]
    candidates = [r for r in catalog()['apis'] if not r['deprecated'] and r['kind'] == 'image_edit'
                  and r['path'].rsplit('/', 1)[0] == family and image_field(properties(r))]
    if len(candidates) != 1:
        raise ValueError('当前 RH 生图接口不接收参考图，请在改图槽明确选择对应型号')
    return candidates[0]


def video_profile(row):
    """从当前端点的官方 schema 派生 UI 约束；首尾帧与普通参考分别声明。"""
    p = properties(row)
    if row['kind'] != 'video' or 'duration' not in p:
        return None
    d = p['duration']
    durations = []
    try:
        durations = [float(v) for v in d.get('enum', []) if float(v) > 0]
    except (ValueError, TypeError):
        return None
    lo, hi = d.get('minimum'), d.get('maximum')
    if durations:
        lo, hi = min(durations), max(durations)
    if lo is None or hi is None:
        # 不完整 schema 允许明确给定的默认值；禁止虚构连续时长范围。
        default = str(d.get('default', ''))
        if not re.fullmatch(r'\d+(?:\.\d+)?', default):
            return None
        lo = hi = float(default)
    first = field(p, 'firstFrameUrl', 'firstImageUrl')
    last = field(p, 'lastFrameUrl', 'lastImageUrl', 'endImageUrl')
    ikey = image_field(p)
    modes = []
    if first:
        modes.append('first_frame')
        if last:
            modes.append('first_last')
    elif ikey:
        if p[ikey].get('type') == 'array':
            modes.append('reference')
        else:
            modes.append('first_frame')
    # 多模态接口允许零参考，图生接口有图字段则由首帧/参考模式调用。
    if not first and not ikey or 'multimodal-to-video' in row['path']:
        modes.append('text')
    rkey = field(p, 'resolution')
    resolutions = p.get(rkey, {}).get('enum') or ([p[rkey]['default']] if rkey and 'default' in p[rkey] else ['model-default'])
    akey = field(p, 'ratio', 'aspectRatio', 'aspect_ratio')
    if not akey and not first and not ikey:
        # 使用 size 等复合字段的纯文生端点留给原生入口，不能虚构可控画幅。
        return None
    ratios = list(dict.fromkeys(p.get(akey, {}).get('enum') or (['adaptive'] if not akey else ['16:9'])))
    if '16:9' in ratios:
        ratios.remove('16:9')
        ratios.insert(0, '16:9')
    return dict(known=True, model=row['id'], profile_revision=catalog()['revision'], adapter='runninghub',
                endpoint=row['path'], modes=modes, min_duration=lo, max_duration=hi,
                duration_choices=durations, default_duration=float(d.get('default', lo)), max_refs=reference_count(row),
                max_audio=int(p.get('audioUrls', {}).get('maxItems', 1)) if 'audioUrls' in p else 0,
                max_video=int(p.get('videoUrls', {}).get('maxItems', 1)) if 'videoUrls' in p else 0,
                resolutions=list(dict.fromkeys(resolutions)), ratios=ratios, frame_adaptive=not akey,
                transport='runninghub_upload', integer_duration=True,
                audio_output=bool(field(p, 'generateAudio', 'generateAudioSwitch', 'enableAudio')),
                seed='seed' in p)


if __name__ == '__main__':
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(model_options(), ensure_ascii=False, indent=2))
