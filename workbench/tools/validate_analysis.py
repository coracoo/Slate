# -*- coding: utf-8 -*-
"""
analysis.json 校验器（AI 短片分析工作台 数据契约 v3）
用法: python validate_analysis.py <analysis.json>
退出码: 0=通过(可能有警告)  1=有错误
"""
import sys, json, os
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

TOP_REQUIRED = ["name", "version", "source", "shots"]  # created_at/engine 由生成器负责，缺失只警告
DIALOGUE_REQUIRED = ["speaker", "text", "t_in", "t_out"]
# 受控词表（阶段三扩展）：transition 老版本可无（兼容）；值不在词表只警告不报错
VOCAB = {
    "shot_size": ["大远景", "远景", "全景", "中景", "中近景", "近景", "特写", "大特写"],
    "camera_move": ["固定", "推", "拉", "摇", "移", "跟", "甩", "升降", "环绕", "手持", "斯坦尼康", "变焦", "轨道", "无人机", "主观"],
    "angle": ["平视", "俯视", "仰视", "鸟瞰", "虫视", "荷兰角", "过肩", "主观"],
    "transition": ["硬切", "叠化", "淡入", "淡出", "闪白", "划像", "匹配剪辑", "蒙太奇", "无"],
}

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

    shots = cfg.get("shots")
    if not isinstance(shots, list) or not shots:
        err("shots 必须为非空数组"); return errors, warnings

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
                if isinstance(d.get("t_in"), (int, float)) and isinstance(d.get("t_out"), (int, float)):
                    if not d["t_in"] < d["t_out"]: err(f"{dt}: 须满足 t_in < t_out")
                    if isinstance(s.get("t_in"), (int, float)) and isinstance(s.get("t_out"), (int, float)):
                        if d["t_in"] < s["t_in"] - 1e-6 or d["t_out"] > s["t_out"] + 1e-6:
                            err(f"{dt}: 时间({d['t_in']}-{d['t_out']}) 超出镜头区间({s['t_in']}-{s['t_out']})")
        kf = s.get("keyframes", [])
        if not isinstance(kf, list):
            err(f"{tag}: keyframes 必须为数组")
        else:
            for f0 in kf:
                if not isinstance(f0, str) or os.path.isabs(f0) or ".." in f0.replace("\\", "/").split("/"):
                    err(f"{tag}: keyframes 元素必须是相对路径: {f0!r}")
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
