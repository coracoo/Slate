# -*- coding: utf-8 -*-
"""动作捕捉剪影：人物纯白、背景纯黑的黑白遮罩视频。
用法: python motion_silhouette.py <video> --start S --end E --out out.mp4 [--mode rembg|diff]
 rembg: 人像抠像（默认，稳）  diff: 帧差运动（轻量，备选）
"""
import sys,os,argparse,cv2,numpy as np
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
    ap.add_argument("--mode",default="rembg",choices=["rembg","diff"]);ap.add_argument("--fps",type=float,default=12)
    ap.add_argument("--style",default="gray",choices=["gray","binary"]);ap.add_argument("--gain",type=float,default=1.05)
    a=ap.parse_args()
    cap=cv2.VideoCapture(a.video);fps=cap.get(cv2.CAP_PROP_FPS) or 24
    W=int(cap.get(3));H=int(cap.get(4))
    end=a.end;real_end=(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)/fps
    if real_end>0:end=min(end,real_end)
    out=a.out or os.path.splitext(a.video)[0]+"_剪影.mp4"
    write,close=make_writer(out,W,H,a.fps,False)
    session=None
    if a.mode=="rembg":
        from rembg import remove,new_session
        session=new_session("u2net_human_seg")
    total=max(1,int((end-a.start)*a.fps));iv=max(25,total//20)
    print(f"[信息] 设备=CPU(无CUDA) 模式={a.mode} 总帧数≈{total} 目标fps={a.fps}",flush=True)
    def tick(done):
        if done%iv==0 or done==total:print(f"PROGRESS {done}/{total} ({done*100//total}%)",flush=True)
    cap.set(cv2.CAP_PROP_POS_MSEC,a.start*1000);prev=None;t=a.start
    n=0;done=0;nx=a.start
    while t<end:
        r,fr=cap.read()
        if not r:break
        if t+1e-6>=nx:
            if a.mode=="rembg":
                cut=remove(fr,session=session)  # RGBA
                alpha=cut[:,:,3]
                if a.style=="binary":
                    mask=(alpha>128).astype(np.uint8)*255
                    mask=cv2.morphologyEx(mask,cv2.MORPH_CLOSE,np.ones((5,5),np.uint8))
                else:
                    g=alpha.astype(np.float32)/255.0
                    # 柔和 S 曲线保留中间灰阶：亮部仍偏白但不全部爆白，边缘/薄处为灰
                    g=np.power(np.clip(g,0,1),0.75)*a.gain
                    mask=np.clip(g*255,0,255).astype(np.uint8)
                    mask=cv2.GaussianBlur(mask,(3,3),0)
            else:
                g=cv2.cvtColor(fr,cv2.COLOR_BGR2GRAY);g=cv2.GaussianBlur(g,(5,5),0)
                if prev is None: mask=np.zeros_like(g)
                else:
                    d=cv2.absdiff(g,prev);_,mask=cv2.threshold(d,25,255,cv2.THRESH_BINARY)
                    mask=cv2.dilate(mask,np.ones((7,7),np.uint8),iterations=2)
                prev=g
            write(mask)
            done+=1;tick(done);nx+=1/a.fps
        n+=1;t=a.start+n/fps
    cap.release();close()
    print("SILHOUETTE ->",out)
if __name__=="__main__":
    try:sys.stdout.reconfigure(encoding="utf-8")
    except Exception:pass
    main()

