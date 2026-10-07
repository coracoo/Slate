# -*- coding: utf-8 -*-
"""素材身份与设定的统一提交边界；正式档案仍保存在人物、场景、道具 JSON。"""
import base64
import copy
import hashlib
import json
import os
import re
import sys
import tempfile
import threading
import uuid
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'previs_system/tools'))
import project_store
import versions
from asset_matching import identity_match, possible_identities

META = {'人物.json': ('character', 'characters'), '场景.json': ('scene', 'scenes'), '道具.json': ('prop', 'props')}
FILES = tuple('素材/'+name for name in META)
LEDGER = '素材/.work/提交记录.json'
JOURNAL = '素材/.work/未完成提交.json'
METADATA = {'asset_revision', 'settings_source', 'settings_updated_at', 'field_sources', 'settings_fingerprint'}
_HELD = threading.local()


def merge_changes(base, first, second, location='', *, prefer_second=False):
    """以同一基线合并两份明确选中的变更；同字段分歧须重新审核。"""
    if second == base or first == second:
        return copy.deepcopy(first)
    if first == base:
        return copy.deepcopy(second)
    if all(isinstance(v, dict) for v in (base, first, second)):
        result = {}
        absent = object()
        for key in dict.fromkeys([*first, *second, *base]):
            b, a, c = base.get(key, absent), first.get(key, absent), second.get(key, absent)
            if key in METADATA:
                if a is not absent:
                    result[key] = copy.deepcopy(a)
                continue
            if c == b or a == c:
                value = a
            elif a == b:
                value = c
            elif absent in (b, a, c):
                if not prefer_second:
                    raise project_store.RevisionConflict('所选变更冲突：'+location+'.'+key)
                value = c
            else:
                value = merge_changes(b, a, c, location+'.'+key, prefer_second=prefer_second)
            if value is not absent:
                result[key] = copy.deepcopy(value)
        return result
    if all(isinstance(v, list) and all(isinstance(r, dict) and r.get('id') for r in v) for v in (base, first, second)):
        merged = merge_changes(*({r['id']: r for r in v} for v in (base, first, second)), location, prefer_second=prefer_second)
        return list(merged.values())
    if prefer_second:
        return copy.deepcopy(second)
    raise project_store.RevisionConflict('所选变更对同一字段有不同修改：'+location)


def is_archive(path):
    path = Path(path)
    return path.name in META and path.parent.name == '素材'


def _bytes(doc):
    return project_store._canonical_bytes(doc)


def _read(path, default=None):
    path = Path(path)
    value = json.loads(path.read_text('utf-8')) if path.is_file() else copy.deepcopy(default or {})
    # 旧三件套允许顶层数组；提交时统一转为对象，保留旧素材及读取基线。
    if is_archive(path) and isinstance(value, list):
        value = {META[path.name][1]: value}
    return value


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.asset-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _restore(root, before):
    for name, encoded in before.items():
        path = root/name
        if encoded is None:
            path.unlink(missing_ok=True)
        else:
            _write(path, base64.b64decode(encoded))


@contextmanager
def locks(project, names=()):
    root = Path(project).resolve()
    paths = set(FILES) | {LEDGER, JOURNAL} | set(names)
    held = getattr(_HELD, 'projects', {})
    if root in held:
        with ExitStack() as stack:
            added = paths - held[root]
            for name in sorted(added):
                path = (root/name).resolve()
                if not path.is_relative_to(root):
                    raise ValueError('素材事务路径不属于当前项目')
                stack.enter_context(project_store._exclusive(path))
            held[root].update(added)
            try:
                yield
            finally:
                held[root].difference_update(added)
        return
    # 全部写入者先获得同一个事务锁，再按路径顺序获得文档锁。
    with project_store._exclusive(root/JOURNAL):
        journal = _read(root/JOURNAL)
        paths.update((journal.get('before') or {}).keys())
        with ExitStack() as stack:
            for name in sorted(paths - {JOURNAL}):
                path = (root/name).resolve()
                if not path.is_relative_to(root):
                    raise ValueError('素材事务路径不属于当前项目')
                stack.enter_context(project_store._exclusive(path))
            if journal:
                _restore(root, journal['before'])
                (root/JOURNAL).unlink()
            _HELD.projects = held
            held[root] = paths
            try:
                yield
            finally:
                del held[root]


def _content(row):
    return {k: v for k, v in row.items() if k not in METADATA}


def _stamp(old, new, source, operation):
    fields = sorted(k for k in set(_content(old)) | set(_content(new)) if old.get(k) != new.get(k))
    if not fields:
        for key in METADATA:
            if key in old:
                new[key] = copy.deepcopy(old[key])
            else:
                new.pop(key, None)
        return None
    now = datetime.now(timezone.utc).isoformat()
    revision = max(1, int(old.get('asset_revision') or 1)) + 1 if old else 1
    new.update(asset_revision=revision, settings_source=source, settings_updated_at=now,
               settings_fingerprint=hashlib.sha256(_bytes(_content(new))).hexdigest())
    provenance = copy.deepcopy(old.get('field_sources') or {})
    for field in fields:
        provenance[field] = {'source': source, 'operation_id': operation, 'revision': revision}
    new['field_sources'] = provenance
    return {'revision': revision, 'fields': fields, 'source': source, 'at': now}


def _impacts(root, changes):
    if not changes:
        return
    visual = {'appearance', 'visual_description', 'identity_anchor', 'sheet_prompt', 'image_prompt', 'states',
              'layout_note', 'geometry', 'spatial_limit', 'action_slots', 'parent_ref', 'related_refs', 'style', 'style_prompt'}
    for change in changes.values():
        fields = set(change['fields'])
        change['impact'] = {'image': bool(fields & visual), 'voice': bool(fields & {'voice', 'voice_binding'}), 'storyboards': []}
    for path in sorted((root/'分镜').glob('*.json')):
        try:
            board = _read(path)
        except (ValueError, OSError):
            continue
        refs = set(re.findall(r'@(character|scene|prop):([A-Za-z0-9_.-]+)', json.dumps(board, ensure_ascii=False)))
        for kind, ident in refs:
            change = changes.get('@'+kind+':'+ident)
            if change:
                change['impact']['storyboards'].append(path.name)


def commit_locked(project, documents, *, source, bases=None, operation_id=None, allow_removal=False, resolved=()):
    """调用者须持有 locks；documents 的键是项目相对路径，值为 JSON、bytes 或 None。"""
    root = Path(project).resolve()
    for name in [*documents, *(bases or {})]:
        if not (root/name).resolve().is_relative_to(root) or str(name) in (LEDGER, JOURNAL):
            raise ValueError('素材事务路径不属于当前项目或占用内部记录')
    operation = operation_id or uuid.uuid4().hex
    payload_hash = hashlib.sha256(_bytes({'source': source, 'documents': {
        str(k): base64.b64encode(v).decode() if isinstance(v, bytes) else v for k, v in documents.items()}})).hexdigest()
    ledger = _read(root/LEDGER, {'operations': {}, 'assets': {}})
    ledger.setdefault('batches', {})
    previous = ledger['operations'].get(operation)
    if previous:
        if previous['payload_hash'] != payload_hash:
            raise ValueError('同一操作 ID 不能提交不同内容')
        return {**previous['result'], 'replayed': True}
    docs = copy.deepcopy(documents)
    for name, base in (bases or {}).items():
        actual = _read(root/name) if (root/name).is_file() else None
        if is_archive(root/name) and isinstance(base, list):
            base = {META[Path(name).name][1]: base}
        if actual != base:
            raise project_store.RevisionConflict(f'{name} 已有新修改，本次旧结果未覆盖，请重新核对差异')
    mapping, changed, pending = {}, {}, []
    ledger.setdefault('pending', {})
    for name in FILES:
        if name not in docs:
            continue
        if docs[name] is None:
            raise ValueError('不能删除正式素材档案')
        if isinstance(docs[name], bytes):
            docs[name] = json.loads(docs[name])
        kind, key = META[Path(name).name]
        if isinstance(docs[name], list):
            docs[name] = {key: docs[name]}
        before = _read(root/name, {key: []})
        old_rows = {str(r['id']): r for r in before.get(key, [])}
        rows, seen = [], set()
        for proposed in docs[name].get(key, []):
            row = copy.deepcopy(proposed)
            ident = str(row.get('id') or '')
            if not ident:
                raise ValueError('正式素材必须具有稳定 ID')
            old = old_rows.get(ident)
            if old is None:
                try:
                    old = None if row.get('identity_decision', {}).get('action') == 'new' else identity_match([*rows, *(r for r in old_rows.values() if r['id'] not in seen)], row)
                except ValueError:
                    if source not in ('planning', 'legacy'):
                        raise
                    old = None
                if old:
                    mapping['@'+kind+':'+ident] = '@'+kind+':'+str(old['id'])
                    row['id'] = old['id']
                    row['aliases'] = list(dict.fromkeys([*(old.get('aliases') or []), *(row.get('aliases') or []), ident]))
                    canonical = next((r for r in rows if r['id'] == old['id']), None)
                    if canonical is not None:
                        row = {**canonical, **row}
                        old = canonical
                        rows.remove(canonical)
                        seen.discard(row['id'])
            if old is None and source in ('planning', 'legacy') and not any(part in ('.work', '.versions') for part in root.parts):
                candidates = possible_identities(list(old_rows.values()), row)
                if candidates:
                    ticket = 'identity-' + hashlib.sha256(_bytes({'operation': payload_hash, 'kind': kind, 'name': row.get('name'),
                        'parent': row.get('parent_ref')})).hexdigest()[:20]
                    prior = ledger['pending'].get(ticket) or {}
                    ledger['pending'][ticket] = {'id': ticket, 'kind': kind, 'record': row, 'source': source, 'batch': payload_hash,
                        'proposed_refs': list(dict.fromkeys([*(prior.get('proposed_refs') or []), '@'+kind+':'+ident])),
                        'matches': ['@'+kind+':'+r['id'] for r in candidates], 'status': 'pending'}
                    pending.append(ticket)
                    continue
            if row['id'] in seen:
                raise ValueError('本次提交包含重复素材 ID：'+str(row['id']))
            seen.add(row['id'])
            if source == 'legacy' and old:
                merged = copy.deepcopy(old)
                for field, value in row.items():
                    if field not in METADATA and field not in ('image_prompt', 'sheet_prompt', 'prompt') and merged.get(field) in (None, '', [], {}):
                        merged[field] = value
                merged['aliases'] = list(dict.fromkeys([*(old.get('aliases') or []), *(row.get('aliases') or [])]))
                row = merged
            if old and source in ('planning', 'completion', 'legacy'):
                for field in old.get('locked_fields') or []:
                    if field in old:
                        row[field] = copy.deepcopy(old[field])
                    elif field.startswith('appearance.'):
                        part = field.split('.', 1)[1]
                        appearance = old.get('appearance') or {}
                        if part in appearance:
                            row.setdefault('appearance', {})[part] = copy.deepcopy(appearance[part])
                            if part in (appearance.get('sources') or {}):
                                row['appearance'].setdefault('sources', {})[part] = appearance['sources'][part]
                states = {s['id']: s for s in row.get('states') or []}
                for state in old.get('states') or []:
                    if state.get('locked_fields'):
                        current = states.setdefault(state['id'], copy.deepcopy(state))
                        for field in state['locked_fields']:
                            if field in state:
                                current[field] = copy.deepcopy(state[field])
                if states:
                    row['states'] = list(states.values())
            rows.append(row)
        missing = set(old_rows) - seen
        if missing and not allow_removal:
            rows.extend(copy.deepcopy(old_rows[ident]) for ident in old_rows if ident in missing)
        docs[name][key] = rows
    if mapping:
        from asset_duplicates import remap
        docs = {name: remap(doc, mapping) if isinstance(doc, dict) else doc for name, doc in docs.items()}
    for name in FILES:
        if name not in docs:
            continue
        kind, key = META[Path(name).name]
        old = {r['id']: r for r in _read(root/name).get(key, [])}
        for row in docs[name][key]:
            change = _stamp(old.get(row['id'], {}), row, source, operation)
            if change:
                changed['@'+kind+':'+row['id']] = change
    for ticket in resolved:
        if ticket not in ledger['pending'] or ledger['pending'][ticket]['status'] != 'pending':
            raise project_store.RevisionConflict('身份审核项已有变化，请重新打开审核')
        ledger['pending'][ticket]['status'] = 'resolved'
    if pending:
        # 关联剧情和名册必须一起采用，身份未定时整批留为候选。
        ledger['batches'].setdefault(payload_hash, {
            'documents': {str(name): {'bytes': base64.b64encode(value).decode()} if isinstance(value, bytes)
                          else {'json': value} for name, value in documents.items()},
            'baseline': {str(name): hashlib.sha256((root/name).read_bytes()).hexdigest() if (root/name).is_file() else None
                         for name in documents},
            'baseline_content': {str(name): base64.b64encode((root/name).read_bytes()).decode() if (root/name).is_file() else None
                                 for name in documents},
            'source': source, 'tickets': pending, 'operation_id': operation})
        docs, changed, mapping = {}, {}, {}
    _impacts(root, changed)
    result = {'ok': not pending, 'operation_id': operation, 'changed': changed, 'remap': mapping, 'pending': pending, 'replayed': False}
    encoded = {str(name): value if isinstance(value, (bytes, type(None))) else _bytes(value) for name, value in docs.items()}
    encoded = {name: value for name, value in encoded.items() if value != ((root/name).read_bytes() if (root/name).is_file() else None)}
    if not encoded and not operation_id and not pending and not resolved:
        return result
    ledger['assets'].update(changed)
    if not pending:
        ledger['operations'][operation] = {'payload_hash': payload_hash, 'result': result}
    for batch in list(ledger['batches']):
        if all(ledger['pending'][t]['status'] == 'resolved' for t in ledger['batches'][batch]['tickets']):
            original_operation = ledger['batches'][batch].get('operation_id')
            if original_operation:
                ledger['operations'][original_operation] = {'payload_hash': batch, 'result': result}
            del ledger['batches'][batch]
    encoded[LEDGER] = _bytes(ledger)
    before = {name: base64.b64encode((root/name).read_bytes()).decode() if (root/name).is_file() else None for name in encoded}
    _write(root/JOURNAL, _bytes({'before': before}))
    try:
        for name, content in encoded.items():
            path = (root/name).resolve()
            if not path.is_relative_to(root):
                raise ValueError('素材事务路径不属于当前项目')
            if name != LEDGER:
                versions.snapshot(str(path))
            if content is None:
                path.unlink(missing_ok=True)
            else:
                _write(path, content)
    except BaseException:
        _restore(root, before)
        (root/JOURNAL).unlink(missing_ok=True)
        raise
    (root/JOURNAL).unlink(missing_ok=True)
    return result


def pending_changes(project):
    return [r for r in _read(Path(project)/LEDGER).get('pending', {}).values() if r.get('status') == 'pending']


def require_applied(result):
    if result.get('pending'):
        raise ValueError(f"{len(result['pending'])} 项素材身份待确认，整批内容已保留。请打开素材页「批量补齐与审核素材设定」，确认对应关系后直接采用，无需重新生成。")
    return result


def upsert_roster(project, roster, replace_settings=False):
    """规划名册只提供身份与骨架字段；统一服务决定正式 ID 和版本。"""
    fields_by_kind = {'character': {'one_line': 'basis'}, 'scene': {'one_line': 'layout_note'}, 'prop': {'one_line': 'shot_hint'}}
    with locks(project):
        docs, mapping, created, reused = {}, {}, [], []
        for filename, (kind, key) in META.items():
            incoming = roster.get(key) or []
            if not incoming:
                continue
            name = '素材/'+filename
            doc = _read(Path(project)/name, {key: []})
            rows = doc.setdefault(key, [])
            for item in incoming:
                try:
                    hit = identity_match(rows, item)
                except ValueError:
                    hit = None
                proposed = '@'+kind+':'+item['id']
                if hit is None:
                    hit = {'id': item['id'], 'name': item['name'], 'source': 'story_units', 'basis': item.get('one_line') or '规划名册'}
                    if kind == 'character':
                        hit.update(role=item.get('role') or '次要', gender=item.get('gender') or '不明', is_collective=bool(item.get('is_collective')), states=[])
                    elif kind == 'scene':
                        hit.update(interior=bool(item.get('interior')), time=item.get('time') or '日')
                    else:
                        hit.update(kind=item.get('kind') or '叙事', asset_required=True)
                    rows.append(hit)
                    created.append(proposed)
                    update = True
                else:
                    reused.append('@'+kind+':'+hit['id'])
                    update = replace_settings
                if item['name'] != hit.get('name'):
                    hit['aliases'] = list(dict.fromkeys([*(hit.get('aliases') or []), item['name']]))
                if update:
                    fields = {k: item.get(k) for k in ('role', 'gender', 'is_collective', 'interior', 'time', 'kind')}
                    fields.update({target: item.get(origin) for origin, target in fields_by_kind[kind].items()})
                    hit.update({k: v for k, v in fields.items() if k not in (hit.get('locked_fields') or []) and v not in (None, '', [], {})})
                mapping[proposed] = '@'+kind+':'+hit['id']
                if item.get('_original_id'):
                    mapping['@'+kind+':'+item['_original_id']] = '@'+kind+':'+hit['id']
            docs[name] = doc
        result = commit_locked(project, docs, source='planning')
        require_applied(result)
        mapping.update(result['remap'])
        return {'created': created, 'reused': reused, 'remap': mapping}


def commit(project, documents, *, source, bases=None, operation_id=None, allow_removal=False):
    with locks(project, documents):
        return commit_locked(project, documents, source=source, bases=bases, operation_id=operation_id, allow_removal=allow_removal)


def update_json(path, mutate, *, source='manual', expected_revision=None, create_default=None, snapshot=None):
    """现有单文档编辑 API 的适配器，版本格式与 project_store 一致。"""
    path = Path(path).resolve()
    if not is_archive(path):
        return project_store.update_json(path, mutate, expected_revision=expected_revision, create_default=create_default, snapshot=snapshot)
    root, name = path.parent.parent, '素材/'+path.name
    with locks(root):
        before = _read(path) if path.is_file() else None
        if before is None and create_default is None:
            raise FileNotFoundError(path)
        revision = project_store._revision(_bytes(before)) if before is not None else None
        if expected_revision is not None and expected_revision != revision:
            raise project_store.RevisionConflict('素材版本已变化，请重新载入后提交')
        doc = copy.deepcopy(before if before is not None else create_default)
        result = mutate(doc)
        if result is not None:
            doc = result
        result = commit_locked(root, {name: doc}, source=source)
        require_applied(result)
        saved = _read(path)
        return saved, project_store._revision(_bytes(saved))
