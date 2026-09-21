# -*- coding: utf-8 -*-
"""blender_previs.py — storyboard JSON -> Blender 构建脚本（支持两种契约）
1) dialogue 契约：shots 带显式 pos/look（海街 tatami 等）
2) previs 契约：actors.c 带 path 走位，shots 用 mode=follow/wide/cu/ots/pov/streetup
用法: python blender_previs.py <storyboard.json>
输出: projects/<项目>/白模3D/gen_<slug>.py（发 Blender MCP 执行）；stdout 末行 GEN_SCRIPT:<path>
坐标: JSON x=横 y=上 z=深；Blender (x, 深, 上)
"""
import sys, os, json, re

HEADER = r'''# -*- coding: utf-8 -*-
# 由 blender_previs.py 自动生成 —— 改 storyboard JSON 后重新生成
import bpy, math, os, re
from mathutils import Vector, Euler
DATA = __DATA__
def _in(node, ident):
    return next((s for s in node.inputs if s.identifier == ident), None)
def mat(name, color, metallic=0.0, rough=0.7, emission=None, emit_str=1.0):
    m = bpy.data.materials.new(name); m.use_nodes = True
    b = next(n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    _in(b, "Base Color").default_value = (*color, 1.0)
    _in(b, "Metallic").default_value = metallic
    _in(b, "Roughness").default_value = rough
    if emission is not None:
        e = _in(b, "Emission Color") or _in(b, "Emission")
        e.default_value = (*emission, 1.0)
        _in(b, "Emission Strength").default_value = emit_str
    return m
def box(name, loc, half, m, rot=(0,0,0)):
    bpy.ops.mesh.primitive_cube_add(size=2, location=loc, rotation=rot)
    o = bpy.context.object; o.name = name; o.scale = half
    o.data.materials.append(m); return o
def cyl(name, loc, r, d, m, rot=(0,0,0), verts=16):
    bpy.ops.mesh.primitive_cylinder_add(vertices=verts, radius=r, depth=d, location=loc, rotation=rot)
    o = bpy.context.object; o.name = name; o.data.materials.append(m); return o
def sph(name, loc, r, m, scale=(1,1,1)):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=10, radius=r, location=loc)
    o = bpy.context.object; o.name = name; o.scale = scale; o.data.materials.append(m); return o
def alight(name, loc, energy, size, color, aim, size_y=None):
    bpy.ops.object.light_add(type='AREA', location=loc)
    l = bpy.context.object; l.name = name
    l.data.energy = energy; l.data.size = size
    if size_y is not None: l.data.size_y = size_y
    l.data.color = color
    l.rotation_euler = (Vector(aim)-l.location).to_track_quat('-Z','Y').to_euler()
def P(j):  # engine(x, y上, z深) -> blender(x, z深, y上)
    return (j[0], j[2], j[1])
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
for blk in (bpy.data.meshes, bpy.data.materials, bpy.data.lights, bpy.data.cameras):
    for d in list(blk):
        if d.users == 0: blk.remove(d)
def new_fig(name, loc, yaw):
    e = bpy.data.objects.new(name, None); bpy.context.collection.objects.link(e)
    e.location = loc; e.rotation_euler = (0,0,yaw); return e
def put(o, e, loc, rot=(0,0,0)):
    R = e.rotation_euler.to_matrix()
    o.location = e.location + R @ Vector(loc)
    o.rotation_euler = (Euler(rot).to_matrix() @ R).to_euler()
    o.parent = e; o.matrix_parent_inverse = e.matrix_world.inverted()
'''

FIGS = r'''
def fig_stand(e, rgb, apron=None):
    shirt = mat("shirt_"+e.name, tuple(c/255 for c in rgb), rough=0.8)
    apm = mat("apron_"+e.name, tuple(c/255 for c in apron), rough=0.8) if apron else None
    def B(n, loc, half, m, rot=(0,0,0)):
        o = box(n,(0,0,0),half,m); put(o,e,loc,rot); return o
    def S(n, loc, r, m, sc=(1,1,1)):
        o = sph(n,(0,0,0),r,m); o.scale=sc; put(o,e,loc); return o
    B("legs",(0,0,0.42),(0.16,0.13,0.42),mat("pants_"+e.name,(0.12,0.11,0.12),rough=0.85))
    B("torso",(0,0,0.98),(0.19,0.13,0.30),shirt)
    if apm: B("apron",(0,0.10,0.92),(0.16,0.02,0.26),apm)
    S("head",(0,0,1.36),0.12,mat("skin_"+e.name,(0.78,0.62,0.50),rough=0.65))
    S("hair",(0,-0.03,1.40),0.13,mat("hair_"+e.name,(0.05,0.04,0.04)),sc=(1,1.05,0.85))
    for s in (-1,1):
        B("arm",(s*0.21,0,0.98),(0.05,0.05,0.30),shirt)
        S("hand",(s*0.21,0,0.64),0.045,mat("skin_"+e.name,(0.78,0.62,0.50)))
        B("foot",(s*0.09,0.06,0.05),(0.09,0.16,0.05),mat("shoe_"+e.name,(0.08,0.08,0.09)))
def fig_seated(e, rgb):
    shirt = mat("shirt_"+e.name, tuple(c/255 for c in rgb), rough=0.8)
    pm = mat("pants_"+e.name,(0.12,0.10,0.10),rough=0.85)
    sk = mat("skin_"+e.name,(0.78,0.62,0.50),rough=0.65)
    hk = mat("hair_"+e.name,(0.05,0.04,0.04))
    cu = mat("cush_"+e.name,(0.45,0.38,0.30),rough=0.9)
    po = mat("porce_"+e.name,(0.90,0.92,0.93),rough=0.35)
    def B(n, loc, half, m, rot=(0,0,0)):
        o = box(n,(0,0,0),half,m); put(o,e,loc,rot); return o
    def C(n, loc, r, d, m, rot=(0,0,0)):
        o = cyl(n,(0,0,0),r,d,m); put(o,e,loc,rot); return o
    def S(n, loc, r, m, sc=(1,1,1)):
        o = sph(n,(0,0,0),r,m); o.scale=sc; put(o,e,loc); return o
    B("zabuton",(0,0,0.035),(0.36,0.36,0.035),cu)
    B("hips",(0,0,0.17),(0.17,0.14,0.09),pm)
    B("torso",(0,-0.04,0.50),(0.17,0.12,0.25),shirt,rot=(math.radians(-10),0,0))
    S("head",(0,0,0.84),0.115,sk); S("hair",(0,-0.02,0.88),0.125,hk,sc=(1,1.05,0.82))
    for s in (-1,1):
        B("knee",(s*0.14,0.26,0.12),(0.085,0.24,0.07),pm,rot=(0,0,math.radians(s*12)))
        B("upper_arm",(s*0.18,0.06,0.60),(0.05,0.05,0.14),shirt,rot=(math.radians(-62),0,0))
        B("forearm",(s*0.10,0.20,0.52),(0.045,0.16,0.045),shirt,rot=(math.radians(-48),0,0))
        S("hand",(s*0.07,0.26,0.56),0.04,sk)
    C("bowl",(0,0.24,0.60),0.075,0.07,po)
def scooter(e, name):
    g = bpy.data.objects.new(name, None); bpy.context.collection.objects.link(g)
    g.parent = e; g.matrix_parent_inverse = e.matrix_world.inverted()
    dm = mat("scooter_"+name,(0.16,0.18,0.20),metallic=0.4,rough=0.4)
    def GB(n, loc, half, m, rot=(0,0,0)):
        o = box(n,(0,0,0),half,m); put(o,g,loc,rot); return o
    def GC(n, loc, r, d, m, rot=(0,0,0)):
        o = cyl(n,(0,0,0),r,d,m,rot=rot); put(o,g,loc); return o
    GB("body",(0,0.05,0.55),(0.22,0.55,0.16),dm)
    GB("seat",(0,-0.25,0.78),(0.20,0.25,0.06),mat("seat_"+name,(0.05,0.05,0.06)))
    GB("handle",(0,0.62,0.95),(0.26,0.04,0.04),dm)
    GC("wheelF",(0,0.62,0.18),0.20,0.06,dm,rot=(math.radians(90),0,0))
    GC("wheelB",(0,-0.45,0.18),0.20,0.06,dm,rot=(math.radians(90),0,0))
    return g

def fig_horse(e):
    # 骑马灰模：马体+颈+头+四腿（+y 为前，z 为上；骑手角色空抬 z=1.28 配合）
    hm = mat("horse_"+e.name,(0.30,0.26,0.23),rough=0.9)
    def B(n, loc, half, m, rot=(0,0,0)):
        o = box(n,(0,0,0),half,m); put(o,e,loc,rot); return o
    B("body",(0,0,1.0),(0.24,0.55,0.30),hm)
    B("neck",(0,0.62,1.35),(0.13,0.16,0.26),hm,rot=(math.radians(-32),0,0))
    B("head",(0,0.82,1.58),(0.09,0.20,0.10),hm)
    for sx in (-1,1):
        for sy in (-1,1):
            B("leg",(sx*0.14,sy*0.40,0.45),(0.07,0.07,0.45),hm)

def fig_pillar(e, rgb):
    # 人形立柱：柱身=角色色（唯一标记）+ 头 + 双臂（臂枢轴可摆动=走路摆臂）
    # 返回 {armL, armR} 臂枢轴空，供行走动画 keyframe rotation
    body = mat("pbody_"+e.name, tuple(c/255 for c in rgb), rough=0.8)
    dark = mat("pleg_"+e.name, tuple(c/255*0.45 for c in rgb), rough=0.9)
    sk = mat("pskin_"+e.name,(0.78,0.62,0.50),rough=0.65)
    def B(n, loc, half, m):
        o = box(n,(0,0,0),half,m); put(o,e,loc); return o
    B("legs",(0,0,0.28),(0.15,0.12,0.28),dark)
    B("torso",(0,0,0.92),(0.21,0.15,0.40),body)
    put(sph("phead_"+e.name,(0,0,0),0.14,sk), e, (0,0,1.56))
    parts = {}
    for s, nm in ((-1,"armL"),(1,"armR")):
        a = bpy.data.objects.new(nm+"_"+e.name, None)
        bpy.context.collection.objects.link(a)
        a.location = (s*0.28, 0, 1.26)
        a.parent = e; a.matrix_parent_inverse = e.matrix_world.inverted()
        o = cyl(nm+"mesh_"+e.name,(0,0,-0.24),0.05,0.50,body)
        put(o, a, (0,0,0))
        parts[nm] = a
    return parts

def fig_pillar_seated(e, rgb):
    # 坐姿立柱（军帐议事）：矮柱身盘坐 + 头 + 臂前伸（静态，不参与行走）
    body = mat("pbody_"+e.name, tuple(c/255 for c in rgb), rough=0.8)
    sk = mat("pskin_"+e.name,(0.78,0.62,0.50),rough=0.65)
    def B(n, loc, half, m):
        o = box(n,(0,0,0),half,m); put(o,e,loc); return o
    B("legs",(0,0.12,0.10),(0.30,0.30,0.10),body)
    B("torso",(0,0,0.48),(0.21,0.15,0.32),body)
    put(sph("phead_"+e.name,(0,0,0),0.14,sk), e, (0,0,0.98))
    for s in (-1,1):
        B("arm",(s*0.22,0.10,0.52),(0.05,0.05,0.22),body)
'''

def dialogue_template():
    return HEADER + FIGS + r'''
stype = (DATA.get("set") or {}).get("type", "generic")
T = DATA.get("set", {}).get("table") or {}
Tx, Td = T.get("x",0.0), T.get("z",0.0)
Th, Tw, Tdd = T.get("h",0.5), T.get("w",2.0), T.get("d",1.0)
actors = DATA.get("actors", {})
shots = DATA["shots"]
# 分场景共存：军帐/房间在原点，战场旷野平移到 x=FX（同一 blend 两条时间线共用角色坐标）
sc_of = [str(sh.get("scene") or "room") for sh in shots]
has_room = any(s != "field" for s in sc_of)
has_field = "field" in sc_of
FX = 60.0
xs = [a["pos"][0] for a in actors.values()] + [Tx]; ds = [a["pos"][1] for a in actors.values()] + [Td]
cx = (min(xs)+max(xs))/2; span_x = max(3.2,(max(xs)-min(xs))+2.4)
cd = (min(ds)+max(ds))/2; span_d = max(3.2,(max(ds)-min(ds))+2.8)
far = max(ds)+1.6
if has_room:
    if stype == "tatami":
        M_floor=mat("tatami",(0.52,0.47,0.30),rough=0.95); M_wood=mat("wood",(0.36,0.23,0.13),rough=0.75)
        M_woodD=mat("woodD",(0.17,0.10,0.06),rough=0.7)
        M_paper=mat("shoji",(0.93,0.89,0.80),rough=0.95,emission=(0.95,0.88,0.72),emit_str=1.6); warm=(1.0,0.93,0.80)
    else:
        M_floor=mat("floor",(0.20,0.22,0.24),rough=0.85); M_wood=mat("wall",(0.32,0.36,0.38),rough=0.9)
        M_woodD=mat("dark",(0.10,0.12,0.14),rough=0.9); M_paper=M_wood; warm=(0.90,0.94,1.0)
    M_porce=mat("porce",(0.90,0.92,0.93),rough=0.35)
    box("floor",(cx,cd,-0.06),(span_x/2,span_d/2,0.06),M_floor)
    box("wall_back",(cx,far+0.05,1.35),(span_x/2+0.2,0.08,1.4),M_wood)
    if stype=="tatami":
        box("shoji_paper",(cx,far-0.02,1.45),(span_x/2-0.2,0.02,1.0),M_paper)
        for i in range(5):
            xx=cx-span_x/2+0.25+i*(span_x-0.5)/4
            box("shoji_post",(xx,far-0.06,1.45),(0.035,0.04,1.0),M_woodD)
        box("beam",(cx,far-0.06,2.45),(span_x/2,0.06,0.06),M_woodD)
        box("wall_side",(cx-span_x/2-0.05,cd,1.35),(0.08,span_d/2,1.4),M_wood)
        box("shoji_side",(cx-span_x/2-0.02,cd,1.45),(0.02,span_d/2-0.3,1.0),M_paper)
    else:
        box("wall_side",(cx-span_x/2-0.05,cd,1.35),(0.08,span_d/2,1.4),M_wood)
    box("ceiling",(cx,cd,2.75),(span_x/2,span_d/2,0.06),M_woodD)
    _rt = max(0.6, min(0.95, Tdd*0.55))
    if stype=="tatami":
        cyl("table_top",(Tx,Td,Th-0.03),_rt,0.06,M_wood)
        for a in range(4):
            ang=a*math.tau/4+math.radians(45)
            cyl("table_leg",(Tx+_rt*0.72*math.cos(ang),Td+_rt*0.72*math.sin(ang),Th/2-0.05),0.04,Th-0.08,M_woodD)
        for i in range(int(T.get("dishes",6))):
            ang=i*math.tau/int(T.get("dishes",6))+0.3
            cyl("dish_%d"%i,(Tx+_rt*0.62*math.cos(ang),Td+_rt*0.62*math.sin(ang),Th+0.01),0.08,0.03,M_porce)
    else:
        box("table_top",(Tx,Td,Th-0.035),(min(Tw,2.4)/2,min(Tdd,1.2)/2,0.035),M_wood)
        for sx in (-1,1):
            for sd in (-1,1):
                box("table_leg",(Tx+sx*min(Tw,2.4)*0.44,Td+sd*min(Tdd,1.2)*0.42,Th/2-0.05),(0.05,0.05,Th/2),M_woodD)
if has_field:
    # 战场旷野：土地 + 阵后战旗剪影 + 两翼远处军阵低块（占位灰模，不做美术）
    M_dirt=mat("dirt",(0.36,0.31,0.25),rough=1.0)
    M_pole=mat("pole",(0.25,0.19,0.13),rough=0.9)
    M_flagA=mat("flagA",(0.55,0.52,0.48),rough=0.9)
    M_flagB=mat("flagB",(0.30,0.31,0.34),rough=0.9)
    M_army=mat("army",(0.22,0.23,0.25),rough=0.95)
    M_rock=mat("rock",(0.29,0.28,0.26),rough=1.0)
    box("f_ground",(FX,0,-0.06),(30,30,0.06),M_dirt)
    import random as _fr; frnd=_fr.Random(11)
    for side,zbase,mf in ((1,14,M_flagA),(-1,-14,M_flagB)):
        for i in range(9):
            fx=FX-14+i*3.5+frnd.uniform(-0.6,0.6); fz=zbase+(i%2)*2.0
            h=3.6+frnd.uniform(0,1.4)
            cyl("fpole_%d_%d"%(side,i),(fx,fz,h/2),0.05,h,M_pole)
            box("fflag_%d_%d"%(side,i),(fx+0.75,fz,h-0.45),(0.7,0.03,0.42),mf,rot=(0,0,math.radians(frnd.uniform(-14,14))))
    for side in (-1,1):
        for r in range(3):
            for k in range(12):
                box("f_army_%d_%d_%d"%(side,r,k),(FX+side*(13+r*2.4),-9+k*1.7+r*0.5,0.5+r*0.06),(0.5,0.62,0.5+r*0.06),M_army)
    for i in range(6):
        rx=FX+(6+frnd.uniform(2,14))*(1 if i%2 else -1); rz=frnd.uniform(-12,12)
        box("f_rock_%d"%i,(rx,rz,0.12),(0.5+frnd.random()*0.7,0.4+frnd.random()*0.5,0.12),M_rock,rot=(0,0,math.radians(frnd.uniform(-30,30))))
# 角色：room 副本在原点，field 副本平移 FX；per-shot staging/场景切换用位置关键帧
# 有 pose 表的新分镜：room 区坐姿立柱（议事）、field 区站立立柱（角色色=唯一标记）；
# 位置变化不再瞬移——镜内走到位（位移插值+上下起伏+摆臂），出入画仍瞬移。
# 老分镜（无 pose）沿用 actor style 判断的旧小人。
has_pose = any(sh.get("pose") for sh in shots)
figs={}; figsF={}; armsF={}
for tag, tgt, xoff in ((("actor_"),figs,0.0),(("actorF_"),figsF,FX)):
    for aid, ac in actors.items():
        px,pd = ac["pos"][0]+xoff, ac["pos"][1]
        yaw = math.atan2(-(Tx-px),(Td-pd))
        if has_pose:
            seated = (xoff == 0.0)
            e = new_fig(tag+aid,(px,pd,0),yaw)
            parts = (fig_pillar_seated if seated else fig_pillar)(e, ac.get("shirt",[200,200,200]))
            if not seated: armsF[aid] = parts
        else:
            seated = stype=="tatami" or ac.get("style","seated")!="stand"
            e = new_fig(tag+aid,(px,pd,0),yaw)
            (fig_seated if seated else fig_stand)(e, ac.get("shirt",[200,200,200]))
        tgt[aid]=e
# env 场景 DSL（可由 LLM 生成，见 gen_scene_env.py）：room/field 各一份几何清单，原样灰模拼装
# {"boxes":[[cx,cy,cz,sx,sy,sz,r,g,b],...], "cyls":[[cx,cy,cz,r,h,r,g,b],...], "spheres":[[cx,cy,cz,r,r,g,b],...]}
_env = DATA.get("env") or {}
def _env_build(spec, xoff, zone):
    if not isinstance(spec, dict): return
    _c=[0]
    def M(rgb):
        _c[0]+=1
        try: c=tuple(max(0.0,min(1.0,float(x))) for x in rgb[:3])
        except Exception: c=(0.45,0.45,0.47)
        return mat("env_%s_%d"%(zone,_c[0]), c, rough=0.9)
    def F(v, d):
        try: return float(v)
        except Exception: return d
    for i,b in enumerate((spec.get("boxes") or [])[:60]):
        if not isinstance(b,(list,tuple)) or len(b)<6: continue
        v=[F(x,0.0) for x in b[:6]]
        box("env_%s_box%d"%(zone,i),(v[0]+xoff,v[1],v[2]),(abs(v[3])/2,abs(v[4])/2,abs(v[5])/2),M(b[6:9]))
    for i,c in enumerate((spec.get("cyls") or [])[:60]):
        if not isinstance(c,(list,tuple)) or len(c)<5: continue
        v=[F(x,0.0) for x in c[:5]]
        cyl("env_%s_cyl%d"%(zone,i),(v[0]+xoff,v[1],v[2]),abs(v[3]),abs(v[4]),M(c[5:8]))
    for i,s in enumerate((spec.get("spheres") or [])[:60]):
        if not isinstance(s,(list,tuple)) or len(s)<4: continue
        v=[F(x,0.0) for x in s[:4]]
        sph("env_%s_sph%d"%(zone,i),(v[0]+xoff,v[1],v[2]),abs(v[3]),M(s[4:7]))
_env_build(_env.get("room"), 0.0, "room")
_env_build(_env.get("field"), FX, "field")
# 战场骑马：每角色一匹马（仅 field 区），位置关键帧与角色同步、z 恒 0
horses={}
if has_pose and any("ride" in (sh.get("pose") or {}).values() for sh in shots):
    for aid, ac in actors.items():
        px,pd = ac["pos"][0]+FX, ac["pos"][1]
        yaw = math.atan2(-(Tx-px),(Td-pd))
        e = new_fig("horseF_"+aid,(px,pd,0),yaw)
        fig_horse(e); horses[aid]=e
if has_room:
    alight("key",(cx,far-0.6,2.2),420 if stype=="tatami" else 260,5.0,warm,(cx,cd,0.8),size_y=2.2)
    alight("fill",(cx,cd-2.6,2.2),140,4.0,warm,(cx,cd+0.5,0.9),size_y=2.4)
if has_field:
    alight("f_sun",(FX-12,-14,18),1600,12.0,(1.0,0.97,0.90),(FX,0,1.2),size_y=14)
    alight("f_amb",(FX+8,10,12),400,10.0,(0.75,0.78,0.82),(FX,0,0))
def make_cam(name,jloc,jaim,fov):
    bpy.ops.object.camera_add(location=P(jloc))
    c=bpy.context.object; c.name=name; c.data.angle=math.radians(fov)
    c.rotation_euler=(Vector(P(jaim))-c.location).to_track_quat('-Z','Y').to_euler()
    return c
def _kloc(o, loc, f):
    if o is None: return
    o.location=Vector(loc); o.keyframe_insert("location", frame=f)
def _kpose(cam, loc, aim, f):
    cam.location=Vector(loc); cam.keyframe_insert("location", frame=f)
    cam.rotation_euler=(Vector(aim)-Vector(loc)).to_track_quat('-Z','Y').to_euler()
    cam.keyframe_insert("rotation_euler", frame=f)
def _rot_xy(rv, ang):
    # blender P 系里绕竖直轴(z)旋转
    ca,sa=math.cos(ang),math.sin(ang)
    return Vector((rv.x*ca-rv.y*sa, rv.x*sa+rv.y*ca, rv.z))
sc=bpy.context.scene
for m in list(sc.timeline_markers): sc.timeline_markers.remove(m)
AWAY=(25.0,25.0,0.0)
frame=1; first=None; prevR=None; prevF=None; prevH=None
for i,sh in enumerate(shots):
    fld = sc_of[i]=="field"; off = FX if fld else 0.0
    p0=[sh["pos"][0]+off]+list(sh["pos"][1:]); l0=[sh["look"][0]+off]+list(sh["look"][1:])
    cam=make_cam("CAM_%02d"%(i+1),p0,l0,sh.get("fov",42))
    if first is None: first=cam
    f_end=frame+max(1,round(float(sh.get("dur",2))*DATA.get("fps",24)))
    st = sh.get("staging") or {}
    pose = sh.get("pose") or {}
    R={}; F={}; H={}
    for aid,ac in actors.items():
        base=(ac["pos"][0],ac["pos"][1],0.0)
        away = st.get(aid) is not None
        if fld:
            R[aid]=AWAY; F[aid]=AWAY if away else base
            ride = (pose.get(aid)=="ride") and not away
            F[aid]=(base[0],base[1],1.28) if ride else F[aid]
            H[aid]=AWAY if away else (base[0],base[1],0.0)
        else:
            R[aid]=AWAY if away else base; F[aid]=AWAY; H[aid]=AWAY
    fps = DATA.get("fps", 24)
    # 行走计划：field 区角色前后两镜都在画内且位置变化 -> 本镜内走到位（位移+起伏+摆臂，不瞬移）
    walks = {}
    if has_pose and prevF is not None and frame>1:
        for aid, ac in actors.items():
            p, n = prevF[aid], F[aid]
            if p is AWAY or n is AWAY or (abs(p[0]-n[0])<0.12 and abs(p[1]-n[1])<0.12):
                continue
            d = math.hypot(n[0]-p[0], n[1]-p[1])
            spd = 3.6 if pose.get(aid)=="ride" else 1.5            # m/s：骑马快、步行慢
            wf = max(4, min(f_end-frame, int(d/spd*fps)))
            walks[aid] = (p, n, wf)
    if prevR is not None and frame>1:   # 上一镜末帧保持旧位 -> 剪切点保持（行走镜由上面接管）
        for aid in actors:
            _kloc(figs.get(aid), prevR[aid], frame-1); _kloc(figsF.get(aid), prevF[aid], frame-1)
            _kloc(horses.get(aid), prevH[aid], frame-1)
    for aid in actors:
        if aid in walks:
            p, n, wf = walks[aid]
            fig = figsF.get(aid); ride = pose.get(aid)=="ride"
            _kloc(fig, p, frame); _kloc(fig, n, frame+wf); _kloc(fig, n, f_end-1)
            _kloc(horses.get(aid), (p[0],p[1],0.0), frame)
            _kloc(horses.get(aid), (n[0],n[1],0.0), frame+wf); _kloc(horses.get(aid), (n[0],n[1],0.0), f_end-1)
            baseyaw = math.atan2(-(Tx-n[0]),(Td-n[1]))             # 到位后面向场景中心
            walkyaw = math.atan2(n[0]-p[0], n[1]-p[1])
            fig.rotation_euler=(0,0,walkyaw); fig.keyframe_insert("rotation_euler", frame=frame)
            fig.rotation_euler=(0,0,baseyaw); fig.keyframe_insert("rotation_euler", frame=frame+wf)
            if not ride and abs(n[2])<0.01 and abs(p[2])<0.01:     # 步态：起伏+摆臂（骑马不做）
                step=max(2, wf//4); k=0
                for f in range(frame, frame+wf, step):
                    u=(f-frame)/wf
                    _kloc(fig, (p[0]+(n[0]-p[0])*u, p[1]+(n[1]-p[1])*u, 0.035 if k%2==0 else 0.0), f)
                    for a in (armsF.get(aid) or {}).values():
                        a.rotation_euler=(0.5 if k%2==0 else -0.5, 0, 0)
                        a.keyframe_insert("rotation_euler", frame=f)
                    k+=1
                _kloc(fig, n, frame+wf)
                for a in (armsF.get(aid) or {}).values():
                    a.rotation_euler=(0,0,0); a.keyframe_insert("rotation_euler", frame=frame+wf)
        else:
            _kloc(figs.get(aid), R[aid], frame); _kloc(figsF.get(aid), F[aid], frame)
            _kloc(horses.get(aid), H[aid], frame)
    prevR, prevF, prevH = R, F, H
    # 运镜关键帧：推拉/环绕/移/升降/摇（起止两帧插值；无参数的镜保持静态相机）
    d=sh.get("dolly"); orb=sh.get("orbit"); tr=sh.get("truck"); cr=sh.get("crane"); pan=sh.get("pan")
    if any(v is not None for v in (d,orb,tr,cr,pan)):
        A=Vector(P(l0)); L=Vector(P(p0)); r=L-A
        r0,r1=r,r; a_off0=Vector((0,0,0)); a_off1=Vector((0,0,0))
        if d=="in": r0=r*1.28
        elif d=="out": r1=r*1.28
        if orb:
            half=math.radians(orb)*0.5; r0=_rot_xy(r0,-half); r1=_rot_xy(r1,half)
        if tr:
            perp=Vector((r.y,-r.x,0))
            perp=perp.normalized() if perp.length>1e-6 else Vector((1,0,0))
            r0=r0-perp*tr*0.5; r1=r1+perp*tr*0.5
        if cr: r0.z+=cr
        if pan:
            half=math.radians(pan)*0.5
            a_off0=_rot_xy(-r,-half); a_off1=_rot_xy(-r,half)
        _kpose(cam, A+r0, L+a_off0, frame)
        _kpose(cam, A+r1, L+a_off1, f_end-1)
    mk=sc.timeline_markers.new("S%d"%(i+1),frame=frame); mk.camera=cam
    frame=f_end
sc.camera=first
__SETTINGS__
'''

def previs_template():
    return HEADER + FIGS + r'''
scene = DATA.get("scene","generic")
fps = DATA.get("fps",24)
actors = DATA.get("actors",{})
shots = DATA["shots"]
ac = actors.get("c") or {}
path = ac.get("path") or [[0,0,0]]
def _vals(p):
    # [t,x,z] -> (x, 0高, z深)；[t,x,y,z] -> (x, y高, z深)
    if len(p) >= 4: return (p[1], p[2], p[3])
    return (p[1], 0.0, p[2])
def p_at(t):
    if len(path)==1: return _vals(path[0])
    for i in range(len(path)-1):
        p0,p1 = path[i],path[i+1]
        if p0[0] <= t <= p1[0]:
            u=(t-p0[0])/max(1e-9,(p1[0]-p0[0])); a,b=_vals(p0),_vals(p1)
            return tuple(a[k]+(b[k]-a[k])*u for k in range(3))
    return _vals(path[-1])
def c_xyz(t):
    return p_at(t)
def c_end():
    return _vals(path[-1])
av = actors.get("v"); ah = actors.get("h")
V = (av["pos"][0], 0.0, av["pos"][1]) if av else None
H = (ah["pos"][0], 0.0, ah["pos"][1]) if ah else None
ride_until = ac.get("rideUntil", 0)
# ---------- 场景 ----------
if scene == "street_stall":
    M_road=mat("road",(0.16,0.16,0.17),rough=0.95); M_side=mat("sidewalk",(0.32,0.31,0.29),rough=0.95)
    M_wood=mat("stallwood",(0.54,0.42,0.28),rough=0.8); M_leaf=mat("leaf",(0.20,0.42,0.16),rough=0.9)
    M_trunk=mat("trunk",(0.30,0.20,0.12),rough=0.9); M_melon=mat("melon",(0.29,0.49,0.23),rough=0.7)
    M_cloth=mat("awning",(0.85,0.82,0.72),rough=0.9); M_red=mat("redsign",(0.62,0.12,0.10),rough=0.6)
    box("ground",(0,2,-0.06),(22,24,0.06),M_side)
    box("road",(0,2,-0.04),(5.5,24,0.03),M_road)
    for zz in range(-18,8,4): box("lane",(0,zz,0.005),(0.08,0.9,0.01),M_cloth)
    # 瓜摊（摊主位）
    sx,sz = (V[0] if V else 3.6),(V[2] if V else 0.6)
    box("stall_top",(sx,sz,0.85),(0.95,1.6,0.06),M_wood)
    for lx in (-0.8,0.8):
        for lz in (-1.4,1.4): box("stall_leg",(sx+lx,sz+lz,0.42),(0.05,0.05,0.42),M_wood)
    box("awning_pole1",(sx-0.95,sz+1.5,1.6),(0.04,0.04,1.6),M_wood)
    box("awning_pole2",(sx+0.95,sz+1.5,1.6),(0.04,0.04,1.6),M_wood)
    box("awning",(sx,sz+1.45,2.25),(1.35,0.9,0.04),M_cloth,rot=(math.radians(-8),0,0))
    box("redsign",(sx+0.95,sz+1.0,1.5),(0.05,0.5,0.25),M_red)
    import random as _r; rnd=_r.Random(7)
    for i in range(10):
        sph("melon_%d"%i,(sx-0.6+rnd.random()*1.2,sz-1.2+rnd.random()*2.2,1.0),0.20,M_melon,scale=(1,1,0.9))
    # 树（林荫道）
    for tz in range(-16,6,4):
        cyl("trunk_%d"%tz,(-4.2,tz,1.4),0.12,2.8,M_trunk)
        sph("crown_%d"%tz,(-4.2,tz,3.2),1.3,M_leaf,scale=(1,1,0.8))
    box("facade_L",(-7.5,2,1.6),(2.5,22,1.6),mat("bldg",(0.42,0.40,0.37),rough=0.9))
    # 出租车
    box("car_body",(-5.2,-3.2,0.45),(0.85,1.7,0.35),mat("taxi",(0.66,0.24,0.24),rough=0.5))
    box("car_top",(-5.2,-3.4,0.95),(0.65,0.8,0.30),mat("taxi2",(0.75,0.72,0.70),rough=0.4))
    bpy.ops.object.light_add(type='SUN',location=(6,-4,10)); sun=bpy.context.object
    sun.data.energy=3.0; sun.data.color=(1.0,0.92,0.78)
    sun.rotation_euler=Euler((math.radians(50),math.radians(-15),math.radians(30)),'XYZ')
    alight("fill",(2,-6,3.5),200,6.0,(0.85,0.9,1.0),(2,1,0.8),size_y=3)
    world_bg=(0.45,0.58,0.72,1); world_str=0.8; look='AgX - Medium Low Contrast'
    eye_h = 1.57
elif scene == "city_aerial":
    M_road=mat("road",(0.23,0.24,0.25),rough=0.95); M_glass=mat("glass",(0.35,0.55,0.62),metallic=0.6,rough=0.3)
    M_conc=mat("concrete",(0.45,0.46,0.48),rough=0.9)
    box("ground",(0,-28,-0.06),(46,60,0.06),M_conc)
    box("road",(0,-28,0.02),(3.2,60,0.02),M_road)
    layout=[(-13,-70,6,30),(13,-66,7,36),(-14,-56,7,40),(14,-50,6,28),(-13,-38,6,34),(13,-32,7,44),
            (-15,-24,7,38),(15,-20,6,30),(-14,-6,7,42),(14,2,7,36),(-13,10,6,28),(15,16,7,40),(-15,24,6,34),(13,28,6,26)]
    for i,(bx,bz,bw,bh) in enumerate(layout):
        box("tower_%d"%i,(bx,bz,bh/2),(bw/2,5.0,bh/2),M_glass if i%2 else M_conc)
    bpy.ops.object.light_add(type='SUN',location=(6,-4,12)); sun=bpy.context.object
    sun.data.energy=2.6; sun.data.color=(1.0,0.94,0.85)
    sun.rotation_euler=Euler((math.radians(55),math.radians(-10),math.radians(20)),'XYZ')
    world_bg=(0.50,0.62,0.78,1); world_str=0.9; look='AgX - Medium Low Contrast'
    eye_h = 1.5
else:
    M_floor=mat("floor",(0.20,0.22,0.24),rough=0.85)
    box("ground",(0,2,-0.06),(12,14,0.06),M_floor)
    bpy.ops.object.light_add(type='SUN',location=(5,-4,8)); sun=bpy.context.object; sun.data.energy=2.5
    sun.rotation_euler=Euler((math.radians(50),0,math.radians(30)),'XYZ')
    world_bg=(0.50,0.56,0.64,1); world_str=0.7; look='AgX - Medium High Contrast'
    eye_h = 1.57
# ---------- 人物 ----------
figs={}
_ce=c_end()
if V:
    e=new_fig("actor_v",(V[0],V[2],0),math.atan2(-(_ce[0]-V[0]), _ce[2]-V[2]))
    fig_stand(e, av.get("shirt",[120,100,80]), av.get("apron")); figs["v"]=e
if H:
    e=new_fig("actor_h",(H[0],H[2],0),math.atan2(-(_ce[0]-H[0]), _ce[2]-H[2]))
    fig_stand(e, ah.get("shirt",[80,80,80])); figs["h"]=e
# 主角 c：path 走位关键帧
ce = new_fig("actor_c",(0,0,0),0); fig_stand(ce, ac.get("shirt",[40,60,90]), ac.get("apron")); figs["c"]=ce
for p in path:
    t=p[0]; x,yh,zd=_vals(p)
    fr=max(1,round(t*fps)+1)
    ce.location=(x,zd,yh)
    if V:
        ce.rotation_euler=(0,0,math.atan2(-(V[0]-x), V[2]-zd))
    else:
        a=p_at(max(0,t-0.5)); b=p_at(t+0.5)
        ce.rotation_euler=(0,0,math.atan2(-(b[0]-a[0]), b[2]-a[2]))
    ce.keyframe_insert("location",frame=fr); ce.keyframe_insert("rotation_euler",frame=fr)
# 踏板摩托：骑乘时随主角，rideUntil 后停到 scooterPark
scot = scooter(ce, "ride_scooter")
park = ac.get("scooterPark")
scot_park = None
if park:
    pe=new_fig("parked_scooter",(park[0],park[1],0),0); scot_park=scooter(pe,"park_scooter")
ride_frame = max(1, round(ride_until*fps)+1)
for obj, vis_before in ((scot,True),(scot_park,False)):
    if obj is None: continue
    members=[obj]+list(obj.children_recursive)
    for f,vis in ((1,vis_before),(ride_frame,not vis_before)):
        for m in members:
            m.hide_render=not vis; m.hide_viewport=not vis
            m.keyframe_insert("hide_render",frame=f); m.keyframe_insert("hide_viewport",frame=f)
# ---------- 相机 ----------
def cam_shot(i, sh):
    bpy.ops.object.camera_add()
    cam=bpy.context.object; cam.name="CAM_%02d"%(i+1)
    cam.data.angle=math.radians(sh.get("fov",42))
    f0=1+sum(round(float(s.get("dur",2))*fps) for s in shots[:i])
    f1=f0+max(1,round(float(sh.get("dur",2))*fps))
    mode=sh.get("mode","follow")
    def t_of(f): return (f-1)/fps
    def place(f):
        t=t_of(f); C=c_xyz(t)
        Cc=(C[0],C[1]+0.95,C[2])
        if mode=="follow":
            off=sh.get("off",[0,2,5]); cp=(Cc[0]+off[0],Cc[2]+off[2],Cc[1]+off[1]); tgt=(Cc[0],Cc[2],Cc[1])
        elif mode=="wide":
            off=sh.get("off",[0,2,5]); cp=(Cc[0]+off[0],Cc[2]+off[2],Cc[1]+off[1])
            tgt=((Cc[0]+(V[0] if V else Cc[0]))/2, (Cc[2]+(V[2] if V else Cc[2]))/2, 1.0)
        elif mode=="cu":
            foc=sh.get("focus","c")
            F = Cc if foc=="c" else (V[0],0.95,V[2])
            O = (V[0],0.95,V[2]) if foc=="c" else Cc
            A=(F[0],F[2],F[1]+0.62); B=(O[0],O[2],O[1]+0.62)
            dx,dz=B[0]-A[0],B[1]-A[1]; L=math.hypot(dx,dz) or 1
            ddx,ddz=dx/L,dz/L; px,pz=-ddz,ddx
            dist=min(sh.get("dist",1.8), L*0.72)
            cp=(A[0]+ddx*dist+px*0.30, A[1]+ddz*dist+pz*0.30, A[2])
            tgt=A
        elif mode=="ots":
            host=sh.get("host","c")
            A = Cc if host=="c" else (V[0],0.95,V[2])
            B = (V[0],0.95,V[2]) if host=="c" else Cc
            A=(A[0],A[2],A[1]+0.62); B=(B[0],B[2],B[1]+0.62)
            dx,dz=B[0]-A[0],B[1]-A[1]; L=math.hypot(dx,dz) or 1
            ddx,ddz=dx/L,dz/L; px,pz=-ddz*0.5,ddx*0.5
            cp=(A[0]-ddx*0.95+px, A[1]-ddz*0.95+pz, A[2])
            tgt=B
        elif mode=="pov":
            cp=(C[0],C[2]-3.2,C[1]+1.2); tgt=(C[0],C[2]+42,C[1])
        elif mode=="streetup":
            cp=(C[0]+4,C[2]+15,2.0); tgt=(C[0],C[2],C[1]+1.0)
        else:
            off=sh.get("off",[0,2,5]); cp=(Cc[0]+off[0],Cc[2]+off[2],Cc[1]+off[1]); tgt=(Cc[0],Cc[2],Cc[1])
        cam.location=cp
        cam.rotation_euler=(Vector(tgt)-Vector(cp)).to_track_quat('-Z','Y').to_euler()
    static = mode in ("cu","ots")
    if static:
        f_mid = f0 + (f1-f0)//2
        bpy.context.scene.frame_set(f_mid); place(f_mid)
    else:
        for f in range(f0, f1+1, 2):
            bpy.context.scene.frame_set(f); place(f)
            cam.keyframe_insert("location",frame=f); cam.keyframe_insert("rotation_euler",frame=f)
    return cam, f0
sc=bpy.context.scene
for m in list(sc.timeline_markers): sc.timeline_markers.remove(m)
first=None
for i,sh in enumerate(shots):
    cam,f0=cam_shot(i,sh)
    if first is None: first=cam
    mk=sc.timeline_markers.new("S%d"%(i+1),frame=f0); mk.camera=cam
sc.camera=first
total = sum(round(float(s.get("dur",2))*fps) for s in shots)
sc.frame_start=1; sc.frame_end=max(2,total); sc.render.fps=fps
w=int(DATA.get("w",960)); h=int(DATA.get("h",540))
sc.render.resolution_x=w-(w%2); sc.render.resolution_y=h-(h%2); sc.render.resolution_percentage=100
sc.render.engine='BLENDER_EEVEE'
try: sc.eevee.taa_render_samples=16
except Exception: pass
world=bpy.data.worlds[0] if bpy.data.worlds else bpy.data.worlds.new("W")
sc.world=world; world.use_nodes=True
bg=next(n for n in world.node_tree.nodes if n.type=="BACKGROUND")
bg.inputs[0].default_value=world_bg; bg.inputs[1].default_value=world_str
try: sc.view_settings.look=look
except Exception: pass
try: sc.render.use_motion_blur=True; sc.render.motion_blur_shutter=0.5
except Exception: pass
os.makedirs(r"__OUTDIR__",exist_ok=True)
sc.render.filepath=os.path.join(r"__OUTDIR__","f_"); sc.render.image_settings.file_format='PNG'
try: bpy.ops.wm.save_as_mainfile(filepath=r"__BLENDPATH__")
except Exception as ex: print("save skipped:",ex)
print("GEN_BUILD_OK objects:",len(bpy.data.objects),"shots:",len(shots),"frames:",sc.frame_end)
'''

SETTINGS = r'''
sc.frame_start=1; sc.frame_end=max(2,frame-1); sc.render.fps=DATA.get("fps",24)
w=int(DATA.get("w",960)); h=int(DATA.get("h",540))
sc.render.resolution_x=w-(w%2); sc.render.resolution_y=h-(h%2); sc.render.resolution_percentage=100
sc.render.engine='BLENDER_EEVEE'
try: sc.eevee.taa_render_samples=16
except Exception: pass
world=bpy.data.worlds[0] if bpy.data.worlds else bpy.data.worlds.new("W")
sc.world=world; world.use_nodes=True
bg=next(n for n in world.node_tree.nodes if n.type=="BACKGROUND")
bg.inputs[0].default_value=(0.42,0.50,0.62,1) if stype=="tatami" else (0.05,0.07,0.10,1)
bg.inputs[1].default_value=0.6 if stype=="tatami" else 0.35
try: sc.view_settings.look='AgX - Medium Low Contrast' if stype=="tatami" else 'AgX - Medium High Contrast'
except Exception: pass
os.makedirs(r"__OUTDIR__",exist_ok=True)
sc.render.filepath=os.path.join(r"__OUTDIR__","f_"); sc.render.image_settings.file_format='PNG'
try: bpy.ops.wm.save_as_mainfile(filepath=r"__BLENDPATH__")
except Exception as ex: print("save skipped:",ex)
print("GEN_BUILD_OK objects:",len(bpy.data.objects),"shots:",len(DATA["shots"]),"frames:",sc.frame_end)
'''

def main():
    if len(sys.argv)<2:
        print("usage: blender_previs.py <storyboard.json>"); sys.exit(1)
    jp=os.path.abspath(sys.argv[1]); data=json.load(open(jp,encoding="utf-8"))
    shots=data.get("shots") or []
    is_dialogue = all(("pos" in s and "look" in s) for s in shots) and len(shots)>0
    parts=jp.replace("\\","/").split("/")
    outdir=None
    if "projects" in parts:
        pi=parts.index("projects")
        if pi+1<len(parts):
            outdir=os.path.join(os.path.dirname(jp).split("projects")[0],"projects",parts[pi+1],"白模3D")
    if not outdir: outdir=os.path.join(os.path.dirname(jp),"白模3D")
    os.makedirs(outdir,exist_ok=True)
    slug=re.sub(r"[^\w\u4e00-\u9fff-]+","_",str(data.get("project","proj"))).strip("_") or "proj"
    script_path=os.path.join(outdir,"gen_%s.py"%slug)
    blend_path=os.path.join(outdir,"gen_%s.blend"%slug)
    render_dir=os.path.join(outdir,"render_gen")
    if is_dialogue:
        actors={}
        for aid,ac in (data.get("actors") or {}).items():
            pos=ac.get("pos")
            if not pos:
                pp=ac.get("path") or []
                if pp: pos=[sum(p[1] for p in pp)/len(pp), sum(p[2] for p in pp)/len(pp)]
                else: pos=[0,2]
            actors[aid]={"name":ac.get("name",aid),"pos":[float(pos[0]),float(pos[1])],
                         "shirt":ac.get("shirt",[200,200,200]),"style":ac.get("style","seated")}
        # shots 整体透传：模板运行时需要 scene/pose/staging/运镜参数（dolly/orbit/...）；
        minimal={"project":data.get("project","proj"),"fps":data.get("fps",24),"w":data.get("w",960),
                 "h":data.get("h",540),"set":data.get("set",{"type":"generic"}),"actors":actors,
                 "env":data.get("env") or {},
                 "shots":[dict(s) for s in shots]}
        code=dialogue_template().replace("__SETTINGS__",SETTINGS)
    else:
        keep=[]
        for s in shots:
            if s.get("mode") in ("follow","wide","cu","ots","pov","streetup","keys") or "off" in s:
                keep.append(s)
        if not keep:
            print("ERR: 无法识别的镜头模式（需 pos/look 或 mode）"); sys.exit(2)
        minimal={"project":data.get("project","proj"),"fps":data.get("fps",24),"w":data.get("w",960),
                 "h":data.get("h",540),"scene":data.get("scene","generic"),
                 "actors":data.get("actors",{}),"shots":keep}
        code=previs_template()
    code=code.replace("__DATA__",repr(minimal))
    code=code.replace("__BLENDPATH__",blend_path.replace("\\","\\\\"))
    code=code.replace('r"__OUTDIR__"',repr(render_dir))
    open(script_path,"w",encoding="utf-8").write(code)
    print("GEN_SCRIPT:"+script_path)

if __name__=="__main__":
    main()
