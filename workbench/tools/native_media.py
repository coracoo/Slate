# -*- coding: utf-8 -*-
"""媒体厂商原生协议。网络由 VendorClient 注入，便于离线契约验证。"""
import base64
import mimetypes
import time
import urllib.parse
import urllib.request
from pathlib import Path

def _error(message):
    from llm_openai import VendorError
    raise VendorError(message)

def checked(data):
    base=data.get('base_resp') or {}
    if base.get('status_code',0) not in (0,'0') or data.get('code',0) not in (0,'0') or data.get('error'):
        _error(str(base.get('status_msg') or data.get('message') or data.get('error'))[:300])
    return data

def _media(c,ref):
    return c._encode_image_ref(ref)

def video(c,prompt,refs,out_path,model,timeout,interval,max_wait,extra,first_frame,last_frame=None):
    opts=dict(extra or {}); model=model or c.model('video')
    duration=int(opts.pop('duration',5)); ratio=opts.pop('ratio','16:9')
    from video_media_input import encode
    from video_profiles import public_url
    audios=opts.pop('audio_refs',[]); videos=opts.pop('video_refs',[])
    role=lambda r: 'first_frame' if first_frame and r==first_frame else 'last_frame' if last_frame and r==last_frame else 'reference_image'
    if c.id=='minimax':
        if not (5 if model=='MiniMax-H3-Max' else 4)<=duration<=15: _error('MiniMax H3 时长不符合模型范围')
        content=[{'type':'text','text':prompt}]
        content.extend({'type':'image_url','image_url':{'url':_media(c,r)},'role':role(r)} for r in refs)
        content.extend({'type':'audio_url','audio_url':{'url':encode(r)},'role':'reference_audio'} for r in audios)
        content.extend({'type':'video_url','video_url':{'url':encode(r)},'role':'reference_video'} for r in videos)
        payload={'model':model,'content':content,'resolution':opts.pop('resolution','768P'),'duration':duration,'ratio':ratio,**opts}
        data=checked(c._post(c._url('video'),payload,timeout)); task=data.get('task_id')
        query=c.base+'/v2/query/video_generation/'+urllib.parse.quote(str(task or ''))
    elif c.id=='aliyun':
        if not c.base or '{' in c.base: _error('请先配置阿里云业务空间与地域 Base URL')
        media=[{'type':role(r),'url':_media(c,r)} for r in refs]
        media.extend({'type':'reference_audio','url':public_url(r)} for r in audios)
        media.extend({'type':'reference_video','url':public_url(r)} for r in videos)
        if 'generate_audio' in opts: opts['audio']=opts.pop('generate_audio')
        if duration!=-1 and not 2<=duration<=30: _error('万相3时长需为2–30秒或-1')
        payload={'model':model,'input':{'prompt':prompt,'media':media},'parameters':{'duration':duration,'resolution':opts.pop('resolution','720P'),'ratio':ratio,**opts}}
        data=checked(c._post(c._url('video'),payload,timeout)); task=(data.get('output') or {}).get('task_id')
        query=c.base+'/tasks/'+urllib.parse.quote(str(task or ''))
    else:
        if len(refs)!=1: _error('可灵 3.0 Turbo 图生视频需要且仅接受一张首帧图')
        if not 3<=duration<=15: _error('可灵 3.0 Turbo 时长需为 3–15 秒')
        payload={'contents':[{'type':'prompt','text':prompt},{'type':'first_frame','url':_media(c,refs[0])}],
                 'settings':{'resolution':opts.pop('resolution','720p'),'duration':duration},'options':opts}
        data=checked(c._post(c._url('video'),payload,timeout)); task=(data.get('data') or {}).get('id')
        query=c.base+'/tasks?'+urllib.parse.urlencode({'task_ids':str(task or '')})
    if not task: _error('创建响应未返回任务 ID；禁止自动重发，请查厂商记录')
    c.last_request={'provider':c.id,'task_id':task,'query_url':query,'model':model,'reference_count':len(refs)}
    if callable(getattr(c, 'on_task_submitted', None)): c.on_task_submitted(c.last_request)
    deadline=time.monotonic()+max_wait
    while time.monotonic()<deadline:
        if interval: time.sleep(interval)
        data=checked(c._get(query,min(30,max_wait)))
        result=data.get('task',{}) if c.id=='minimax' else data.get('output',{}) if c.id=='aliyun' else next((x for x in data.get('data',[]) if str(x.get('id'))==str(task)),{})
        status=str(result.get('status') or result.get('task_status') or '').lower()
        if status in ('failed','cancelled','canceled','unknown'): _error(f'{c.id} 任务 {task} 失败：{result.get("message") or result.get("error_message") or status}')
        if status=='succeeded':
            url=(result.get('content') or {}).get('url') if c.id=='minimax' else result.get('video_url') if c.id=='aliyun' else next((x.get('url') for x in result.get('outputs',[]) if x.get('type')=='video'),None)
            if not url: _error('任务成功但未返回视频地址')
            if out_path: urllib.request.urlretrieve(url,out_path); return out_path
            return url
    _error(f'轮询超时，保留任务 ID {task}；请查询已有任务，勿重新生成')

def audio(c,kind,text,out_path,model=None,timeout=300,extra=None):
    if c.id!='minimax': _error('当前音乐/语音原生适配器仅支持 MiniMax')
    opts=dict(extra or {})
    payload={'model':model or c.model(kind),**opts,'stream':False,'output_format':'hex'}
    if kind=='speech':
        payload['text']=text
        voice=payload.get('voice_setting') or {'voice_id':(c.cfg.get('extra') or {}).get('voice_id','')}
        if not voice.get('voice_id'): _error('MiniMax 语音需配置 voice_id')
        payload['voice_setting']=voice
    else:
        payload['prompt']=text
        payload.setdefault('is_instrumental',not bool(payload.get('lyrics')))
    payload.setdefault('audio_setting',{'format':'mp3','sample_rate':32000 if kind=='speech' else 44100,'bitrate':128000 if kind=='speech' else 256000})
    data=checked(c._post(c._url(kind),payload,timeout))
    raw=(data.get('data') or {}).get('audio')
    if not raw: _error('音频响应为空')
    try: content=bytes.fromhex(raw)
    except ValueError: _error('音频响应不是约定的 hex 数据')
    if not content: _error('音频文件为空')
    Path(out_path).write_bytes(content)
    return out_path

def _gemini_part(c,part):
    if part.get('type')=='text': return {'text':part.get('text','')}
    url=(part.get('image_url') or {}).get('url','')
    if not url: _error('Gemini 不支持该消息片段')
    data=_media(c,url)
    if not data.startswith('data:'):
        with urllib.request.urlopen(data,timeout=30) as r:
            return {'inlineData':{'mimeType':r.headers.get_content_type(),'data':base64.b64encode(r.read()).decode()}}
    head,raw=data.split(',',1)
    return {'inlineData':{'mimeType':head[5:].split(';')[0],'data':raw}}

def gemini_chat(c,messages,model,kind,max_tokens,temperature,timeout,extra):
    content=[]; system=[]
    for msg in messages:
        raw=msg.get('content',''); parts=[{'text':raw}] if isinstance(raw,str) else [_gemini_part(c,p) for p in raw]
        if msg.get('role')=='system': system.extend(parts)
        else: content.append({'role':'model' if msg.get('role')=='assistant' else 'user','parts':parts})
    payload={'contents':content,'generationConfig':{'maxOutputTokens':max_tokens,'temperature':temperature}}
    if system: payload['systemInstruction']={'parts':system}
    if extra: payload.update(extra)
    name=str(model or c.model(kind)).removeprefix('models/')
    data=checked(c._post(c.base+'/models/'+urllib.parse.quote(name,safe='')+':generateContent',payload,timeout))
    parts=((data.get('candidates') or [{}])[0].get('content') or {}).get('parts',[])
    text=''.join(p.get('text','') for p in parts if not p.get('thought'))
    if not text: _error('Gemini 未返回文本，请检查安全拦截或输出预算')
    return text

def gemini_image(c,prompt,out_path,refs,model,timeout,extra,negative):
    # 使用当前官方图片文档的 Interactions 接口；附件以真实 base64 像素传入。
    parts=[]
    for ref in refs:
        p=_gemini_part(c,{'type':'image_url','image_url':{'url':ref}})['inlineData']
        parts.append({'type':'image','data':p['data'],'mime_type':p['mimeType']})
    parts.append({'type':'text','text':prompt+('\n不要出现：'+negative if negative else '')})
    opts=extra or {}
    payload={'model':model or c.model('image'),'input':parts,'response_format':{'type':'image','aspect_ratio':opts.get('ratio','16:9')}}
    if opts.get('image_size'): payload['response_format']['image_size']=opts['image_size']
    data=checked(c._post(c.base+'/interactions',payload,timeout))
    outputs=[block for step in data.get('steps',[]) if step.get('type')=='model_output' for block in step.get('content',[])]
    outputs=outputs or data.get('outputs') or data.get('output') or []
    if isinstance(outputs,dict): outputs=[outputs]
    images=[p for p in outputs if p.get('type')=='image' and p.get('data')]
    if len(images)!=1: _error(f'Gemini 返回 {len(images)} 张图片，单图任务拒绝自动选择')
    Path(out_path).write_bytes(base64.b64decode(images[0]['data'],validate=True))
    return out_path

def qwen_image(c,prompt,out_path,refs,model,timeout,extra,negative):
    model=model or c.model('image')
    if refs and not any(x in model for x in ('edit','2.0')): _error('此通义模型未适配参考图，请选择 Qwen Image Edit 或 2.0 模型')
    content=[{'image':_media(c,r)} for r in refs]+[{'text':prompt}]
    opts=extra or {}; ratio=opts.get('ratio','16:9')
    sizes={'16:9':'1664*928','9:16':'928*1664','1:1':'1328*1328','4:3':'1472*1104','3:4':'1104*1472'}
    requested=opts.get('size','')
    size=requested if requested in sizes.values() else sizes.get(ratio,'1664*928')
    payload={'model':model,'input':{'messages':[{'role':'user','content':content}]},'parameters':{'n':1,'size':size,'negative_prompt':negative or '', 'watermark':False}}
    base=c.base.replace('/compatible-mode/v1','/api/v1')
    data=checked(c._post(base+c.endpoint('image'),payload,timeout))
    items=(((data.get('output') or {}).get('choices') or [{}])[0].get('message') or {}).get('content',[])
    images=[p['image'] for p in items if p.get('image')]
    if len(images)!=1: _error('通义未返回单张图片')
    urllib.request.urlretrieve(images[0],out_path)
    return out_path
