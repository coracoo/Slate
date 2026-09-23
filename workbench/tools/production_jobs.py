# -*- coding: utf-8 -*-
"""三层制作后台任务：先保存请求再执行，重复 nonce 复用同一条记录。"""
from __future__ import annotations
import copy
import datetime
import hashlib
import json
import os
import re
import shutil
import sys
import uuid
from pathlib import Path
from production_studio import project_store, board_path, read_board, save_units, save_shots, validate_units, shot_list, shot_unit, inside, duration_cap
from production_prompts import CONTRACT, LLM_FIELDS, source_hash, media_source_hash, require_prompts
from production_media import bound_path, binding, digest, tail_frame, make_grid, concatenate, probe

try: sys.stdout.reconfigure(encoding='utf-8')
except Exception: pass


def now(): return datetime.datetime.now().isoformat(timespec='seconds')


def config_fingerprint(cfg):
    # 仅保存单向摘要；账号/端点/模型在排队后变化时阻断，绝不换到另一计费通道。
    return hashlib.sha256(json.dumps(cfg, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def compile_redo_request(project, body, cfg):
    """局部修补请求：抽已采用 V 视频的 t0/t1 锚点帧，走首尾帧模式生成中间段，成功后自动拼回原片。

    不经过 compile_request——修补的参考帧来自已采用视频本身（锚点），不是 S 的已采用关键帧；
    产出只登记为该 V 的新候选，绝不自动采用（人工审核流程不变）。
    """
    from redo_segment import extract_anchor_frames, validate_window, video_duration
    from video_profiles import capabilities, settings, validate_media
    board, revision = read_board(project, body['board'])
    if body.get('revision') and body['revision'] != revision: raise ValueError('分镜已被修改，请刷新后重试')
    units = board.get('video_units', [])
    target = str(body.get('target') or '')
    unit = next((u for u in units if u['id'] == target), None)
    if not unit: raise ValueError('分镜视频不存在')
    if not unit.get('video_binding'): raise ValueError('该 V 尚未采用视频，无法局部修补')
    members = shot_list(board, unit)
    source = dict(unit['video_binding'])
    src_path = bound_path(project, source)   # 已采用素材版本变化在此阻断
    try:
        t0_raw, t1_raw = float(body.get('t0')), float(body.get('t1'))
    except (TypeError, ValueError):
        raise ValueError('请提供数值形式的修补起止时间 t0/t1（秒）')
    t0, t1 = validate_window(video_duration(src_path), t0_raw, t1_raw)
    # 片段时长=t1-t0：过短/非整数秒等模型能力限制由 settings 原样抛出，交给用户调整窗口。
    video_options = settings(cfg, {**(body.get('video_options') or {}), 'mode': 'first_last', 'duration': t1 - t0})
    cap = capabilities(cfg)
    if cap['transport'] == 'public_url': raise ValueError('当前视频适配器需要成品帧公网 URL，局部修补暂不支持该厂商')
    # 锚点帧按 源视频摘要+窗口 缓存复用；入队快照仍会复制进请求目录，缓存丢失不影响已排队请求。
    folder = Path(project) / '素材/修补锚点' / f"{source['sha256'][:16]}_{t0:g}_{t1:g}"
    frames = extract_anchor_frames(src_path, t0, t1, folder)
    refs = []
    for role, key, label in (('first_frame', 'head', '首'), ('last_frame', 'tail', '尾')):
        path = frames[key]
        ts = t0 if role == 'first_frame' else t1
        refs.append({'path': path.relative_to(Path(project)).as_posix(), 'sha256': digest(path),
                     'frame_role': role, 'purpose': f'修补{label}锚点（原片 {ts:g}s 帧）', 'name': f'修补锚点·{label}',
                     'asset_ref': '', 'shot_id': ''})
    validate_media(cap, 'first_last', ['0', '1'], '0', '1', [], [])
    prompt = str(body.get('prompt') or unit.get('prompt_video') or '').strip()
    if not prompt: raise ValueError('修补提示词为空，请填写或先补全该 V 的视频提示词')
    from skill_lib import image_skill_text
    style = image_skill_text(str(project))
    if style: prompt += '\n画风：' + style
    prompt += (f'\n总时长 {t1 - t0:g} 秒。首尾帧已锁定为原片 {t0:g}s 与 {t1:g}s 的画面，'
               f'只生成两帧之间自然衔接的动作，保持人物身份与场景空间连续。')
    negs = []
    for s in members:
        n = s.get('negative') or []
        negs.extend(n if isinstance(n, list) else [n])
    negative = '；'.join(dict.fromkeys([str(unit.get('negative') or ''), '文字、水印、字幕、边框'] + [str(n) for n in negs if n])).strip('；')
    label = f'V{units.index(unit)+1:02}_REDO'
    return {'scope': 'V', 'target': target, 'label': label, 'board': body['board'], 'board_revision': revision,
            'shots': copy.deepcopy(members), 'unit': copy.deepcopy(unit),
            'source_hash': media_source_hash(members, 'video', unit), 'type': 'video',
            'prompt': prompt, 'negative': negative, 'refs': refs, 'ref_mode': 'keyframes', 'prompt_grid': '',
            'video_options': video_options, 'audio_urls': [], 'video_urls': [],
            'duration': t1 - t0, 'continuity': {}, 'vendor_id': cfg['id'],
            'model': (cfg.get('models') or {}).get('video'), 'include_voices': False, 'voices': [],
            'redo': {'t0': t0, 't1': t1,
                     'anchors': {'head': refs[0]['path'], 'tail': refs[1]['path']},
                     'source': source,
                     'source_output': {'item_id': source.get('item_id', ''), 'output_index': source.get('output_index', 0),
                                       'path': source.get('path', '')}}}


def enqueue(project, body, spawn, providers):
    from production_requests import compile_request
    from llm_openai import load_vendors
    action = body.get('action', 'generate')
    nonce = str(body.get('nonce') or '')
    if not re.fullmatch(r'[\w-]{12,100}', nonce): raise ValueError('请求需要稳定的 nonce，重试时请使用同一值')
    clean = {k: v for k, v in body.items() if k not in ('project', 'nonce')}
    request_hash = hashlib.sha256(json.dumps(clean, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    mf = Path(project) / '创作/creation.json'
    cfgs = load_vendors(providers)
    config = next((v for v in cfgs if v.get('id') == body.get('vendor_id') and v.get('enabled')), None)
    # 重复请求先查，不因源文件后来变更而创建第二条。
    if mf.exists():
        old = next((i for i in project_store.read_json(mf)[0].get('items', []) if i.get('nonce') == nonce), None)
        if old:
            if old.get('request_hash') != request_hash: raise ValueError('同一 nonce 不得提交不同内容')
            return {'ok': True, 'id': old.get('job_id'), 'item_id': old['id'], 'reused': True}
    if action == 'generate':
        if not config: raise ValueError('请选择已启用的厂商')
        packet = compile_request(project, body, config)
    elif action == 'redo_segment':
        if not config: raise ValueError('请选择已启用的厂商')
        packet = compile_redo_request(project, body, config)
    elif action in ('prompts', 'group', 'optimize'):
        if not config or not (config.get('models') or {}).get('text'): raise ValueError('请选择已启用的文字模型')
        board, revision = read_board(project, body['board'])
        packet = {'board': body['board'], 'snapshot': board, 'board_revision': revision, 'vendor_id': config['id'], 'type': 'production'}
        if action == 'optimize':
            scope = body.get('scope'); field = body.get('field')
            allowed = FIELDS if scope == 'S' else ('prompt_video', 'prompt_grid') if scope == 'V' else ()
            if field not in allowed: raise ValueError('提示词类型不支持优化')
            rows = board['shots'] if scope == 'S' else board.get('video_units', [])
            target = next((r for r in rows if r['id'] == body.get('target')), None)
            if not target: raise ValueError('优化目标不存在')
            packet.update(scope=scope, target=target['id'], field=field, current_text=str(target.get(field) or ''))
    elif action == 'concat':
        board, revision = read_board(project, body['board'])
        units = board.get('video_units') or []
        validate_units(board, units)
        refs = []
        for u in units:
            if not u.get('video_binding'): raise ValueError('请先采用每个 V 的视频')
            ref = u['video_binding']; bound_path(project, ref)
            if ref.get('source_hash') != media_source_hash(shot_list(board, u), 'video', u): raise ValueError('存在已过期的 V 视频，请重新确认采用')
            refs.append(copy.deepcopy(ref))
        packet = {'board': body['board'], 'refs': refs, 'scope': 'E', 'type': 'video', 'label': 'EPISODE', 'board_revision': revision,
                  'quality': str(body.get('quality') or 'master')}
    elif action in ('voice_catalog', 'voice_sample', 'voice_design', 'speech'):
        from voice_assets import prepare
        packet = prepare(project, body, config)
    else: raise ValueError('未知制作任务')
    if config: packet['config_fingerprint'] = config_fingerprint(config)
    if (packet.get('continuity') or {}).get('mode') == 'tail_context':
        vision = next((v for v in cfgs if v.get('id') == packet['continuity']['vision_vendor'] and v.get('enabled') and v.get('models', {}).get('vision')), None)
        if not vision: raise ValueError('请选择已启用的 Vision 模型')
        packet['continuity']['config_fingerprint'] = config_fingerprint(vision)
    item_id = 'prod-' + uuid.uuid4().hex
    packet.update(action=action, item_id=item_id, project=str(Path(project).resolve()))
    folder = Path(project) / '创作' / item_id
    folder.mkdir(parents=True, exist_ok=True)
    # 原图复制成请求快照，源素材覆盖不会影响排队请求。
    for i, ref in enumerate(packet.get('refs') or []):
        src = bound_path(project, ref)
        dest = folder / f'ref_{i+1}{src.suffix}'
        shutil.copyfile(src, dest)
        if digest(dest) != ref['sha256']: raise ValueError('复制参考素材期间源文件发生变化，请重新提交')
        ref['original_path'] = ref['path']; ref['path'] = dest.relative_to(Path(project)).as_posix()
    for media_kind in ('audio', 'video'):
        for i, ref in enumerate(packet.get(media_kind + '_media') or []):
            if not ref.get('path'): continue
            src = inside(project, ref['path'])
            dest = folder / f'{media_kind}_ref_{i+1}{src.suffix}'
            shutil.copyfile(src, dest)
            if digest(dest) != ref['sha256']: raise ValueError('复制参考音视频期间源文件发生变化')
            ref['original_path'] = ref['path']
            ref['path'] = dest.relative_to(Path(project)).as_posix()
    for i, voice in enumerate(packet.get('voices') or []):
        src = inside(project, voice['sample'])
        if digest(src) != voice['sha256']: raise ValueError('音色样本发生变化，请重新提交')
        dest = folder / f'voice_{i+1}{src.suffix}'
        shutil.copyfile(src, dest)
        if digest(dest) != voice['sha256']: raise ValueError('复制音色期间源文件发生变化，请重新提交')
        voice['original_sample'] = voice['sample']; voice['sample'] = dest.relative_to(Path(project)).as_posix()
    if (packet.get('continuity') or {}).get('source'):
        source = packet['continuity']['source']; src = bound_path(project, source)
        dest = folder / ('previous_video' + src.suffix); shutil.copyfile(src, dest)
        if digest(dest) != source['sha256']: raise ValueError('复制前序视频期间源文件发生变化，请重新提交')
        source['original_path'] = source['path']; source['path'] = dest.relative_to(Path(project)).as_posix()
    if (packet.get('redo') or {}).get('source'):
        # 修补源视频同样快照进请求目录：排队期间重新采用/覆盖不影响本次拼回基准。
        source = packet['redo']['source']; src = bound_path(project, source)
        dest = folder / ('redo_source' + src.suffix); shutil.copyfile(src, dest)
        if digest(dest) != source['sha256']: raise ValueError('复制修补源视频期间源文件发生变化，请重新提交')
        source['original_path'] = source['path']; source['path'] = dest.relative_to(Path(project)).as_posix()
    request_file = folder / 'request.json'
    request_file.write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding='utf-8')
    item = {'id': item_id, 'nonce': nonce, 'request_hash': request_hash, 'type': packet['type'],
            'status': 'queued', 'created_at': now(), 'updated_at': now(), 'outputs': [], 'board': packet.get('board', ''),
            'scope': packet.get('scope', ''), 'unit_id': packet.get('target') if packet.get('scope') == 'V' else '',
            'shot_id': packet.get('target') if packet.get('scope') == 'S' else '', 'vendor_id': body.get('vendor_id', ''),
            'prompt': packet.get('prompt', action), 'source_hash': packet.get('source_hash', ''),
            'request': request_file.relative_to(Path(project)).as_posix(), 'duration': packet.get('duration')}
    if action == 'redo_segment':
        # 候选卡片标记「修补 t0–t1s」+ 锚点/来源溯源信息，随 creation.json 透出给前端。
        item['redo'] = {k: packet['redo'][k] for k in ('t0', 't1', 'anchors', 'source_output')}
    reused = []
    def append(data):
        for existing in data.setdefault('items', []):
            if existing.get('nonce') == nonce:
                if existing.get('request_hash') != request_hash: raise ValueError('nonce 内容冲突')
                reused.append(existing); return
        data['items'].append(item)
    project_store.update_json(mf, append, create_default={'items': []})
    if reused: return {'ok': True, 'id': reused[0].get('job_id'), 'item_id': reused[0]['id'], 'reused': True}
    from create_media import update_item
    try:
        jid = spawn('production', [sys.executable, str(Path(__file__).resolve()), str(request_file), str(providers)])
        update_item(str(mf), item_id, job_id=jid)
    except Exception as exc:
        update_item(str(mf), item_id, status='error', note='执行器启动结果待核查：' + str(exc))
        raise RuntimeError('请求已记录，请接管原请求并查看启动日志：' + str(exc)) from exc
    return {'ok': True, 'id': jid, 'item_id': item_id}


def adopt(project, body):
    ref = binding(project, body['item_id'], body.get('output_index', 0), body['type'])
    if ref['board'] != body['board']: raise ValueError('不能采用另一集的产出')
    def mutate(board):
        if body['scope'] == 'S':
            target = next((s for s in board['shots'] if s['id'] == body['target']), None)
            if not target or ref.get('shot_id') != target['id']: raise ValueError('产出与转场镜头不匹配')
            members = [target]
        else:
            target = next((u for u in board.get('video_units', []) if u['id'] == body['target']), None)
            if not target or ref.get('unit_id') != target['id']: raise ValueError('产出与分镜视频不匹配')
            members = shot_list(board, target)
        target_unit = target if body['scope'] == 'V' else shot_unit(board, target)
        ref['generated_source_hash'] = ref.get('source_hash', '')
        ref['source_hash'] = media_source_hash(members, body['type'], target_unit)
        target['keyframe' if body['type'] == 'image' else 'video_binding'] = ref
    project_store.update_json(board_path(project, body['board']), mutate, expected_revision=body.get('revision'))
    return {'ok': True}


def llm_task(project, packet, client):
    from creation_pipeline import parse_json
    snapshot = packet['snapshot']
    if packet['action'] == 'optimize':
        from production_prompts import EDIT_FORMAT, format_shot_prompt
        rows = snapshot['shots'] if packet['scope'] == 'S' else snapshot.get('video_units', [])
        target = next(r for r in rows if r['id'] == packet['target'])
        members = [target] if packet['scope'] == 'S' else shot_list(snapshot, target)
        response = client.chat([
            {'role': 'system', 'content': CONTRACT + '\n' + EDIT_FORMAT + '\n只优化指定字段，返回 JSON {"text":"优化后的完整提示词"}，不要输出其他字段。'},
            {'role': 'user', 'content': json.dumps({'field': packet['field'], 'current_text': packet['current_text'], 'shots': members}, ensure_ascii=False)}
        ], kind='text', max_tokens=12000, timeout=720, extra={'thinking': {'type': 'disabled'}})
        value = str(parse_json(response).get('text') or '').strip()
        if not value: raise ValueError('模型返回空提示词，原文已保留')
        if packet['scope'] == 'S':
            parent = next((u for u in snapshot.get('video_units', []) if target['id'] in u['shot_ids']), None)
            start = 0
            if parent:
                for s in shot_list(snapshot, parent):
                    if s['id'] == target['id']: break
                    start += float(s['dur'])
            value = format_shot_prompt(target, value, start)
        def mutate(board):
            targets = board['shots'] if packet['scope'] == 'S' else board['video_units']
            row = next(r for r in targets if r['id'] == packet['target'])
            row[packet['field']] = value
            row[packet['field'] + '_source'] = 'llm'
            if packet['field'] == 'prompt_image': row['prompt'] = value
        project_store.update_json(board_path(project, packet['board']), mutate,
                                  expected_revision=packet['board_revision'],
                                  snapshot=__import__('versions').snapshot)
        return
    if packet['action'] == 'prompts':
        instruction = '只补写 shots 的 prompt_image 与 prompt_video 两类提示词，镜号、镜头顺序和所有事实不得改变。返回 shots 数组（id 与这两个 prompt 字段）。宫格文案是人工配置字段，不要生成。'
    else:
        cap = duration_cap()
        instruction = (f'只输出 video_units 分组及 prompt_video/negative/title，不重写 shots，也不要生成宫格文案。'
                      f'分组目标时长：每个 V 的成员 dur 之和不超过 {cap} 秒——同场景连续优先合并，'
                      f'同一段剧情尽量放在一起，时长放不下就切到下一个 V；超上限的分组保存时会被自动拆分。')
    response = client.chat([{'role': 'system', 'content': CONTRACT + '\n' + instruction},
                            {'role': 'user', 'content': json.dumps(snapshot, ensure_ascii=False)}], kind='text', max_tokens=24000, timeout=720,
                           extra={'thinking': {'type': 'disabled'}})
    parsed = parse_json(response)
    if packet['action'] == 'prompts':
        rows = parsed.get('shots') or []
        if [r.get('id') for r in rows] != [s['id'] for s in snapshot['shots']]: raise ValueError('LLM 补全改变了镜号或数量，已拒绝写回')
        require_prompts(rows)
        cleaned = []
        for old, row in zip(snapshot['shots'], rows):
            updated = copy.deepcopy(old)
            for field in LLM_FIELDS:
                if old.get(field + '_source') != 'authored':
                    updated[field] = str(row[field]); updated[field + '_source'] = 'llm'
            # prompt_grid 是按需人工配置字段：LLM 刷新不生成、不覆盖、不清空
            cleaned.append(updated)
        save_shots(project, packet['board'], cleaned, packet['board_revision'], trusted_sources=True)
    else:
        units = parsed.get('video_units') or []
        old = {tuple(u['shot_ids']): u for u in snapshot.get('video_units', [])}
        # 人工编辑的 V 不允许在模型重新分组时悄悄消失。
        proposed = {tuple(u.get('shot_ids') or []) for u in units}
        if any(ids not in proposed and any(u.get(f + '_source') == 'authored' for f in ('prompt_video', 'prompt_grid', 'negative', 'title')) for ids, u in old.items()):
            raise ValueError('LLM 改变了含人工内容的 V 分组，已保留原编排；请手动拆分/合并后再整合')
        for u in units:
            previous = old.get(tuple(u.get('shot_ids') or []), {})
            u['id'] = previous.get('id') or 'v-' + uuid.uuid4().hex[:12]
            members = shot_list(snapshot, u)
            if not u.get('prompt_video'): raise ValueError('V 缺少视频提示词')
            u['duration'] = previous.get('duration') or sum(float(s['dur']) for s in members)
            u['scene_ref'] = members[0].get('scene_ref', '')
            u['source_hash'] = source_hash(members)
            for field in ('prompt_video', 'prompt_grid', 'negative', 'title'):
                if previous.get(field + '_source') == 'authored': u[field] = previous.get(field, '')
                u[field + '_source'] = previous.get(field + '_source') if previous.get(field + '_source') == 'authored' else 'llm'
            if previous.get('generation_options'): u['generation_options'] = previous['generation_options']
            if previous.get('video_binding'): u['video_binding'] = previous['video_binding']
        save_units(project, packet['board'], units, packet['board_revision'], trusted_sources=True)


def execute(packet, providers):
    from llm_openai import VendorClient
    from create_media import update_item
    project = Path(packet['project']); mf = project / '创作/creation.json'; ident = packet['item_id']
    def update(**fields): update_item(str(mf), ident, **fields)
    action = packet['action']; folder = project / '创作' / ident
    # 已启动过的 worker 不得再次提交云端请求。
    claimed = []
    def claim(data):
        item = next(i for i in data['items'] if i['id'] == ident)
        if item.get('execution_started'): raise ValueError('该请求已启动过，禁止重复执行；请查已有任务日志')
        item.update(execution_started=now(), status='running'); claimed.append(True)
    project_store.update_json(mf, claim)
    client = None
    media_done = False
    try:
        if packet.get('vendor_id'):
            client = VendorClient(packet['vendor_id'], providers)
            if packet.get('config_fingerprint') and config_fingerprint(client.cfg) != packet['config_fingerprint']:
                raise ValueError('排队期间厂商配置发生变化，未调用模型；请检查后重新提交')
            client.on_task_submitted = lambda data: update(provider_task=data)
        if action in ('group', 'prompts', 'optimize'):
            llm_task(project, packet, client); update(status='done', note='分镜内容已保存'); return
        if action.startswith('voice_') or action == 'speech':
            from voice_assets import execute_voice
            result = execute_voice(project, packet, client, folder)
            update(status='done', **result); return
        if action == 'concat':
            out = folder / f"{Path(packet['board']).stem}_EPISODE_VIDEO.mp4"
            # 交付出口默认母版（按片段推导规格+响度归一）；quality='proxy' 才走 720p 预览代理
            info = concatenate(project, packet['refs'], out, spec='proxy' if packet.get('quality') == 'proxy' else 'master')
        else:
            refs = copy.deepcopy(packet['refs'])
            if packet['ref_mode'] == 'grid': refs = [make_grid(project, refs, packet['prompt_grid'])]
            prompt = packet['prompt']; first_frame = None; last_frame = None
            continuity = packet.get('continuity') or {}
            if continuity.get('mode'):
                meta = tail_frame(project, continuity['source'])
                update(continuity=meta)
                if continuity['mode'] == 'tail_context':
                    vision = VendorClient(continuity['vision_vendor'], providers)
                    if config_fingerprint(vision.cfg) != continuity.get('config_fingerprint'): raise ValueError('Vision 配置已变化，未调用模型')
                    description = vision.chat([{'role': 'user', 'content': [
                        {'type': 'text', 'text': '描述这张视频尾帧的角色位置、姿态、视线、构图和光线，作为下一段视频的连续性约束。不要虚构画外动作。'},
                        vision.image_part(str(inside(project, meta['path'])))]}], kind='vision', max_tokens=1500, timeout=180)
                    prompt += '\n前段结束画面（连续性参考）：' + description
                    update(continuity={**meta, 'vision_description': description})
                else:
                    first_frame = str(inside(project, meta['path']))
                    refs.insert(0, dict(meta, purpose='前序视频真实尾帧，作为本段首帧', frame_role='first_frame'))
            paths = [str(bound_path(project, r)) for r in refs]
            from video_profiles import capabilities
            if packet['type'] == 'video':
                if capabilities(client.cfg)['transport'] == 'public_url': paths = [r['public_url'] for r in refs]
                first_frame = next((p for p,r in zip(paths,refs) if r.get('frame_role') == 'first_frame'), first_frame)
                last_frame = next((p for p,r in zip(paths,refs) if r.get('frame_role') == 'last_frame'), None)
            prompt += '\n参考图片映射：\n' + '\n'.join(f"图片{i+1}：{r.get('asset_ref', '')} {r.get('name', '')} {r.get('purpose', r.get('shot_id', '参考画面'))}" for i, r in enumerate(refs))
            if packet['negative']: prompt += '\n禁止：' + packet['negative']
            extra = packet.get('video_options') or {'duration': packet['duration'], 'ratio': '16:9'}
            extra = {**extra, 'audio_refs': list(packet.get('audio_urls') or []), 'video_refs': list(packet.get('video_urls') or [])}
            for media_kind in ('audio', 'video'):
                if media_kind + '_media' not in packet: continue
                media_refs = []
                for ref in packet[media_kind + '_media']:
                    if ref.get('url'): media_refs.append(ref['url']); continue
                    path = inside(project, ref['path'])
                    if digest(path) != ref['sha256']: raise ValueError('参考音视频请求快照发生变化')
                    from media_gateway import load as gateway_config, signed_url
                    media_refs.append(signed_url(project, ref['path']) if gateway_config().get('base_url') else str(path))
                extra[media_kind + '_refs'] = media_refs
            if packet.get('include_voices'):
                from production_requests import capabilities
                if not capabilities(client.cfg)['audio_refs']: raise ValueError('当前视频模型不支持音色参考，请在音色页单独生成台词配音')
                voices = packet.get('voices') or []
                for v in voices:
                    if digest(inside(project, v['sample'])) != v['sha256']: raise ValueError('音色请求副本发生变化')
                extra['audio_refs'] += [str(inside(project, v['sample'])) for v in voices]
                prompt += '\n音色映射：' + '；'.join(f"音频{i+1}对应 @character:{v['character_id']}" for i, v in enumerate(voices))
                update(voice_bindings=voices)
            ext = '.png' if packet['type'] == 'image' else '.mp4'
            out = folder / f"{Path(packet['board']).stem}_{packet['label']}_{'KF_MAIN' if ext == '.png' else 'VIDEO'}{ext}"
            update(submission_intent_at=now(), actual_prompt=prompt, actual_refs=refs)
            if packet['type'] == 'image':
                if client.id == 'chatgpt-queue':
                    from image_use_runtime import generate_image
                    generate_image(prompt, str(out), refs=paths, timeout=1800)
                else:
                    # 与旧 create_media 入口同源：画幅走结构化 size/ratio 字段，
                    # 不能只靠提示词里的「画幅 16:9」——云端图像模型对中文画幅词服从度低。
                    # 缺省读制作规格（E05 brief.aspect_ratio，无 brief=16:9），兼容旧排队请求。
                    from brief import aspect_ratio_of
                    from create_media import image_size_for_aspect, image_ratio_for_aspect
                    ratio = str((packet.get('image_options') or {}).get('ratio') or aspect_ratio_of(project))
                    client.generate_image(prompt, out_path=str(out), image_refs=paths, mode=packet['image_mode'], model=packet['model'], timeout=1800,
                                          extra={"size": image_size_for_aspect(ratio), "ratio": image_ratio_for_aspect(ratio)})
                from PIL import Image
                with Image.open(out) as generated: generated.verify()
                info = {}
            else:
                client.generate_video(prompt, image_refs=paths, out_path=str(out), model=packet['model'],
                                      poll_max=7200, extra=extra, first_frame=first_frame, last_frame=last_frame)
                info = probe(out)
        if action == 'redo_segment':
            # 新片段生成成功即自动拼回：原片[0,t0] + 新片段 + 原片[t1,末]；
            # 拼回基准是入队时的源视频快照，登记候选的是拼回后的完整视频（原始片段留在请求目录备查）。
            from redo_segment import splice_back
            redo = packet['redo']
            snapshot = inside(project, redo['source']['path'])
            if digest(snapshot) != redo['source']['sha256']: raise ValueError('修补源视频快照发生变化，请重新提交')
            spliced = folder / f"{Path(packet['board']).stem}_{packet['label']}_SPLICED.mp4"
            splice_back(snapshot, out, redo['t0'], redo['t1'], spliced)
            out = spliced
            info = probe(out)
        if not out.is_file() or not out.stat().st_size: raise ValueError('生成未返回有效媒体文件')
        relative = out.relative_to(project).as_posix()
        update(status='done', outputs=['projects/' + project.name + '/' + relative],
               actual_duration=(info.get('format') or {}).get('duration'))
        media_done = True
        from creation_media import register_output
        record = register_output(project, {'id': ident, 'type': packet['type'], 'board': packet.get('board', ''),
                                          'shot_id': packet.get('target', ''), 'vendor_id': packet.get('vendor_id', '')}, out)
        if record: update(**record)
        # 空槽且源未变才自动采用；重跑只产生候选。
        if action == 'generate':
            board, rev = read_board(project, packet['board'])
            target = next((s for s in (board['shots'] if packet['scope'] == 'S' else board.get('video_units', [])) if s['id'] == packet['target']), None)
            if target:
                members = [target] if packet['scope'] == 'S' else shot_list(board, target)
                field = 'keyframe' if packet['type'] == 'image' else 'video_binding'
                target_unit = target if packet['scope'] == 'V' else shot_unit(board, target)
                if not target.get(field) and media_source_hash(members, packet['type'], target_unit) == packet['source_hash']:
                    adopt(project, {**packet, 'item_id': ident, 'revision': rev})
    except Exception as exc:
        if media_done:
            update(archive_error=str(exc)[:2000])
            print('媒体已完成；归档/自动采用待处理：' + str(exc)); return
        update(status='error', note=str(exc)[:2000], provider_task=getattr(client, 'last_request', None) if client else None)
        raise


if __name__ == '__main__':
    import traceback
    try:
        execute(json.loads(Path(sys.argv[1]).read_text(encoding='utf-8')), sys.argv[2])
    except Exception:
        traceback.print_exc(); sys.exit(1)
