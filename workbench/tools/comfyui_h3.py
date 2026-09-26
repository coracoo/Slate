# -*- coding: utf-8 -*-
"""本地 MiniMax H3 的 ComfyUI API 图。参考图是身份/画风锚点，并非强制首帧。"""
import os
import urllib.parse

from comfyui_client import ComfyUIClient, ComfyUIError

FL_MODEL = 'minimax_h3_fl2va_pruned_int8_convrot.safetensors'
REF_MODEL = 'minimax_h3_ref2va_pruned_int8_convrot.safetensors'
ENCODER = 'qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors'
VIDEO_VAE = 'minimax_h3_video_vae_fp16.safetensors'
AUDIO_VAE = 'minimax_h3_audio_vae_fp32.safetensors'


def build_h3_workflow(prompt, uploaded_refs=(), *, video_refs=(), audio_refs=(), width=672, height=384, seconds=5, seed=1):
    """按 Comfy-Org H3 模板的原生节点连接，生成带同步音频的 MP4。

    参考输入按节点真实容量：ref_images ≤9、ref_videos ≤3、ref_video_audios ≤3
    （COMFY_AUTOGROW_V3 动态槽，来自 192.168.0.134:8188 object_info 实测）。
    """
    refs = list(uploaded_refs)
    videos = list(video_refs); audios = list(audio_refs)
    if len(refs) > 9:
        raise ComfyUIError('MiniMax H3 工作流参考图最多 9 张；请减少 @ref 数量后重试')
    if len(videos) > 3:
        raise ComfyUIError('MiniMax H3 工作流参考视频最多 3 段（2–15s）')
    if len(audios) > 3:
        raise ComfyUIError('MiniMax H3 工作流参考音频最多 3 条')
    if seconds < 4 or seconds > 15:
        raise ComfyUIError('MiniMax H3 时长须为 4–15 秒')
    frames = max(5, round(seconds * 24))
    frames += (5 - frames % 17) % 17
    model = REF_MODEL if refs else FL_MODEL
    text = str(prompt).strip()
    if refs:
        text = '参考图按顺序对应 ' + '、'.join(f'<Picture {i}>' for i in range(1, len(refs)+1)) + '，仅用于主体与画风一致性；具体动作以以下视频提示词为准。\n' + text
    graph = {
        '1': {'class_type':'UNETLoader','inputs':{'unet_name':model,'weight_dtype':'default'}},
        '2': {'class_type':'CLIPLoader','inputs':{'clip_name':ENCODER,'type':'minimax','device':'default'}},
        '3': {'class_type':'VAELoader','inputs':{'vae_name':VIDEO_VAE}},
        '4': {'class_type':'VAELoader','inputs':{'vae_name':AUDIO_VAE}},
        '5': {'class_type':'MiniMaxH3ReferenceToVideo' if refs else 'MiniMaxH3ImageToVideo',
              'inputs':{'clip':['2',0],'prompt':text,'width':width,'height':height,'length':frames,
                        **({'vae':['3',0],'audio_vae':['4',0],'ref_image_size':'match'} if refs else {'vae':['3',0]})}},
        '6': {'class_type':'RandomNoise','inputs':{'noise_seed':seed}},
        '7': {'class_type':'BasicGuider','inputs':{'model':['1',0],'conditioning':['5',0]}},
        '8': {'class_type':'KSamplerSelect','inputs':{'sampler_name':'res_multistep'}},
        '9': {'class_type':'BasicScheduler','inputs':{'model':['1',0],'scheduler':'simple','steps':20,'denoise':1.0}},
        '10': {'class_type':'SamplerCustomAdvanced','inputs':{'noise':['6',0],'guider':['7',0],
              'sampler':['8',0],'sigmas':['9',0],'latent_image':['5',1]}},
        '11': {'class_type':'VAEDecode','inputs':{'samples':['10',0],'vae':['3',0]}},
        '12': {'class_type':'VAEDecodeAudio','inputs':{'samples':['10',0],'vae':['4',0]}},
        '13': {'class_type':'CreateVideo','inputs':{'images':['11',0],'audio':['12',0],'fps':24}},
        '14': {'class_type':'SaveVideo','inputs':{'video':['13',0],
              'filename_prefix':'VideoWorkbench/H3','format':'auto','codec':'auto'}},
    }
    for i, name in enumerate(refs):
        node_id = str(20+i)
        graph[node_id] = {'class_type':'LoadImage','inputs':{'image':name}}
        graph['5']['inputs'][f'ref_images.ref_image_{i}'] = [node_id,0]
    for i, name in enumerate(videos):
        node_id = str(30+i)
        graph[node_id] = {'class_type':'LoadVideo','inputs':{'video':name}}
        graph['5']['inputs'][f'ref_videos.ref_video_{i}'] = [node_id,0]
    for i, name in enumerate(audios):
        node_id = str(40+i)
        graph[node_id] = {'class_type':'LoadAudio','inputs':{'audio':name}}
        graph['5']['inputs'][f'ref_video_audios.ref_video_audio_{i}'] = [node_id,0]
    return graph


def generate_h3(client: ComfyUIClient, prompt, refs, out_path, *, seconds=5, timeout=3600, video_refs=(), audio_refs=()):
    # 帧数感知的轮询预算：H3 本地 326 帧（13.5s）常见 >1h，按 30s/帧兜底并至少 2h；
    # 超时只放弃轮询，ComfyUI 侧任务不会被取消，仍可从 history 回收。
    timeout = max(int(timeout), int(float(seconds) * 24 * 30), 7200)
    uploaded = [client.upload_image(path) for path in refs]
    uploaded_videos = [client.upload_media(path) for path in video_refs]
    uploaded_audios = [client.upload_media(path) for path in audio_refs]
    seed = int.from_bytes(os.urandom(4),'big')
    graph = build_h3_workflow(prompt, uploaded, video_refs=uploaded_videos, audio_refs=uploaded_audios, seconds=seconds, seed=seed)
    client.last_request = {'workflow':graph,'model':REF_MODEL if refs else FL_MODEL,
                           'seed':seed,'reference_count':len(refs),'seconds':seconds}
    return client.run_video_workflow(graph, out_path, timeout=timeout)
