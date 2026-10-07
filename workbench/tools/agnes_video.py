# -*- coding: utf-8 -*-
"""Agnes 官方异步协议：video_id 与 model_name 共同查询，绝不重建超时任务。"""
import time
import urllib.parse
import urllib.request
from video_profiles import public_url


def generate(c, prompt, refs, out, model, timeout, interval, max_wait, options, mode, first, last):
    from llm_openai import VendorError
    payload = dict(model=model, prompt=prompt, mode='keyframe' if mode in ('first_frame','first_last','last_frame') else mode,
                   seconds=str(options['duration']), size=options['resolution'], aspect_ratio=options['ratio'], n=1)
    if 'seed' in options: payload['seed'] = options['seed']
    if first: payload['first_frame'] = public_url(first)
    if last: payload['last_frame'] = public_url(last)
    if mode == 'reference':
        if refs: payload['images'] = [public_url(r) for r in refs]
        if options.get('audio_refs'): payload['audios'] = [public_url(r) for r in options['audio_refs']]
        if options.get('video_refs'): payload['videos'] = [{'url':public_url(r)} for r in options['video_refs']]
    # 免费队列高峰期常 503 video_queue_full：退避重试 3 次（45s/90s/180s）。已提交成功则不会走到这里，
    # 重试的都是"创建被拒"——没有重复扣费风险（Agnes 免费，也无计费）。
    import time as _time
    waits = (45, 90, 180)
    for attempt in range(len(waits) + 1):
        try:
            data = c._post(c.base + '/videos', payload, timeout)
            break
        except VendorError as exc:
            if 'video_queue_full' not in str(exc) or attempt >= len(waits):
                raise
            print(f'[Agnes 队列满] 第 {attempt + 1} 次被拒，{waits[attempt]}s 后重试（共 3 次）', flush=True)
            _time.sleep(waits[attempt])
    task = data.get('video_id')
    if not task: raise VendorError('Agnes 未返回 video_id，禁止自动重发，请核对厂商记录')
    query = c.base.removesuffix('/v1') + '/agnesapi?' + urllib.parse.urlencode({'video_id':task,'model_name':model})
    c.last_request = dict(provider=c.id, task_id=data.get('task_id') or data.get('id'), video_id=task, query_url=query, model=model)
    if callable(getattr(c,'on_task_submitted',None)): c.on_task_submitted(c.last_request)
    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        if interval: time.sleep(interval)
        result = c._get(query, min(30,max_wait))
        status = result.get('status')
        if status == 'completed':
            url = (result.get('metadata') or {}).get('url')
            if not url: raise VendorError('Agnes 已完成但缺少 metadata.url')
            if out: urllib.request.urlretrieve(url,out); return out
            return url
        if status in ('failed','cancelled','canceled'): raise VendorError(f'Agnes 任务 {task} 失败：{result.get("error") or status}')
    raise VendorError(f'Agnes 轮询超时，已有 video_id={task}，请查询原任务，不要重复提交')
