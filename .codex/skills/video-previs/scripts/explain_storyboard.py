# -*- coding: utf-8 -*-
"""
镜头语言讲解生成器（无AI，规则拼接）：读 storyboard JSON -> 出讲解 Markdown。
逐镜头解释景别/机位模式/时长/台词，并附节奏小结。输出默认写到项目 拉片/ 目录。
用法: python explain_storyboard.py <storyboard.json> [--out out.md]
"""
import sys,os,json,argparse
HERE=os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0,HERE)
MODE_TXT={
 "wide":"全景/定场：交代空间关系与人物站位，让观众建立场景地图。",
 "two":"双人中景：同时收纳对话双方，强调关系与彼此反应。",
 "cu":"单人特写：隔离主体、放大情绪，节奏收紧。",
 "ots":"越肩反打：过肩看说话者，建立正反打轴线，对话沉浸感最强。",
 "follow":"跟拍：摄影机跟随主体运动，带入行进感与主观视点。",
 "near":"近景背影：跟在角色身后，制造代入感与悬念。",
}
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("src"); ap.add_argument("--out"); ap.add_argument("--ai",action="store_true")
    a=ap.parse_args()
    cfg=json.load(open(a.src,encoding="utf-8"))
    shots=cfg.get("shots",[])
    L=[]
    L.append(f"# {cfg.get('title','分镜')} · 镜头语言讲解\n")
    L.append(f"> 场景 `{cfg.get('scene','')}` · {len(shots)} 个镜头 · 总时长 {sum(s.get('dur',0) for s in shots):.1f}s · {cfg.get('w',960)}x{cfg.get('h',540)}@{cfg.get('fps',24)}\n")
    L.append("| # | 时间 | 景别/机位 | 台词/动作 | 讲解 |")
    L.append("|---|---|---|---|---|")
    t=0; seen={}
    for i,s in enumerate(shots,1):
        dur=float(s.get("dur",0))
        mode=s.get("mode") or s.get("cam") or ""
        why=(s.get("prompt_cn") or s.get("note") or "").replace("\n"," ")
        line=(s.get("line") or "").strip().replace("\n"," ")
        if mode not in seen:  # 该机位首次出现：给完整叙事解释
            exp=MODE_TXT.get(mode,"")
            seen[mode]=i
        else:
            exp=f"{mode}（第 {seen[mode]} 镜已释）"
        L.append(f"| {i} | {t:.1f}-{t+dur:.1f}s | {s.get('shot','') or ''} · {mode} | {line[:40]} | {exp} {why} |")
        t+=dur
    L.append("\n## 节奏小结\n")
    if shots:
        durs=[float(s.get("dur",0)) for s in shots]
        L.append(f"- 平均镜头时长 {sum(durs)/len(durs):.1f}s，最长 {max(durs):.1f}s，最短 {min(durs):.1f}s。")
        fast=[str(i+1) for i,d in enumerate(durs) if d<=2]
        if fast: L.append(f"- 快切镜头（≤2s）：第 {'、'.join(fast)} 个，用于收紧节奏或强调即时反应。")
        slow=[str(i+1) for i,d in enumerate(durs) if d>=8]
        if slow: L.append(f"- 长镜头（≥8s）：第 {'、'.join(slow)} 个，用于定场或情绪延续。")
        seq=[s.get("mode") or s.get("cam") or "?" for s in shots]
        L.append("- 机位序列："+" → ".join(f"第{i+1}镜 {m}" for i,m in enumerate(seq))+"。")
        L.append("- 序列读法：开场用 `"+seq[0]+"`"+("定场" if seq[0] in ("wide","follow") else "进入")+"；收尾落在 `"+seq[-1]+"`，"
                 +("以关系镜头收束对话。" if seq[-1] in ("two","wide") else "以主体镜头收束，情绪落在人物身上。"))
    if getattr(a,"ai",False):
        try:
            import glm_client as G
            tbl="\n".join(f"{i+1}. [{x.get('shot','')}/{x.get('mode')}] {x.get('dur')}s 台词:{x.get('line') or '（无）'} 画面:{x.get('prompt_cn','')}"
                          for i,x in enumerate(shots))
            ai=G.chat([
              {"role":"system","content":"你是影评/导演课讲师。基于给定的分镜时间轴，写《镜头语言深度讲解》：1)逐镜 1-2 句讲清该镜的叙事功能与切换理由（结合前后镜）；2)结尾一段总体节奏与轴线评价。中文，直接输出 markdown（不要重复已知时间轴事实）。"},
              {"role":"user","content":tbl}],model=G.text_model(),timeout=180)
            L.append("\n## 深度讲解（AI）\n")
            L.append(ai.strip())
            L.append("\n> 以上由 AI 生成，请人工复核。")
        except Exception as e:
            L.append(f"\n> （AI 深度讲解失败：{e}；以上为规则模板结果。）")
    out=a.out or os.path.join(os.path.dirname(os.path.abspath(a.src)),"..","拉片",os.path.splitext(os.path.basename(a.src))[0]+"_镜头讲解.md")
    out=os.path.normpath(out)
    os.makedirs(os.path.dirname(out),exist_ok=True)
    with open(out,"w",encoding="utf-8") as f: f.write("\n".join(L))
    print("# 讲解已生成 ->",out)
    print(f"# {len(shots)} 个镜头")
if __name__=="__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
