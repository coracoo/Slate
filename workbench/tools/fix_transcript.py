# -*- coding: utf-8 -*-
"""ASR 台词 LLM 纠错（同音错字/文言书面语），时间轴与行数不变。
用法:
  python fix_transcript.py <项目目录或 *_ASR.srt> [--vendor glm] [--batch 15] [--out 路径]
规则:
  - 输入为项目目录时自动 glob 第一个 *_ASR.srt
  - 分批发给已启用厂商的文本模型，只改正文本，行数/顺序/时间戳不变
  - 默认产物 <base>_ASR_修正.srt（不覆盖原文件）；改动明细打到 stdout
  - 某一批失败：保留原句并告警，不中断整体
依赖: llm_openai.py / merge_lines.py（同目录）；providers.json 需有启用的文本厂商
退出码: 0=成功 1=失败
"""
import sys, os, re, json, glob, argparse
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from merge_lines import parse_srt
from llm_openai import VendorClient, load_vendors

PROMPT = """以下是语音识别的中文台词转写结果，按行给出。它可能含有同音/近音错字，
尤其文言、成语、人名、地名、官职名（如“天数有变，神器更易”“庙堂之上，朽木为官”这类表述）。
请结合上下文把每句订正为正确的书面台词（可参考《三国演义》等经典出处），可补全标点。
硬性要求：不得合并、拆分、增加或删除任何一行；输出与输入行数完全一致；
每行只给订正后的文本，不要解释。输出 JSON 数组，元素为 {"i": 行号, "t": "订正文本"}。"""


def _pm_sys():
    """系统提示词经 prompt_modules 注册表（Skill 中心可覆盖，id=fixasr）。"""
    try:
        import prompt_modules as _PM
        return _PM.sys_for("fixasr", PROMPT)
    except Exception:
        return PROMPT


def pick_vendor(vid):
    """指定厂商 id，否则取第一个已启用且配好文本模型+key 的厂商。"""
    if vid:
        return VendorClient(vid)
    for v in load_vendors():
        if v.get("enabled") and (v.get("models") or {}).get("text") and v.get("api_key"):
            return VendorClient.from_config(v, check_enabled=False)
    raise SystemExit("[错误] 没有可用文本厂商：请在环境页启用某厂商并配置文本模型与 API Key")


def fix_batch(client, rows):
    """订正一批（行数不变）。失败保留原文并告警。"""
    idx_text = [{"i": i, "t": r["text"]} for i, r in enumerate(rows)]
    msgs = [{"role": "system", "content": _pm_sys()},
            {"role": "user", "content": PROMPT + "\n\n输入：\n" +
             json.dumps(idx_text, ensure_ascii=False) + "\n\n只输出 JSON 数组。"}]
    for attempt in range(2):
        try:
            out = client.chat(msgs, kind="text", max_tokens=4096, timeout=180, temperature=0)
            m = re.search(r"\[.*\]", out, re.S)
            if not m: raise ValueError("响应中没有 JSON 数组")
            arr = json.loads(m.group(0))
            texts = {int(it["i"]): str(it["t"]).strip() for it in arr if isinstance(it, dict) and "i" in it and "t" in it}
            if sorted(texts) != list(range(len(rows))):
                raise ValueError(f"行号不完整: 期望 0..{len(rows)-1}")
            return [texts[i] or rows[i]["text"] for i in range(len(rows))], None
        except Exception as e:
            err = e
    return [r["text"] for r in rows], err


def fmt_ts(x):
    h = int(x // 3600); m = int((x % 3600) // 60); ss = x % 60
    return "%02d:%02d:%02d,%03d" % (h, m, int(ss), int((ss - int(ss)) * 1000))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src", help="项目目录或 *_ASR.srt")
    ap.add_argument("--vendor", default=None, help="厂商 id（默认自动选第一个可用文本厂商）")
    ap.add_argument("--batch", type=int, default=15, help="每批行数（默认 15）")
    ap.add_argument("--out", default=None, help="输出 srt（默认 <base>_ASR_修正.srt）")
    a = ap.parse_args()

    src = os.path.abspath(a.src)
    if os.path.isdir(src):
        fs = sorted(glob.glob(os.path.join(src, "*_ASR.srt")) + glob.glob(os.path.join(src, "*", "*_ASR.srt")))
        if not fs:
            print("[错误] 目录下未找到 *_ASR.srt"); sys.exit(1)
        src = fs[0]
    if not os.path.isfile(src):
        print(f"[错误] 文件不存在: {src}"); sys.exit(1)
    rows = parse_srt(src)
    if not rows:
        print("[错误] srt 未解析到任何行"); sys.exit(1)
    client = pick_vendor(a.vendor)
    print(f"输入: {src} ({len(rows)} 行)  厂商: {client.id} / {client.model('text')}", flush=True)

    fixed, n_warn = [], 0
    for b0 in range(0, len(rows), a.batch):
        batch = rows[b0:b0 + a.batch]
        texts, err = fix_batch(client, batch)
        if err:
            n_warn += 1
            print(f"[警告] 第 {b0+1}-{b0+len(batch)} 行纠错失败，保留原文: {err}", flush=True)
        fixed += texts
        print(f"  进度 {min(b0+a.batch, len(rows))}/{len(rows)}", flush=True)

    n_change = sum(1 for r, t in zip(rows, fixed) if t != r["text"])
    print(f"\n改动 {n_change}/{len(rows)} 行：", flush=True)
    for r, t in zip(rows, fixed):
        if t != r["text"]:
            print(f"  {r['t_in']:8.2f}  {r['text'][:24]}  ->  {t[:24]}", flush=True)

    out = os.path.abspath(a.out) if a.out else src.replace("_ASR.srt", "_ASR_修正.srt")
    with open(out, "w", encoding="utf-8") as f:
        for i, (r, t) in enumerate(zip(rows, fixed), 1):
            f.write(f"{i}\n{fmt_ts(r['t_in'])} --> {fmt_ts(r['t_out'])}\n{t}\n\n")
    print(f"OUTPUT:{out}")
    print(f"完成: {len(rows)} 行，改动 {n_change}，失败批 {n_warn}")


if __name__ == "__main__":
    main()
