# -*- coding: utf-8 -*-
"""前期创作阶段契约与只读状态检查；正式写入仍由既有管线负责。"""
import sys
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import json
from pathlib import Path


STAGES = (
    {"id": "concept", "name": "构想或导入正文", "requires": (), "output": "剧本/构想.txt 或 剧本/剧本.txt"},
    {"id": "story_units", "name": "全剧设定锚定", "requires": ("concept",), "output": "大纲、人物小传、分集大纲、埋线与世界设定的确认版本"},
    {"id": "episodes", "name": "分集卡", "requires": ("concept",), "output": "剧本/分集.json"},
    {"id": "episode_scripts", "name": "逐集正文", "requires": ("episodes",), "output": "分集正文"},
    {"id": "assets", "name": "人物场景道具投影", "requires": ("episode_scripts",), "output": "素材/人物.json、场景.json、道具.json"},
    {"id": "storyboard", "name": "S 镜头与 V 编排", "requires": ("episode_scripts", "assets"), "output": "分镜/剧本_*.json"},
)


def _json(path):
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _text(path):
    try:
        return path.is_file() and bool(path.read_text(encoding="utf-8").strip())
    except (OSError, UnicodeError):
        return False


def completion_preflight(project_dir, target_episodes=None, *, units=None, report=None):
    """补空缺不能修正已有集数和分段冲突，须在创建任务及调用模型前检查。"""
    import story_units
    from brief import load_brief
    project = Path(project_dir)
    u = units if units is not None else story_units.load_units(project)
    checked = report if report is not None else story_units.check(project)
    count = len(u['episodes'])
    saved_target = load_brief(project).get('total_episodes')
    target = int(saved_target or target_episodes or 0) or None
    requested = int(target_episodes or 0) or None
    errors = [e for e in checked['errors'] if e['code'] in ('SPEC_EP_COUNT', 'ARC_OVERLAP')]
    if count and target and count != target and not any(e['code'] == 'SPEC_EP_COUNT' for e in errors):
        errors.insert(0, {'code': 'SPEC_EP_COUNT', 'path': '制作规则.目标集数', 'severity': 'error',
                         'message': f'目标 {target} 集与当前 {count} 集不一致；请修改全剧目标集数，或采用当前 {count} 集。补全不会新增或删除分集。'})
    if count and requested and requested != count and not any(e['code'] == 'SPEC_EP_COUNT' for e in errors):
        errors.insert(0, {'code': 'SPEC_EP_COUNT', 'path': '生成参数.目标集数', 'severity': 'error',
                         'message': f'本次请求目标 {requested} 集与当前 {count} 集不一致；补全不会新增或删除分集。'})
    reason = '补全前请先修正：' + '；'.join(e['message'] for e in errors) if errors else ''
    return {'ok': not errors, 'errors': errors, 'reason': reason,
            'episode_count': count, 'target_episodes': target}


def require_completion_ready(project_dir, target_episodes=None):
    result = completion_preflight(project_dir, target_episodes)
    if not result['ok']:
        raise ValueError(result['reason'])
    return result


def workflow_state(project_dir):
    """规划、核对锁定、正文扩写共用的业务门槛；导入稿和旧项目兼容。"""
    import story_units
    project = Path(project_dir)
    u = story_units.load_units(project)
    imported = u['episodes_doc'].get('mode') == 'imported'
    managed = bool(u['outline'].get('units_version') or u['episodes_doc'].get('units_version')) and not imported
    report = story_units.check(project)
    preflight = completion_preflight(project, units=u, report=report)
    settings = story_units.pending_settings(project, per_round=100000, references_only=True)['targets'] if managed else []
    draft = bool(u['episodes'] and u['outline'])
    locked = story_units.is_anchored(project)
    stale = story_units.anchor_rev(project) > 0 and not locked
    source = _text(project / '剧本' / '构想.txt') or _text(project / '剧本' / '剧本.txt')
    ready = bool(u['episodes']) and (not managed or (locked and report['ok']))
    state = ('imported' if imported else 'legacy' if not managed and u['episodes'] else
             'stale' if stale else 'locked' if locked and report['ok'] else 'review' if draft else 'empty')
    next_action = ('expand' if ready else 'build' if not draft else
                   'repair' if not preflight['ok'] else 'complete_settings' if settings else
                   'repair' if not report['ok'] else 'anchor')
    reason = {'expand': '', 'build': '生成全剧规划后，核对并确认。',
              'complete_settings': f'还有 {len(settings)} 个在用人物、场景或道具需要补齐设定并确认。',
              'repair': '请查看待处理项，点击对应入口修改。',
              'anchor': '规划已具备正文生成条件，请核对后确认。'}[next_action]
    return {'state': state, 'managed': managed, 'can_build': bool(source and not locked and preflight['ok']),
            'can_complete': bool(source and (not locked or settings) and preflight['ok']),
            'episode_count': preflight['episode_count'], 'target_episodes': preflight['target_episodes'],
            'completion_blockers': preflight['errors'],
            'blockers': report['errors'], 'settings_missing': settings, 'next_action': next_action,
            'can_anchor': bool(draft and report['ok'] and not locked), 'can_expand': ready,
            'anchor_stale': stale, 'reason': reason, 'anchor_rev': story_units.anchor_rev(project)}


def require_expansion_ready(project_dir):
    state = workflow_state(project_dir)
    if not state['can_expand']:
        raise ValueError(state['reason'])
    return state


def inspect_project(project_dir):
    """返回阶段图当前状态，不创建、修改或迁移任何项目文件。"""
    project = Path(project_dir)
    script = project / "剧本"
    assets = project / "素材"
    episode_doc = _json(script / "分集.json")
    episodes = episode_doc.get("episodes") if isinstance(episode_doc, dict) else episode_doc
    episodes = episodes if isinstance(episodes, list) else []
    outline = _json(script / "大纲.json")
    revision = int(outline.get("anchor_rev") or 0) if isinstance(outline, dict) else 0
    imported = isinstance(episode_doc, dict) and episode_doc.get("mode") == "imported"
    concept = _text(script / "构想.txt") or _text(script / "剧本.txt")
    episode_scripts = imported and _text(script / "剧本.txt")
    episode_scripts = episode_scripts or any(
        isinstance(row, dict) and str(row.get("text") or "").strip() for row in episodes
    ) or any(_text(path) for path in script.glob("分集剧本_*.txt"))
    asset_files = (assets / "人物.json", assets / "场景.json", assets / "道具.json")
    available = {
        "concept": concept,
        "story_units": revision > 0 and isinstance(_json(script / "埋线.json"), dict) and bool(episodes),
        "episodes": bool(episodes),
        "episode_scripts": episode_scripts,
        "assets": all(isinstance(_json(path), (dict, list)) for path in asset_files),
        "storyboard": any(path.is_file() for path in (project / "分镜").glob("剧本_*.json")),
    }
    from episode_editor import episode_text
    completed = sum(bool(episode_text(project, episode_doc or {}, row).strip()) for row in episodes if isinstance(row, dict))
    rows = []
    for spec in STAGES:
        item = dict(spec)
        item["requires"] = list(spec["requires"])
        item["revision"] = revision if spec["id"] == "story_units" else None
        if available[spec["id"]]:
            item["status"] = "done"
        elif spec["id"] == "story_units" and imported:
            item["status"] = "optional"
        elif all(available[required] for required in spec["requires"]):
            item["status"] = "ready"
        else:
            item["status"] = "blocked"
        if spec['id'] == 'episode_scripts' and episodes:
            item.update(completed=completed, total=len(episodes))
            item['status'] = 'done' if completed == len(episodes) else 'partial' if completed else 'ready'
        rows.append(item)
    workflow = workflow_state(project)
    if workflow['managed']:
        for row in rows:
            if row['id'] == 'story_units':
                row['status'] = 'done' if workflow['state'] == 'locked' else 'blocked' if workflow['anchor_stale'] else 'ready'
            if row['id'] == 'episode_scripts' and not workflow['can_expand'] and row['status'] == 'ready':
                row['status'] = 'blocked'
    return {"stages": rows, "script_workflow": workflow}
