# -*- coding: utf-8 -*-
"""
拉片文档生成器（确定性）：切点 cuts.txt -> 拉片切点表.md + 分镜骨架.json
前置：先跑 extract_shots.py（其产物 frames_<名>/cuts.txt）。
骨架 JSON 可直接被 export_docs.py 导出 md/xlsx，或人工填台词/机位后渲染白模。
用法: python lapian_docs.py <video>
"""
import sys,os,json,argparse,re
def slug(s):
    s=re.sub(r"[^\w\u4e00-\u9fff-]+","_",s).strip("_")
    return s or "proj"
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("video")
    a=ap.parse_args()
    v=os.path.abspath(a.video)
    base=os.path.splitext(os.path.basename(v))[0]
    projdir=os.path.dirname(os.path.dirname(v))  # projects/<项目>/拉片素材/<video>
    cuts=os.path.join(os.path.dirname(v),"frames_"+base,"cuts.txt")
    if not os.path.isfile(cuts):
        sys.exit(f"找不到切点文件 {cuts}，请先跑 extract_shots.py")
    rows=[]
    for ln in open(cuts,encoding="utf-8"):
        p=ln.split()
        if len(p)>=2: rows.append((float(p[0]),float(p[1])))
    if not rows: sys.exit("cuts.txt 为空")
    lap=os.path.join(projdir,"拉片"); sto=os.path.join(projdir,"分镜")
    os.makedirs(lap,exist_ok=True); os.makedirs(sto,exist_ok=True)
    # ① 拉片切点表 md
    md=[f"# {base} · 拉片切点表\n",
        f"> 来源 `{os.path.basename(v)}` · {len(rows)} 段 · 总时长 {rows[-1][1]:.1f}s · 接触印片见 拉片素材/frames_{base}/contact_*.jpg\n",
        "| 段 | 开始(s) | 结束(s) | 时长(s) | 内容备注 |","|---|---|---|---|---|"]
    for i,(s,e) in enumerate(rows,1):
        md.append(f"| S{i:02d} | {s:.1f} | {e:.1f} | {e-s:.2f} | |")
    md.append("\n> 备注：内容备注列人工填写；快切段（<1s）可合并。")
    mdpath=os.path.join(lap,f"{base}_拉片切点.md")
    open(mdpath,"w",encoding="utf-8").write("\n".join(md))
    # ③ 分镜骨架 json（供 export_docs / dialogue_engine 消费，人工润色后可用）
    cfg={
      "project":slug(base),"title":base,"scene":"street_stall","w":960,"h":540,"fps":24,
      "actors":{
        "c":{"name":"主角","shirt":[60,90,140],"apron":None,"scale":1.0,
             "path":[[0.0,-1.0,2.0],[max(r[1] for r in rows)+1,-1.0,2.0]]},
        "v":{"name":"对手","shirt":[120,82,60],"apron":None,"scale":1.0,"static":True,"pos":[1.6,0.6]}
      },
      "shots":[{"id":"S%02d"%i,"shot":"全景","mode":"wide","dur":round(e-s,2),
                "line":"","prompt_cn":f"第{i}段（{s:.1f}-{e:.1f}s）待填画面描述"}
               for i,(s,e) in enumerate(rows,1)]
    }
    jpath=os.path.join(sto,f"{slug(base)}_骨架.json")
    json.dump(cfg,open(jpath,"w",encoding="utf-8"),ensure_ascii=False,indent=1)
    print(f"# 拉片切点表 -> {mdpath}")
    print(f"# 分镜骨架   -> {jpath}  ({len(cfg['shots'])} 镜)")
    print("# 下一步：人工填 shot/line/prompt_cn 后，可用导出分镜表/渲染白模")
if __name__=="__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
