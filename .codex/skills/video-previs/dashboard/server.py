# -*- coding: utf-8 -*-
"""视频拉片·白模工作台 后端：静态文件 + 项目扫描 + 跑脚本（无AI，本地）。
用法: python server.py [--port 8770]   然后浏览器打开 http://localhost:8770
"""
import os,json,subprocess,sys,re,urllib.parse,html,threading,shutil,socket,glob,time
from http.server import HTTPServer,BaseHTTPRequestHandler
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
ROOT=os.path.dirname(os.path.abspath(__file__))
# projects/ 与脚本目录：向上查找（dashboard 可在 Skill 内或独立 workbench 内）
def _find_projects():
    d=ROOT
    for _ in range(6):
        cand=os.path.join(d,"projects")
        if os.path.isdir(cand): return d
        nd=os.path.dirname(d)
        if nd==d: break
        d=nd
    return os.path.abspath(os.path.join(ROOT,"..","..","..",".."))
VIDEO=_find_projects()
# 脚本查找顺序：本 Skill 的 scripts（dashboard 在 <skill>/dashboard 时为 ../scripts）→ 工作区 previs_system → 旧 .codex 路径
SKILL=os.path.abspath(os.path.join(ROOT,"..","scripts"))
SYS=os.path.join(VIDEO,"previs_system")
# 兼容旧 workbench 布局（server 在 workbench/ 时 SKILL 不存在，回退到工作区 skill）
if not os.path.isdir(SKILL):
    alt=os.path.join(VIDEO,".codex","skills","video-previs","scripts")
    if os.path.isdir(alt): SKILL=alt
def tool(name):
    for d in [SKILL,os.path.join(SYS,"tools"),os.path.join(SYS,"engine")]:
        f=os.path.join(d,name)
        if os.path.exists(f): return f
    return None
DL=os.path.join(os.path.expanduser("~"),"Downloads")
LLMCFG=os.path.join(ROOT,"llm_config.json")
def load_llm_cfg():
    """启动/保存配置时把 llm_config.json 应用进环境变量（子进程脚本继承）。"""
    try: c=json.load(open(LLMCFG,encoding="utf-8"))
    except Exception: return
    if c.get("api_key"): os.environ["ZHIPUAI_API_KEY"]=c["api_key"]
    else:
        os.environ.pop("ZHIPUAI_API_KEY",None); os.environ.pop("GLM_API_KEY",None)
    for k,e in [("text_model","GLM_TEXT_MODEL"),("vision_model","GLM_VISION_MODEL"),("base","GLM_BASE")]:
        if c.get(k): os.environ[e]=c[k]
def under(root,p):
    rr=os.path.realpath(root); rp=os.path.realpath(p)
    return rp==rr or rp.startswith(rr+os.sep)
def safe_dl(name):
    p=os.path.normpath(os.path.join(DL,name))
    return p if under(DL,p) and os.path.isfile(p) else None
def safe_video(name):
    p=os.path.normpath(os.path.join(VIDEO,name))
    return p if under(VIDEO,p) and os.path.isfile(p) else None
def scan_sources():
    out=[]
    if os.path.isdir(DL):
        for f in sorted(os.listdir(DL)):
            if f.lower().endswith((".mp4",".mkv",".mov")):
                try: sz=round(os.path.getsize(os.path.join(DL,f))/1048576,1)
                except Exception: sz=0
                out.append({"name":f,"mb":sz})
    return out
MEDIA_EXT=(".mp4",".mov",".mkv",".png",".jpg",".jpeg",".blend",".py",".md",".srt",".txt",".json",".xlsx",".wav",".m4a",".mp3",".aac")
def scan_projects():
    pj=os.path.join(VIDEO,"projects"); out=[]
    if not os.path.isdir(pj): return out
    for name in sorted(os.listdir(pj)):
        d=os.path.join(pj,name)
        if not os.path.isdir(d): continue
        arts={"拉片":[],"分镜":[],"白模":[],"帧":[],"素材":[],"成片":[],
              "三维探索":[],"白模3D":[],"根目录":[]}
        for sub in ["拉片","分镜","白模","帧","成片","素材","三维探索","白模3D"]:
            sd=os.path.join(d,sub)
            if not os.path.isdir(sd): continue
            for f in sorted(os.listdir(sd)):
                fp=os.path.join(sd,f)
                if os.path.isdir(fp):  # 嵌套一层（如 render/ 帧序列、frames_x/）
                    gs=[g for g in sorted(os.listdir(fp)) if os.path.isfile(os.path.join(fp,g))]
                    if len(gs)>20:
                        arts.setdefault(sub,[]).append("[帧序列] %s/（共%d个文件，已折叠）"%(f,len(gs)))
                    else:
                        for g in gs: arts.setdefault(sub,[]).append(f+"/"+g)
                else:
                    arts.setdefault(sub,[]).append(f)
        # 项目根目录散放的产物（如 07_监狱白模3D 的 .blend/.mp4/build_*.py）
        for f in sorted(os.listdir(d)):
            if os.path.isfile(os.path.join(d,f)) and f.lower().endswith(MEDIA_EXT):
                arts["根目录"].append(f)
        out.append({"name":name,"dirs":arts})
    return out

def find_blender():
    cands=[r"J:\Blender 5.2\blender.exe",r"J:\Blender 5.1\blender.exe",r"J:\Blender 5.0\blender.exe"]
    cands+=glob.glob(r"C:\Program Files\Blender Foundation\Blender *\blender.exe")
    cands+=glob.glob(r"J:\Blender*\blender.exe")
    for c in cands:
        if os.path.isfile(c): return c
    return None
class H(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    JOBS={}; JOBSEQ=[0]; JLOCK=threading.Lock()
    def spawn_job(self,step,cmd):
        with self.JLOCK:
            self.JOBSEQ[0]+=1; jid=self.JOBSEQ[0]
            self.JOBS[jid]={"id":jid,"step":step,"status":"running","out":"","err":"","cmd":[os.path.basename(c) for c in cmd]}
        def work():
            try:
                r=subprocess.run(cmd,capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=3600)
                st,so,se=r.returncode==0,r.stdout,r.stderr
            except Exception as e:
                st,so,se=False,"",str(e)
            with self.JLOCK:
                self.JOBS[jid].update(status="done" if st else "failed",ok=st,out=so[-8000:],err=se[-4000:])
        threading.Thread(target=work,daemon=True).start()
        return jid
    def _send(self,code,ct,body):
        self.send_response(code); self.send_header("Content-Type",ct); self.send_header("Content-Length",str(len(body)))
        self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        u=urllib.parse.urlparse(self.path); q=urllib.parse.parse_qs(u.query)
        if u.path=="/" or u.path=="/index.html":
            p=os.path.join(ROOT,"index.html")
            self.send_response(200); self.send_header("Content-Type","text/html; charset=utf-8")
            self.send_header("Cache-Control","no-store, no-cache, must-revalidate"); self.send_header("Pragma","no-cache")
            body=open(p,"rb").read(); self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body); return
        if u.path=="/api/sources":
            return self._send(200,"application/json; charset=utf-8",json.dumps(scan_sources(),ensure_ascii=False).encode())
        if u.path=="/src":
            p=safe_dl(q.get("p",[""])[0])
            if p:
                size=os.path.getsize(p); rng=self.headers.get("Range")
                if rng:
                    import re as _re
                    m=_re.search(r"bytes=(\d+)-(\d*)",rng)
                    st=int(m.group(1)); en=int(m.group(2)) if m.group(2) else size-1
                    en=min(en,size-1)
                    self.send_response(206); self.send_header("Content-Type","video/mp4")
                    self.send_header("Content-Range",f"bytes {st}-{en}/{size}")
                    self.send_header("Accept-Ranges","bytes"); self.send_header("Content-Length",str(en-st+1)); self.end_headers()
                    with open(p,"rb") as f:
                        f.seek(st); left=en-st+1
                        while left>0:
                            b=f.read(min(1<<18,left)); 
                            if not b: break
                            self.wfile.write(b); left-=len(b)
                else:
                    self.send_response(200); self.send_header("Content-Type","video/mp4")
                    self.send_header("Accept-Ranges","bytes"); self.send_header("Content-Length",str(size)); self.end_headers()
                    with open(p,"rb") as f:
                        while True:
                            b=f.read(1<<18)
                            if not b: break
                            self.wfile.write(b)
                return
            return self._send(404,"text/plain",b"no")
        if u.path=="/api/projects":
            return self._send(200,"application/json; charset=utf-8",json.dumps(scan_projects(),ensure_ascii=False).encode())
        if u.path=="/api/llmstatus":
            ok=bool(os.environ.get("ZHIPUAI_API_KEY") or os.environ.get("GLM_API_KEY"))
            return self._send(200,"application/json; charset=utf-8",json.dumps({"configured":ok}).encode())
        if u.path=="/api/llmconfig":
            def mask(k): return (k[:4]+"****"+k[-4:]) if k and len(k)>8 else ("已配置" if k else "")
            c={"api_key":os.environ.get("ZHIPUAI_API_KEY") or os.environ.get("GLM_API_KEY") or "",
               "text_model":os.environ.get("GLM_TEXT_MODEL","glm-5.3-flash"),
               "vision_model":os.environ.get("GLM_VISION_MODEL","glm-4.5v"),
               "base":os.environ.get("GLM_BASE","https://open.bigmodel.cn/api/paas/v4")}
            c["api_key_masked"]=mask(c["api_key"]); c["api_key"]=""
            return self._send(200,"application/json; charset=utf-8",json.dumps(c,ensure_ascii=False).encode())
        if u.path=="/api/job":
            jid=int(q.get("id",["0"])[0])
            with self.JLOCK: j=self.JOBS.get(jid)
            return self._send(200,"application/json; charset=utf-8",json.dumps(j or {"err":"无此任务"},ensure_ascii=False).encode())
        if u.path=="/api/import_src":
            body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
            proj=(body.get("project") or "").replace("/","").replace("\\","")
            fn=os.path.basename(body.get("name") or "")
            src=safe_dl(fn)
            if not proj or not src:
                return self._send(400,"application/json",json.dumps({"ok":False,"err":"项目或源文件不合法"}).encode())
            d=os.path.join(VIDEO,"projects",proj,"素材"); os.makedirs(d,exist_ok=True)
            dst=os.path.join(d,fn)
            if os.path.exists(dst): return self._send(409,"application/json",json.dumps({"ok":False,"err":"同名文件已存在"}).encode())
            try: shutil.copyfile(src,dst)
            except Exception as e: return self._send(500,"application/json",json.dumps({"ok":False,"err":str(e)}).encode())
            return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"path":"projects/"+proj+"/素材/"+fn},ensure_ascii=False).encode())
        if u.path=="/api/openblend":
            body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
            p=os.path.normpath(os.path.join(VIDEO,body.get("p","")))
            if not under(VIDEO,p) or not os.path.isfile(p) or not p.lower().endswith(".blend"):
                return self._send(400,"application/json",json.dumps({"ok":False,"err":"blend 文件不合法"}).encode())
            try:
                os.startfile(p)
                return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"opened":os.path.basename(p)},ensure_ascii=False).encode())
            except Exception as e:
                return self._send(500,"application/json",json.dumps({"ok":False,"err":str(e)}).encode())
        if u.path=="/api/file":
            # 读文本文件
            rel=q.get("p",[""])[0]; p=safe_video(rel)
            if p:
                try: return self._send(200,"text/plain; charset=utf-8",open(p,encoding="utf-8",errors="replace").read().encode())
                except Exception as e: return self._send(200,"text/plain; charset=utf-8",str(e).encode())
            return self._send(404,"text/plain",b"not found")
        if u.path=="/media":
            rel=q.get("p",[""])[0]; pth=safe_video(rel)
            if pth:
                size=os.path.getsize(pth); rng=self.headers.get("Range"); ct={".mp4":"video/mp4",".mp3":"audio/mpeg",".jpg":"image/jpeg",".png":"image/png",".mkv":"video/x-matroska",".mov":"video/quicktime",".wav":"audio/wav",".m4a":"audio/x-m4a",".mp3":"audio/mpeg",".aac":"audio/aac",".ogg":"audio/ogg",".blend":"application/octet-stream",".py":"text/plain; charset=utf-8",".xlsx":"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}.get(os.path.splitext(pth)[1].lower(),"application/octet-stream")
                fh=open(pth,"rb")
                if rng and "bytes=" in rng:
                    mm=re.search(r"bytes=(\d+)-(\d*)",rng); st=int(mm.group(1)); en=int(mm.group(2)) if mm.group(2) else size-1; en=min(en,size-1)
                    self.send_response(206); self.send_header("Content-Type",ct); self.send_header("Accept-Ranges","bytes")
                    self.send_header("Content-Range","bytes %d-%d/%d"%(st,en,size)); self.send_header("Content-Length",str(en-st+1)); self.end_headers()
                    fh.seek(st); left=en-st+1
                    while left>0:
                        b=fh.read(min(1<<18,left))
                        if not b: break
                        self.wfile.write(b); left-=len(b)
                else:
                    self.send_response(200); self.send_header("Content-Type",ct); self.send_header("Accept-Ranges","bytes"); self.send_header("Content-Length",str(size)); self.end_headers()
                    while True:
                        b=fh.read(1<<18)
                        if not b: break
                        self.wfile.write(b)
                fh.close(); return
            self._send(404,"text/plain",b"not found")
        # 其余静态
        p=os.path.normpath(os.path.join(ROOT,u.path.lstrip("/")))
        if under(ROOT,p) and os.path.isfile(p):
            return self._send(200,"application/octet-stream",open(p,"rb").read())
        self._send(404,"text/plain",b"404")
    def do_POST(self):
        u=urllib.parse.urlparse(self.path); q=urllib.parse.parse_qs(u.query); ln=int(self.headers.get("Content-Length",0))
        if u.path=="/api/import":
            proj=q.get("project",[""])[0].replace("/","").replace("\\","")
            fn=os.path.basename(q.get("name",[""])[0])
            if not proj or not fn.lower().endswith((".mp4",".mkv",".mov")):
                return self._send(400,"application/json",json.dumps({"ok":False,"err":"项目名或文件名不合法"}).encode())
            d=os.path.join(VIDEO,"projects",proj,"素材"); os.makedirs(d,exist_ok=True)
            dst=os.path.join(d,fn)
            if os.path.exists(dst): return self._send(409,"application/json",json.dumps({"ok":False,"err":"同名文件已存在:"+fn}).encode())
            with open(dst,"wb") as f:
                left=ln
                while left>0:
                    b=self.rfile.read(min(1<<20,left))
                    if not b: break
                    f.write(b); left-=len(b)
            return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"path":"projects/"+proj+"/素材/"+fn},ensure_ascii=False).encode())
        if u.path=="/api/job":
            jid=int(q.get("id",["0"])[0])
            with self.JLOCK: j=self.JOBS.get(jid)
            return self._send(200,"application/json; charset=utf-8",json.dumps(j or {"err":"无此任务"},ensure_ascii=False).encode())
        if u.path=="/api/llmconfig":
            body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
            old={}
            try: old=json.load(open(LLMCFG,encoding="utf-8"))
            except Exception: pass
            new={"api_key":"" if body.get("clear_key") else ((body.get("api_key") or "").strip() or old.get("api_key","")),
                 "text_model":(body.get("text_model") or "").strip() or old.get("text_model","glm-5.3-flash"),
                 "vision_model":(body.get("vision_model") or "").strip() or old.get("vision_model","glm-4.5v"),
                 "base":(body.get("base") or "").strip() or old.get("base","https://open.bigmodel.cn/api/paas/v4")}
            json.dump(new,open(LLMCFG,"w",encoding="utf-8"),ensure_ascii=False,indent=1)
            load_llm_cfg()
            ok=bool(os.environ.get("ZHIPUAI_API_KEY") or os.environ.get("GLM_API_KEY"))
            return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"configured":ok},ensure_ascii=False).encode())
        if u.path=="/api/run":
            body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
            step=body.get("step"); raw=body.get("args",[])
            mp={"shots":"extract_shots.py","subtitles":"extract_subtitles.py","speech":"extract_speech.py",
                "plan":"plan_coverage.py","render":"dialogue_engine.py","xlsx":"export_storyboard_xlsx.py","dialogue":"export_dialogue.py","explain":"explain_storyboard.py",
                "lapdoc":"lapian_docs.py","docs":"export_docs.py",
                "fill":"fill_descriptions.py","draft":"draft_storyboard.py","explain_ai":"explain_storyboard.py"}
            if step=="blender_previs":
                jsp=os.path.normpath(os.path.join(VIDEO,raw[0] if raw else ""))
                if not under(VIDEO,jsp) or not os.path.isfile(jsp):
                    return self._send(400,"application/json",json.dumps({"ok":False,"err":"分镜 JSON 不存在"},ensure_ascii=False).encode())
                gen=tool("blender_previs.py")
                if not gen:
                    return self._send(500,"application/json",json.dumps({"ok":False,"err":"blender_previs.py 工具缺失"},ensure_ascii=False).encode())
                try:
                    _env=dict(os.environ); _env["PYTHONIOENCODING"]="utf-8"
                    r=subprocess.run([sys.executable,gen,jsp],capture_output=True,text=True,encoding="utf-8",errors="replace",timeout=120,env=_env)
                except Exception as e:
                    return self._send(500,"application/json",json.dumps({"ok":False,"err":"生成器异常:"+str(e)},ensure_ascii=False).encode())
                if r.returncode!=0:
                    return self._send(500,"application/json",json.dumps({"ok":False,"err":"生成失败:"+(r.stderr or r.stdout or "")[-800:]},ensure_ascii=False).encode())
                line=[l for l in (r.stdout or "").splitlines() if l.startswith("GEN_SCRIPT:")]
                if not line:
                    return self._send(500,"application/json",json.dumps({"ok":False,"err":"未拿到生成脚本路径"},ensure_ascii=False).encode())
                sp=line[0].split(":",1)[1].strip()
                code=open(sp,encoding="utf-8",errors="replace").read()
                with self.JLOCK:
                    self.JOBSEQ[0]+=1; jid=self.JOBSEQ[0]
                    self.JOBS[jid]={"id":jid,"step":step,"status":"running","out":"已生成: "+sp+"\n发送到 Blender MCP…","err":"","cmd":["blender-mcp:9876",os.path.basename(sp)]}
                def _mcp2():
                    try:
                        sk=socket.create_connection(("127.0.0.1",9876),timeout=10); sk.settimeout(300)
                        sk.sendall(json.dumps({"type":"execute_code","params":{"code":code}}).encode("utf-8"))
                        buf=b""
                        while True:
                            try: ch=sk.recv(65536)
                            except socket.timeout: break
                            if not ch: break
                            buf+=ch
                            try: json.loads(buf.decode("utf-8","replace")); break
                            except json.JSONDecodeError: continue
                        sk.close()
                        resp=buf.decode("utf-8","replace")
                        ok='"status": "success"' in resp or '"status":"success"' in resp
                        with self.JLOCK:
                            self.JOBS[jid].update(status="done" if ok else "failed",ok=ok,
                                out=("已生成脚本: "+sp+"\n"+resp)[-8000:])
                    except Exception as e:
                        with self.JLOCK:
                            self.JOBS[jid].update(status="failed",ok=False,
                                err="MCP 发送失败（脚本已生成，可在 ⑥ 页签点「发送构建脚本」重试；Blender 是否开着 9876？）: "+str(e))
                threading.Thread(target=_mcp2,daemon=True).start()
                return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"id":jid},ensure_ascii=False).encode())
            if step=="openblend":
                p=os.path.normpath(os.path.join(VIDEO,raw[0] if raw else ""))
                if not under(VIDEO,p) or not os.path.isfile(p):
                    return self._send(400,"application/json",json.dumps({"ok":False,"err":"文件不存在"}).encode())
                try:
                    os.startfile(p)
                    return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"opened":os.path.basename(p)},ensure_ascii=False).encode())
                except Exception as e:
                    return self._send(500,"application/json",json.dumps({"ok":False,"err":str(e)}).encode())
            if step=="blender_render":
                blend=os.path.normpath(os.path.join(VIDEO,raw[0] if raw else ""))
                if not under(VIDEO,blend) or not os.path.isfile(blend):
                    return self._send(400,"application/json",json.dumps({"ok":False,"err":"blend 文件不存在"}).encode())
                exe=find_blender()
                if not exe:
                    return self._send(500,"application/json",json.dumps({"ok":False,"err":"未找到 blender.exe"}).encode())
                jid=self.spawn_job(step,[exe,"-b",blend,"-a"])
                return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"id":jid},ensure_ascii=False).encode())
            if step=="blender_build":
                sp=os.path.normpath(os.path.join(VIDEO,raw[0] if raw else ""))
                if not under(VIDEO,sp) or not os.path.isfile(sp):
                    return self._send(400,"application/json",json.dumps({"ok":False,"err":"构建脚本不存在"}).encode())
                code=open(sp,encoding="utf-8",errors="replace").read()
                with self.JLOCK:
                    self.JOBSEQ[0]+=1; jid=self.JOBSEQ[0]
                    self.JOBS[jid]={"id":jid,"step":step,"status":"running","out":"","err":"","cmd":["blender-mcp:9876",os.path.basename(sp)]}
                def _mcp():
                    try:
                        sk=socket.create_connection(("127.0.0.1",9876),timeout=10); sk.settimeout(300)
                        sk.sendall(json.dumps({"type":"execute_code","params":{"code":code}}).encode("utf-8"))
                        buf=b""
                        while True:
                            try: ch=sk.recv(65536)
                            except socket.timeout: break
                            if not ch: break
                            buf+=ch
                            try: json.loads(buf.decode("utf-8","replace")); break
                            except json.JSONDecodeError: continue
                        sk.close()
                        resp=buf.decode("utf-8","replace")
                        ok='"status": "success"' in resp or '"status":"success"' in resp
                        with self.JLOCK:
                            self.JOBS[jid].update(status="done" if ok else "failed",ok=ok,out=resp[-8000:])
                    except Exception as e:
                        with self.JLOCK:
                            self.JOBS[jid].update(status="failed",ok=False,err="MCP 连接失败（Blender 是否开着 9876？）: "+str(e))
                threading.Thread(target=_mcp,daemon=True).start()
                return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"id":jid},ensure_ascii=False).encode())
            if step=="h264":
                src=os.path.normpath(os.path.join(VIDEO,raw[0]))
                if not under(VIDEO,src) or not os.path.isfile(src):
                    return self._send(400,"application/json",json.dumps({"ok":False,"err":"文件不存在"}).encode())
                dst=src.rsplit(".",1)[0]+"_H264.mp4"
                jid=self.spawn_job(step,["ffmpeg","-y","-i",src,"-c:v","libx264","-pix_fmt","yuv420p","-movflags","+faststart",dst])
                return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"id":jid},ensure_ascii=False).encode())
            scr=mp.get(step)
            if not scr or not tool(scr): return self._send(500,"application/json",json.dumps({"ok":False,"err":"工具缺失:"+str(scr)}).encode())
            args=[]
            for a in raw:
                a=str(a)
                if a.startswith("projects/"):
                    p=os.path.normpath(os.path.join(VIDEO,a))
                    if not under(VIDEO,p) or not (os.path.exists(p) or os.path.isdir(os.path.dirname(p))):
                        return self._send(400,"application/json",json.dumps({"ok":False,"err":"路径不存在:"+a}).encode())
                    args.append(p)
                else: args.append(a)
            jid=self.spawn_job(step,[sys.executable,tool(scr)]+args)
            return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"id":jid},ensure_ascii=False).encode())
        else: self._send(404,"text/plain",b"404")
if __name__=="__main__":
    load_llm_cfg()
    port=int(sys.argv[1]) if len(sys.argv)>1 and sys.argv[1].isdigit() else 8775
    print(f"工作台: http://localhost:{port}")
    HTTPServer(("127.0.0.1",port),H).serve_forever()

