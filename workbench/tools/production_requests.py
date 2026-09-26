# -*- coding: utf-8 -*-
"""将 S/V 作者内容编译为一次生成的不可变请求，并在提交前检查能力。"""
import copy
import math
import os
import sys
from pathlib import Path
from production_studio import read_board, shot_list, shot_unit, timeline, inside
from production_prompts import source_hash, media_source_hash
from production_media import bound_path, digest
from reference_limits import reference_limit
from brief import aspect_ratio_of


from video_profiles import capabilities, settings, validate_media, public_url



PERFORMANCE_NOTE = '表演指导（只补充可见表演，不改变机位/走位/台词）'

# 关键帧时间角色受控词表（update.md E07）：start=开始状态 / beat=动作关键点 / end=结束状态 / compose=构图参考。
# 标注在分镜已采用关键帧 shot['keyframe']['time_role'] 上；旧数据无该字段=未分类，兜底放行并打警告日志。
# 词表以 previs_system/tools/actor_contract.py 为唯一来源：曾在此另定义一份，
# ⑤ 演员层与 ⑦ 提交侧各自演进会让节拍标签与槽位校验口径分叉。
_CORE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "previs_system", "tools"))
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)
from actor_contract import TIME_ROLES
TIME_ROLE_LABELS = {'start': '开始状态', 'beat': '动作关键点', 'end': '结束状态', 'compose': '构图参考'}
# 首帧槽位只接受「开始状态」图、尾帧槽位只接受「结束状态」图——把结束图当首帧会让模型倒推动作。
_FRAME_SLOT_NEED = {'first_frame': 'start', 'last_frame': 'end'}
_FRAME_SLOT_LABEL = {'first_frame': '首帧', 'last_frame': '尾帧'}


def _check_frame_time_role(shot_id, time_role, frame_role):
    """按关键帧时间角色约束首帧/尾帧槽位；reference_image 不做时间角色约束。"""
    need = _FRAME_SLOT_NEED.get(frame_role)
    if not need:
        return
    slot = _FRAME_SLOT_LABEL[frame_role]
    role = str(time_role or '').strip()
    if not role:
        # 向后兼容：未分类的旧关键帧维持既有行为，只打警告提醒补标。
        print(f"[警告] {shot_id} 关键帧未标注时间角色 time_role，{slot}槽位按既有行为兜底；"
              f"建议在分镜 JSON 给该 keyframe 标注 start/end（结束图当首帧会让模型倒推动作）")
        return
    if role not in TIME_ROLES:
        print(f"[警告] {shot_id} 关键帧时间角色「{role}」不在受控词表 {TIME_ROLES}，按未分类兜底")
        return
    if role != need:
        raise ValueError(f"{shot_id} 的关键帧是「{TIME_ROLE_LABELS[role]}」（time_role={role}），不能当{slot}用；"
                         f"{slot}只接受「{TIME_ROLE_LABELS[need]}」图，请换图或改标后再提交")


def _performance_rows(board, shots, media_type):
    """已采用且未过期的演员表演 → 提交时注入行。

    复用 prompt_compiler 的节拍格式化（image 按角色取拍：优先 time_role=end，未标取末拍）与新鲜度公式
    （pop 本镜 performance 后 artifact_hash 比对）；过期表演静默不注入，
    与旧创作线行为一致——表演层永远不擅自改写镜头事实。
    """
    perf_shots = [x for x in shots if isinstance(x.get('performance'), dict) and x['performance'].get('status') == 'ready']
    if not perf_shots: return []
    from prompt_compiler import _performance_text
    rows = []
    for shot in perf_shots:
        perf = shot['performance']
        if perf.get('source_hash'):
            try:
                from artifact_provenance import artifact_hash
                probe = copy.deepcopy(board)
                for item in probe.get('shots') or []:
                    if str(item.get('id')) == str(shot.get('id')): item.pop('performance', None); break
                if perf['source_hash'] != artifact_hash(probe, 'prompt', 'actor-v1'): continue
            except Exception:
                continue
        for r in _performance_text(perf, board.get('actors') or {}, media_type):
            rows.append(f"{shot['id']} {r}" if media_type == 'video' else r)
    return rows

def _plan_refs_enabled(project, body):
    """平面图参考帧注入开关：请求体 plan_refs 优先，缺省读项目 brief.plan_refs（默认开）。
    brief 读取异常回 True（宁可注入也不改变旧链路容错性）。"""
    if body.get('plan_refs') is not None:
        return bool(body.get('plan_refs'))
    try:
        from brief import load_brief
        return bool(load_brief(str(project)).get('plan_refs', True))
    except Exception:
        return True


def compile_request(project, body, cfg):
    board, revision = read_board(project, body['board'])
    if body.get('revision') and body['revision'] != revision: raise ValueError('分镜已被修改，请刷新后重试')
    scope, target = body.get('scope'), str(body.get('target') or '')
    if scope == 'S':
        shot = next((s for s in board.get('shots', []) if s['id'] == target), None)
        if not shot: raise ValueError('转场镜头不存在')
        unit = shot_unit(board, shot)
        if body.get('duration') is not None: unit['duration'] = body['duration']
        shots = [shot]
        prompt = str(shot.get('prompt_' + body['type']) or (shot.get('prompt') if body['type'] == 'image' else '') or '')
        description = str(shot.get('prompt_grid') or '')
        label = target
    elif scope == 'V':
        units = board.get('video_units', [])
        source = next((u for u in units if u['id'] == target), None)
        if not source: raise ValueError('分镜视频不存在')
        if body['type'] != 'video': raise ValueError('关键帧按 S 生成，请先选择转场镜头')
        unit = copy.deepcopy(source)
        shots = shot_list(board, unit)
        if source.get('source_hash') != source_hash(shots): raise ValueError('分镜视频汇总已过期，请更新或确认汇总后再生成')
        if body.get('duration') is not None: unit['duration'] = body['duration']
        prompt = str(unit.get('prompt_video') or '')
        description = str(unit.get('prompt_grid') or '')
        label = f'V{units.index(source)+1:02}'
    else: raise ValueError('必须选择 S 转场镜头或 V 分镜视频')
    kind = body['type']
    if not prompt.strip(): raise ValueError('当前类型的提示词为空，请先由 LLM 补全或手动编辑')
    if kind not in ('image', 'video'): raise ValueError('制作请求只支持关键帧或视频')
    # 制作规格（E05）：画幅缺省读 brief.aspect_ratio；无 brief 保持 16:9
    aspect = aspect_ratio_of(project)
    video_options = settings(cfg, {**(body.get('video_options') or {}), 'duration': unit['duration']}) if kind == 'video' else {}
    video_mode = video_options.get('mode', '')
    refs = []
    if kind == 'image':
        from prompt_assembler import resolve_shot_refs
        refs = resolve_shot_refs(shots[0], str(project), actors=board.get('actors'), board_name=body['board'], max_refs=100, board=board)
        from asset_registry import AssetRegistry
        assets = {r.get('path'): r for r in AssetRegistry(str(project)).list() if r.get('path')}
        for r in refs:
            r['sha256'] = digest(inside(project, r['path']))
            asset = assets.get(r['path'], {})
            r['asset_ref'] = asset.get('ref', ''); r['name'] = asset.get('name', '')
    else:
        # 宫格模式：参考图 = 已采用的故事板宫格（产物经人工「采用」闸进入参考链，
        # 绝不隐式抓取最新候选/关键帧排版）；未采用时明确报错指引。
        frame_shots = shots if video_mode == 'reference' and (body.get('ref_mode') or 'keyframes') != 'grid' else []
        if (body.get('ref_mode') or 'keyframes') == 'grid':
            gb = unit.get('grid_binding')
            if not isinstance(gb, dict) or not gb.get('path'):
                raise ValueError('本 V 尚未采用故事板宫格：请先在①整 V 直出生成宫格图并点「采用为宫格参考」')
            r = copy.deepcopy(gb); bound_path(project, r)
            if r.get('source_hash') and r['source_hash'] != media_source_hash(shots, 'image', unit):
                # 必须与 adopt_grid 写入侧同公式（都是 shot_list(board, unit)）；此处原写 members，
                # 该名字在本函数从未定义——走到"已过期"这一支会抛 NameError，友好提示永远出不来。
                raise ValueError('宫格参考已过期（成员 S 素材变动），请重新生成宫格图并采用')
            r['purpose'] = '故事板宫格参考图'; r['frame_role'] = 'reference_image'
            refs.append(r)
        if video_mode in ('first_frame','first_last') and (body.get('continuity') or {}).get('mode') != 'tail_first_frame':
            sid = body.get('first_shot_id') or shots[0]['id']
            frame_shots += [next((s for s in shots if s['id'] == sid), None)]
        if video_mode in ('last_frame','first_last'):
            sid = body.get('last_shot_id') or shots[-1]['id']
            frame_shots += [next((s for s in shots if s['id'] == sid), None)]
        if any(s is None for s in frame_shots): raise ValueError('首尾帧必须来自当前创作范围内的 S')
        for s in frame_shots:
            if not s.get('keyframe'): raise ValueError(f"{s['id']} 尚未采用关键帧")
            r = copy.deepcopy(s['keyframe']); bound_path(project, r)
            if r.get('source_hash') and r['source_hash'] != media_source_hash([s], 'image', shot_unit(board, s)):
                raise ValueError(f"{s['id']} 关键帧来源已过期，请确认画面后重新采用")
            r['purpose'] = f"{s['id']} 的已采用关键帧"; r['shot_id'] = s['id']
            r['frame_role'] = 'reference_image' if video_mode == 'reference' else 'last_frame' if video_mode == 'last_frame' or (video_mode == 'first_last' and len(refs) == (0 if (body.get('continuity') or {}).get('mode') == 'tail_first_frame' else 1)) else 'first_frame'
            # E07：首帧槽位只准 start 角色关键帧、尾帧槽位只准 end 角色；未标注的旧图兜底放行并打警告。
            _check_frame_time_role(s['id'], r.get('time_role'), r['frame_role'])
            if (body.get('image_urls') or {}).get(s['id']): r['public_url'] = public_url(body['image_urls'][s['id']])
            refs.append(r)
        beats = timeline(board, unit)
        if any(not b['prompt'].strip() for b in beats): raise ValueError('成员 S 尚缺视频提示词，请先补全提示词')
        prompt += '\n时间轴（秒）：\n' + '\n'.join(f"{b['start']:g}–{b['end']:g}｜{b['shot_id']}：{b['prompt']}" for b in beats)
    negative = str(unit.get('negative') or '')
    # 风格仍由项目/资产 Skill 提供，避免从旧分镜自由文本带入过期画风。
    # 必须走镜头侧过滤：整篇 skill 正文含「三视图/纯白背景/表情中性」，
    # 会把单镜关键帧与 V 视频拉成白底设定图。
    from prompt_assembler import skill_positive
    style = skill_positive(str(project))
    if style: prompt += '\n画风：' + style
    if kind == 'image':
        prompt += f'\n只绘制一张独立关键帧，画幅 {aspect}。'
    else:
        prompt += f"\n总时长 {float(unit['duration']):g} 秒，画幅 {video_options.get('ratio', aspect)}。连续视频，保持人物身份与场景空间连续。"
    perf_rows = _performance_rows(board, shots, kind)
    if perf_rows: prompt += '\n' + PERFORMANCE_NOTE + '：' + '；'.join(perf_rows)
    # 平面图参考回流（plan v1）：V 视频走全能参考且非宫格时，把成员 S 的平面图帧按参考图
    # 预算注入 refs（必含首尾），并补一句空间约束；无 plan/渲染失败/预算耗尽均静默跳过。
    # 只加参考图与一句约束，不动镜头事实。开关：body.plan_refs > brief.plan_refs（默认开）。
    plan_ref_count = 0
    if (scope == 'V' and kind == 'video' and video_mode == 'reference'
            and (body.get('ref_mode') or 'keyframes') != 'grid'
            and _plan_refs_enabled(project, body)):
        try:
            _model = (cfg.get('models') or {}).get(kind)
            _budget = reference_limit(cfg['id'], _model, kind, cfg) - len(refs)
            if _budget > 0:
                from plan_frames import plan_ref_frames
                _prefs = plan_ref_frames(str(project), body['board'], shots, label, _budget, log=print)
                if _prefs:
                    refs.extend(_prefs)
                    plan_ref_count = len(_prefs)
                    prompt += '\n场景空间布局与人物走位以参考平面图为准，保持画左画右关系一致。'
        except Exception as _e:
            print(f"[警告] 平面图参考帧注入失败（忽略，不影响编译）: {_e}")
    negs = []
    for s in shots:
        n = s.get('negative') or []
        negs.extend(n if isinstance(n, list) else [n])
    negative = '；'.join(dict.fromkeys([negative, '文字、水印、字幕、边框'] + [str(n) for n in negs if n])).strip('；')
    continuity = body.get('continuity') or {}
    mode = continuity.get('mode', '')
    if mode not in ('', 'tail_context', 'tail_first_frame'): raise ValueError('尾帧模式不合法')
    if mode:
        if kind != 'video': raise ValueError('只有视频可以关联尾帧')
        from production_media import binding
        source = binding(project, continuity.get('item_id'), continuity.get('output_index', 0), 'video')
        if source.get('board') == body['board'] and ((scope == 'V' and source.get('unit_id') == target) or (scope == 'S' and source.get('shot_id') == target)):
            raise ValueError('不能将当前目标的视频引用为自己的前序视频')
        continuity = {'mode': mode, 'source': source, 'vision_vendor': str(continuity.get('vision_vendor') or '')}
        if mode == 'tail_context' and not continuity['vision_vendor']: raise ValueError('尾帧画面参考需要选择 Vision 模型')
    ref_mode = body.get('ref_mode') or 'keyframes'
    if ref_mode not in ('keyframes', 'grid'): raise ValueError('参考模式不合法')
    # 宫格提示词留空 = 默认九宫格 3×3 按剧情时间顺序，不再强制填写
    cap = capabilities(cfg)
    if kind == 'video' and ref_mode == 'grid' and video_mode != 'reference': raise ValueError('故事板宫格只用于全能参考')
    voices = []
    if body.get('include_voices'):
        if kind != 'video' or video_mode != 'reference' or not cap['audio_refs']: raise ValueError('当前模式不支持音色参考')
        from voice_assets import video_voices
        voices = video_voices(project, shots)
    from reference_media import resolve
    audio_media = resolve(project, body.get('audio_urls', []), 'audio', cfg) if kind == 'video' else []
    video_media = resolve(project, body.get('video_urls', []), 'video', cfg) if kind == 'video' else []
    audio_urls = [r.get('url', r.get('path')) for r in audio_media]
    video_urls = [r.get('url', r.get('path')) for r in video_media]
    if kind == 'video':
        if mode == 'tail_first_frame' and video_mode not in ('first_frame','first_last'):
            raise ValueError('尾帧强制续接需要选择首帧或首尾帧模式')
        identifiers = list(range(len(refs)))
        first = next((str(i) for i,r in enumerate(refs) if r.get('frame_role') == 'first_frame'), None)
        last = next((str(i) for i,r in enumerate(refs) if r.get('frame_role') == 'last_frame'), None)
        if mode == 'tail_first_frame': first = 'previous_tail'
        check_refs = [str(i) for i in identifiers]
        if ref_mode == 'grid': check_refs = ['grid']
        validate_media(cap, video_mode, check_refs, first, last, voices + audio_urls, video_urls)
        if cap['transport'] == 'public_url':
            if mode == 'tail_first_frame' or ref_mode == 'grid': raise ValueError('Agnes 需要成品帧的公网 URL；请先导出该帧后绑定 URL')
            if any(not r.get('public_url') for r in refs): raise ValueError('Agnes 参考图需要公网 URL，请在参考设置为每张帧填写对应 URL')
        if voices and cfg['id'] in ('agnes','aliyun'): raise ValueError('此接口音频需要公网 URL，请取消本地音色引用，填写参考音频 URL')
    count = 1 if ref_mode == 'grid' and refs else len(refs)
    model = (cfg.get('models') or {}).get(kind)
    image_mode = ''
    if kind == 'image':
        from creation_media import image_route
        _, image_mode, model = image_route(cfg, bool(refs))
    # S 图结构化画幅（N80 extra 的编译期来源）：body 显式指定优先，缺省落 brief.aspect_ratio
    image_options = dict(body.get('image_options') or {})
    if kind == 'image': image_options.setdefault('ratio', aspect)
    if count > reference_limit(cfg['id'], model, kind, cfg):
        raise ValueError(f'参考图数量 {count} 超过模型上限，请拆分 V 或明确选择宫格；不会丢弃图片')
    return {'scope': scope, 'target': target, 'label': label, 'board': body['board'], 'board_revision': revision,
            'shots': copy.deepcopy(shots), 'unit': unit, 'source_hash': media_source_hash(shots, kind, unit), 'type': kind,
            'prompt': prompt, 'negative': negative, 'refs': refs, 'ref_mode': ref_mode, 'prompt_grid': description,
            'video_options': video_options, 'image_options': image_options, 'audio_urls': audio_urls, 'video_urls': video_urls,
            'audio_media': audio_media, 'video_media': video_media,
            'duration': unit.get('duration'), 'continuity': continuity, 'vendor_id': cfg['id'],
            'model': model, 'image_mode': image_mode, 'include_voices': bool(body.get('include_voices')), 'voices': voices,
            'plan_refs': plan_ref_count}
