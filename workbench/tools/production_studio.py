# -*- coding: utf-8 -*-
"""S/V 制作数据。作者内容在分镜 JSON 内，产出快照在 creation.json 内。"""
from __future__ import annotations
import copy
import json
import math
import sys
import uuid
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / 'previs_system' / 'tools'
if str(CORE) not in sys.path: sys.path.insert(0, str(CORE))
import project_store
import versions
from production_prompts import FIELDS, normalize_prompts, source_hash, media_source_hash

# ---- 制作默认设定（环境页可改；分组合法性与模型提交能力对齐的口径） ----
SETTINGS_PATH = Path(__file__).resolve().parents[1] / 'studio_settings.json'
DURATION_CHOICES = (8, 15, 30)
SPEECH_RATE = 4.0   # 中文自然语速（字/秒）：时间轴对白下限与判官共用同一常数


def load_settings():
    try: data = json.loads(SETTINGS_PATH.read_text(encoding='utf-8'))
    except Exception: data = {}
    value = data.get('default_video_duration')
    return {'default_video_duration': int(value) if value in DURATION_CHOICES else 15}


def save_settings(patch):
    data = load_settings()
    for key in ('default_video_duration',):
        if key in patch: data[key] = patch[key]
    if data['default_video_duration'] not in DURATION_CHOICES:
        raise ValueError(f'default_video_duration 只能是 {"s/".join(map(str, DURATION_CHOICES))}s')
    SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return data


def duration_cap():
    return load_settings()['default_video_duration']


def inside(project, relative, *, exists=True):
    base = Path(project).resolve()
    path = (base / relative).resolve()
    if not path.is_relative_to(base) or (exists and not path.is_file()):
        raise ValueError(f'项目内文件不存在或路径越界：{relative}')
    return path


def board_path(project, name):
    if Path(name).name != name or not name.endswith('.json'): raise ValueError('分镜文件名不合法')
    return inside(project, '分镜/' + name)


def read_board(project, name):
    return project_store.read_json(board_path(project, name))


def shot_list(board, unit):
    index = {s['id']: s for s in board.get('shots', [])}
    try: return [index[sid] for sid in unit['shot_ids']]
    except KeyError as exc: raise ValueError(f'分镜视频引用了不存在的转场镜头：{exc}') from exc


def shot_unit(board, shot):
    parent = next((u for u in board.get('video_units', []) if shot['id'] in u.get('shot_ids', [])), {})
    return {'id': shot['id'], 'shot_ids': [shot['id']], 'duration': shot.get('video_duration') or shot['dur'],
            'negative': parent.get('negative') or '', 'scene_ref': shot.get('scene_ref'),
            'prompt_grid': shot.get('prompt_grid') or '', 'generation_options': shot.get('generation_options') or {}}


def default_units(board, cap=None):
    """按 同场景连续 + 累积时长 ≤ 默认上限 自动分组；放不下就切下一个 V（N84）。"""
    cap = duration_cap() if cap is None else cap
    units = []
    for shot in board.get('shots', []):
        scene = str(shot.get('scene_ref') or '')
        dur = float(shot.get('dur') or 4)
        if scene and units and units[-1]['scene_ref'] == scene and units[-1]['_acc'] + dur <= cap + 1e-6:
            units[-1]['shot_ids'].append(shot['id'])
        else:
            units.append({'id': 'v-' + uuid.uuid4().hex[:12], 'title': scene.removeprefix('@scene:') or shot['id'],
                          'shot_ids': [shot['id']], 'scene_ref': scene, '_acc': 0.0})
        units[-1]['_acc'] += dur
    for unit in units:
        unit.pop('_acc', None)
        unit['duration'] = sum(float(s.get('dur') or 4) for s in shot_list(board, unit))
        unit['source_hash'] = source_hash(shot_list(board, unit))
    return units


def auto_split_units(board, units, cap=None):
    """超上限的 V 自动拆分：片段继承原汇总提示词等作者内容，source_hash 随成员变化自然标 stale（待按片段重写）。"""
    cap = duration_cap() if cap is None else cap
    out = []
    for unit in units:
        shots = shot_list(board, unit)
        if sum(float(s.get('dur') or 4) for s in shots) <= cap + 1e-6:
            out.append(unit); continue
        current = None; acc = 0.0; first = True; frags = []
        for shot in shots:
            dur = float(shot.get('dur') or 4)
            if current is None or acc + dur > cap + 1e-6:
                current = {k: copy.deepcopy(v) for k, v in unit.items() if k not in ('id', 'shot_ids', 'duration', 'source_hash', 'timeline', 'label', 'stale', 'video_stale', 'judge')}
                current['id'] = unit['id'] if first else 'v-' + uuid.uuid4().hex[:12]
                current['shot_ids'] = []
                frags.append(current); out.append(current); acc = 0.0; first = False
            current['shot_ids'].append(shot['id']); acc += dur
        for frag in frags:
            frag['duration'] = sum(float(s.get('dur') or 4) for s in shot_list(board, frag))
    return out


def validate_units(board, units):
    wanted = [s['id'] for s in board.get('shots', [])]
    actual = [sid for u in units for sid in u.get('shot_ids', [])]
    if actual != wanted: raise ValueError('V 分组必须按原顺序完整覆盖全部 S，不能漏镜、重复或调序')
    seen = set()
    for unit in units:
        if not unit.get('shot_ids'): raise ValueError('分镜视频不能为空')
        if unit.get('id') in seen: raise ValueError('分镜视频 ID 重复')
        seen.add(unit.get('id'))
        shots = shot_list(board, unit)
        scenes = {s.get('scene_ref') for s in shots}
        if len(shots) > 1 and (len(scenes) != 1 or not next(iter(scenes))):
            raise ValueError('一个 V 只能包含相邻且 scene_ref 相同的 S；缺失场景时请先补齐')
        duration = float(unit.get('duration') or sum(float(s.get('dur') or 4) for s in shots))
        if not math.isfinite(duration) or not 0 < duration <= 600: raise ValueError('时长必须在 0~600 秒内')
        cap = duration_cap()
        if duration > cap + 1e-6: raise ValueError(f'V 生成时长 {duration:g}s 超过默认上限 {cap}s（环境页可调 8/15/30s）；请拆分后重写片段提示词')
    return units


def speech_floor(shot):
    """对白按自然语速估算的最小时长（N84）：台词字数 ÷ SPEECH_RATE。"""
    chars = sum(len(str(line.get('line') or line.get('text') or '')) for line in shot.get('lines') or [])
    return chars / SPEECH_RATE if chars else 0.0


def timeline(board, unit):
    """时间轴分摊：对白镜头时长锁定在 max(叙事时长, 语速下限) 不被压缩；纯视觉镜头按比例分摊剩余伸缩量。"""
    shots = shot_list(board, unit)
    durs = [float(s.get('dur') or 4) for s in shots]
    floors = [speech_floor(s) for s in shots]
    total = sum(durs)
    duration = float(unit.get('duration') or total)
    lengths = []
    locked_total = 0.0
    for dur, floor in zip(durs, floors):
        if floor > 0: lengths.append(max(dur, floor)); locked_total += max(dur, floor)
        else: lengths.append(None)
    free_total = sum(d for d, f in zip(durs, floors) if f <= 0)
    budget = max(duration - locked_total, 0.0)
    for i, length in enumerate(lengths):
        if length is None: lengths[i] = budget * durs[i] / free_total if free_total > 0 else 0.0
    at = 0.0; beats = []
    for i, shot in enumerate(shots):
        end = at + lengths[i]
        beats.append({'shot_id': shot['id'], 'start': round(at, 3), 'end': round(end, 3),
                      'prompt': shot.get('prompt_video') or '', 'keyframe': shot.get('keyframe')})
        at = end
    return beats


def judge_unit(board, unit, cap=None):
    """提示词判官 v1（本地确定性，不硬拦截）：台词语速 vs 节拍时长、时长上限、人物/参考图密度（N84）。"""
    cap = duration_cap() if cap is None else cap
    warnings = []
    shots = shot_list(board, unit)
    duration = float(unit.get('duration') or sum(float(s.get('dur') or 4) for s in shots))
    beats = timeline(board, unit)
    if beats and beats[-1]['end'] > duration + 0.05:
        overflow = beats[-1]['end'] - duration
        warnings.append(f'台词按自然语速（{SPEECH_RATE:g} 字/s）需要约 {beats[-1]["end"]:.1f}s，超出设定时长 {duration:g}s 约 {overflow:.1f}s，画面可能展示不完整')
    for beat, shot in zip(beats, shots):
        need = speech_floor(shot); span = beat['end'] - beat['start']
        if need and span + 0.05 < need:
            warnings.append(f"{shot['id']} 台词约需 {need:.1f}s，当前节拍仅 {span:.1f}s")
    if duration > cap + 1e-6:
        warnings.append(f'生成时长 {duration:g}s 超过默认上限 {cap}s，无法提交所选主流模型')
    actors = sorted({a for s in shots for a in (s.get('actor_refs') or [])})
    if len(actors) > 4: warnings.append(f'本 V 含 {len(actors)} 个角色，动作密度高，建议评估是否拆分')
    refs = sum(1 for s in shots if s.get('keyframe'))
    if refs > 6: warnings.append(f'本 V 引用 {refs} 张关键帧参考图，部分模型参考图上限更低，建议拆分')
    return {'ok': not warnings, 'warnings': warnings}


def merge_shots(board, incoming, *, trusted_sources=False):
    index = {s['id']: s for s in board.get('shots', [])}
    merged = []
    for patch in incoming:
        item = copy.deepcopy(index.get(patch['id'], {}))
        item.update(copy.deepcopy(patch))
        # 老编辑器修改 prompt 时等价于修改参考帧；未带新字段时不删除新内容。
        if 'prompt' in patch and 'prompt_image' not in patch:
            item['prompt_image'] = patch['prompt']
        normalize_prompts(item)
        for field in FIELDS:
            if (field in patch or (field == 'prompt_image' and 'prompt' in patch)) and item.get(field) != index.get(patch['id'], {}).get(field) and not trusted_sources:
                item[field + '_source'] = 'authored'
        merged.append(item)
    return merged


def save_shots(project, name, shots, revision=None, *, trusted_sources=False):
    def mutate(board):
        previous = copy.deepcopy(board.get('shots', []))
        board['shots'] = merge_shots(board, shots, trusted_sources=trusted_sources)
        sync_shot_durations(board, previous)
    return project_store.update_json(board_path(project, name), mutate, expected_revision=revision, snapshot=versions.snapshot)


def sync_shot_durations(board, previous):
    """S 时长变化后重算所属 V；其他编辑不覆盖用户单独设置的 V 总时长。"""
    old = {s['id']: s.get('dur') for s in previous}
    changed = set()
    for s in board.get('shots', []):
        dur = float(s.get('dur') or 0)
        if not math.isfinite(dur) or not 0 < dur <= 600: raise ValueError('S 时长必须在 0~600 秒内')
        if old.get(s['id']) != s.get('dur'):
            changed.add(s['id']); s['video_duration'] = dur
    for u in board.get('video_units', []):
        if changed.intersection(u['shot_ids']):
            u['duration'] = sum(float(s['dur']) for s in shot_list(board, u))


def state(project, name):
    board, revision = read_board(project, name)
    for s in board.get('shots', []): normalize_prompts(s)
    units = board.get('video_units') or []
    for i, u in enumerate(units):
        u['label'] = f'V{i+1:02}'
        try:
            u['stale'] = not u.get('prompt_video') or u.get('source_hash') != source_hash(shot_list(board, u))
            u['timeline'] = timeline(board, u)
            u['video_stale'] = bool(u.get('video_binding')) and u['video_binding'].get('source_hash') != media_source_hash(shot_list(board, u), 'video', u)
            u['judge'] = judge_unit(board, u)
        except ValueError:
            u['stale'] = True
            u['timeline'] = []
            u['judge'] = {'ok': True, 'warnings': []}
    return {'board': board, 'revision': revision}


def clean_units(board, units, *, trusted_sources=False):
    units = auto_split_units(board, copy.deepcopy(units))   # 超默认上限的 V 保存时自动拆（继承提示词，成员变→自然 stale 待重写）
    validate_units(board, units)
    previous = {u['id']: u for u in board.get('video_units', [])}
    clean = copy.deepcopy(units)
    for u in clean:
        for field in ('timeline', 'label', 'stale', 'video_stale', 'judge'): u.pop(field, None)
        for field in ('prompt_video', 'prompt_grid', 'negative', 'title'):
            if not trusted_sources and u.get(field) != previous.get(u['id'], {}).get(field): u[field + '_source'] = 'authored'
    return clean


def save_units(project, name, units, revision=None, *, trusted_sources=False):
    def mutate(board):
        board['video_units'] = clean_units(board, units, trusted_sources=trusted_sources)
    return project_store.update_json(board_path(project, name), mutate, expected_revision=revision, snapshot=versions.snapshot)


def retain_production(previous, generated):
    """重新分镜不抹去旧绑定；事实改变的单元显式变旧。"""
    old = {s['id']: s for s in previous.get('shots', [])}
    for s in generated.get('shots', []):
        for key in ('keyframe', 'video_binding', 'video_duration', 'continuity', 'generation_options'):
            if key in old.get(s['id'], {}): s[key] = copy.deepcopy(old[s['id']][key])
    if previous.get('video_units'):
        try:
            validate_units(generated, previous['video_units'])
            generated['video_units'] = copy.deepcopy(previous['video_units'])
        except ValueError:
            generated['production_previous'] = {'video_units': previous['video_units']}
    return generated
