# -*- coding: utf-8 -*-
"""
拉片解构编排器（AI 短片分析工作台 阶段一/三）
用法: python analyze_film.py <视频> --out <projects/<项目>/拉片/<分析名>/> [选项]
      python analyze_film.py --fill <分析目录> [--shots S3,S5] [--only-empty] [--vendor id] [--max-ai N]
选项: [--no-ai] [--vendor 厂商id] [--max-ai N] [--thresh 13] [--min-dur 2.0] [--note 备注]
流程: 切点检测(extract_shots.py --thresh，失败兜底) -> 碎镜合并(--min-dur)
      -> 每镜抽 3 关键帧 -> (可选厂商 vision 逐镜读帧，厂商缺省自动选 providers.json
      第一个 enabled+vision+key 的厂商；再退 GLM 环境变量) -> analysis.json/md + _versions.json
证据层(E02/E03): 顶层 cut_detection 记录切点检测方式/参数/合并规模（均匀 5s 兜底标
      sampling:true，非真实镜头边界）；被碎镜合并吞并的镜带 merged_from 原始边界；
      受控词表含合法值「不确定」（证据不足时的合法出口，禁止猜测）；
      E03 增强：每镜（除首镜）加抽转场交界帧（S{n}_prev_tail/S{n}_head，--no-transition-frames
      可关），AI 依据「上一镜结尾 vs 本镜开头」证据判断切入转场，缺失才允许「不确定」。
台词并入(E08 独立音轨): 顶层 dialogue_track=台词脚本完整时间轴本体（L001… 幂等事件 id，
      不裁毁）；shot.dialogue 为引用视图（交集挂镜、一句可跨多镜，span=primary|overlap，
      显示时间裁镜内）。--merge-lines / fill 收尾 / merge_lines.py 自动同步同走此函数。
提速: 厂商 vision 按 --workers 并发（默认 4，429/超时指数退避重试）；
      逐镜 checkpoint（_partial.flag + 半成品 analysis.json），中断后重跑自动复用
      切点一致且已识别的镜，不重复烧 AI 调用费。
      --fill: 对已存在版本单独跑 AI 填充（跳过切点/抽帧），只改 AI 字段，支持单镜重识。
AI 字段含 transition（转场，受控词表）；词表见 VOCAB。
依赖: ffmpeg/ffprobe(关键帧必需)；opencv(切点)；厂商 key 或 GLM key(AI 可选)。
退出码: 0=成功 1=失败
"""
import sys, os, json, re, glob, argparse, subprocess, shutil, datetime, base64, time
from concurrent.futures import ThreadPoolExecutor, as_completed
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
VIDEO = os.path.realpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
import llm_openai   # 厂商薄客户端（providers.json vendors 结构）

# ---------- 小工具 ----------

def find_ffmpeg():
    p = shutil.which("ffmpeg")
    if p: return p
    cands = glob.glob(os.path.expandvars(
        r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_*\ffmpeg-*\bin\ffmpeg.exe"))
    for c in cands:
        if os.path.isfile(c): return c
    return None

def ffprobe_duration(video):
    ff = shutil.which("ffprobe")
    if not ff:
        for c in glob.glob(os.path.expandvars(
                r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_*\ffmpeg-*\bin\ffprobe.exe")):
            if os.path.isfile(c): ff = c; break
    if ff:
        try:
            r = subprocess.run([ff, "-v", "error", "-show_entries", "format=duration",
                                "-of", "default=nw=1:nk=1", video],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
            return float(r.stdout.strip())
        except Exception:
            pass
    try:
        import cv2
        cap = cv2.VideoCapture(video); fps = cap.get(cv2.CAP_PROP_FPS) or 24
        d = (cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0) / fps; cap.release()
        return d
    except Exception:
        return 0.0

def find_glm_client():
    for c in [os.path.join(VIDEO, ".codex", "skills", "video-previs", "scripts", "glm_client.py"),
              os.path.join(VIDEO, "previs_system", "tools", "glm_client.py")]:
        if os.path.isfile(c):
            sys.path.insert(0, os.path.dirname(c))
            try:
                import glm_client
                return glm_client
            except Exception:
                continue
    return None

def find_extract_shots():
    for c in [os.path.join(VIDEO, ".codex", "skills", "video-previs", "scripts", "extract_shots.py"),
              os.path.join(VIDEO, "previs_system", "tools", "extract_shots.py")]:
        if os.path.isfile(c): return c
    return None

# ---------- 切点检测（主: extract_shots.py；兜底: ffmpeg scene detect / 均匀 5s） ----------

def detect_shots_extract(video, thresh=13.0):
    """调 extract_shots.py，解析 stdout 的 'S1: 0.0-4.4  (4.4s)' 行。返回 [(t_in,t_out),...] 或 None。
    thresh 为切点灵敏度阈值（越大越不敏感，老片噪点/叠化误报多，默认从 9 提到 13）。"""
    sc = find_extract_shots()
    if not sc: return None
    try:
        r = subprocess.run([sys.executable, sc, video, "--thresh", str(thresh)],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=600)
        if r.returncode != 0:
            print(f"[警告] extract_shots.py 退出码 {r.returncode}: {(r.stderr or '')[:200]}"); return None
        segs = []
        for m in re.finditer(r"S(\d+):\s+([\d.]+)-([\d.]+)", r.stdout or ""):
            segs.append((float(m.group(2)), float(m.group(3))))
        return segs or None
    except Exception as e:
        print(f"[警告] extract_shots.py 调用失败: {e}"); return None

def detect_shots_ffmpeg(video, ff):
    """ffmpeg scene detect 兜底。返回 [(t_in,t_out),...] 或 None。"""
    try:
        r = subprocess.run([ff, "-i", video, "-vf", "select='gt(scene,0.35)',showinfo", "-f", "null", "-"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
        cuts = [float(m.group(1)) for m in
                re.finditer(r"pts_time:([\d.]+)", (r.stderr or "") + (r.stdout or ""))]
        dur = ffprobe_duration(video)
        if dur <= 0: return None
        cuts = sorted(c for c in cuts if 0.3 < c < dur - 0.3)
        bounds = [0.0] + cuts + [dur]
        return [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]
    except Exception as e:
        print(f"[警告] ffmpeg scene detect 失败: {e}"); return None

def detect_shots_uniform(video):
    dur = ffprobe_duration(video)
    if dur <= 0: return None
    return [(t, min(t + 5.0, dur)) for t in [i * 5.0 for i in range(int(dur // 5.0) + 1)]
            if t < dur - 0.2]

def detect_shots(video, ff, thresh=13.0):
    """三级切点检测：extract_shots.py -> ffmpeg scene detect -> 均匀 5s 兜底。
    返回 (segs, method)，method ∈ extract_shots / ffmpeg_scene / uniform5s（全失败返回 (None, None)）。
    uniform5s 只是均匀采样分段、不是真实镜头边界，调用方须在产物 cut_detection 标 sampling。"""
    segs = detect_shots_extract(video, thresh)
    if segs: print(f"切点: extract_shots.py(thresh={thresh}) 检出 {len(segs)} 镜"); return segs, "extract_shots"
    segs = detect_shots_ffmpeg(video, ff) if ff else None
    if segs: print(f"切点: ffmpeg scene detect 兜底检出 {len(segs)} 镜"); return segs, "ffmpeg_scene"
    segs = detect_shots_uniform(video)
    if segs:
        print(f"切点: 均匀 5s 兜底切分为 {len(segs)} 镜")
        print("[警告] ====================================================")
        print("[警告] 切点检测失败，本次为均匀 5s 采样分段，非真实镜头边界！")
        print("[警告] ====================================================")
        return segs, "uniform5s"
    return None, None

def merge_short_shots(segs, min_dur):
    """碎镜合并：当前镜时长 < min_dur 就并入相邻较长者（首尾只并向唯一邻居），
    合并后由调用方按新区间重抽关键帧、重编 S 号。min_dur<=0 不合并。
    返回 (合并后段列表, merged_from)：merged_from = {输出段下标: [[t0,t1],...] 原始段列表}，
    只记录"实际吞并过邻居"的输出段（未合并的段不出现，调用方无需为它们写 merged_from）；
    min_dur<=0 或不足两段时不合并，merged_from 恒为空 dict（零开销）。"""
    if not min_dur or min_dur <= 0 or len(segs) < 2:
        return list(segs), {}
    n0 = len(segs)
    segs = [list(s) for s in segs]
    src = [[[s[0], s[1]]] for s in segs]   # 与 segs 平行：每个输出段由哪些原始段合并而来
    i = 0
    while i < len(segs):
        if segs[i][1] - segs[i][0] < min_dur and len(segs) > 1:
            if i == 0:
                segs[1][0] = segs[0][0]; src[1] = src[0] + src[1]
                segs.pop(0); src.pop(0); continue
            if i == len(segs) - 1:
                segs[-2][1] = segs[-1][1]; src[-2] = src[-2] + src[-1]
                segs.pop(); src.pop(); break
            dl = segs[i-1][1] - segs[i-1][0]; dr = segs[i+1][1] - segs[i+1][0]
            if dl >= dr:
                segs[i-1][1] = segs[i][1]; src[i-1] = src[i-1] + src[i]
                segs.pop(i); src.pop(i)
            else:
                segs[i+1][0] = segs[i][0]; src[i+1] = src[i] + src[i+1]
                segs.pop(i); src.pop(i)
            i = max(0, i - 1); continue   # 回退重查被撑大的邻居
        i += 1
    if len(segs) != n0:
        print(f"合并碎镜(<{min_dur}s): {n0} -> {len(segs)} 镜")
    merged_from = {k: [[round(t0, 3), round(t1, 3)] for t0, t1 in grp]
                   for k, grp in enumerate(src) if len(grp) > 1}
    return [tuple(s) for s in segs], merged_from

def build_cut_detection(method, thresh, min_dur, merged):
    """顶层 cut_detection 对象（E02 切点溯源）：method=三级检测实际命中的方式，
    thresh/min_dur=本次参数，merged=碎镜合并吞掉的原始段数；
    uniform5s 兜底额外标 sampling=True（均匀采样分段，非真实镜头边界）。"""
    cd = {"method": method, "thresh": thresh, "min_dur": min_dur, "merged": merged}
    if method == "uniform5s":
        cd["sampling"] = True
    return cd

# ---------- 关键帧 ----------

BOUNDARY_PREV_OFFSET = 0.15   # 交界帧：上一镜结尾前 ~0.15s
BOUNDARY_HEAD_OFFSET = 0.05   # 交界帧：本镜开头后 ~0.05s

def _grab_frame(ff, video, t, path):
    """抽单帧；成功（退出码 0 且文件落盘）返回 True，失败 False（调用方警告，不阻断）。"""
    try:
        r = subprocess.run([ff, "-y", "-ss", f"{t:.3f}", "-i", video, "-frames:v", "1", "-q:v", "3", path],
                           capture_output=True, timeout=120)
        return r.returncode == 0 and os.path.isfile(path)
    except Exception:
        return False

def boundary_frame_plan(times, i, vdur=None):
    """第 i 镜（0 基）的交界帧计划：返回 [(相对路径, 抽帧时间), ...]；首镜无上一镜返回 []。
    prev_tail=上一镜结尾前 ~0.15s，head=本镜开头后 ~0.05s；时间 clamp 到 [0, vdur]
    （vdur 缺省取最后一镜结尾），head 另 clamp 不越过本镜结尾。
    合并段（merged_from）直接按分析段边界取：head=段首（=首个原始段的头）、prev_tail=段首前
    （=上一分析段尾）；段内被合并掉的切点也是真切点，但与"本分析段如何切入"无关，故不用。"""
    if i <= 0 or i >= len(times):
        return []
    t0, t1 = times[i]
    if vdur is None:
        vdur = times[-1][1]
    n = i + 1
    t_prev = min(max(times[i - 1][1] - BOUNDARY_PREV_OFFSET, 0.0), vdur)
    t_head = min(max(t0 + BOUNDARY_HEAD_OFFSET, 0.0), max(t0, t1 - 0.02), vdur)
    # 统一到 3 位小数（与 shot t_in/t_out 精度一致，避免浮点尾巴进数据/日志）
    return [(f"keyframes/S{n}_prev_tail.jpg", round(t_prev, 3)),
            (f"keyframes/S{n}_head.jpg", round(t_head, 3))]

def split_boundary_frames(kf_list):
    """按文件命名把 keyframes 分成 (交界帧, 镜内帧)：S{n}_prev_tail.jpg / S{n}_head.jpg 为交界帧，
    其余（S{n}_a/b/c.jpg 等）为镜内帧；交界帧固定排序 prev_tail 在前、head 在后。"""
    b, m = [], []
    for f0 in kf_list:
        base = os.path.basename(str(f0))
        if base.endswith("_prev_tail.jpg") or base.endswith("_head.jpg"):
            b.append(f0)
        else:
            m.append(f0)
    b.sort(key=lambda f0: 0 if str(f0).endswith("_prev_tail.jpg") else 1)
    return b, m

def extract_keyframes(video, segs, outdir, ff, transition_frames=True):
    """每镜抽 25/50/75% 三帧（S{n}_a/b/c.jpg）；transition_frames=True（默认，E03 转场交界帧）
    时每镜（除第一镜）再抽 2 张交界帧：S{n}_prev_tail.jpg（上一镜结尾前 ~0.15s）与
    S{n}_head.jpg（本镜开头 +~0.05s），供 AI 判断本镜切入转场。合并段的交界帧按分析段
    边界取（段首/段尾，见 boundary_frame_plan docstring）。抽取失败只警告跳过、不阻断。
    返回每镜相对路径列表，顺序为 [prev_tail, head, a, b, c]（缺失自动省略）。"""
    kd = os.path.join(outdir, "keyframes"); os.makedirs(kd, exist_ok=True)
    vdur = segs[-1][1] if segs else 0.0
    frames = []
    for i, (t0, t1) in enumerate(segs):
        dur = t1 - t0
        plan = boundary_frame_plan(segs, i, vdur) if transition_frames else []
        plan = plan + [(f"keyframes/S{i+1}_{tag}.jpg", t0 + dur * frac)
                       for tag, frac in [("a", 0.25), ("b", 0.5), ("c", 0.75)]]
        ok = []
        for rel, t in plan:
            if _grab_frame(ff, video, t, os.path.join(outdir, rel)):
                ok.append(rel)
            else:
                print(f"[警告] 关键帧抽取失败 {rel} (t={t:.2f})")
        frames.append(ok)
    return frames

def ensure_boundary_frames(analysis, shots, idx, ad, ff=None, video=None):
    """--fill 用：确保第 idx 镜（0 基）的交界帧就绪（E03 转场交界帧）。
    已有（命名命中且文件存在）直接用；缺 1~2 张且源视频+ffmpeg 可用时现抽并写回
    shot["keyframes"]（随 fill 落盘持久化）；首镜/无视频/无 ffmpeg/抽取失败时返回
    现有（可能为空）列表——AI 提示词按实际张数退化，transition 允许「不确定」。
    返回交界帧相对路径列表（0~2 个，顺序 prev_tail, head）。"""
    s = shots[idx]
    kfl = [f0 for f0 in (s.get("keyframes") or []) if os.path.isfile(os.path.join(ad, f0))]
    have, _ = split_boundary_frames(kfl)
    if len(have) >= 2 or idx <= 0:
        return have
    video = video or analysis.get("source")
    if not video or not os.path.isfile(video):
        print(f"[警告] {s.get('id','?')}: 交界帧缺失且源视频不可用，本次不补抽"
              f"（transition 允许「不确定」）")
        return have
    ff = ff or find_ffmpeg()
    if not ff:
        print(f"[警告] {s.get('id','?')}: 未找到 ffmpeg，交界帧不补抽（transition 允许「不确定」）")
        return have
    times = [(float(x.get("t_in", 0)), float(x.get("t_out", 0))) for x in shots]
    vdur = times[-1][1] if times else 0.0
    out = list(have)
    for rel, t in boundary_frame_plan(times, idx, vdur):
        if rel in have:
            continue
        if _grab_frame(ff, video, t, os.path.join(ad, rel)):
            out.append(rel)
            if rel not in (s.get("keyframes") or []):
                s.setdefault("keyframes", []).append(rel)
        else:
            print(f"[警告] 交界帧现抽失败 {rel} (t={t:.2f})")
    out.sort(key=lambda f0: 0 if f0.endswith("_prev_tail.jpg") else 1)
    return out

# ---------- GLM 逐镜分析 ----------

VOCAB = {
    "shot_size": ["大远景", "远景", "全景", "中景", "中近景", "近景", "特写", "大特写", "不确定"],
    "camera_move": ["固定", "推", "拉", "摇", "移", "跟", "甩", "升降", "环绕", "手持", "斯坦尼康", "变焦", "轨道", "无人机", "主观", "不确定"],
    "angle": ["平视", "俯视", "仰视", "鸟瞰", "虫视", "荷兰角", "过肩", "主观", "不确定"],
    "transition": ["硬切", "叠化", "淡入", "淡出", "闪白", "划像", "匹配剪辑", "蒙太奇", "无", "不确定"],
}
# 「不确定」是合法值（E03）：三帧证据不足时的合法出口，模型必须填它而不是猜测
VOCAB_LINES = "\n".join(f'- "{k}": 必须是 {"、".join(v)} 之一' for k, v in VOCAB.items())

def _compose_prompt(intro, transition_rule):
    """组装逐镜识别提示词：intro 描述本次附上的帧，transition_rule 是转场判据（随交界帧数量变）。"""
    return f"""你是电影拉片分析师。{intro}
请只输出一个 JSON 对象（不要输出任何其他文字），字段:
- "shot_size": 景别
- "camera_move": 运镜
- "angle": 拍摄角度
- "transition": 本镜是如何切入的（转场方式）
- "lighting": 光线描述（一句短语，可空字符串）
- "action": 画面中可见动作（一句短语，可空字符串）
- "story": 该镜剧情信息（一句短语，可空字符串）
- "dialogue": 镜头内台词数组，每项 {{"speaker":"说话角色代称(如 c/v/unknown)","text":"台词"}}；无台词则为 []
- "prompt_cn": 用这段画面重建镜头的文生视频中文提示词（一两句，含景别运镜主体动作）
判据纪律（证据优先，禁止猜测）:
- 只能依据给出的帧里可见的证据作答；证据不足时必须填 "不确定"，严禁编造或沿用最常见的值。
{transition_rule}
- "camera_move": 若镜内帧构图基本一致、看不出运动趋势，填 "不确定"，不要默认 "固定"。
- "shot_size"/"angle" 同理：画面无法支撑判断（如黑场、严重模糊）就填 "不确定"。
以下字段必须严格从给定词表选值（不要用词表外的值，不要用 null；"不确定" 是合法值，表示证据不足）:
{VOCAB_LINES}
lighting/action/story 是自由字符串（证据不足可留空字符串）。全部字段必须是字符串或数组。"""

# 转场判据三档（E03 增强：转场交界帧）——按实际附上的交界帧数量选用
_TR_RULE_FULL = """- "transition": 前 2 帧就是本镜的切入交界（上一镜结尾 vs 本镜开头），必须依据这两帧的
  交界证据作答：两帧画面完全无连续性、直接跳变是「硬切」；帧内可见叠化/淡入淡出/闪白/划像
  痕迹填对应值；两帧主体/构图明显呼应延续是「匹配剪辑」；只有交界帧缺失时才允许填 "不确定"。"""
_TR_RULE_PARTIAL = """- "transition": 只附了 1 张交界帧，证据不充分；能明确判断就填对应转场，否则填 "不确定"。"""
_TR_RULE_NONE = """- "transition": 你看不到本镜切点前后的画面，转场原则上无法判断；除非帧内明确可见
  转场过程（如画面正处于淡入/叠化中途），否则一律填 "不确定"，不要默认 "硬切"；
  若本镜是全片第一镜（无上一镜），可填 "无"。"""
_INTRO_FULL = "这是同一镜头按时间顺序的 5 帧：前 2 帧是上一镜结尾与本镜开头的交界帧，后 3 帧是本镜 25%/50%/75% 处。"
_INTRO_PARTIAL = "这是同一镜头的 4 帧：第 1 帧是交界帧（上一镜结尾或本镜开头），后 3 帧是本镜 25%/50%/75% 处。"
_INTRO_NONE = "这是同一镜头内按时间顺序的 3 帧（25%/50%/75% 处）。"

AI_PROMPT = _compose_prompt(_INTRO_NONE, _TR_RULE_NONE)   # 无交界帧版本（缺省/旧版本数据/首镜）
try:
    import prompt_modules as _PM
    AI_PROMPT = _PM.sys_for("fill", AI_PROMPT)
except Exception:
    pass

def build_ai_prompt(n_boundary=0):
    """按附带交界帧数量取提示词（E03 增强：转场交界帧）。
    n_boundary>=2：前 2 帧为「上一镜结尾 / 本镜开头」，transition 必须依据交界证据作答，
        只有交界帧缺失时才允许「不确定」；
    n_boundary==1：只有 1 张交界帧，证据不充分，transition 可填「不确定」；
    n_boundary<=0：无交界帧（全片首镜/抽取失败/--no-transition-frames/旧版本数据），
        看不到切点前后画面，transition 证据不足填「不确定」，全片第一镜可填「无」。"""
    if n_boundary >= 2:
        return _compose_prompt(_INTRO_FULL, _TR_RULE_FULL)
    if n_boundary == 1:
        return _compose_prompt(_INTRO_PARTIAL, _TR_RULE_PARTIAL)
    return AI_PROMPT   # 走模块级（可能被 skills/system/fill.md 覆盖层替换）

def ai_analyze_shot_glm(gc, frames_b64, n_boundary=0):
    """frames_b64 顺序：交界帧(prev_tail, head) 在前、镜内 25/50/75 帧在后；
    n_boundary=实际附上的交界帧张数（0~2），决定提示词的转场判据档。"""
    msgs = [{"role": "user", "content": [{"type": "text", "text": build_ai_prompt(n_boundary)}] +
             [gc.image_part(b) for b in frames_b64]}]
    txt = gc.chat(msgs, model=gc.vision_model(), timeout=180, temperature=0.3)
    return gc.extract_json(txt)

# ---------- 厂商 vision 逐镜分析（providers.json vendors 结构） ----------

def load_vendor_client(vendor_id=None):
    """按 id 或自动规则（第一个 enabled 且 models.vision 非空且 api_key 非空）取 VendorClient。
    返回 (client, 选择说明) 或 (None, 原因)。"""
    pp = os.path.join(HERE, "..", "providers.json")
    vendors = llm_openai.load_vendors(pp)
    if vendor_id:
        v = next((x for x in vendors if x.get("id") == vendor_id), None)
        if not v: return None, f"厂商 {vendor_id} 不存在"
        if not v.get("enabled"): return None, f"厂商 {vendor_id} 未启用"
        if not (v.get("models") or {}).get("vision"): return None, f"厂商 {vendor_id} 未配置 vision 模型"
        if not v.get("api_key"): return None, f"厂商 {vendor_id} 未配置 api_key"
        try: return llm_openai.VendorClient(vendor_id, pp), f"指定厂商 {vendor_id}"
        except Exception as e: return None, f"厂商 {vendor_id} 初始化失败: {e}"
    for v in vendors:
        if v.get("enabled") and (v.get("models") or {}).get("vision") and v.get("api_key"):
            try: return llm_openai.VendorClient(v["id"], pp), f"自动选中厂商 {v['id']}"
            except Exception: continue
    return None, "无 enabled+vision+key 的厂商"

def ai_analyze_shot_vendor(client, frame_paths, n_boundary=0):
    """OpenAI vision 格式：text + image_url(data URI)。返回解析后的 JSON dict。
    frame_paths 顺序：交界帧(prev_tail, head) 在前、镜内 25/50/75 帧在后；
    n_boundary=实际附上的交界帧张数（0~2），决定提示词的转场判据档。"""
    content = [{"type": "text", "text": build_ai_prompt(n_boundary)}]
    for fp in frame_paths:
        content.append(client.image_part(fp))
    txt = client.chat([{"role": "user", "content": content}],
                      kind="vision", timeout=180, temperature=0.3)
    m = txt.find("{"); n = txt.rfind("}")
    if m < 0 or n <= m: raise RuntimeError("AI 未返回 JSON: " + txt[:120])
    return json.loads(txt[m:n + 1])

_RETRYABLE = ("429", "超时", "Timeout", "timeout", "连接失败", "请求异常", "HTTP 5")
def ai_with_retry(fn, *args, retries=3, base=5):
    """AI 单镜调用包装：限流(429)/超时/网络/5xx 指数退避重试（5/10/20s），其余异常直接抛。"""
    for k in range(retries + 1):
        try:
            return fn(*args)
        except Exception as e:
            msg = str(e)
            if k < retries and any(t in msg for t in _RETRYABLE):
                wait = base * (2 ** k)
                print(f"  [重试] {msg[:70]} — {wait}s 后第 {k + 2}/{retries + 1} 次")
                time.sleep(wait)
                continue
            raise

def apply_ai_result(s, r):
    """把 AI 返回的 dict 应用到 shot（含 transition 词表校验，非法值置"无"并警告）。
    只改 AI 字段，绝不碰 t_in/t_out/duration/id/keyframes。"""
    for k in ["shot_size", "camera_move", "angle", "lighting", "action", "story", "prompt_cn"]:
        if isinstance(r.get(k), str): s[k] = r[k]
    tr = r.get("transition")
    if isinstance(tr, str) and tr.strip():
        tr = tr.strip()
        if tr in VOCAB["transition"]: s["transition"] = tr
        else:
            print(f"[警告] {s.get('id','?')}: transition「{tr}」不在受控词表，置为「无」")
            s["transition"] = "无"
    elif "transition" not in s:
        s["transition"] = "无"
    if isinstance(r.get("dialogue"), list):
        s["dialogue"] = [{"speaker": str(d.get("speaker", "unknown")),
                          "text": str(d.get("text", "")),
                          "t_in": s["t_in"], "t_out": s["t_out"]}
                         for d in r["dialogue"] if isinstance(d, dict) and d.get("text")]

# ---------- 输出生成 ----------

def render_markdown(a, base_dir=None):
    """analysis.json -> 人类可读 markdown 文本。base_dir 存在时校验关键帧是否存在。"""
    L = []
    L.append(f"# {a.get('name','')} 拉片分析\n")
    L.append(f"- 源片: {a.get('source','')}")
    L.append(f"- 创建: {a.get('created_at','')} · 引擎: {a.get('engine','none')} · 版本: v{a.get('version',3)}")
    L.append(f"- 镜头数: {len(a.get('shots', []))}\n")
    for s in a.get("shots", []):
        L.append(f"\n## {s.get('id','?')}  {s.get('t_in',0):.2f}s – {s.get('t_out',0):.2f}s（{s.get('duration',0):.2f}s）\n")
        L.append(f"- 景别/运镜/角度: {s.get('shot_size','')} / {s.get('camera_move','')} / {s.get('angle','')}")
        if s.get("transition"): L.append(f"- 转场: {s['transition']}")
        L.append(f"- 光线: {s.get('lighting','')}")
        L.append(f"- 剧情: {s.get('story','')}")
        L.append(f"- 动作: {s.get('action','')}")
        if s.get("dialogue"):
            for d in s["dialogue"]:
                L.append(f"- 台词 [{d.get('speaker','?')}] {d.get('t_in',0):.2f}-{d.get('t_out',0):.2f}s: {d.get('text','')}")
        else:
            L.append("- 台词: （无）")
        L.append(f"- 提示词: {s.get('prompt_cn','')}")
        kf = []
        for f0 in s.get("keyframes", []):
            ok = ""
            if base_dir:
                ok = "✓" if os.path.isfile(os.path.join(base_dir, f0)) else "✗"
            kf.append(f"{f0}{ok}")
        L.append(f"- 关键帧: {', '.join(kf) if kf else '（无）'}")
    return "\n".join(L) + "\n"

def write_markdown(a, outdir):
    open(os.path.join(outdir, "analysis.md"), "w", encoding="utf-8").write(render_markdown(a, outdir))

def register_version(lapian_dir, name, entry):
    """向 拉片/_versions.json 追加版本记录；同名自动加 _vN 后缀。返回最终 name。"""
    vf = os.path.join(lapian_dir, "_versions.json")
    vers = []
    if os.path.isfile(vf):
        try: vers = json.load(open(vf, encoding="utf-8"))
        except Exception: vers = []
    names = {v.get("name") for v in vers}
    final = name; n = 2
    while final in names:
        final = f"{name}_v{n}"; n += 1
    entry["name"] = final
    vers.append(entry)
    json.dump(vers, open(vf, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return final

# ---------- 台词并入（台词页合并结果 -> 独立音轨 dialogue_track + 各镜 dialogue 引用视图） ----------

def merge_script_lines(analysis, project_dir):
    """把 台词/台词脚本.json（ASR 为主合并 + AI 归属 + 人工校对）并入 analysis（E08 独立音轨）。
    顶层 dialogue_track = 台词脚本完整时间轴本体（绝不裁毁），每条加稳定事件 id
    （L001…按 t_in 排序编号；重复并入幂等——同 (t_in,t_out,text) 保留原 id，新增/改动的
    取新 id）。shot 的 dialogue[] 是引用视图：台词与镜头区间有交集即挂入（不再只归中点镜，
    一句可跨多镜），每项 {event, speaker, text, t_in, t_out, span}——span=primary 中点所在镜
    （主场展示；中点落缝隙时取重叠最大者），overlap 跨界延伸镜；镜内 t_in/t_out 裁到本镜
    区间仅作显示用，完整时间以 dialogue_track 本体为准。
    台词脚本存在时完全取代 AI 看帧猜的 dialogue。返回并入条数（=本体条数）。"""
    sp = os.path.join(project_dir, "台词", "台词脚本.json")
    if not os.path.isfile(sp): return 0
    try: sc = json.load(open(sp, encoding="utf-8"))
    except Exception as e:
        print(f"[警告] 台词脚本.json 读取失败，跳过台词并入: {e}"); return 0
    slines = [l for l in (sc.get("lines") or [])
              if (l.get("t_out") or 0) > (l.get("t_in") or 0) and str(l.get("text") or "").strip()]
    if not slines: return 0

    # 幂等事件 id：沿用旧 dialogue_track 里同 (t_in,t_out,text) 的 event；新句取 L{max+1} 递增
    id_of = {}; maxn = 0
    for e in (analysis.get("dialogue_track") or []):
        if not isinstance(e, dict): continue
        key = (round(float(e.get("t_in", 0) or 0), 3), round(float(e.get("t_out", 0) or 0), 3),
               str(e.get("text") or ""))
        if isinstance(e.get("event"), str): id_of.setdefault(key, e["event"])
        m = re.match(r"L(\d+)$", str(e.get("event") or ""))
        if m: maxn = max(maxn, int(m.group(1)))
    track = []
    for l in sorted(slines, key=lambda x: float(x["t_in"])):
        key = (round(float(l["t_in"]), 3), round(float(l["t_out"]), 3), str(l.get("text") or ""))
        ev = id_of.get(key)
        if not ev:
            maxn += 1; ev = f"L{maxn:03d}"
        e = {"event": ev, "t_in": key[0], "t_out": key[1],
             "speaker": str(l.get("speaker") or "unknown"), "text": key[2]}
        if l.get("source"): e["source"] = str(l["source"])
        track.append(e)
    analysis["dialogue_track"] = track

    # 引用视图：交集挂镜（primary=中点所在镜，其余 overlap；显示时间裁到镜内）
    shots = analysis.get("shots") or []
    for s in shots: s["dialogue"] = []
    for e in track:
        mid = (e["t_in"] + e["t_out"]) / 2
        hits = [s for s in shots
                if min(e["t_out"], s["t_out"]) - max(e["t_in"], s["t_in"]) > 0.02]
        if not hits: continue   # 台词与任何镜头都无交集（缝隙孤儿句），不进视图（本体仍在）
        prim = next((s for s in hits if s["t_in"] <= mid < s["t_out"]), None)
        if prim is None:   # 中点落在镜头缝隙：主场取重叠最大者
            prim = max(hits, key=lambda s: min(e["t_out"], s["t_out"]) - max(e["t_in"], s["t_in"]))
        for s in hits:
            s["dialogue"].append({"event": e["event"], "speaker": e["speaker"], "text": e["text"],
                                  "t_in": round(max(e["t_in"], s["t_in"]), 3),
                                  "t_out": round(min(e["t_out"], s["t_out"]), 3),
                                  "span": "primary" if s is prim else "overlap"})
    for s in shots:
        s["dialogue"].sort(key=lambda d: (d["t_in"], d.get("event") or ""))
    return len(track)

def merge_lines_mode(a):
    """--merge-lines：只把 台词/台词脚本.json 并入已有版本的 dialogue（无 AI、不重切点、秒级）。"""
    ad = os.path.abspath(a.merge_lines)
    aj = os.path.join(ad, "analysis.json")
    if not os.path.isfile(aj):
        print(f"[错误] analysis.json 不存在: {ad}"); sys.exit(1)
    analysis = json.load(open(aj, encoding="utf-8"))
    n = merge_script_lines(analysis, os.path.dirname(os.path.dirname(ad)))
    errors, warnings = self_validate(analysis)
    for w in warnings: print(f"[警告] {w}")
    if errors:
        for e in errors: print(f"[错误] {e}")
        print("[错误] 自检未通过，未写出"); sys.exit(1)
    json.dump(analysis, open(aj, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    write_markdown(analysis, ad)
    print(f"完成: 并入台词 {n} 条 -> {aj}")
    print(f"MERGED:{n}")
    print(f"OUTPUT:{aj}")

# ---------- 主流程 ----------

def self_validate(a):
    sys.path.insert(0, HERE)
    import validate_analysis
    return validate_analysis.validate(a)

def pick_engine(vendor_id):
    """AI 引擎选择：厂商（指定/自动）优先 -> GLM 环境变量兜底。
    返回 (client, gc, engine, why)。"""
    client, why = load_vendor_client(vendor_id)
    if client:
        return client, None, f"{client.id}:{client.models.get('vision','')}", f"{why}（vision 模型 {client.models.get('vision','')}）"
    gc = None; engine = "none"
    if os.environ.get("ZHIPUAI_API_KEY") or os.environ.get("GLM_API_KEY"):
        gc = find_glm_client()
        if gc:
            try:
                if gc.is_configured(): engine = os.environ.get("GLM_VISION_MODEL", "glm-4.5v")
            except Exception:
                engine = os.environ.get("GLM_VISION_MODEL", "glm-4.5v")
            return None, gc, engine, f"GLM 环境变量（{engine}）"
        why += "；glm_client.py 未找到"
    return None, None, "none", why

def fill_mode(a):
    """对已存在版本单独跑 AI 填充：跳过切点检测与关键帧抽取。
    --shots S3,S5 只重识这些镜；--only-empty 只补空字段镜；都没传=全部重识。
    只改 AI 字段，t_in/t_out/keyframes/id 绝不改。"""
    ad = os.path.abspath(a.fill)
    aj = os.path.join(ad, "analysis.json")
    if not os.path.isfile(aj):
        print(f"[错误] analysis.json 不存在: {ad}"); sys.exit(1)
    analysis = json.load(open(aj, encoding="utf-8"))
    shots = analysis.get("shots") or []
    if not shots:
        print("[错误] analysis.json 无 shots"); sys.exit(1)

    client, gc, engine, why = pick_engine(a.vendor)
    if not (client or gc):
        print(f"[错误] {why}；无法跑 AI 填充（可配 GLM key 或用 --vendor 指定厂商）"); sys.exit(1)
    print(f"[提示] {why}")
    analysis["engine"] = engine

    if a.shots:
        wanted = {x.strip() for x in str(a.shots).split(",") if x.strip()}
        targets = [s for s in shots if s.get("id") in wanted]
        miss = wanted - {s.get("id") for s in targets}
        if miss: print(f"[警告] 这些镜号不存在，已跳过: {sorted(miss)}")
    elif a.only_empty:
        targets = [s for s in shots if not s.get("shot_size") and not s.get("story")]
        print(f"[提示] --only-empty: 命中 {len(targets)} 镜空字段")
    else:
        targets = list(shots)
    if a.max_ai: targets = targets[:a.max_ai]
    if not targets:
        print("[提示] 没有需要识别的镜头"); return

    okc = failc = 0
    aj = os.path.join(ad, "analysis.json")
    def _dump():
        try: json.dump(analysis, open(aj, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        except Exception: pass
    def _one(s):
        kfl = [f0 for f0 in (s.get("keyframes") or []) if os.path.isfile(os.path.join(ad, f0))]
        if not kfl: raise FileNotFoundError("无关键帧文件")
        b, m = split_boundary_frames(kfl)
        if a.transition_frames:
            # 交界帧缺失时现抽（fill 有 analysis["source"] 视频可用）；抽不到则退化，
            # n_boundary=0 的提示词允许 transition 填「不确定」
            idx = next((k for k, x in enumerate(shots) if x is s), 0)
            b = ensure_boundary_frames(analysis, shots, idx, ad)
        fl = b + m   # 交界帧在前、镜内帧在后，与提示词描述一致
        if client:
            return s, ai_with_retry(ai_analyze_shot_vendor, client,
                                    [os.path.join(ad, f0) for f0 in fl], len(b))
        return s, ai_with_retry(ai_analyze_shot_glm, gc,
                                [open(os.path.join(ad, f0), "rb").read() for f0 in fl], len(b))
    w = max(1, min(a.workers, 16))
    t0 = time.time()
    if client and w > 1 and len(targets) > 1:
        print(f"[提示] 厂商 vision 并发 {w} 路（--workers 可调）")
        with ThreadPoolExecutor(max_workers=w) as ex:
            for fut in as_completed({ex.submit(_one, s): s for s in targets}):
                s = None
                try:
                    s, r = fut.result()
                    apply_ai_result(s, r); okc += 1
                    print(f"  {s['id']} 重识别完成 ({okc}/{len(targets)})")
                except Exception as e:
                    failc += 1
                    print(f"[警告] {(s or {}).get('id', '?')} AI 识别失败（该镜保留原值）: {e}")
                _dump()   # 逐镜落盘：中断不丢已识别结果
    else:
        for s in targets:
            try:
                _, r = _one(s)
                apply_ai_result(s, r); okc += 1
                print(f"  {s['id']} 重识别完成 ({okc}/{len(targets)})")
            except Exception as e:
                failc += 1
                print(f"[警告] {s.get('id')} AI 识别失败（该镜保留原值）: {e}")
            _dump()
    print(f"AI 阶段完成: 识别 {okc} / 失败 {failc}，耗时 {time.time() - t0:.0f}s")

    analysis["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    _nm = merge_script_lines(analysis, os.path.dirname(os.path.dirname(ad)))
    if _nm: print(f"[提示] 已并入台词脚本 {_nm} 条（台词以台词页合并结果为准，AI 看帧台词被取代）")
    errors, warnings = self_validate(analysis)
    for w in warnings: print(f"[警告] {w}")
    if errors:
        for e in errors: print(f"[错误] {e}")
        print("[错误] 自检未通过，未写出"); sys.exit(1)
    json.dump(analysis, open(aj, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    write_markdown(analysis, ad)
    print(f"完成: 识别 {okc} 镜 / 失败 {failc} 镜 -> {aj}")
    print(f"OUTPUT:{aj}")
    print(f"STATS:识别{okc}镜,失败{failc}镜")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video", nargs="?", help="源视频（--fill 模式不需要）")
    ap.add_argument("--out", help="输出目录: projects/<项目>/拉片/<分析名>/（--fill 不需要）")
    ap.add_argument("--no-ai", action="store_true", help="跳过 AI 分析，字段留空")
    ap.add_argument("--vendor", default=None, help="指定厂商 id（缺省自动选 providers.json 里可用厂商）")
    ap.add_argument("--max-ai", type=int, default=0, help="只对前 N 镜调 AI（防大片烧钱），0=不限")
    ap.add_argument("--workers", type=int, default=4, help="厂商 vision 并发数（默认 4；429 限流时调小，1=串行）")
    ap.add_argument("--thresh", type=float, default=13.0, help="切点灵敏度阈值（透传 extract_shots.py，默认 13）")
    ap.add_argument("--min-dur", type=float, default=2.0, help="碎镜合并阈值(秒)，短于此并入相邻较长镜，0=不合并")
    ap.add_argument("--note", default="", help="版本备注")
    ap.add_argument("--fill", metavar="分析目录", help="AI 填充模式：对已存在版本单独跑 AI（跳过切点/抽帧）")
    ap.add_argument("--merge-lines", metavar="分析目录", help="只把 台词脚本.json 并入已有版本 dialogue（无 AI，秒级）")
    ap.add_argument("--shots", default=None, help="--fill 用：只重识这些镜号，逗号分隔（如 S3,S5）")
    ap.add_argument("--only-empty", action="store_true", help="--fill 用：只补 shot_size/story 均空的镜")
    ap.add_argument("--transition-frames", dest="transition_frames", action="store_true",
                    default=True, help="抽转场交界帧供 AI 判断切入转场（默认开启）")
    ap.add_argument("--no-transition-frames", dest="transition_frames", action="store_false",
                    help="不抽交界帧（省抽帧耗时；transition 证据不足只能填「不确定」）")
    a = ap.parse_args()
    if a.merge_lines:
        merge_lines_mode(a); return
    if a.fill:
        fill_mode(a); return
    if not a.video or not a.out:
        ap.error("初次生成须给 <视频> 与 --out；对已存在版本跑 AI 用 --fill <分析目录>")
    video = os.path.abspath(a.video)
    if not os.path.isfile(video):
        print(f"[错误] 视频不存在: {video}"); sys.exit(1)
    ff = find_ffmpeg()
    if not ff:
        print("[错误] 未找到 ffmpeg（关键帧抽取必需）"); sys.exit(1)

    # AI 引擎选择：厂商（指定/自动）优先 -> GLM 环境变量兜底 -> no-ai
    client = None; gc = None; engine = "none"
    if not a.no_ai:
        client, gc, engine, why = pick_engine(a.vendor)
        print(f"[提示] {why}")
        if not client and not gc:
            print("[提示] 无可用厂商且未配 GLM key，自动走 --no-ai")

    det = detect_shots(video, ff, a.thresh)
    if not det or not det[0]:
        print("[错误] 切点检测全部失败"); sys.exit(1)
    segs, cut_method = det
    n_raw = len(segs)
    segs, merged_from = merge_short_shots(segs, a.min_dur)
    # 切点溯源（E02）：记录检测方式/参数/合并规模；均匀 5s 兜底标 sampling（非真实镜头边界）
    cut_detection = build_cut_detection(cut_method, a.thresh, a.min_dur, n_raw - len(segs))

    outdir = os.path.abspath(a.out)
    # 同名预检：_versions.json 已登记该名、或目标目录已有"完成版" analysis.json（无 _partial.flag）时，
    # 本次产物先改用 _vN 名，旧版本原样保留；有 _partial.flag 的是上次中断的半成品，同名续跑。
    lapian_dir = os.path.dirname(outdir)
    def _occupied(base):
        vf=os.path.join(lapian_dir,"_versions.json")
        if os.path.isfile(vf):
            try:
                if base in {v.get("name") for v in json.load(open(vf,encoding="utf-8"))}: return True
            except Exception: pass
        return os.path.isfile(os.path.join(lapian_dir,base,"analysis.json")) and \
               not os.path.isfile(os.path.join(lapian_dir,base,"_partial.flag"))
    def _unique_name(base):
        if not _occupied(base): return base
        n=2
        while True:
            cand=f"{base}_v{n}"
            if not _occupied(cand): return cand
            n+=1
    _final=_unique_name(os.path.basename(outdir))
    if _final!=os.path.basename(outdir):
        print(f"[提示] 版本名「{os.path.basename(outdir)}」已存在，本次产物写入「{_final}」（旧版本保留）")
        outdir=os.path.join(lapian_dir,_final)
    os.makedirs(outdir, exist_ok=True)
    kf = extract_keyframes(video, segs, outdir, ff, a.transition_frames)

    # 断点续跑：目录里有 _partial.flag 时，切点一致（同视频/参数）且已有内容的镜直接复用，不再烧钱
    FLAG=os.path.join(outdir,"_partial.flag")
    reuse={}
    if os.path.isfile(FLAG):
        try:
            pa=json.load(open(os.path.join(outdir,"analysis.json"),encoding="utf-8"))
            pm={x.get("id"):x for x in pa.get("shots") or []}
            for i,(t0,t1) in enumerate(segs):
                x=pm.get(f"S{i+1}")
                if x and abs(x.get("t_in",-1)-round(t0,3))<0.05 and abs(x.get("t_out",-1)-round(t1,3))<0.05 \
                   and (x.get("shot_size") or x.get("story")):
                    reuse[i]=x
        except Exception:
            reuse={}
        if reuse: print(f"[提示] 检测到上次中断的部分结果，复用 {len(reuse)}/{len(segs)} 镜（切点一致）")

    shots = []
    for i, (t0, t1) in enumerate(segs):
        if i in reuse:
            shots.append(dict(reuse[i])); continue
        s = {"id": f"S{i+1}", "t_in": round(t0, 3), "t_out": round(t1, 3),
             "duration": round(t1 - t0, 3),
             "shot_size": "", "camera_move": "", "angle": "", "lighting": "",
             "action": "", "story": "", "dialogue": [], "prompt_cn": "",
             "transition": "无",
             "keyframes": kf[i]}
        if i in merged_from:
            # 实际吞并过碎镜的镜保留全部原始切点边界（E02 切点溯源，消费方可选使用）
            s["merged_from"] = merged_from[i]
        shots.append(s)
    # 断点复用的镜可能来自旧版半成品（无 merged_from 字段），按本次合并映射补齐
    for i, mf in merged_from.items():
        shots[i].setdefault("merged_from", mf)

    def _checkpoint():
        """逐镜落盘半成品（含 _partial.flag）：中断后重跑按镜复用，AI 调用费不白烧。"""
        try:
            json.dump({"name": os.path.basename(outdir), "version": 3, "source": video,
                       "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
                       "engine": engine, "cut_detection": cut_detection, "shots": shots},
                      open(os.path.join(outdir, "analysis.json"), "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
            open(FLAG, "w", encoding="utf-8").write("partial")
        except Exception:
            pass

    # AI 逐镜分析：厂商路径按 --workers 并发（GLM 兜底路径保持串行），逐镜 checkpoint
    ai_done = len(reuse)
    todo = []  # 按 max_ai 配额截取待识别镜
    quota = a.max_ai if a.max_ai else len(shots)
    for i, s in enumerate(shots):
        if i in reuse or not ((client or gc) and kf[i]): continue
        if quota <= 0: break
        todo.append((i, s)); quota -= 1
    t_ai0 = time.time()
    def _run_one(item):
        i, s = item
        b, m = split_boundary_frames(kf[i])   # 交界帧在前、镜内帧在后，与提示词描述一致
        fl = b + m
        if client:
            return i, s, ai_with_retry(ai_analyze_shot_vendor, client,
                                       [os.path.join(outdir, f0) for f0 in fl], len(b))
        return i, s, ai_with_retry(ai_analyze_shot_glm, gc,
                                   [open(os.path.join(outdir, f0), "rb").read() for f0 in fl], len(b))
    if todo:
        w = max(1, min(a.workers, 16))
        if client and w > 1:
            print(f"[提示] 厂商 vision 并发 {w} 路（--workers 可调）")
            with ThreadPoolExecutor(max_workers=w) as ex:
                futs = {ex.submit(_run_one, it): it for it in todo}
                for fut in as_completed(futs):
                    it = futs[fut]; s = it[1]
                    try:
                        _, _, r = fut.result()
                        apply_ai_result(s, r); ai_done += 1
                        print(f"  {s['id']} AI 完成 ({ai_done}/{len(todo) + len(reuse)})")
                    except Exception as e:
                        print(f"[警告] {s['id']} AI 分析失败（该镜留空）: {e}")
                    _checkpoint()
        else:
            for it in todo:
                i, s = it
                try:
                    _, _, r = _run_one(it)
                    apply_ai_result(s, r); ai_done += 1
                    print(f"  {s['id']} AI 完成 ({ai_done}/{len(todo) + len(reuse)})")
                except Exception as e:
                    print(f"[警告] {s['id']} AI 分析失败（该镜留空）: {e}")
                _checkpoint()
        print(f"AI 阶段完成: {ai_done - len(reuse)} 镜新识别 / {len(reuse)} 镜复用，"
              f"耗时 {time.time() - t_ai0:.0f}s")

    analysis = {"name": os.path.basename(outdir), "version": 3, "source": video,
                "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
                "engine": engine, "cut_detection": cut_detection, "shots": shots}
    _nm = merge_script_lines(analysis, os.path.dirname(lapian_dir))
    if _nm: print(f"[提示] 已并入台词脚本 {_nm} 条（台词以台词页合并结果为准，AI 看帧台词被取代）")
    errors, warnings = self_validate(analysis)
    for w in warnings: print(f"[警告] {w}")
    if errors:
        for e in errors: print(f"[错误] {e}")
        print("[错误] 自检未通过，未写出"); sys.exit(1)
    json.dump(analysis, open(os.path.join(outdir, "analysis.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    try: os.remove(FLAG)   # 完整落盘：清除断点标记，此后同名重跑会走 _vN 保护
    except Exception: pass
    lapian_dir = os.path.dirname(outdir)
    final = register_version(lapian_dir, analysis["name"],
                             {"name": analysis["name"], "created_at": analysis["created_at"],
                              "status": "done", "shot_count": len(shots), "source": video,
                              "note": a.note})
    if final != analysis["name"]:  # 同名版本已存在：改名并迁移
        newdir = os.path.join(lapian_dir, final)
        if not os.path.exists(newdir):
            os.rename(outdir, newdir); outdir = newdir
            analysis["name"] = final
            json.dump(analysis, open(os.path.join(outdir, "analysis.json"), "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
        print(f"[提示] 版本名冲突，已改为 {final}")
    write_markdown(analysis, outdir)
    print(f"完成: {len(shots)} 镜 -> {os.path.join(outdir, 'analysis.json')}")
    print(f"OUTPUT:{os.path.join(outdir, 'analysis.json')}")

if __name__ == "__main__":
    main()
