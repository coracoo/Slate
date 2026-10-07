# -*- coding: utf-8 -*-
"""历史素材使用稳定 ID、完整名称和明确别名匹配；歧义不自动选取。"""
import re
import unicodedata


def name_key(value):
    return ''.join(c.lower() for c in unicodedata.normalize('NFKC', str(value or '')) if c.isalnum())


def identity_names(row):
    """完整名称与显式别名；不把场景后缀或修饰词当作同一实体。"""
    aliases = row.get('aliases') or []
    if isinstance(aliases, str):
        aliases = [aliases]
    return {name_key(v) for v in [row.get('name'), *aliases] if name_key(v)}


def identity_match(rows, incoming):
    """只沿用唯一匹配的稳定 ID，重复档案交由批量审核处理。"""
    ident = str(incoming.get('id') or '')
    exact = [r for r in rows if ident and (r.get('id') == ident or ident in (r.get('merged_ids') or []))]
    hits = exact or [r for r in rows if identity_names(r) & identity_names(incoming)
                    and str(r.get('parent_ref') or '') == str(incoming.get('parent_ref') or '')
                    and not (r.get('gender') in ('男', '女', 'male', 'female') and incoming.get('gender') in ('男', '女', 'male', 'female') and r['gender'] != incoming['gender'])]
    if len(hits) > 1:
        raise ValueError(f"{incoming.get('name') or ident} 存在重复素材，请在素材页「批量补齐与审核素材设定」核对对应关系")
    return hits[0] if hits else None


def possible_identities(rows, incoming):
    """相似名称只提出身份对应关系，不能直接写成正式的新身份。"""
    keys = identity_names(incoming)
    return [r for r in rows if str(r.get('id')) != str(incoming.get('id'))
            and r.get('id') not in (incoming.get('identity_distinct_from') or [])
            and incoming.get('id') not in (r.get('identity_distinct_from') or [])
            and not (incoming.get('parent_ref') and incoming.get('parent_ref') != r.get('parent_ref'))
            and any(a == b or min(len(a), len(b)) >= 3 and (a in b or b in a)
                    for a in keys for b in identity_names(r))]


def scene_key(value, scenes, episodes=()):
    raw = str(value or '').strip()
    if raw in set(episodes):
        return {'episode': raw, 'key': raw}
    parts = re.split(r'[／/]', raw)
    time = parts[-1] if len(parts) > 1 and parts[-1] in ('日', '夜', '昼', '昏', '黄昏', '清晨', '晨', '黎明', '傍晚') else ''
    location = raw[:-(len(time)+1)] if time else raw
    ident = location.removeprefix('@scene:')
    exact = [s for s in scenes if s.get('id') == ident]
    matches = exact or [s for s in scenes if name_key(location) in identity_names(s)]
    if len(matches) != 1:
        return None
    ref = '@scene:' + str(matches[0]['id'])
    return {'scene_ref': ref, 'time': time, 'key': ref + ('／' + time if time else '')}


def state_matches_shot(keys, episode, shot, scenes, episodes):
    for key in keys:
        if str(key).strip() == episode and re.fullmatch(r'E\d+', episode or ''):
            return True
        match = scene_key(key, scenes, episodes)
        if not match:
            continue
        if match.get('episode') == episode:
            return True
        if match.get('scene_ref') != shot.get('scene_ref'):
            continue
        time = match.get('time')
        if not time:
            return True
        text = ' '.join(str(shot.get(k) or '') for k in ('time', 'location', 'lighting', 'content', 'prompt_video', 'prompt_image'))
        words = {'夜': ('夜', '月光'), '日': ('白天', '日光', '白昼', '阳光'), '昏': ('黄昏', '暮', '夕阳')}.get(time, (time,))
        if any(word in text for word in words):
            return True
    return False
