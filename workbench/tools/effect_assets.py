# -*- coding: utf-8 -*-
"""将经用户确认的非实体状态关联到独立特效素材，保留状态和旧图历史。"""
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'previs_system' / 'tools'))
import project_store
import versions


def promote_states(project, character_id, state_ids, effect_id, name, description):
    import re
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{1,63}', effect_id) or not str(description).strip():
        raise ValueError('特效需要稳定 ID 和可见画面描述')
    root = Path(project) / '素材'
    characters, revision = project_store.read_json(root / '人物.json')
    actor = next((r for r in characters.get('characters', []) if r.get('id') == character_id), None)
    wanted = set(state_ids)
    states = [r for r in (actor or {}).get('states', []) if r.get('id') in wanted]
    if not wanted or len(states) != len(wanted):
        raise ValueError('来源角色或状态不存在')
    ref = '@prop:' + effect_id
    if any(s.get('output_asset_ref') not in (None, '', ref) for s in states):
        raise ValueError('状态已关联其他素材，不能覆盖')
    source_refs = [f'@character:{character_id}#{sid}' for sid in sorted(wanted)]
    def add_effect(doc):
        rows = doc.setdefault('props', [])
        old = next((r for r in rows if r.get('id') == effect_id), None)
        if old:
            if old.get('source_state_refs') != source_refs or old.get('kind') != '显现/特效':
                raise ValueError('目标 ID 已被其他素材使用')
            return
        rows.append({'id': effect_id, 'name': name, 'kind': '显现/特效', 'asset_required': True,
                     'image_prompt': str(description).strip(), 'related_refs': ['@character:' + character_id],
                     'source_state_refs': source_refs,
                     'source_episode_ids': sorted({str(ep) for s in states for ep in s.get('episodes', [])}),
                     'usage_boundary': '仅在来源剧情状态中表现非实体显现，不代替角色实体身份图。',
                     'asset_revision': 1, 'locked_fields': ['kind', 'image_prompt']})
    project_store.update_json(root / '道具.json', add_effect, create_default={'props': []}, snapshot=versions.snapshot)
    def link(doc):
        current = next(r for r in doc['characters'] if r.get('id') == character_id)
        for state in current.get('states', []):
            if state.get('id') in wanted:
                state['output_asset_ref'] = ref
    if any(s.get('output_asset_ref') != ref for s in states):
        project_store.update_json(root / '人物.json', link, expected_revision=revision, snapshot=versions.snapshot)
    index_path = root / '素材图.json'
    if index_path.exists():
        def mark(doc):
            for sid, entry in (((doc.get('人物') or {}).get(character_id) or {}).get('states') or {}).items():
                if sid in wanted and isinstance(entry, dict):
                    entry['output_asset_ref'] = ref
        project_store.update_json(index_path, mark, snapshot=versions.snapshot)
    return ref
