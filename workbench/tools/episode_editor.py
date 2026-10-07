# -*- coding: utf-8 -*-
"""单集正文编辑：分集 JSON 为权威源，覆写快照与后续复核范围。"""
import hashlib
import json
import sys
import os
import tempfile
from datetime import datetime
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / 'previs_system/tools'
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))
import project_store
import versions
import script_repository
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass


def text_hash(text):
    return hashlib.sha256(str(text or '').encode('utf-8')).hexdigest()


def episode_text(project, book, row):
    """正文优先使用分集字段；旧导入分集按原稿字符区间读取。"""
    if row.get('text'):
        return str(row['text'])
    if book.get('mode') == 'imported' and row.get('char_end') is not None:
        return script_repository.load_script(str(project))[int(row.get('char_start') or 0):int(row['char_end'])]
    if book.get('mode') == 'imported' and len(book.get('episodes') or []) == 1:
        return script_repository.load_script(str(project))
    return ''


def board_episode(name, board):
    return str(board.get('episode_id') or (name[3:-5] if name.startswith('剧本_') and name.endswith('.json') else ''))


def boards_for_episode(project, episode):
    for path in sorted((Path(project) / '分镜').glob('*.json')):
        try:
            board = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if isinstance(board, dict) and board_episode(path.name, board) in (episode, '全本'):
            yield path, board


def needs_review(project, episode, row, name, board, book=None):
    """单集指纹优先；旧分镜使用本集改稿修订号，不牵连其他集。"""
    if board.get('script_source_hash') and board_episode(name, board) == episode:
        return board['script_source_hash'] != text_hash(episode_text(project, book or {}, row))
    source_rev = int(board.get('script_rev') or 0)
    if int(row.get('script_edit_rev') or 0) > source_rev:
        return True
    if book is None:
        book = project_store.read_json(Path(project) / '剧本/分集.json')[0]
    latest = int(book.get('rev') or 0)
    edits = book.get('script_edit_revisions') or {}
    if latest - source_rev > 100:
        return True
    for rev in range(source_rev + 1, latest + 1):
        affected = edits.get(str(rev))
        if affected is None or affected == '*':
            return True
        if isinstance(affected, list):
            if episode in affected:
                return True
        elif affected == episode:
            return True
    return False


def affected_outputs(project, episode, row):
    boards = []
    for path, board in boards_for_episode(project, episode):
        if not needs_review(project, episode, row, path.name, board):
            continue
        shots = [s for s in board.get('shots', []) if isinstance(s, dict)]
        units = [u for u in board.get('video_units', []) if isinstance(u, dict)]
        boards.append({'name': path.name, 'shots': [s.get('id') for s in shots],
                       'units': [u.get('id') for u in units],
                       'adopted': sum(bool(s.get('keyframe')) + bool(s.get('video_binding')) for s in shots)
                                  + sum(bool(u.get('video_binding')) for u in units)})
    refs = list(dict.fromkeys(str(ref) for key in ('cast_refs', 'scene_refs', 'key_asset_refs')
                             for ref in row.get(key) or [] if ref))
    return {'boards': boards, 'asset_refs': refs}


def _sync_text(path, text):
    """刷新可再生的单集文本副本；正式正文以分集 JSON 为准。"""
    versions.snapshot(str(path))
    fd, temporary = tempfile.mkstemp(dir=str(path.parent), prefix='.episode-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def sync_episode_copy(project, episode, seed_script=False):
    """与权威文件同锁刷新文本副本，使用最新正文而非任务启动时的旧稿。"""
    path = Path(project) / '剧本/分集.json'
    with project_store._exclusive(path):
        latest, _ = project_store._read_unlocked(path)
        row = next(r for r in latest['episodes'] if r['id'] == episode)
        text = episode_text(project, latest, row)
        _sync_text(path.parent / f'分集剧本_{episode}.txt', text)
        if seed_script and not (path.parent / '剧本.txt').exists():
            _sync_text(path.parent / '剧本.txt', script_repository.load_script(str(project)))
        return text


def save_episode(project, episode, text, revision):
    if not isinstance(text, str) or not text.strip():
        raise ValueError('单集正文不能为空')
    if not isinstance(revision, str) or not revision:
        raise ValueError('请重新读取正文后保存')
    path = Path(project) / '剧本/分集.json'
    changed = False
    def mutate(book):
        nonlocal changed
        row = next((r for r in book.get('episodes') or [] if isinstance(r, dict) and r.get('id') == episode), None)
        if row is None:
            raise ValueError('分集不存在，请刷新后重试')
        if episode_text(project, book, row) == text and not row.get('planning_review_required'):
            return
        # 首次编辑导入稿后，其他集仍保留原稿切片，不能因切换聚合口径而消失。
        for other in book.get('episodes') or []:
            if isinstance(other, dict) and not other.get('text'):
                other['text'] = episode_text(project, book, other)
        changed = True
        book['rev'] = int(book.get('rev') or 0) + 1
        book['mode'] = 'generated'
        row.update(text=text, char_start=0, char_end=len(text), text_revision=text_hash(text),
                   script_edit_rev=book['rev'], edited_at=datetime.now().isoformat(timespec='seconds'))
        row.pop('planning_review_required', None)
        edits = book.setdefault('script_edit_revisions', {})
        edits[str(book['rev'])] = episode
        book['script_edit_revisions'] = dict(list(edits.items())[-100:])
    book, current = project_store.update_json(path, mutate, expected_revision=revision, snapshot=versions.snapshot)
    row = next(r for r in book['episodes'] if r['id'] == episode)
    warnings = []
    if changed:
        try:
            sync_episode_copy(project, episode)
        except (OSError, ValueError, project_store.RevisionConflict) as exc:
            warnings.append(f'正文已保存，文本副本未刷新：{exc}')
    return {'ok': True, 'changed': changed, 'revision': current, 'episode': row,
            'affected': affected_outputs(project, episode, row), 'warnings': warnings}


def script_history(project, episode=None):
    """只读正文历史；兼容旧规划存档中的已保存正文，不恢复规划或素材。"""
    project = Path(project)
    base = project / '剧本'
    path = base / '分集.json'
    sources = [{'path': path, 'root': project, 'label': '当前稿', 'current': True}]
    sources += [{'path': Path(p), 'root': project, 'label': versions._history_sort_key(p), 'current': False}
                for p in versions._history_files(str(path))]
    for meta_path in (base / '.versions/规划').glob('*/version.json'):
        try:
            meta = json.loads(meta_path.read_text(encoding='utf-8'))
            if meta.get('status') not in ('history', 'adopted'):
                continue
            root = meta_path.parent / '内容'
            sources.append({'path': root / '剧本/分集.json', 'root': root,
                            'label': meta.get('created_at', ''), 'current': False})
        except (OSError, ValueError, AttributeError):
            continue
    seen, result = set(), []
    sources = sources[:1] + sorted(sources[1:], key=lambda s: str(s['label']).replace('-', '').replace('T', '_').replace(':', ''), reverse=True)
    for item in sources:
        try:
            book = ({} if item['current'] and not episode and not item['path'].is_file()
                    else json.loads(item['path'].read_text(encoding='utf-8')))
            rows = book.get('episodes') or []
            if episode:
                row = next(r for r in rows if r.get('id') == episode)
                text = str(row.get('text') or '')
                if not text and (item['current'] or item['root'] != project):
                    text = episode_text(item['root'], book, row)
            elif item['current'] or item['root'] != project:
                text = script_repository.load_script(str(item['root']))
            else:
                text = '\n\n'.join(f'【{r["id"]} {r.get("title") or r["id"]}】\n{str(r["text"]).strip()}'
                                   for r in rows if str(r.get('text') or '').strip())
                if text:
                    text += '\n'
        except (OSError, ValueError, StopIteration, AttributeError, KeyError, TypeError):
            continue
        if not text.strip():
            continue
        digest = text_hash(text)
        if digest in seen:
            continue
        seen.add(digest)
        result.append({'id': str(item['label']) + '-' + digest[:12], 'label': item['label'],
                       'current': item['current'], 'text': text})
    if not episode:
        for old in versions._history_files(str(base / '剧本.txt')):
            text = Path(old).read_text(encoding='utf-8')
            digest = text_hash(text)
            if text.strip() and digest not in seen:
                seen.add(digest)
                result.append({'id': versions._history_sort_key(old) + '-' + digest[:12],
                               'label': versions._history_sort_key(old), 'current': False, 'text': text})
    return result


def episode_history(project, episode):
    return script_history(project, episode)
