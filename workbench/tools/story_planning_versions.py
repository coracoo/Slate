# -*- coding: utf-8 -*-
"""完整规划版本：隔离生成候选，采用或恢复前归档当前规划。"""
import hashlib
import copy
import json
import os
import re
import sys
import tempfile
import uuid
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / 'previs_system/tools'
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))
import project_store
import story_units
import versions
from error_utils import scrub_error
from brief import load_brief
from script_repository import load_script

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

FILES = ('剧本/构想.txt', '剧本/剧本.txt', '剧本/规划原稿.txt', '剧本/source.json',
         '剧本/brief.json', '剧本/style.json', '剧本/大纲.json', '剧本/分集.json',
         '剧本/埋线.json', '素材/人物.json', '素材/场景.json', '素材/道具.json')


def _collect(project):
    project = Path(project)
    paths = [project / name for name in FILES]
    paths += [p for p in (project / '剧本').glob('分集剧本_E*.txt')
              if re.fullmatch(r'分集剧本_E\d+\.txt', p.name)]
    return {p.relative_to(project).as_posix(): p.read_bytes() for p in paths if p.is_file()}


def _fingerprint(contents):
    hashes = {name: hashlib.sha256(value).hexdigest() for name, value in contents.items()}
    return hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()


def current_revision(project):
    return _fingerprint(_collect(project))


def _write_bytes(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.planning-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _json_bytes(data):
    return json.dumps(data, ensure_ascii=False, indent=1).encode('utf-8')


def _root(project):
    return Path(project) / '剧本/.versions/规划'


def _directory(project, version_id):
    if not re.fullmatch(r'(?:plan|history)-[a-f0-9]{16}', str(version_id)):
        raise ValueError('规划版本号不合法')
    return _root(project) / version_id


def _meta(project, version_id):
    path = _directory(project, version_id) / 'version.json'
    if not path.is_file():
        raise ValueError('规划版本不存在')
    return json.loads(path.read_text(encoding='utf-8'))


def version_project(project, version_id):
    _meta(project, version_id)
    return _directory(project, version_id) / '内容'


def _save_meta(project, meta):
    _write_bytes(_directory(project, meta['id']) / 'version.json', _json_bytes(meta))


def _archive(project, contents, label):
    version_id = 'history-' + uuid.uuid4().hex[:16]
    target = _directory(project, version_id) / '内容'
    for name, content in contents.items():
        _write_bytes(target / name, content)
    _save_meta(project, {'id': version_id, 'status': 'history', 'label': label,
                        'created_at': datetime.now().isoformat(timespec='seconds'),
                        'fingerprint': _fingerprint(contents)})
    return version_id


def _locks(project, names):
    stack = ExitStack()
    try:
        for name in sorted(set(FILES) | set(names)):
            stack.enter_context(project_store._exclusive(Path(project) / name))
    except BaseException:
        stack.close()
        raise
    return stack


def list_versions(project):
    revision = current_revision(project)
    result = []
    for path in _root(project).glob('*/version.json'):
        meta = json.loads(path.read_text(encoding='utf-8'))
        workspace = version_project(project, meta['id'])
        units = story_units.load_units(workspace)
        result.append({key: meta.get(key) for key in ('id', 'status', 'label', 'created_at', 'error',
                                                     'revision_mode', 'instructions', 'changes')}
                      | {'episode_count': len(units['episodes']), 'premise': units['outline'].get('premise', ''),
                         'can_adopt': meta['status'] == 'ready' and meta.get('base_revision') == revision
                                      and meta.get('revision_mode') in ('extend', 'rewrite'),
                         'can_resume': meta['status'] == 'failed' and meta.get('base_revision') == revision
                                       and meta.get('revision_mode') in ('extend', 'rewrite')
                                       and (workspace / '剧本/修订上下文.json').is_file()})
    return sorted(result, key=lambda row: (row['created_at'], row['id']), reverse=True)


# 扩写保护判据只看"剧情内容字段"——arc_id 是分段归属，扩集时 LLM 合法重划分段会改它，
# 混进判据会让扩写永远撞"不能替换既有框架"（E3-E6 剧情一字未动也被否）。归属统一由骨架阶段重算。
FRAMEWORK_FIELDS = ('title', 'summary', 'beats', 'hook', 'cliff', 'duration_min', 'cast_refs',
                    'scene_refs', 'key_asset_refs', 'relation_shift', 'state_derive')


def revision_request(project, target, mode='auto', instructions=''):
    """同一项目只扩展或修订既有故事；初次创作由首次规划入口负责。"""
    rows = story_units.load_units(project)['episodes']
    ids = [str(row.get('id')) for row in rows]
    if not rows or sorted(ids) != sorted(f'E{n}' for n in range(1, len(rows) + 1)):
        raise ValueError('请先核对已有分集，集号须连续且不重复')
    if mode == 'auto':
        mode = 'extend' if target > len(rows) else 'rewrite'
    if mode not in ('extend', 'rewrite'):
        raise ValueError('修订方式须为扩写或改写')
    if mode == 'extend' and target <= len(rows):
        raise ValueError('扩写分集的目标集数须大于当前集数；同集数或缩集请使用改写')
    instructions = str(instructions or '').strip()
    if mode == 'rewrite' and not instructions:
        raise ValueError('改写已有故事请填写修改要求，不能以新构想替换当前项目')
    return mode, instructions


def _canon_ref(value):
    if isinstance(value, list):
        return tuple(sorted(_canon_ref(v) for v in value))
    return ''.join(ch for ch in str(value) if ch.isalnum()).lower()


def _framework(row):
    """框架指纹：引用列表按规范化排序去重后比较——同一实体的 id 变体（连字符/下划线）
    或仅顺序不同不算框架变化；文本字段原样比较。"""
    out = {}
    for key in FRAMEWORK_FIELDS:
        value = row.get(key)
        if isinstance(value, list) and key.endswith('_refs'):
            seen, uniq = set(), []
            for item in value:
                ck = _canon_ref(item)
                if ck not in seen:
                    seen.add(ck); uniq.append(item)
            value = sorted(uniq)
        out[key] = value or None
    return out


def _finish_revision(workspace, context):
    """保留未改变的正文，变化的分集正文由历史版本保存。"""
    candidate = story_units.load_units(workspace)
    book = candidate['episodes_doc']
    old = {str(row['id']): row for row in context['episodes']}
    new = {str(row['id']): row for row in book['episodes']}
    changes = {'preserved': [], 'changed': [], 'added': [], 'removed': [eid for eid in old if eid not in new]}
    for eid, row in new.items():
        prior = old.get(eid)
        same = prior is not None and _framework(prior) == _framework(row)
        if context['mode'] == 'extend' and prior is not None and not same:
            raise ValueError(f'扩写不能替换 {eid} 的既有框架；需要调整旧集时请使用改写并填写修改要求')
        bucket = 'added' if prior is None else 'preserved' if same else 'changed'
        changes[bucket].append(eid)
        for field in ('text', 'char_start', 'char_end', 'start', 'end'):
            row.pop(field, None)
        if same:
            if context['mode'] == 'extend':
                row.update(copy.deepcopy(prior))
                row['arc_id'] = next((arc['id'] for arc in candidate['outline'].get('arcs') or []
                    if int(str(arc['ep_from'])[1:]) <= int(eid[1:]) <= int(str(arc['ep_to'])[1:])), row.get('arc_id'))
            elif prior.get('text'):
                row['text'] = prior['text']
                if 'script_edit_rev' in prior:
                    row['script_edit_rev'] = prior['script_edit_rev']
        elif row.get('script_edit_rev'):
            row.pop('script_edit_rev')
        text = str(row.get('text') or '')
        if text:
            _write_bytes(workspace / f'剧本/分集剧本_{eid}.txt', text.encode('utf-8'))
    book.update(mode='generated', anchor_rev=0, revision_mode=context['mode'])
    _write_bytes(workspace / '剧本/分集.json', _json_bytes(book))
    merged = '\n\n'.join(f'【{row["id"]}】\n{row["text"]}' for row in book['episodes'] if row.get('text'))
    _write_bytes(workspace / '剧本/剧本.txt', merged.encode('utf-8'))
    return changes


def generate(project, *, target_episodes, source='idea', idea=None, revision_mode='auto', instructions='', runner):
    project = Path(project)
    target = int(target_episodes or load_brief(project).get('total_episodes') or 0)
    if not 1 <= target <= 200:
        raise ValueError('重新规划请填写 1–200 的全剧目标集数')
    if source not in ('idea', 'script'):
        raise ValueError('重新规划来源须为构想或正文')
    with _locks(project, _collect(project)):
        revision_mode, instructions = revision_request(project, target, revision_mode, instructions)
        original = _collect(project)
        source_text = load_script(str(project))
        existing = story_units.load_units(project)
        idea_text = str(idea if idea is not None else original.get('剧本/构想.txt', b'').decode('utf-8')).strip()
        from episode_editor import episode_text
        old_episodes = copy.deepcopy(existing['episodes'])
        for row in old_episodes:
            text = episode_text(project, existing['episodes_doc'], row)
            if text:
                row['text'] = text
        references = story_units.referenced_assets(project)
        asset_settings = {}
        for kind, key, fields in (('character', 'characters', story_units.CHAR_KEYS),
                                  ('scene', 'scenes', story_units.SCENE_KEYS), ('prop', 'props', story_units.PROP_KEYS)):
            keys = ('id', 'name', 'aliases', 'gender', 'age', 'role', 'parent_ref', 'derived_from', *fields)
            asset_settings[key] = [{field: row[field] for field in keys if field in row}
                                   for row in existing[key] if f'@{kind}:{row.get("id")}' in references]
        context = {'version': 1, 'mode': revision_mode, 'instructions': instructions, 'target_episodes': target,
                   'outline': existing['outline'], 'episodes': old_episodes,
                   'foreshadows': existing['foreshadows'], 'hooks': existing['hooks'], 'asset_settings': asset_settings}
        base_revision = _fingerprint(original)
        history_id = _archive(project, original, '重新规划前的完整版本')
    version_id = 'plan-' + uuid.uuid4().hex[:16]
    workspace = _directory(project, version_id) / '内容'
    label = '扩写分集' if revision_mode == 'extend' else '改写分集'
    meta = {'id': version_id, 'status': 'generating', 'label': f'{label} · {len(old_episodes)} → {target} 集',
            'created_at': datetime.now().isoformat(timespec='seconds'), 'base_revision': base_revision,
            'history_id': history_id, 'source': source, 'target_episodes': target,
            'revision_mode': revision_mode, 'instructions': instructions}
    _save_meta(project, meta)
    try:
        for name in ('剧本/brief.json', '剧本/style.json', '素材/人物.json', '素材/场景.json', '素材/道具.json'):
            if name in original:
                _write_bytes(workspace / name, original[name])
        brief = load_brief(workspace)
        brief['total_episodes'] = target
        _write_bytes(workspace / '剧本/brief.json', _json_bytes(brief))
        _write_bytes(workspace / '剧本/构想.txt', idea_text.encode('utf-8'))
        _write_bytes(workspace / '剧本/剧本.txt', source_text.encode('utf-8'))
        _write_bytes(workspace / '剧本/规划原稿.txt', source_text.encode('utf-8'))
        _write_bytes(workspace / '剧本/修订上下文.json', _json_bytes(context))
        if revision_mode == 'extend':
            book = copy.deepcopy(existing['episodes_doc'])
            book.update(episodes=old_episodes, anchor_rev=0)
            book.pop('anchor_fingerprint', None)
            _write_bytes(workspace / '剧本/分集.json', _json_bytes(book))
        for name, key in [('人物', 'characters'), ('场景', 'scenes'), ('道具', 'props')]:
            path = workspace / '素材' / f'{name}.json'
            doc = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {key: []}
            doc.pop('anchor_rev', None)
            for row in doc.get(key) or []:
                if 'source_episode_ids' not in (row.get('locked_fields') or []):
                    row.pop('source_episode_ids', None)
            _write_bytes(path, _json_bytes(doc))
        print(f'[{label}] 既有 {len(old_episodes)} 集作为剧情依据；完整历史已保存，候选 {version_id}，目标 {target} 集。', flush=True)
        return _run_candidate(project, meta, runner)
    except (Exception, SystemExit) as exc:
        meta.update(status='failed', error=scrub_error(exc))
        _save_meta(project, meta)
        print(f'[重新规划失败] {meta["error"]}；当前规划保留，候选记录可查看。', flush=True)
        return {'ok': False, 'version_id': version_id, 'incomplete': [meta['error']]}


def _run_candidate(project, meta, runner):
    """首次生成与断点续跑共享同一套完成闸门。"""
    workspace = version_project(project, meta['id'])
    target = meta['target_episodes']
    try:
        context = json.loads((workspace / '剧本/修订上下文.json').read_text(encoding='utf-8'))
        report = runner(workspace, target)
        units = story_units.load_units(workspace)
        ids = [str(e.get('id')) for e in units['episodes']]
        exact = sorted(ids) == sorted(f'E{n}' for n in range(1, target + 1))
        checked = story_units.check(workspace)
        ok = bool(report.get('ok') and not report.get('incomplete') and checked['ok'] and exact)
        if not exact:
            report.setdefault('incomplete', []).append(f'候选分集必须完整覆盖 E1–E{target}')
        if not ok:
            raise ValueError('；'.join(report.get('incomplete') or [e['message'] for e in checked['errors']]) or '候选规划未完成')
        changes = _finish_revision(workspace, context)
        _write_bytes(workspace / '剧本/source.json', _json_bytes({'mode': 'generated'}))
        meta.update(status='ready', changes=changes, fingerprint=current_revision(workspace))
        meta.pop('error', None)
        _save_meta(project, meta)
        print(f'[候选完成] {target} 集规划可在剧本页查看并采用；当前规划未替换。', flush=True)
        return {'ok': True, 'version_id': meta['id'], 'incomplete': []}
    except (Exception, SystemExit) as exc:
        meta.update(status='failed', error=scrub_error(exc))
        _save_meta(project, meta)
        print(f'[修订失败] {meta["error"]}；候选进度已保存，可继续生成。', flush=True)
        return {'ok': False, 'version_id': meta['id'], 'incomplete': [meta['error']]}


def resume_request(project, version_id):
    meta = _meta(project, version_id)
    if meta['status'] != 'failed':
        raise ValueError('仅失败候选允许继续生成；执行中的候选不能重复启动')
    if meta.get('base_revision') != current_revision(project):
        raise project_store.RevisionConflict('当前规划已变化，原候选不可续跑，请按当前内容重新修订')
    workspace = version_project(project, version_id)
    path = workspace / '剧本/修订上下文.json'
    if meta.get('revision_mode') not in ('extend', 'rewrite') or not path.is_file():
        raise ValueError('旧版候选缺少修订基线，无法继续生成')
    context = json.loads(path.read_text(encoding='utf-8'))
    if context.get('mode') != meta['revision_mode'] or context.get('target_episodes') != meta['target_episodes']:
        raise ValueError('候选修订基线与版本记录不一致')
    return meta


def resume(project, version_id, *, runner):
    project = Path(project)
    with _locks(project, _collect(project)), project_store._exclusive(_directory(project, version_id) / 'version.json'):
        meta = resume_request(project, version_id)
        meta.update(status='generating', resumed_at=datetime.now().isoformat(timespec='seconds'))
        _save_meta(project, meta)
    print(f'[继续修订] 沿用候选 {version_id}，目标 {meta["target_episodes"]} 集；只补未完成部分。', flush=True)
    return _run_candidate(project, meta, runner)


def _replace(project, version_id, revision, *, restoring):
    project = Path(project)
    meta = _meta(project, version_id)
    workspace = version_project(project, version_id)
    current = story_units.load_units(project)['episodes_doc']
    if not restoring and current.get('planning_version') == version_id:
        return {'ok': True, 'changed': False, 'version_id': version_id}
    allowed = ('history', 'adopted') if restoring else ('ready',)
    if meta['status'] not in allowed:
        raise ValueError('该版本不可恢复' if restoring else '候选未完成，不能采用')
    if not restoring and meta.get('revision_mode') not in ('extend', 'rewrite'):
        raise ValueError('旧版候选缺少修订基线，请使用扩写 / 改写重新生成')
    contents = _collect(workspace)
    if _fingerprint(contents) != meta.get('fingerprint'):
        raise ValueError('规划版本文件已变化，请重新生成候选')
    with _locks(project, set(_collect(project)) | set(contents)):
        before = _collect(project)
        current_revision_value = _fingerprint(before)
        if not revision or revision != current_revision_value:
            raise project_store.RevisionConflict('当前规划已变化，请刷新后重试')
        if not restoring and meta['base_revision'] != current_revision_value:
            raise project_store.RevisionConflict('候选生成后当前规划已修改，请重新规划；当前修改已保留')
        history_id = _archive(project, before, '恢复前的完整版本' if restoring else '采用前的完整版本')
        book = json.loads(contents.get('剧本/分集.json', b'{}'))
        old_book = json.loads(before.get('剧本/分集.json', b'{}'))
        rev = max(int(book.get('rev') or 0), int(old_book.get('rev') or 0)) + 1
        book.update(rev=rev, anchor_rev=0, planning_version=version_id)
        changed_ids = (list(dict.fromkeys(meta['changes']['changed'] + meta['changes']['added'] + meta['changes']['removed']))
                       if not restoring and meta.get('changes') else '*')
        edits = {**(book.get('script_edit_revisions') or {}), **(old_book.get('script_edit_revisions') or {})}
        edits[str(rev)] = changed_ids
        book['script_edit_revisions'] = edits
        for ep in book.get('episodes') or []:
            if changed_ids == '*' or ep['id'] in changed_ids:
                ep['script_edit_rev'] = rev
        contents['剧本/分集.json'] = _json_bytes(book)
        for name in ('剧本/大纲.json', '剧本/埋线.json', '素材/人物.json', '素材/场景.json', '素材/道具.json'):
            if name in contents:
                doc = json.loads(contents[name])
                doc['anchor_rev'] = 0
                doc.pop('anchor_fingerprint', None)
                contents[name] = _json_bytes(doc)
        try:
            for name in sorted(set(before) | set(contents)):
                path = project / name
                versions.snapshot(str(path))
                if name in contents:
                    _write_bytes(path, contents[name])
                elif path.is_file():
                    path.unlink()
            if not restoring:
                meta['status'] = 'adopted'
                _save_meta(project, meta)
        except BaseException:
            for name in sorted(set(before) | set(contents)):
                path = project / name
                if name in before:
                    _write_bytes(path, before[name])
                elif path.is_file():
                    path.unlink()
            raise
    return {'ok': True, 'changed': True, 'version_id': version_id, 'history_id': history_id,
            'revision': current_revision(project), 'requires_review': True}


def adopt(project, version_id, revision):
    return _replace(project, version_id, revision, restoring=False)


def restore(project, version_id, revision):
    return _replace(project, version_id, revision, restoring=True)
