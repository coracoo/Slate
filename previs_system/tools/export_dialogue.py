# -*- coding: utf-8 -*-
"""storyboard JSON -> 带时间戳的台词文件（全局时间轴）。
合并每镜 speaker/line 与镜内台词轨 lines[]，输出:
  <名>_台词.srt   (标准SRT字幕)
  <名>_台词.txt   ([起-止] 【角色】台词)
用法: python export_dialogue.py <storyboard.json> [outdir]
"""
import sys,os,json
def main():
    src=os.path.abspath(sys.argv[1]); cfg=json.load(open(src,encoding="utf-8"))
    actors=cfg.get("actors",{}); outdir=sys.argv[2] if len(sys.argv)>2 else os.path.dirname(src)
    os.makedirs(outdir,exist_ok=True); base=cfg["project"]
    rows=[]; t=0.0
    for sh in cfg["shots"]:
        dur=sh.get("dur",0); t0=t; t=t0+dur
        nm=lambda sp: actors.get(sp,{}).get("name",sp or "")
        if sh.get("lines"):
            for L in sh["lines"]:
                a=t0+float(L.get("at",0)); b=a+float(L.get("dur",2.0));
                rows.append((a,min(b,t),nm(L.get("speaker")),L.get("line","")))
        elif sh.get("line"):
            rows.append((t0,t,nm(sh.get("speaker")),sh["line"]))
    rows=[r for r in rows if r[3] and r[3].strip()]
    def ts(x):
        h=int(x//3600);m=int((x%3600)//60);s=x%60
        return "%02d:%02d:%02d,%03d"%(h,m,int(s),int((s-int(s))*1000))
    srt=os.path.join(outdir,base+"_台词.srt"); txt=os.path.join(outdir,base+"_台词.txt")
    with open(srt,"w",encoding="utf-8") as f:
        for i,(a,b,n,line) in enumerate(rows,1):
            f.write(f"{i}\n{ts(a)} --> {ts(b)}\n{f'【{n}】' if n else ''}{line}\n\n")
    with open(txt,"w",encoding="utf-8") as f:
        for a,b,n,line in rows:
            f.write(f"[{a:7.2f}-{b:7.2f}] {('【'+n+'】') if n else ''}{line}\n")
    print(f"# {len(rows)} 行 -> {srt}\n#        {txt}")
    for a,b,n,line in rows: print(f"  {a:7.2f}-{b:7.2f} {('【'+n+'】') if n else ''}{line}")
if __name__=="__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
