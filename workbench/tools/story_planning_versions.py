# -*- coding: utf-8 -*-
"""全剧修订工作候选：隔离生成、差异审核、一次采用；正文历史独立保留。"""
import hashlib
import copy
import json
import os
import re
import sys
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / 'previs_system/tools'
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))
import project_store
import story_units
import versions
import asset_repository
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
    return Path(project) / '剧本/.work/规划'


def _directory(project, version_id):
    if not re.fullmatch(r'(?:plan|history)-[a-f0-9]{16}', str(version_id)):
        raise ValueError('规划版本号不合法')
    # 旧完整存档仅供读取正文历史，不迁移、不删除，也不能整体恢复。
    if str(version_id).startswith('history-'):
        return Path(project) / '剧本/.versions/规划' / version_id
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
    raise ValueError('规划整体历史已停用；已保存正文可在正文历史中查看')


def _locks(project, names):
    # 规划和素材提交共用事务锁：先锁恢复日志，再按顺序锁全部文档。
    return asset_repository.locks(project, set(FILES) | set(names))


def list_versions(project):
    """旧规划列表接口保持空列表；日常只审核当前工作候选。"""
    return []


def _current_meta(project):
    candidates = []
    for path in _root(project).glob('plan-*/version.json'):
        try:
            meta = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if meta.get('id') == path.parent.name:
            candidates.append(meta)
    return max(candidates, key=lambda row: (row.get('created_at', ''), row['id']), default=None)


def _candidate_summary(project, meta, revision):
    result = {key: meta.get(key) for key in ('id', 'status', 'label', 'created_at', 'error',
                                            'revision_mode', 'instructions', 'changes')}
    current = _current_meta(project)
    valid = (current is not None and current['id'] == meta['id'] and
             meta.get('base_revision') == revision and meta.get('revision_mode') in ('extend', 'rewrite'))
    result.update(can_adopt=bool(valid and meta['status'] == 'ready'),
                  can_resume=bool(valid and meta['status'] == 'failed' and
                                  (version_project(project, meta['id']) / '剧本/修订上下文.json').is_file()))
    return result


def _bind_runtime_job(meta):
    """工作台后台进程绑定当前尝试；CLI 续跑不沿用已经结束的任务号。"""
    meta.pop('job_id', None)
    meta.pop('job_attempt_id', None)
    meta['runner_pid'] = os.getpid()
    try:
        job_id = int(os.environ.get('SLATE_JOB_ID') or '')
    except ValueError:
        return
    meta['job_id'] = job_id
    meta['job_attempt_id'] = str(os.environ.get('SLATE_JOB_ATTEMPT_ID') or '')


def _runner_alive(pid):
    """只读确认进程存活；不存在返 False，访问被拒或无法确认返 None。"""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return None
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False if ctypes.get_last_error() == 87 else None
        try:
            code = wintypes.DWORD()
            return code.value == 259 if kernel.GetExitCodeProcess(handle, ctypes.byref(code)) else None
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return None
    return True


def current_candidate(project, job_lookup=None):
    """只返回最近一次工作候选，旧工作稿与整体存档不组成版本选择器。"""
    revision = current_revision(project)
    meta = _current_meta(project)
    if meta and meta.get('status') == 'generating' and meta.get('job_id') is not None and job_lookup is not None:
        job = job_lookup(meta['job_id'])
        if not job or not (job.get('status') == 'running' or job.get('process_alive')):
            alive = _runner_alive(meta.get('runner_pid'))
            name = (_directory(project, meta['id']) / 'version.json').relative_to(Path(project)).as_posix()
            with _locks(project, [name]):
                latest = _meta(project, meta['id'])
                if latest.get('status') == 'generating' and latest.get('job_id') == meta['job_id'] and latest.get('job_attempt_id') == meta.get('job_attempt_id'):
                    if alive is False:
                        latest.update(status='failed', error='后台生成已中断或失联；工作稿已保留，请核对后继续生成',
                                      interrupted_at=datetime.now().isoformat(timespec='seconds'))
                        _save_meta(project, latest)
                    else:
                        message = '后台任务记录已结束，但生成进程仍存活或无法确认；请核查任务状态，暂不能重复生成'
                        if latest.get('error') != message:
                            latest['error'] = message
                            _save_meta(project, latest)
                meta = latest
                revision = current_revision(project)
    return {'ok': True, 'candidate': _candidate_summary(project, meta, revision) if meta else None,
            'revision': revision}


def preview_candidate(project, version_id):
    """候选与当前正式稿的逐文件差异；采用仍须复核正式稿版本。"""
    current = _current_meta(project)
    if current is None or current['id'] != version_id:
        raise ValueError('请选择当前工作候选；旧规划存档不再提供整体采用或恢复')
    meta = _meta(project, version_id)
    workspace = version_project(project, version_id)
    with _locks(project, _collect(project)):
        before, after = _collect(project), _collect(workspace)
        revision = _fingerprint(before)
        groups = [{'label': name, 'before': before.get(name, b'').decode('utf-8'),
                   'after': after.get(name, b'').decode('utf-8')}
                  for name in sorted(set(before) | set(after)) if before.get(name) != after.get(name)]
    return {'ok': True, 'candidate': _candidate_summary(project, meta, revision),
            'revision': revision, 'groups': groups}


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
            value = sorted({_canon_ref(item) for item in value})
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
        if not same and prior is not None and prior.get('text'):
            # 规划改写替换了本集框架：旧正文保留并标记待人工核对——
            # 新正文未产出前不能静默丢掉已审核文本（episode_editor 据此拦改写）。
            row['text'] = prior['text']
            row['planning_review_required'] = True
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
        # 同请求续跑：上次 generate 失败后，同一 base_revision+目标集数+改写要求的请求
        # 沿用失败候选的 workspace 进度（runner 只补未完成段），不再新建空候选。
        prior = _current_meta(project)
        if prior and prior.get('status') == 'generating':
            raise ValueError('当前项目已有规划任务，请等待任务结束后再修改')
        if prior and not (prior.get('status') == 'failed'
                          and prior.get('base_revision') == base_revision
                          and prior.get('target_episodes') == target
                          and prior.get('revision_mode') == revision_mode
                          and prior.get('instructions') == instructions
                          and prior.get('source') == source
                          and prior.get('idea_hash') == hashlib.sha256(idea_text.encode('utf-8')).hexdigest()
                          and (_directory(project, prior['id']) / '内容/剧本/修订上下文.json').is_file()):
            prior = None
        if prior:
            meta = prior
            meta.update(status='generating', resumed_at=datetime.now().isoformat(timespec='seconds'))
        else:
            version_id = 'plan-' + uuid.uuid4().hex[:16]
            label = '扩写分集' if revision_mode == 'extend' else '改写分集'
            meta = {'id': version_id, 'status': 'generating', 'label': f'{label} · {len(old_episodes)} → {target} 集',
                    'created_at': datetime.now().isoformat(timespec='microseconds'), 'base_revision': base_revision,
                    'source': source, 'target_episodes': target,
                    'idea_hash': hashlib.sha256(idea_text.encode('utf-8')).hexdigest(),
                    'revision_mode': revision_mode, 'instructions': instructions}
        _bind_runtime_job(meta)
        _save_meta(project, meta)
    # 远端调用不占正式文档锁；采用时复核生成前的基线。
    if prior:
        print(f"[继续修订] 沿用失败候选 {prior['id']}（{prior['target_episodes']} 集）；只补未完成部分。", flush=True)
        return _run_candidate(project, meta, runner)
    version_id = meta['id']
    workspace = _directory(project, version_id) / '内容'
    label = '扩写分集' if revision_mode == 'extend' else '改写分集'
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
        print(f'[{label}] 既有 {len(old_episodes)} 集作为剧情依据；当前内容保留，工作候选 {version_id}，目标 {target} 集。', flush=True)
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
        import traceback; traceback.print_exc()
        meta.update(status='failed', error=scrub_error(exc))
        _save_meta(project, meta)
        print(f'[修订失败] {meta["error"]}；候选进度已保存，可继续生成。', flush=True)
        return {'ok': False, 'version_id': meta['id'], 'incomplete': [meta['error']]}


def resume_request(project, version_id):
    meta = _meta(project, version_id)
    current = _current_meta(project)
    if current is None or current['id'] != version_id:
        raise ValueError('只支持继续当前工作候选，请按最新内容重新修订')
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
        _bind_runtime_job(meta)
        _save_meta(project, meta)
    print(f'[继续修订] 沿用候选 {version_id}，目标 {meta["target_episodes"]} 集；只补未完成部分。', flush=True)
    return _run_candidate(project, meta, runner)


def _replace(project, version_id, revision, *, restoring):
    if restoring:
        raise ValueError('规划整体恢复已停用；已保存正文可在正文历史中查看')
    if not re.fullmatch(r'plan-[a-f0-9]{16}', str(version_id)):
        raise ValueError('仅当前工作候选可采用，旧规划存档不能整体恢复')
    project = Path(project)
    workspace = version_project(project, version_id)
    meta_name = (_directory(project, version_id) / 'version.json').relative_to(project).as_posix()
    with _locks(project, set(_collect(project)) | set(_collect(workspace)) | {meta_name}):
        meta = _meta(project, version_id)
        current = story_units.load_units(project)['episodes_doc']
        if current.get('planning_revision_id') == version_id and meta['status'] == 'applied':
            return {'ok': True, 'changed': False, 'version_id': version_id, 'revision': current_revision(project)}
        latest = _current_meta(project)
        if latest is None or latest['id'] != version_id:
            raise ValueError('请选择当前工作候选，旧稿不能覆盖当前规划')
        if meta['status'] != 'ready':
            raise ValueError('工作候选未完成，不能采用')
        if meta.get('revision_mode') not in ('extend', 'rewrite'):
            raise ValueError('候选缺少修订基线，请重新生成')
        contents = _collect(workspace)
        if _fingerprint(contents) != meta.get('fingerprint'):
            raise ValueError('工作候选文件已变化，请重新生成候选')
        before = _collect(project)
        current_revision_value = _fingerprint(before)
        if not revision or revision != current_revision_value:
            raise project_store.RevisionConflict('当前规划已变化，请刷新后重试')
        if meta['base_revision'] != current_revision_value:
            raise project_store.RevisionConflict('候选生成后当前规划已修改，请重新规划；当前修改已保留')
        book = json.loads(contents.get('剧本/分集.json', b'{}'))
        old_book = json.loads(before.get('剧本/分集.json', b'{}'))
        rev = max(int(book.get('rev') or 0), int(old_book.get('rev') or 0)) + 1
        book.update(rev=rev, anchor_rev=0, planning_revision_id=version_id)
        book.pop('planning_version', None)
        changed_ids = (list(dict.fromkeys(meta['changes']['changed'] + meta['changes']['added'] + meta['changes']['removed']))
                       if meta.get('changes') else '*')
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
        # 正文 JSON 与文本副本在统一事务中快照，工作候选记录随正式稿一起采用。
        documents = {name: contents.get(name) for name in set(before) | set(contents)}
        meta['status'] = 'applied'
        meta.pop('error', None)
        documents[meta_name] = meta
        result = asset_repository.commit_locked(project, documents, source='planning',
                                                operation_id='planning-' + version_id)
        asset_repository.require_applied(result)
    return {'ok': True, 'changed': True, 'version_id': version_id,
            'revision': current_revision(project), 'requires_review': True}


def adopt(project, version_id, revision):
    return _replace(project, version_id, revision, restoring=False)


def restore(project, version_id, revision):
    return _replace(project, version_id, revision, restoring=True)
