# -*- coding: utf-8 -*-
"""项目音色资产与角色绑定。云端音色 ID 的作用域是当前厂商账户。"""
import copy
import hashlib
import json
import uuid
from pathlib import Path
from production_studio import project_store, inside, read_board
from production_media import digest, probe
from native_media import checked

try:
    import billing          # 计费账本；缺失时静默跳过
except Exception:
    billing = None


def _bill(kind, cfg, op, ok=True, units=None, cost=None, error=None):
    """直连 MiniMax 接口的补账入口；任何异常静默。"""
    try:
        if billing is None:
            return
        if cost is not None:    # 免费查询类：显式 cost=0，不走价格表
            billing.record({"vendor": cfg.get("id") or "", "kind": kind,
                            "model": (cfg.get("models") or {}).get("speech") or "",
                            "op": op, "ok": ok, "cost": cost, "currency": None,
                            **({"units": units} if units else {}),
                            **({"error": str(error)[:300]} if error else {})})
        else:
            billing.bill(cfg, kind=kind, model=(cfg.get("models") or {}).get("speech") or "",
                         op=op, ok=ok, units=units, error=error)
    except Exception:
        pass


def library(project):
    path = Path(project) / '素材/音色/音色库.json'
    return project_store.read_json(path)[0] if path.exists() else {'voices': []}


def characters(project):
    path = Path(project) / '素材/人物.json'
    return project_store.read_json(path)[0].get('characters', []) if path.exists() else []


def profile(cfg):
    # 缓存不跨账户共用，不将原始密钥写入项目资产。
    return hashlib.sha256((cfg.get('base_url', '') + '\n' + cfg.get('api_key', '')).encode()).hexdigest()[:20]


def state(project, cfg=None):
    result = {'characters': characters(project), **library(project), 'catalog': []}
    if cfg:
        cache = Path(__file__).resolve().parents[1] / 'cache/voices' / profile(cfg) / 'catalog.json'
        if cache.exists(): result['catalog'] = json.loads(cache.read_text(encoding='utf-8'))['voices']
    return result


def resolve(project, character_id, voice_asset_id=None):
    actor = next((c for c in characters(project) if c.get('id') == character_id), None)
    if not actor: raise ValueError('角色不存在；旁白不作为人物素材')
    binding = actor.get('voice_binding') or {}
    if not binding:
        parent = str(actor.get('parent_ref') or actor.get('derived_from') or '')
        if parent.startswith('@character:') and parent.split(':', 1)[1] != character_id:
            mother = next((c for c in characters(project) if c.get('id') == parent.split(':', 1)[1]), {})
            binding = mother.get('voice_binding') or {}
    if voice_asset_id:
        choices = [binding, *(actor.get('voice_variants') or [])]
        binding = next((v for v in choices if v.get('voice_asset_id') == voice_asset_id), None)
        if not binding: raise ValueError('派生音色不属于当前角色')
    voice = next((v for v in library(project)['voices'] if v['id'] == binding.get('voice_asset_id') and v['revision'] == binding.get('revision')), None)
    if not voice: raise ValueError(f'{actor.get("name", character_id)} 尚未绑定音色')
    return {**voice, 'character_id': character_id, 'character_name': actor.get('name', character_id)}


def bind(project, body):
    voice = next((v for v in library(project)['voices'] if v['id'] == body.get('voice_asset_id') and v['revision'] == body.get('revision')), None)
    if not voice or not voice.get('sample'): raise ValueError('请先保存可试听的音色资产')
    inside(project, voice['sample'])
    variant = body.get('mode') == 'variant'
    name = str(body.get('name') or '').strip()
    if variant:
        if not name.startswith('角色音乐·') or not name.removeprefix('角色音乐·').strip():
            raise ValueError('请编辑派生音色名称，格式为：角色音乐·XXX')
        actor = next((c for c in characters(project) if c['id'] == body.get('character_id')), None)
        if not actor or not actor.get('voice_binding'): raise ValueError('请先绑定角色全剧默认音色')
        ident = 'voice-' + uuid.uuid4().hex[:16]
        voice = {**copy.deepcopy(voice), 'id': ident, 'name': name, 'revision': 1,
                 'character_id': actor['id'], 'derived_from': actor['voice_binding']['voice_asset_id'],
                 'source_voice_asset_id': voice['id']}
        def add(data):
            if any(v.get('character_id') == actor['id'] and v.get('name') == name for v in data.get('voices', [])):
                raise ValueError('该角色已有同名派生音色，请修改名称')
            data.setdefault('voices', []).append(voice)
        project_store.update_json(Path(project) / '素材/音色/音色库.json', add, create_default={'voices': []})
    def mutate(data):
        actor = next((c for c in data['characters'] if c['id'] == body.get('character_id')), None)
        if not actor: raise ValueError('角色不存在')
        link = {'voice_asset_id': voice['id'], 'revision': voice['revision']}
        if variant: actor.setdefault('voice_variants', []).append({**link, 'name': name})
        else: actor['voice_binding'] = {**link, 'scope': 'project'}
    project_store.update_json(Path(project) / '素材/人物.json', mutate)
    return {'ok': True}


def prepare(project, body, cfg):
    if not cfg or cfg.get('id') != 'minimax': raise ValueError('当前音色适配器支持 MiniMax，请启用并配置该厂商')
    action = body['action']
    packet = {'type': 'speech' if action == 'speech' else 'voice', 'vendor_id': cfg['id'], 'profile': profile(cfg)}
    if action == 'voice_sample':
        if not str(body.get('voice_id') or '').strip(): raise ValueError('请选择云端音色')
        packet.update(voice_id=str(body['voice_id']), name=str(body.get('name') or body['voice_id']),
                      preview_text=str(body.get('preview_text') or '你好，这是角色音色试听。'))
    elif action == 'voice_design':
        if not str(body.get('description') or '').strip(): raise ValueError('请填写音色描述')
        packet.update(description=str(body['description']), name=str(body.get('name') or 'AI 创作音色'),
                      preview_text=str(body.get('preview_text') or '你好，这是为角色创作的声音。'))
    elif action == 'speech':
        voice = resolve(project, str(body.get('character_id') or ''), body.get('voice_asset_id'))
        if voice['profile'] != packet['profile']: raise ValueError('音色属于其他厂商账户，请重新查询和绑定')
        board, _ = read_board(project, body['board'])
        texts = []
        for s in board.get('shots', []):
            for i, line in enumerate(s.get('lines', [])):
                if line.get('speaker') == voice['character_id']:
                    texts.append({'line_id': f"{s['id']}:{i}", 'shot_id': s['id'], 'at': line.get('at', 0),
                                  'dur': line.get('dur'), 'text': line.get('line') or line.get('text') or ''})
        if not texts: raise ValueError('该集没有当前角色的台词')
        packet.update(voice=copy.deepcopy(voice), lines=texts, board=body['board'])
    return packet


def execute_voice(project, packet, client, folder):
    action = packet['action']
    if profile(client.cfg) != packet['profile']: raise ValueError('排队期间厂商账户发生变化，已停止')
    if action == 'voice_catalog':
        data = checked(client._post(client.base + '/v1/get_voice', {'voice_type': 'all'}, 60))
        _bill('voice', client.cfg, 'get_voice', cost=0)   # 免费查询类：记账不计费
        rows = [{**row, 'category': category} for category in ('system_voice', 'voice_cloning', 'voice_generation') for row in data.get(category, [])]
        cache = Path(__file__).resolve().parents[1] / 'cache/voices' / packet['profile'] / 'catalog.json'
        cache.parent.mkdir(parents=True, exist_ok=True)
        project_store.update_json(cache, lambda d: d.update(voices=rows), create_default={})
        return {'note': f'已获取 {len(rows)} 个云端音色'}
    if action == 'speech':
        from creation_media import register_output
        voice = packet['voice']; outputs = []
        for i, line in enumerate(packet['lines']):
            out = folder / f'{i+1:03}.mp3'
            client.generate_speech(line['text'], str(out), extra={'voice_setting': {'voice_id': voice['voice_id']}})
            media = register_output(project, {'id': packet['item_id'] + f'-{i+1}', 'type': 'speech',
                'character_id': voice['character_id'], 'character_name': voice['character_name'], 'voice_id': voice['voice_id'],
                'board': packet['board'], 'shot_id': line['shot_id'], 'vendor_id': client.id}, out)
            outputs.append({**line, 'path': 'projects/' + Path(project).name + '/' + media['material_path'], 'voice_binding': {'voice_asset_id': voice['id'], 'revision': voice['revision']}})
            # 逐句检查点，失败后保留已生成文件与归属，不重发已完成句。
            from create_media import update_item
            update_item(str(Path(project) / '创作/creation.json'), packet['item_id'], outputs=[o['path'] for o in outputs], line_outputs=outputs)
        return {'outputs': [o['path'] for o in outputs], 'line_outputs': outputs, 'voice_binding': voice, 'note': f'已按角色音色生成 {len(outputs)} 句台词'}
    ident = 'voice-' + uuid.uuid4().hex[:16]
    dest = Path(project) / '素材/音色' / ident / 'r001'
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / 'sample.mp3'
    if action == 'voice_design':
        data = checked(client._post(client.base + '/v1/voice_design', {'prompt': packet['description'], 'preview_text': packet['preview_text']}, 300))
        _bill('voice', client.cfg, 'voice_design', units={"calls": 1})   # 音色创作为计费接口
        # 官方 MiniMax-MCP 的 voice_design：voice_id + trial_audio(hex)。
        voice_id = str(data.get('voice_id') or '')
        (folder / 'voice_design_result.json').write_text(json.dumps({'voice_id': voice_id, 'profile': packet['profile'], 'local_asset_id': ident}, ensure_ascii=False), encoding='utf-8')
        if not voice_id or not data.get('trial_audio'): raise ValueError('音色创作未返回 ID 或试听音频')
        out.write_bytes(bytes.fromhex(data['trial_audio']))
    else:
        voice_id = packet['voice_id']
        client.generate_speech(packet['preview_text'], str(out), extra={'voice_setting': {'voice_id': voice_id}})
    probe(out)
    row = {'id': ident, 'revision': 1, 'name': packet['name'], 'voice_id': voice_id, 'vendor_id': client.id,
           'profile': packet['profile'], 'description': packet.get('description', ''),
           'sample': out.relative_to(Path(project)).as_posix(), 'sha256': digest(out), 'origin': action,
           'tts_verified': action == 'voice_sample'}
    (dest / 'voice.json').write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding='utf-8')
    project_store.update_json(Path(project) / '素材/音色/音色库.json', lambda d: d.setdefault('voices', []).append(row), create_default={'voices': []})
    return {'voice_asset': row, 'outputs': ['projects/' + Path(project).name + '/' + row['sample']], 'note': '音色已保存，可试听并绑定角色'}


def video_voices(project, shots):
    ids = list(dict.fromkeys(line.get('speaker') for s in shots for line in s.get('lines', []) if line.get('speaker') and line.get('speaker') != 'narrator'))
    result = []
    for ident in ids:
        voice = resolve(project, ident)
        path = inside(project, voice['sample'])
        if digest(path) != voice['sha256']: raise ValueError('音色样本版本改变')
        result.append(voice)
    return result
