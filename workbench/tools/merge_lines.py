# -*- coding: utf-8 -*-
"""台词合并（ASR 为主，OCR 只补漏）
用法:
  python merge_lines.py <项目目录> [--ocr <srt>] [--asr <srt>] --out <台词/台词脚本.json>
  python merge_lines.py <项目目录> --from-txt <_台词.txt> --out <台词/台词脚本.json>
规则:
  - srt 缺省时在项目目录及一层子目录自动 glob: OCR 找 *_字幕.srt，ASR 找 *_ASR_修正.srt / *_ASR.srt
  - 合并策略——ASR 整表作为时间轴基底（时间轴与文本以 ASR 为准，修正版优先）:
      OCR 行仅在该字幕窗口"未被 ASR 覆盖"时才保留为补充（source="ocr"）；
      窗口被 ASR 覆盖 >=50%，或覆盖 >20% 且与覆盖时段的 ASR 拼接文本相似，即视为已识别、丢弃
      （若文本不相似——可能是 ASR 听错——把 OCR 原文挂到重叠最多那行 ASR 的 line["alt"] 供人工核对）。
      ASR 足够准时可以完全不做 OCR；只跑 ASR 一样能合并。
  - speaker 归属优先级: ① *_台词角色.json 标注（区间+文本匹配） ② _台词.txt 里 【角色名】 前缀（区间 IoU>0.5）
    两者皆无则 unknown
  - *_台词角色.json 还可提供 "speakers": {"c":{"name":"顾客","color":"#..."}} 作为真实角色名/配色
  - 输出按 t_in 排序
产物: 台词/台词脚本.json = {"speakers": {...}, "lines": [{"t_in","t_out","speaker","text","source"},...]}
退出码: 0=成功 1=失败
"""
import sys, os, re, glob, json, argparse, difflib
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# ---------- 解析 ----------

def ts2s(ts):
    h, m, rest = ts.replace(",", ".").split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)

def parse_srt(path):
    items = []
    if not path or not os.path.isfile(path): return items
    txt = open(path, encoding="utf-8", errors="replace").read().replace("\r\n", "\n").replace("\r", "\n")
    for block in re.split(r"\n\s*\n", txt):
        lines = [l.strip() for l in block.strip().split("\n") if l.strip()]
        if len(lines) < 2: continue
        m = re.search(r"(\d+:\d+:\d+[,.]\d+)\s*-->\s*(\d+:\d+:\d+[,.]\d+)", "\n".join(lines))
        if not m: continue
        tl = next((i for i, l in enumerate(lines) if "-->" in l), 0)  # 去掉序号行与时间轴行
        body = " ".join([l for i, l in enumerate(lines)
                         if i != tl and not (i < tl and re.fullmatch(r"\d+", l))]).strip()
        items.append({"t_in": round(ts2s(m.group(1)), 2), "t_out": round(ts2s(m.group(2)), 2),
                      "text": body})
    return items

def parse_txt_axis(path):
    """解析 [  0.00-  4.40] 文本 时间轴。返回 [{t_in,t_out,text}]；文本含 【角色名】 前缀时另附 speaker。"""
    items = []
    if not path or not os.path.isfile(path): return items
    for line in open(path, encoding="utf-8", errors="replace"):
        m = re.match(r"\s*\[\s*([\d.]+)-\s*([\d.]+)\]\s*(.*)", line)
        if not m: continue
        body = m.group(3).strip()
        sp = None
        sm = re.match(r"^【(.+?)】\s*(.*)$", body)
        if sm:
            sp = sm.group(1).strip(); body = sm.group(2).strip()
        items.append({"t_in": float(m.group(1)), "t_out": float(m.group(2)), "text": body, "speaker": sp})
    return items

# ---------- 工具 ----------

def iou(a, b):
    lo = max(a["t_in"], b["t_in"]); hi = min(a["t_out"], b["t_out"])
    inter = max(0.0, hi - lo)
    union = max(a["t_out"], b["t_out"]) - min(a["t_in"], b["t_in"])
    return inter / union if union > 0 else 0.0

def norm_text(t):
    return re.sub(r"[\s，。、！？!?,.·:：;；\-—~\"'「」『』()（）\[\]【】]", "", t or "")

def similar(a, b):
    na, nb = norm_text(a), norm_text(b)
    if not na or not nb: return False
    if na in nb or nb in na: return True
    return difflib.SequenceMatcher(None, na, nb).ratio() > 0.55

def find_one(root, patterns):
    for pat in patterns:
        fs = sorted(glob.glob(os.path.join(root, pat)) + glob.glob(os.path.join(root, "*", pat)))
        if fs: return fs[0]
    return None

def pick_speaker(item, axis, slines):
    """axis: parse_txt_axis 结果（带 【角色名】 前缀的条目）；slines: *_台词角色.json 的 lines 标注。
    优先级: 台词角色标注 > txt 【角色名】。区间匹配(IoU>0.5)则取角色名。"""
    for a in slines:
        if a.get("speaker") and iou(item, a) > 0.45:   # 容忍毫秒四舍五入造成的 0.5 临界
            if not a.get("text") or similar(item.get("text", ""), a["text"]):
                return a["speaker"]
    for a in axis:
        if a.get("speaker") and iou(item, a) > 0.45:
            return a["speaker"]
    return "unknown"

# ---------- 主流程 ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project", help="项目目录")
    ap.add_argument("--ocr", default=None, help="OCR srt（默认自动 glob *_字幕.srt）")
    ap.add_argument("--asr", default=None, help="ASR srt（默认自动 glob *_ASR.srt）")
    ap.add_argument("--out", required=True, help="输出 台词/台词脚本.json")
    ap.add_argument("--from-txt", default=None, help="直接从时间轴 _台词.txt 生成（无 srt 路径）")
    a = ap.parse_args()
    root = os.path.abspath(a.project)
    if not os.path.isdir(root):
        print(f"[错误] 项目目录不存在: {root}"); sys.exit(1)
    out = os.path.abspath(a.out)

    axis = parse_txt_axis(find_one(root, ["*_台词.txt"]))
    # 台词角色标注 sidecar（人工校对产物，提供真实 speaker 与角色名）
    slines = []; spk_meta = {}
    sc_p = find_one(root, ["*_台词角色.json"])
    if sc_p:
        try:
            sc = json.load(open(sc_p, encoding="utf-8"))
            slines = sc.get("lines", []) or []
            spk_meta = sc.get("speakers", {}) or {}
            print(f"角色标注: {sc_p} ({len(slines)} 条)")
        except Exception as e:
            print(f"[警告] 角色标注读取失败: {e}")

    if a.from_txt:
        items = parse_txt_axis(a.from_txt)
        if not items:
            print(f"[错误] 时间轴 txt 未解析到任何行: {a.from_txt}"); sys.exit(1)
        lines = [{"t_in": it["t_in"], "t_out": it["t_out"], "speaker": it.get("speaker") or "unknown",
                  "text": it["text"], "source": "manual"} for it in items]
    else:
        ocr_p = a.ocr or find_one(root, ["*_字幕.srt", "*_台词.srt"])
        asr_p = a.asr or find_one(root, ["*_ASR_修正.srt", "*_ASR.srt"])
        ocr = parse_srt(ocr_p); asr = parse_srt(asr_p)
        print(f"OCR: {ocr_p or '未找到'} ({len(ocr)} 条)  ASR: {asr_p or '未找到'} ({len(asr)} 条)")
        if not ocr and not asr:
            print("[错误] OCR 与 ASR 均为空，请用 --from-txt 或提供 srt"); sys.exit(1)
        # ASR 整表为基底：时间轴与文本均以 ASR 为准（修正版已在 glob 顺序里优先）
        lines = []
        for s in asr:
            s2 = dict(s); s2["speaker"] = pick_speaker(s, axis, slines); s2["source"] = "asr"
            lines.append(s2)
        # OCR 只补漏：字幕窗口被 ASR 覆盖 >=50%（或 >20% 且文本相似）即视为已识别，丢弃
        dropped = kept_ocr = 0
        for o in ocr:
            dur = max(o["t_out"] - o["t_in"], 0.01)
            inter_total = 0.0; cov_texts = []; best_j, best_inter = None, 0.0
            for j, s in enumerate(asr):
                d = min(o["t_out"], s["t_out"]) - max(o["t_in"], s["t_in"])
                if d > 0.05:
                    inter_total += d; cov_texts.append(s["text"])
                    if d > best_inter: best_inter, best_j = d, j
            cov = inter_total / dur
            cov_text = "".join(cov_texts)
            if cov >= 0.5 or (cov > 0.2 and similar(o["text"], cov_text)):
                dropped += 1
                # 文本对不上时可能是 ASR 听错：OCR 原文挂到重叠最多的 ASR 行备查，不进正文
                if best_j is not None and not similar(o["text"], cov_text):
                    lines[best_j].setdefault("alt", []).append({"source": "ocr", "text": o["text"]})
                continue
            kept_ocr += 1
            o2 = dict(o); o2["speaker"] = pick_speaker(o, axis, slines); o2["source"] = "ocr"
            lines.append(o2)
        if ocr:
            print(f"OCR 补漏: 保留 {kept_ocr} 条 / 丢弃 {dropped} 条（已被 ASR 覆盖）")

    lines.sort(key=lambda l: (l["t_in"], l["t_out"]))
    spk_names = sorted({l["speaker"] for l in lines})
    palette = ["#ff8a65", "#4fc3f7", "#aed581", "#ffd54f", "#ba68c8", "#f06292", "#4db6ac"]
    script = {"speakers": {}, "lines": lines}
    for i, s in enumerate(spk_names):
        meta = spk_meta.get(s) or {}
        script["speakers"][s] = {"name": meta.get("name", s),
                                 "color": meta.get("color", palette[i % len(palette)])}
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    json.dump(script, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    n_src = {}
    for l in lines: n_src[l["source"]] = n_src.get(l["source"], 0) + 1
    print(f"完成: {len(lines)} 条台词 {n_src} -> {out}")
    print(f"OUTPUT:{out}")
    sync_latest_analysis(root)


def sync_latest_analysis(proj):
    """合并完自动把 台词脚本.json 并进最新 done 解构版本的 dialogue（幂等：先清空再并入）。
    操作流因此单向前行：拉片解构 -> 台词(提取/归属/合并=自动同步) -> 白模，
    不再需要回拉片页手动点「同步台词」；该按钮保留用于台词微调后的重同步。"""
    vf = os.path.join(proj, "拉片", "_versions.json")
    ver = None
    if os.path.isfile(vf):
        try:
            vs = json.load(open(vf, encoding="utf-8"))
            done = [v for v in vs if v.get("status") == "done"
                    and os.path.isfile(os.path.join(proj, "拉片", v.get("name", ""), "analysis.json"))]
            if done: ver = done[-1]["name"]
        except Exception:
            pass
    if not ver:
        cands = [d for d in glob.glob(os.path.join(proj, "拉片", "*"))
                 if os.path.isfile(os.path.join(d, "analysis.json"))]
        ver = os.path.basename(max(cands, key=os.path.getmtime)) if cands else None
    if not ver:
        print("[同步] 项目下还没有解构版本，跳过自动同步（先跑拉片解构）")
        return
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from analyze_film import merge_script_lines
        ad = os.path.join(proj, "拉片", ver)
        ana = json.load(open(os.path.join(ad, "analysis.json"), encoding="utf-8"))
        n = merge_script_lines(ana, proj)
        json.dump(ana, open(os.path.join(ad, "analysis.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print(f"[同步] 台词已并入解构版本 {ver}（{n} 句）；台词微调后可在拉片页重新同步")
        print(f"SYNCED:{ver}:{n}")
    except Exception as e:
        print(f"[同步] 自动并入解构失败（可在拉片页手动同步）: {e}")

if __name__ == "__main__":
    main()
