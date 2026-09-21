# -*- coding: utf-8 -*-
"""
storyboard.json 校验器（白模管线的可执行规范）
用法: python validate_storyboard.py <storyboard.json>
退出码: 0=通过(可能有警告)  1=有错误
"""
import sys,json
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

MODES={"follow","cu","ots","wide","keys","pov","streetup"}

def validate(cfg):
    errors=[]; warnings=[]
    def err(m): errors.append(m)
    def warn(m): warnings.append(m)

    for k in ["project","title","scene","actors","shots"]:
        if k not in cfg: err(f"缺少顶层字段: {k}")
    if errors: return errors,warnings

    # 输出参数
    if not isinstance(cfg.get("w",960),int) or cfg.get("w",960)%2: err("w 应为偶数整数")
    if not isinstance(cfg.get("h",540),int) or cfg.get("h",540)%2: err("h 应为偶数整数")
    if not (isinstance(cfg.get("fps",24),int) and 1<=cfg.get("fps",24)<=60): err("fps 应在 1-60")

    actors=cfg["actors"]
    if not isinstance(actors,dict) or not actors: err("actors 必须为非空对象"); return errors,warnings

    def check_color(v,label):
        if v is None: return
        ok=isinstance(v,list) and len(v)==3 and all(isinstance(n,int) and 0<=n<=255 for n in v)
        if not ok: err(f"{label} 必须为 3 个 0-255 的整数，或 null")

    # 主角 c（走位主体）
    if "c" not in actors:
        err("必须包含主角 actors.c（走位/相机跟随主体）")
    else:
        c=actors["c"]; path=c.get("path")
        if not isinstance(path,list) or len(path)<2:
            err("actors.c.path 至少需要 2 个走位关键帧 [[t,x,z],...]")
        else:
            for wp in path:
                if not (isinstance(wp,list) and len(wp) in (3,4,5,6) and all(isinstance(v,(int,float)) for v in wp)):
                    err("path 关键帧格式必须为 [t,x,z] / [t,x,y,z] / [t,x,y,z,pitch] / [t,x,y,z,pitch,roll]（数字）"); break
            else:
                ts=[wp[0] for wp in path]
                if ts[0]!=0: err("path 第一个关键帧 t 必须为 0")
                if any(ts[i+1]<=ts[i] for i in range(len(ts)-1)): err("path 的时间 t 必须严格递增")

    for aid,a in actors.items():
        if not isinstance(a,dict): err(f"actors.{aid} 必须为对象"); continue
        if "name" not in a: err(f"actors.{aid} 缺少 name")
        check_color(a.get("shirt"),f"actors.{aid}.shirt")
        check_color(a.get("apron"),f"actors.{aid}.apron")
        if a.get("scale",1.0) and not (isinstance(a.get("scale",1.0),(int,float)) and a.get("scale",1.0)>0):
            err(f"actors.{aid}.scale 必须为正数")
        if aid!="c" and not a.get("static"):
            if not (isinstance(a.get("pos"),list) and len(a.get("pos",[]))==2):
                err(f"actors.{aid} 为静态角色，需要 pos:[x,z]")

    shots=cfg["shots"]
    if not isinstance(shots,list) or not shots: err("shots 必须为非空数组"); return errors,warnings
    ids=set(); total=0.0
    for i,sh in enumerate(shots):
        sid=sh.get("id",f"#{i}")
        if not sh.get("id"): err(f"shots[{i}] 缺少 id")
        if sid in ids: err(f"镜头 id 重复: {sid}")
        ids.add(sid)
        dur=sh.get("dur")
        if not (isinstance(dur,(int,float)) and dur>0): err(f"{sid}: dur 必须为正数")
        else: total+=dur
        mode=sh.get("mode")
        if mode not in MODES: err(f"{sid}: mode 必须为 {sorted(MODES)} 之一")
        fov=sh.get("fov",42)
        if not (isinstance(fov,(int,float)) and 20<=fov<=90): err(f"{sid}: fov 应在 20-90")
        if mode in ("follow","wide","keys"):
            off=sh.get("off")
            if not (isinstance(off,list) and len(off)==3 and all(isinstance(v,(int,float)) for v in off)):
                err(f"{sid}: mode={mode} 需要 off:[x,y,z]")
        if mode=="cu":
            if sh.get("focus") not in actors: err(f"{sid}: cu 的 focus 必须指向 actors 中已有角色")
            if not (isinstance(sh.get("dist"),(int,float)) and sh["dist"]>0): err(f"{sid}: cu 需要 dist>0")
        if mode=="ots":
            if sh.get("host") not in actors: err(f"{sid}: ots 的 host 必须指向 actors 中已有角色")
        if not sh.get("line"): warn(f"{sid}: 无台词/字幕 line")
        if not sh.get("prompt_cn"): warn(f"{sid}: 无 prompt_cn")

    cpath=actors.get("c",{}).get("path")
    if isinstance(cpath,list) and len(cpath)>=2 and cpath[-1][0] < total:
        warn(f"走位 path 结束时间 {cpath[-1][0]} 早于镜头总时长 {total:.1f}s（末段角色会停在终点）")
    return errors,warnings

if __name__=="__main__":
    cfg=json.load(open(sys.argv[1],encoding="utf-8"))
    errs,warns=validate(cfg)
    for w in warns: print("  [警告]",w)
    for e in errs: print("  [错误]",e)
    if errs:
        print(f"❌ 校验未通过：{len(errs)} 个错误，{len(warns)} 个警告")
        sys.exit(1)
    print(f"✅ 校验通过：{len(cfg['shots'])} 个镜头，{sum(s['dur'] for s in cfg['shots']):.1f}s，{len(warns)} 个警告")
    sys.exit(0)




