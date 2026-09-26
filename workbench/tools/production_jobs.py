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
from production_prompts import CONTRACT, FIELDS, LLM_FIELDS, source_hash, media_source_hash, require_prompts
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
    members = shot_list(board, unit)
    # 重拍源：默认已采用绑定；source_item_id 指定该 V 的任意候选版本（片段重拍页的版本列表）。
    source_item_id = str(body.get('source_item_id') or '').strip()
    if source_item_id:
        mf = Path(project) / '创作/creation.json'
        src_item = next((i for i in project_store.read_json(mf)[0].get('items', []) if i.get('id') == source_item_id), None)
        if not src_item: raise ValueError('所选重拍源版本不存在，请刷新版本列表')
        if str(src_item.get('unit_id') or '') and src_item.get('unit_id') != target: raise ValueError('所选版本不属于该 V')
        raw = (src_item.get('outputs') or [''])[0]
        rel = raw['path'] if isinstance(raw, dict) else raw
        if not rel: raise ValueError('所选版本没有视频文件')
        if rel.startswith('projects/'): rel = rel.removeprefix('projects/' + Path(project).name + '/')
        src_path = inside(project, rel)
        source = {'item_id': src_item['id'], 'output_index': 0, 'path': rel, 'sha256': digest(src_path)}
    elif unit.get('video_binding'):
        source = dict(unit['video_binding'])
        src_path = bound_path(project, source)   # 已采用素材版本变化在此阻断
    else:
        raise ValueError('该 V 尚未采用视频：请先在版本列表选择一个重拍源')
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
                     'defer': bool(body.get('defer_merge')),
                     'source_output': {'item_id': source.get('item_id', ''), 'output_index': source.get('output_index', 0),
                                       'path': source.get('path', '')}}}


_GRID_LAYOUT_RE = re.compile(r'\d+\s*[×x*]\s*\d+|[0-9一二三四五六七八九十]+\s*宫格')

def _grid_layout(raw):
    """宫格提示词只取"分格布局说明"：含 N×N / N宫格 等布局特征，或非模板开头的手写分格说明。

    旧分镜三件套模板生成的 prompt_grid 是关键帧式单帧描述（【S1（0—4s）：远景…】），
    不能当布局指挥分格——识别后直接忽略，落到默认 3×3 按时间顺序。
    """
    raw = (raw or '').strip()
    if not raw: return ''
    m = _GRID_LAYOUT_RE.search(raw)
    if m:
        dims = re.search(r'(\d+)\s*[×x*]\s*(\d+)', m.group(0))
        if dims and (int(dims.group(1)) <= 1 or int(dims.group(2)) <= 1):
            return ''   # 1×N / N×1 单条不是宫格：回默认九宫格
        return raw
    if re.match(r'^【S\d+镜（', raw): return ''
    return raw

def compile_grid_request(project, body, cfg):
    """静态参考·宫格：一次生图调用，按剧情生成多格故事板图（九宫格/25 宫格等由宫格提示词描述布局）。

    宫格分两级，各自独立：scope=S = 本镜自己的多格动作板（范围小）；
    scope=V = 整段混排（范围大，覆盖全部 S）。
    参考图 = 对应范围解析出的身份/场景锚点（去重后最多 4 张），保证格间人物与场景一致；
    产出登记为图片候选（item.grid=True），供页面预览与视频 ref_mode=grid 复用。
    """
    from prompt_assembler import resolve_shot_refs
    from creation_media import image_route
    board, revision = read_board(project, body['board'])
    if body.get('revision') and body['revision'] != revision: raise ValueError('分镜已被修改，请刷新后重试')
    target = str(body.get('target') or '')
    is_shot = str(body.get('scope') or 'S') == 'S'
    unit = None
    if is_shot:
        shot = next((s for s in board['shots'] if s['id'] == target), None)
        if not shot: raise ValueError('转场镜头不存在')
        members = [shot]
        story = str(shot.get('prompt_video') or shot.get('content') or shot.get('prompt') or '').strip()
        layout = _grid_layout(str(body.get('prompt_grid') or shot.get('prompt_grid') or ''))
        negative_head = str(shot.get('negative') or '')
        label = f'{target}_GRID'
    else:
        unit = next((u for u in board.get('video_units', []) if u['id'] == target), None)
        if not unit: raise ValueError('分镜视频不存在')
        members = shot_list(board, unit)
        story = str(unit.get('prompt_video') or '\n'.join(s.get('prompt_video') or '' for s in members)).strip()
        layout = _grid_layout(str(body.get('prompt_grid') or unit.get('prompt_grid') or ''))
        negative_head = str(unit.get('negative') or '')
        label = f'V{board.get("video_units", []).index(unit)+1:02}_GRID'
    if not story: raise ValueError('宫格缺少剧情内容：请先补全对应的视频提示词')
    # 引用素材完整性：镜头明确引用了、但设定图从未生成的资产 → 阻断并指路（不静默跳过、更不拿旧图顶替）。
    from asset_registry import AssetRegistry
    registry = AssetRegistry(str(project))
    proj_root = Path(project)
    missing, seen_tok = [], set()
    for s in members:
        for token in [s.get('scene_ref'), *(s.get('actor_refs') or []), *(s.get('prop_refs') or [])]:
            token = str(token or '').strip()
            if not token or token in seen_tok: continue
            seen_tok.add(token)
            try:
                rec = registry.resolve(token)
            except Exception:
                continue   # 素材库中无此资产（未提炼）：连生成入口都没有，只能在②素材提炼补
            img = str(rec.get('path') or '')
            if not img or not (proj_root / img).is_file():
                nm = rec.get('name') or rec.get('id') or token
                if nm not in missing: missing.append(nm)
    if missing:
        raise ValueError('引用素材不全：' + '、'.join(missing) + ' 缺少设定图。请在当前 V 的「引用素材」区点「生成」补齐后，再生成宫格图')
    layout_line = f'布局：{layout}。' if layout else '布局：3×3 九宫格，共 9 格（3 列 × 3 行），格子间细白线分隔，按从左到右、从上到下的顺序叙述。'
    prompt = ('生成一张完整的影视分镜故事板宫格图（一张图内含多个格子）。' + layout_line +
              '整图为 16:9 横构图（strictly 16:9 landscape aspect ratio），9 个格子按 3 列 × 3 行均匀铺满整幅画面，不多不少正好 9 格。'
              '把以下剧情按时间顺序分到各格，每格一个关键瞬间并轮换景别（远景/中景/近景/特写交替）；'
              '所有格子中人物外貌、发型、服装、体型完全一致，场景空间与色调统一；格内不要任何文字或字幕。\n剧情：' + story)
    negs = []
    for s in members:
        n = s.get('negative') or []
        negs.extend(n if isinstance(n, list) else [n])
    negative = '；'.join(dict.fromkeys([negative_head, '文字、水印、字幕、边框'] + [str(n) for n in negs if n])).strip('；')
    refs, seen = [], set()
    for s in members:
        for r in resolve_shot_refs(s, str(project), actors=board.get('actors'), board_name=body['board'], max_refs=4, board=board):
            if r.get('path') and r['path'] not in seen:
                seen.add(r['path'])
                refs.append({'path': r['path'], 'sha256': digest(inside(project, r['path'])), 'purpose': r.get('purpose') or '身份/场景锚点'})
    refs = refs[:4]
    _, image_mode, model = image_route(cfg, bool(refs))
    return {'scope': 'S' if is_shot else 'V', 'target': target, 'label': label, 'board': body['board'], 'board_revision': revision,
            'shots': copy.deepcopy(members), 'unit': copy.deepcopy(unit),
            'source_hash': media_source_hash(members, 'image', unit), 'type': 'image',
            'prompt': prompt, 'negative': negative, 'refs': refs, 'ref_mode': 'reference', 'prompt_grid': layout,
            'video_options': {}, 'image_options': {'ratio': '16:9'}, 'audio_urls': [], 'video_urls': [],
            'audio_media': [], 'video_media': [],
            'duration': unit.get('duration') if unit else members[0].get('dur'), 'continuity': {}, 'vendor_id': cfg['id'],
            'model': model, 'image_mode': image_mode, 'include_voices': False, 'voices': [], 'plan_refs': 0}
    # 注意：宫格生成包自己的 ref_mode 用中性 'reference'——若标 'grid'，execute 会把旧宫格/锚点拼图错当本次的参考图


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
    # 失败/出错的旧条目不再占用 nonce：同内容允许重提，落到新条目（否则失败任务永远无法重试）。
    if mf.exists():
        old = next((i for i in project_store.read_json(mf)[0].get('items', [])
                    if i.get('nonce') == nonce and i.get('status') not in ('error', 'failed')), None)
        if old:
            if old.get('request_hash') != request_hash: raise ValueError('同一 nonce 不得提交不同内容')
            return {'ok': True, 'id': old.get('job_id'), 'item_id': old['id'], 'reused': True}
    if action == 'generate':
        if not config: raise ValueError('请选择已启用的厂商')
        packet = compile_request(project, body, config)
    elif action == 'redo_segment':
        if not config: raise ValueError('请选择已启用的厂商')
        packet = compile_redo_request(project, body, config)
    elif action == 'grid':
        if not config: raise ValueError('请选择已启用的厂商')
        packet = compile_grid_request(project, body, config)
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
    elif action in ('voice_catalog', 'voice_sample', 'voice_design', 'voice_clone', 'speech'):
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
    item = {'id': item_id, 'nonce': nonce, 'request_hash': request_hash, 'type': packet['type'], 'action': action,
            'status': 'queued', 'created_at': now(), 'updated_at': now(), 'outputs': [], 'board': packet.get('board', ''),
            'scope': packet.get('scope', ''), 'unit_id': packet.get('target') if packet.get('scope') == 'V' else '',
            'shot_id': packet.get('target') if packet.get('scope') == 'S' else '', 'vendor_id': body.get('vendor_id', ''),
            'prompt': packet.get('prompt', action), 'source_hash': packet.get('source_hash', ''),
            'request': request_file.relative_to(Path(project)).as_posix(), 'duration': packet.get('duration')}
    if action == 'redo_segment':
        # 候选卡片标记「修补 t0–t1s」+ 锚点/来源溯源信息，随 creation.json 透出给前端。
        item['redo'] = {k: packet['redo'][k] for k in ('t0', 't1', 'anchors', 'source_output', 'defer') if k in packet['redo']}
    if action == 'grid':
        item['grid'] = True   # 宫格候选标记：一次生图调用的多格故事板图
    reused = []
    def append(data):
        for existing in data.setdefault('items', []):
            if existing.get('nonce') == nonce and existing.get('status') not in ('error', 'failed'):
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


def adopt_grid(project, body):
    """采用宫格候选：产物经显式人工闸写入 unit.grid_binding，成为整 V 直出的宫格参考。

    这是宫格「产物 → 素材」的唯一通道——绝不隐式抓取最新产出；替换旧参考需再次采用。
    """
    mf = Path(project) / '创作/creation.json'
    item = next((i for i in project_store.read_json(mf)[0].get('items', []) if i.get('id') == body.get('item_id')), None)
    if not item: raise ValueError('宫格候选不存在，请刷新后重试')
    if not item.get('grid'): raise ValueError('该候选不是宫格图')
    raw = (item.get('outputs') or [''])[0]
    rel = raw['path'] if isinstance(raw, dict) else raw
    if not rel: raise ValueError('该宫格候选没有图片')
    if rel.startswith('projects/'): rel = rel.removeprefix('projects/' + Path(project).name + '/')
    p = inside(project, rel)
    board, _ = read_board(project, str(body.get('board') or item.get('board') or ''))
    unit = next((u for u in board.get('video_units', []) if u['id'] == (str(body.get('target') or '') or item.get('unit_id'))), None)
    if not unit: raise ValueError('分镜视频不存在')
    members = shot_list(board, unit)
    binding = {'item_id': item['id'], 'output_index': 0, 'path': rel, 'sha256': digest(p),
               'source_hash': media_source_hash(members, 'image', unit), 'purpose': '故事板宫格参考'}
    def mutate(bd):
        u = next((x for x in bd.get('video_units', []) if x['id'] == unit['id']), None)
        if u: u['grid_binding'] = binding
    project_store.update_json(board_path(project, str(body.get('board') or item.get('board') or '')), mutate,
                              expected_revision=body.get('revision'))
    return {'ok': True, 'note': '宫格已采用为整 V 直出的参考图'}


def adopt(project, body):
    if body.get('type') == 'grid':
        return adopt_grid(project, body)
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
            {'role': 'system', 'content': CONTRACT + '\n' + EDIT_FORMAT + '\n只优化指定字段，返回 JSON {"text":"优化后的完整提示词"}，不要输出其他字段。'
              + ('宫格提示词=一次生图调用出多格故事板的分格说明：第一行写布局——必须是多格宫格形态（默认九宫格 3×3 共 9 格；剧情节奏需要可用 4×4 或 25 宫格 5×5；禁止 1×N 单条横版）；随后按剧情时间顺序写每格关键瞬间（格1…、格2…，轮换景别）；结尾注明：所有格子人物外貌服装完全一致、场景色调统一、格内无文字。它是给生图模型的出图说明，不是单帧画面描述，不要写焦距/光圈式长句。' if packet['field'] == 'prompt_grid' else '')},
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
            value = format_shot_prompt(target, value, start) if packet['field'] != 'prompt_grid' else value
            # 宫格提示词是分格布局说明（3×3；格1…），套单帧模板反而毁语义，原样写回
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
    # 记账归属：⑦ 的生成跑在服务进程线程里，线程局部上下文保证并发项目之间不串台
    from llm_openai import set_billing_project
    set_billing_project(project.name)
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
            # 参考图以编译期为准：素材锚点 / 已采用关键帧 / 已采用的宫格（grid_binding），
            # 产物（宫格候选）必须经显式「采用」进入参考链，这里绝不隐式抓取最新产出。
            refs = copy.deepcopy(packet['refs'])
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
        if action == 'redo_segment' and not packet['redo'].get('defer'):
            # 新片段生成成功即自动拼回：原片[0,t0] + 新片段 + 原片[t1,末]；
            # 拼回基准是入队时的源视频快照，登记候选的是拼回后的完整视频（原始片段留在请求目录备查）。
            # defer_merge=true（片段重拍页）时不拼回：产出即中段片段，等用户在页面上点「一键合并」。
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


def delete_item(project, body):
    """删除「当前目标产出」候选：移除条目、产物文件与请求快照目录。

    守卫：产物已被采用（S 关键帧 / S 或 V 视频）或有重拍版本以它为底片时拒绝删除，
    防止把在用的参考链删断；排队/运行中的任务删除后 worker 结果将无处记录，自行斟酌。
    """
    mf = Path(project) / '创作/creation.json'
    data, _ = project_store.read_json(mf)
    items = data.get('items', [])
    item = next((i for i in items if i.get('id') == body.get('item_id')), None)
    if not item: raise ValueError('候选不存在或已删除')
    ident = item['id']
    board, _ = read_board(project, str(body.get('board') or item.get('board') or ''))
    bound = []
    for s in board.get('shots', []):
        for key, cn in (('keyframe', '关键帧'), ('video_binding', '视频')):
            b = s.get(key)
            if isinstance(b, dict) and b.get('item_id') == ident: bound.append(f"{s['id']} 的{cn}")
    for u in board.get('video_units', []):
        b = u.get('video_binding')
        if isinstance(b, dict) and b.get('item_id') == ident: bound.append(f"{u.get('label') or u['id']} 的已采用视频")
    if bound: raise ValueError('该产物已被采用（' + '、'.join(bound) + '），请先采用其他候选后再删除')
    for i in items:
        r = i.get('redo') or {}
        if i['id'] != ident and (r.get('source_output') or {}).get('item_id') == ident:
            raise ValueError('有重拍版本以该产物为底片，请先删除对应重拍/合并版本')
    removed = 0
    for raw in item.get('outputs') or []:
        rel = raw['path'] if isinstance(raw, dict) else raw
        if not rel: continue
        if rel.startswith('projects/'): rel = rel.removeprefix('projects/' + Path(project).name + '/')
        try:
            inside(project, rel).unlink(missing_ok=True); removed += 1
        except Exception: pass
    folder = Path(project) / '创作' / ident
    if folder.is_dir(): shutil.rmtree(folder, ignore_errors=True)
    def mutate(d):
        d['items'] = [i for i in d.get('items', []) if i.get('id') != ident]
    project_store.update_json(mf, mutate)
    return {'ok': True, 'removed_files': removed, 'note': f'候选已删除（清理 {removed} 个产物文件）'}


def redo_frames(project, item_id):
    """重拍拉片：把指定候选视频按 ≤48 帧抽成帧条，返回 {duration, frames:[{t, path}]}。

    帧图与清单缓存在 素材/修补锚点/frames_<摘要>/，同一视频重复拉片直接复用；
    返回的 path 相对项目根，前端经 /media/projects/<项目>/… 访问。
    """
    mf = Path(project) / '创作/creation.json'
    item = next((i for i in project_store.read_json(mf)[0].get('items', []) if i.get('id') == item_id), None)
    if not item: raise ValueError('候选版本不存在，请刷新版本列表')
    raw = (item.get('outputs') or [''])[0]
    rel = raw['path'] if isinstance(raw, dict) else raw
    if not rel: raise ValueError('该候选版本没有视频文件')
    if rel.startswith('projects/'): rel = rel.removeprefix('projects/' + Path(project).name + '/')
    src = inside(project, rel)
    from production_media import binary, run
    info = probe(src)
    duration = float((info.get('format') or {}).get('duration') or 0)
    if duration <= 0: raise ValueError('无法确定该版本视频的时长')
    folder = Path(project) / '素材/修补锚点' / ('frames_' + hashlib.sha256(f"{rel}:{duration:.2f}".encode()).hexdigest()[:16])
    manifest = folder / 'manifest.json'
    if manifest.is_file():
        cached = json.loads(manifest.read_text(encoding='utf-8'))
        cached['item_id'] = item_id
        return cached
    step = max(0.4, round(duration / 48, 2))
    folder.mkdir(parents=True, exist_ok=True)
    run([binary('ffmpeg'), '-v', 'error', '-i', str(src), '-vf', f'fps=1/{step}', '-q:v', '3',
         '-y', str(folder / 'f%04d.jpg')], 600)
    shots_ = sorted(folder.glob('f*.jpg'))
    if not shots_: raise ValueError('抽帧未产出任何帧图，请检查视频文件')
    frames = [{'t': round((i + 1) * step, 2), 'path': p.relative_to(Path(project)).as_posix()} for i, p in enumerate(shots_)]
    result = {'item_id': item_id, 'duration': round(duration, 2), 'step': step, 'frames': frames}
    manifest.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    return result


def redo_merge(project, body):
    """一键合并：把已完成的重拍片段三段拼回源版本，登记为带版本名的新候选。

    版本名 V<标签>_<首>_<尾>_v<n>.mp4：首/尾为锚点秒数，v<n> 为该 V 第 n 次合并；
    只登记候选不自动采用，人工在版本列表审核。
    """
    from redo_segment import splice_back
    from creation_media import register_output
    mf = Path(project) / '创作/creation.json'
    data, _ = project_store.read_json(mf)
    items = data.get('items', [])
    item = next((i for i in items if i.get('id') == body.get('item_id')), None)
    if not item: raise ValueError('重拍片段不存在，请刷新页面')
    redo = item.get('redo') or {}
    if item.get('action') != 'redo_segment' or not redo: raise ValueError('该产出不是重拍片段')
    if redo.get('merged'): raise ValueError('该片段已合并过，请在版本列表直接使用')
    raw = (item.get('outputs') or [''])[0]
    rel = raw['path'] if isinstance(raw, dict) else raw
    if not rel: raise ValueError('重拍片段没有视频文件，请先完成重拍')
    if rel.startswith('projects/'): rel = rel.removeprefix('projects/' + Path(project).name + '/')
    seg = inside(project, rel)
    src_rel = (redo.get('source_output') or {}).get('path') or (redo.get('source') or {}).get('path')
    if not src_rel: raise ValueError('缺少重拍源视频信息，无法合并')
    if src_rel.startswith('projects/'): src_rel = src_rel.removeprefix('projects/' + Path(project).name + '/')
    src = inside(project, src_rel)
    target = str(body.get('target') or item.get('unit_id') or '')
    n = 1 + sum(1 for i in items if i.get('unit_id') == target and (i.get('redo') or {}).get('merged'))
    label = target
    try:
        board, _ = read_board(project, str(body.get('board') or item.get('board') or ''))
        unit = next((u for u in board.get('video_units', []) if u['id'] == target), None)
        if unit and unit.get('label'): label = str(unit['label'])
    except Exception: pass   # 分镜被移走时合并不应失败：标签退化用单元 id
    out = seg.parent / f"{label}_{redo['t0']:g}_{redo['t1']:g}_v{n}.mp4"
    info = splice_back(src, seg, redo['t0'], redo['t1'], out)
    record = register_output(project, {'id': item['id'] + f'-m{n}', 'type': 'video', 'board': item.get('board', ''),
                                       'shot_id': target, 'vendor_id': item.get('vendor_id', '')}, out)
    rel_out = 'projects/' + Path(project).name + '/' + ((record or {}).get('material_path') or out.relative_to(Path(project)).as_posix())
    new_id = 'prod-' + uuid.uuid4().hex
    def mutate(d):
        for it in d['items']:
            if it.get('id') == item['id'] and it.get('redo'):
                it['redo']['merged'] = True          # 中段条目标记已合并：禁止重复出 v(n+1)
                it['redo']['version'] = f'v{n}'
                it['redo']['version_name'] = out.name
        d.setdefault('items', []).append({
            'id': new_id, 'action': 'redo_segment', 'status': 'done', 'created_at': now(),
            'board': item.get('board', ''), 'unit_id': target, 'type': 'video',
            'outputs': [rel_out], 'actual_duration': (info.get('format') or {}).get('duration'),
            'vendor_id': item.get('vendor_id', ''),
            'redo': {**{k: redo.get(k) for k in ('t0', 't1', 'anchors', 'source_output')},
                     'merged': True, 'version': f'v{n}', 'version_name': out.name, 'from_item': item['id']},
            'note': f"一键合并：{label} {redo['t0']:g}–{redo['t1']:g}s 第 {n} 版（候选，未自动采用）"})
    project_store.update_json(mf, mutate)
    return {'ok': True, 'item_id': new_id, 'version': f'v{n}', 'version_name': out.name,
            'outputs': [rel_out], 'note': f'已合并为 {out.name}，请在版本列表试看后采用'}

if __name__ == '__main__':
    import traceback
    try:
        execute(json.loads(Path(sys.argv[1]).read_text(encoding='utf-8')), sys.argv[2])
    except Exception:
        traceback.print_exc(); sys.exit(1)
