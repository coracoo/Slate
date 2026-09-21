# -*- coding: utf-8 -*-
"""分镜三类作者提示词；编译层不得反写或相互补齐。"""
import hashlib
import json
import re

FIELDS = ('prompt_image', 'prompt_video', 'prompt_grid')
EDIT_FORMAT = '''输出格式：每段以【S镜号（起点—终点s）：开头，以】结束，顺序为序号、时长、景别、镜头（焦距/机位）、运镜、画面呈现（内容、人物、动作、声音、台词）、光影。未知事实不得编造。
以用户当前框体文字为主要依据，保留用户新增的创意、人物和动作，分镜结构字段只补缺；不要用旧默认提示词覆盖当前内容。
静帧只描写一个瞬间；运镜/声音/台词仅作为拍摄上下文，不把时间编号、文字或对白画入图像。视频描述连续动作；宫格描述有序画格，三者不可混用。'''


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


def retime_prompt(text, ident, start, end):
    """只更新时间标签，不重写用户正文。"""
    return re.sub(r'^【' + re.escape(ident) + r'镜（[^）]*）：',
                  f'【{ident}镜（{start:.1f}—{end:.1f}s）：', str(text or ''))
CONTRACT = '''
【制作提示词契约 v2，必须随每个 S 转场镜头一起生成】
同一次 JSON 输出的每个 shots 元素必须另外包含三个独立字符串：
prompt_image：单张参考关键帧，选择可辨认的一个动作瞬间，写姿态、位置、视线、景别、光线。
只能一幅画面，不写连续阶段、运镜过程或宫格；不要字幕/对白框/文字/水印。
prompt_video：本镜完整连续动作，写开始状态→动作发展→结束状态，机位与运镜、节奏、声音和原文台词。
不得写“输出单张/冻结/三视图/拼图”；不要把后期字幕当作画面元素。
prompt_grid：用于故事板的格子规划，写本镜起始/发展/落点的可视瞬间、格序与统一机位/人物/场景约束。
它是宫格布局说明，绝不能直接作为单张参考帧或视频提示词。
三个字段各 60~140 字，内容不同；人物与场景用现有 @character/@scene/@prop ID 引用，不复制资产外观。
三个字段统一按【S镜号（起点—终点s）：景别；镜头/机位；运镜；画面内容、人物、动作、声音、台词；光影】组织。静帧中的时间、运镜和声音仅作上下文，不作为画面元素。
旧 prompt 字段可省略，系统将以 prompt_image 提供兼容视图。
保留 scene_ref、dur、动作和台词事实；narrator 仅存在台词轨。
同时返回 video_units 数组：按实际 scene_ref 将相邻 S 组合为 V 分镜视频，不能跨场景或跳过/重复/调换镜头。
格式 [{"shot_ids":["S1","S2"],"title":"场景段落","prompt_video":"承接动作与空间关系的整段视频描述",
"prompt_grid":"各 S 关键帧的格序和布局用途","negative":"整段共用负面约束"}]。
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
    for shot in shots:
        values = [str(shot.get(k) or '').strip() for k in FIELDS]
        if not all(values) or len(set(values)) != 3:
            raise ValueError(f"{shot.get('id')} 的三类提示词缺失或重复，拒绝以一类内容补齐另外两类")


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
    return fingerprint(value)


def media_source_hash(shots, kind, unit=None):
    """按消费阶段计算依赖：视频/宫格文字修改不会使静帧无端失效。"""
    fields = ('id', 'scene_ref', 'actor_refs', 'prop_refs', 'action', 'content', 'shot_size', 'angle', 'lighting', 'negative')
    values = [{k: s.get(k) for k in fields + (('prompt_image',) if kind == 'image' else ('dur', 'prompt_video', 'lines', 'camera_move'))} for s in shots]
    payload = {'shots': values, 'kind': kind}
    if kind == 'image': payload['shared_negative'] = (unit or {}).get('negative') or ''
    if kind == 'video':
        payload['keyframes'] = [(s.get('keyframe') or {}).get('sha256') for s in shots]
        payload['unit'] = {k: (unit or {}).get(k) for k in ('id', 'duration', 'prompt_video', 'prompt_grid', 'negative', 'generation_options')}
    return fingerprint(payload)
