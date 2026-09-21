# -*- coding: utf-8 -*-
"""ASR 语音转写 -> 带时间轴台词（本地 faster-whisper / 云端厂商 API 双引擎）。
用法:
  本地: python extract_speech.py <video> [--start S] [--end E] [--model large-v3-turbo] [--lang zh]
  云端: python extract_speech.py <video> --engine cloud --vendor glm [--model glm-asr-2512] [--lang zh]
依赖: 本地需 pip install faster-whisper（ffmpeg 供音频解码）；
      云端配置读 workbench/providers.json（按 --vendor 取配置）。
云端分两种链路:
  - OpenAI 兼容: POST {base_url}/audio/transcriptions（multipart，response_format=srt，540s 分片合并）
    适用 glm(qwen 等) ；模型如 glm-asr-2512 / paraformer-v2 / whisper-1
  - 豆包 doubao-seed-asr-2.0: Agent Plan SAUC WebSocket，直接传 PCM 音频。
    Resource-Id=volc.seedasr.sauc.duration；使用豆包 API Key，可在 extra.speech_api_key 单独覆盖。
    结果另存 <名>_ASR_说话人.json 供人物归属参考。
模型(本地): tiny/base/small/medium/large-v3/large-v3-turbo；文言/书面语建议 large-v3-turbo
            （CPU int8 可跑，首次自动下载约 1.5GB；CUDA 失败自动退 CPU int8）。
云端分片: OpenAI 兼容链单文件建议 <25MB，按 540s 切片上传后按偏移合并时间轴。
设备: 环境变量 ASR_DEVICE=cpu 可强制 CPU。
输出: <名>_ASR.srt / _ASR_台词.txt
"""
import sys, os, re, json, glob, argparse, subprocess, tempfile, uuid, time, hmac, hashlib
import urllib.request, urllib.error, urllib.parse
from datetime import datetime, timezone

CHUNK_SEC = 540  # 16k 单声道 16bit ≈ 32KB/s，540s ≈ 17MB，留足 25MB 余量


def extract_wav(video, start, end):
    """ffmpeg 抽 16k 单声道 wav，返回临时文件路径。"""
    wav = tempfile.NamedTemporaryFile(suffix=".wav", delete=False).name
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    if start > 0: cmd += ["-ss", str(start)]
    if end > 0: cmd += ["-to", str(end)]
    cmd += ["-i", video, "-ar", "16000", "-ac", "1", wav]
    subprocess.run(cmd, check=True)
    return wav


def probe_duration(video):
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "csv=p=0", video], capture_output=True, text=True).stdout.strip()
        return float(out)
    except Exception:
        return 0.0


def ts(x):
    h = int(x // 3600); m = int((x % 3600) // 60); ss = x % 60
    return "%02d:%02d:%02d,%03d" % (h, m, int(ss), int((ss - int(ss)) * 1000))


def parse_srt(txt):
    """srt 文本 -> [(t0,t1,text)]。"""
    items = []
    for block in re.split(r"\n\s*\n", txt.replace("\r\n", "\n").replace("\r", "\n")):
        lines = [l.strip() for l in block.strip().split("\n") if l.strip()]
        if len(lines) < 2: continue
        m = re.search(r"(\d+:\d+:\d+[,.]\d+)\s*-->\s*(\d+:\d+:\d+[,.]\d+)", "\n".join(lines))
        if not m: continue
        tl = next((i for i, l in enumerate(lines) if "-->" in l), 0)
        body = " ".join(l for i, l in enumerate(lines)
                        if i != tl and not (i < tl and re.fullmatch(r"\d+", l))).strip()
        h, mm, rest = m.group(1).replace(",", ".").split(":"); t0 = int(h) * 3600 + int(mm) * 60 + float(rest)
        h, mm, rest = m.group(2).replace(",", ".").split(":"); t1 = int(h) * 3600 + int(mm) * 60 + float(rest)
        if body: items.append((t0, t1, body))
    return items


# ---------- 本地引擎（faster-whisper） ----------

def local_rows(video, a):
    from faster_whisper import WhisperModel
    wav = extract_wav(video, a.start, a.end)
    base_t = a.start
    device = os.environ.get("ASR_DEVICE", "auto")
    if device == "auto":
        try:
            import ctranslate2
            device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
        except Exception:
            device = "cpu"

    def _load(dev):
        return WhisperModel(a.model, device=dev, compute_type="default" if dev == "cuda" else "int8")

    def _run(model):
        """转写并物化分段（segment 是惰性生成，错误在迭代时才抛）。
        幻觉过滤：no_speech 置信度高的段（多为音乐/静音尾部）丢弃；连续重复文本只留第一条。"""
        seg, info = model.transcribe(wav, language=a.lang, vad_filter=True, beam_size=5)
        out = []
        for s in seg:
            text = s.text.strip().replace(" ", "")
            if not text: continue
            if getattr(s, "no_speech_prob", 0) > 0.8: continue
            if out and text == out[-1][2]: continue
            out.append((base_t + s.start, base_t + s.end, text))
        return out

    try:
        model = _load(device)
        print(f"[信息] 本地引擎  设备: {device}  模型: {a.model}")
        rows = _run(model)
    except Exception as e:
        if device != "cpu":   # 老卡（如 GTX 970）/缺 cublas 时退 CPU int8
            print(f"[警告] {device} 失败，退回 CPU int8: {e}")
            model = _load("cpu")
            rows = _run(model)
        else:
            raise
    os.remove(wav)
    return rows


# ---------- 云端引擎：豆包 Agent Plan SAUC 流式 ASR ----------

def doubao_rows(video, a, cfg):
    """直接通过 Agent Plan WebSocket 识别；不使用旧 AUC/TOS 计费链路。"""
    if (cfg.get("base_url") or "").rstrip("/") != "https://ark.cn-beijing.volces.com/api/plan/v3":
        raise SystemExit("[错误] 豆包仅允许 Agent Plan 配置地址 /api/plan/v3")
    api_key = (cfg.get("extra") or {}).get("speech_api_key") or cfg.get("api_key") or ""
    if not api_key:
        raise SystemExit("[错误] 豆包 Agent Plan ASR 缺少 API Key")
    from pathlib import Path
    for parent in Path(__file__).resolve().parents:
        adapter = parent / "workbench" / "tools" / "doubao_plan_asr.py"
        if adapter.is_file():
            sys.path.insert(0, str(adapter.parent))
            break
    else:
        raise SystemExit("[错误] 找不到 workbench/tools/doubao_plan_asr.py")
    from doubao_plan_asr import transcribe_wav
    t0, t1 = a.start, a.end
    total = probe_duration(video)
    if t1 <= 0: t1 = total
    if total > 0: t1 = min(t1, total)
    wav = extract_wav(video, t0, t1)
    try:
        return transcribe_wav(wav, api_key, offset=t0)
    finally:
        os.remove(wav)


# ---------- 云端引擎（OpenAI 兼容 /audio/transcriptions） ----------

def providers_path():
    """skill 副本位于 .codex/skills/video-previs/scripts/，向上找 workbench/providers.json。"""
    here = os.path.dirname(os.path.abspath(__file__))
    for up in (4, 3, 2, 1):
        cand = os.path.normpath(os.path.join(here, *[".."] * up, "workbench", "providers.json"))
        if os.path.isfile(cand): return cand
    return None


def vendor_cfg(vid):
    p = providers_path()
    if not p: raise SystemExit("[错误] 找不到 workbench/providers.json")
    for v in (json.load(open(p, encoding="utf-8")).get("vendors") or []):
        if v.get("id") == vid: return v
    raise SystemExit(f"[错误] providers.json 中无厂商 {vid}")


def post_transcription(base, key, model, wav_path, lang):
    """multipart POST {base}/audio/transcriptions，response_format=srt。返回 srt 文本。"""
    boundary = "----wb" + uuid.uuid4().hex
    body = bytearray()
    for k, v in [("model", model), ("response_format", "srt"), ("language", lang)]:
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode("utf-8")
    body += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
             f'filename="audio.wav"\r\nContent-Type: audio/wav\r\n\r\n').encode("utf-8")
    body += open(wav_path, "rb").read()
    body += f"\r\n--{boundary}--\r\n".encode("utf-8")
    req = urllib.request.Request(
        base.rstrip("/") + "/audio/transcriptions", data=bytes(body), method="POST",
        headers={"Authorization": "Bearer " + key,
                 "Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        b = ""
        try: b = e.read().decode("utf-8", "replace")[:300]
        except Exception: pass
        raise SystemExit(f"[错误] 云端转写失败 HTTP {e.code}: {b or e.reason}")
    except urllib.error.URLError as e:
        raise SystemExit(f"[错误] 云端转写连接失败: {e.reason}")


def cloud_rows(video, a):
    """返回 (rows, sidecar|None)。doubao 走 openspeech 任务制；其余走 OpenAI 兼容端点。"""
    cfg = vendor_cfg(a.vendor)
    if a.vendor == "doubao":
        return doubao_rows(video, a, cfg)
    base = (cfg.get("base_url") or "").rstrip("/")
    key = cfg.get("api_key") or ""
    if not base or not key:
        raise SystemExit(f"[错误] 厂商 {a.vendor} 未配置 base_url/api_key（到 ⑧ 环境页配置）")
    if not cfg.get("enabled"):
        print(f"[警告] 厂商 {a.vendor} 未启用，仍按当前配置尝试调用")

    t0, t1 = a.start, a.end
    total = probe_duration(video)
    if t1 <= 0: t1 = total if total > 0 else t0 + CHUNK_SEC
    if total > 0: t1 = min(t1, total)

    rows = []
    seg_start = t0
    while seg_start < t1 - 0.05:
        seg_end = min(seg_start + CHUNK_SEC, t1) if t1 > 0 else seg_start + CHUNK_SEC
        wav = extract_wav(video, seg_start, seg_end)
        print(f"[信息] 云端转写 {a.vendor}/{a.model}  分片 {seg_start:.0f}-{seg_end:.0f}s", flush=True)
        srt = post_transcription(base, key, a.model, wav, a.lang)
        os.remove(wav)
        if not srt.strip().startswith("1") and "-->" not in srt:
            raise SystemExit(f"[错误] 响应不是 srt（该厂商可能不支持 /audio/transcriptions）: {srt[:200]}")
        for (x0, x1, text) in parse_srt(srt):
            text = text.strip().replace(" ", "")
            if not text: continue
            if rows and text == rows[-1][2]: continue   # 幻觉重复过滤
            rows.append((seg_start + x0, seg_start + x1, text))
        seg_start = seg_end
    return rows, None


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("video")
    ap.add_argument("--start", type=float, default=0.0); ap.add_argument("--end", type=float, default=0.0)
    ap.add_argument("--model", default="small"); ap.add_argument("--lang", default="zh")
    ap.add_argument("--engine", default="local", choices=["local", "cloud"])
    ap.add_argument("--vendor", default=None, help="云端厂商 id（providers.json）")
    a = ap.parse_args()

    sidecar = None
    if a.engine == "cloud":
        if not a.vendor: raise SystemExit("[错误] 云端引擎需指定 --vendor")
        rows, sidecar = cloud_rows(a.video, a)
    else:
        rows = local_rows(a.video, a)

    base = os.path.splitext(a.video)[0]
    srt = base + "_ASR.srt"; txt = base + "_ASR_台词.txt"
    with open(srt, "w", encoding="utf-8") as f:
        for i, (t0, t1, s) in enumerate(rows, 1): f.write(f"{i}\n{ts(t0)} --> {ts(t1)}\n{s}\n\n")
    with open(txt, "w", encoding="utf-8") as f:
        for t0, t1, s in rows: f.write(f"[{t0:7.1f}-{t1:7.1f}] {s}\n")
    if sidecar:
        sp = base + "_ASR_说话人.json"
        with open(sp, "w", encoding="utf-8") as f:
            json.dump(sidecar, f, ensure_ascii=False, indent=1)
        print(f"[信息] 说话人分离结果 -> {os.path.basename(sp)}（说话人 {len(sidecar.get('speakers') or [])} 个）")
    print(f"# ASR({a.engine}) {len(rows)} 行 -> {os.path.basename(srt)}")
    for t0, t1, s in rows: print(f"  {t0:6.1f}-{t1:6.1f}  {s}")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
