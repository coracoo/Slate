# -*- coding: utf-8 -*-
"""OCR 提取烧录字幕 -> 带时间轴台词（rapidocr，无需联网/轻量）。
用法: python extract_subtitles.py <video> [--start S] [--end E] [--every 0.5]"""
import sys,os,argparse,re,difflib
def main():
    ap=argparse.ArgumentParser(); ap.add_argument("video")
    ap.add_argument("--start",type=float,default=0.0); ap.add_argument("--end",type=float,default=1e9)
    ap.add_argument("--every",type=float,default=0.5); ap.add_argument("--bottom",type=float,default=0.24)
    ap.add_argument("--gap",type=float,default=1.0,help="OCR 短暂丢失容忍秒数(避免一句被断成两段)")
    a=ap.parse_args()
    import cv2,numpy as np
    from rapidocr_onnxruntime import RapidOCR
    ocr=RapidOCR()
    cap=cv2.VideoCapture(a.video); fps=cap.get(cv2.CAP_PROP_FPS) or 24
    H=int(cap.get(4)); y0=int(H*(1-a.bottom))
    t1=min(a.end,(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)/fps); t=a.start
    lines=[]; last=None; last_t=t; last_seen=t; variants={}
    def norm(s): return re.sub(r"\s+","",s)
    def same_line(x,y):
        """OCR 噪声容错：包含关系 / 匹配块覆盖≥70% 短串 / 整体相似度≥0.6 视为同一句。"""
        if x in y or y in x: return True
        sm=difflib.SequenceMatcher(None,x,y)
        m=sum(b.size for b in sm.get_matching_blocks())
        return m/min(len(x),len(y))>=0.7 or sm.ratio()>=0.6
    while t<t1:
        cap.set(cv2.CAP_PROP_POS_MSEC,t*1000); r,f=cap.read()
        if not r: break
        crop=f[y0:H-1,:,:]
        out,_=ocr(crop)
        txt=""
        if out:
            txt=norm("".join([box[1] for box in out]))
        if len(txt)>=2:
            if last is None:
                last,last_t,last_seen=txt,t,t; variants={txt:1}
            elif same_line(txt,last):
                variants[txt]=variants.get(txt,0)+1
                last=max(variants,key=lambda k:(variants[k],len(k)))  # 代表文本：出现最多，平手取最长
                last_seen=t
            else:
                lines.append((last_t,last_seen+a.every,last)); last,last_t,last_seen=txt,t,t; variants={txt:1}
        else:
            if last is not None and t-last_seen>a.gap:
                lines.append((last_t,last_seen+a.every,last)); last=None; variants={}
        t+=a.every
    if last is not None: lines.append((last_t,min(last_seen+a.every,t1),last))
    cap.release()
    base=os.path.splitext(a.video)[0]
    srt=base+"_字幕.srt"; txt=base+"_台词.txt"
    def ts(x):
        h=int(x//3600);m=int((x%3600)//60);s=x%60
        return "%02d:%02d:%02d,%03d"%(h,m,int(s),int((s-int(s))*1000))
    with open(srt,"w",encoding="utf-8") as f:
        for i,(a0,a1,s) in enumerate(lines,1):
            f.write(f"{i}\n{ts(a0)} --> {ts(a1)}\n{s}\n\n")
    with open(txt,"w",encoding="utf-8") as f:
        for a0,a1,s in lines: f.write(f"[{a0:7.1f}-{a1:7.1f}] {s}\n")
    print(f"# {len(lines)} 行 -> {os.path.basename(srt)}")
    for a0,a1,s in lines: print(f"  {a0:6.1f}-{a1:6.1f}  {s}")
if __name__=="__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
