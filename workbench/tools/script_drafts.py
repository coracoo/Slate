# -*- coding: utf-8 -*-
"""分集正文候选：后台批量生成、差异预览、原子批量采用。"""
import argparse
import copy
import json
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'previs_system/tools'))
import project_store
import versions
import episode_editor
import story_planning_versions as planning
from error_utils import scrub_error

VALIDATION_VERSION = 2

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass


def _path(project, batch):
    if not re.fullmatch(r'draft-[a-f0-9]{16}', str(batch)):
        raise ValueError('正文批次号无效')
    return Path(project) / '剧本/.versions/正文候选' / batch / 'batch.json'


def _card(row):
    return {k: v for k, v in row.items() if k not in
            ('text', 'char_start', 'char_end', 'text_revision', 'script_edit_rev', 'edited_at')}


def _context(project):
    import story_units
    return story_units.anchor_fingerprint(project)


def create(project, episodes, instructions='', vendor=None):
    from preproduction_flow import require_expansion_ready
    if not isinstance(episodes, list) or not episodes or any(not isinstance(e, str) for e in episodes):
        raise ValueError('请选择需要生成的分集')
    if len(set(episodes)) != len(episodes):
        raise ValueError('分集不能重复')
    if not isinstance(instructions, str) or len(instructions) > 8000:
        raise ValueError('修改要求须为不超过 8000 字的文本')
    with planning._locks(project, planning.FILES):
        for existing in (Path(project) / '剧本/.versions/正文候选').glob('draft-*/batch.json'):
            if project_store._read_unlocked(existing)[0]['status'] in ('queued', 'running'):
                raise ValueError('当前项目已有正文生成批次，请等待其结束')
        book, _ = project_store._read_unlocked(Path(project) / '剧本/分集.json')
        rows = {r['id']: r for r in book.get('episodes', [])}
        if any(e not in rows for e in episodes):
            raise ValueError('选择包含不存在的分集')
        batch = 'draft-' + uuid.uuid4().hex[:16]
        path = _path(project, batch)
        inputs = path.parent / '输入'
        for name, content in planning._collect(project).items():
            planning._write_bytes(inputs / name, content)
        require_expansion_ready(str(inputs))
        doc = {'id': batch, 'status': 'queued', 'created_at': datetime.now().isoformat(timespec='microseconds'),
               'instructions': instructions.strip(), 'vendor': vendor, 'context': _context(project), 'context_version': 2,
               'items': [{'episode': e, 'title': rows[e].get('title', ''), 'status': 'queued',
                          'before': episode_editor.episode_text(project, book, rows[e]),
                          'card': _card(rows[e]), 'after': '', 'error': ''} for e in episodes]}
        planning._write_bytes(path, planning._json_bytes(doc))
    return doc


def _update(path, mutate):
    return project_store.update_json(path, mutate)[0]


def validate_candidate(source, episode, text):
    import story_units
    report = story_units.gap_report(str(source), episode, text)
    marks = re.findall(r'【缺口[:：]\s*([^】]+)】', text)
    report = {**report, 'reported_gaps': marks}
    details = []
    for key, label in (('missing_scenes', '未登记场景'), ('missing_speakers', '未登记说话人')):
        names = [str(row['name']) for row in report.get(key) or []]
        if names:
            details.append(label + '：' + '、'.join(names))
    if marks:
        details.append('模型自报缺口：' + '；'.join(marks))
    report['blocking'] = bool(report.get('blocking') or marks)
    return report, '；'.join(details) if report['blocking'] else ''


def revalidate(project, batch):
    """重新校验被规则拦截的原稿；不重发模型请求，不采用正文。"""
    path = _path(project, batch)
    source = path.parent / '输入'
    def refresh(doc):
        if doc['status'] in ('queued', 'running'):
            return
        changed = False
        for item in doc['items']:
            validation_failure = item.get('failure_kind') == 'validation' or item.get('error') == '候选涉及未登记实体或规划冲突，请修改角色/场景设定后再生成'
            if item['status'] != 'failed' or not validation_failure or not item.get('after'):
                continue
            report, error = validate_candidate(source, item['episode'], item['after'])
            item.update(validation=report, error=error, failure_kind='validation' if error else '',
                        status='failed' if error else 'ready', validation_rechecked=True,
                        validation_version=VALIDATION_VERSION)
            changed = True
        if changed:
            doc['status'] = 'partial' if any(i['status'] == 'failed' for i in doc['items']) else 'ready'
    return _update(path, refresh)


def bind_job(project, batch, job_id):
    def bind(doc):
        if doc.get('job_id'):
            doc['job_ids'] = sorted(set(doc.get('job_ids', []) + [doc['job_id']]))
        doc['job_id'] = int(job_id)
    _update(_path(project, batch), bind)


def interrupt(project, batch, message):
    def stop(doc):
        if doc['status'] not in ('queued', 'running'):
            return
        doc['status'] = 'partial'
        for item in doc['items']:
            if item['status'] in ('queued', 'running'):
                item.update(status='failed', error=message)
                for attempt in item.get('repairs', []):
                    if attempt['status'] == 'running':
                        attempt.update(status='failed', error=message,
                                       finished_at=datetime.now().isoformat(timespec='seconds'))
    _update(_path(project, batch), stop)


def reconcile(project, job_record):
    """任务管理器确认结束后，收敛未写完的批次；不自动重发模型请求。"""
    for path in (Path(project) / '剧本/.versions/正文候选').glob('draft-*/batch.json'):
        doc, _ = project_store.read_json(path)
        if doc['status'] not in ('queued', 'running'):
            continue
        job = job_record(doc['job_id']) if doc.get('job_id') else None
        elapsed = (datetime.now() - datetime.fromisoformat(doc.get('task_requested_at') or doc['created_at'])).total_seconds()
        if job and (job.get('status') == 'running' or job.get('process_alive')):
            continue
        if job or elapsed > 120:
            interrupt(project, doc['id'], '后台任务已结束或失联；未确认的结果不自动重发，请检查日志后重新选择生成')


def _repair_text(path, source, doc, episode, text, report, repair=None, instructions='', automatic=False):
    from creation_pipeline import repair_episode_text
    ep = episode['id']
    entry = {'id': uuid.uuid4().hex, 'status': 'running', 'automatic': automatic,
             'before': text, 'instructions': instructions, 'created_at': datetime.now().isoformat(timespec='seconds')}
    def start(value):
        item = next(i for i in value['items'] if i['episode'] == ep)
        item.update(after=text, failure_kind='validation', validation=report,
                    validation_version=VALIDATION_VERSION)
        item.setdefault('repair_original', text)
        item.setdefault('repairs', []).append(entry)
    _update(path, start)
    print(f'[文本修正] {ep}：' + ('自动修正一次' if automatic else '按确认要求修正'), flush=True)
    proposed, error = '', ''
    try:
        proposed = (repair or repair_episode_text)(str(source), doc.get('vendor'), episode, text, report, instructions)
        if not isinstance(proposed, str) or not proposed.strip():
            raise ValueError('修正模型返回空正文')
        checked, error = validate_candidate(source, ep, proposed)
        if not error:
            text, report = proposed, checked
    except Exception as exc:
        error = scrub_error(exc)
    def finish(value):
        item = next(i for i in value['items'] if i['episode'] == ep)
        record = next(r for r in item['repairs'] if r['id'] == entry['id'])
        record.update(status='failed' if error else 'done', proposed=proposed, error=error,
                      finished_at=datetime.now().isoformat(timespec='seconds'))
    _update(path, finish)
    print(f'[{"修正未通过" if error else "修正完成"}] {ep}' + (f'：{error}；原稿保留' if error else ''), flush=True)
    return text, report, error


def queue_repair(project, batch, episodes, instructions=''):
    if not isinstance(episodes, list) or not episodes or any(not isinstance(e, str) for e in episodes) or len(set(episodes)) != len(episodes):
        raise ValueError('请选择需要修正的分集，不能重复')
    if not isinstance(instructions, str) or len(instructions) > 8000:
        raise ValueError('修正要求须为不超过 8000 字的文本')
    path = _path(project, batch)
    with planning._locks(project, (*planning.FILES, str(path.relative_to(project)))):
        doc, _ = project_store._read_unlocked(path)
        for existing in path.parent.parent.glob('draft-*/batch.json'):
            current = doc if existing == path else project_store._read_unlocked(existing)[0]
            if current['status'] in ('queued', 'running'):
                raise ValueError('当前项目已有正文任务正在处理中')
        baseline = doc['context'] if doc.get('context_version') == 2 else _context(path.parent / '输入')
        if _context(project) != baseline:
            raise project_store.RevisionConflict('剧情依据已变化，请按最新规划生成受影响分集；旧稿保留')
        _require_latest(project, batch, episodes)
        selected = [i for i in doc['items'] if i['episode'] in episodes]
        if len(selected) != len(episodes) or any(i['status'] != 'failed' or not i.get('after') or
                not (i.get('failure_kind') == 'validation' or i.get('error') == '候选涉及未登记实体或规划冲突，请修改角色/场景设定后再生成') for i in selected):
            raise ValueError('只支持修正已保存原稿的文本校验失败项')
        book, _ = project_store._read_unlocked(Path(project) / '剧本/分集.json')
        current_rows = {r['id']: r for r in book['episodes']}
        for i in selected:
            row = current_rows.get(i['episode'])
            if row is None or _card(row) != i['card'] or episode_editor.episode_text(project, book, row) != i['before']:
                raise project_store.RevisionConflict(f'{i["episode"]} 正文已更新，旧候选保留；请按最新正文生成')
        doc.update(status='queued', task_requested_at=datetime.now().isoformat(timespec='seconds'),
                   repair_request={'episodes': episodes, 'instructions': instructions.strip()})
        if doc.get('job_id'):
            doc['job_ids'] = sorted(set(doc.get('job_ids', []) + [doc.pop('job_id')]))
        for i in selected:
            i['status'] = 'queued'
        planning._write_bytes(path, planning._json_bytes(doc))
    return doc


def run_repair(project, batch, repair=None):
    from llm_openai import set_billing_project
    path = _path(project, batch)
    def claim(doc):
        if doc['status'] != 'queued' or not doc.get('repair_request'):
            raise ValueError('修正任务已执行，禁止重复提交')
        doc['status'] = 'running'
    doc = _update(path, claim)
    source = path.parent / '输入'
    request = doc['repair_request']
    failures = 0
    try:
        set_billing_project(Path(project).name)
        book, _ = project_store.read_json(source / '剧本/分集.json')
        rows = {r['id']: r for r in book['episodes']}
        for item in doc['items']:
            ep = item['episode']
            if ep not in request['episodes']:
                continue
            _update(path, lambda d: next(i for i in d['items'] if i['episode'] == ep).update(status='running'))
            text = item['after']
            report, error = validate_candidate(source, ep, text)
            if error:
                text, report, repair_error = _repair_text(path, source, doc, rows[ep], text, report,
                    repair, request['instructions'])
                _, error = validate_candidate(source, ep, text)
                if repair_error and not error:
                    error = repair_error
            else:
                print(f'[复核完成] {ep}：原稿已通过，无需再请求模型', flush=True)
            failures += bool(error)
            _update(path, lambda d: next(i for i in d['items'] if i['episode'] == ep).update(
                status='failed' if error else 'ready', after=text, error=error, validation=report,
                validation_version=VALIDATION_VERSION, failure_kind='validation' if error else ''))
    finally:
        _finish_batch(path)
    if failures:
        raise ValueError(f'{failures} 集仍需处理；已停止自动修正，原稿和修正记录已保留')
    return project_store.read_json(path)[0]


def _finish_batch(path):
    def finish(doc):
        for row in doc['items']:
            if row['status'] in ('queued', 'running'):
                row.update(status='failed', error='任务中断，原稿保留；请检查日志后继续处理')
                for attempt in row.get('repairs', []):
                    if attempt['status'] == 'running':
                        attempt.update(status='failed', error=row['error'],
                                       finished_at=datetime.now().isoformat(timespec='seconds'))
        doc['status'] = 'partial' if any(i['status'] == 'failed' for i in doc['items']) else 'ready'
    _update(path, finish)


def run(project, batch, generate=None, repair=None):
    from creation_pipeline import generate_episode_text
    from llm_openai import set_billing_project
    import story_units
    from script_repository import load_script
    path = _path(project, batch)
    def claim(doc):
        if doc['status'] != 'queued':
            raise ValueError('此批次已执行，禁止重复提交')
        doc['status'] = 'running'
    doc = _update(path, claim)
    source = path.parent / '输入'
    failed = 0
    try:
        set_billing_project(Path(project).name)
        book, _ = project_store.read_json(source / '剧本/分集.json')
        rows = book['episodes']
        idea_path = source / '剧本/构想.txt'
        idea = idea_path.read_text(encoding='utf-8').strip() if idea_path.exists() else ''
        idea = idea or load_script(str(source))
        generator = generate or generate_episode_text
        for item in doc['items']:
            ep = item['episode']
            def active(value):
                next(r for r in value['items'] if r['episode'] == ep)['status'] = 'running'
            _update(path, active)
            result, error, failure_kind, validation = '', '', 'generation', {}
            try:
                idx = next(i for i, r in enumerate(rows) if r['id'] == ep)
                row = copy.deepcopy(rows[idx])
                row['text'] = item['before']
                result = generator(str(source), doc['vendor'], idea, row,
                    rows[idx - 1].get('summary') if idx else None, doc['instructions'])
                if not isinstance(result, str) or not result.strip():
                    raise ValueError('模型返回空正文')
                failure_kind = 'validation'
                validation, validation_error = validate_candidate(source, ep, result)
                if validation_error:
                    result, validation, _ = _repair_text(path, source, doc, row, result, validation,
                        repair, automatic=True)
                    validation, validation_error = validate_candidate(source, ep, result)
                    if validation_error:
                        raise ValueError(validation_error)
            except Exception as exc:
                error = scrub_error(exc)
                failed += 1
            def finish(value):
                target = next(r for r in value['items'] if r['episode'] == ep)
                target.update(status='failed' if error else 'ready', after=result, error=error,
                              failure_kind=failure_kind if error else '', validation=validation,
                              validation_version=VALIDATION_VERSION)
            _update(path, finish)
            print(f'[{"失败" if error else "候选完成"}] {ep}' + (f'：{error}' if error else ''), flush=True)
    finally:
        _finish_batch(path)
    if failed:
        raise ValueError(f'{failed} 集生成失败；其余候选已保留，可单独审核采用')
    return project_store.read_json(path)[0]


def _latest_batches(project):
    docs = [project_store._read_unlocked(path)[0] for path in
            (Path(project) / '剧本/.versions/正文候选').glob('draft-*/batch.json')]
    docs.sort(key=lambda row: (row.get('created_at', ''), row['id']), reverse=True)
    latest = {}
    for doc in docs:
        for item in doc['items']:
            latest.setdefault(item['episode'], doc['id'])
    return docs, latest


def _require_latest(project, batch, episodes):
    _, latest = _latest_batches(project)
    outdated = [ep for ep in episodes if latest.get(ep) != batch]
    if outdated:
        raise project_store.RevisionConflict('已有更新候选，请打开当前待审核稿：' + '、'.join(outdated))


def list_batches(project, *, include_history=True):
    result = []
    root = Path(project) / '剧本/.versions/正文候选'
    try:
        book, _ = project_store.read_json(Path(project) / '剧本/分集.json')
    except FileNotFoundError:
        book = {}
    docs, latest = _latest_batches(project)
    rows = {r['id']: r for r in book.get('episodes', [])}
    context = _context(project)
    for doc in docs:
        path = _path(project, doc['id'])
        baseline = doc['context'] if doc.get('context_version') == 2 else _context(path.parent / '输入')
        if baseline == context and doc['status'] == 'partial' and any(latest.get(i['episode']) == doc['id'] and i['status'] == 'failed' and i.get('after') and i.get('validation_version') != VALIDATION_VERSION and
                (i.get('failure_kind') == 'validation' or i.get('error') == '候选涉及未登记实体或规划冲突，请修改角色/场景设定后再生成') for i in doc['items']):
            doc = revalidate(project, doc['id'])
        for item in doc['items']:
            if item['episode'] in book.get('adopted_draft_batches', {}).get(doc['id'], []):
                item['status'] = 'adopted'
            elif item['status'] not in ('queued', 'running'):
                row = rows.get(item['episode'])
                reason = ('已有更新候选' if latest.get(item['episode']) != doc['id'] else
                          '剧情设定已变化' if context != baseline else
                          '正式正文或分集规划已更新' if row is None or _card(row) != item['card'] or
                          episode_editor.episode_text(project, book, row) != item['before'] else '')
                if reason:
                    item.update(status='stale', inactive_reason=reason)
        statuses = {i['status'] for i in doc['items']}
        doc['active'] = bool(statuses & {'ready', 'failed', 'queued', 'running'})
        if not doc['active']:
            doc['status'] = 'adopted' if statuses == {'adopted'} else 'stale'
        elif doc['status'] not in ('queued', 'running'):
            doc['status'] = 'ready' if statuses == {'ready'} else 'partial'
        if include_history or doc['active']:
            result.append({k: v for k, v in doc.items() if k not in ('context', 'vendor')})
    return result


def _adopt_book(project, batch, episodes, *, reviewed=None):
    if not isinstance(episodes, list) or not episodes or any(not isinstance(e, str) for e in episodes):
        raise ValueError('请选择待采用的正文')
    if len(set(episodes)) != len(episodes):
        raise ValueError('分集不能重复')
    path = _path(project, batch)
    book_path = Path(project) / '剧本/分集.json'
    warnings = []
    with planning._locks(project, (*planning.FILES, str(path.relative_to(project)))):
        doc, _ = project_store._read_unlocked(path)
        book, revision = project_store._read_unlocked(book_path)
        received = book.get('adopted_draft_batches', {}).get(batch, [])
        if all(ep in received for ep in episodes):
            return {'ok': True, 'revision': revision, 'adopted': episodes, 'warnings': []}
        if any(ep in received for ep in episodes):
            raise ValueError('选中项包含已采用正文，请刷新后重新选择')
        _require_latest(project, batch, episodes)
        if doc['status'] in ('queued', 'running'):
            raise ValueError('批次尚未结束')
        chosen = [r for r in doc['items'] if r['episode'] in episodes]
        if len(chosen) != len(episodes) or any(r['status'] != 'ready' for r in chosen):
            raise ValueError('只能采用本批尚未采用的成功候选')
        if reviewed is not None:
            if not isinstance(reviewed, dict) or any(not isinstance(reviewed.get(ep), dict) for ep in episodes):
                raise ValueError('请提供每个选中稿的审核内容')
            for item in chosen:
                if any(reviewed[item['episode']].get(key) != item.get(key) for key in ('before', 'after')):
                    raise project_store.RevisionConflict('候选在审核期间已更新，请重新打开差异对比')
        baseline = doc['context'] if doc.get('context_version') == 2 else _context(path.parent / '输入')
        if _context(project) != baseline:
            raise project_store.RevisionConflict('候选的剧情依据已变化（构想、制作规则、分集规划或人物小传）；候选已保留，请在生成与审核中重新生成受影响分集')
        rows = {r['id']: r for r in book['episodes']}
        source = path.parent / '输入'
        idea_path = source / '剧本/构想.txt'
        if not idea_path.exists() or not idea_path.read_text(encoding='utf-8').strip():
            baseline, _ = project_store.read_json(source / '剧本/分集.json')
            for original in baseline['episodes']:
                ep = original['id']
                prior = episode_editor.episode_text(source, baseline, original)
                if ep in received:
                    prior = next(r['after'] for r in doc['items'] if r['episode'] == ep)
                if ep not in rows or episode_editor.episode_text(project, book, rows[ep]) != prior:
                    raise project_store.RevisionConflict('作为故事依据的正文已变化，请重新生成候选')
        for item in chosen:
            current = rows.get(item['episode'])
            if current is None or _card(current) != item['card'] or episode_editor.episode_text(project, book, current) != item['before']:
                raise project_store.RevisionConflict(f'{item["episode"]} 已更新，候选保留，请重新对照')
        def apply(value):
            for row in value['episodes']:
                if not row.get('text'):
                    row['text'] = episode_editor.episode_text(project, value, row)
            value['mode'] = 'generated'
            value['rev'] = int(value.get('rev') or 0) + 1
            edits = value.setdefault('script_edit_revisions', {})
            edits[str(value['rev'])] = episodes
            value['script_edit_revisions'] = dict(list(edits.items())[-100:])
            for item in chosen:
                row = next(r for r in value['episodes'] if r['id'] == item['episode'])
                row.update(text=item['after'], char_start=0, char_end=len(item['after']),
                    text_revision=episode_editor.text_hash(item['after']), script_edit_rev=value['rev'],
                    edited_at=datetime.now().isoformat(timespec='seconds'))
                row.pop('planning_review_required', None)
            value.setdefault('adopted_draft_batches', {})[batch] = sorted(set(
                value.get('adopted_draft_batches', {}).get(batch, []) + episodes))
        apply(book)
        new_revision = project_store._atomic_write(book_path, book, versions.snapshot)
        for item in chosen:
            item['status'] = 'adopted'
        doc['status'] = 'adopted' if all(r['status'] == 'adopted' for r in doc['items']) else 'partial'
        try:
            planning._write_bytes(path, planning._json_bytes(doc))
        except OSError as exc:
            warnings.append('正文已采用，批次显示将按采用记录恢复：' + scrub_error(exc))
    return {'ok': True, 'revision': new_revision, 'adopted': episodes, 'warnings': warnings}


def adopt(project, batch, episodes, *, reviewed=None):
    result = _adopt_book(project, batch, episodes, reviewed=reviewed)
    for ep in episodes:
        try:
            episode_editor.sync_episode_copy(project, ep)
        except (OSError, ValueError, project_store.RevisionConflict) as exc:
            result['warnings'].append('正文已采用，文本副本刷新失败：' + scrub_error(exc))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('project')
    parser.add_argument('batch')
    parser.add_argument('--repair', action='store_true')
    args = parser.parse_args()
    try:
        (run_repair if args.repair else run)(args.project, args.batch)
    except Exception as exc:
        print('[批次结束] ' + scrub_error(exc), flush=True)
        sys.exit(1)
