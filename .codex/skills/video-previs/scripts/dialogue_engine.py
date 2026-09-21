# -*- coding: utf-8 -*-
"""
通用 2D 对白/短剧白模引擎（数据驱动，无AI）
读 dialogue storyboard JSON -> 直接出 MP4
相机自动：wide全景 / two双人 / cu单人特写 / ots越肩（由人物站位解析，轴线一致）
人物支持 seated(盘腿坐) / stand(站)。极简场景，不搭假景。
用法: python dialogue_engine.py <storyboard.json> [out.mp4]
"""
import sys,json,math,os
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
import numpy as np
from PIL import Image,ImageDraw,ImageFont,ImageFilter
import cv2
def V(*a): return np.array(a,dtype=float)
def norm(d):
    d=d.copy(); d[1]=0; n=np.linalg.norm(d); return d/n if n>1e-6 else V(0,0,1)
def ease(x): return 2*x*x if x<.5 else 1-(-2*x+2)**2/2
SKY=(176,182,190); FOG=np.array([150,156,158])

class E:
    def __init__(s,cfg,clean=False):
        s.cfg=cfg; s.W=cfg.get("w",960); s.H=cfg.get("h",540); s.FPS=cfg.get("fps",24)
        # 干净帧：storyboard "overlay":false 或 clean=True —— 不烧 HUD（镜号/运镜/提示词/台词），
        # 供图生视频当参考图；默认带 HUD 是人类预演版
        s.clean=clean or cfg.get("overlay") is False
        s.actors=cfg["actors"]; s.shots=cfg["shots"]; s.TOTAL=sum(x["dur"] for x in s.shots)
        s.setd=cfg.get("set",{}); s.fontL=ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc",22)
        s.fontS=ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc",28)
        s.fontP=ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc",17)
        s.fontN=ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc",24)
    class Cam:
        def __init__(s,pos,tgt,fov,eng): s.pos=V(*pos); s.tgt=V(*tgt); s.fov=fov; s.e=eng
        def basis(s):
            f=s.tgt-s.pos; f/=np.linalg.norm(f); r=np.cross(f,V(0,1,0)); r/=np.linalg.norm(r); u=np.cross(r,f); return f,r,u
        def proj(s,p):
            f,r,u=s.basis(); d=p-s.pos; z=d@f
            if z<=0.05: return None
            fc=(s.e.W/2)/math.tan(math.radians(s.fov)/2)
            return s.e.W/2+(d@r)/z*fc, s.e.H/2-(d@u)/z*fc, z
        def radius(s,wr,p):
            q=s.proj(p)
            if q is None: return None
            fc=(s.e.W/2)/math.tan(math.radians(s.fov)/2)
            return q[0],q[1],wr/q[2]*fc,q[2]
    def shade(s,c,z,lit=1):
        t=np.clip(1-(z-2)/46,0.18,1); c=np.array(c,float)*min(lit,1.15)*t+s.FOG*(1-t)
        return tuple(np.clip(c,0,255).astype(int))
    def box(s,cen,siz,yaw,rgb):
        cx,cy,cz=cen; hx,hy,hz=[v/2 for v in siz]; ca,sa=math.cos(yaw),math.sin(yaw)
        rt=lambda x,z:(cx+ca*x-sa*z,cz+sa*x+ca*z); C={}
        for ix in(-1,1):
            for iy in(-1,1):
                for iz in(-1,1):
                    wx,wz=rt(ix*hx,iz*hz); C[(ix,iy,iz)]=(wx,cy+iy*hy,wz)
        fl=[((0,1,0),[(1,1,1),(1,-1,1),(-1,-1,1),(-1,1,1)]),((0,-1,0),[(1,-1,1),(1,-1,-1),(-1,-1,-1),(-1,-1,1)]),
            ((1,0,0),[(1,1,1),(1,-1,1),(1,-1,-1),(1,1,-1)]),((-1,0,0),[(-1,1,1),(-1,-1,1),(-1,-1,-1),(-1,1,-1)]),
            ((0,0,1),[(-1,1,1),(-1,-1,1),(1,-1,1),(1,1,1)]),((0,0,-1),[(-1,1,-1),(-1,-1,-1),(1,-1,-1),(1,1,-1)])]
        for n,ids in fl:
            nw=V(n[0]*ca-n[2]*sa,n[1],n[0]*sa+n[2]*ca); wc=V(*C[ids[0]])
            if nw@(s.cam.pos-wc)<0: continue
            pr=[s.cam.proj(C[k]) for k in ids]
            if any(q is None for q in pr): continue
            lit=.55+.45*abs(nw[1])*.7; z=sum(q[2] for q in pr)/4
            s.polys.append((z,[(q[0],q[1]) for q in pr],s.shade(rgb,z,lit)))
    def ball(s,pos,r,rgb):
        q=s.cam.radius(r,V(*pos))
        if q: s.circs.append((q[3],q[0],q[1],q[2],s.shade(rgb,q[3])))
    def quad(s,x0,x1,z0,z1,rgb,n=1):
        # 大平面细分为 n×n 网格逐格投影：单块四角只要有一个在相机身后就会被整块丢弃，
        # 全景机位贴在地面临界面上时（战场 ±40 地面）会丢掉整片地面，故必须分格
        for i in range(n):
            for j in range(n):
                ex=(x1-x0)/n*0.05; ez=(z1-z0)/n*0.05
                a0=x0+(x1-x0)*i/n-(ex if i>0 else 0); a1=x0+(x1-x0)*(i+1)/n+(ex if i<n-1 else 0)
                b0=z0+(z1-z0)*j/n-(ez if j>0 else 0); b1=z0+(z1-z0)*(j+1)/n+(ez if j<n-1 else 0)
                ps=[s.cam.proj(V(a0,0,b0)),s.cam.proj(V(a1,0,b0)),s.cam.proj(V(a1,0,b1)),s.cam.proj(V(a0,0,b1))]
                if all(ps): s.polys.append((sum(p[2] for p in ps)/4,[(p[0],p[1]) for p in ps],s.shade(rgb,sum(p[2] for p in ps)/4)))
    def seated(s,x,z,yaw,shirt):
        # 盘腿坐姿：双腿坐垫(低宽) + 直立躯干 + 头 + 手臂朝桌
        sc=1.0
        s.box((x,0.16,z),(0.62*sc,0.3,0.5*sc),yaw,(54,50,46))            # 盘坐的腿/坐垫
        s.box((x,0.62,z),(0.44*sc,0.62,0.3*sc),yaw,tuple(shirt))          # 躯干
        s.ball((x,1.06,z),0.15*sc,(226,188,150))                          # 头
        fx,fz=math.sin(yaw),math.cos(yaw)
        s.box((x+fx*0.28,0.66,z+fz*0.28),(0.34*sc,0.1,0.1*sc),yaw,tuple(shirt))  # 手臂朝前(桌)
    def standing(s,x,z,yaw,shirt,y0=0.0):
        s.box((x,0.4+y0,z),(0.34,0.8,0.26),yaw,(48,52,58))
        s.box((x,1.15+y0,z),(0.5,0.85,0.34),yaw,tuple(shirt)); s.ball((x,1.86+y0,z),0.2,(226,188,150))
    def ride(s,x,z,yaw,shirt):
        # 骑马灰模：马体+颈+头+四腿，骑手为抬高的站姿（只锁"骑马"这一调度事实，不做美术）
        fx,fz=math.sin(yaw),math.cos(yaw); sx,sz=math.cos(yaw),-math.sin(yaw)
        dk=(60,56,52)
        s.box((x,0.95,z),(0.52,0.72,1.7),yaw,dk)                                     # 马体
        s.box((x+fx*0.95,1.4,z+fz*0.95),(0.26,0.55,0.5),yaw,dk)                      # 颈
        s.box((x+fx*1.3,1.62,z+fz*1.3),(0.2,0.24,0.42),yaw,dk)                       # 头
        for a,b in ((0.62,0.2),(0.62,-0.2),(-0.62,0.2),(-0.62,-0.2)):
            s.box((x+fx*a+sx*b,0.29,z+fz*a+sz*b),(0.14,0.58,0.14),yaw,dk)            # 四腿
        s.standing(x,z,yaw,shirt,y0=1.28)
    def cur(s,t):
        a=0
        for i,sh in enumerate(s.shots):
            if t<a+sh["dur"]: return i,sh,(t-a)/sh["dur"]
            a+=sh["dur"]
        return len(s.shots)-1,s.shots[-1],1
    def ap(s,id):
        ac=s.actors[id]
        if "path" in ac:  # 走位角色：按 t 在关键帧间线性插值
            p=ac["path"]; t=getattr(s,"t",p[-1][0])
            if t<=p[0][0]: return V(p[0][1],0,p[0][2])
            if t>=p[-1][0]: return V(p[-1][1],0,p[-1][2])
            for (t0,x0,z0),(t1,x1,z1) in zip(p,p[1:]):
                if t<=t1:
                    k=(t-t0)/(t1-t0) if t1>t0 else 0
                    return V(x0+(x1-x0)*k,0,z0+(z1-z0)*k)
        return V(ac["pos"][0],0,ac["pos"][1])
    def yawTo(s,a,b): d=b-a; return math.atan2(d[0],d[2])

    def frame(s,t):
        s.polys=[]; s.circs=[]
        i,sh,p=s.cur(t)
        s.t=t
        tb=sh.get("table") or s.setd.get("table"); T=V(tb["x"],0,tb["z"]) if tb else V(0,0,0)
        staging=sh.get("staging",{})
        fov=sh.get("fov",42)
        host=sh.get("host"); tgt=sh.get("target")
        # ---- 相机：显式优先（1:1 对齐原片构图），否则自动覆盖 ----
        cam=sh.get("cam") or {"follow":"off"}.get(sh.get("mode"))
        if "pos" in sh and "look" in sh:
            cp=V(*sh["pos"]); tg=V(*sh["look"])
        elif cam=="off" and "off" in sh:
            A=s.ap(sh.get("focus") or "c"); host=sh.get("focus") or "c"
            cp=A+V(*sh["off"]); tg=A+V(0,1.0,0)
        elif sh.get("cam")=="near":
            A=s.ap(sh["focus"]); host=sh["focus"]
            cp=V(T[0],1.35,A[2]+2.7); tg=V(T[0],1.0,T[2])
        elif sh.get("cam")=="wide":
            cp=V(T[0],1.95,T[2]-6.2); tg=V(T[0],1.0,T[2]); host=None
        elif sh.get("cam")=="two":
            A=s.ap(host); B=s.ap(tgt); mid=(A+B)/2; d=norm(B-A); perp=V(-d[2],0,d[0])
            cp=mid+perp*-3.2+V(0,1.5,0); tg=mid+V(0,1.0,0); host=None
        elif sh.get("cam")=="cu" and tgt:
            A=s.ap(tgt); d=norm(T-A)
            cp=A+d*2.2+V(0,1.3,0); tg=A+V(0,1.0,0); host=None
        elif sh.get("cam")=="ots" and host and tgt:
            A=s.ap(host); B=s.ap(tgt); d=norm(B-A); perp=V(-d[2],0,d[0])
            cp=A-d*1.05+perp*0.55+V(0,1.25,0); tg=B+V(0,1.0,0)
        else:
            # 兜底：缺角色指向时用全景
            cp=V(T[0],1.95,T[2]-5.6); tg=V(T[0],0.95,T[2]); host=None; sh=dict(sh,cam="wide")
        # ---- 运镜插值（p=镜内进度 0..1）：推拉/环绕/移/升降/摇/甩/变焦 ----
        if p is not None and p < 1.0:
            ease = p*p*p if sh.get("whip") else p          # 甩镜前段极快
            fw=tg-cp; fw[1]=0
            ln=np.linalg.norm(fw)
            if ln>0.01:
                fw=fw/ln; sd=V(-fw[2],0,fw[0])
                d=sh.get("dolly")
                if d=="in": cp=tg+(cp-tg)*(1.28-0.28*ease)
                elif d=="out": cp=tg+(cp-tg)*(1.0+0.28*ease)
                tr=sh.get("truck")
                if tr: cp=cp+sd*tr*(ease-0.5)
                cr=sh.get("crane")
                if cr: cp=cp+V(0,cr*(1-ease),0)
                orb=sh.get("orbit")
                if orb:
                    a=math.radians(orb)*(ease-0.5); ca,sa=math.cos(a),math.sin(a)
                    r=cp-tg; cp=tg+V(r[0]*ca-r[2]*sa,0,r[0]*sa+r[2]*ca)
                pan=sh.get("pan")
                if pan:
                    a=math.radians(pan)*(ease-0.5); ca,sa=math.cos(a),math.sin(a)
                    r=tg-cp; r=V(r[0]*ca-r[2]*sa,0,r[0]*sa+r[2]*ca); tg=cp+r
            zm=sh.get("zoom")
            if zm: fov=fov/(1+ (zm-1)*(1-ease))
        s.cam=E.Cam(cp,tg,fov,s)
        fld=sh.get("scene")=="field"   # 室外战场镜：土地 + 阵旗剪影，无桌案
        s.FOG=np.array([198,182,152]) if fld else FOG   # 战场暖沙尘雾，远处融进土黄天
        # ---- 布景：地面 + 矮桌 ----
        if fld:
            s.quad(-40,40,-40,40,(146,132,110),n=10)
            import random as _r; rr=_r.Random(5)
            for k in range(10):
                fx=(-16+k*3.6)+rr.uniform(-0.5,0.5); fz=13+(k%2)*2.2; h=3.4+rr.uniform(0,1.2)
                p0=s.cam.proj(V(fx,0,fz)); p1=s.cam.proj(V(fx,h,fz))
                if p0 and p1:
                    s.polys.append((p0[2]+0.01,[(p0[0],p0[1]),(p1[0],p1[1]),(p1[0]+1.6,p1[1]+1),(p0[0]+1.6,p0[1])],(84,74,66)))
                    s.polys.append((p0[2]+0.01,[(p1[0],p1[1]),(p1[0]+9,p1[1]+3),(p1[0]+8,p1[1]+5),(p1[0],p1[1]+2)],(99,95,90)))
        else:
            s.quad(-7,7,-7,7,(150,146,134) if s.setd.get("type")=="tatami" else (132,136,140),n=4)
            if tb:
                s.box((T[0],tb.get("h",0.5)/2,T[2]),(tb["w"],tb.get("h",0.5),tb["d"]),0,(120,86,58))
                rnd=np.random.RandomState(3)
                for _ in range(tb.get("dishes",6)):
                    rx=T[0]+rnd.uniform(-tb["w"]/3,tb["w"]/3); rz=T[2]+rnd.uniform(-tb["d"]/3,tb["d"]/3)
                    s.ball((rx,tb.get("h",0.5)+0.06,rz),0.09,(210,210,200))
        # ---- 人物 ----
        ots_host=None
        for aid,ac in s.actors.items():
            if aid in staging: pos=V(staging[aid][0],0,staging[aid][1])
            else: pos=s.ap(aid)
            partners=[x for x in (host,tgt) if x]
            if aid in partners and len(partners)==2:
                other=s.ap(tgt) if aid==host else s.ap(host); yaw=s.yawTo(pos,other)
            elif aid==tgt and host:
                yaw=s.yawTo(pos,s.ap(host))
            elif aid==host and tgt:
                yaw=s.yawTo(pos,s.ap(tgt))
            else:
                # 显式相机或默认：面向桌子
                yaw=s.yawTo(pos,T)
            st=(sh.get("pose") or {}).get(aid) or ac.get("style","seated")
            if st=="ride": s.ride(pos[0],pos[2],yaw,ac["shirt"])
            elif st=="stand": s.standing(pos[0],pos[2],yaw,ac["shirt"])
            else: s.seated(pos[0],pos[2],yaw,ac["shirt"])
            if sh.get("serve") and aid==sh["serve"]:
                s.box((pos[0],0.66,pos[2]+0.42),(0.55,0.1,0.12),yaw,tuple(ac["shirt"]))
            if sh.get("cam")=="ots" and aid==host: ots_host=aid
        # ---- 绘制 ----
        img=Image.new("RGB",(s.W,s.H),(208,196,176) if fld else SKY); dr=ImageDraw.Draw(img,"RGBA")
        items=[(z,"p",pts,c) for z,pts,c in s.polys]+[(z,"c",(x,y,r),c) for z,x,y,r,c in s.circs]
        items.sort(key=lambda q:-q[0])
        for z,k,d,c in items:
            if k=="p": dr.polygon(d,fill=c)
            else:
                x,y,r=d; r=max(1,r); dr.ellipse((x-r,y-r,x+r,y+r),fill=c)
        if ots_host:
            ov=Image.new("RGBA",(s.W,s.H),(0,0,0,0)); od=ImageDraw.Draw(ov)
            col=tuple(s.actors[ots_host]["shirt"])+(235,)
            od.ellipse((-120,s.H+6,250,s.H+200),fill=col); od.ellipse((-60,-120,190,150),fill=col[:3]+(200,))
            ov=ov.filter(ImageFilter.GaussianBlur(13)); img=Image.alpha_composite(img.convert("RGBA"),ov).convert("RGB"); dr=ImageDraw.Draw(img,"RGBA")
        # 顶部运镜
        if not s.clean:
            camTag={"wide":"全景","near":"近景背影","two":"双人中景","cu":"单人特写","ots":"越肩反打"}.get(sh.get("cam"),sh.get("cam","?"))
            moveTxt=sh.get("move",camTag)
            dr.rectangle((0,0,s.W,42),fill=(10,14,18,188))
            dr.text((12,8),f"{sh['id']}   运镜：{moveTxt}",font=s.fontL,fill=(143,208,255,255))
        # 右上 动作/提示词
        if not s.clean:
            def wrap(text,font,maxw):
                out=[];cur=""
                for ch in text:
                    if dr.textlength(cur+ch,font=font)>maxw: out.append(cur);cur=ch
                    else: cur+=ch
                if cur: out.append(cur)
                return out
            panel=[]
            if sh.get("action"): panel.append("● 动作："+sh["action"])
            if sh.get("prompt"): panel.append("● 提示词："); panel+=wrap(sh["prompt"],s.fontP,330)
            if panel:
                ph=len(panel)*24+14; pw=360; px=s.W-pw-10; py=50
                dr.rectangle((px,py,s.W-10,py+ph),fill=(10,14,18,175)); yy=py+8
                for ln in panel:
                    col=(255,214,120,255) if ln.startswith("● 动作") else ((150,220,170,255) if ln.startswith("● 提示") else (210,222,238,255))
                    dr.text((px+12,yy),ln,font=s.fontP,fill=col); yy+=24
        # 底部台词（带角色名）；支持长镜头镜内台词轨 lines:[{at,dur,speaker,line}]（clean 帧不烧字幕）
        if s.clean:
            return np.array(img)[:,:,::-1]
        spk=sh.get("speaker"); line=sh.get("line",""); sub=""
        if sh.get("lines"):
            elap=p*sh["dur"]
            cur=None
            for L in sh["lines"]:
                if L["at"]<=elap<L["at"]+L.get("dur",2.0): cur=L; break
            if cur:
                spk=cur.get("speaker"); line=cur.get("line","")
        if spk and line:
            nm="旁白" if spk=="narrator" else s.actors.get(spk,{}).get("name",spk); sub=f"【{nm}】{line}"; col=(255,244,214,255)
        elif line:
            sub=line; col=(200,206,214,255)
        elif sh.get("action"):
            sub="（"+sh["action"]+"）"; col=(150,158,168,255)
        else: sub=""
        if sub:
            tb=dr.textbbox((0,0),sub,font=s.fontN); tw=tb[2]-tb[0]
            dr.rectangle((0,s.H-58,s.W,s.H),fill=(8,10,12,205))
            dr.text(((s.W-tw)/2,s.H-46),sub,font=s.fontN,fill=col)
        return np.array(img)[:,:,::-1]

    def render(s,out):
        vw=cv2.VideoWriter(out,cv2.VideoWriter_fourcc(*"mp4v"),s.FPS,(s.W,s.H))
        n=int(s.TOTAL*s.FPS)
        for f in range(n): vw.write(s.frame(f/s.FPS))
        vw.release(); return n
if __name__=="__main__":
    cfg=json.load(open(sys.argv[1],encoding="utf-8"))
    out=sys.argv[2] if len(sys.argv)>2 else os.path.abspath(os.path.join(os.path.dirname(sys.argv[1]),"..","out",cfg["project"]+"_对白白模.mp4"))
    n=E(cfg).render(out); print(f"[OK] {cfg['project']}: {n} frames / {sum(x['dur'] for x in cfg['shots']):.1f}s -> {out}")
