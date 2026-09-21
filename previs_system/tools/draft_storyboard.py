# -*- coding: utf-8 -*-
"""
AI 起草分镜：台词 SRT -> 视觉模型不可用时仅文本模型 -> 分镜骨架 JSON（含景别/画面描述建议）。
一行台词 = 一个候选镜头；文本模型统一给每镜配景别与画面描述。输出 <名>_AI骨架.json 到同级 分镜/。
用法: python draft_storyboard.py <srt> [--scene street_stall]
"""
import sys,os,json,argparse,re
HERE=os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0,HERE)
import glm_client as G
from lapian_docs import slug

SHOT2MODE={"全景":"wide","双人中景":"two","特写":"cu","近景":"cu","中景":"two","越肩":"ots"}
def ts(s):
    m=int(s//60); return f"{m:02d}:{int(s%60):02d}"
def parse_srt(p):
    rows=[]; cur={}
    for ln in open(p,encoding="utf-8-sig"):
        ln=ln.strip("\ufeff").strip()
        if "-->" in ln:
            a,b=ln.split("-->"); cur={"s":hms(a),"e":hms(b)}
        elif ln and ln.isdigit() and "s" not in cur: pass
        elif ln and "s" in cur and "line" not in cur: cur["line"]=ln; rows.append(cur); cur={}
        elif not ln and cur: cur={}
    return rows
def hms(x):
    x=x.strip().replace(",",".")
    h,m,sec=x.split(":"); return int(h)*3600+int(m)*60+float(sec)
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("src"); ap.add_argument("--scene",default="street_stall"); ap.add_argument("--title")
    a=ap.parse_args()
    if not G.is_configured(): sys.exit("未配置 ZHIPUAI_API_KEY/GLM_API_KEY，无法 AI 起草")
    sp=os.path.abspath(a.src)
    base=re.sub(r"_(台词|字幕|ASR)(_.*)?$","",os.path.splitext(os.path.basename(sp))[0])
    rows=parse_srt(sp)
    if not rows: sys.exit("SRT 解析为空")
    listing="\n".join(f"{i}. [{r['s']:.1f}-{r['e']:.1f}s] {r['line']}" for i,r in enumerate(rows,1))
    out=G.chat([
      {"role":"system","content":"你是微短剧分镜师。为每行台词设计一个镜头。输出严格 JSON：{\"shots\":[{\"i\":1,\"shot\":\"<全景|双人中景|中景|近景|特写|越肩>\",\"prompt_cn\":\"<画面描述45-70字，含景别机位/主体动作/环境/情绪>\"}]}，shots 数量与输入行数一致。只输出 JSON。"},
      {"role":"user","content":f"剧名：{a.title or base}\n台词时间轴：\n{listing}"}],
      model=G.text_model(),timeout=180)
    d=G.extract_json(out)
    shots=[]
    for idx,(r,ai) in enumerate(zip(rows,d.get("shots",[])),1):
        shot=ai.get("shot") if isinstance(ai,dict) else None
        shots.append({"id":"S%02d"%idx,"shot":shot or "中景",
                      "mode":SHOT2MODE.get(shot or "中景","two"),
                      "dur":round(max(r["e"]-r["s"],0.8),2),
                      "line":r["line"],"prompt_cn":(ai.get("prompt_cn") if isinstance(ai,dict) else "") or "",
                      "host":"c","target":"v"})
    cfg={"project":slug(base)+"_ai","title":a.title or base,"scene":a.scene,"w":960,"h":540,"fps":24,
         "actors":{"c":{"name":"主角","shirt":[60,90,140],"apron":None,"scale":1.0,
                        "path":[[0.0,-1.0,2.0],[rows[-1]["e"]+1,-1.0,2.0]]},
                   "v":{"name":"对手","shirt":[120,82,60],"apron":None,"scale":1.0,"static":True,"pos":[1.6,0.6]}},
         "shots":shots}
    sto=os.path.join(os.path.dirname(os.path.dirname(sp)),"分镜"); os.makedirs(sto,exist_ok=True)
    jp=os.path.join(sto,f"{slug(base)}_AI骨架.json")
    json.dump(cfg,open(jp,"w",encoding="utf-8"),ensure_ascii=False,indent=1)
    print(f"# AI 分镜骨架 -> {jp}  ({len(shots)} 镜)")
    for s in shots[:5]: print(f"  {s['id']} {s['shot']} {s['dur']}s | {s['line'][:24]} | {s['prompt_cn'][:30]}")
if __name__=="__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
