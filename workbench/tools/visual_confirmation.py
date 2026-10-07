# -*- coding: utf-8 -*-
"""视觉设定审核记录；确认不改变外观、图片来源或现有媒体。"""
import hashlib
import json
from datetime import datetime, timezone

def review_hash(contract, state=None):
    payload = {'source_hash': contract['source_hash'], 'state': {k: state.get(k) for k in
               ('id', 'look_diff', 'sheet_prompt', 'image_prompt', 'episodes', 'output_asset_ref')} if state else None}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def review_status(contract, row, state=None):
    target = state if state is not None else row
    saved = target.get('visual_review') or {}
    status = 'unconfirmed'
    if saved.get('source_hash'):
        status = 'confirmed' if contract['ready'] and saved['source_hash'] == review_hash(contract, state) else 'changed'
    return {'review_status': status, 'reviewed_at': saved.get('reviewed_at')}


def confirmation_contract(project, kind, row, state=None):
    from visual_asset_prompt import visual_contract
    contract = visual_contract(kind, row, state, project=project)
    if state and state.get('output_asset_ref'):
        # 复用母图无需生成独立外观，仍核对母图和派生场景关联。
        parent = visual_contract(kind, row, project=project)
        candidate = {k: v for k, v in state.items() if k != 'output_asset_ref'}
        association = visual_contract(kind, row, candidate, project=project)
        issues = [w for w in association['warnings'] if '匹配键' in w]
        if state['output_asset_ref'] != '@'+kind+':'+row['id']:
            issues.append('该状态指向其他视觉素材，请先核对对应关系')
        contract = {**parent, 'ready': parent['ready'] and not issues,
                    'warnings': [*parent['warnings'], *issues]}
    return {**contract, **review_status(contract, row, state)}


def confirm_selection(project, kind, row, selection, owner):
    if not isinstance(selection, dict) or set(selection) - {'base', 'states'}:
        raise ValueError('确认范围必须包含母素材选择和派生 ID')
    base, ids = selection.get('base', False), selection.get('states', [])
    if type(base) is not bool or not isinstance(ids, list) or any(not isinstance(s, str) for s in ids) or len(ids) != len(set(ids)) or not (base or ids):
        raise ValueError('请选择有效的母素材或派生确认范围')
    states = {s['id']: s for s in row.get('states') or []}
    if set(ids) - set(states):
        raise ValueError('派生状态不存在，请重新载入设定')
    results = []
    for sid in ([None] if base else []) + ids:
        state = states.get(sid)
        contract = confirmation_contract(project, kind, row, state)
        target = state if state is not None else row
        digest = review_hash(contract, state)
        if contract['ready'] and target.get('visual_review', {}).get('source_hash') != digest:
            target['visual_review'] = {'source_hash': digest, 'reviewed_at': datetime.now(timezone.utc).isoformat()}
            fields = (['look_diff', 'episodes', 'output_asset_ref'] if state is not None else
                      ['appearance.'+k for k in row.get('appearance', {}) if k not in ('sources', 'proposals')] if kind == 'character' else
                      [k for k in ('visual_description', 'description', 'geometry', 'layout_note', 'appearance') if row.get(k)])
            target['locked_fields'] = list(dict.fromkeys([*(target.get('locked_fields') or []), *fields]))
        results.append({'id': owner, 'state_id': sid, 'name': row.get('name', row['id']) +
                        (' · '+str(state.get('label') or sid) if state else ''),
                        'ready': contract['ready'], 'warnings': [*contract['field_labels'], *contract['warnings']]})
    return results
