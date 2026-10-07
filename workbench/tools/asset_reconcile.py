# -*- coding: utf-8 -*-
"""历史素材复用提案：读取已有内容，差异确认后只补当前空缺。"""
import copy
import hashlib
import json
import re
from pathlib import Path

import project_store
import story_units
import story_planning_versions as planning
import versions
import asset_duplicates
import asset_repository
from asset_matching import name_key, scene_key
from visual_asset_prompt import missing_visual_fields

FILES = (*planning.FILES, '素材/素材图.json')
KINDS = (('character', '人物', 'characters'), ('scene', '场景', 'scenes'), ('prop', '道具', 'props'))


def _revision(project):
    return hashlib.sha256(b''.join((name.encode('utf-8') + (Path(project)/name).read_bytes())
        for name in _files(project) if (Path(project)/name).is_file())).hexdigest()


def _files(project):
    return sorted(set(FILES) | set(asset_duplicates.current_files(project)))


def _legacy_text(text):
    from skill_lib import strip_character_layout
    text = strip_character_layout(text)
    return '\n'.join(line for line in text.splitlines() if not re.match(
        r'\s*(?:禁止|负面|画风|风格|生图风格|图像风格|输出|生成契约|negative|style)\s*[:：]', line, re.I)).strip()


def preview(project):
    revision = _revision(project)
    units = story_units.load_units(project)
    active = story_units.referenced_assets(project)
    index_path = Path(project)/'素材/素材图.json'
    index = json.loads(index_path.read_text('utf-8')) if index_path.exists() else {}
    items, changes, issues = asset_duplicates.proposals(units)
    merging = {ref for change in changes.values() for ref in (
        '@'+change[3]['_merge']['kind']+':'+change[3]['_merge']['old'],
        '@'+change[3]['_merge']['kind']+':'+change[2])}
    episode_ids = [e['id'] for e in units['episodes']]
    for kind, zone, key in KINDS:
        saved = index.get(zone) or {}
        saved = [dict(value, id=ident) for ident, value in saved.items() if isinstance(value, dict)] if isinstance(saved, dict) else saved
        for row in units[key]:
            ref = '@' + kind + ':' + str(row.get('id'))
            if ref in merging:
                continue
            if ref not in active:
                continue
            patch, groups = {}, []
            names = {name_key(v) for v in [row.get('name'), *(row.get('aliases') or [])] if v}
            exact = [v for v in saved if isinstance(v, dict) and v.get('id') == row['id']]
            donors = exact or [v for v in saved if isinstance(v, dict) and name_key(v.get('name')) in names]
            if len(donors) > 1:
                issues.append(f"{row.get('name')}：历史素材匹配有歧义，保留现有引用")
            donor = donors[0] if len(donors) == 1 else {}
            if kind != 'character' and missing_visual_fields(kind, row) and 'visual_description' not in (row.get('locked_fields') or []):
                text = _legacy_text(str(row.get('image_prompt') or donor.get('visual_description') or donor.get('prompt') or donor.get('image_prompt') or ''))
                if text:
                    patch['visual_description'] = text
                    groups.append({'label': '复用历史外观描绘', 'before': '', 'after': text})
                else:
                    issues.append(f"{row.get('name')}：没有可复用的外观文字，请批量补齐素材设定")
            source_path = str(donor.get('path') or '')
            candidate = Path(project)/source_path
            has_current_image = row.get('path') or row.get('image') or any((Path(project)/'素材'/zone/(row['id']+ext)).is_file() for ext in ('.png','.jpg','.jpeg','.webp'))
            if donor and donor.get('id') != row['id'] and source_path and not has_current_image and candidate.is_file() and candidate.resolve().is_relative_to(Path(project).resolve()):
                relative = candidate.resolve().relative_to(Path(project).resolve()).as_posix()
                patch['_reused_image'] = {'path': relative, 'name': row.get('name'), 'reused_from': donor['id']}
                groups.append({'label': '复用已有图片', 'before': '', 'after': relative})
            states = copy.deepcopy(row.get('states') or [])
            for state in states:
                if state.get('output_asset_ref') or 'states' in (row.get('locked_fields') or []):
                    continue
                old = state.get('episodes') or []
                mapped = []
                for value in old:
                    match = scene_key(value, units['scenes'], episode_ids)
                    mapped.append(match['key'] if match else value)
                    if not match:
                        issues.append(f"{row.get('name')} · {state.get('label')}：无法唯一匹配 {value}")
                if mapped != old:
                    state['episodes'] = mapped
                    groups.append({'label': str(state.get('label') or state['id']) + ' · 场景关联',
                                   'before': '、'.join(old), 'after': '、'.join(mapped)})
                    patch['states'] = states
            if patch:
                items.append({'id': ref, 'title': zone + ' · ' + str(row.get('name') or row['id']),
                              'note': '复用已有档案与图片，核对后采用；画风仍使用当前项目设置。', 'groups': groups})
                changes[ref] = (zone, key, row['id'], patch)
    if revision != _revision(project):
        raise project_store.RevisionConflict('读取期间素材或剧情已变化，请重新匹配')
    return {'ok': True, 'revision': revision, 'items': items,
            'issues': list(dict.fromkeys(issues)), '_changes': changes}


def prepare(project, selected, result, names):
    docs, mappings = {}, {}
    for ref in selected:
        if ref not in result['_changes']:
            raise ValueError('选择中包含已失效的匹配项')
        zone, key, ident, patch = result['_changes'][ref]
        if '_merge' in patch:
            mappings.update(asset_duplicates.merge_into(project, docs, zone, key, patch['_merge']))
            continue
        path = Path(project)/'素材'/f'{zone}.json'
        doc = docs.setdefault(path, json.loads(path.read_text('utf-8')))
        row = next(r for r in doc[key] if r.get('id') == ident)
        patch = copy.deepcopy(patch)
        reused = patch.pop('_reused_image', None)
        if reused:
            index_path = Path(project)/'素材/素材图.json'
            index = docs.setdefault(index_path, json.loads(index_path.read_text('utf-8')))
            if not isinstance(index.get(zone), dict):
                raise ValueError('历史图片索引格式不支持写回，请先整理为按 ID 保存的索引')
            index[zone][ident] = reused
        row.update(patch)
    if mappings:
        for name in names:
            path = Path(project)/name
            if path.suffix != '.json' or not path.is_file():
                continue
            doc = docs.get(path)
            if doc is None:
                doc = json.loads(path.read_text('utf-8'))
            updated = asset_duplicates.remap(doc, mappings)
            if updated != doc or path in docs:
                docs[path] = updated
    return docs, mappings


def apply(project, selected, revision):
    if not isinstance(selected, list) or not selected or any(not isinstance(x, str) for x in selected) or len(set(selected)) != len(selected):
        raise ValueError('请选择需要采用的素材匹配项')
    names = _files(project)
    with planning._locks(project, names):
        if names != _files(project):
            raise project_store.RevisionConflict('当前项目文件已变化，请重新匹配')
        result = preview(project)
        if not revision or revision != result['revision']:
            raise project_store.RevisionConflict('素材或剧情已变化，请重新匹配并核对差异')
        if any(ref not in result['_changes'] for ref in selected):
            raise ValueError('选择中包含已失效的匹配项')
        docs, mappings = prepare(project, selected, result, names)
        asset_repository.commit_locked(project, {path.relative_to(Path(project)).as_posix(): doc for path, doc in docs.items()},
            source='migration', allow_removal=True)
    return {'ok': True, 'applied': selected, 'merged_refs': mappings}
