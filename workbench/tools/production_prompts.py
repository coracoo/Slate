# -*- coding: utf-8 -*-
"""分镜三类作者提示词；编译层不得反写或相互补齐。"""
import hashlib
import json

FIELDS = ('prompt_image', 'prompt_video', 'prompt_grid')
# LLM 生成面只覆盖前两类；宫格是确定性排版（make_grid 按固定行列排已采用关键帧），
# prompt_grid 只是宫格参考模式用的元数据说明——按需人工配置，不让模型逐镜付费生成。
LLM_FIELDS = ('prompt_image', 'prompt_video')
EDIT_FORMAT = '''输出格式：每段以【S镜号（起点—终点s）：开头，以】结束，顺序为序号、时长、景别、镜头（焦距/机位）、运镜、画面呈现（内容、人物、动作、声音、台词）、光影。未知事实不得编造。
以用户当前框体文字为主要依据，保留用户新增的创意、人物和动作，分镜结构字段只补缺；不要用旧默认提示词覆盖当前内容。
静帧只描写一个瞬间；运镜/声音/台词仅作为拍摄上下文，不把时间编号、文字或对白画入图像。视频描述连续动作。两者不可混用。'''


def format_shot_prompt(shot, text, start=0):
    """从同一分镜字段构造编辑默认值；已结构化的人工文本原样保留。"""
    if str(text or '').lstrip().startswith('【'):
        return str(text)
    end = start + float(shot.get('dur') or 4)
    lines = '；'.join(f"{line.get('speaker', '')}：“{line.get('line') or line.get('text') or ''}”" for line in shot.get('lines', []))
    parts = [shot.get('shot_size'), shot.get('angle'), shot.get('lens'), shot.get('camera_move'),
             text or shot.get('content'), shot.get('action'), shot.get('sound'), lines,
             shot.get('lighting') or shot.get('light')]
    return f"【{shot['id']}镜（{start:.1f}—{end:.1f}s）：" + '；'.join(str(p) for p in parts if p) + '】'


CONTRACT = '''
【制作提示词契约 v3，必须随每个 S 转场镜头一起生成】
同一次 JSON 输出的每个 shots 元素必须另外包含两个独立字符串：
prompt_image：单张参考关键帧，选择可辨认的一个动作瞬间，写姿态、位置、视线、景别、光线。
只能一幅画面，不写连续阶段、运镜过程或宫格；不要字幕/对白框/文字/水印。
prompt_video：本镜完整连续动作，写开始状态→动作发展→结束状态，机位与运镜、节奏、声音和原文台词。
不得写“输出单张/冻结/三视图/拼图”；不要把后期字幕当作画面元素。
两个字段各 60~140 字，内容不同；人物与场景用现有 @character/@scene/@prop ID 引用，不复制资产外观。
两个字段统一按【S镜号（起点—终点s）：景别；镜头/机位；运镜；画面内容、人物、动作、声音、台词；光影】组织。静帧中的时间、运镜和声音仅作上下文，不作为画面元素。
旧 prompt 字段可省略，系统将以 prompt_image 提供兼容视图。
保留 scene_ref、dur、动作和台词事实；narrator 仅存在台词轨。
不要生成宫格/布局文案（prompt_grid）——宫格提示词=分格布局说明（格数布局如 3×3 + 每格关键瞬间），由人工按需填写，宫格图由生图模型一次调用生成。
同时返回 video_units 数组：按实际 scene_ref 将相邻 S 组合为 V 分镜视频，不能跨场景或跳过/重复/调换镜头。
格式 [{"shot_ids":["S1","S2"],"title":"场景段落","prompt_video":"承接动作与空间关系的整段视频描述","negative":"整段共用负面约束"}]。
分组按原 S 顺序完整覆盖；总时长为成员 dur 之和，不能增删剧情或改写台词。
'''


def normalize_prompts(shot):
    """旧 prompt 只兼容静帧；缺少的动态/宫格保留为空待 LLM 补全。"""
    if not shot.get('prompt_image') and shot.get('prompt'):
        shot['prompt_image'] = str(shot['prompt'])
    if shot.get('prompt_image'):
        shot['prompt'] = shot['prompt_image']
    return shot


def require_prompts(shots):
    """按用途校验：关键帧与视频两类必须齐且互不相同；宫格为可选人工字段不在此列。"""
    for shot in shots:
        values = [str(shot.get(k) or '').strip() for k in LLM_FIELDS]
        if not all(values) or len(set(values)) != len(LLM_FIELDS):
            raise ValueError(f"{shot.get('id')} 的提示词缺失或两类内容重复，拒绝以一类内容补齐另一类")


def fingerprint(value):
    def normalize(v):
        if isinstance(v, float) and v.is_integer(): return int(v)
        if isinstance(v, dict): return {k: normalize(x) for k, x in v.items()}
        if isinstance(v, list): return [normalize(x) for x in v]
        return v
    return hashlib.sha256(json.dumps(normalize(value), ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def source_hash(shots):
    fields = ('id', 'dur', 'scene_ref', 'actor_refs', 'prop_refs', 'action', 'content',
              'shot_size', 'camera_move', 'angle', 'lighting', 'lines', 'negative') + FIELDS
    value = [{k: s.get(k) for k in fields} for s in shots]
    # transition 只在"这本分镜确实写了转场"时并入指纹（同本文件 media_source_hash 的 performance 先例）：
    # 无条件加键会让全部存量 V 的 source_hash 一次性对不上、集体判过期，
    # 而 09-25 实测 10 本分镜里只有 2 本用得到 transition（7 个 V 会被无端打回重写）。
    trans = [[s.get('id'), s.get('transition')] for s in shots if s.get('transition')]
    if not trans:
        return fingerprint(value)
    return fingerprint({'shots': value, 'transition': trans})


def media_source_hash(shots, kind, unit=None):
    """按消费阶段计算依赖：视频/宫格文字修改不会使静帧无端失效。"""
    fields = ('id', 'scene_ref', 'actor_refs', 'prop_refs', 'action', 'content', 'shot_size', 'angle', 'lighting', 'negative')
    values = [{k: s.get(k) for k in fields + (('prompt_image',) if kind == 'image' else ('dur', 'prompt_video', 'lines', 'camera_move'))} for s in shots]
    payload = {'shots': values, 'kind': kind}
    # 转场会改写 S 图提示词里的"本镜如何接上下镜"，所以它变了静帧就该重出；
    # 同样只在真写了 transition 时并入，避免无端把存量产物判过期。
    trans = [[s.get('id'), s.get('transition')] for s in shots if s.get('transition')]
    if trans:
        payload['transition'] = trans
    if kind == 'image': payload['shared_negative'] = (unit or {}).get('negative') or ''
    if kind == 'video':
        payload['keyframes'] = [(s.get('keyframe') or {}).get('sha256') for s in shots]
        payload['unit'] = {k: (unit or {}).get(k) for k in ('id', 'duration', 'prompt_video', 'prompt_grid', 'negative', 'generation_options')}
        # 采用/更换演员表演后旧视频应提示重生成；仅在存在表演时并入指纹，保证无表演的旧绑定不误报过期
        perf = [(s.get('id'), (s.get('performance') or {}).get('status') or '', (s.get('performance') or {}).get('source_hash') or '') for s in shots]
        if any(p[1] for p in perf): payload['performance'] = perf
    return fingerprint(payload)
