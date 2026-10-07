# -*- coding: utf-8 -*-
"""身份候选与其关联内容在普通素材审核中一起采用。"""
import base64
import copy
import hashlib
import json
from pathlib import Path

import asset_repository as repo
import asset_duplicates
import project_store
from asset_matching import possible_identities


def state_episodes(choice):
    values = choice.get('episodes') or []
    if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
        raise ValueError('派生对应分集或场景须为文本列表')
    return list(dict.fromkeys(v.strip() for v in values if v.strip()))


def existing_preview(project):
    result = []
    for filename, (kind, key) in repo.META.items():
        rows = repo._read(Path(project)/'素材'/filename).get(key, [])
        ranked = sorted(rows, key=lambda r: (asset_duplicates._priority(r), r['id']))
        for index, row in enumerate(ranked):
            candidates = [r for r in possible_identities(ranked[index+1:], row) if not asset_duplicates._related(row, r, kind)]
            if not candidates:
                continue
            ident = 'existing-identity-'+kind+':'+row['id']
            result.append({'id': ident, 'kind': kind, 'record': row, 'existing': True, 'batch_size': 1,
                'title': '历史身份 · '+str(row.get('name') or row['id']),
                'matches': ['@'+kind+':'+r['id'] for r in reversed(candidates)],
                'groups': [{'label': '待核对档案', 'before': json.dumps(row, ensure_ascii=False, indent=2),
                            'after': json.dumps(candidates, ensure_ascii=False, indent=2)}]})
    return result


def prepare_existing(project, decisions):
    """旧档只贡献缺项和媒体，用户选定的正式目标保留已采用设定。"""
    proposals = {r['id']: r for r in existing_preview(project)}
    docs, mappings = {}, {}
    removed = {'@'+proposals[d['id']]['kind']+':'+proposals[d['id']]['record']['id']
               for d in decisions if d['id'] in proposals and d.get('action') in ('reuse', 'derived')}
    edges = {}
    for choice in decisions:
        item = proposals.get(choice['id'])
        if not item:
            raise project_store.RevisionConflict('历史身份项已变化，请重新打开审核')
        if choice.get('action') in ('reuse', 'derived') and choice.get('target') not in item['matches']:
            raise ValueError('请选择对应的正式素材')
        if choice.get('action') == 'reuse':
            edges['@'+item['kind']+':'+item['record']['id']] = choice['target']
    final_targets = {}
    for source, target in edges.items():
        visited = {source}
        while target in edges:
            if target in visited:
                raise ValueError('合并关系存在循环，请为这些素材选择同一个保留目标；本次未写入')
            visited.add(target)
            target = edges[target]
        if target in removed:
            raise ValueError('合并目标正在转为派生，请选择最终保留的母素材；本次未写入')
        final_targets[source] = target
    for choice in decisions:
        item = proposals.get(choice['id'])
        if not item:
            raise project_store.RevisionConflict('历史身份项已变化，请重新打开审核')
        kind, old = item['kind'], item['record']
        filename, key = next((name, k) for name, (typ, k) in repo.META.items() if typ == kind)
        zone = filename[:-5]
        path = Path(project)/'素材'/filename
        doc = docs.setdefault(path, repo._read(path))
        incoming = next(r for r in doc[key] if r['id'] == old['id'])
        action, target = choice.get('action'), choice.get('target')
        if action == 'new':
            incoming['identity_distinct_from'] = list(dict.fromkeys([*(incoming.get('identity_distinct_from') or []), *[ref.split(':', 1)[1] for ref in item['matches']]]))
            continue
        if action == 'reuse':
            target = final_targets['@'+kind+':'+old['id']]
        if action not in ('reuse', 'derived') or action == 'derived' and target in removed:
            raise ValueError('请选择保留的正式身份；同批不能将目标本身再次合并')
        target_id = target.split(':', 1)[1]
        current = next(r for r in doc[key] if r['id'] == target_id)
        updated = {**copy.deepcopy(incoming), **copy.deepcopy(current)}
        updated['aliases'] = list(dict.fromkeys([*(current.get('aliases') or []), *(incoming.get('aliases') or []), incoming['id'], incoming.get('name', '')]))
        updated['merged_ids'] = list(dict.fromkeys([*(current.get('merged_ids') or []), *(incoming.get('merged_ids') or []), incoming['id']]))
        states = {s['id']: copy.deepcopy(s) for r in (incoming, current) for s in r.get('states') or []}
        if states:
            updated['states'] = list(states.values())
        if action == 'derived':
            label, difference = str(choice.get('label') or '').strip(), str(choice.get('difference') or '').strip()
            if not label or not difference:
                raise ValueError('派生需要名称和可见差异')
            state_id = 'state-'+hashlib.sha256(choice['id'].encode()).hexdigest()[:12]
            updated['states'] = [*states.values(), {'id': state_id, 'label': label, 'look_diff': difference, 'episodes': state_episodes(choice)}]
            index_path = Path(project)/'素材/素材图.json'
            if index_path.is_file():
                media = docs.setdefault(index_path, repo._read(index_path))
                donor = (media.get(zone) or {}).pop(incoming['id'], None)
                if donor:
                    media.setdefault(zone, {}).setdefault(target_id, {}).setdefault('states', {})[state_id] = donor
        for field in ('prompt', 'sheet_prompt', 'image_prompt'):
            if field not in current:
                updated.pop(field, None)
        mappings.update(asset_duplicates.merge_into(project, docs, zone, key, {'kind': kind, 'old': incoming['id'], 'record': updated}))
    for name in asset_duplicates.current_files(project):
        path = Path(project)/name
        doc = docs.get(path, repo._read(path))
        updated = asset_duplicates.remap(doc, mappings)
        if updated != doc or path in docs:
            docs[path] = updated
    return docs


def reviewed_documents(project, batch):
    """只用于人工审核：差异以最新正式稿为左侧，保留候选未修改的当前字段。"""
    root, changes = Path(project), {}
    for name, content in batch['documents'].items():
        proposed = base64.b64decode(content['bytes']) if 'bytes' in content else copy.deepcopy(content['json'])
        if isinstance(proposed, bytes) and name.endswith('.json'):
            proposed = json.loads(proposed)
        baseline_content = batch.get('baseline_content', {}).get(name)
        base = json.loads(base64.b64decode(baseline_content)) if baseline_content and name.endswith('.json') else {}
        if Path(name).name in repo.META and isinstance(proposed, dict):
            key = repo.META[Path(name).name][1]
            ids = {r['id'] for r in proposed.get(key, [])}
            proposed.setdefault(key, []).extend(copy.deepcopy(r) for r in base.get(key, []) if r['id'] not in ids)
        path = root/name
        actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        if actual != batch['baseline'][name]:
            if name not in batch.get('baseline_content', {}):
                raise project_store.RevisionConflict('候选缺少原始基线：'+name+'；请核对后重新提交此候选')
            if name.endswith('.json') and path.is_file() and batch['baseline_content'][name] is not None:
                proposed = repo.merge_changes(base, repo._read(path), proposed, name, prefer_second=True)
        changes[name] = proposed
    return changes


def preview(project):
    ledger = repo._read(Path(project)/repo.LEDGER)
    result = []
    for row in repo.pending_changes(project):
        batch = ledger.get('batches', {}).get(row['batch'], {})
        groups = []
        for name, content in reviewed_documents(project, batch).items():
            before = (Path(project)/name).read_text('utf-8') if (Path(project)/name).is_file() else ''
            after = content.decode('utf-8') if isinstance(content, bytes) else json.dumps(content, ensure_ascii=False, indent=2)
            if before != after:
                groups.append({'label': name, 'before': before, 'after': after})
        result.append({**row, 'title': '身份确认 · '+str(row['record'].get('name') or row['record']['id']),
                       'groups': groups, 'batch_size': len(batch.get('tickets', []))})
    return result + existing_preview(project)


def prepare(project, decisions):
    """返回待写文档和已解决候选；调用者负责持锁并统一提交。"""
    root = Path(project)
    ledger = repo._read(root/repo.LEDGER)
    pending = {r['id']: r for r in repo.pending_changes(project)}
    selected = {d['id']: d for d in decisions}
    if len(selected) != len(decisions) or any(t not in pending for t in selected):
        raise ValueError('身份审核项无效或重复，请重新打开审核')
    docs, resolved = {}, []
    for batch_id in dict.fromkeys(pending[t]['batch'] for t in selected):
        batch = ledger['batches'][batch_id]
        if any(t not in selected for t in batch['tickets']):
            raise ValueError('此批身份变更包含关联剧情，请一并选择同批的全部身份项；未确认时整批保持待审核')
        changes = reviewed_documents(project, batch)
        mappings = {}
        for ticket in batch['tickets']:
            choice, candidate = selected[ticket], pending[ticket]
            kind, row = candidate['kind'], candidate['record']
            name, key = next((n, k) for n, (typ, k) in repo.META.items() if typ == kind)
            relative = '素材/'+name
            doc = changes.setdefault(relative, repo._read(root/relative, {key: []}))
            rows = doc[key]
            incoming = next((r for r in rows if r['id'] == row['id']), None)
            if incoming is None:
                raise ValueError('候选素材与关联文档不一致')
            action = choice.get('action')
            if action == 'new':
                incoming['identity_decision'] = {'action': 'new', 'ticket': ticket}
                incoming['identity_distinct_from'] = list(dict.fromkeys([*(incoming.get('identity_distinct_from') or []), *[ref.split(':', 1)[1] for ref in candidate['matches']]]))
            elif action in ('reuse', 'derived'):
                target = str(choice.get('target') or '')
                if target not in candidate['matches']:
                    raise ValueError('请选择候选中的同类型正式素材')
                ident = target.split(':', 1)[1]
                formal = next((r for r in repo._read(root/relative).get(key, []) if r['id'] == ident), None)
                if formal is None:
                    raise project_store.RevisionConflict('目标素材已不存在')
                existing = next((r for r in rows if r['id'] == ident), None)
                if existing is None:
                    existing = copy.deepcopy(formal)
                    rows.append(existing)
                if action == 'reuse':
                    for field, value in incoming.items():
                        if field not in repo.METADATA | {'id', 'aliases', 'states', 'locked_fields'} and value not in (None, '', [], {}):
                            if candidate['source'] != 'legacy' or existing.get(field) in (None, '', [], {}):
                                existing[field] = value
                    existing['aliases'] = list(dict.fromkeys([*(existing.get('aliases') or []), formal.get('name', ''), incoming['id'], incoming.get('name', '')]))
                    existing['merged_ids'] = list(dict.fromkeys([*(existing.get('merged_ids') or []), incoming['id']]))
                    states = {s['id']: s for s in existing.get('states') or []}
                    for state in incoming.get('states') or []:
                        if state['id'] not in states or candidate['source'] != 'legacy':
                            states[state['id']] = state
                    if states:
                        existing['states'] = list(states.values())
                else:
                    label, difference = str(choice.get('label') or '').strip(), str(choice.get('difference') or '').strip()
                    if not label or not difference:
                        raise ValueError('派生状态需要名称和明确的可见差异')
                    states = existing.setdefault('states', [])
                    state_id = 'state-'+hashlib.sha256(ticket.encode()).hexdigest()[:12]
                    states.append({'id': state_id, 'label': label, 'look_diff': difference,
                                   'episodes': state_episodes(choice), 'source_ref': '@'+kind+':'+incoming['id']})
                existing['identity_decision'] = {'action': action, 'ticket': ticket}
                rows.remove(incoming)
                for ref in candidate['proposed_refs']:
                    mappings[ref] = target
            else:
                raise ValueError('请选择复用已有、独立素材或派生状态；暂不采用请取消勾选')
            resolved.append(ticket)
        for name, doc in changes.items():
            doc = asset_duplicates.remap(doc, mappings) if isinstance(doc, dict) else doc
            if name in docs and docs[name] != doc:
                raise ValueError('所选两批修改同一文档，请先采用一批，再核对下一批')
            docs[name] = doc
    book = docs.get('剧本/分集.json')
    if isinstance(book, dict) and book.get('planning_revision_id'):
        import story_planning_versions as planning
        path = planning._directory(project, book['planning_revision_id'])/'version.json'
        if path.is_file():
            meta = repo._read(path)
            meta.update(status='applied')
            meta.pop('error', None)
            docs[path.relative_to(root).as_posix()] = meta
    return docs, resolved
