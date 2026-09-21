# -*- coding: utf-8 -*-
"""教材/笔记 → 经验卡片 批量摄取（导演/表演/剪辑教材、文章、个人笔记）

用法: python textbook_cards.py <教材.txt|md> [--max-per-chunk 8] [--vendor 厂商id]
流程: 按 ~2500 字分块 → 每块 LLM 抽取卡片（card_extract 系统提示词，可被 Skill 中心覆盖）
      → 与既有卡片按手法名去重 → 写入用户卡片层 user_cards.json（检索加权优先）。
stdout 末行 OUTPUT:<去重后新增卡数>
"""
import sys, os, json, re, argparse
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from llm_openai import VendorClient, load_vendors, VendorError
import prompt_modules as PM
import knowledge as K
from creation_pipeline import chat_retry, parse_json, pick_vendor


def chunks(text, size=2500):
    paras = text.replace("\r\n", "\n").split("\n")
    buf, cur = [], ""
    for p in paras:
        if len(cur) + len(p) > size and cur:
            buf.append(cur); cur = ""
        cur += p + "\n"
    if cur.strip():
        buf.append(cur)
    return [b for b in buf if len(b.strip()) > 120]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("textbook")
    ap.add_argument("--max-per-chunk", type=int, default=8)
    ap.add_argument("--vendor", default=None)
    ap.add_argument("--dry", action="store_true", help="只打印抽取结果，不写库")
    a = ap.parse_args()
    if not os.path.isfile(a.textbook):
        print(f"[错误] 文件不存在: {a.textbook}"); sys.exit(1)
    text = open(a.textbook, encoding="utf-8", errors="replace").read()
    blocks = chunks(text)
    if not blocks:
        print("[错误] 有效内容过短"); sys.exit(1)
    cli = VendorClient(pick_vendor(a.vendor))
    print(f"[信息] 教材 {len(text)} 字 / {len(blocks)} 块 / 厂商 {cli.id}")
    existing = {c["skill"] for c in K.load_user()} | {c["skill"] for c in K.load()}
    added, dup = 0, 0
    for i, b in enumerate(blocks, 1):
        sys_p, user_p = PM.card_extract_prompt(b, max_n=a.max_per_chunk)
        try:
            txt = chat_retry(cli, [{"role": "system", "content": sys_p},
                                   {"role": "user", "content": user_p}],
                             max_tokens=3000, timeout=300, extra={"thinking": {"type": "disabled"}})
            data = parse_json(txt)
        except Exception as e:
            print(f"[跳过] 块{i}: {str(e)[:80]}"); continue
        for c in data.get("cards") or []:
            skill = str(c.get("skill") or "").strip()[:14]
            pres = str(c.get("prescription") or "").strip()
            if not skill or not pres:
                continue
            if skill in existing:
                dup += 1; continue
            existing.add(skill)
            print(f"  [卡] {skill} | 触发:{c.get('trigger','')} | {pres[:40]}")
            if not a.dry:
                K.save_card(skill, str(c.get("trigger") or ""), pres, str(c.get("example") or ""))
            added += 1
    print(f"[完成] 新增 {added} 张 / 去重跳过 {dup} 张 -> user_cards.json")
    print(f"OUTPUT:{added}")


if __name__ == "__main__":
    main()
