# -*- coding: utf-8 -*-
"""AI 台词人物归属（vision）：与 OCR/ASR 平级，作为「合并台词」的第三路输入。
用法: python attribute_speakers.py <项目目录> [--analysis 拉片版本名] [--providers p.json] [--vendor id]
流程:
  1. 读原始台词行：OCR(*_字幕.srt) + ASR(*_ASR.srt)（与 merge_lines 同款 glob/解析）
  2. 取拉片最新版本（或 --analysis 指定）的镜头区间 + 关键帧
  3. 台词行按中心时间分桶到镜头；每桶一次 vision 调用：
     关键帧(≤3张) + 台词清单 + 累积角色表 -> 逐句 speaker（角色名全片一致）
  4. 收尾一次文本 LLM 别名归一（"左侧老者"->"王朗"）
  5. 写 <项目>/台词/AI归属_台词角色.json（merge_lines 的 sidecar，合并时优先级最高）；
     若已存在 台词/台词脚本.json，同步按 IoU+文本相似把归属写回（免重跑合并）
依据: 镜头归属(谁说话给谁镜头) + 称谓语义 + 画面口型/动作，由 vision 模型一次完成；
     全景群像/画外音可能判错，台词页可手动改。
产物: 台词/AI归属_台词角色.json (+可选同步 台词/台词脚本.json)；退出码 0=成功 1=失败
"""
import sys, os, json, glob, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from merge_lines import parse_srt, find_one, iou, similar

PALETTE = ["#f87171", "#fbbf24", "#34d399", "#38bdf8", "#a78bfa", "#e879f9", "#fb923c", "#22d3ee"]

ATTR_PROMPT = """你是台词归属标注员。以下是同一镜头的关键帧（带烧录字幕），时间段 {t0:.1f}s–{t1:.1f}s。
该镜头内有以下台词（按时间排序）：
{lines}
请判断每句台词是谁说的（依据：谁说话给谁镜头/画面中说话者的口型面向/台词里的称谓语义）。
已知角色表（全片保持一致，优先复用）: {roster}
只输出一个 JSON 对象，不要输出任何其他文字:
{{"lines":[{{"i":0,"speaker":"角色名"}},...]}}
speaker 必须优先取自已知角色表；表中无人能对上时才用新名字（用最有辨识度的称呼，如诸葛亮/王朗/老者，禁止用 unknown）。"""

NORM_PROMPT = """以下是对同一部影片做台词归属时收集到的角色名列表，可能包含同一人的不同叫法：
{names}
请做别名归一：把指向同一人物的名字映射到最规范的一个（如 "左侧老者"/"老者" -> "王朗"）。
只输出一个 JSON 对象: {{"map":{{"原名1":"规范名","原名2":"规范名"}}}}；无需合并的名字不要出现在 map 里。"""

# 覆盖层必须在两份内置提示词都定义之后再套：曾把 sys_for 调用放在 NORM_PROMPT
# 定义之前，NameError 被下面的 except 吞掉，attribute_norm 覆盖层从此永久失效。
try:
    import prompt_modules as _PM
    ATTR_PROMPT = _PM.sys_for("attribute", ATTR_PROMPT)
    NORM_PROMPT = _PM.sys_for("attribute_norm", NORM_PROMPT)
except Exception:
    pass


def ts(s):
    return f"{int(s//60)}:{s%60:04.1f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project", help="项目目录")
    ap.add_argument("--analysis", default=None, help="拉片版本名（默认取最新修改的）")
    ap.add_argument("--providers", default=os.path.join(HERE, "..", "providers.json"))
    ap.add_argument("--vendor", "--provider", dest="vendor", default=None)
    a = ap.parse_args()
    root = os.path.abspath(a.project)
    if not os.path.isdir(root):
        print(f"[错误] 项目目录不存在: {root}", flush=True)
        sys.exit(1)

    # ---- 原始台词行：OCR + ASR ----
    raw = []
    ocr_p = find_one(root, ["*_字幕.srt"])
    asr_p = find_one(root, ["*_ASR_修正.srt", "*_ASR.srt"])
    if ocr_p:
        raw += [{**it, "source": "ocr"} for it in parse_srt(ocr_p)]
    if asr_p:
        raw += [{**it, "source": "asr"} for it in parse_srt(asr_p)]
    if not raw:
        print("[错误] 未找到 *_字幕.srt / *_ASR.srt，请先在台词页跑 ① OCR ② ASR 提取", flush=True)
        sys.exit(1)
    raw.sort(key=lambda x: x["t_in"])
    print(f"[信息] 原始台词行: OCR {len(parse_srt(ocr_p)) if ocr_p else 0} + ASR {len(parse_srt(asr_p)) if asr_p else 0} = {len(raw)} 行", flush=True)

    # ---- 拉片版本（镜头区间 + 关键帧）----
    lp = os.path.join(root, "拉片")
    cands = [d for d in glob.glob(os.path.join(lp, "*"))
             if os.path.isfile(os.path.join(d, "analysis.json"))]
    if a.analysis:
        cands = [d for d in cands if os.path.basename(d) == a.analysis]
    if not cands:
        print("[错误] 未找到拉片解构版本（需要镜头区间与关键帧，请先在 ① 拉片解构生成）", flush=True)
        sys.exit(1)
    ad = max(cands, key=os.path.getmtime)
    ana = json.load(open(os.path.join(ad, "analysis.json"), encoding="utf-8"))
    shots = ana.get("shots") or []
    print(f"[信息] 拉片版本: {os.path.basename(ad)} · {len(shots)} 镜", flush=True)

    def shot_of(line):
        c = (line["t_in"] + line["t_out"]) / 2
        for s in shots:
            if s.get("t_in", 0) - 0.3 <= c <= s.get("t_out", 0) + 0.3:
                return s
        return None

    buckets = {}  # shot key -> (shot, [raw_idx])
    misses = 0
    for i, l in enumerate(raw):
        s = shot_of(l)
        if s is None:
            misses += 1
            continue
        buckets.setdefault(s.get("id") or id(s), (s, []))[1].append(i)
    if misses:
        print(f"[警告] {misses} 行不落在任何镜头区间内，不进归属结果", flush=True)

    # ---- vision 厂商 ----
    import llm_openai
    from explain_shots import pick_vision_vendor
    client, _ = pick_vision_vendor(a.providers, a.vendor)
    if not client:
        print("[错误] 无可用 vision 厂商（⑨ 环境页启用并配置 models.vision + key）", flush=True)
        sys.exit(1)
    print(f"[信息] 使用厂商: {client.id}", flush=True)

    # ---- 角色表预扫描：全片采样帧找人物介绍字幕（名片帧），防止模型编造角色名 ----
    cards = []  # [{name,traits,frame,t}]
    vid = ana.get("source") or ""
    if not os.path.isfile(vid):
        vs = glob.glob(os.path.join(root, "拉片素材", "*.mp4")) + glob.glob(os.path.join(root, "拉片素材", "*.mkv"))
        vid = vs[0] if vs else ""
    if vid:
        import cv2, tempfile
        cap2 = cv2.VideoCapture(vid); sfps2 = cap2.get(cv2.CAP_PROP_FPS) or 24
        dur = (cap2.get(cv2.CAP_PROP_FRAME_COUNT) or 0) / sfps2
        tmpd = tempfile.mkdtemp(prefix="roster_")
        # 变速采样：名片集中在角色首次出场（片头密采 5s/帧），后半 15s/帧；帧缩到 960 宽防请求体过大
        frames = []; t = 2.0
        while t < dur and len(frames) < 60:
            cap2.set(cv2.CAP_PROP_POS_MSEC, t * 1000); r, fr = cap2.read()
            if r:
                h0, w0 = fr.shape[:2]
                if w0 > 960: fr = cv2.resize(fr, (960, int(h0 * 960 / w0)))
                p = os.path.join(tmpd, f"f{len(frames):03d}.jpg")
                cv2.imwrite(p, fr, [cv2.IMWRITE_JPEG_QUALITY, 80])
                frames.append((t, p))
            t += 5.0 if t < 150 else 15.0
        cap2.release()
        for bi in range(0, len(frames), 6):
            batch = frames[bi:bi + 6]
            q = ("以下是影片按时间顺序的采样帧（帧序号从0开始）。找出其中带人物介绍字幕的帧"
                 "（角色姓名，常竖排，可能带“字某某”），只输出 JSON："
                 "{\"cards\":[{\"frame\":0,\"name\":\"角色名\",\"traits\":\"外貌/位置/服饰特征\"}]}；"
                 "没有介绍字幕的帧不要列出。")
            content = [{"type": "text", "text": q}] + [llm_openai.VendorClient.image_part(p) for _, p in batch]
            for attempt in (1, 2):
                try:
                    txt = client.chat([{"role": "user", "content": content}], kind="vision",
                                      max_tokens=1024, timeout=120, temperature=0.1)
                    m1, m2 = txt.find("{"), txt.rfind("}")
                    for c in (json.loads(txt[m1:m2 + 1]).get("cards") or []):
                        fi = int(c.get("frame", -1)); nm = str(c.get("name") or "").strip()
                        if 0 <= fi < len(batch) and nm and nm not in [x["name"] for x in cards]:
                            cards.append({"name": nm, "traits": str(c.get("traits") or ""),
                                          "frame": batch[fi][1], "t": batch[fi][0]})
                    break
                except Exception as e:
                    if attempt == 2:
                        print(f"[警告] 名片扫描批次 {bi//6+1} 失败: {e}", flush=True)
        print(f"[信息] 角色名片: {len(cards)} 个（{('、'.join(c['name'] for c in cards)) or '未发现'}）", flush=True)

    # ---- 逐镜归属 ----
    roster = [f"{c['name']}（{c['traits']}）" if c.get("traits") else c["name"] for c in cards]
    known = {c["name"] for c in cards}
    card_frames = [c["frame"] for c in cards[:4]]
    items = sorted(buckets.values(), key=lambda x: x[0].get("t_in", 0))
    total = len(items)
    done = 0
    failed = 0
    for s, idxs in items:
        kps = [os.path.join(ad, k) for k in (s.get("keyframes") or [])[:3]
               if os.path.isfile(os.path.join(ad, k))]
        if not kps:
            print(f"[警告] {s.get('id')} 无关键帧，{len(idxs)} 行跳过", flush=True)
            failed += len(idxs)
            done += 1
            continue
        lst = "\n".join(f"[{j}] \"{raw[i].get('text','')}\"（{ts(raw[i]['t_in'])}–{ts(raw[i]['t_out'])}）"
                        for j, i in enumerate(idxs))
        prompt = ATTR_PROMPT.format(t0=s.get("t_in", 0), t1=s.get("t_out", 0),
                                    lines=lst, roster=json.dumps(roster, ensure_ascii=False) if roster else "（空）")
        content = [{"type": "text", "text": prompt}]
        if card_frames:
            content.append({"type": "text", "text": "参考帧：人物介绍字幕帧（角色名以这些帧为准）："})
            content += [llm_openai.VendorClient.image_part(k) for k in card_frames]
        content.append({"type": "text", "text": "以下才是本镜头关键帧："})
        content += [llm_openai.VendorClient.image_part(k) for k in kps]
        try:
            txt = client.chat([{"role": "user", "content": content}], kind="vision",
                              max_tokens=2048, timeout=180, temperature=0.2)
            m1, m2 = txt.find("{"), txt.rfind("}")
            obj = json.loads(txt[m1:m2 + 1])
            for ent in obj.get("lines") or []:
                j = int(ent.get("i", -1)); sp = str(ent.get("speaker") or "").strip()
                if 0 <= j < len(idxs) and sp and sp != "unknown":
                    raw[idxs[j]]["speaker"] = sp
                    if sp not in known:
                        known.add(sp)
                        roster.append(sp)
            print(f"  {s.get('id')} 归属 {len(obj.get('lines') or [])}/{len(idxs)} 行", flush=True)
        except Exception as e:
            failed += len(idxs)
            print(f"[警告] {s.get('id')} 归属失败: {e}", flush=True)
        done += 1
        print(f"PROGRESS {done}/{total} ({done*100//total}%)", flush=True)

    # ---- 别名归一（>1 个角色名时，一次文本 LLM 调用）----
    names = sorted({l.get("speaker") for l in raw if l.get("speaker")})
    if len(names) > 1:
        try:
            txt = client.chat([{"role": "user", "content": NORM_PROMPT.format(
                names=json.dumps(names, ensure_ascii=False))}], kind="text", max_tokens=1024, timeout=60, temperature=0.1)
            m1, m2 = txt.find("{"), txt.rfind("}")
            alias = (json.loads(txt[m1:m2 + 1]).get("map") or {})
            alias = {k: v for k, v in alias.items() if k in names and v and k != v}
            if alias:
                for l in raw:
                    if l.get("speaker") in alias:
                        l["speaker"] = alias[l["speaker"]]
                print(f"[信息] 别名归一: {json.dumps(alias, ensure_ascii=False)}", flush=True)
        except Exception as e:
            print(f"[警告] 别名归一失败（保留原名）: {e}", flush=True)

    # ---- 写 sidecar：台词角色.json（merge_lines 优先级最高的归属来源）----
    final_names = sorted({l.get("speaker") for l in raw if l.get("speaker")})
    speakers = {n: {"name": n, "color": PALETTE[i % len(PALETTE)]} for i, n in enumerate(final_names)}
    sidecar = {"speakers": speakers,
               "lines": [{"t_in": l["t_in"], "t_out": l["t_out"], "text": l.get("text", ""),
                          "speaker": l.get("speaker"), "source": l.get("source")}
                         for l in raw if l.get("speaker")]}
    sc_out = os.path.join(root, "台词", "AI归属_台词角色.json")
    os.makedirs(os.path.dirname(sc_out), exist_ok=True)
    try:
        import versions as _V
        _V.snapshot(sc_out)
    except Exception:
        pass
    json.dump(sidecar, open(sc_out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[信息] sidecar -> {sc_out}（{len(sidecar['lines'])} 条，{len(final_names)} 人）", flush=True)

    # ---- 已有台词脚本.json 则同步写回（IoU + 文本相似，复用 merge_lines 规则）----
    scr_p = os.path.join(root, "台词", "台词脚本.json")
    synced = 0
    if os.path.isfile(scr_p):
        script = json.load(open(scr_p, encoding="utf-8"))
        slines = [l for l in sidecar["lines"] if l.get("speaker")]
        for l in script.get("lines") or []:
            for a_l in slines:
                if iou(l, a_l) > 0.45 and (not a_l.get("text") or similar(l.get("text", ""), a_l["text"])):
                    l["speaker"] = a_l["speaker"]
                    synced += 1
                    break
        m_spk = script.setdefault("speakers", {})
        for n, v in speakers.items():
            m_spk.setdefault(n, v)
        try:
            import shutil
            bak = os.path.join(root, "台词", "台词脚本.bak.json")
            if not os.path.isfile(bak):
                shutil.copy2(scr_p, bak)
        except Exception:
            pass
        # .bak.json 只留第一次的原始副本；每轮写回都要另存当轮被覆盖的版本
        try:
            import versions as _V
            _V.snapshot(scr_p)
        except Exception:
            pass
        json.dump(script, open(scr_p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"[信息] 已同步写回 台词脚本.json（{synced} 行）", flush=True)

    print(f"完成: 角色 {len(final_names)} 人（{('、'.join(final_names)) or '无'}）· 归属 {len(sidecar['lines'])}/{len(raw)} 行", flush=True)
    print(f"OUTPUT:{sc_out}", flush=True)
    sys.exit(0 if len(sidecar["lines"]) > 0 else 1)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
