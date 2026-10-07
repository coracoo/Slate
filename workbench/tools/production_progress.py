# -*- coding: utf-8 -*-
"""按集统计正文、分镜和已采用媒体；只读，不触发制作任务。"""
import json
import sys
from pathlib import Path
import episode_editor as editor
from production_prompts import media_source_hash
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass


def _json(path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def _present(project, binding):
    raw = (binding or {}).get('path')
    if not raw:
        return False
    raw = str(raw).removeprefix(f'projects/{Path(project).name}/')
    path = (Path(project) / raw).resolve()
    return path.is_relative_to(Path(project).resolve()) and path.is_file() and path.stat().st_size > 0


def _count(project, rows, kind, board_review):
    done = review = 0
    for target, members, unit in rows:
        binding = target.get('keyframe' if kind == 'image' else 'video_binding')
        if not isinstance(binding, dict) or not _present(project, binding):
            continue
        if board_review or not binding.get('source_hash') or binding['source_hash'] != media_source_hash(members, kind, unit):
            review += 1
        else:
            done += 1
    return {'done': done, 'total': len(rows), 'review': review}


def inspect_project(project):
    project = Path(project)
    book = _json(project / '剧本/分集.json', {})
    manifest = _json(project / '创作/creation.json', {})
    items = manifest.get('items', []) if isinstance(manifest, dict) else []
    result = []
    from asset_registry import AssetRegistry
    registry = AssetRegistry(str(project))
    for ep in book.get('episodes', []) if isinstance(book, dict) else []:
        if not isinstance(ep, dict) or not ep.get('id'):
            continue
        boards = list(editor.boards_for_episode(project, ep['id']))
        # 全本分镜没有逐集边界，单独提示，不把同一组 S/V 重复算给每一集。
        specific = [(p, b) for p, b in boards if editor.board_episode(p.name, b) == ep['id']]
        board_pair = next(((p, b) for p, b in specific if p.name == f'剧本_{ep["id"]}.json'), specific[0] if specific else None)
        row = {'id': ep['id'], 'title': ep.get('title') or '', 'script_done': bool(editor.episode_text(project, book, ep).strip()),
               'storyboard_review': False, 'board': None, 'shared_board': bool(boards and not specific),
               'keyframes': {'done': 0, 'total': 0, 'review': 0}, 'videos': {'done': 0, 'total': 0, 'review': 0},
               'shot_videos': {'done': 0, 'total': 0, 'review': 0},
               'assets': {'done': 0, 'total': 0},
               'episode_video_done': False}
        if board_pair:
            path, board = board_pair
            stale = editor.needs_review(project, ep['id'], ep, path.name, board, book=book)
            row.update(board=path.name, storyboard_review=stale)
            shots = [s for s in board.get('shots') or [] if isinstance(s, dict)]
            units = [u for u in board.get('video_units') or [] if isinstance(u, dict)]
            by_id = {s.get('id'): s for s in shots}
            image_rows, video_rows = [], []
            for s in shots:
                unit = next((u for u in units if s.get('id') in u.get('shot_ids', [])), {})
                image_rows.append((s, [s], unit))
            if units:
                for u in units:
                    members = [by_id[sid] for sid in u.get('shot_ids', []) if sid in by_id]
                    video_rows.append((u, members, u))
            else:
                video_rows = [(s, [s], {'id': s.get('id'), 'shot_ids': [s.get('id')], 'duration': s.get('video_duration') or s.get('dur')}) for s in shots]
            row['keyframes'] = _count(project, image_rows, 'image', stale)
            row['videos'] = _count(project, video_rows, 'video', stale)
            from production_studio import shot_unit
            row['shot_videos'] = _count(project, [(s, [s], shot_unit(board, s)) for s in shots], 'video', stale)
            # 集视频必须来自当前分镜版本；旧清单无修订记录时展示待复核。
            board_revision = editor.project_store.current_revision(path)
            for item in items:
                if not isinstance(item, dict) or item.get('scope') != 'E' or item.get('board') != path.name or item.get('status') != 'done' or stale:
                    continue
                request = _json(project / str(item.get('request') or ''), {}) if not item.get('board_revision') else {}
                revision = item.get('board_revision') or request.get('board_revision')
                if revision == board_revision and any(_present(project, {'path': o.get('path') if isinstance(o, dict) else o}) for o in item.get('outputs', [])):
                    row['episode_video_done'] = True
                    break
        refs = [str(ref) for key in ('cast_refs', 'scene_refs', 'key_asset_refs') for ref in ep.get(key) or []]
        if board_pair:
            refs += [str(ref) for s in board_pair[1].get('shots') or [] for ref in s.get('asset_refs') or []]
        refs = list(dict.fromkeys(ref if ref.startswith('@') else '@' + ref for ref in refs))
        refs = [ref for ref in refs if ref.startswith(('@character:', '@scene:', '@prop:'))]
        row['assets']['total'] = len(refs)
        for ref in refs:
            try:
                row['assets']['done'] += int(_present(project, registry.resolve(ref)))
            except ValueError:
                pass
        result.append(row)
    return {'episodes': result}
