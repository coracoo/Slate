# -*- coding: utf-8 -*-
"""
analysis.json 校验器（AI 短片分析工作台 数据契约 v3）
用法: python validate_analysis.py <analysis.json>
退出码: 0=通过(可能有警告)  1=有错误
可选新字段（证据层 E02/E03，老版本没有不警告）：顶层 cut_detection（sampling=true 醒目警告）、
镜级 merged_from（碎镜合并的原始边界，存在时做结构校验）；受控词表含合法值「不确定」。
台词独立音轨（E08）：顶层 dialogue_track 为台词完整时间本体（一句可跨多镜）；镜内 dialogue 带
span=primary|overlap 时视为本体的交集引用视图——只要求与镜头区间有交集，不再要求落镜内；
primary 项另校验 event 存在且本体中点落在本镜。无 span 的旧格式维持原落镜内规则。
"""
import sys, json, os
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

TOP_REQUIRED = ["name", "version", "source", "shots"]  # created_at/engine 由生成器负责，缺失只警告
DIALOGUE_REQUIRED = ["speaker", "text", "t_in", "t_out"]
# 受控词表（阶段三扩展）：transition 老版本可无（兼容）；值不在词表只警告不报错。
# 「不确定」是合法值（E03 证据层）：三帧证据不足时的合法出口，不警告。
VOCAB = {
    "shot_size": ["大远景", "远景", "全景", "中景", "中近景", "近景", "特写", "大特写", "不确定"],
    "camera_move": ["固定", "推", "拉", "摇", "移", "跟", "甩", "升降", "环绕", "手持", "斯坦尼康", "变焦", "轨道", "无人机", "主观", "不确定"],
    "angle": ["平视", "俯视", "仰视", "鸟瞰", "虫视", "荷兰角", "过肩", "主观", "不确定"],
    "transition": ["硬切", "叠化", "淡入", "淡出", "闪白", "划像", "匹配剪辑", "蒙太奇", "无", "不确定"],
}
# 切点检测方式（E02 切点溯源，顶层 cut_detection.method 的合法值）
CUT_METHODS = ("extract_shots", "ffmpeg_scene", "uniform5s")

# 旧版兼容值（老词汇合法存在，不警告）
VOCAB_LEGACY = {"angle": {"俯拍", "仰拍"}}

def validate(cfg, base_dir=None):
    """返回 (errors, warnings)。base_dir 用于校验 keyframes 相对路径（可选）。"""
    errors = []; warnings = []
    def err(m): errors.append(m)
    def warn(m): warnings.append(m)

    if not isinstance(cfg, dict):
        return ["顶层必须是 JSON 对象"], warnings
    for k in TOP_REQUIRED:
        if k not in cfg: err(f"缺少顶层字段: {k}")
    if errors: return errors, warnings

    if not isinstance(cfg.get("name"), str) or not cfg["name"].strip(): err("name 必须为非空字符串")
    if not isinstance(cfg.get("version"), int): err("version 应为整数")
    if not isinstance(cfg.get("source"), str) or not cfg["source"]: err("source 必须为非空字符串")
    for k in ["created_at", "engine"]:
        if k not in cfg: warn(f"缺少顶层字段(警告): {k}")

    # 切点溯源（E02）：cut_detection 为可选顶层对象，老版本没有不警告。
    # sampling=true 表示切点检测失败、走了均匀 5s 采样分段兜底——醒目警告但不阻断。
    cd = cfg.get("cut_detection")
    if cd is not None:
        if not isinstance(cd, dict):
            err("cut_detection 应为对象")
        else:
            m = cd.get("method")
            if m is not None and m not in CUT_METHODS:
                warn(f"cut_detection.method「{m}」未知（{'/'.join(CUT_METHODS)}）")
            if cd.get("sampling"):
                warn("⚠ cut_detection.sampling=true：切点检测失败，本次为均匀 5s 采样分段，"
                     "非真实镜头边界——镜头数量/时长/转场等证据不可当作真实切镜依据")

    shots = cfg.get("shots")
    if not isinstance(shots, list) or not shots:
        err("shots 必须为非空数组"); return errors, warnings

    # 台词独立音轨（E08）：dialogue_track 为可选顶层数组，是台词的完整时间本体；
    # 各镜 dialogue 是它的交集引用视图（span=primary|overlap）。老版本没有 dialogue_track 不警告。
    track_events = {}
    track = cfg.get("dialogue_track")
    if track is not None:
        if not isinstance(track, list):
            err("dialogue_track 应为数组")
        else:
            prev_t = None
            for j, e in enumerate(track):
                et = f"dialogue_track[{j}]"
                if not isinstance(e, dict):
                    err(f"{et}: 应为对象"); continue
                ev = e.get("event")
                if not isinstance(ev, str) or not ev.strip():
                    err(f"{et}: 缺少 event（非空字符串）")
                elif ev in track_events:
                    err(f"{et}: event 重复: {ev}")
                else:
                    track_events[ev] = e
                for k in ("t_in", "t_out"):
                    if not isinstance(e.get(k), (int, float)): err(f"{et}: {k} 必须为数字")
                if isinstance(e.get("t_in"), (int, float)) and isinstance(e.get("t_out"), (int, float)):
                    if not e["t_in"] < e["t_out"]: err(f"{et}: 须满足 t_in < t_out")
                    # 允许句间重叠（抢话/叠话），但 t_in 须单调不减，防乱序
                    if prev_t is not None and e["t_in"] < prev_t - 1e-6:
                        err(f"{et}: t_in({e['t_in']}) 早于上一条 t_in({prev_t})，本体须按时间排序")
                    prev_t = e["t_in"]

    ids = set()
    prev_out = None
    for i, s in enumerate(shots):
        tag = f"第{i+1}镜"
        sid = s.get("id")
        if not isinstance(sid, str) or not sid.strip():
            err(f"{tag}: 缺少 id"); sid = f"<#{i+1}>"
        else:
            if sid in ids: err(f"{tag}: id 重复: {sid}")
            ids.add(sid)
            tag = f"镜头 {sid}"
        for k in ["t_in", "t_out", "duration"]:
            if not isinstance(s.get(k), (int, float)): err(f"{tag}: {k} 必须为数字")
        if isinstance(s.get("t_in"), (int, float)) and isinstance(s.get("t_out"), (int, float)):
            if not s["t_in"] < s["t_out"]: err(f"{tag}: 须满足 t_in < t_out")
            if prev_out is not None and s["t_in"] < prev_out - 1e-6:
                err(f"{tag}: t_in({s['t_in']}) 早于上一镜 t_out({prev_out})，区间须排序且不重叠")
            prev_out = s["t_out"]
            if isinstance(s.get("duration"), (int, float)):
                if abs(s["duration"] - (s["t_out"] - s["t_in"])) > 0.05:
                    err(f"{tag}: duration({s['duration']}) 与区间时长({round(s['t_out']-s['t_in'],3)}) 不一致")
        for k in ["shot_size", "camera_move", "angle", "lighting", "action", "story", "prompt_cn"]:
            if k not in s: warn(f"{tag}: 缺少字段(警告，留空即可): {k}")
        for k, vocab in VOCAB.items():   # 受控词表检查：只警告；老版本缺 transition 不报错
            val = s.get(k)
            if val is None: continue
            if not isinstance(val, str):
                err(f"{tag}: {k} 应为字符串"); continue
            if val and val not in vocab and val not in VOCAB_LEGACY.get(k, set()):
                warn(f"{tag}: {k}「{val}」不在受控词表（{'/'.join(vocab[:5])}…），建议修正")
        dlg = s.get("dialogue", [])
        if not isinstance(dlg, list):
            err(f"{tag}: dialogue 必须为数组")
        else:
            for j, d in enumerate(dlg):
                dt = f"{tag} 台词{j+1}"
                for k in DIALOGUE_REQUIRED:
                    if k not in d: err(f"{dt}: 缺少 {k}")
                span = d.get("span")
                if span is not None and span not in ("primary", "overlap"):
                    err(f"{dt}: span 应为 primary|overlap，实际: {span!r}")
                if isinstance(d.get("t_in"), (int, float)) and isinstance(d.get("t_out"), (int, float)):
                    if not d["t_in"] < d["t_out"]: err(f"{dt}: 须满足 t_in < t_out")
                    if isinstance(s.get("t_in"), (int, float)) and isinstance(s.get("t_out"), (int, float)):
                        if span is not None:
                            # E08 引用视图：显示时间已裁到镜内但本体可能跨镜，只要求与镜头区间有交集
                            ov = min(d["t_out"], s["t_out"]) - max(d["t_in"], s["t_in"])
                            if ov <= 1e-6:
                                err(f"{dt}: 时间({d['t_in']}-{d['t_out']}) 与镜头区间({s['t_in']}-{s['t_out']}) 无交集")
                        elif d["t_in"] < s["t_in"] - 1e-6 or d["t_out"] > s["t_out"] + 1e-6:
                            err(f"{dt}: 时间({d['t_in']}-{d['t_out']}) 超出镜头区间({s['t_in']}-{s['t_out']})")
                # E08 一致性：primary 项的 event 必须存在于 dialogue_track；本体中点若落在
                # 某个镜头内，则 primary 必须是那个镜（中点落镜缝时主场取重叠最大者，合法不报错）
                if span == "primary" and track_events:
                    ev0 = d.get("event")
                    if not isinstance(ev0, str) or ev0 not in track_events:
                        err(f"{dt}: primary 引用的 event 在 dialogue_track 中不存在: {ev0!r}")
                    else:
                        ev_it = track_events[ev0]
                        if (isinstance(ev_it.get("t_in"), (int, float)) and isinstance(ev_it.get("t_out"), (int, float))
                                and isinstance(s.get("t_in"), (int, float)) and isinstance(s.get("t_out"), (int, float))):
                            mid = (ev_it["t_in"] + ev_it["t_out"]) / 2
                            if not (s["t_in"] <= mid < s["t_out"]):
                                in_other = any(isinstance(o.get("t_in"), (int, float))
                                               and isinstance(o.get("t_out"), (int, float))
                                               and o["t_in"] <= mid < o["t_out"] for o in shots)
                                if in_other:
                                    err(f"{dt}: primary 但本体中点({round(mid,3)}) 不在本镜区间({s['t_in']}-{s['t_out']}) 内")
        kf = s.get("keyframes", [])
        if not isinstance(kf, list):
            err(f"{tag}: keyframes 必须为数组")
        else:
            for f0 in kf:
                if not isinstance(f0, str) or os.path.isabs(f0) or ".." in f0.replace("\\", "/").split("/"):
                    err(f"{tag}: keyframes 元素必须是相对路径: {f0!r}")
        # 切点溯源（E02）：merged_from 为可选字段（碎镜合并的原始边界），老版本没有不警告；
        # 存在时做结构校验——应为 [[t0, t1], ...] 数字对且 t0 < t1
        mf = s.get("merged_from")
        if mf is not None:
            if not isinstance(mf, list) or not mf:
                err(f"{tag}: merged_from 应为非空数组")
            else:
                for j, seg in enumerate(mf):
                    if not (isinstance(seg, (list, tuple)) and len(seg) == 2
                            and all(isinstance(x, (int, float)) for x in seg)):
                        err(f"{tag}: merged_from[{j}] 应为 [t0, t1] 数字对")
                    elif not seg[0] < seg[1]:
                        err(f"{tag}: merged_from[{j}] 须满足 t0 < t1")
    return errors, warnings

def main():
    if len(sys.argv) < 2:
        print("用法: python validate_analysis.py <analysis.json>"); sys.exit(1)
    p = sys.argv[1]
    try:
        cfg = json.load(open(p, encoding="utf-8"))
    except Exception as e:
        print(f"[错误] 读取失败: {e}"); sys.exit(1)
    base = os.path.dirname(os.path.abspath(p))
    errors, warnings = validate(cfg, base)
    for w in warnings: print(f"[警告] {w}")
    for e in errors: print(f"[错误] {e}")
    if errors:
        print(f"校验失败: {len(errors)} 个错误"); sys.exit(1)
    print(f"校验通过: {len(cfg.get('shots', []))} 镜" + (f"（{len(warnings)} 个警告）" if warnings else ""))
    sys.exit(0)

if __name__ == "__main__":
    main()
