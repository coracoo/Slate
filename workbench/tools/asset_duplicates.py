# -*- coding: utf-8 -*-
"""重复档案的批量合并提案与当前引用迁移。历史快照和生成请求保持原样。"""
import copy
import json
import re
from pathlib import Path

from asset_matching import identity_names, name_key

KINDS = (('character', '人物', 'characters'), ('scene', '场景', 'scenes'), ('prop', '道具', 'props'))


def current_files(project):
    root = Path(project)
    files = set()
    for folder in ('剧本', '素材', '分镜', '演员', '推演'):
        files.update(p.relative_to(root).as_posix() for p in (root/folder).glob('*.json'))
    # 已生成的任务、诊断和候选是历史证据，不改其中的请求参数。
    return sorted(files)


def _priority(row):
    return (bool(row.get('field_sources')), str(row.get('settings_updated_at') or '') if row.get('field_sources') else '',
            row.get('source') == 'story_units' or row.get('settings_source') == 'story_units')


def _related(a, b, kind):
    refs = {'@'+kind+':'+str(a['id']), '@'+kind+':'+str(b['id'])}
    return any(r.get(k) in refs for r in (a, b) for k in ('parent_ref', 'derived_from'))


def proposals(units):
    items, changes, issues = [], {}, []
    for kind, zone, key in KINDS:
        rows = units[key]
        pairs = []
        for i, a in enumerate(rows):
            for b in rows[i+1:]:
                if a.get('id') == b.get('id') or _related(a, b, kind):
                    continue
                exact = bool(identity_names(a) & identity_names(b))
                short, long = sorted((name_key(a.get('name')), name_key(b.get('name'))), key=len)
                similar = len(short) >= 3 and short in long
                if exact or similar:
                    pairs.append((a, b, exact))
        for a, b, exact in pairs:
            if sum(a in pair[:2] or b in pair[:2] for pair in pairs) > 1:
                issues.append(f"{a.get('name')} / {b.get('name')}：对应多个相似档案，需明确别名后再合并")
                continue
            if _priority(a) == _priority(b):
                issues.append(f"{a.get('name')} / {b.get('name')}：无法确定设定先后，保留两项")
                continue
            old, new = sorted((a, b), key=_priority)
            if any(old.get(f) not in (None, '', [], {}) and old.get(f) != new.get(f)
                   for f in old.get('locked_fields') or []):
                issues.append(f"{old.get('name')} / {new.get('name')}：旧档案含不同的人工锁定设定，请先核对")
                continue
            merged = copy.deepcopy(old)
            merged.update(copy.deepcopy(new))
            # 旧的已组装生图提示词不能跟随新设定继续作为输入。
            for field in ('image_prompt', 'prompt', 'sheet_prompt', 'path', 'image'):
                if field not in new:
                    merged.pop(field, None)
            merged['aliases'] = list(dict.fromkeys([*(new.get('aliases') or []), old['name'], *(old.get('aliases') or []), old['id']]))
            merged['merged_ids'] = list(dict.fromkeys([*(new.get('merged_ids') or []), old['id'], *(old.get('merged_ids') or [])]))
            states = {s['id']: copy.deepcopy(s) for r in (old, new) for s in r.get('states') or [] if s.get('id')}
            if states:
                merged['states'] = list(states.values())
            merged['asset_revision'] = int(new.get('asset_revision') or 0) + 1
            ident = 'merge:' + kind + ':' + old['id'] + ':' + new['id']
            items.append({'id': ident, 'title': f"合并{zone} · {old['name']} → {new['name']}",
                'defaultSelected': exact,
                'note': ('同名或显式别名。' if exact else '名称相近，需核对确为同一实体；不同场地或派生状态请取消勾选。') +
                        '采用当前规划设定；历史图片保留为可复用资源，现有引用统一迁移。',
                'groups': [{'label': '设定冲突：旧档案 → 合并后的当前档案',
                            'before': json.dumps(old, ensure_ascii=False, indent=2),
                            'after': json.dumps(merged, ensure_ascii=False, indent=2)}]})
            changes[ident] = (zone, key, new['id'], {'_merge': {'kind': kind, 'old': old['id'], 'record': merged}})
    return items, changes, issues


def remap(value, mappings, field='', kind=None):
    """只改资产引用与明确的角色 ID 字段，保留磁盘路径及历史文件名。"""
    if field in ('path', 'file', 'filename', 'output', 'aliases', 'merged_ids', 'reused_from'):
        return value
    if isinstance(value, str):
        for old, new in mappings.items():
            value = re.sub(re.escape(old) + r'(?![A-Za-z0-9_-])', lambda _: new, value)
        if kind and field in ('id', 'speaker', 'actor', 'actor_id', 'character_id', 'scene_id', 'prop_id', 'owner'):
            value = mappings.get('@'+kind+':'+value, '@'+kind+':'+value).split(':', 1)[1]
        return value
    if isinstance(value, list):
        result = [remap(v, mappings, field, kind) for v in value]
        return list(dict.fromkeys(result)) if field.endswith('_refs') and all(isinstance(v, str) for v in result) else result
    if isinstance(value, dict):
        if field in ('actors', 'staging', 'pose', 'speakers'):
            result = {}
            for k, v in value.items():
                target = mappings.get('@character:'+k, '@character:'+k).split(':', 1)[1]
                if target in value and target != k:
                    continue
                result[target] = remap(v, mappings, '', 'character')
            return result
        result = {}
        for k, v in value.items():
            child_kind = {'characters': 'character', 'scenes': 'scene', 'props': 'prop',
                          'speaker': 'character', 'actor_id': 'character', 'character_id': 'character',
                          'scene_id': 'scene', 'prop_id': 'prop', 'owner': 'character'}.get(k, kind)
            result[remap(k, mappings)] = remap(v, mappings, k, child_kind)
        return result
    return value


def merge_into(project, docs, zone, key, merge):
    path = Path(project)/'素材'/f'{zone}.json'
    doc = docs.setdefault(path, json.loads(path.read_text('utf-8')))
    old, new = merge['old'], merge['record']['id']
    mappings = {'@'+merge['kind']+':'+old: '@'+merge['kind']+':'+new}
    doc[key] = [copy.deepcopy(merge['record']) if r['id'] == new else r for r in doc[key] if r['id'] != old]
    index_path = Path(project)/'素材/素材图.json'
    if index_path.exists():
        index = docs.setdefault(index_path, json.loads(index_path.read_text('utf-8')))
        rows = index.get(zone) or {}
        if not isinstance(rows, dict):
            raise ValueError('素材图索引须为按 ID 保存的格式，合并尚未写入')
        for source in [k for k in rows if k == old or k.startswith(old+'__')]:
            target = new + source[len(old):]
            donor = rows.pop(source)
            current = rows.get(target) or {}
            combined = {**donor, **current, 'id': target}
            if not current.get('path'):
                combined['path'] = donor.get('path', '')
                combined['reused_from'] = source
            if source == old:
                combined['name'] = merge['record']['name']
            if donor.get('states') or current.get('states'):
                combined['states'] = {**(donor.get('states') or {}), **(current.get('states') or {})}
            rows[target] = combined
            mappings['@'+merge['kind']+':'+source] = '@'+merge['kind']+':'+target
        index[zone] = rows
    return mappings
