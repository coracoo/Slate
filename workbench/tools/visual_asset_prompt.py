# -*- coding: utf-8 -*-
"""从素材权威档案投影生图主体；展示与生成共用，不回写历史描绘。"""
import sys
import hashlib
import json
import re
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

from character_design import character_image_prompt, APPEARANCE_FIELDS, APPEARANCE_LABELS, has_design_value, _human


def uses_planning_settings(project):
    """已采用规划的项目只读设定；未确认或已解锁也不能退回重新发现素材。"""
    if not project:
        return False
    docs = []
    for name in ('大纲.json', '分集.json'):
        path = Path(project) / '剧本' / name
        try:
            doc = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            doc = {}
        docs.append(doc if isinstance(doc, dict) else {})
    return bool(any(doc.get('units_version') for doc in docs)) and docs[1].get('mode') != 'imported'


def generation_kind(kind, record):
    """存储引用保持兼容，构图按明确登记的视觉类型选择。"""
    return 'effect' if kind == 'prop' and record.get('kind') == '显现/特效' else kind


def raw_prompt(kind, record):
    field = 'sheet_prompt' if kind == 'character' else 'image_prompt'
    return str(record.get(field) or record.get('prompt') or '').strip()


def state_output_notice(record, state):
    ref = str(state.get('output_asset_ref') or '')
    if ref == '@character:' + str(record.get('id') or ''):
        return '该状态复用角色母图，不另生派生图；剧情动作由分镜表现。'
    return '该状态已归入独立视觉素材，请生成 ' + ref


def editable_prompt(kind, record, *, project=None):
    """人工补充与历史自动提炼分开；历史文本仍留原档案供查阅。"""
    field = 'sheet_prompt' if kind == 'character' else 'image_prompt'
    appearance = record.get('appearance') if isinstance(record.get('appearance'), dict) else {}
    structured = kind == 'character' and (any(has_design_value(appearance.get(key)) for key in APPEARANCE_FIELDS) or bool(appearance.get('proposals')))
    if (uses_planning_settings(project) or structured) and field not in (record.get('locked_fields') or []):
        return ''
    return raw_prompt(kind, record)


def subject_prompt(kind, record, state=None, *, project=None):
    planned = uses_planning_settings(project)
    if generation_kind(kind, record) == 'effect':
        return editable_prompt(kind, record, project=project) or str(record.get('description') or '').strip()
    if kind == 'character':
        if state is not None:
            if state.get('output_asset_ref'):
                raise ValueError(state_output_notice(record, state))
            from skill_lib import strip_character_layout
            difference = strip_character_layout(state.get('look_diff'))
            detail = strip_character_layout(raw_prompt(kind, state))
            parts = []
            if difference:
                parts.append('必须实现的可见变化：' + difference)
            if detail and detail != difference:
                parts.append('目标状态外观：' + detail)
            if difference and detail:
                parts.append('外观补充与上述可见变化冲突时，以可见变化为准。')
            return character_image_prompt(record, '\n'.join(parts), state=True)
        return character_image_prompt({**record, '_planning_visual': planned}, editable_prompt(kind, record, project=project))
    fields = ({'visual_description': '视觉设定', 'description': '场景描绘', 'geometry': '场景结构', 'layout_note': '场景布局', 'time': '时间', 'light': '光线',
               'spatial_limit': '空间限制', 'action_slots': '空间动作位'} if kind == 'scene' else
              {'visual_description': '视觉设定', 'description': '道具描绘', 'appearance': '外观', 'usage_boundary': '使用边界'})
    parts = []
    for key, label in fields.items():
        value = record.get(key)
        if isinstance(value, list):
            value = '；'.join(str(item).strip() for item in value if str(item).strip())
        if isinstance(value, str) and value.strip():
            parts.append(f'{label}：{value.strip()}')
    raw = editable_prompt(kind, record, project=project)
    if raw:
        parts.append('人工补充描绘：' + raw if planned else '素材描绘：' + raw)
        if planned and parts:
            parts.append('人工补充只补充视觉细节，不改变上列当前设定中的结构、时代和物品身份。')
    if parts and record.get('name'):
        parts.insert(0, '素材：' + str(record['name']))
    if state:
        if state.get('output_asset_ref'):
            raise ValueError('该状态复用母图，不另生成派生图')
        parts.append('以母素材图保持身份与空间结构；仅改变以下可见状态：'+str(state.get('look_diff') or state.get('label') or ''))
        if state.get('image_prompt'):
            parts.append(str(state['image_prompt']))
    return '\n'.join(parts)


def missing_visual_fields(kind, record):
    """空间动作限制和道具使用规则不能充当视觉描绘；独立于正文锁定。"""
    if kind == 'character':
        return []
    fields = ('visual_description', 'description', 'geometry', 'layout_note') if kind == 'scene' else ('visual_description', 'description', 'appearance')
    def meaningful(value):
        if isinstance(value, str):
            return has_design_value(value)
        return isinstance(value, list) and any(meaningful(item) for item in value)
    if any(meaningful(record.get(key)) for key in fields) or 'image_prompt' in (record.get('locked_fields') or []) and has_design_value(raw_prompt(kind, record)):
        return []
    return ['visual_description']


def visual_contract(kind, record, state=None, *, project=None, image=None, has_image=False):
    """校验当前正式视觉输入，并与已生成图的来源比较；不修改档案或旧索引。"""
    planned = uses_planning_settings(project)
    appearance = record.get('appearance') if isinstance(record.get('appearance'), dict) else {}
    proposals = appearance.get('proposals') if isinstance(appearance.get('proposals'), dict) else {}
    sources = appearance.get('sources') if isinstance(appearance.get('sources'), dict) else {}
    pending, missing, warnings = [], [], []
    if kind == 'character':
        design = {**proposals, **{k: v for k, v in appearance.items() if has_design_value(v)}}
        required = ('face', 'hair', 'body_type', 'outfit') if _human(design) else ('body_type', 'distinctive_features', 'look')
        for key in required:
            adopted = has_design_value(appearance.get(key)) and sources.get(key) not in ('proposal', 'unknown')
            if not adopted and (has_design_value(proposals.get(key)) or sources.get(key) == 'proposal'):
                pending.append(key)
            elif not adopted and planned:
                missing.append(key)
    elif planned:
        missing.extend(missing_visual_fields(kind, record))
    try:
        subject = subject_prompt(kind, record, state, project=project)
    except ValueError as exc:
        subject = ''
        warnings.append(str(exc))
    if not subject:
        missing.append('visual_description')
    if state:
        difference = str(state.get('look_diff') or '')
        if re.search(r'后被|随后|然后|继而', difference) or re.search(r'(?:拔出|举起|手持|持有).+最终', difference):
            warnings.append('派生差异包含前后动作，请保留一个确定时刻的服装、持物或伤情')
        if difference and re.search(r'承认|身份公开|表明立场|决定|信任|质疑', difference) and not re.search(r'衣|甲|伤|疤|头发|披|持|包扎|血', difference):
            warnings.append('该状态只有剧情或心理变化，应复用母图并由分镜表现')
        if project:
            try:
                episodes = json.loads((Path(project) / '剧本' / '分集.json').read_text(encoding='utf-8')).get('episodes', [])
                scenes = json.loads((Path(project) / '素材' / '场景.json').read_text(encoding='utf-8')).get('scenes', [])
            except (OSError, ValueError, AttributeError):
                episodes, scenes = [], []
            from asset_matching import scene_key
            known = {str(e.get('id')) for e in episodes if isinstance(e, dict)}
            unknown = [str(key) for key in state.get('episodes') or [] if not scene_key(key, scenes, known)]
            if episodes and unknown:
                warnings.append('派生匹配键无法关联当前分集或场景：' + '、'.join(unknown))
    fields = ('parent_ref', 'derived_from', 'style', 'style_prompt')
    payload = {'version': 1, 'kind': kind, 'subject': subject,
               'settings': {key: record.get(key) for key in fields}}
    if project:
        from skill_lib import compose_asset_image_prompt
        final, negative = compose_asset_image_prompt(project, subject, skill_id=record.get('style'),
            kind=generation_kind(kind, record), style_prompt=record.get('style_prompt'))
        payload.update(final=final, negative=negative)
    source_hash = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()
    reasons = []
    image = image if isinstance(image, dict) else {}
    image_status = 'missing'
    if has_image:
        saved = image.get('visual_source_hash')
        image_status = 'outdated' if image.get('stale') or saved and saved != source_hash else 'current' if saved else 'unverified'
        if image_status == 'outdated':
            reasons = image.get('stale_reasons') or ['当前设定与该图生成时的设定不同']
        elif image_status == 'unverified':
            reasons = ['历史图没有设定校验记录，请核对画面后决定是否重生成']
    labels = list(dict.fromkeys(APPEARANCE_LABELS.get(key, '视觉描绘') for key in [*pending, *missing]))
    mask = image.get('head_mask') or {}
    postprocess_warning = ('头部遮盖未完成：' + str(mask.get('reason') or '请检查图片布局')
                           if has_image and mask.get('status') == 'needs_review' else '')
    result = {'ready': not pending and not missing and not warnings, 'subject': subject,
            'source': '剧本规划设定' if planned else '当前素材档案', 'source_hash': source_hash,
            'editable_prompt': editable_prompt(kind, record, project=project),
            'pending_fields': pending, 'missing_fields': missing, 'field_labels': labels,
            'warnings': warnings, 'image_status': image_status, 'image_stale': image_status == 'outdated',
            'image_reasons': reasons, 'postprocess_warning': postprocess_warning}
    from visual_confirmation import review_status
    return {**result, **review_status(result, record, state)}


def require_visual_settings(kind, record, state=None, *, project=None):
    contract = visual_contract(kind, record, state, project=project)
    if not contract['ready']:
        action = '在素材页「批量补齐与审核素材设定」中补齐或修改后确认外观与派生'
        details = '、'.join(contract['field_labels'] + contract['warnings'])
        label = str(record.get('name') or record.get('id') or '素材')
        if state:
            label += ' · ' + str(state.get('label') or state.get('id')) + '（' + str(state.get('id')) + '）'
        raise ValueError(f"{label}：{details}；请{action}，保存后自动重新校验，无需重新提炼。")
    return contract


def check_project(project, kind=None, episode=''):
    """现有素材入口在规划项目中只检查投影，不运行 LLM 或写回素材。"""
    from asset_registry import AssetRegistry
    rows = AssetRegistry(project).list()
    checks = []
    kinds = {'人物': 'character', '场景': 'scene', '道具': 'prop'}
    for row in rows:
        if row['kind'] == 'style' or kind and row['kind'] != kinds.get(kind, kind):
            continue
        if (row.get('visual_status') or {}).get('archive_present') is False:
            continue
        if row.get('in_use') is False:
            continue
        if episode and row.get('source_episode_ids') and episode not in row['source_episode_ids']:
            continue
        status = row.get('visual_status') or {}
        checks.append({'ref': row['ref'], 'name': row['name'], **status})
        if not status.get('ready'):
            print(f"[待确认] {row['name']}：{'、'.join(status.get('field_labels', []) + status.get('warnings', []))}", flush=True)
        for state in row.get('states') or []:
            status = state.get('visual_status') or {}
            checks.append({'ref': row['ref'] + '#' + state['id'], 'name': state['label'], **status})
            if status.get('warnings'):
                print(f"[派生待核对] {row['name']} · {state['label']}：{'；'.join(status['warnings'])}", flush=True)
    print(f"[素材校验] 在用素材 {len(checks)} 项，待处理 {sum(not row.get('ready') for row in checks)} 项。外观建议可在顶部批量审核，历史素材可重新匹配。", flush=True)
    return {'ok': all(row.get('ready') for row in checks), 'checks': checks}
