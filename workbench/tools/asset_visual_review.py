# -*- coding: utf-8 -*-
"""人物、场景、道具的视觉设定集中审核与版本保护。"""
import copy
import json
import hashlib
import sys
from pathlib import Path

import character_profiles
import project_store
import story_planning_versions as planning
import story_units
import versions
import asset_repository
import asset_reconcile
import asset_identity_review
from production_state import bump_asset_revision
from skill_lib import read_scope, ensure_explicit_defaults
from visual_asset_prompt import visual_contract
from visual_confirmation import confirm_selection, confirmation_contract

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

META = {'character': ('人物', 'characters'), 'scene': ('场景', 'scenes'), 'prop': ('道具', 'props')}


def current_revision(project):
    ledger = Path(project)/asset_repository.LEDGER
    return hashlib.sha256(asset_reconcile._revision(project).encode() + (ledger.read_bytes() if ledger.is_file() else b'')).hexdigest()


def state(project):
    ensure_explicit_defaults(project)
    revision = current_revision(project)
    with read_scope():
        characters = character_profiles.state(project)
        units = story_units.load_units(project)
        from episode_editor import episode_text
        evidence = {'outline': units['outline'], 'episodes': [{**e, 'text': episode_text(project, units['episodes_doc'], e)} for e in units['episodes']],
                    'foreshadows': units['foreshadows']}
        records = {f'@{kind}:{row["id"]}': row for kind, (_, key) in META.items() for row in units[key]}
        active = story_units.referenced_assets(project)
        others, scenes = [], []
        for kind in ('scene', 'prop'):
            zone, key = META[kind]
            path = Path(project) / '素材' / (zone + '.json')
            doc = json.loads(path.read_text('utf-8')) if path.exists() else {}
            for row in doc.get(key, []):
                ref = '@' + kind + ':' + row['id']
                check = visual_contract(kind, row, project=project)
                others.append({'id': ref, 'name': row.get('name') or row['id'], 'kind': kind,
                    'states': [{**copy.deepcopy(state), 'visual_status': {
                        k: v for k, v in confirmation_contract(project, kind, row, state).items()
                        if k not in ('subject', 'editable_prompt')}} for state in row.get('states') or []],
                    'visual_description': row.get('visual_description') or '', 'in_use': ref in active,
                    'visual_status': {k: v for k, v in check.items() if k not in ('subject', 'editable_prompt')}})
                if kind == 'scene':
                    scenes.append({'ref': ref, 'name': row.get('name') or row['id']})
        reconciliation = asset_reconcile.preview(project)
        identities = asset_identity_review.preview(project)
        if revision != current_revision(project):
            raise project_store.RevisionConflict('读取期间设定已有更新，请重新打开审核')
        return {**characters, 'character_revision': characters['revision'],
            'revision': revision, 'other_assets': others, 'scenes': scenes, 'evidence': evidence, 'records': records,
            'identity_changes': identities, 'reuse_changes': [r for r in reconciliation['items'] if not r['id'].startswith('merge:')],
            'issues': [issue for issue in reconciliation['issues'] if not any(marker in issue for marker in ('相似档案', '设定先后', '人工锁定'))]}


def save(project, items, revision):
    if not isinstance(items, list) or not items or len(items) > 500:
        raise ValueError('请选择 1–500 项修改')
    names = asset_reconcile._files(project)
    for pending in asset_repository._read(Path(project)/asset_repository.LEDGER).get('batches', {}).values():
        names.extend(pending['documents'])
    with planning._locks(project, names):
        if not revision or revision != current_revision(project):
            raise project_store.RevisionConflict('设定或剧情已有更新，请重新打开审核')
        if any(not isinstance(item, dict) or not isinstance(item.get('id'), str) for item in items):
            raise ValueError('审核项必须包含 ID')
        if len({item['id'] for item in items}) != len(items):
            raise ValueError('同一审核项不能重复提交')
        identity_items = [item for item in items if item['id'].startswith('identity-')]
        existing_items = [item for item in items if item['id'].startswith('existing-identity-')]
        reuse_items = [item['id'][6:] for item in items if item['id'].startswith('reuse:')]
        normal_items = [item for item in items if not item['id'].startswith(('identity-', 'existing-identity-', 'reuse:'))]
        staged, resolved = asset_identity_review.prepare(project, identity_items) if identity_items else ({}, [])
        docs = {Path(project)/name: doc for name, doc in staged.items()}
        if existing_items:
            for path, doc in asset_identity_review.prepare_existing(project, existing_items).items():
                docs[path] = asset_repository.merge_changes(asset_repository._read(path), docs[path], doc, path.name) if path in docs else doc
        if reuse_items:
            reused_docs, mappings = asset_reconcile.prepare(project, reuse_items, asset_reconcile.preview(project), names)
            for path, doc in reused_docs.items():
                docs[path] = asset_repository.merge_changes(asset_repository._read(path), docs[path], doc, path.name) if path in docs else doc
            if mappings:
                from asset_duplicates import remap
                docs = {path: remap(doc, mappings) if isinstance(doc, dict) else doc for path, doc in docs.items()}
        seen, validations, targets = {i['id'] for i in items if i not in normal_items}, {}, set()
        confirmations = []
        for item in normal_items:
            if not isinstance(item.get('patch', {}), dict) or not (item.get('patch') or item.get('confirm')):
                raise ValueError('审核项必须包含修改字段或确认范围')
            ident, patch = item['id'], copy.deepcopy(item.get('patch') or {})
            if ident in seen:
                raise ValueError('同一素材不能重复提交')
            seen.add(ident)
            kind, aid = ident[1:].split(':', 1) if ident.startswith('@') and ':' in ident else ('character', ident)
            if kind not in META or kind == 'character' and character_profiles._reserved(aid):
                raise ValueError('请选择已有的视觉素材')
            if (kind, aid) in targets:
                raise ValueError('同一素材不能重复提交')
            targets.add((kind, aid))
            zone, key = META[kind]
            path = Path(project) / '素材' / (zone + '.json')
            if path not in docs:
                docs[path] = json.loads(path.read_text('utf-8')) if path.exists() else {key: []}
            row = next((r for r in docs[path][key] if r.get('id') == aid), None)
            if row is None:
                raise ValueError('素材不存在：' + ident)
            if kind == 'character':
                if set(patch) - {'appearance', 'states'}:
                    raise ValueError('集中视觉审核只修改外观和派生')
                if patch:
                    character_profiles._apply_patch(docs[path], aid, patch)
            else:
                if set(patch) - {'visual_description', 'states'}:
                    raise ValueError('场景和道具只修改视觉描绘和派生状态')
                if 'visual_description' in patch:
                    if not isinstance(patch['visual_description'], str) or len(patch['visual_description']) > 20000:
                        raise ValueError('视觉描绘最多 20000 字')
                    row['visual_description'] = patch['visual_description'].strip()
                    row['locked_fields'] = list(dict.fromkeys([*(row.get('locked_fields') or []), 'visual_description']))
                if 'states' in patch:
                    old_states = {s['id']: s for s in row.get('states') or []}
                    states = patch['states']
                    if not isinstance(states, list) or any(not isinstance(s, dict) for s in states) or len(states) != len(old_states) or {s.get('id') for s in states} != set(old_states):
                        raise ValueError('派生审核不能新增、删除或更换状态 ID')
                    for state in states:
                        old = old_states[state['id']]
                        for field in ('label', 'look_diff'):
                            if not isinstance(state.get(field, ''), str) or len(state.get(field, '')) > 20000:
                                raise ValueError('派生描绘须为文本，最多 20000 字')
                            old[field] = state.get(field, '').strip()
                        if 'episodes' in state:
                            old['episodes'] = asset_identity_review.state_episodes(state)
                        old['locked_fields'] = list(dict.fromkeys([*(old.get('locked_fields') or []), 'label', 'look_diff', 'episodes']))
                    row['states'] = list(old_states.values())
                if patch:
                    bump_asset_revision(row)
            if item.get('confirm') is not None:
                confirmations.extend(confirm_selection(project, kind, row, item['confirm'], ident))
            check = visual_contract(kind, row, project=project)
            warnings = list(check['warnings'])
            for derived in row.get('states') or []:
                if derived.get('output_asset_ref'):
                    continue
                result = visual_contract(kind, row, derived, project=project)
                warnings.extend(str(derived.get('label') or derived['id']) + '：' + w for w in result['warnings'])
            validations[ident] = {'ready': check['ready'] and not warnings,
                'field_labels': check['field_labels'], 'warnings': warnings}
        asset_repository.commit_locked(project, {path.relative_to(Path(project)).as_posix(): doc for path, doc in docs.items()},
            source='manual', resolved=resolved, allow_removal=bool(reuse_items or existing_items))
        return {'ok': True, 'saved': list(seen), 'revision': current_revision(project), 'validations': validations,
                'confirmations': confirmations}
