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


def default_units(board):
    units = []
    for shot in board.get('shots', []):
        scene = str(shot.get('scene_ref') or '')
        if scene and units and units[-1]['scene_ref'] == scene:
            units[-1]['shot_ids'].append(shot['id'])
        else:
            units.append({'id': 'v-' + uuid.uuid4().hex[:12], 'title': scene.removeprefix('@scene:') or shot['id'],
                          'shot_ids': [shot['id']], 'scene_ref': scene})
    for unit in units:
        unit['duration'] = sum(float(s.get('dur') or 4) for s in shot_list(board, unit))
        unit['source_hash'] = source_hash(shot_list(board, unit))
    return units


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
    return units


def timeline(board, unit):
    shots = shot_list(board, unit)
    total = sum(float(s.get('dur') or 4) for s in shots)
    duration = float(unit.get('duration') or total)
    at = 0.0
    beats = []
    for i, shot in enumerate(shots):
        end = duration if i == len(shots)-1 else at + duration * float(shot.get('dur') or 4) / total
        beats.append({'shot_id': shot['id'], 'start': round(at, 3), 'end': round(end, 3),
                      'prompt': shot.get('prompt_video') or '', 'keyframe': shot.get('keyframe')})
        at = end
    return beats


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
        except ValueError:
            u['stale'] = True
            u['timeline'] = []
    return {'board': board, 'revision': revision}


def clean_units(board, units, *, trusted_sources=False):
    validate_units(board, units)
    previous = {u['id']: u for u in board.get('video_units', [])}
    clean = copy.deepcopy(units)
    for u in clean:
        for field in ('timeline', 'label', 'stale', 'video_stale'): u.pop(field, None)
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
