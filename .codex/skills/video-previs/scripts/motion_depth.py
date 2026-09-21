# -*- coding: utf-8 -*-
"""单目深度图视频：近亮远暗，全场景灰阶。
用法: motion_depth.py <video> --start S --end E --out o.mp4 [--fps 12]
  --enhance stretch|clahe|none   对比增强(默认 stretch)
  --color none|inferno           伪彩
  --global-norm                  全段统一归一化(两遍,尺度稳定)"""
import sys,os,argparse,cv2,numpy as np
def enh(g,mode):
    if mode=="none":return g
    lo,hi=np.percentile(g,[2,98] if mode=="stretch" else [1,99])
    g2=np.clip((g.astype(np.float32)-lo)*255/max(1,hi-lo),0,255).astype(np.uint8)
    if mode=="clahe":g2=cv2.createCLAHE(3.0,(8,8)).apply(g2)
    return g2
def make_writer(out,W,H,fps,color):
    """优先 ffmpeg 管道直出 H.264(yuv420p/faststart，浏览器可播)；无 ffmpeg 退化为 cv2 mp4v。"""
    import shutil,subprocess
    if shutil.which("ffmpeg"):
        pix="bgr24" if color else "gray"
        cmd=["ffmpeg","-y","-v","error","-f","rawvideo","-pix_fmt",pix,"-s",f"{W}x{H}","-r",f"{fps:g}","-i","-",
             "-an","-vf","scale=trunc(iw/2)*2:trunc(ih/2)*2","-c:v","libx264","-preset","fast","-crf","20",
             "-pix_fmt","yuv420p","-movflags","+faststart",out]
        p=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        def wr(fr):p.stdin.write(fr.tobytes())
        def cl():
            try:p.stdin.close()
            except Exception:pass
            p.wait()
        return wr,cl
    vw=cv2.VideoWriter(out,cv2.VideoWriter_fourcc(*"mp4v"),fps,(W,H),color)
    return vw.write,vw.release
def main():
    ap=argparse.ArgumentParser();ap.add_argument("video");ap.add_argument("--start",type=float,default=0)
    ap.add_argument("--end",type=float,default=1e9);ap.add_argument("--out",default=None)
    ap.add_argument("--fps",type=float,default=12);ap.add_argument("--invert",action="store_true")
    ap.add_argument("--enhance",default="none",choices=["stretch","clahe","none"],help="默认none=忠实模型原始深度；stretch/clahe仅轻微提对比")
    ap.add_argument("--color",default="none",choices=["none","inferno"])
    ap.add_argument("--global-norm",action="store_true")
    ap.add_argument("--fix-gaps",action="store_true",help="异常帧用上一正常帧填充(默认关)")
    ap.add_argument("--infer-size",type=int,default=0,help="深度模型输入边长(14的倍数)，0=自动：源≤480p用518全质量，≤720p用336，>720p用308")
    a=ap.parse_args()
    from transformers import pipeline
    from PIL import Image
    cap=cv2.VideoCapture(a.video);sfps=cap.get(cv2.CAP_PROP_FPS) or 24
    W=int(cap.get(3));H=int(cap.get(4))
    end=a.end;real_end=(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)/sfps
    if real_end>0:end=min(end,real_end)
    pipe=pipeline("depth-estimation",model="depth-anything/Depth-Anything-V2-Small-hf",device="cpu")
    short=min(W,H);isz=a.infer_size or (518 if short<=480 else 336 if short<=720 else 308)
    isz=max(98,round(isz/14)*14)
    if isz!=518:pipe.image_processor.size={"height":isz,"width":isz}
    total=max(1,int((end-a.start)*a.fps));iv=max(25,total//20)
    print(f"[信息] 设备=CPU(无CUDA) 总帧数≈{total} 目标fps={a.fps} 源fps={sfps:.2f} 推理边长={isz}",flush=True)
    def tick(done):
        if done%iv==0 or done==total:print(f"PROGRESS {done}/{total} ({done*100//total}%)",flush=True)
    out=a.out or os.path.splitext(a.video)[0]+"_深度.mp4"
    color=a.color!="none"
    write,close=make_writer(out,W,H,a.fps,color)
    def grab(fr):
        rgb=cv2.cvtColor(fr,cv2.COLOR_BGR2RGB)
        d=np.array(pipe(Image.fromarray(rgb))["depth"]).astype(np.float32)
        if a.invert:d=-d
        return d
    # 第一遍：收集深度（或直接边算边出）
    cap.set(cv2.CAP_PROP_POS_MSEC,a.start*1000)
    if a.global_norm:
        raws=[];t=a.start;n=0;done=0;nx=a.start
        while t<end:
            r,fr=cap.read()
            if not r:break
            if t+1e-6>=nx:raws.append(grab(fr));done+=1;tick(done);nx+=1/a.fps
            n+=1;t=a.start+n/sfps
        allv=np.concatenate([x.ravel() for x in raws])
        glo,ghi=np.percentile(allv,[5,95])
        # 可选时序修复：异常帧用上一正常帧 hold(不混合，避免淡入淡出/叠影)
        if a.fix_gaps:
            meds=np.array([np.median(x) for x in raws])
            q1=np.percentile(meds,25); ref=np.median(meds[meds>q1*0.6]) if (meds>q1*0.6).any() else np.median(meds)
            bad=np.abs(meds-ref)>ref*0.55
            last=None
            for i in range(len(raws)):
                if bad[i] and last is not None: raws[i]=last
                elif not bad[i]: last=raws[i]
        for d in raws:
            g=np.clip((d-glo)*255/max(1e-6,ghi-glo),0,255).astype(np.uint8)
            # 保底：整帧过暗(中位数偏低)时，按该帧自身分位轻度提亮，防止个别帧被全段尺度压黑
            if np.median(g)<70:
                lo,hi=np.percentile(g,[5,99]); g=np.clip((g.astype(np.float32)-lo)*255/max(1,hi-lo),0,255).astype(np.uint8)
            if a.enhance=="clahe": g=cv2.createCLAHE(3.0,(8,8)).apply(g)
            write(cv2.applyColorMap(g,cv2.COLORMAP_INFERNO) if color else g)
    else:
        t=a.start;n=0;done=0;nx=a.start
        while t<end:
            r,fr=cap.read()
            if not r:break
            if t+1e-6>=nx:
                d=grab(fr);d=(d-d.min())/(d.max()-d.min()+1e-6)
                gray=enh((d*255).astype(np.uint8),a.enhance)
                write(cv2.applyColorMap(gray,cv2.COLORMAP_INFERNO) if color else gray)
                done+=1;tick(done);nx+=1/a.fps
            n+=1;t=a.start+n/sfps
    cap.release();close()
    print("DEPTH ->",out,W,"x",H,a.enhance,a.color,"global" if a.global_norm else "")
if __name__=="__main__":
    try:sys.stdout.reconfigure(encoding="utf-8")
    except Exception:pass
    main()
