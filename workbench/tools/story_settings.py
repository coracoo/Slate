# -*- coding: utf-8 -*-
"""规划设定按缺项分批、校验与断点保存。"""
import copy
import sys

import story_units
from character_design import merge_generated_appearance

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

KINDS = {'character': 'characters', 'scene': 'scenes', 'prop': 'props'}


class SettingsOutputError(ValueError):
    """结构化输出不合格，可通过缩小批次重试。"""


def batches(targets):
    """每批最多四个实体，按缺项工作量控制输出规模。"""
    batch, weight = [], 0
    for target in targets:
        cost = sum(4 if field == 'biography' else 1 for field in target['missing'])
        if batch and (len(batch) >= 4 or weight + cost > 16):
            yield batch
            batch, weight = [], 0
        batch.append(target)
        weight += cost
    if batch:
        yield batch


def context(units, targets):
    """全剧保留剧情概要，名册仅身份索引；只给本批实体完整设定。"""
    refs = {t['ref'] for t in targets}
    catalog, current = [], []
    for kind, key in KINDS.items():
        for row in units[key]:
            ref = f"@{kind}:{row.get('id')}"
            catalog.append({'ref': ref, 'name': row.get('name')})
            if ref in refs:
                fields = ('name', 'gender', 'role', 'description', 'appearance', 'sheet_prompt', 'identity_anchor',
                          'locked_fields', *story_units.CHAR_TEXT_KEYS, 'relations', 'spatial_limit', 'action_slots', 'usage_boundary',
                          'visual_description', 'geometry', 'layout_note', 'basis', 'states', 'acting', 'voice')
                current.append({'ref': ref, **{k: row[k] for k in fields if row.get(k) is not None}})
    from episode_editor import episode_text
    from pathlib import Path
    project = Path(units['paths']['episodes']).parent.parent
    episodes = []
    for episode in units['episodes']:
        row = {k: v for k, v in episode.items() if k != 'text'}
        related = refs.intersection([*episode.get('cast_refs', []), *episode.get('scene_refs', []), *episode.get('key_asset_refs', [])])
        if related:
            body = episode_text(project, units['episodes_doc'], episode)
            if body:
                row.update(adopted_body=body, body_complete=True)
        episodes.append(row)
    return {'outline': units['outline'], 'premise': units['outline'].get('premise'), 'rules': units['outline'].get('rules'),
            'arcs': units['outline'].get('arcs'), 'roster': catalog, 'current_entities': current,
            'episodes': episodes,
            'foreshadows': [f for f in units['foreshadows'] if refs.intersection(f.get('refs') or [])]}


def validated_patch(data, targets, units):
    """必须逐一返回本批实体；只允许缺项进入合并，不接受顺带改写。"""
    if not isinstance(data, dict):
        raise SettingsOutputError('设定输出必须是 JSON 对象')
    expected = {t['ref']: t for t in targets}
    existing = {f'@{kind}:{r.get("id")}': r for kind, key in KINDS.items() for r in units[key]}
    result = {key: [] for key in KINDS.values()}
    seen = set()
    for kind, key in KINDS.items():
        rows = data.get(key, [])
        if not isinstance(rows, list):
            raise SettingsOutputError(f'{key} 必须是数组')
        for row in rows:
            ref = row.get('ref') if isinstance(row, dict) else None
            if ref not in expected or ref in seen or not ref.startswith('@' + kind + ':'):
                raise SettingsOutputError(f'返回了非本批或重复实体：{ref}')
            seen.add(ref)
            target = expected[ref]
            patch = {'ref': ref}
            appearance_keys = [field.split('.', 1)[1] for field in target['missing'] if field.startswith('appearance.')]
            for field in target['missing']:
                if not field.startswith('appearance.') and field in row:
                    value = row[field]
                    if field == 'action_slots':
                        if not isinstance(value, list) or any(not isinstance(v, str) or not v.strip() for v in value):
                            raise SettingsOutputError(f'{ref}.{field} 必须为非空文本数组')
                    elif not isinstance(value, str) or not value.strip():
                        raise SettingsOutputError(f'{ref}.{field} 必须为非空文本')
                    patch[field] = value
            candidate = copy.deepcopy(existing[ref])
            if appearance_keys:
                appearance = row.get('appearance') or {}
                if not isinstance(appearance, dict):
                    raise SettingsOutputError(f'{ref}.appearance 必须为对象')
                filtered = {k: appearance[k] for k in appearance_keys if k in appearance}
                for meta in ('sources', 'proposals'):
                    values = appearance.get(meta) or {}
                    if not isinstance(values, dict):
                        raise SettingsOutputError(f'{ref}.appearance.{meta} 必须为对象')
                    filtered[meta] = {k: v for k, v in values.items() if k in appearance_keys}
                patch['appearance'] = filtered
                try:
                    candidate['appearance'] = merge_generated_appearance(candidate.get('appearance'), filtered,
                        fill_only=True, locked_fields=candidate.get('locked_fields'))
                except ValueError as exc:
                    raise SettingsOutputError(str(exc)) from exc
            candidate.update({k: v for k, v in patch.items() if k not in ('appearance', 'ref')})
            missing = story_units.missing_fields(kind, candidate)
            # 视觉层字段不进 U2 校验（同 pending_settings 口径：视觉完整性归 ③ 素材链把关）
            if missing:
                raise SettingsOutputError(f'{ref} 仍缺：' + '、'.join(missing))
            result[key].append(patch)
    if seen != set(expected):
        raise SettingsOutputError('本批漏实体：' + '、'.join(sorted(set(expected) - seen)))
    return result


def complete(proj, generate, selected_refs=None):
    """缺项固定排队；格式错误有限拆批，单项失败保留并继续其他项。"""
    def pending_scope():
        result = story_units.pending_settings(proj, per_round=100000, references_only=selected_refs is None)
        if selected_refs is not None:
            result['targets'] = [t for t in result['targets'] if t['ref'] in selected_refs]
            result['remaining'] = len(result['targets'])
        return result
    pending = pending_scope()
    units = story_units.load_units(proj)
    records = {f'@{kind}:{r.get("id")}': r for kind, key in KINDS.items() for r in units[key]}
    if selected_refs is not None and (not selected_refs or not isinstance(selected_refs, list) or any(ref not in records for ref in selected_refs)):
        raise ValueError('请选择已有素材，空选择不会补全全项目')
    blocked = []
    for target in pending['targets']:
        locks = set(records[target['ref']].get('locked_fields') or [])
        fields = [field for field in target['missing'] if field in locks or field.split('.')[0] in locks]
        if fields:
            blocked.append(target['ref'] + '：' + '、'.join(fields))
    if blocked:
        raise ValueError('缺项被人工锁定，未调用模型；请填写或解除对应字段锁：' + '；'.join(blocked))
    queue = list(batches(pending['targets']))
    failures = []
    while queue:
        requested = queue.pop(0)
        latest = {t['ref']: t for t in pending_scope()['targets']}
        targets = [latest[t['ref']] for t in requested if t['ref'] in latest]
        if not targets:
            continue
        units = story_units.load_units(proj)
        print(f"[U2 设定补全] 剩余 {len(latest)} 个在用实体，本批 {len(targets)} 个：" + '、'.join(t['name'] or t['id'] for t in targets), flush=True)
        try:
            data = generate(context(units, targets), targets)
            patch = validated_patch(data, targets, units)
        except SettingsOutputError as exc:
            if len(targets) <= 1:
                message = f'单实体补全失败，停止重试：{targets[0]["ref"]}；{exc}'
                failures.append(message)
                print('[U2 失败] ' + message, flush=True)
                # 单实体都过不了校验＝响应模式问题（不是批太大），整队停止：
                # 继续下一批只会同样烧调用（测试契约：拆批后单实体失败即停，总调用数封顶）。
                break
            mid = len(targets) // 2
            queue[0:0] = [targets[:mid], targets[mid:]]
            print(f'[U2 拆批] 输出未通过校验，本批拆为 {mid}+{len(targets)-mid}；已保存内容保留。', flush=True)
            continue
        story_units.apply_entities(proj, patch, fill_only=True)
        after = {t['ref'] for t in pending_scope()['targets']}
        unchanged = [t['ref'] for t in targets if t['ref'] in after]
        if unchanged:
            raise ValueError('设定未能完整落盘，停止重复处理，请检查人工锁或并发编辑：' + '、'.join(unchanged))
        print(f'[U2 已保存] 本批 {len(targets)} 个实体补齐，剩余 {len(after)} 个在用实体。', flush=True)
    remaining = pending_scope()
    if failures:
        raise ValueError('；'.join(failures) + '；其余结果已保存，再次补齐只处理剩余缺项。')
    if remaining['remaining']:
        raise ValueError(f'运行期间新增或改变了缺项，仍有 {remaining["remaining"]} 个在用实体未完成；已保存结果保留，请核对后续跑。')
