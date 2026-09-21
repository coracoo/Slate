# -*- coding: utf-8 -*-
"""两种俯视图共用的在场角色及平面位置规则。"""
import re


def actor_positions(shot, actors, scene=None):
    staging = shot.get('staging') or {}
    spawn = ((scene or {}).get('layout') or {}).get('spawn') or {}
    text = ' '.join(str(shot.get(k) or '') for k in ('action','prompt','content'))
    tokens = set(re.findall(r'@character:([\w-]+)',text))
    mentioned = {line.get('speaker') for line in shot.get('lines',[]) if isinstance(line,dict)} | tokens
    spawn_positions = {}
    for aid, actor in actors.items():
        name = str(actor.get('name') or aid)
        if name in text: mentioned.add(aid)
        for key, pos in spawn.items():
            if str(key).removeprefix('@character:') in (aid,name):
                mentioned.add(aid); spawn_positions[aid] = pos
    result = {}
    for aid, actor in actors.items():
        if aid == 'narrator': continue
        # 显式 staging 按原契约覆盖；没有任何在场线索时保留旧分镜的全角色回退。
        if not staging and mentioned and aid not in mentioned: continue
        pos = staging.get(aid,spawn_positions.get(aid,actor.get('pos',[0,0])))
        if not isinstance(pos,(list,tuple)) or len(pos)<2: continue
        x,z = float(pos[0]),float(pos[1])
        if x>20 and z>20: continue
        result[aid] = [x,z]
    return result
