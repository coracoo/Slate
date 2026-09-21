# -*- coding: utf-8 -*-
"""将 S/V 作者内容编译为一次生成的不可变请求，并在提交前检查能力。"""
import copy
import math
from pathlib import Path
from production_studio import read_board, shot_list, shot_unit, timeline, inside
from production_prompts import source_hash, media_source_hash
from production_media import bound_path, digest
from reference_limits import reference_limit


from video_profiles import capabilities, settings, validate_media, public_url


def compile_request(project, body, cfg):
    board, revision = read_board(project, body['board'])
    if body.get('revision') and body['revision'] != revision: raise ValueError('分镜已被修改，请刷新后重试')
    scope, target = body.get('scope'), str(body.get('target') or '')
    if scope == 'S':
        shot = next((s for s in board.get('shots', []) if s['id'] == target), None)
        if not shot: raise ValueError('转场镜头不存在')
        unit = shot_unit(board, shot)
        if body.get('duration') is not None: unit['duration'] = body['duration']
        shots = [shot]
        prompt = str(shot.get('prompt_' + body['type']) or (shot.get('prompt') if body['type'] == 'image' else '') or '')
        description = str(shot.get('prompt_grid') or '')
        label = target
    elif scope == 'V':
        units = board.get('video_units', [])
        source = next((u for u in units if u['id'] == target), None)
        if not source: raise ValueError('分镜视频不存在')
        if body['type'] != 'video': raise ValueError('关键帧按 S 生成，请先选择转场镜头')
        unit = copy.deepcopy(source)
        shots = shot_list(board, unit)
        if source.get('source_hash') != source_hash(shots): raise ValueError('分镜视频汇总已过期，请更新或确认汇总后再生成')
        if body.get('duration') is not None: unit['duration'] = body['duration']
        prompt = str(unit.get('prompt_video') or '')
        description = str(unit.get('prompt_grid') or '')
        label = f'V{units.index(source)+1:02}'
    else: raise ValueError('必须选择 S 转场镜头或 V 分镜视频')
    kind = body['type']
    if not prompt.strip(): raise ValueError('当前类型的提示词为空，请先由 LLM 补全或手动编辑')
    if kind not in ('image', 'video'): raise ValueError('制作请求只支持关键帧或视频')
    video_options = settings(cfg, {**(body.get('video_options') or {}), 'duration': unit['duration']}) if kind == 'video' else {}
    video_mode = video_options.get('mode', '')
    refs = []
    if kind == 'image':
        from prompt_assembler import resolve_shot_refs
        refs = resolve_shot_refs(shots[0], str(project), actors=board.get('actors'), board_name=body['board'], max_refs=100, board=board)
        from asset_registry import AssetRegistry
        assets = {r.get('path'): r for r in AssetRegistry(str(project)).list() if r.get('path')}
        for r in refs:
            r['sha256'] = digest(inside(project, r['path']))
            asset = assets.get(r['path'], {})
            r['asset_ref'] = asset.get('ref', ''); r['name'] = asset.get('name', '')
    else:
        frame_shots = shots if video_mode == 'reference' else []
        if video_mode in ('first_frame','first_last') and (body.get('continuity') or {}).get('mode') != 'tail_first_frame':
            sid = body.get('first_shot_id') or shots[0]['id']
            frame_shots += [next((s for s in shots if s['id'] == sid), None)]
        if video_mode in ('last_frame','first_last'):
            sid = body.get('last_shot_id') or shots[-1]['id']
            frame_shots += [next((s for s in shots if s['id'] == sid), None)]
        if any(s is None for s in frame_shots): raise ValueError('首尾帧必须来自当前创作范围内的 S')
        for s in frame_shots:
            if not s.get('keyframe'): raise ValueError(f"{s['id']} 尚未采用关键帧")
            r = copy.deepcopy(s['keyframe']); bound_path(project, r)
            if r.get('source_hash') and r['source_hash'] != media_source_hash([s], 'image', shot_unit(board, s)):
                raise ValueError(f"{s['id']} 关键帧来源已过期，请确认画面后重新采用")
            r['purpose'] = f"{s['id']} 的已采用关键帧"; r['shot_id'] = s['id']
            r['frame_role'] = 'reference_image' if video_mode == 'reference' else 'last_frame' if video_mode == 'last_frame' or (video_mode == 'first_last' and len(refs) == (0 if (body.get('continuity') or {}).get('mode') == 'tail_first_frame' else 1)) else 'first_frame'
            if (body.get('image_urls') or {}).get(s['id']): r['public_url'] = public_url(body['image_urls'][s['id']])
            refs.append(r)
        beats = timeline(board, unit)
        if any(not b['prompt'].strip() for b in beats): raise ValueError('成员 S 尚缺视频提示词，请先补全三类提示词')
        prompt += '\n时间轴（秒）：\n' + '\n'.join(f"{b['start']:g}–{b['end']:g}｜{b['shot_id']}：{b['prompt']}" for b in beats)
    negative = str(unit.get('negative') or '')
    # 风格仍由项目/资产 Skill 提供，避免从旧分镜自由文本带入过期画风。
    from skill_lib import image_skill_text
    style = image_skill_text(str(project))
    if style: prompt += '\n画风：' + style
    if kind == 'image':
        prompt += '\n只绘制一张独立关键帧，画幅 16:9。'
    else:
        prompt += f"\n总时长 {float(unit['duration']):g} 秒，画幅 {video_options.get('ratio', '16:9')}。连续视频，保持人物身份与场景空间连续。"
    negs = []
    for s in shots:
        n = s.get('negative') or []
        negs.extend(n if isinstance(n, list) else [n])
    negative = '；'.join(dict.fromkeys([negative, '文字、水印、字幕、边框'] + [str(n) for n in negs if n])).strip('；')
    continuity = body.get('continuity') or {}
    mode = continuity.get('mode', '')
    if mode not in ('', 'tail_context', 'tail_first_frame'): raise ValueError('尾帧模式不合法')
    if mode:
        if kind != 'video': raise ValueError('只有视频可以关联尾帧')
        from production_media import binding
        source = binding(project, continuity.get('item_id'), continuity.get('output_index', 0), 'video')
        if source.get('board') == body['board'] and ((scope == 'V' and source.get('unit_id') == target) or (scope == 'S' and source.get('shot_id') == target)):
            raise ValueError('不能将当前目标的视频引用为自己的前序视频')
        continuity = {'mode': mode, 'source': source, 'vision_vendor': str(continuity.get('vision_vendor') or '')}
        if mode == 'tail_context' and not continuity['vision_vendor']: raise ValueError('尾帧画面参考需要选择 Vision 模型')
    ref_mode = body.get('ref_mode') or 'keyframes'
    if ref_mode not in ('keyframes', 'grid'): raise ValueError('参考模式不合法')
    if kind == 'video' and ref_mode == 'grid' and not description.strip(): raise ValueError('使用故事板宫格前请补全宫格提示词')
    cap = capabilities(cfg)
    if kind == 'video' and ref_mode == 'grid' and video_mode != 'reference': raise ValueError('故事板宫格只用于全能参考')
    voices = []
    if body.get('include_voices'):
        if kind != 'video' or video_mode != 'reference' or not cap['audio_refs']: raise ValueError('当前模式不支持音色参考')
        from voice_assets import video_voices
        voices = video_voices(project, shots)
    from reference_media import resolve
    audio_media = resolve(project, body.get('audio_urls', []), 'audio', cfg) if kind == 'video' else []
    video_media = resolve(project, body.get('video_urls', []), 'video', cfg) if kind == 'video' else []
    audio_urls = [r.get('url', r.get('path')) for r in audio_media]
    video_urls = [r.get('url', r.get('path')) for r in video_media]
    if kind == 'video':
        if mode == 'tail_first_frame' and video_mode not in ('first_frame','first_last'):
            raise ValueError('尾帧强制续接需要选择首帧或首尾帧模式')
        identifiers = list(range(len(refs)))
        first = next((str(i) for i,r in enumerate(refs) if r.get('frame_role') == 'first_frame'), None)
        last = next((str(i) for i,r in enumerate(refs) if r.get('frame_role') == 'last_frame'), None)
        if mode == 'tail_first_frame': first = 'previous_tail'
        check_refs = [str(i) for i in identifiers]
        if ref_mode == 'grid': check_refs = ['grid']
        validate_media(cap, video_mode, check_refs, first, last, voices + audio_urls, video_urls)
        if cap['transport'] == 'public_url':
            if mode == 'tail_first_frame' or ref_mode == 'grid': raise ValueError('Agnes 需要成品帧的公网 URL；请先导出该帧后绑定 URL')
            if any(not r.get('public_url') for r in refs): raise ValueError('Agnes 参考图需要公网 URL，请在参考设置为每张帧填写对应 URL')
        if voices and cfg['id'] in ('agnes','aliyun'): raise ValueError('此接口音频需要公网 URL，请取消本地音色引用，填写参考音频 URL')
    count = 1 if ref_mode == 'grid' and refs else len(refs)
    model = (cfg.get('models') or {}).get(kind)
    image_mode = ''
    if kind == 'image':
        from creation_media import image_route
        _, image_mode, model = image_route(cfg, bool(refs))
    if count > reference_limit(cfg['id'], model, kind, cfg):
        raise ValueError(f'参考图数量 {count} 超过模型上限，请拆分 V 或明确选择宫格；不会丢弃图片')
    return {'scope': scope, 'target': target, 'label': label, 'board': body['board'], 'board_revision': revision,
            'shots': copy.deepcopy(shots), 'unit': unit, 'source_hash': media_source_hash(shots, kind, unit), 'type': kind,
            'prompt': prompt, 'negative': negative, 'refs': refs, 'ref_mode': ref_mode, 'prompt_grid': description,
            'video_options': video_options, 'audio_urls': audio_urls, 'video_urls': video_urls,
            'audio_media': audio_media, 'video_media': video_media,
            'duration': unit.get('duration'), 'continuity': continuity, 'vendor_id': cfg['id'],
            'model': model, 'image_mode': image_mode, 'include_voices': bool(body.get('include_voices')), 'voices': voices}
