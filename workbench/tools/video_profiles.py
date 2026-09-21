# -*- coding: utf-8 -*-
"""视频能力单一真源：按已确认的厂商、型号与模式编译参数，未知型号禁止猜测。"""
import math
import re
from urllib.parse import urlparse

RATIOS = ['16:9', '9:16', '1:1', '4:3', '3:4', '21:9']
MODES = {'reference': '全能参考', 'first_frame': '首帧', 'first_last': '首尾帧', 'last_frame': '尾帧', 'text': '文生视频'}


def capabilities(cfg, model=None):
    name = str(model or (cfg.get('models') or {}).get('video') or '')
    # 私有部署 ID 可显式声明兼容本厂商已接型号，不根据相似名称猜测。
    declared = str((cfg.get('extra') or {}).get('video_profile') or name)
    vid = cfg.get('id'); low = declared.lower()
    p = dict(known=True, model=name, profile_revision='2026-09-21', modes=list(MODES),
             min_duration=4, max_duration=15, max_refs=9, max_audio=0, max_video=0,
             resolutions=['720p'], ratios=RATIOS.copy(), frame_adaptive=False,
             transport='inline', adapter='', endpoint='', integer_duration=True,
             audio_output=False, seed=False)
    if vid == 'local-comfyui' and ('minimax_h3' in low or not low):
        p.update(adapter='comfyui', modes=['reference', 'text'], max_refs=3,
                 resolutions=['workflow'], ratios=['16:9'], integer_duration=False)
    elif vid == 'minimax' and low in ('minimax-h3', 'minimax-h3-max'):
        p.update(adapter='minimax', endpoint='/v2/video_generation', max_audio=3, max_video=3,
                 resolutions=['768P', '2K'], frame_adaptive=True)
        if low.endswith('-max'): p.update(min_duration=5, resolutions=['768P', '480P'])
    elif vid == 'aliyun' and low in ('wan3.0-video', 'wan3.0-video-prime'):
        p.update(adapter='wan3', endpoint='/services/aigc/video-generation/video-synthesis', min_duration=2,
                 max_duration=30, max_refs=10, max_audio=5, max_video=5,
                 resolutions=['720P', '1080P', '480P'], audio_output=True, seed=True)
    elif vid == 'agnes' and low in ('agnes-video-2.5', 'agnes-video-2.5-flash'):
        flash = low.endswith('-flash')
        p.update(adapter='agnes', endpoint='/videos', max_duration=12, max_refs=5 if flash else 8,
                 max_audio=3, max_video=0 if flash else 1, transport='public_url', seed=True,
                 resolutions=['720P'] if flash else ['720P', '1080P', '1K', '2K'])
    elif vid == 'kling' and low in ('kling-v3-0-turbo', 'kling-3.0-turbo'):
        # 当前原生适配器只接该型号的首帧接口，不能把整个可灵家族标为全能参考。
        p.update(adapter='kling3-turbo', endpoint='/image-to-video/kling-3.0-turbo', modes=['first_frame'],
                 min_duration=3, max_refs=1, resolutions=['720p'], ratios=['adaptive'], frame_adaptive=True)
    elif vid in ('doubao', 'doubao-api') and re.fullmatch(r'(?:doubao-)?seedance[- ](?:2[.-]0(?:-(?:fast|mini))?|2[.-]5)(?:-\d{6})?', low):
        v25 = bool(re.search(r'2[.-]5', low))
        lightweight = bool(re.search(r'-(?:fast|mini)(?:-\d{6})?$', low))
        # 各版本分辨率独立；Fast 不能拼成 Flash，也不推测未发布的 2.5 衍生版。
        # 核对：火山 activity/seedance2；Comfy-Org/ComfyUI nodes_bytedance.py。
        resolutions = ['720p','480p']
        if not lightweight: resolutions += ['1080p'] if v25 else ['1080p','4k']
        p.update(adapter='ark', endpoint='/contents/generations/tasks', max_duration=30 if v25 else 15,
                 modes=['reference','first_frame','first_last','text'], max_refs=30 if v25 else 9,
                 max_audio=10 if v25 else 3, max_video=10 if v25 else 3,
                 resolutions=resolutions, frame_adaptive=True, audio_output=True)
    else:
        p.update(known=False, modes=[], max_refs=0, resolutions=[], ratios=[])
    p['default_mode'] = 'reference' if 'reference' in p['modes'] else next(iter(p['modes']), '')
    p['first_frame'] = 'first_frame' in p['modes']
    p['last_frame'] = 'last_frame' in p['modes'] or 'first_last' in p['modes']
    p['mixed_first_refs'] = False
    p['audio_refs'] = p['max_audio'] > 0
    return p


def settings(cfg, options=None, *, model=None):
    """输出规范化参数；不裁剪时长、不换厂商、不丢弃用户参数。"""
    p = capabilities(cfg, model); o = dict(options or {})
    if not p['known']: raise ValueError(f"尚未适配视频型号 {p['model']}，请使用已声明型号；不自动套用相似名称的协议")
    allowed = {'mode','duration','resolution','ratio','seed','generate_audio'}
    if set(o) - allowed: raise ValueError('未声明的视频参数：' + '、'.join(sorted(set(o)-allowed)))
    mode = o.get('mode') or p['default_mode']
    if mode not in p['modes']: raise ValueError('当前型号不支持模式：' + str(mode))
    duration = float(o.get('duration', 5))
    if not math.isfinite(duration) or not p['min_duration'] <= duration <= p['max_duration']:
        raise ValueError(f"当前型号时长须为 {p['min_duration']}–{p['max_duration']} 秒；请修改时长或拆分 V")
    if p['integer_duration'] and not duration.is_integer(): raise ValueError('当前云端视频模型时长必须是整数秒')
    resolution = o.get('resolution') or p['resolutions'][0]
    if resolution not in p['resolutions']: raise ValueError('当前型号不支持分辨率：' + str(resolution))
    ratio = o.get('ratio') or p['ratios'][0]
    if p['frame_adaptive'] and mode in ('first_frame','first_last','last_frame'): ratio = 'adaptive'
    allowed_ratios = p['ratios'] + (['adaptive'] if p['adapter'] in ('ark','wan3','minimax','kling3-turbo') else [])
    if ratio not in allowed_ratios: raise ValueError('不支持的画幅')
    if ratio == 'adaptive' and mode == 'text': raise ValueError('文生视频需要明确画幅')
    out = dict(mode=mode, duration=int(duration) if p['integer_duration'] else duration, resolution=resolution, ratio=ratio)
    if 'generate_audio' in o:
        if not p['audio_output'] or not isinstance(o['generate_audio'],bool): raise ValueError('当前型号未开放生成声音开关')
        out['generate_audio'] = o['generate_audio']
    if 'seed' in o:
        seed = o['seed']
        if not p['seed'] or type(seed) != int or not -1 <= seed <= 2147483647: raise ValueError('种子不在该模型支持范围')
        out['seed'] = seed
    return out


def validate_media(p, mode, refs, first=None, last=None, audio=(), video=()):
    """帧控制与全能参考严格互斥，显式输入不得因模式切换而被悄悄丢弃。"""
    if mode == 'text' and (refs or first or last or audio or video): raise ValueError('文生视频不能携带参考素材')
    if mode == 'reference':
        if first or last: raise ValueError('全能参考不能混入强制首尾帧')
        if not (refs or audio or video): raise ValueError('全能参考至少需要一个素材；无素材请选文生视频')
        if len(refs)>p['max_refs'] or len(audio)>p['max_audio'] or len(video)>p['max_video']:
            raise ValueError(f"参考素材超限（图片 {p['max_refs']} / 音频 {p['max_audio']} / 视频 {p['max_video']}），不会丢弃素材")
    if mode in ('first_frame','first_last','last_frame'):
        if audio or video or any(r not in (first,last) for r in refs): raise ValueError('首尾帧模式不能混入全能参考素材')
        if bool(first) != (mode in ('first_frame','first_last')) or bool(last) != (mode in ('last_frame','first_last')):
            raise ValueError('请为当前模式明确指定首帧/尾帧')


def public_url(value):
    """仅检查 URL 语法和明显的本地地址；公网可达性仍由服务端上传环节保障。"""
    import ipaddress
    u = urlparse(str(value))
    if u.scheme not in ('https','http') or not u.hostname or u.username or u.password: raise ValueError('该模型素材需要公网 HTTP(S) URL，不能直接传本地路径或 Base64')
    if u.hostname.lower() in ('localhost','localhost.localdomain') or u.hostname.endswith('.local'): raise ValueError('素材 URL 不能是本机地址')
    try: address = ipaddress.ip_address(u.hostname)
    except ValueError: address = None
    if address is not None and not address.is_global: raise ValueError('素材 URL 不能是局域网地址')
    return str(value)
