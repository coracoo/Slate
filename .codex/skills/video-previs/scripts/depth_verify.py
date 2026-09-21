# -*- coding: utf-8 -*-
"""深度图验证：抽样帧，统计亮度分布 + 前景/背景分离度，并生成增强对比图。
用法: python depth_verify.py <depth.mp4> [--src 原片.mp4]"""
import sys,os,argparse,cv2,numpy as np
ap=argparse.ArgumentParser();ap.add_argument("depth");ap.add_argument("--src",default=None);a=ap.parse_args()
cap=cv2.VideoCapture(a.depth);n=int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
sel=[int(x) for x in np.linspace(0,n-1,6)]
rows=[]
for i in sel:
    cap.set(cv2.CAP_PROP_POS_FRAMES,i);r,f=cap.read()
    if not r:continue
    g=cv2.cvtColor(f,cv2.COLOR_BGR2GRAY)
    h,w=g.shape
    # 前景=下三分之二中央区(人物/桌)，背景=上部/两侧
    fg=g[int(h*0.45):,int(w*0.15):int(w*0.85)]
    bg=np.concatenate([g[:int(h*0.4)].ravel(),g[:, :int(w*0.1)].ravel(),g[:,int(w*0.9):].ravel()])
    pct=np.percentile(g,[2,25,50,75,98])
    sep=fg.mean()-bg.mean()
    # 直方图拉伸(2%-98% clip) + CLAHE
    lo,hi=np.percentile(g,[2,98]);stretch=np.clip((g.astype(np.float32)-lo)*255/max(1,(hi-lo)),0,255).astype(np.uint8)
    clahe=cv2.createCLAHE(clipLimit=3.0,tileGridSize=(8,8)).apply(g)
    color=cv2.applyColorMap(stretch,cv2.COLORMAP_INFERNO)
    rows.append((i,g,pct,sep,stretch,clahe,color))
cap.release()
print("帧   p2/p25/p50/p75/p98      前景-背景均值差(分离度)")
for i,g,pct,sep,*_ in rows:
    print(f"{i:4d} "+" ".join(f"{int(v):3d}" for v in pct)+f"   sep={sep:+.1f}")
# 拼图：原始 / 拉伸 / CLAHE / 伪彩（取第2、4帧示例）
tiles=[]
for k in [1,3]:
    if k<len(rows):
        i,g,pct,sep,stretch,clahe,color=rows[k]
        for im,c in [(g,"raw"),(stretch,"stretch"),(clahe,"clahe"),(cv2.cvtColor(color,cv2.COLOR_BGR2GRAY),"inferno")]:
            t=cv2.cvtColor(im,cv2.COLOR_GRAY2BGR) if im.ndim==2 else im
            t=cv2.resize(t,(300,170));cv2.putText(t,c,(6,18),cv2.FONT_HERSHEY_SIMPLEX,0.6,(0,255,255),2);tiles.append(t)
sheet=np.full(((170)*2,300*4,3),20,np.uint8)
for j,t in enumerate(tiles): sheet[(j//4)*170:(j//4+1)*170,(j%4)*300:(j%4+1)*300]=t
ok,buf=cv2.imencode(".jpg",sheet,[cv2.IMWRITE_JPEG_QUALITY,92]);buf.tofile(os.path.join(os.path.dirname(os.path.abspath(a.depth)), "depth_verify.jpg"))
print("verify sheet saved")
