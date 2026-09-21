# -*- coding: utf-8 -*-
"""
台词驱动分镜建议器（coverage planner）。
输入台词轨（SRT 或 JSON lines），按对话节奏输出建议镜头表：
  多人闲谈/交代 -> wide 全景； 关键句/决定 -> 说话者 cu 特写；
  一来一回 -> ots 越肩正反打； 重要台词后 -> 听者反应 cu。
用法:
  python plan_coverage.py <台词.srt|台词.json> [--out shots.json]
JSON 输入: {"actors":{"A":"大姐",...},"lines":[{"t0":..,"t1":..,"speaker":"A","line":"...","beats":["key"]}]}
beat 标记(可选): key=关键句/决定, react=接反应, establish=交代。
"""
import sys,os,re,json,argparse
def parse_srt(path):
    raw=open(path,encoding="utf-8-sig").read()
    rows=[]; blocks=re.split(r"\n\s*\n",raw.strip())
    def tc(s):
        s=s.strip().replace(",","."); h,m,rest=s.split(":"); sec=float(rest)
        return int(h)*3600+int(m)*60+sec
    for b in blocks:
        ls=[x for x in b.splitlines() if x.strip()]
        if len(ls)<3: continue
        t=re.search(r"(\d+:\d+:\d+[\.,]\d+)\s*-->\s*(\d+:\d+:\d+[\.,]\d+)",b)
        if not t: continue
        text=re.sub(r"\s+","",b.split("-->")[1].split("\n",1)[1]).strip()
        rows.append({"t0":tc(t.group(1)),"t1":tc(t.group(2)),"speaker":None,"line":text})
    return {"actors":{},"lines":rows}
def plan(data):
    lines=data.get("lines",[])
    shots=[]; i=0; tprev=lines[0]["t0"] if lines else 0
    def push(cam,t0,t1,host=None,target=None,spk=None,line="",note=""):
        shots.append({"id":f"S{len(shots)+1}","dur":round(t1-t0,2),"cam":cam,
                      "host":host,"target":target,"speaker":spk,"line":line,"note":note})
    # 开场建立：前2句/短于6s 用 wide
    est_end=tprev
    while i<len(lines) and (lines[i]["t0"]-tprev)<6 and len([x for x in shots if x["cam"]=="wide"])==0:
        est_end=max(est_end,lines[i]["t1"]); i+=1
        if i>=2: break
    if est_end>tprev:
        push("wide",tprev,est_end,note="建立空间：多人闲谈/交代")
    while i<len(lines):
        L=lines[i]; nxt=lines[i+1] if i+1<len(lines) else None
        beats=set(L.get("beats",[]) or [])
        spk=L["speaker"]
        # 关键句/决定 -> 说话者特写
        if "key" in beats or "establish" not in beats and L["t1"]-L["t0"]>=2.5 and not (nxt and nxt.get("speaker") and nxt["speaker"]!=spk and nxt["t0"]-L["t1"]<0.8):
            push("cu",L["t0"],L["t1"],target=spk,spk=spk,line=L["line"],note="关键句：说话者特写")
            # 后接反应：若下一句换人或标 react -> 听者反应特写
            if "react" in beats or (nxt and nxt.get("speaker") and nxt["speaker"]!=spk):
                rt1=nxt["t1"] if nxt else L["t1"]+1.5
                push("cu",L["t1"],rt1,target=(nxt["speaker"] if nxt else None),spk=None,
                     line=(nxt["line"] if nxt and nxt["speaker"]==nxt.get("speaker") else ""),note="听者反应特写")
                i+=2; continue
        elif nxt and nxt.get("speaker") and nxt["speaker"]!=spk and (nxt["t0"]-L["t1"])<1.2:
            # 一来一回 -> 越肩正反打（两句合一镜：听者肩+说话者）
            push("ots",L["t0"],nxt["t1"],host=nxt["speaker"],target=spk,spk=spk,
                 line=L["line"]+" ／ "+nxt["line"],note="正反打：越听者肩看说话者")
            i+=2; continue
        else:
            # 普通一句：越听者肩看说话者（若知道双方）
            if spk and nxt and nxt.get("speaker"):
                push("ots",L["t0"],L["t1"],host=nxt["speaker"],target=spk,spk=spk,line=L["line"],note="越肩看说话者")
            else:
                push("cu",L["t0"],L["t1"],target=spk,spk=spk,line=L["line"],note="说话者近景")
        i+=1
    return shots
def main():
    ap=argparse.ArgumentParser(); ap.add_argument("src"); ap.add_argument("--out")
    a=ap.parse_args()
    data=parse_srt(a.src) if a.src.lower().endswith(".srt") else json.load(open(a.src,encoding="utf-8"))
    shots=plan(data)
    out=a.out or (os.path.splitext(a.src)[0]+"_建议分镜.json")
    json.dump({"source":os.path.basename(a.src),"shots":shots},open(out,"w",encoding="utf-8"),ensure_ascii=False,indent=2)
    print(f"# 建议 {len(shots)} 个镜头 -> {os.path.basename(out)}")
    for s in shots:
        who=f"{s['cam']} host={s.get('host')} target={s.get('target')}"
        print(f"  {s['id']:>3} {s['dur']:5.1f}s {who:28s} | {(s['line'] or '')[:26]}  〔{s['note']}〕")
if __name__=="__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
