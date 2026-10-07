# -*- coding: utf-8 -*-
"""角色工作区：人物档案为唯一来源，镜内表演由演员管线单独审核采用。"""
import copy
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

for _path in (Path(__file__).resolve().parent,
              Path(__file__).resolve().parents[2] / "previs_system" / "tools"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
import project_store
import asset_repository
import versions
from character_design import merge_appearance, APPEARANCE_FIELDS
from skill_lib import normalize_character_sheet

TEXT_FIELDS = ("biography", "bio_language", "bio_crack", "bio_pressure", "bio_address", "bio_arc",
               "sheet_prompt", "identity_anchor", "voice")
ACTING_FIELDS = ("personality", "goal", "relationship", "expression_rules", "arc_stage")
EDITABLE_FIELDS = set(TEXT_FIELDS) | {"acting", "appearance", "relations", "states"}


def _reserved(value):
    return str(value or "").strip().lower() in {
        "narrator", "旁白", "叙述者", "画外音", "解说", "vo", "voiceover", "voice-over"}


def _rows(doc):
    rows = doc.get("characters", [])
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("人物档案 characters 必须为对象数组")
    return rows


def state(project):
    from skill_lib import read_scope
    with read_scope():
        return _state(project)


def _state(project):
    """返回 {ok, characters, revision}；旁白仅作为合成音色行返回。"""
    path = Path(project) / "素材" / "人物.json"
    doc, revision = project_store.read_json(path) if path.exists() else ({"characters": []}, "")
    characters = [copy.deepcopy(row) for row in _rows(doc)
                  if not _reserved(row.get("id")) and not _reserved(row.get("name"))]
    for character in characters:
        from visual_asset_prompt import editable_prompt, visual_contract
        character['visual_status'] = {key: value for key, value in visual_contract('character', character, project=project).items()
                                      if key not in ('subject', 'editable_prompt')}
        character["sheet_prompt"] = editable_prompt('character', character, project=project)
        for state in character.get('states') or []:
            if not isinstance(state, dict):
                continue
            from visual_confirmation import confirmation_contract
            state['visual_status'] = {key: value for key, value in
                confirmation_contract(project, 'character', character, state).items()
                if key not in ('subject', 'editable_prompt')}
    from voice_assets import narrator_binding
    characters.append({"id": "narrator", "name": "旁白", "reserved": True,
                       "voice_binding": narrator_binding(project) or None})
    return {"ok": True, "characters": characters, "revision": revision}


def _text(value, label):
    if not isinstance(value, str):
        raise ValueError(f"{label} 必须为文本")
    if len(value) > 20000:
        raise ValueError(f"{label} 不能超过 20000 字")
    return value.strip()


def normalize_sheets(project):
    """带快照归一化当前人物母图与状态图；不改外观事实或历史版本。"""
    path = Path(project) / "素材" / "人物.json"
    if not path.exists():
        return 0
    doc, revision = project_store.read_json(path)
    count = 0
    for row in _rows(doc):
        for record in [row, *(item for item in row.get('states') or [] if isinstance(item, dict))]:
            previous = record.get('sheet_prompt')
            normalized = normalize_character_sheet(previous)
            if previous and normalized != previous:
                record['sheet_prompt'] = normalized
                count += 1
    if count:
        def replace(current):
            current.clear()
            current.update(doc)
        asset_repository.update_json(path, replace, source='normalization', expected_revision=revision, snapshot=versions.snapshot)
    return count


def _apply_patch(doc, character_id, patch):
    rows = _rows(doc)
    row = next((item for item in rows if item.get("id") == character_id), None)
    if row is None or _reserved(row.get("name")):
        raise ValueError("角色不存在或属于保留说话人")
    clean = {}
    for field, value in patch.items():
        if field in TEXT_FIELDS:
            clean[field] = _text(value, field)
            if field == "sheet_prompt":
                clean[field] = normalize_character_sheet(clean[field])
        elif field == "acting":
            if not isinstance(value, dict) or set(value) - set(ACTING_FIELDS):
                raise ValueError("演绎方式只接受性格、目标、关系、表达规则与弧线阶段")
            clean[field] = {**(row.get(field) if isinstance(row.get(field), dict) else {}),
                            **{key: _text(text, key) for key, text in value.items()}}
        elif field == "appearance":
            clean[field] = merge_appearance(row.get(field), value)
        elif field == "relations":
            if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
                raise ValueError("人物关系必须为对象数组")
            valid_refs = {"@character:" + str(item.get("id")) for item in rows
                          if not _reserved(item.get("id")) and not _reserved(item.get("name"))}
            for item in value:
                if item.get("to_ref") not in valid_refs:
                    raise ValueError("人物关系必须指向本项目已有角色")
                for key in ("kind", "note"):
                    if key in item:
                        _text(item[key], key)
            clean[field] = copy.deepcopy(value)
        elif field == 'states':
            previous = {str(item.get('id')): item for item in row.get('states') or [] if isinstance(item, dict)}
            if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
                raise ValueError('派生状态必须为对象数组')
            if len(value) != len(previous) or {str(item.get('id')) for item in value} != set(previous):
                raise ValueError('本次只编辑现有派生状态，不能更换 ID 或删除状态')
            merged = []
            for item in value:
                state = copy.deepcopy(previous[str(item['id'])])
                changed_keys = []
                for key in ('label', 'look_diff', 'sheet_prompt', 'camp'):
                    if key in item:
                        state[key] = _text(item[key], key)
                        if state[key] != previous[str(item['id'])].get(key):
                            changed_keys.append(key)
                if 'episodes' in item:
                    if not isinstance(item['episodes'], list) or any(not isinstance(e, str) for e in item['episodes']):
                        raise ValueError('派生匹配键必须为文本数组')
                    state['episodes'] = list(dict.fromkeys(e.strip() for e in item['episodes'] if e.strip()))
                    if state['episodes'] != previous[str(item['id'])].get('episodes'):
                        changed_keys.append('episodes')
                if 'output_asset_ref' in item:
                    ref = _text(item['output_asset_ref'], 'output_asset_ref')
                    if ref and ref != '@character:' + character_id and ref != previous[str(item['id'])].get('output_asset_ref'):
                        raise ValueError('复用母图必须指向当前角色')
                    if ref != str(previous[str(item['id'])].get('output_asset_ref') or ''):
                        changed_keys.append('output_asset_ref')
                    if ref:
                        state['output_asset_ref'] = ref
                    else:
                        state.pop('output_asset_ref', None)
                state['locked_fields'] = list(dict.fromkeys([*(state.get('locked_fields') or []),
                    *changed_keys]))
                merged.append(state)
            clean[field] = merged
    row.update(clean)
    # 人工设定沿用锚定层的字段锁，后续提取不能静默改回旧设定。
    locked = row.get("locked_fields") if isinstance(row.get("locked_fields"), list) else []
    touched = [field for field in patch if field not in ('appearance', 'states')]
    if 'appearance' in patch:
        appearance_patch = patch['appearance']
        keys = set(appearance_patch) | set(appearance_patch.get('sources', {})) | set(appearance_patch.get('proposals', {}))
        touched.extend('appearance.' + key for key in APPEARANCE_FIELDS if key in keys)
    row["locked_fields"] = list(dict.fromkeys([*locked, *touched]))
    from production_state import bump_asset_revision
    bump_asset_revision(row)


def save_batch(project, items, *, expected_revision=None):
    """集中审核后一次落盘；任何条目无效或版本过期则整批不写入。"""
    if not isinstance(items, list) or not items or len(items) > 500:
        raise ValueError('请选择 1–500 个角色修改')
    if not isinstance(expected_revision, str) or not expected_revision:
        raise ValueError('缺少人物档案版本，请重新打开审核')
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('角色修改必须为对象')
        target, patch = item.get('character_id'), item.get('patch')
        if not isinstance(target, str) or not target or _reserved(target):
            raise ValueError('请选择已有角色，旁白仅维护音色')
        if target in seen:
            raise ValueError('同一角色不能重复提交')
        seen.add(target)
        if not isinstance(patch, dict) or not patch or set(patch) - EDITABLE_FIELDS:
            raise ValueError('请提供支持的角色修改字段')

    def mutate(doc):
        for item in items:
            _apply_patch(doc, item['character_id'], item['patch'])

    doc, revision = asset_repository.update_json(Path(project) / '素材' / '人物.json', mutate,
        expected_revision=expected_revision, snapshot=versions.snapshot)
    from visual_asset_prompt import visual_contract
    rows = [row for row in _rows(doc) if row.get('id') in seen]
    checks = {row['id']: {key: value for key, value in visual_contract('character', row, project=project).items()
                          if key not in ('subject', 'editable_prompt')} for row in rows}
    return {'ok': True, 'saved': [item['character_id'] for item in items], 'revision': revision,
            'characters': rows, 'visual_validations': checks}


def save(project, character_id, patch, *, expected_revision=None):
    """带版本校验和快照更新项目角色；返回 {ok, character, revision}。"""
    if _reserved(character_id):
        raise ValueError("旁白只维护音色绑定，不能写入人物档案")
    if not isinstance(patch, dict) or not patch:
        raise ValueError("请提供需要保存的角色字段")
    unknown = set(patch) - EDITABLE_FIELDS
    if unknown:
        raise ValueError("不支持修改字段：" + "、".join(sorted(unknown)))
    if not isinstance(expected_revision, str) or not expected_revision:
        raise ValueError("缺少人物档案版本，请刷新后保存")

    def mutate(doc):
        _apply_patch(doc, character_id, patch)

    doc, revision = asset_repository.update_json(Path(project) / "素材" / "人物.json", mutate,
                                              expected_revision=expected_revision, snapshot=versions.snapshot)
    row = next(item for item in _rows(doc) if item.get("id") == character_id)
    from visual_asset_prompt import visual_contract
    validation = visual_contract('character', row, project=project)
    return {"ok": True, "character": row, "revision": revision,
            'visual_validation': {key: value for key, value in validation.items() if key not in ('subject', 'editable_prompt')}}
