# -*- coding: utf-8 -*-
"""项目音色资产与角色绑定。云端音色 ID 的作用域是当前厂商账户。"""
import copy
import hashlib
import json
import shutil
import urllib.request
import uuid
from pathlib import Path
from production_studio import project_store, inside, read_board
from production_media import digest, probe
from native_media import checked
import versions

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


NARRATOR = 'narrator'   # 保留说话人：旁白不是人物素材（禁建档口径不变），音色绑定单独存 sidecar


def narrator_file(project):
    return Path(project) / '素材' / '音色' / '旁白.json'


def narrator_binding(project):
    """旁白的音色绑定；没有 sidecar 或未绑定都返回 {}（未绑定=不生成不注入，不是错误）。"""
    path = narrator_file(project)
    if not path.exists():
        return {}
    return (project_store.read_json(str(path))[0] or {}).get('voice_binding') or {}


def profile(cfg):
    # 缓存不跨账户共用，不将原始密钥写入项目资产。
    return hashlib.sha256((cfg.get('base_url', '') + '\n' + cfg.get('api_key', '')).encode()).hexdigest()[:20]


def state(project, cfg=None):
    chars = characters(project)
    # 分状态音色绑定要展示派生状态图：探测约定路径 素材/人物/<角色id>__<状态id>.png
    for c in chars:
        for s in c.get('states') or []:
            img = Path(project) / '素材' / '人物' / f"{c.get('id')}__{s.get('id')}.png"
            if img.is_file():
                s['image'] = img.relative_to(Path(project)).as_posix()
    # 旁白作为"可绑定音色的说话人"出现在 ④ 列表里，但只进响应、不进人物档案（禁建档口径不变）
    narrator_row = {'id': NARRATOR, 'name': '旁白', 'reserved': True,
                    'voice_binding': narrator_binding(project) or None}
    result = {'characters': chars + [narrator_row], **library(project), 'catalog': []}
    if cfg:
        cache = Path(__file__).resolve().parents[1] / 'cache/voices' / profile(cfg) / 'catalog.json'
        if cache.exists(): result['catalog'] = json.loads(cache.read_text(encoding='utf-8'))['voices']
    return result


AUDIO_EXT = ('.mp3', '.wav', '.m4a', '.ogg', '.flac')

def upload_voice(project, name, stream, length):
    """上传本地音色参考文件（mp3/wav/m4a/ogg/flac）入库：素材/音色/<id>/r001/sample.<ext> + 音色库.json 登记。"""
    ext = Path(name).suffix.lower()
    if ext not in AUDIO_EXT: raise ValueError('音色文件仅支持 mp3 / wav / m4a / ogg / flac')
    if not 0 < length <= 100 * 1024 * 1024: raise ValueError('文件必须非空且不超过 100 MB')
    ident = 'voice-' + uuid.uuid4().hex[:16]
    dest = Path(project) / '素材' / '音色' / ident / 'r001'
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / ('sample' + ext)
    try:
        with out.open('xb') as f:
            left = length
            while left:
                chunk = stream.read(min(left, 1024 * 1024))
                if not chunk: raise ValueError('上传中断，请重新选择文件')
                f.write(chunk); left -= len(chunk)
        probe(out)
    except Exception:
        out.unlink(missing_ok=True)
        raise ValueError('文件不是可解析的音频，请检查格式后重新上传')
    row = {'id': ident, 'revision': 1, 'name': Path(name).stem, 'voice_id': '', 'vendor_id': 'local',
           'profile': 'local', 'description': '', 'sample': out.relative_to(Path(project)).as_posix(),
           'sha256': digest(out), 'origin': 'upload', 'tts_verified': False}
    (dest / 'voice.json').write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding='utf-8')
    def add(data):
        data.setdefault('voices', []).append(row)
    project_store.update_json(Path(project) / '素材' / '音色' / '音色库.json', add, create_default={'voices': []})
    return row


def resolve(project, character_id, voice_asset_id=None):
    if character_id == NARRATOR:
        if voice_asset_id and voice_asset_id != narrator_binding(project).get('voice_asset_id'):
            raise ValueError('旁白只有一个绑定，不支持按派生音色选择')
        binding = narrator_binding(project)
        voice = next((v for v in library(project)['voices']
                      if v['id'] == binding.get('voice_asset_id') and v['revision'] == binding.get('revision')), None)
        if not voice: raise ValueError('旁白尚未绑定音色')
        return {**voice, 'character_id': NARRATOR, 'character_name': '旁白'}
    actor = next((c for c in characters(project) if c.get('id') == character_id), None)
    if not actor: raise ValueError('角色不存在')
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
    if str(body.get('character_id') or '') == NARRATOR:
        # 旁白的绑定写在 sidecar（素材/音色/旁白.json）：人物档案里永不出现 narrator，
        # validate_dialogue 的 RESERVED_ACTOR / 禁建档口径保持不变。
        if body.get('state') or body.get('mode') == 'variant':
            raise ValueError('旁白没有角色状态，不支持分状态或派生音色')

        def mutate_narrator(data):
            ref = str(body.get('voice_asset_id') or '').strip()
            if not ref:
                data['voice_binding'] = {}   # 解绑=回到"不出声、不注入"
                return data
            voice = next((v for v in library(project)['voices'] if v['id'] == ref
                          and v['revision'] == body.get('revision')), None)
            if not voice or not voice.get('sample'): raise ValueError('请先保存可试听的音色资产')
            inside(project, voice['sample'])
            data['voice_binding'] = {'voice_asset_id': voice['id'], 'revision': voice['revision'], 'scope': 'narrator'}
            return data
        path = narrator_file(project)
        path.parent.mkdir(parents=True, exist_ok=True)
        project_store.update_json(str(path), mutate_narrator,
                                  create_default={'voice_binding': {}}, snapshot=versions.snapshot)
        return {'ok': True, 'binding': narrator_binding(project)}
    state_id = str(body.get('state') or '').strip()
    if state_id:
        # 分状态音色：不复制资产，仅在角色的 voice_variants 上挂/摘状态链接（voice_asset_id 为空=恢复跟随全剧默认）
        actor = next((c for c in characters(project) if c.get('id') == body.get('character_id')), None)
        if not actor: raise ValueError('角色不存在')
        label = next((str(s.get('label') or '') for s in actor.get('states') or [] if s.get('id') == state_id), '')
        if not label: raise ValueError('派生状态不存在，请先在素材提炼中定义状态资产')
        voice_ref = str(body.get('voice_asset_id') or '').strip()
        voice = next((v for v in library(project)['voices'] if v['id'] == voice_ref), None) if voice_ref else None
        if voice_ref and (not voice or not voice.get('sample')): raise ValueError('请先保存可试听的音色资产')
        def mutate_state(data):
            row = next((c for c in data['characters'] if c.get('id') == actor['id']), None)
            if not row: raise ValueError('角色不存在')
            variants = [v for v in row.get('voice_variants') or [] if v.get('state') != state_id]
            if voice:
                variants.append({'voice_asset_id': voice['id'], 'revision': voice['revision'],
                                 'name': '角色音乐·' + label, 'state': state_id})
            if variants: row['voice_variants'] = variants
            else: row.pop('voice_variants', None)
        project_store.update_json(Path(project) / '素材' / '人物.json', mutate_state)
        return {'ok': True}
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


CLONE_EXT = ('.mp3', '.m4a', '.wav')

def minimax_upload_file(client, path):
    """multipart 上传音频到 /v1/files/upload（purpose=voice_clone），返回 file_id（音色复刻第一步）。"""
    boundary = '----slate' + uuid.uuid4().hex
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="purpose"\r\n\r\nvoice_clone\r\n').encode()
    body += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
             f'Content-Type: audio/mpeg\r\n\r\n').encode() + path.read_bytes() + b'\r\n'
    body += f'--{boundary}--\r\n'.encode()
    req = urllib.request.Request(client.base + '/v1/files/upload', data=body, method='POST',
        headers={'Authorization': 'Bearer ' + (client.cfg.get('api_key') or ''),
                 'Content-Type': 'multipart/form-data; boundary=' + boundary})
    with urllib.request.urlopen(req, timeout=300) as r:
        return int(checked(json.loads(r.read().decode('utf-8')))['file']['file_id'])


def prepare(project, body, cfg):
    action = body['action']
    if not cfg: raise ValueError('请先在环境页启用并配置厂商')
    if action != 'speech' and cfg.get('id') != 'minimax':
        raise ValueError('音色库 / 试听 / 创作 / 复刻仅支持 MiniMax；台词配音可在下方选择其他已配置语音的厂商')
    if cfg.get('id') == 'minimax' and not str(cfg.get('api_key') or '').strip():
        raise ValueError('MiniMax 未配置 API Key，请到环境页填写并保存后再试')
    if action != 'speech' or cfg.get('id') == 'minimax':
        packet = {'type': 'speech' if action == 'speech' else 'voice', 'vendor_id': cfg['id'], 'profile': profile(cfg)}
    else:
        # 本地 / 第三方 TTS：只要求配置了语音模型或端点，不要求 api_key（本地服务常无鉴权）
        if not (cfg.get('models') or {}).get('speech') and not (cfg.get('endpoints') or {}).get('speech'):
            raise ValueError('该厂商未配置语音模型或端点，请到环境页补全后重试')
        packet = {'type': 'speech', 'vendor_id': cfg['id'], 'profile': profile(cfg)}
    if action == 'voice_sample':
        if not str(body.get('voice_id') or '').strip(): raise ValueError('请选择云端音色')
        packet.update(voice_id=str(body['voice_id']), name=str(body.get('name') or body['voice_id']),
                      preview_text=str(body.get('preview_text') or '你好，这是角色音色试听。'))
    elif action == 'voice_design':
        if not str(body.get('description') or '').strip(): raise ValueError('请填写音色描述')
        packet.update(description=str(body['description']), name=str(body.get('name') or 'AI 创作音色'),
                      preview_text=str(body.get('preview_text') or '你好，这是为角色创作的声音。'))
    elif action == 'voice_clone':
        voice = next((v for v in library(project)['voices'] if v['id'] == body.get('voice_asset_id')), None)
        if not voice or not voice.get('sample'): raise ValueError('请先上传要复刻的本地音色样本')
        src = inside(project, voice['sample'])
        if Path(src).suffix.lower() not in CLONE_EXT: raise ValueError('复刻仅支持 mp3 / m4a / wav 样本，请重新上传')
        if Path(src).stat().st_size > 20 * 1024 * 1024: raise ValueError('复刻样本需不超过 20MB')
        try: dur = float((probe(src).get('format') or {}).get('duration') or 0)
        except Exception: dur = 0
        if not 10 <= dur <= 300: raise ValueError(f'复刻样本时长需 10 秒–5 分钟，当前约 {dur:.0f} 秒，请裁剪后重新上传')
        packet.update(voice_asset_id=voice['id'], source_sample=voice['sample'], source_name=voice['name'],
                      name=str(body.get('name') or '').strip() or (voice['name'] + '·复刻'),
                      preview_text=str(body.get('preview_text') or '你好，这是克隆出来的角色音色。'),
                      custom_voice_id=str(body.get('custom_voice_id') or '').strip())
    elif action == 'speech':
        ident = str(body.get('character_id') or '')
        if ident == NARRATOR and not narrator_binding(project):
            # 旁白不套用厂商默认音色：未绑定=不出声，仍旧留在台词轨由后期人声轨处理（用户 09-25 定版口径）
            raise ValueError('旁白尚未绑定音色：不绑定则不生成配音，请到 ④ 音色绑定为旁白选一个音色')
        try:
            voice = resolve(project, str(body.get('character_id') or ''), body.get('voice_asset_id'))
            if voice['profile'] != packet['profile']:
                voice = None   # 绑定的音色属于其他厂商账户：非 MiniMax 语音回落厂商默认音色
        except ValueError:
            voice = None
        if voice is None:
            if cfg.get('id') == 'minimax':
                raise ValueError('该说话人尚未绑定本厂商音色，请先在音色页试听并绑定')
            voice = {'character_id': str(body.get('character_id') or ''), 'character_name': '', 'id': '',
                     'voice_id': (cfg.get('extra') or {}).get('voice') or 'alloy', 'profile': packet['profile']}
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
            if client.id == 'minimax':
                extra = {'voice_setting': {'voice_id': voice['voice_id']}}
            else:
                extra = {'voice': voice['voice_id'] or 'alloy'}
            client.generate_speech(line['text'], str(out), extra=extra)
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
    elif action == 'voice_clone':
        # 音色复刻：本地样本 → /v1/files/upload → /v1/voice_clone → 下载 demo_audio 入库
        file_id = minimax_upload_file(client, inside(project, packet['source_sample']))
        custom = packet.get('custom_voice_id') or ('svclone-' + uuid.uuid4().hex[:10])
        data = checked(client._post(client.base + '/v1/voice_clone', {
            'file_id': file_id, 'voice_id': custom,
            'model': client.models.get('speech') or 'speech-02-hd',
            'text': packet['preview_text']}, 300))
        _bill('voice', client.cfg, 'voice_clone', units={"chars": len(packet['preview_text'])})
        voice_id = str(data.get('voice_id') or custom)
        (folder / 'voice_clone_result.json').write_text(json.dumps(
            {'voice_id': voice_id, 'file_id': file_id, 'source': packet['source_name'], 'local_asset_id': ident}, ensure_ascii=False), encoding='utf-8')
        demo = str(data.get('demo_audio') or '')
        if demo:
            with urllib.request.urlopen(demo, timeout=120) as r: out.write_bytes(r.read())
        else:
            shutil.copyfile(inside(project, packet['source_sample']), out)
    else:
        voice_id = packet['voice_id']
        client.generate_speech(packet['preview_text'], str(out), extra={'voice_setting': {'voice_id': voice_id}})
    probe(out)
    row = {'id': ident, 'revision': 1, 'name': packet['name'], 'voice_id': voice_id, 'vendor_id': client.id,
           'profile': packet['profile'],
           'description': (f"由本地样本「{packet['source_name']}」复刻；复刻音色 7 天内需正式合成台词，否则云端将删除"
                           if action == 'voice_clone' else packet.get('description', '')),
           'sample': out.relative_to(Path(project)).as_posix(), 'sha256': digest(out), 'origin': action,
           'tts_verified': action in ('voice_sample', 'voice_clone')}
    (dest / 'voice.json').write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding='utf-8')
    project_store.update_json(Path(project) / '素材/音色/音色库.json', lambda d: d.setdefault('voices', []).append(row), create_default={'voices': []})
    return {'voice_asset': row, 'outputs': ['projects/' + Path(project).name + '/' + row['sample']], 'note': '音色已保存，可试听并绑定角色'}


def video_voices(project, shots):
    # 旁白不再无条件排除：绑了音色就随其他说话人一起注入，未绑定则跳过（不生成不注入，出声留给后期人声轨）
    bound = bool(narrator_binding(project))
    ids = list(dict.fromkeys(line.get('speaker') for s in shots for line in s.get('lines', [])
                             if line.get('speaker') and (bound or line.get('speaker') != 'narrator')))
    result = []
    for ident in ids:
        voice = resolve(project, ident)
        path = inside(project, voice['sample'])
        if digest(path) != voice['sha256']: raise ValueError('音色样本版本改变')
        result.append(voice)
    return result
