# -*- coding: utf-8 -*-
"""一次性 fixture 构建：把 projects/01_买瓜 已有人工拉片产物转成 analysis.json 回归参照。
数据源（全部真实人工数据，不造假）:
  - 拉片/maigua_v1_镜头讲解.md   表格(#|时间|景别/机位|台词/动作|讲解) -> t_in/t_out/景别/剧情
  - 分镜/maigua_v1.json          shot/prompt_cn/line -> 景别/提示词/台词
  - 分镜/maigua_v1_台词.srt      台词时间码 -> dialogue t_in/t_out
  - 拉片素材/*.mp4                   ffmpeg 抽每镜 3 关键帧
产物: 拉片/maigua_v1_分析/{analysis.json, analysis.md, keyframes/} + 登记 _versions.json
用法: python build_fixture.py
"""
import sys, os, re, json, glob, subprocess, shutil, datetime
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
VIDEO = os.path.realpath(os.path.join(HERE, "..", ".."))
PROJ = os.path.join(VIDEO, "projects", "01_买瓜")
OUTDIR = os.path.join(PROJ, "拉片", "maigua_v1_分析")
sys.path.insert(0, HERE)
import analyze_film  # 复用 register_version / write_markdown / 校验

# 台词说话者（人工校对过: 4=顾客问价 5=摊主报价 6=顾客讥讽 7=摊主 8=顾客+摊主）
SPEAKERS = {4: ["c"], 5: ["v"], 6: ["c"], 7: ["v"], 8: ["c", "v"]}

CAMERA_MOVE = {"follow": "跟", "cu": "固定", "ots": "固定", "wide": "固定"}

def find_ffmpeg():
    p = shutil.which("ffmpeg")
    if p: return p
    for c in glob.glob(os.path.expandvars(
            r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_*\ffmpeg-*\bin\ffmpeg.exe")):
        if os.path.isfile(c): return c
    return None

def parse_md_table(path):
    rows = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line.startswith("|") or "---" in line or "景别" in line: continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 5: continue
        m = re.match(r"([\d.]+)-([\d.]+)s", cells[1])
        if not m: continue
        size_move = [p.strip() for p in cells[2].split("·")]
        rows.append({"n": int(cells[0]), "t_in": float(m.group(1)), "t_out": float(m.group(2)),
                     "shot_size": size_move[0], "cam": size_move[1] if len(size_move) > 1 else "",
                     "line_action": cells[3], "story": cells[4]})
    return rows

def parse_srt(path):
    items = []
    txt = open(path, encoding="utf-8", errors="replace").read().replace("\r\n", "\n")
    for block in re.split(r"\n\s*\n", txt):
        lines = [l.strip() for l in block.strip().split("\n") if l.strip()]
        if len(lines) < 2: continue
        m = re.search(r"(\d+:\d+:\d+)[,.](\d+)\s*-->\s*(\d+:\d+:\d+)[,.](\d+)", "\n".join(lines))
        if not m: continue
        def sec(hms, ms): return int(hms.split(":")[0]) * 3600 + int(hms.split(":")[1]) * 60 + int(hms.split(":")[2]) + int(ms) / 1000.0
        items.append({"t_in": sec(m.group(1), m.group(2)), "t_out": sec(m.group(3), m.group(4)),
                      "text": " ".join(lines[1:])})
    return items

def main():
    video = sorted(glob.glob(os.path.join(PROJ, "拉片素材", "*.mp4")))
    if not video:
        print("[错误] 找不到源片"); sys.exit(1)
    video = video[0]
    rows = parse_md_table(os.path.join(PROJ, "拉片", "maigua_v1_镜头讲解.md"))
    story = json.load(open(os.path.join(PROJ, "分镜", "maigua_v1.json"), encoding="utf-8"))
    srt = parse_srt(os.path.join(PROJ, "分镜", "maigua_v1_台词.srt"))
    assert len(rows) == len(story["shots"]) == len(srt) == 8, f"行数不符: {len(rows)}/{len(story['shots'])}/{len(srt)}"
    ff = find_ffmpeg()
    if not ff:
        print("[错误] 未找到 ffmpeg"); sys.exit(1)

    os.makedirs(OUTDIR, exist_ok=True)
    kd = os.path.join(OUTDIR, "keyframes"); os.makedirs(kd, exist_ok=True)
    shots = []
    for i, row in enumerate(rows):
        t0, t1 = row["t_in"], row["t_out"]; dur = t1 - t0
        kfs = []
        for tag, frac in [("a", 0.25), ("b", 0.5), ("c", 0.75)]:
            fn = f"S{i+1}_{tag}.jpg"
            kfs.append("keyframes/" + fn)
            subprocess.run([ff, "-y", "-ss", f"{t0 + dur * frac:.3f}", "-i", video,
                            "-frames:v", "1", "-q:v", "3", os.path.join(kd, fn)],
                           capture_output=True, timeout=120)
        sj = story["shots"][i]
        # 动作: 括号内的舞台指示；剧情: 讲解文本
        action = row["line_action"].strip("（）") if row["line_action"].startswith("（") else ""
        dialogue = []
        for spk in SPEAKERS.get(row["n"], []):
            s0 = srt[i]
            if len(SPEAKERS.get(row["n"], [])) > 1:  # 双台词镜头按区间对半分给两人
                mid = (s0["t_in"] + s0["t_out"]) / 2
                ti, to = (s0["t_in"], mid) if spk == SPEAKERS[row["n"]][0] else (mid, s0["t_out"])
            else:
                ti, to = s0["t_in"], s0["t_out"]
            text = sj["line"].split("/")[0].strip().lstrip("—").strip() if spk == "c" and "/" in sj["line"] else \
                   (sj["line"].split("/")[-1].strip() if "/" in sj["line"] else sj["line"])
            if spk == "v" and "/" in sj["line"]:
                text = sj["line"].split("/")[-1].strip()
            dialogue.append({"speaker": spk, "text": text,
                             "t_in": round(max(ti, t0), 3), "t_out": round(min(to, t1), 3)})
        shots.append({"id": f"S{i+1}", "t_in": t0, "t_out": t1, "duration": round(dur, 3),
                      "shot_size": sj["shot"], "camera_move": CAMERA_MOVE.get(sj["mode"], ""),
                      "angle": "过肩" if sj["mode"] == "ots" else "平视",
                      "lighting": "", "action": action, "story": row["story"],
                      "dialogue": dialogue, "prompt_cn": sj["prompt_cn"], "keyframes": kfs})
        print(f"  S{i+1} {t0}-{t1}s 景别={sj['shot']} 台词={len(dialogue)}")

    analysis = {"name": "maigua_v1_分析", "version": 3, "source": os.path.abspath(video),
                "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
                "engine": "none", "shots": shots}
    errors, warnings = analyze_film.self_validate(analysis)
    for w in warnings: print(f"[警告] {w}")
    if errors:
        for e in errors: print(f"[错误] {e}"); sys.exit(1)
    json.dump(analysis, open(os.path.join(OUTDIR, "analysis.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    final = analyze_film.register_version(os.path.dirname(OUTDIR), analysis["name"],
                                          {"name": analysis["name"], "created_at": analysis["created_at"],
                                           "status": "done", "shot_count": len(shots),
                                           "source": analysis["source"],
                                           "note": "人工拉片产物转换的回归参照 fixture"})
    analyze_film.write_markdown(analysis, OUTDIR)
    print(f"完成: {len(shots)} 镜 -> {OUTDIR} (版本名 {final})")

if __name__ == "__main__":
    main()
