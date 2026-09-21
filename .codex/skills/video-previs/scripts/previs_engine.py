# -*- coding: utf-8 -*-
"""
白模预演渲染引擎 v4（数据驱动，无AI）
v4: 人物改为带关节的人形骨模(球-棒骨架)，可摆 站立/四肢张开飞/头朝下俯冲 等真实姿态
用法: python previs_engine.py <storyboard.json> [out.mp4]
"""
import sys,json,math,os
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
import numpy as np
from PIL import Image,ImageDraw,ImageFont,ImageFilter
import cv2

def np3(*a): return np.array(a,dtype=float)
def norm(d):
    d=d.copy(); d[1]=0; n=np.linalg.norm(d); return d/n if n>1e-6 else np3(0,0,1)
def ease(x): return 2*x*x if x<.5 else 1-(-2*x+2)**2/2
def clamp(x,a,b): return max(a,min(b,x))
def Ry(a): c,s=math.cos(a),math.sin(a); return np.array([[c,0,s],[0,1,0],[-s,0,c]])
def Rx(a): c,s=math.cos(a),math.sin(a); return np.array([[1,0,0],[0,c,-s],[0,s,c]])
def Rz(a): c,s=math.cos(a),math.sin(a); return np.array([[c,-s,0],[s,c,0],[0,0,1]])
SKIN=(222,186,146); LEG=(46,50,58)

# 三档本地姿势（局部坐标：+Y上 +Z面朝 ，骨盆原点）
def pose_stand():
    return dict(pelvis=(0,0,0),neck=(0,0.52,0),head=(0,0.80,0),
      shL=(-0.19,0.50,0),elL=(-0.21,0.27,0.01),haL=(-0.21,0.06,0.02),
      shR=( 0.19,0.50,0),elR=( 0.21,0.27,0.01),haR=( 0.21,0.06,0.02),
      hipL=(-0.09,0,0),knL=(-0.10,-0.42,0.01),ftL=(-0.10,-0.82,0.03),
      hipR=( 0.09,0,0),knR=( 0.10,-0.42,0.01),ftR=( 0.10,-0.82,0.03))
def pose_fly():  # 四肢张开自由落体（身体水平）
    return dict(pelvis=(0,0,0),neck=(0,0.52,0),head=(0,0.78,0.06),
      shL=(-0.19,0.50,0),elL=(-0.42,0.46,0.12),haL=(-0.60,0.36,0.26),
      shR=( 0.19,0.50,0),elR=( 0.42,0.46,0.12),haR=( 0.60,0.36,0.26),
      hipL=(-0.09,0,0),knL=(-0.17,-0.40,-0.02),ftL=(-0.27,-0.76,0.14),
      hipR=( 0.09,0,0),knR=( 0.17,-0.40,-0.02),ftR=( 0.27,-0.76,0.14))
def pose_dive():  # 头朝下俯冲，手臂前伸、双腿并拢
    return dict(pelvis=(0,0,0),neck=(0,0.52,0),head=(0,0.78,0.10),
      shL=(-0.19,0.50,0),elL=(-0.14,0.56,0.16),haL=(-0.10,0.82,0.30),
      shR=( 0.19,0.50,0),elR=( 0.14,0.56,0.16),haR=( 0.10,0.82,0.30),
      hipL=(-0.09,0,0),knL=(-0.07,-0.42,0),ftL=(-0.08,-0.84,0.02),
      hipR=( 0.09,0,0),knR=( 0.07,-0.42,0),ftR=( 0.08,-0.84,0.02))
def blend_pose(pitch):
    S,F,D=pose_stand(),pose_fly(),pose_dive()
    p=clamp(pitch,0,170)
    out={}
    def lerpd(A,B,t): return {k:tuple(np3(*A[k])+(np3(*B[k])-np3(*A[k]))*t) for k in A}
    if p<=90: out=lerpd(S,F,p/90.0)
    else: out=lerpd(F,D,(p-90)/80.0)
    return out

class Engine:
    def __init__(s,cfg):
        s.cfg=cfg
        s.W=cfg.get("w",960); s.H=cfg.get("h",540); s.FPS=cfg.get("fps",24)
        s.SKY=(176,186,196) if cfg.get("scene")=="city_aerial" else (185,192,199)
        s.FOG=np.array([150,160,172]) if cfg.get("scene")=="city_aerial" else np.array([150,158,150])
        s.shots=cfg["shots"]; s.actors=cfg["actors"]
        s.TOTAL=sum(x["dur"] for x in s.shots)
        s.aerial = cfg.get("scene")=="city_aerial"
        s.fogNear=2.0; s.fogFar=120.0 if s.aerial else 52.0
        s.fogFloor=0.20 if s.aerial else 0.10
        s.fontL=ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc",22)
        s.fontSub=ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc",28)

    class Cam:
        def __init__(s,pos,tgt,fov,eng):
            s.pos=np3(*pos); s.target=np3(*tgt); s.fov=fov; s.eng=eng
        def basis(s):
            f=s.target-s.pos; f/=np.linalg.norm(f)
            r=np.cross(f,np3(0,1,0)); r/=np.linalg.norm(r); u=np.cross(r,f); return f,r,u
        def proj(s,p):
            f,r,u=s.basis(); d=p-s.pos; z=d@f
            if z<=0.05: return None
            focal=(s.eng.W/2)/math.tan(math.radians(s.fov)/2)
            return s.eng.W/2+(d@r)/z*focal, s.eng.H/2-(d@u)/z*focal, z
        def radius(s,wr,p):
            pr=s.proj(p)
            if pr is None: return None
            focal=(s.eng.W/2)/math.tan(math.radians(s.fov)/2)
            return pr[0],pr[1],wr/pr[2]*focal,pr[2]

    def actor_state(s,t):
        path=s.actors["c"]["path"]; P=[]
        for k in path:
            if len(k)==3:   q=(k[1],0,k[2],0.0,0.0)
            elif len(k)==4: q=(k[1],k[2],k[3],0.0,0.0)
            elif len(k)==5: q=(k[1],k[2],k[3],k[4],0.0)
            else:           q=(k[1],k[2],k[3],k[4],k[5])
            P.append((k[0],np3(q[0],q[1],q[2]),q[3],q[4]))
        for i in range(len(P)-1):
            if P[i][0]<=t<=P[i+1][0]:
                k=ease((t-P[i][0])/(P[i+1][0]-P[i][0]))
                return (P[i][1]+(P[i+1][1]-P[i][1])*k, i, P,
                        P[i][2]+(P[i+1][2]-P[i][2])*k, P[i][3]+(P[i+1][3]-P[i][3])*k)
        return P[-1][1],len(P)-2,P,P[-1][2],P[-1][3]

    def cur(s,t):
        a=0
        for i,sh in enumerate(s.shots):
            if t<a+sh["dur"]: return i,sh,(t-a)/sh["dur"]
            a+=sh["dur"]
        return len(s.shots)-1,s.shots[-1],1.0

    def shade(s,rgb,z,lit=1.0):
        t=np.clip(1-(z-s.fogNear)/(s.fogFar-s.fogNear),s.fogFloor,1.0)
        c=np.array(rgb,float)*min(lit,1.15)*t+np.array(s.FOG,float)*(1-t)
        return tuple(np.clip(c,0,255).astype(int))
    def box(s,cen,siz,yaw,rgb):
        cx,cy,cz=cen;hx,hy,hz=[v/2 for v in siz];ca,sa=math.cos(yaw),math.sin(yaw)
        def rt(x,z): return (cx+ca*x-sa*z,cz+sa*x+ca*z)
        C={}
        for ix in(-1,1):
            for iy in(-1,1):
                for iz in(-1,1):
                    wx,wz=rt(ix*hx,iz*hz);C[(ix,iy,iz)]=(wx,cy+iy*hy,wz)
        fl=[((0,1,0),[(1,1,1),(1,-1,1),(-1,-1,1),(-1,1,1)]),((0,-1,0),[(1,-1,1),(1,-1,-1),(-1,-1,-1),(-1,-1,1)]),
            ((1,0,0),[(1,1,1),(1,-1,1),(1,-1,-1),(1,1,-1)]),((-1,0,0),[(-1,1,1),(-1,-1,1),(-1,-1,-1),(-1,1,-1)]),
            ((0,0,1),[(-1,1,1),(-1,-1,1),(1,-1,1),(1,1,1)]),((0,0,-1),[(-1,1,-1),(-1,-1,-1),(1,-1,-1),(1,1,-1)])]
        for n,ids in fl:
            nw=np3(n[0]*ca-n[2]*sa,n[1],n[0]*sa+n[2]*ca); wc=np3(*C[ids[0]])
            if nw@(s.camera.pos-wc)<0: continue
            pr=[s.camera.proj(C[k]) for k in ids]
            if any(q is None for q in pr): continue
            lit=0.5+0.5*abs(nw[1])*0.7+0.35*max(0,nw@np3(0.3,0.7,0.3))
            z=sum(q[2] for q in pr)/4
            s.polys.append((z,[(q[0],q[1]) for q in pr],s.shade(rgb,z,lit)))
    def obox(s,siz,R,wcen,rgb):
        hx,hy,hz=[v/2 for v in siz]; C={}
        for ix in(-1,1):
            for iy in(-1,1):
                for iz in(-1,1):
                    C[(ix,iy,iz)]=wcen+R@np3(ix*hx,iy*hy,iz*hz)
        fl=[((0,1,0),[(1,1,1),(1,-1,1),(-1,-1,1),(-1,1,1)]),((0,-1,0),[(1,-1,1),(1,-1,-1),(-1,-1,-1),(-1,-1,1)]),
            ((1,0,0),[(1,1,1),(1,-1,1),(1,-1,-1),(1,1,-1)]),((-1,0,0),[(-1,1,1),(-1,-1,1),(-1,-1,-1),(-1,1,-1)]),
            ((0,0,1),[(-1,1,1),(-1,-1,1),(1,-1,1),(1,1,1)]),((0,0,-1),[(-1,1,-1),(-1,-1,-1),(1,-1,-1),(1,1,-1)])]
        for n,ids in fl:
            nw=R@np3(*n); wc=C[ids[0]]
            if nw@(s.camera.pos-wc)<0: continue
            pr=[s.camera.proj(C[k]) for k in ids]
            if any(q is None for q in pr): continue
            lit=0.5+0.5*abs(nw[1])*0.7+0.35*max(0,nw@np3(0.3,0.7,0.3))
            z=sum(q[2] for q in pr)/4
            s.polys.append((z,[(q[0],q[1]) for q in pr],s.shade(rgb,z,lit)))
    def ball(s,pos,r,rgb,lit=1.0):
        q=s.camera.radius(r,np3(*pos))
        if q: s.circs.append((q[3],q[0],q[1],q[2],s.shade(rgb,q[3],lit)))
    def limb(s,w1,w2,r,rgb):
        w1=np3(*w1); w2=np3(*w2); d=w2-w1; L=float(np.linalg.norm(d))
        if L<1e-3: return
        mid=(w1+w2)/2; yaw=math.atan2(d[0],d[2]); tilt=-math.asin(clamp(d[1]/L,-1,1))
        Rm=Ry(yaw)@Rx(tilt)
        s.obox((2*r,2*r,L),Rm,mid,rgb)
    def gquad(s,x0,x1,z0,z1,y,rgb):
        ps=[s.camera.proj(np3(x0,y,z0)),s.camera.proj(np3(x1,y,z0)),s.camera.proj(np3(x1,y,z1)),s.camera.proj(np3(x0,y,z1))]
        if all(ps): s.polys.append((sum(p[2] for p in ps)/4,[(p[0],p[1]) for p in ps],s.shade(rgb,sum(p[2] for p in ps)/4)))

    def person(s,pelvis,yaw,pitch,roll,shirt,scale=1.0,legs=None,head=None,hand=None):
        """关节骨模。pelvis=骨盆世界坐标。pitch:0站 90水平飞 170头朝下。颜色RGB可配。"""
        R=Ry(yaw)@Rx(math.radians(pitch))@Rz(math.radians(roll))
        base=np3(*pelvis); sc=scale; sh=tuple(shirt)
        legc=tuple(legs) if legs else LEG; headc=tuple(head) if head else SKIN; handc=tuple(hand) if hand else SKIN
        J=blend_pose(pitch)
        W={k: base+R@(np3(*v)*sc) for k,v in J.items()}
        def L(a,b,r,col): s.limb(W[a],W[b],r*sc,col)
        L('pelvis','neck',0.105,sh); s.ball(W['neck'],0.07*sc,sh)
        s.ball(W['head'],0.125*sc,headc)
        for side in ('L','R'):
            L('sh'+side,'el'+side,0.052,sh); L('el'+side,'ha'+side,0.046,sh)
            s.ball(W['sh'+side],0.06*sc,sh); s.ball(W['ha'+side],0.05*sc,handc)
        for side in ('L','R'):
            L('hip'+side,'kn'+side,0.068,legc); L('kn'+side,'ft'+side,0.058,legc)
            s.ball(W['kn'+side],0.06*sc,legc); s.ball(W['ft'+side],0.055*sc,legc)

    def scooter(s,pos,yaw):
        x,_,z=pos
        s.box((x,0.55,z),(0.5,0.4,1.25),yaw,(42,46,52)); s.box((x,0.82,z+0.55),(0.42,0.5,0.3),yaw,(52,56,62))
        s.ball((x,0.28,z+0.6),0.27,(22,24,28)); s.ball((x,0.28,z-0.55),0.27,(22,24,28))
        s.box((x,1.12,z+0.7),(0.72,0.06,0.06),yaw,(30,32,36))

    # ---------- 标准资产库（参数化建模，可复用） ----------
    def tower(s,x,z,w,h,d=6.0,yaw=0.0,style="concrete"):
        if style=="glass":
            base=(122,150,166); 
        elif style=="res":
            base=(166,150,132)
        else:
            base=(150,148,144)
        s.box((x,h/2,z),(w,h,d),yaw,base)
        # 顶部收分
        s.box((x,h+0.8,z),(w*0.6,1.6,d*0.6),yaw,tuple(int(v*0.92) for v in base))
        if style=="glass":
            s.box((x,h+0.1,z),(w*1.02,0.5,d*1.02),yaw,(96,126,140))      # 顶部深色檐
            s.box((x+w*0.18,h+3.0,z),(0.25,4.2,0.25),yaw,(60,64,70))    # 天线
        elif style=="res":
            s.box((x-w*0.22,h+1.2,z-d*0.2),(w*0.32,1.4,d*0.3),yaw,(140,126,108))  # 水箱/阁楼
        else:
            s.box((x,h+2.2,z),(0.22,3.2,0.22),yaw,(70,72,76))
    def rooftop(s,x,z,topH):
        s.box((x,topH/2,z),(7,topH,7),0,(150,156,164))                 # 楼顶所在楼
        s.box((x,topH+0.6,z),(7,1.2,7),0,(176,182,188))                # 台面
        s.box((x-2.2,topH+1.4,z-2.2),(1.4,1.6,1.4),0,(120,124,130))    # 设备间
        s.box((x+2.0,topH+1.0,z+2.0),(0.15,2.2,0.15),0,(70,74,80))     # 竖杆
    def tree(s,x,z,h=3.4):
        s.box((x,h/2-0.2,z),(0.3,h,0.3),0,(107,86,66)); s.ball((x,h+0.9,z),1.45,(110,140,96))
    def lamp(s,x,z):
        s.box((x,2.6,z),(0.12,5.2,0.12),0,(70,74,78)); s.box((x,5.2,z+0.6),(0.1,0.1,1.3),0,(90,92,80))
    def car(s,x,z,yaw,col=(150,40,40)):
        s.box((x,0.45,z),(1.7,0.7,3.4),yaw,col); s.box((x,0.95,z-0.2),(1.3,0.6,1.6),yaw,tuple(int(v*0.8) for v in col))

    def build_scene(s):
        if s.cfg.get("scene")=="street_stall":
            s.gquad(-7,2.7,-32,12,0,(86,90,94)); s.gquad(2.7,6.6,-32,12,0,(140,142,136))
            # 临街建筑（资产复用，带门洞/铺面）
            for zz,hh,ww in [(-26,7,4),(-18,8.5,4.5),(-10,6.5,5),(-2,8,4),(7,7.5,5),(15,6,4)]:
                s.box((-7.4,hh/2,zz),(ww,hh,4.6),0,(150,144,134)); s.box((-7.4,1.3,zz),(ww*0.7,2.6,0.2),0,(110,104,94))
            for zz,hh,ww in [(-22,6.5,4),(-13,8,4.5),(-5,7,4),(4,8.5,4.5),(12,6.5,5)]:
                s.box((7.9,hh/2,zz),(ww,hh,4.6),0,(140,138,132)); s.box((7.9,1.3,zz),(ww*0.7,2.6,0.2),0,(104,100,92))
            for zz in range(-28,10,5):
                s.tree(-4.3,zz); s.tree(5.2,zz+2.5,3.2)
            s.lamp(-1.2,-6); s.lamp(-1.2,8)
            sx,sz=2.9,0.7
            s.box((sx,0.9,sz),(1.7,0.12,3.2),0,(138,106,70))
            for lx,lz in[(-0.6,-1.2),(0.6,-1.2),(-0.6,1.2),(0.6,1.2)]: s.box((sx+lx,0.45,sz+lz),(0.1,0.9,0.1),0,(122,92,60))
            rnd=np.random.RandomState(7)
            for _ in range(16): s.ball((sx-0.55+rnd.rand()*1.1,1.18,sz-1.3+rnd.rand()*2.6),0.23,(74,125,58))
            for i in range(5): s.ball((sx+0.62,1.2,sz-1.1+i*0.45),0.11,(224,193,77))
            s.box((sx+0.55,1.15,sz+1.2),(0.4,0.25,0.4),0,(102,102,102))
            s.box((sx-0.95,1.4,sz+1.3),(0.05,0.7,0.5),-0.2,(239,234,224))
            s.box((sx+1.8,0.8,sz+0.2),(1.1,1.6,1.0),0,(124,124,120)); s.box((sx+1.8,1.25,sz+0.72),(0.4,0.4,0.04),0,(160,32,32))
            s.car(-5.2,-3.2,0.2,(170,60,60))
        elif s.aerial:
            s.gquad(-46,46,-90,34,0,(98,102,106)); s.gquad(-3.2,3.2,-90,34,0.02,(58,60,64))
            for zz in range(-80,24,6): s.box((0,0.06,zz),(6.4,0.02,0.18),0,(200,200,180))  # 车道虚线
            # 楼顶起跳平台（远）
            s.rooftop(2,-44,28)
            # 倾斜玻璃屋顶（S03：砸上、贴附滑落）
            gR=Ry(0.25)@Rx(-0.52)
            s.obox((15,0.6,13),gR,np3(1.5,25,-12),(126,158,176))
            s.obox((15,0.3,13),gR,np3(1.5,25,-12),(150,182,198))
            s.box((1.5,18.5,-12),(0.6,13,0.6),0,(90,96,104))   # 支撑
            # 主角会经过的玻璃高塔（摆荡锚点）
            s.tower(9,-12,6,46,6,0.0,"glass"); s.tower(-9,2,6,42,6,0.0,"glass"); s.tower(8,8,5,36,5,0.0,"glass")
            # 两侧城市峡谷（精选可复布局，不是随机）
            layout=[(-13,-70,6,30,"res"),(13,-66,7,36,"concrete"),(-14,-56,7,40,"glass"),(14,-50,6,28,"res"),
                    (-13,-38,6,34,"concrete"),(13,-32,7,44,"glass"),(-15,-24,7,38,"res"),(15,-20,6,30,"concrete"),
                    (-14,-6,7,42,"glass"),(14,2,7,36,"res"),(-13,10,6,28,"concrete"),(15,16,7,40,"glass"),
                    (-15,24,6,34,"res"),(13,28,6,26,"concrete")]
            for x,z,w,h,st in layout: s.tower(x,z,w,h,6,0.0,st)

    def render_frame(s,t):
        s.polys=[]; s.circs=[]; s.webs=[]
        i,sh,p=s.cur(t)
        C,seg,P,pitch,roll=s.actor_state(t)
        av=s.actors.get("v"); ah=s.actors.get("h")
        host=None
        if s.aerial:
            d=P[seg+1][1]-P[seg][1]; yc=math.atan2(d[0],d[2]) if abs(d[0])+abs(d[2])>1e-6 else 0
            V=Hp=None
        else:
            V=np3(av["pos"][0],0.95,av["pos"][1]); Hp=np3(ah["pos"][0],0.95,ah["pos"][1])
            yc=math.atan2(V[0]-C[0],V[2]-C[2]); pitch=roll=0
        mode=sh["mode"]
        eye=np3(0,0.62,0) if not s.aerial else np3(0,0,0)
        Cc=C+np3(0,0.95,0) if not s.aerial else C
        off=np3(*sh.get("off",[0,2,5]))
        if "off2" in sh: off=off+(np3(*sh["off2"])-off)*ease(p)
        if mode=="follow": cp=Cc+off; tgt=Cc
        elif mode=="wide": tgt=(Cc+V)/2 if V is not None else Cc; cp=Cc+off
        elif mode=="pov": cp=C+np3(0,1.2,-3.2); tgt=np3(C[0],0,C[2]+42)
        elif mode=="streetup": cp=np3(C[0]+4,2.0,C[2]+15); tgt=C
        elif mode=="cu":
            fov=sh.get("fov",42)
            if V is not None:
                A=(Cc if sh.get("focus","c")=="c" else V)+eye
                B=(V if sh.get("focus","c")=="c" else Cc)+eye
                dd=norm(B-A); perp=np3(-dd[2],0,dd[0])
                cp=A+dd*sh["dist"]+perp*0.15; tgt=A
            else:
                fwd=np3(math.sin(yc),0,math.cos(yc)); cp=Cc+fwd*sh["dist"]; tgt=Cc
            s.camera=s.Cam(cp,tgt,fov,s); s._draw(C,yc,pitch,roll,V,Hp,av,ah,sh,None,mode,t); return s._finish(sh,mode,None)
        else:
            A=(Cc if sh["host"]=="c" else V)+eye; B=(V if sh["host"]=="c" else Cc)+eye
            dd=norm(B-A); perp=np3(-dd[2],0,dd[0])*0.5
            cp=A-dd*0.95+perp; tgt=B; host=sh["host"]
        s.camera=s.Cam(cp,tgt,sh.get("fov",42),s)
        s._draw(C,yc,pitch,roll,V,Hp,av,ah,sh,host,mode,t)
        return s._finish(sh,mode,host)

    def _draw(s,C,yc,pitch,roll,V,Hp,av,ah,sh,host,mode,t):
        s.build_scene()
        ac=s.actors["c"]
        if s.aerial:
            ac2=s.actors['c']
            s.person(C,yc,pitch,roll,(190,35,45),scale=1.0,
                     legs=ac2.get('legs'),head=ac2.get('head'),hand=ac2.get('hand'))
            if sh.get("web"):
                anc=np3(*sh["anchor"]); handp=C+np3(0,1.4,0)
                a1=s.camera.proj(handp); a2=s.camera.proj(anc)
                if a1 and a2: s.webs.append(((a1[0],a1[1]),(a2[0],a2[1])))
        else:
            # 越肩(OTS)：前景宿主人物不画整身，由 _finish 的虚化肩块代表
            if host!="c":
                if sh.get("ride") or t<ac.get("rideUntil",0):
                    s.scooter(C,0); s.person(np3(C[0],0.78,C[2]),0,0,0,ac["shirt"])
                else:
                    s.scooter(np3(ac["scooterPark"][0],0,ac["scooterPark"][1]),0)
                    s.person(np3(C[0],0.95,C[2]),yc,0,0,ac["shirt"])
            else:
                s.scooter(np3(ac["scooterPark"][0],0,ac["scooterPark"][1]),0)
            yv=math.atan2(C[0]-V[0],C[2]-V[2]) if V is not None else 0
            if V is not None and host!="v":
                s.person(V,yv,0,0,av["shirt"],scale=av.get("scale",1))
            if Hp is not None:
                s.person(Hp,yv,0,0,ah["shirt"],scale=ah.get("scale",0.9))

    def _finish(s,sh,mode,host):
        img=Image.new("RGB",(s.W,s.H),s.SKY); dr=ImageDraw.Draw(img,"RGBA")
        items=[(z,"p",pts,c) for z,pts,c in s.polys]+[(z,"c",(x,y,r),c) for z,x,y,r,c in s.circs]
        items.sort(key=lambda q:-q[0])
        for z,k,d,c in items:
            if k=="p": dr.polygon(d,fill=c)
            else:
                x,y,r=d; r=max(1,r); dr.ellipse((x-r,y-r,x+r,y+r),fill=c)
        for (x1,y1),(x2,y2) in getattr(s,"webs",[]):
            dr.line((x1,y1,x2,y2),fill=(235,238,245,230),width=2)
            dr.ellipse((x2-4,y2-4,x2+4,y2+4),fill=(235,238,245,220))
        if host:
            ov=Image.new("RGBA",(s.W,s.H),(0,0,0,0)); od=ImageDraw.Draw(ov)
            col=(30,42,62,225) if host=="c" else (120,100,70,225)
            if host=="c": od.ellipse((-120,s.H+10,230,s.H+210),fill=col)
            else: od.ellipse((s.W-230,s.H+10,s.W+120,s.H+210),fill=col)
            od.rectangle((0,s.H-40,s.W,s.H),fill=(10,12,10,60))
            ov=ov.filter(ImageFilter.GaussianBlur(14)); img=Image.alpha_composite(img.convert("RGBA"),ov).convert("RGB"); dr=ImageDraw.Draw(img,"RGBA")
        dr.rectangle((0,0,s.W,42),fill=(10,14,12,175))
        tag={"follow":"跟随跟拍","wide":"广角","cu":"特写推镜","ots":"越肩OTS","pov":"俯冲主观POV","streetup":"街面仰拍","keys":"关键帧"}.get(mode,mode)
        dr.text((12,9),f"{sh['id']}   [{tag}] fov{sh.get('fov',42)}",font=s.fontL,fill=(160,220,165,255))
        sub=sh.get("line",""); tb=dr.textbbox((0,0),sub,font=s.fontSub); tw=tb[2]-tb[0]
        dr.rectangle((0,s.H-58,s.W,s.H),fill=(8,10,8,190)); dr.text(((s.W-tw)/2,s.H-48),sub,font=s.fontSub,fill=(255,255,255,255))
        return np.array(img)[:,:,::-1]

    def render(s,out):
        vw=cv2.VideoWriter(out,cv2.VideoWriter_fourcc(*"mp4v"),s.FPS,(s.W,s.H))
        n=int(s.TOTAL*s.FPS)
        for f in range(n): vw.write(s.render_frame(f/s.FPS))
        vw.release(); return n

if __name__=="__main__":
    cfg=json.load(open(sys.argv[1],encoding="utf-8"))
    out=sys.argv[2] if len(sys.argv)>2 else os.path.join(os.path.dirname(sys.argv[1]),"..","out",cfg["project"]+"_白模.mp4")
    out=os.path.abspath(out)
    eng=Engine(cfg); n=eng.render(out)
    print(f"[OK] {cfg['project']}: {n} frames / {eng.TOTAL:.1f}s -> {out}")
