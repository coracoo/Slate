# -*- coding: utf-8 -*-
"""厂商目录、能力槽和有界迁移的唯一来源。"""
import copy

KINDS = ('text', 'vision', 'image', 'image_edit', 'video', 'music', 'speech')
ORDER = ('local-comfyui', 'chatgpt-queue', 'doubao', 'doubao-api', 'minimax', 'qwen', 'aliyun',
         'gemini', 'kling', 'agnes', 'openai-compat', 'kimi', 'glm', 'deepseek')
SOURCES = {
    'chatgpt-queue':'https://github.com/leeguooooo/image-use',
    'minimax':'https://platform.minimax.cn/docs/api-reference/video-generation-v2-create',
    'aliyun':'https://help.aliyun.com/zh/model-studio/wan3-video-generation-api-reference',
    'gemini':'https://ai.google.dev/gemini-api/docs/image-generation',
    'kling':'https://www.klingai.com/document-api/api/video/3-0-turbo/image-to-video',
    'agnes':'https://www.agnes-ai.com/zh-Hans/docs/agnes-video-25',
}

def _vendor(vid, label, base, models, endpoints=None, note='', extra=None, enabled=False):
    return dict(id=vid, label=label, base_url=base, api_key='', enabled=enabled,
                models={k:models.get(k,'') for k in KINDS},
                endpoints={k:(endpoints or {}).get(k, '/chat/completions' if k in ('text','vision') and models.get(k) else '') for k in KINDS},
                note=note, extra=extra or {})

DEFAULT_VENDORS = [
    _vendor('local-comfyui','ComfyUI','http://192.168.0.134:8188',
            {'image':'z_image_turbo_int8_convrot.safetensors','image_edit':'qwen_image_edit_2511_int8_convrot.safetensors','video':'minimax_h3_fl2va_pruned_int8_convrot.safetensors'},
            {'image':'/prompt','image_edit':'/prompt','video':'/prompt'},
            '本地或局域网工作流；生图、改图、视频分别配置。',
            {'workflow_path':'','image_edit_workflow_path':''},True),
    _vendor('chatgpt-queue','ChatGPT · chrome-use','',{'image':'chatgpt-web'},
            note='服务由 image-use 提供；浏览器登录 ChatGPT，一次一张、串行导入。',enabled=True),
    _vendor('doubao','火山方舟 · Agent Plan','https://ark.cn-beijing.volces.com/api/plan/v3',
            {'text':'doubao-seed-evolving','vision':'doubao-seed-evolving','image':'doubao-seedream-5.0-pro'},
            {'image':'/images/generations','video':'/contents/generations/tasks'},'Agent Plan 视频模型需按当前套餐填写；Seedance 1.5 Pro 不适用于此接口。'),
    _vendor('doubao-api','火山方舟 · 普通 API','https://ark.cn-beijing.volces.com/api/v3',{},
            {'image':'/images/generations','video':'/contents/generations/tasks'},
            '按量计费；独立填写 API Key 和模型 ID，不继承 Agent Plan 密钥。'),
    _vendor('minimax','MiniMax','https://api.minimax.cn',
            {'text':'MiniMax-M2.7','video':'MiniMax-H3','music':'music-3.0','speech':'speech-2.8-hd'},
            {'text':'/v1/chat/completions','video':'/v2/video_generation','music':'/v1/music_generation','speech':'/v1/t2a_v2'},
            'H3 视频、Music 音乐、Speech 语音使用原生协议；语音须填写 voice_id。',{'voice_id':''}),
    _vendor('qwen','通义','https://dashscope.aliyuncs.com/compatible-mode/v1',
            {'text':'qwen3.6-plus','vision':'qwen3-vl-plus','image':'qwen-image-max'},
            {'image':'/services/aigc/multimodal-generation/generation'},'文本/视觉兼容接口；图片使用百炼原生协议。'),
    _vendor('aliyun','阿里云 · 万相','',{'video':'wan3.0-video'},
            {'video':'/services/aigc/video-generation/video-synthesis'},
            'Base URL 填 https://业务空间ID.cn-beijing.maas.aliyuncs.com/api/v1；模型、地域和密钥必须一致。'),
    _vendor('gemini','Gemini','https://generativelanguage.googleapis.com/v1beta',
            {'text':'gemini-2.5-flash','vision':'gemini-2.5-flash','image':'gemini-3.1-flash-image','image_edit':'gemini-3.1-flash-image'},
            {'text':'/models/{model}:generateContent','vision':'/models/{model}:generateContent','image':'/interactions','image_edit':'/interactions'},
            '原生 generateContent 文本/视觉，Interactions 图片及多图参考；模型可手动填写或拉取。'),
    _vendor('kling','可灵','https://api-beijing.klingai.com',{'video':'kling-v3-0-turbo'},
            {'video':'/image-to-video/kling-3.0-turbo'},'3.0 Turbo 原生接口；API Key 鉴权，图生视频仅支持首帧。'),
    _vendor('agnes','Agnes AI','https://apihub.agnes-ai.com/v1',{'video':'agnes-video-2.5'},
            {'video':'/videos'},'2.5 / 2.5 Flash；参考素材需公网 URL；异步任务按 video_id 查询。'),
    _vendor('openai-compat','OpenAI 兼容','',{},note='自行填写兼容服务地址、密钥和模型；不使用本机 Codex 登录态。'),
    _vendor('kimi','Kimi','https://api.moonshot.cn/v1',{'text':'kimi-k3','vision':'kimi-k3'}),
    _vendor('glm','GLM','https://open.bigmodel.cn/api/paas/v4',
            {'text':'glm-5.2','vision':'glm-4.6v','image':'cogview-4','video':'cogvideox-3'},
            {'image':'/images/generations','video':'/videos/generations'}),
    _vendor('deepseek','DeepSeek','https://api.deepseek.com',{'text':'deepseek-chat'}),
]

def normalize_vendors(rows):
    """只迁移已知旧默认值，用户自定义地址/模型/密钥原样保留。"""
    existing = {v['id']:copy.deepcopy(v) for v in rows if isinstance(v,dict) and v.get('id') != 'local-codex' and v.get('id')}
    result=[]
    for default in DEFAULT_VENDORS:
        vid=default['id']; row=existing.pop(vid,copy.deepcopy(default))
        if not row.get('catalog_revision'):
            if vid=='minimax':
                if row.get('base_url') in ('https://api.minimaxi.com/v1','https://api.minimax.cn/v1'):
                    row['base_url']=default['base_url']
                    if (row.get('endpoints') or {}).get('text') == '/chat/completions': row['endpoints']['text']='/v1/chat/completions'
                if (row.get('models') or {}).get('video')=='hailuo-3': row['models']['video']='MiniMax-H3'
                if (row.get('endpoints') or {}).get('video')=='/videos/generations': row['endpoints']['video']='/v2/video_generation'
                if (row.get('endpoints') or {}).get('music')=='/music/generations': row['endpoints']['music']='/v1/music_generation'
            if vid=='gemini' and row.get('base_url')=='https://generativelanguage.googleapis.com/v1beta/openai':
                row['base_url']=default['base_url']; row['endpoints']=copy.deepcopy(default['endpoints'])
            if vid=='kling':
                if not row.get('base_url'): row['base_url']=default['base_url']
                if (row.get('models') or {}).get('video')=='kling-v1': row['models']['video']=default['models']['video']
                if (row.get('endpoints') or {}).get('video')=='/v1/videos': row['endpoints']['video']=default['endpoints']['video']
            if vid=='qwen' and (row.get('endpoints') or {}).get('image')=='/images/generations':
                row['endpoints']['image']=default['endpoints']['image']
            for field in ('models','endpoints'):
                row.setdefault(field,{})
                for k,value in default[field].items():
                    if not row[field].get(k): row[field][k]=value
            row['catalog_revision']=1
        for field in ('models','endpoints'):
            row.setdefault(field,{})
            for k in KINDS: row[field].setdefault(k,'')
        for k,v in default.items(): row.setdefault(k,copy.deepcopy(v))
        row['label']=default['label']
        if vid=='gemini' and row.get('note')=='走 OpenAI 兼容端点；拉取模型自动切 ?key= 鉴权':
            row['note']=default['note']
        row['documentation_url']=SOURCES.get(vid,'')
        for k,v in default['extra'].items(): row.setdefault('extra',{}).setdefault(k,v)
        result.append(row)
    result.extend(existing.values())
    return result
