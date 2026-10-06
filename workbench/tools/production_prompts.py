# -*- coding: utf-8 -*-
"""分镜三类作者提示词；编译层不得反写或相互补齐。"""
import hashlib
import json
import re

FIELDS = ('prompt_image', 'prompt_video', 'prompt_grid')
LLM_FIELDS = FIELDS
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
【制作提示词契约 v4，必须随每个 S 转场镜头一起生成】
同一次 JSON 输出的每个 shots 元素必须另外包含三个独立字符串：
prompt_image：单张参考关键帧，选择可辨认的一个动作瞬间，写姿态、位置、视线、景别、光线。
只能一幅画面，不写连续阶段、运镜过程或宫格；不要字幕/对白框/文字/水印。
prompt_video：本镜完整连续动作，写开始状态→动作发展→结束状态，机位与运镜、节奏、声音和原文台词。
不得写“输出单张/冻结/三视图/拼图”；不要把后期字幕当作画面元素。
prompt_grid：一次生图的多格故事板说明，明确行列布局（默认3×3，禁止1×N长条）、逐格关键瞬间、景别与构图；按本镜动作顺序拆解，不新增剧情。所有格保持身份、服装、场景和画风一致，格内无文字。与单张关键帧、连续视频描述分开。
三个字段内容不同，长度按信息需要，不得为压字数丢失事实；人物与场景用现有 @character/@scene/@prop ID 引用，不复制资产外观。
prompt_image 与 prompt_video 按【S镜号（起点—终点s）：景别；镜头/机位；运镜；画面内容、人物、动作、声音、台词；光影】组织。静帧中的时间、运镜和声音仅作上下文，不作为画面元素；prompt_grid 直接采用布局与逐格说明。
旧 prompt 字段可省略，系统将以 prompt_image 提供兼容视图。
保留 scene_ref、dur、动作和台词事实；narrator 仅存在台词轨。
同时返回 video_units 数组：按实际 scene_ref 将相邻 S 组合为 V 分镜视频，不能跨场景或跳过/重复/调换镜头。
格式 [{"shot_ids":["S1","S2"],"title":"场景段落","prompt_video":"承接动作与空间关系的整段视频描述","prompt_grid":"统一布局，按成员S顺序安排每格，不把多个完整宫格嵌入宫格","negative":"整段共用负面约束"}]。
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
    """新生成的三类提示词必须齐全且互不相同；历史数据读取不调用此校验。"""
    for shot in shots:
        values = [shot.get(k) for k in LLM_FIELDS]
        if any(not isinstance(value, str) for value in values):
            raise ValueError(f"{shot.get('id')} 的三类提示词必须是独立字符串")
        values = [value.strip() for value in values]
        if not all(values) or len(set(values)) != len(LLM_FIELDS):
            raise ValueError(f"{shot.get('id')} 的提示词缺失或三类内容重复，拒绝以一类内容补齐另一类")


def retime_prompt(text, shot_id, start, end):
    """只替换本镜格式化时间标签，不改台词、动作或自由文本里的数字。"""
    pattern = r'【' + re.escape(str(shot_id)) + r'(?:镜)?[（(][^）)]*[）)][：:]'
    return re.sub(pattern, f'【{shot_id}镜（{start:g}—{end:g}s）：', str(text or ''))


# 运镜元数据 → 可执行运镜句（图生视频模型对标签词服从度低，须译成连续动作描述；
# 09-24 V01 实证：「平视缓推/固定」被模型无视——8-12s 要求固定却持续剧烈运动）。
CAMERA_MOTION_SENTENCES = {
    '固定': '机位全程焊死不动，背景透视与人物在画面中的位置关系完全不变，只有人物与物体自身在动',
    '推': '镜头自始至终匀速缓慢向前推进，如滑轨前移，画面边缘持续向外扩张、主体逐渐放大，无任何剪切',
    '拉': '镜头自始至终匀速缓慢向后拉远，如滑轨后移，画面边缘持续向内收缩、更多环境入画，无任何剪切',
    '摇': '机位原地水平匀速摇摄，像站在原地转头，画面内容连续横移而无视角跳跃',
    '移': '机位水平横移跟随，如轨道侧移，前景与背景以不同速度滑过画面',
    '跟': '镜头跟随主体移动，主体在画面中的大小与位置基本稳定，背景持续流动变化',
    '甩': '镜头快速水平甩动转场，运动模糊后稳定到新构图，一次完成不停顿',
    '升降': '机位垂直匀速升降，画面地平线平稳上抬或下压',
    '环绕': '机位绕主体弧形环绕移动，背景透视持续旋转变化，主体保持画面中心',
    '手持': '机位带轻微手持呼吸感晃动，幅度小而持续，无大幅位移',
    '斯坦尼康': '机位稳定器般平滑移动，跟随时几乎无抖动',
    '变焦': '镜头焦距匀速变化（画面整体等比放大或缩小），机位本身不动',
    '轨道': '机位沿直线轨道匀速移动，画面透视线性变化',
    '无人机': '机位大范围空中移动，俯仰与高度连续变化，视野开阔',
    '主观': '画面即人物第一人称视角，视线落点即画面中心，带轻微头部晃动',
}
CAMERA_MOTION_DEFAULT = CAMERA_MOTION_SENTENCES['固定']


def camera_motion_sentence(shot):
    """运镜执行句：camera_move 受控词 → 动作描述；angle 俯仰并入一句。"""
    move = str(shot.get('camera_move') or '固定').strip()
    sentence = CAMERA_MOTION_SENTENCES.get(move, CAMERA_MOTION_DEFAULT)
    angle = str(shot.get('angle') or '').strip()
    angle_txt = {'俯视': '镜头略高于主体向下取景', '仰视': '镜头略低于主体向上取景'}.get(angle, '')
    return sentence + ('；' + angle_txt if angle_txt else '')


def video_shot_text(shot, start, end):
    """提交时补齐结构化镜头事实，不回写三类作者提示词。

    N90：运镜不再是元数据标签——camera_move 译成可执行运镜句（运镜执行：行）
    放在镜正文之后；标签本身保留在分镜补充里供人工核对。"""
    text = retime_prompt(shot.get('prompt_video'), shot['id'], start, end)
    fields = [('场景', 'scene_ref'), ('景别', 'shot_size'), ('机位', 'angle'),
              ('镜头', 'lens'), ('拍摄方式', 'rig'), ('运镜', 'camera_move'),
              ('画面', 'content'), ('动作', 'action'), ('声音', 'sound'),
              ('光影', 'lighting'), ('转场', 'transition')]
    rows = []
    for label, key in fields:
        value = str(shot.get(key) or (shot.get('light') if key == 'lighting' else '') or '').strip()
        if value and value not in text:
            rows.append(f'{label}：{value}')
    from narration import is_narrator
    refs = [str(ref) for ref in (shot.get('actor_refs') or []) + (shot.get('prop_refs') or [])
            if ref and not is_narrator(str(ref).removeprefix('@character:')) and str(ref) not in text]
    if refs:
        rows.append('资产引用：' + '、'.join(dict.fromkeys(refs)))
    for line in shot.get('lines') or []:
        speech = str(line.get('line') or line.get('text') or '').strip()
        if speech and speech not in text:
            speaker = '旁白（仅画外音）' if is_narrator(line.get('speaker')) else str(line.get('speaker') or '')
            rows.append(f'{speaker}：“{speech}”')
    motion = camera_motion_sentence(shot)
    if motion.split('；')[0][:8] not in text:
        text += '\n运镜执行：' + motion + '。'
    return text + ('\n分镜补充：' + '；'.join(dict.fromkeys(rows)) if rows else '')


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
    camera_fields = ('lens', 'fov', 'pos', 'look', 'cam', 'move', 'scene', 'staging', 'table', 'sound')
    # 新机位字段只在实际存在时扩展指纹形状，避免没有这些字段的历史分镜全部被判过期。
    if any(s.get(k) not in (None, '', [], {}) for s in shots for k in camera_fields):
        fields += camera_fields
    if any(s.get('rig') for s in shots): fields += ('rig',)
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
    fields = ('id', 'scene_ref', 'actor_refs', 'prop_refs', 'action', 'content', 'shot_size', 'angle',
              'lighting', 'negative')
    camera_fields = ('lens', 'fov', 'pos', 'look', 'cam', 'move', 'scene', 'staging', 'table', 'sound')
    if any(s.get(k) not in (None, '', [], {}) for s in shots for k in camera_fields):
        fields += camera_fields
    if any(s.get('rig') for s in shots): fields += ('rig',)
    values = [{k: s.get(k) for k in fields + (('prompt_image',) if kind == 'image' else ('dur', 'prompt_video', 'lines', 'camera_move'))} for s in shots]
    payload = {'shots': values, 'kind': kind}
    if kind == 'grid':
        payload['grid_prompts'] = [(s['id'], s.get('prompt_grid')) for s in shots]
        payload['unit'] = {k: (unit or {}).get(k) for k in ('shot_ids', 'prompt_grid', 'prompt_video', 'negative')}
    # 转场会改写 S 图提示词里的"本镜如何接上下镜"，所以它变了静帧就该重出；
    # 同样只在真写了 transition 时并入，避免无端把存量产物判过期。
    trans = [[s.get('id'), s.get('transition')] for s in shots if s.get('transition')]
    if trans:
        payload['transition'] = trans
    if kind == 'image': payload['shared_negative'] = (unit or {}).get('negative') or ''
    # 图片与视频都会消费已采用表演；保存完整 packet，不能只看 status/source_hash。
    # 这样 visible_action、时间点或姿态任一变化都会精确使相应媒体过期。
    perf = [(s.get('id'), s.get('performance')) for s in shots
            if isinstance(s.get('performance'), dict) and s['performance'].get('status') == 'ready']
    if perf:
        payload['performance'] = perf
    if kind == 'video':
        payload['keyframes'] = [(s.get('keyframe') or {}).get('sha256') for s in shots]
        payload['unit'] = {k: (unit or {}).get(k) for k in ('id', 'duration', 'prompt_video', 'prompt_grid', 'negative', 'generation_options')}
    return fingerprint(payload)
