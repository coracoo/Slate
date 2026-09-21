# -*- coding: utf-8 -*-
"""视频拉片·白模工作台 后端：静态文件 + 项目扫描 + 跑脚本（无AI，本地）。
用法: python server.py [--port 8770]   然后浏览器打开 http://localhost:8770
"""
import os,json,copy,subprocess,sys,re,urllib.parse,urllib.request,html,threading,shutil,socket,glob,time,importlib.util,hashlib,base64
# 必须在加载业务模块之前拒绝旧解释器，避免页面可用而延迟导入失败。
if sys.version_info[:2] != (3, 12):
    raise SystemExit("Slate 需要 Python 3.12.x；请用 uv venv --python 3.12 --seed .venv 重建环境。")
from email.parser import BytesParser
from email.policy import default as email_default_policy
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class RequestContext:
    """只读路由信息；请求体留给处理函数按 JSON/二进制/multipart 解析。"""
    url: object
    query: object
    content_length: int = 0


def route(method, *paths):
    """声明精确路由，注册表构建时检查重复，不在装饰时修改全局表。"""
    if method not in ('GET', 'POST', 'HEAD') or not paths or any(not p.startswith('/') for p in paths):
        raise ValueError('无效路由声明')
    def decorate(handler):
        handler.route_keys = (*getattr(handler, 'route_keys', ()), *((method, p) for p in paths))
        return handler
    return decorate


def build_route_table(handler_class):
    table = {}
    for handler in vars(handler_class).values():
        for key in getattr(handler, 'route_keys', ()):
            if key in table:
                raise ValueError(f'重复路由：{key[0]} {key[1]}')
            table[key] = handler
    return MappingProxyType(table)


class ResponseAlreadyStarted(RuntimeError):
    """同一请求不允许发送第二个 HTTP 响应。"""

try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
ROOT=os.path.dirname(os.path.abspath(__file__))
TOOLS=os.path.join(ROOT,"tools")
if TOOLS not in sys.path: sys.path.insert(0, TOOLS)
VIDEO=os.path.abspath(os.path.join(ROOT,".."))
SKILL=os.path.join(VIDEO,".codex","skills","video-previs","scripts")
SYS=os.path.join(VIDEO,"previs_system")
CORE_TOOLS=os.path.join(SYS,"tools")
if CORE_TOOLS not in sys.path: sys.path.insert(0, CORE_TOOLS)
try:
    import project_store
except Exception:
    project_store = None
try:
    import chatgpt_queue
    import chatgpt_import
    import chatgpt_run_api
    import image_use_runtime
    import image_use_runner
except Exception:
    chatgpt_queue = None
    chatgpt_import = None
    chatgpt_run_api = None
    image_use_runtime = None
    image_use_runner = None
try:
    import script_repository
except Exception:
    script_repository = None
try:
    from reference_limits import reference_limit
except Exception:
    reference_limit = None
_KEY_PAT=re.compile(r"(sk-[A-Za-z0-9]{8,}|Bearer\s+[A-Za-z0-9]{8,}|api[_-]?key['\"=:\s]+[A-Za-z0-9]{12,})",re.I)
def scrub_err(e):
    """异常文本去密钥：sk-xxx / Bearer xxx / api_key=xxx 打码后再回前端。"""
    from error_utils import scrub_error
    return scrub_error(e)

def tool(name):
    for d in [SKILL,os.path.join(SYS,"tools"),os.path.join(SYS,"engine"),os.path.join(ROOT,"tools")]:
        f=os.path.join(d,name)
        if os.path.exists(f): return f
    return None
DL=os.path.join(os.path.expanduser("~"),"Downloads")
LLMCFG=os.path.join(ROOT,"llm_config.json")
PROV=os.path.join(ROOT,"providers.json")   # 阶段二：厂商+能力槽位配置（v2 vendors 结构）
PROV_BAK2=PROV+".bak2"                      # v1->v2 迁移前备份
from provider_catalog import KINDS, DEFAULT_VENDORS, normalize_vendors

# v1 平铺供应商 id -> v2 厂商 id 映射（迁移聚合用）
LEGACY_ID_MAP={"glm-vision":"glm","glm-text":"glm","qwen":"qwen","qwen-vl":"qwen","qwen-image":"qwen",
               "doubao-vision":"doubao","seedream":"doubao","seedance":"doubao","kimi":"kimi","kling":"kling",
               "minimax":"minimax","minimax-video":"minimax","gemini-image":"gemini",
               "local":"local-comfyui","local-comfyui":"local-comfyui",
               "openai-img":"openai-compat","openai-compat":"openai-compat","jimeng":"doubao"}
def migrate_legacy(data):
    """v1 providers 平铺数组 -> v2 vendors：同厂商映射 id 聚合，
    base_url 取首个非空，api_key 保留最长的非空值，models/endpoints 按 kind 归档。
    返回 (vendors, legacy_list)。"""
    legacy=data.get("providers") or []
    groups={}
    for p in legacy:
        if not isinstance(p,dict): continue
        vid=LEGACY_ID_MAP.get(p.get("id"),p.get("id"))
        g=groups.setdefault(vid,{"id":vid,"label":p.get("label") or vid,"enabled":False,
                                 "base_url":"","api_key":"","note":"",
                                 "models":{},"endpoints":{}})
        if p.get("label") and g["label"]==vid: g["label"]=p["label"]
        if p.get("base_url") and not g["base_url"]: g["base_url"]=p["base_url"]
        k=(p.get("api_key") or "")
        if len(k)>len(g["api_key"]): g["api_key"]=k   # key 保留最长非空值
        if p.get("enabled"): g["enabled"]=True
        kind=p.get("kind") or "text"
        if kind=="local": kind="image"               # v1 的 local 即本地生图
        if kind in KINDS:
            if p.get("model"): g["models"][kind]=p["model"]
            if p.get("endpoint"): g["endpoints"][kind]=p["endpoint"]
    for g in groups.values():
        for k in KINDS: g["models"].setdefault(k,""); g["endpoints"].setdefault(k,"")
        # 只补不覆盖：用户已有的模型/端点保留，空缺槽位用新默认补齐
        dv=next((d for d in DEFAULT_VENDORS if d["id"]==g["id"]),None)
        if dv:
            for k in KINDS:
                if not g["models"][k] and (dv.get("models") or {}).get(k): g["models"][k]=dv["models"][k]
                if not g["endpoints"][k] and (dv.get("endpoints") or {}).get(k): g["endpoints"][k]=dv["endpoints"][k]
    return list(groups.values()),legacy
def ensure_providers():
    """有界迁移：保存旧配置备份，保留密钥、自定义模型和自定义厂商。"""
    if os.path.isfile(PROV):
        # 解析错误不能当空配置覆盖，否则会丢密钥。
        with open(PROV,encoding="utf-8") as f: data=json.load(f)
    else: data={}
    if data.get("providers") and not data.get("vendors"):
        vendors,legacy=migrate_legacy(data)
        data={"vendors":vendors,"providers_legacy":legacy}
    vendors=normalize_vendors(data.get("vendors") or [])
    if vendors != data.get("vendors"):
        backup=PROV+".before-chrome-use.bak"
        if os.path.isfile(PROV) and not os.path.exists(backup): shutil.copyfile(PROV,backup)
        data["vendors"]=vendors
        temporary=PROV+".tmp"
        with open(temporary,"w",encoding="utf-8") as f: json.dump(data,f,ensure_ascii=False,indent=2)
        os.replace(temporary,PROV)

def mask_key(k):
    """api_key 掩码：前4****后4。"""
    return (k[:4]+"****"+k[-4:]) if k and len(k)>8 else ("已配置" if k else "")
def looks_masked(k):
    """判断是否为掩码值（防止前端/脚本把掩码当真 key 回传落盘）。"""
    k=str(k or "")
    return "****" in k or k=="已配置"
def load_vendors():
    ensure_providers()
    try: return json.load(open(PROV,encoding="utf-8")).get("vendors") or []
    except Exception: return []
def vendors_masked():
    out=[]
    secret_extra=("asr_api_key","asr_ak","asr_sk","speech_api_key")   # 豆包语音 ASR 等扩展凭据
    for v in load_vendors():
        q=dict(v); q["api_key"]=mask_key(v.get("api_key",""))
        q['video_capabilities'] = tools_mod('video_profiles.py').capabilities(v)
        ex=v.get("extra")
        if isinstance(ex,dict):
            q["extra"]={k:(mask_key(str(val)) if k in secret_extra else val) for k,val in ex.items()}
        out.append(q)
    return out
def vendor_with_draft(v, draft):
    """把草稿 {base_url, api_key, models} 覆盖到厂商配置上（不改落盘）：
    草稿 api_key 为空回落已落盘 key；models 按槽位覆盖。返回新 dict。"""
    if not isinstance(draft,dict): return v
    m=dict(v) if isinstance(v,dict) else {"id":"draft","enabled":True,"base_url":"","api_key":"","models":{},"endpoints":{}}
    if draft.get("base_url"): m["base_url"]=str(draft["base_url"]).strip()
    k=str(draft.get("api_key") or "").strip()
    if k: m["api_key"]=k
    elif isinstance(v,dict) and v.get("api_key"): m["api_key"]=v["api_key"]   # 空 key 回落落盘值
    dm=draft.get("models") if isinstance(draft.get("models"),dict) else {}
    mm=dict(m.get("models") or {})
    for kk in KINDS:
        if dm.get(kk) is not None: mm[kk]=str(dm.get(kk) or "")
    m["models"]=mm
    for field in ("endpoints","extra"):
        m[field]={**(m.get(field) or {}), **(draft.get(field) or {})}
    if not isinstance(v,dict): m.setdefault("enabled",True)   # 纯草稿（厂商未落盘）默认可用
    return m
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
def _gitbash_to_win(p):
    """Git Bash 形态 /c/... 转 Windows 盘符路径，便于统一 under-root 校验。"""
    m=re.match(r"^/([a-zA-Z])[/\\](.*)$",p)
    if m: return m.group(1).upper()+":\\"+m.group(2).replace("/","\\")
    return p
def looks_like_path_arg(a):
    """命令参数是否"看起来像路径"：Windows 绝对路径（C:\\ / C:/）、UNC、/c/ 形态，或含 ../ 上跳。"""
    a=str(a)
    if re.match(r"^[a-zA-Z]:[\\/]",a): return True
    if a.startswith("\\\\"): return True
    if re.match(r"^/[a-zA-Z][/\\]",a): return True
    return re.search(r"(^|[\\/])\.\.([\\/]|$)",a) is not None
def run_arg_path_ok(a):
    """/api/run 路径类参数的 under-root 校验口径：归一化后须落在工作区 VIDEO 根内，
    或用户 Downloads（scan_sources 只读源豁免）内。"""
    p=_gitbash_to_win(str(a))
    if p.startswith(("/", "\\")): return False   # 当前盘根起的绝对路径，不在豁免范围
    rp=os.path.normpath(p if os.path.isabs(p) else os.path.join(VIDEO,p))
    return under(VIDEO,rp) or under(DL,rp)
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
MEDIA_EXT=(".mp4",".mov",".mkv",".png",".jpg",".jpeg",".webp",".blend",".py",".md",".srt",".txt",".json",".xlsx",".wav",".m4a",".mp3",".aac",".html",".ogg")
DENY_BASENAMES=("providers.json","llm_config.json","mcp.json","mcp_runtime.json","media_gateway.json","media_gateway.tmp")
DENY_EXT=(".log",".env")
def _deny_file(p):
    """敏感文件拒绝通过 HTTP 外发：厂商/LLM 配置及其 .bak 备份变体、日志、.env。"""
    b=os.path.basename(p).lower()
    if b.endswith(DENY_EXT): return True
    return any(b==n or b.startswith(n+".") for n in DENY_BASENAMES)
def scan_projects():
    pj=os.path.join(VIDEO,"projects"); out=[]
    if not os.path.isdir(pj): return out
    for name in sorted(os.listdir(pj)):
        d=os.path.join(pj,name)
        if not os.path.isdir(d): continue
        try:
            lm=tools_mod("project_layout.py")
            if lm: lm.migrate_project(d)
        except Exception:
            pass
        arts={"拉片":[],"分镜":[],"白模":[],"深度":[],"逐帧":[],"拉片素材":[],"成片":[],
              "三维探索":[],"白模3D":[],"创作":[],"素材":[],"演员":[],"推演":[],"台词":[],"根目录":[]}
        for sub in ["拉片","分镜","白模","深度","逐帧","成片","拉片素材","三维探索","白模3D","创作","素材","演员","推演","台词"]:
            sd=os.path.join(d,sub)
            if not os.path.isdir(sd): continue
            for f in sorted(os.listdir(sd)):
                # 隐藏目录（版本快照等）只供版本管理使用，不进入项目文件树，
                # 否则 .versions/xxx.json 会被误识别为当前分镜并触发 404。
                if f.startswith("."):
                    continue
                fp=os.path.join(sd,f)
                if os.path.isdir(fp):  # 嵌套一层（如 render/ 帧序列、frames_x/）
                    gs=[g for g in sorted(os.listdir(fp))
                        if not g.startswith(".") and os.path.isfile(os.path.join(fp,g))]
                    if len(gs)>20 and all(os.path.splitext(g)[1].lower() in (".png", ".jpg", ".jpeg", ".webp") for g in gs):
                        arts.setdefault(sub,[]).append("[帧序列] %s/（共%d个文件，已折叠）"%(f,len(gs)))
                    else:
                        for g in gs: arts.setdefault(sub,[]).append(f+"/"+g)
                else:
                    arts.setdefault(sub,[]).append(f)
        # 项目根目录散放的产物（如 07_监狱白模3D 的 .blend/.mp4/build_*.py）
        for f in sorted(os.listdir(d)):
            if f.startswith('.') or f.startswith('_versions'):
                continue
            if os.path.isfile(os.path.join(d,f)) and f.lower().endswith(MEDIA_EXT):
                arts["根目录"].append(f)
        ptype="拆片"
        try:
            mp=os.path.join(d,"项目.json")
            if os.path.isfile(mp):
                ptype=str(json.load(open(mp,encoding="utf-8")).get("type") or "拆片")
        except Exception:
            pass
        out.append({"name":name,"dirs":arts,"type":ptype,"workspace_root":VIDEO})
    return out

def find_blender():
    cands=[r"J:\Blender 5.2\blender.exe",r"J:\Blender 5.1\blender.exe",r"J:\Blender 5.0\blender.exe"]
    cands+=glob.glob(r"C:\Program Files\Blender Foundation\Blender *\blender.exe")
    cands+=glob.glob(r"J:\Blender*\blender.exe")
    for c in cands:
        if os.path.isfile(c): return c
    return None

def detect_packages():
    """检测常用 Python 包是否安装，返回 {包名: 版本|"缺失"}。"""
    import importlib.metadata as md
    out={}
    for name,mod in [("numpy","numpy"),("Pillow","PIL"),("opencv-python","cv2"),
                     ("openpyxl","openpyxl"),("rapidocr_onnxruntime","rapidocr_onnxruntime"),
                     ("faster-whisper","faster_whisper"),("transformers","transformers")]:
        try: out[mod]=md.version(name)
        except Exception: out[mod]="缺失"
    return out
def detect_env():
    """本机环境检测：python/ffmpeg/包/blender/MCP。"""
    import shutil as _sh
    ff=_sh.which("ffmpeg")
    if not ff:
        for c in glob.glob(os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_*\ffmpeg-*\bin\ffmpeg.exe")):
            if os.path.isfile(c): ff=c; break
    mcp=False
    try:
        s=socket.create_connection(("127.0.0.1",9876),timeout=2); s.close(); mcp=True
    except Exception: pass
    return {"python":sys.version.split()[0],"ffmpeg":ff,"packages":detect_packages(),
            "blender":find_blender(),"mcp":mcp,"chrome_use":image_use_runtime.status()}
TOOLS=os.path.join(ROOT,"tools")                       # 阶段一新增脚本目录
WEBDIST=os.path.join(ROOT,"web","dist")               # Vite 构建产物（workbench/web，存在才生效）
JOBSDIR=os.path.join(ROOT,"jobs")                     # 任务状态持久化目录
JOBS_MAX=500                                          # JOBS 内存上限（超出淘汰最旧的非 running 记录，running 永不淘汰）
JOB_LOG_KEEP_DAYS=7                                   # jobs/ 磁盘日志保留天数
def cleanup_job_logs(now=None):
    """删除 jobs/ 下超过保留期的任务日志（job_*.json，按文件修改时间）。返回删除数。"""
    try:
        cut=(now if now is not None else time.time())-JOB_LOG_KEEP_DAYS*86400
        n=0
        for f in os.listdir(JOBSDIR):
            if not re.match(r"job_\d+\.json$",f): continue
            p=os.path.join(JOBSDIR,f)
            try:
                if os.path.getmtime(p)<cut: os.remove(p); n+=1
            except OSError: pass
        return n
    except Exception: return 0
def restore_jobs(h):
    """启动时从 jobs/ 恢复历史任务；running 状态标记为 interrupted（进程已被杀）。"""
    try:
        for f in os.listdir(JOBSDIR):
            if not f.startswith("job_"): continue
            try: rec=json.load(open(os.path.join(JOBSDIR,f),encoding="utf-8"))
            except Exception: continue
            jid=rec.get("id")
            if not isinstance(jid,int): continue
            if rec.get("status")=="running":
                rec["status"]="interrupted"; rec["err"]=(rec.get("err") or "")+"[服务重启，任务中断]"
                try: json.dump(rec,open(os.path.join(JOBSDIR,f),"w",encoding="utf-8"),ensure_ascii=False,indent=1)  # 写回磁盘
                except Exception: pass
            h.JOBS[jid]=rec; h.JOBSEQ[0]=max(h.JOBSEQ[0],jid)
    except Exception: pass
_tools_cache={}
def tools_mod(name):
    """按文件路径加载 workbench/tools/ 下的模块（不污染 sys.path）。"""
    if name in _tools_cache: return _tools_cache[name]
    p=os.path.join(TOOLS,name)
    if not os.path.isfile(p): return None
    spec=importlib.util.spec_from_file_location(name[:-3],p)
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    _tools_cache[name]=m
    return m
def _versions_snapshot(path):
    """覆写项目产物前自动快照旧版本到 .versions/（versions.py 缺失或失败均静默跳过）。"""
    try:
        vm=tools_mod("versions.py")
        if vm and os.path.isfile(path):
            vm.snapshot(path)
    except Exception:
        pass


def asset_image_preflight(project_dir, kind="all", asset_id=None, vendor=None):
    """检查资产图片任务的参考模式与本地 ComfyUI 改图配置。

    参考口径：人物/场景/道具母图一律无参考生成；仅 derived_from 派生
    子图引用父资产母图并切到 Qwen Image Edit。在创建后台任务前先做同
    一份判定，避免任务启动后才发现改图模型缺失。选中 image_edit 模型
    后，ComfyUI 适配器自动使用内置 Qwen Image Edit 工作流；用户填写的
    image_edit_workflow_path 只是可选覆盖。返回
    ``{ok, plans, needs_edit, edit_workflow, notes?, err?}``，不访问
    ComfyUI 网络。派生父图缺失不拦截任务，只记入 ``notes``（生成端会
    降级为无参考生成）。
    """
    result = {"ok": True, "plans": [], "needs_edit": False, "edit_workflow": ""}
    if not isinstance(vendor, dict):
        return result
    planner = tools_mod("gen_asset_images.py")
    if planner is None or not hasattr(planner, "collect_asset_image_plan"):
        return {"ok": False, "plans": [], "needs_edit": False,
                "err": "资产生成规划模块缺失，无法确认参考图模式"}
    try:
        plans = planner.collect_asset_image_plan(project_dir, kind, asset_id, vendor.get("id", ""))
    except Exception as exc:
        return {"ok": False, "plans": [], "needs_edit": False,
                "err": "读取资产引用失败：" + str(exc)}
    result["plans"] = plans
    edit_plans = [p for p in plans if p.get("mode") == "edit"]
    result["needs_edit"] = bool(edit_plans)
    if result["needs_edit"] and str(vendor.get("id") or "") == "local-comfyui":
        models = vendor.get("models") if isinstance(vendor.get("models"), dict) else {}
        if not str(models.get("image_edit") or "").strip():
            return {**result, "ok": False,
                    "err": "资产包含派生引用，将使用 Qwen Image Edit；请先在环境页配置 local-comfyui 的 image_edit 模型。"}
        extra = vendor.get("extra") if isinstance(vendor.get("extra"), dict) else {}
        workflow = str(extra.get("image_edit_workflow_path") or "").strip()
        # 自定义工作流是可选覆盖；未填写时明确标记内置路径，不再把
        # “模型已选”错误地判定为配置不完整。
        result["edit_workflow"] = workflow or "builtin:qwen_image_edit_2511"
        # 仅在用户主动选择自定义工作流时校验文件是否存在且能解析；参考
        # 位数量、负面输入位等细节由 ComfyUI 适配器在实际渲染前继续校验。
        comfy = tools_mod("comfyui_client.py")
        if workflow and comfy is not None:
            try:
                comfy.load_workflow_template(workflow)
            except Exception as exc:
                return {**result, "ok": False, "err": "改图工作流不可用：" + str(exc)}
    # 缺图不再拦截：母图本就不依赖任何参考；derived_from 派生图的父图
    # 缺失时由生成器降级为无参考生成并告警。批量任务中已排队生成的父
    # 资产不算缺失；其余降级情况作为 note 透出，便于前端提示。
    planned_refs = {
        f"@{p.get('kind')}:{p.get('id')}"
        for p in plans
        if p.get("can_generate")
    }
    notes = []
    for plan in plans:
        external_missing = [
            ref for ref in (plan.get("missing_refs") or [])
            if str(ref) not in planned_refs
        ]
        if external_missing:
            refs = ", ".join(str(x) for x in external_missing[:4])
            notes.append(f"{plan.get('kind')}/{plan.get('id')} 派生母图缺失（{refs}），将降级为无参考生成")
    if notes:
        result["notes"] = notes
    return result
def safe_proj(name):
    """项目名/版本名清洗：只允许单层目录名。"""
    name=(name or "").replace("/","").replace("\\","")
    name=os.path.basename(name)
    return name if name and name not in (".","..") else ""
def proj_dir(name):
    d=os.path.join(VIDEO,"projects",safe_proj(name))
    if not os.path.isdir(d): return None
    try:
        lm=tools_mod("project_layout.py")
        if lm: lm.migrate_project(d)
    except Exception:
        pass
    return d
def storyboard_episode_error(project_dir, episode_id=None):
    """分镜/资产任务启动前检查正文；返回可读错误，正文可用时返回 None。"""
    ep = str(episode_id or "").strip()
    try:
        repo = tools_mod("script_repository.py")
        if ep:
            book_path = os.path.join(project_dir, "剧本", "分集.json")
            if os.path.isfile(book_path):
                with open(book_path, encoding="utf-8") as fh:
                    book = json.load(fh)
            else:
                book = {}
            episodes = book.get("episodes") or []
            if not any(str(item.get("id")) == ep for item in episodes if isinstance(item, dict)):
                return f"分集 {ep} 不存在"
        text = repo.load_script(project_dir, ep or None) if repo else ""
    except Exception as exc:
        return f"读取剧本文本失败：{exc}"
    if str(text or "").strip():
        return None
    label = f"分集 {ep}" if ep else "项目"
    return f"{label}暂无剧本文本，请先在①剧本分集页导入或扩写"
def load_storyboard(project_dir, board_name):
    """安全读取项目分镜 JSON，返回 (规范化文件名, board)。"""
    d=os.path.abspath(project_dir or "")
    nm=safe_proj(board_name)
    p=os.path.join(d,"分镜",nm) if d and nm else ""
    if not (nm.lower().endswith(".json") and p and under(os.path.join(d,"分镜"),os.path.realpath(p)) and os.path.isfile(p)):
        raise ValueError("分镜不存在")
    try:
        board=json.load(open(p,encoding="utf-8"))
    except Exception as exc:
        raise ValueError("分镜 JSON 无法读取: "+str(exc))
    if not isinstance(board,dict) or not isinstance(board.get("shots"),list):
        raise ValueError("分镜格式无效")
    return nm,board,p
def assemble_create_shot(project_dir, board_name, shot_id, prompt_user="", media_type="image", vendor_id="", mode="generate"):
    """服务端统一调用阶段提示词编译器，避免前端自行拼提示词造成漂移。"""
    mod=tools_mod("prompt_assembler.py")
    compiler=tools_mod("prompt_compiler.py")
    if not mod: raise ValueError("prompt_assembler.py 缺失")
    nm,board,p=load_storyboard(project_dir,board_name)
    shot=next((s for s in board.get("shots",[]) if str(s.get("id"))==str(shot_id)),None)
    if not isinstance(shot,dict): raise ValueError("镜头不存在: "+str(shot_id))
    stage = "storyboard_image" if str(media_type).lower() == "image" else "video"
    compiled = None
    if compiler and getattr(compiler, "compile_stage_prompt", None):
        compiled = compiler.compile_stage_prompt(stage, shot, project_dir, mode=mode, board=board)
        bundle = {
            "prompt_assembled": compiled.get("content_prompt", ""),
            "prompt_json": compiled.get("prompt_json"),
            "asset_refs": compiled.get("asset_refs") or [],
            "negative": compiled.get("negative_prompt", ""),
            "asset_context": compiled.get("asset_context") or {},
            "system_prompt": compiled.get("system_prompt", ""),
            "prompt_stage": compiled.get("stage", stage),
            "prompt_revision": compiled.get("prompt_revision", ""),
            "asset_revisions": compiled.get("asset_revisions") or {},
        }
    else:
        bundle=mod.assemble_shot_prompt(shot,{"dir":project_dir,"board":board,"media_type":media_type})
        bundle.update({"prompt_stage": stage, "prompt_revision": "", "asset_revisions": {}})
    ref_shot=mod.reference_shot_for_prompt(shot,bundle.get("prompt_assembled",""),media_type)
    vendor_cfg = next((v for v in load_vendors() if v.get("id") == str(vendor_id or "")), None)
    model = ((vendor_cfg or {}).get("models") or {}).get(media_type, "")
    ref_cap = (reference_limit(str(vendor_id or ""), model, media_type, vendor_cfg) if vendor_id and reference_limit else 10)
    refs=mod.resolve_shot_refs(ref_shot,project_dir,actors=board.get("actors"),board_name=nm,max_refs=ref_cap,board=board,media_type=media_type)
    bundle.update({"board":nm,"shot_id":str(shot_id),"prompt_user":str(prompt_user or ""),"refs":refs,
                   "source":os.path.relpath(p,project_dir).replace(os.sep,"/"),
                   "source_hash":compiler.artifact_hash(board,"prompt","actor-v1") if compiler and compiler.artifact_hash else ""})
    return bundle
def safe_video_abs(p):
    """绝对路径视频：须在 VIDEO 根内或用户 Downloads 内。"""
    p=os.path.normpath(p or "")
    if not os.path.isfile(p): return None
    if under(VIDEO,p) or under(DL,p): return p
    return None
class H(BaseHTTPRequestHandler):
    def log_message(self,*a): pass

    def log_request(self, code='-', size='-'):
        # 仅记录服务端错误；不写请求正文、查询参数或鉴权头。
        if str(code).isdigit() and int(code) >= 500:
            path = urllib.parse.urlsplit(self.path).path
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] HTTP {code} {self.command} {path}", flush=True)
    JOBS={}; JOBSEQ=[0]; JLOCK=threading.RLock(); PROCS={}
    def _persist_job(self,jid):
        """任务状态落盘 workbench/jobs/job_<id>.json（服务重启后可查）。"""
        try:
            with self.JLOCK: rec=dict(self.JOBS.get(jid) or {})
            if not rec: return
            os.makedirs(JOBSDIR,exist_ok=True)
            json.dump(rec,open(os.path.join(JOBSDIR,"job_%d.json"%jid),"w",encoding="utf-8"),ensure_ascii=False,indent=1)
        except Exception: pass
    def job_record(self,jid):
        """内存优先，磁盘兜底。返回 (record 或 None)。"""
        with self.JLOCK: j=self.JOBS.get(jid)
        if j: return j
        try:
            p=os.path.join(JOBSDIR,"job_%d.json"%jid)
            if os.path.isfile(p): return json.load(open(p,encoding="utf-8"))
        except Exception: pass
        return None
    def _evict_jobs(self):
        """JOBS 内存上限淘汰：超过 JOBS_MAX 时按 id 从旧到新删除已结束记录
        （done/failed/error/interrupted/cancelling），running 永不淘汰；磁盘 jobs/*.json 保留。"""
        with self.JLOCK:
            overflow=len(self.JOBS)-JOBS_MAX
            if overflow<=0: return
            for jid in sorted(self.JOBS):
                if overflow<=0: break
                if self.JOBS[jid].get("status")=="running": continue
                del self.JOBS[jid]; overflow-=1
    def spawn_job(self,step,cmd):
        with self.ENV_INSTALL_LOCK, self.JLOCK:
            active = [j for j in self.JOBS.values() if j.get("status") == "running" or j.get("process_alive")]
            if any(j.get("step") == "environment_install" for j in active) or (step == "environment_install" and active):
                raise ValueError("环境安装与生成任务不能同时运行，请等待当前任务结束")
            self.JOBSEQ[0]+=1; jid=self.JOBSEQ[0]
            self.JOBS[jid]={"id":jid,"step":step,"status":"running","out":"","err":"","cmd":[os.path.basename(c) for c in cmd],
                            "fullcmd":list(cmd),"attempts":1,"attempt_id":f"{jid}-a1",
                            "write_revoked":False,"process_alive":False,
                            "started_at":time.time(),"updated_at":time.time()}
        self._evict_jobs()
        self._persist_job(jid)
        self._job_work(jid,cmd, f"{jid}-a1")
        return jid
    def _job_work(self,jid,cmd,attempt_id=None):
        """任务执行线程（spawn 与 429 看门狗重试共用）。流式读输出：每行实时并入 JOBS[jid]['out']
        （尾部 8000 字符），每 20 行或 2 秒节流落盘；stderr 已并入 stdout。timeout 14400s 由看门狗 kill
        （AI 解构长片 69 镜约 1h+，3600s 会误杀；超时 err 标明原因）。"""
        def work():
            ok=False; err=""
            timed_out=[False]
            p=None
            try:
                _env=dict(os.environ); _env["PYTHONUNBUFFERED"]="1"  # 子进程不缓冲 stdout，日志实时流式可见
                p=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                                   text=True,encoding="utf-8",errors="replace",env=_env)
                with self.JLOCK:
                    rec=self.JOBS.get(jid)
                    if rec and (attempt_id is None or rec.get("attempt_id")==attempt_id) and not rec.get("write_revoked"):
                        rec["process_alive"]=True; rec["pid"]=p.pid; rec["updated_at"]=time.time()
                    self.PROCS[jid]=p
                def _watchdog():
                    try: p.wait(timeout=14400)
                    except subprocess.TimeoutExpired:
                        timed_out[0]=True
                        try: p.kill()
                        except Exception: pass
                threading.Thread(target=_watchdog,daemon=True).start()
                buf=[]; n=0; last=time.time()
                for line in (p.stdout or []):
                    buf.append(line); n+=1
                    now=time.time()
                    if n>=20 or now-last>=2.0:   # 节流落盘
                        with self.JLOCK:
                            j=self.JOBS.get(jid)
                            if j and (attempt_id is None or j.get("attempt_id")==attempt_id) and not j.get("write_revoked"):
                                j["out"]=((j.get("out") or "")+"".join(buf))[-8000:]; j["updated_at"]=time.time()
                        self._persist_job(jid); buf=[]; n=0; last=now
                p.wait()
                ok=p.returncode==0
                if not ok: err=("服务端看门狗超时（>4h）已强制终止，可在对应页面重试或分段运行"
                                if timed_out[0] else "退出码 %s"%p.returncode)
                if buf:
                    with self.JLOCK:
                        j=self.JOBS.get(jid)
                        if j and (attempt_id is None or j.get("attempt_id")==attempt_id) and not j.get("write_revoked"):
                            j["out"]=((j.get("out") or "")+"".join(buf))[-8000:]; j["updated_at"]=time.time()
            except Exception as e:
                ok=False; err=str(e)
            finally:
                with self.JLOCK:
                    if self.PROCS.get(jid) is p:
                        self.PROCS.pop(jid, None)
            with self.JLOCK:
                j=self.JOBS.get(jid)
                if j and (attempt_id is None or j.get("attempt_id")==attempt_id) and not j.get("write_revoked"):
                    j["out"]=(j.get("out") or "")[-8000:]
                    j["err"]=err[-4000:]; j["status"]="done" if ok else "failed"; j["ok"]=ok
                    j["process_alive"]=False; j.pop("pid",None)
                    j["finished_at"]=time.time()
                    if j.get("started_at"): j["elapsed"]=round(j["finished_at"]-j["started_at"],1)
            self._persist_job(jid)
        threading.Thread(target=work,daemon=True).start()
    def _send(self,code,ct,body,extra=None):
        if ct.startswith('application/json'):
            from error_utils import scrub_payload
            body=json.dumps(scrub_payload(json.loads(body)),ensure_ascii=False).encode('utf-8')
        self.send_response(code); self.send_header("Content-Type",ct); self.send_header("Content-Length",str(len(body)))
        for k,v in (extra or {}).items(): self.send_header(k,v)
        self.end_headers(); self.wfile.write(body)
    def _run_token(self):
        return str(self.headers.get("X-Previs-Run-Token") or "").strip()
    def _send_run_json(self,code,payload):
        return self._send(code,"application/json; charset=utf-8",json.dumps(payload,ensure_ascii=False).encode())
    def _send_run_error(self,exc):
        status=int(getattr(exc,"status",409 if isinstance(exc,project_store.RevisionConflict) else
                           404 if isinstance(exc,FileNotFoundError) else 400 if isinstance(exc,ValueError) else 500))
        message=str(getattr(exc,"message",exc))
        return self._send_run_json(status,{"ok":False,"err":message})
    def do_GET(self):
        self._dispatch_request('GET')
    def do_POST(self):
        self._dispatch_request('POST')
    def do_HEAD(self):
        self._dispatch_request('HEAD')

    def handle_one_request(self):
        # HTTP keep-alive 的每一个请求独立计数，包括解析错误和不支持的方法。
        self._response_started = False
        return super().handle_one_request()

    def send_response_only(self, code, message=None):
        if getattr(self, '_response_started', False):
            raise ResponseAlreadyStarted('响应已经开始，禁止重复发送')
        # 100 Continue 等临时响应不占用最终响应名额；101 切换协议后不可再响应。
        if code >= 200 or code == 101:
            self._response_started = True
        return super().send_response_only(code, message)

    def _request_error(self, code, message):
        if getattr(self, '_response_started', False):
            self.close_connection = True
            print(f'HTTP 响应开始后中止：{self.command} {scrub_err(message)}', file=sys.stderr)
            return
        try:
            self._send_run_json(code, {'ok': False, 'err': message})
        except (OSError, ResponseAlreadyStarted):
            self.close_connection = True

    def _dispatch_request(self, method):
        try:
            url = urllib.parse.urlparse(self.path)
            query = MappingProxyType({k: tuple(v) for k, v in urllib.parse.parse_qs(url.query).items()})
            length = 0
            if method == 'POST':
                try:
                    length = int(self.headers.get('Content-Length', 0))
                    if length < 0: raise ValueError('negative length')
                except ValueError:
                    return self._request_error(400, 'Content-Length 无效')
            ctx = RequestContext(url, query, length)
            handler = ROUTES.get((method, url.path))
            if handler is not None:
                # 二进制上传保持流式；其余 POST 在业务分支前验证对象 JSON。
                multipart_import = url.path == '/api/create/chatgpt/import' and self.headers.get('Content-Type','').lower().startswith('multipart/form-data')
                if method == 'POST' and url.path not in ('/api/import', '/api/reference-media/upload') and not multipart_import:
                    import io
                    raw=self.rfile.read(length)
                    try: parsed=json.loads(raw.decode('utf-8') or '{}')
                    except (ValueError,UnicodeError): return self._request_error(400,'JSON 解析失败')
                    if not isinstance(parsed,dict): return self._request_error(400,'JSON 请求体必须是对象')
                    # 请求处理后恢复原始流，避免破坏 keep-alive 下一个请求。
                    original=self.rfile
                    self.rfile=io.BytesIO(raw)
                    try: handler(self,ctx)
                    finally: self.rfile=original
                else:
                    handler(self, ctx)
                if not self._response_started:
                    self._request_error(500, '接口处理完成但没有响应')
                return
            if method == 'GET':
                return self._static_or_not_found(ctx)
            return self._send(404, 'text/plain', b'404')
        except json.JSONDecodeError:
            self._request_error(400, 'JSON 解析失败')
        except Exception as e:
            self._request_error(500, scrub_err(e))
    @route('GET', '/', '/index.html')
    def route_get_index(self, ctx):
        u, q = ctx.url, ctx.query
        p=os.path.join(WEBDIST,"index.html")
        if not os.path.isfile(p):
            return self._send(503, "text/html; charset=utf-8", "<meta charset=utf-8><h1>新版前端尚未构建</h1><p>请在 workbench/web 执行 npm ci，再执行 npm run build，然后刷新本页。</p>".encode("utf-8"), {"Cache-Control": "no-store"})
        self.send_response(200); self.send_header("Content-Type","text/html; charset=utf-8")
        self.send_header("Cache-Control","no-store, no-cache, must-revalidate"); self.send_header("Pragma","no-cache")
        body=open(p,"rb").read(); self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body); return

    @route('GET', '/api/sources')
    def route_get_api_sources(self, ctx):
        u, q = ctx.url, ctx.query
        return self._send(200,"application/json; charset=utf-8",json.dumps(scan_sources(),ensure_ascii=False).encode())

    @route('GET', '/src')
    def route_get_src(self, ctx):
        u, q = ctx.url, ctx.query
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

    @route('GET', '/api/projects')
    def route_get_api_projects(self, ctx):
        u, q = ctx.url, ctx.query
        return self._send(200,"application/json; charset=utf-8",json.dumps(scan_projects(),ensure_ascii=False).encode())

    @route('GET', '/api/jobs')
    def route_get_api_jobs(self, ctx):
        u, q = ctx.url, ctx.query
        # 历史任务清单：磁盘 jobs/*.json + 内存合并，按 id 倒序（轻字段，详情走 /api/job?id=）
        items={}
        try:
            for f in os.listdir(JOBSDIR):
                m=re.match(r"job_(\d+)\.json$",f)
                if not m: continue
                p=os.path.join(JOBSDIR,f)
                try:
                    rec=json.load(open(p,encoding="utf-8"))
                except Exception: continue
                jid=rec.get("id")
                if not isinstance(jid,int): continue
                items[jid]={"id":jid,"step":rec.get("step"),"status":rec.get("status"),
                            "ok":rec.get("ok"),"started_at":rec.get("started_at") or os.path.getmtime(p),
                            "finished_at":rec.get("finished_at"),"elapsed":rec.get("elapsed"),
                            "err":rec.get("err") or ""}
        except Exception: pass
        with self.JLOCK:
            for jid,j in self.JOBS.items():
                items[jid]={"id":jid,"step":j.get("step"),"status":j.get("status"),
                            "ok":j.get("ok"),"started_at":j.get("started_at"),
                            "finished_at":j.get("finished_at"),"elapsed":j.get("elapsed"),
                            "err":j.get("err") or ""}
        lst=sorted(items.values(),key=lambda x:-x["id"])[:100]
        return self._send(200,"application/json; charset=utf-8",json.dumps({"jobs":lst},ensure_ascii=False).encode())

    @route('GET', '/api/job')
    def route_get_api_job(self, ctx):
        u, q = ctx.url, ctx.query
        jid=int(q.get("id",["0"])[0])
        j=self.job_record(jid)
        if j and j.get("status")=="interrupted": j=dict(j)  # 重启后恢复的历史任务
        return self._send(200,"application/json; charset=utf-8",json.dumps(j or {"err":"无此任务","lost":True},ensure_ascii=False).encode())

    @route('GET', '/api/file')
    def route_get_api_file(self, ctx):
        u, q = ctx.url, ctx.query
        # 读文本文件。兼容旧调用：project + 项目内相对路径；
        # 新调用仍可直接传 projects/<项目>/...。
        rel=q.get("p",[""])[0]
        project=q.get("project",[""])[0]
        if project and rel and not rel.replace("\\","/").startswith("projects/"):
            rel="projects/"+safe_proj(project)+"/"+rel.lstrip("/\\")
        p=safe_video(rel)
        if p and _deny_file(p):
            return self._send(403,"text/plain; charset=utf-8","禁止访问敏感文件".encode())
        if p and not p.lower().endswith(MEDIA_EXT):
            return self._send(403,"text/plain; charset=utf-8","文件类型不在允许范围".encode())
        if p:
            try: return self._send(200,"text/plain; charset=utf-8",open(p,encoding="utf-8",errors="replace").read().encode())
            except Exception as e: return self._send(500,"text/plain; charset=utf-8",str(e).encode())
        return self._send(404,"text/plain",b"not found")

    @route('GET', '/media')
    def route_get_media(self, ctx):
        u, q = ctx.url, ctx.query
        rel=q.get("p",[""])[0]; pth=safe_video(rel)
        if pth and (_deny_file(pth) or not pth.lower().endswith(MEDIA_EXT)):
            return self._send(403,"text/plain; charset=utf-8","禁止访问该文件".encode())
        if pth:
            size=os.path.getsize(pth); rng=self.headers.get("Range"); ct={".mp4":"video/mp4",".mp3":"audio/mpeg",".jpg":"image/jpeg",".png":"image/png",".webp":"image/webp",".mkv":"video/x-matroska",".mov":"video/quicktime",".wav":"audio/wav",".m4a":"audio/x-m4a",".mp3":"audio/mpeg",".aac":"audio/aac",".ogg":"audio/ogg",".blend":"application/octet-stream",".py":"text/plain; charset=utf-8",".xlsx":"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",".html":"text/html; charset=utf-8",".json":"application/json"}.get(os.path.splitext(pth)[1].lower(),"application/octet-stream")
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

    @route('GET', '/api/analysis')
    def route_get_api_analysis(self, ctx):
        u, q = ctx.url, ctx.query
        d=proj_dir(q.get("project",[""])[0])
        vf=os.path.join(d,"拉片","_versions.json") if d else ""
        vers=[]
        if d and os.path.isfile(vf):
            try: vers=json.load(open(vf,encoding="utf-8"))
            except Exception: vers=[]
        for v in vers:
            ad=os.path.join(d,"拉片",safe_proj(v.get("name","")))
            aj=os.path.join(ad,"analysis.json")
            if os.path.isfile(aj):
                try:
                    a=json.load(open(aj,encoding="utf-8"))
                    sh=a.get("shots") or []
                    v["shot_count"]=len(sh)
                    v["engine"]=a.get("engine","none")
                    v["first_shot"]=({"id":sh[0].get("id"),"t_in":sh[0].get("t_in"),"t_out":sh[0].get("t_out")} if sh else None)
                    v["first_t"]=sh[0].get("t_in") if sh else None
                    v["last_t"]=sh[-1].get("t_out") if sh else None
                except Exception: v["err"]="analysis.json 读取失败"
        return self._send(200,"application/json; charset=utf-8",json.dumps(vers,ensure_ascii=False).encode())

    @route('GET', '/api/analysis/get')
    def route_get_api_analysis_get(self, ctx):
        u, q = ctx.url, ctx.query
        d=proj_dir(q.get("project",[""])[0]); nm=safe_proj(q.get("name",[""])[0])
        p=os.path.join(d,"拉片",nm,"analysis.json") if d and nm else ""
        if p and under(d,p) and os.path.isfile(p):
            return self._send(200,"application/json; charset=utf-8",open(p,"rb").read())
        return self._send(404,"application/json",json.dumps({"err":"版本不存在"},ensure_ascii=False).encode())

    @route('GET', '/api/white/board')
    def route_get_api_white_board(self, ctx):
        u, q = ctx.url, ctx.query
        # 读取 分镜/<name>（白模契约 storyboard），供白模页可视化
        d=proj_dir(q.get("project",[""])[0]); nm=safe_proj(q.get("name",[""])[0])
        p=os.path.join(d,"分镜",nm) if d and nm else ""
        if p and nm.lower().endswith(".json") and under(d,p) and os.path.isfile(p):
            return self._send(200,"application/json; charset=utf-8",open(p,"rb").read())
        return self._send(404,"application/json",json.dumps({"err":"分镜不存在"},ensure_ascii=False).encode())

    @route('GET', '/api/creation/diagrams')
    def route_get_api_creation_diagrams(self, ctx):
        u, q = ctx.url, ctx.query
        # 当前分镜已经生成的平面图清单，前端据此避免请求不存在的 S##.png。
        d=proj_dir(q.get("project",[""])[0]); nm=safe_proj(q.get("name",[""])[0])
        base=nm[:-5] if nm.lower().endswith(".json") else nm
        dd=os.path.join(d,"推演","平面图_"+base) if d and base else ""
        paths=[]
        if dd and os.path.isdir(dd):
            for fn in sorted(os.listdir(dd)):
                if fn.lower().endswith((".png",".jpg",".jpeg",".webp")) and os.path.isfile(os.path.join(dd,fn)):
                    paths.append("projects/"+safe_proj(q.get("project",[""])[0])+"/推演/平面图_"+base+"/"+fn)
        return self._send(200,"application/json; charset=utf-8",
                          json.dumps({"paths":paths},ensure_ascii=False).encode())

    @route('GET', '/api/analysis/xlsx')
    def route_get_api_analysis_xlsx(self, ctx):
        u, q = ctx.url, ctx.query
        # 在线查看分镜脚本 xlsx：解析为 {sheets:[{title,rows}]}（不存在则 404，前端可先调 export 生成）
        d=proj_dir(q.get("project",[""])[0]); nm=safe_proj(q.get("name",[""])[0])
        p=os.path.join(d,"拉片",nm,nm+"_分镜脚本.xlsx") if d and nm else ""
        if not (p and under(d,p) and os.path.isfile(p)):
            return self._send(404,"application/json",json.dumps({"err":"xlsx 不存在，请先生成"},ensure_ascii=False).encode())
        try:
            from openpyxl import load_workbook
            wb=load_workbook(p,read_only=True,data_only=True)
            sheets=[{"title":ws.title,"rows":[[("" if c is None else str(c)) for c in row] for row in ws.iter_rows(values_only=True)]} for ws in wb.worksheets]
            wb.close()
            return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"sheets":sheets},ensure_ascii=False).encode())
        except Exception as e:
            return self._send(500,"application/json",json.dumps({"err":"xlsx 解析失败:"+str(e)},ensure_ascii=False).encode())

    @route('GET', '/api/frames')
    def route_get_api_frames(self, ctx):
        u, q = ctx.url, ctx.query
        d=proj_dir(q.get("project",[""])[0])
        p=os.path.join(d,"逐帧","每秒","frames_manifest.json") if d else ""
        if p and os.path.isfile(p):
            return self._send(200,"application/json; charset=utf-8",open(p,"rb").read())
        return self._send(404,"application/json",json.dumps({"err":"尚未抽取每秒帧"},ensure_ascii=False).encode())

    @route('GET', '/api/lines')
    def route_get_api_lines(self, ctx):
        u, q = ctx.url, ctx.query
        d=proj_dir(q.get("project",[""])[0])
        p=os.path.join(d,"台词","台词脚本.json") if d else ""
        if p and os.path.isfile(p):
            return self._send(200,"application/json; charset=utf-8",open(p,"rb").read())
        # 制作项目可以没有台词脚本；返回空契约，避免页面切换时制造无意义 404。
        return self._send(200,"application/json; charset=utf-8",
                          json.dumps({"speakers":{}, "lines":[]},ensure_ascii=False).encode())

    @route('GET', '/api/assets')
    def route_get_api_assets(self, ctx):
        u, q = ctx.url, ctx.query
        d=proj_dir(q.get("project",[""])[0])
        kind=str(q.get("kind",[""])[0] or "").strip()
        if not d:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"项目不存在"},ensure_ascii=False).encode())
        try:
            mod=tools_mod("asset_registry.py")
            registry=mod.AssetRegistry(d)
            assets=registry.list(kind or None)
            return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"assets":assets},ensure_ascii=False).encode())
        except Exception as exc:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":str(exc)},ensure_ascii=False).encode())

    @route('GET', '/api/asset/prompt_layers')
    def route_get_api_asset_prompt_layers(self, ctx):
        u, q = ctx.url, ctx.query
        # 资产生图三层提示词预览：外观事实 / 画风层 / 负面（统一）+ 类别硬约束与最终合成。
        d=proj_dir(q.get("project",[""])[0])
        kind=str(q.get("kind",[""])[0] or "character").strip()
        ident=str(q.get("id",[""])[0] or "").strip()
        ZONE={"character":"人物","scene":"场景","prop":"道具"}
        KEY={"character":"characters","scene":"scenes","prop":"props"}
        PFIELD={"character":"sheet_prompt","scene":"image_prompt","prop":"image_prompt"}
        if not d or not ident or kind not in ZONE:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"project/kind/id 参数不合法"},ensure_ascii=False).encode())
        item=None
        fp=os.path.join(d,"素材",ZONE[kind]+".json")
        if os.path.isfile(fp):
            try:
                data=json.load(open(fp,encoding="utf-8"))
                for it in data.get(KEY[kind],[]):
                    if str(it.get("id"))==ident: item=it; break
            except Exception: pass
        if not item:
            return self._send(404,"application/json",json.dumps({"ok":False,"err":"资产不存在"},ensure_ascii=False).encode())
        sl=tools_mod("skill_lib.py")
        subject=str(item.get(PFIELD[kind]) or "").strip()
        style_text,src=sl.resolve_asset_style_text(d,item.get("style"),item.get("style_prompt"))
        final,negative=sl.compose_asset_image_prompt(d,subject,skill_id=item.get("style"),kind=kind,style_prompt=item.get("style_prompt"))
        return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"kind":kind,"id":ident,
            "layers":{"subject":subject,"style":style_text,"style_source":src,
                      "negative":negative,"constraint":sl.ASSET_KIND_CONSTRAINTS.get(kind,""),
                      "final":final}},ensure_ascii=False).encode())

    @route('GET', '/api/acting/evals')
    def route_get_api_acting_evals(self, ctx):
        u, q = ctx.url, ctx.query
        # 演员评分历史：扫描 演员/表演对比/*/manifest.json，按分镜名过滤，新的在前。
        d=proj_dir(q.get("project",[""])[0]); nm=safe_proj(q.get("storyboard",[""])[0] or q.get("name",[""])[0])
        out=[]
        root=os.path.join(d,"演员","表演对比") if d else ""
        if nm and root and os.path.isdir(root):
            for exp in sorted(os.listdir(root), reverse=True):
                mp=os.path.join(root,exp,"manifest.json")
                if not os.path.isfile(mp): continue
                try: mf=json.load(open(mp,encoding="utf-8"))
                except Exception: continue
                if str(mf.get("board") or "")!=nm: continue
                out.append({"experiment_id":mf.get("experiment_id"),"created_at":mf.get("created_at"),
                            "board":mf.get("board"),"shots":mf.get("shots"),"modes":mf.get("modes"),
                            "summary":mf.get("summary"),"scores":mf.get("scores") or {}})
                if len(out)>=20: break
        return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"evals":out},ensure_ascii=False).encode())

    @route('GET', '/api/acting/context', '/api/acting/candidates')
    def route_get_api_acting_context(self, ctx):
        u, q = ctx.url, ctx.query
        d=proj_dir(q.get("project",[""])[0]); nm=safe_proj(q.get("storyboard",[""])[0] or q.get("name",[""])[0])
        p=os.path.realpath(os.path.join(d,"分镜",nm)) if d and nm else ""
        if not d or not nm.lower().endswith(".json") or not under(d,p) or not os.path.isfile(p):
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"项目/分镜不合法"},ensure_ascii=False).encode())
        try:
            mod=tools_mod("actor_pipeline.py")
            board,revision=mod._read_board(p)
            candidates=mod.list_candidates(p)
            for item in candidates:
                if item.get("path"):
                    item["path"]=os.path.relpath(item["path"],d).replace("\\","/")
            if u.path=="/api/acting/candidates":
                return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"revision":revision,"candidates":candidates},ensure_ascii=False).encode())
            context=mod.hydrate_actor_cards(board.get("acting_context") or mod.default_context(board, d), d, board)
            main_actors=mod.main_actor_records(board, d) if hasattr(mod, "main_actor_records") else (board.get("actors") or {})
            acting_status=board.get("acting_status") if isinstance(board.get("acting_status"),dict) else {}
            try:
                compiler=tools_mod("prompt_compiler.py")
            except Exception:
                compiler=None
            shots=[]
            for shot in board.get("shots") or []:
                sid=str(shot.get("id") or "")
                actor_ids=mod.actor_ids_for_shot(board, shot, d) if hasattr(mod, "actor_ids_for_shot") else []
                actor_names=[str((main_actors.get(actor_id) or {}).get("name") or actor_id) for actor_id in actor_ids]
                performance=shot.get("performance") if isinstance(shot.get("performance"),dict) else None
                status=str(acting_status.get(sid) or ("ready" if performance and performance.get("status")=="ready" else "pending"))
                if performance and performance.get("status") == "invalid":
                    status="invalid"
                if compiler and performance and performance.get("status") == "ready":
                    try:
                        check=compiler.compile_shot(board, sid, mode="stateful", media_type="video")
                        if any(("过期" in str(warning) or "来源" in str(warning)) for warning in (check.get("warnings") or [])):
                            status="stale"
                    except Exception:
                        pass
                if shot.get("performance_locked") or shot.get("acting_locked"):
                    status="locked"
                shots.append({"id":sid,"dur":shot.get("dur"),"speaker":shot.get("speaker") or "",
                               "action":shot.get("action") or "","prompt":shot.get("prompt") or "",
                               "actor_ids":actor_ids,"actor_names":actor_names,
                               "performance_status":status,"performance_locked":bool(shot.get("performance_locked") or shot.get("acting_locked")),
                               "performance":performance})
            return self._send(200,"application/json; charset=utf-8",json.dumps(
                {"ok":True,"board":nm,"revision":revision,"context":context,
                  "actors":main_actors, "shots":shots,"candidates":candidates},
                ensure_ascii=False).encode())
        except Exception as exc:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":str(exc)},ensure_ascii=False).encode())

    @route('GET', '/api/script/data')
    def route_get_api_script_data(self, ctx):
        u, q = ctx.url, ctx.query
        d=proj_dir(q.get("project",[""])[0])
        if not d:
            return self._send(400,"application/json",json.dumps({"err":"项目不存在"},ensure_ascii=False).encode())
        def _rd(name):
            p=os.path.join(d,"剧本",name)
            return json.load(open(p,encoding="utf-8")) if os.path.isfile(p) else None
        def _ra(name):
            p=os.path.join(d,"素材",name)
            return json.load(open(p,encoding="utf-8")) if os.path.isfile(p) else None
        eps_doc=_rd("分集.json")
        eps=eps_doc.get("episodes") if isinstance(eps_doc,dict) else eps_doc
        script_rev=int(eps_doc.get("rev") or 0) if isinstance(eps_doc,dict) else 0
        chars_data=_ra("人物.json")
        props_data=_ra("道具.json")
        scenes_data=_ra("场景.json")
        if isinstance(chars_data,dict) and isinstance(chars_data.get("characters"),list) and script_repository:
            chars_data["characters"]=[x for x in chars_data["characters"] if not script_repository.is_collective_asset(x)]
        if isinstance(props_data,dict) and isinstance(props_data.get("props"),list):
            # 页面需要同时拿到叙事道具和挂在母素材下的服饰、配饰、组件；
            # 关系归一化会把 owner 等旧字段补成 parent_ref。
            if script_repository:
                props_data["props"]=[x for x in props_data["props"] if script_repository.is_asset_prop(x)]
            else:
                props_data["props"]=[x for x in props_data["props"] if x.get("asset_required", True)]
        relmod=tools_mod("asset_relations.py")
        if relmod:
            normalized,_issues=relmod.normalize_asset_relations({
                "characters": (chars_data or {}).get("characters", []) if isinstance(chars_data,dict) else [],
                "scenes": (scenes_data or {}).get("scenes", []) if isinstance(scenes_data,dict) else [],
                "props": (props_data or {}).get("props", []) if isinstance(props_data,dict) else [],
            })
            chars_data=dict(chars_data) if isinstance(chars_data,dict) else {}
            scenes_data=dict(scenes_data) if isinstance(scenes_data,dict) else {}
            props_data=dict(props_data) if isinstance(props_data,dict) else {}
            chars_data["characters"]=normalized.get("characters", [])
            scenes_data["scenes"]=normalized.get("scenes", [])
            props_data["props"]=normalized.get("props", [])
        mode=eps_doc.get("mode") if isinstance(eps_doc,dict) else None
        # 剧本正文权威源：generated=聚合分集正文；imported=剧本.txt
        script_mod=tools_mod("script_repository.py")
        script=script_mod.load_script(d) if script_mod else ""
        if not script:
            sp=os.path.join(d,"剧本","剧本.txt")
            script=open(sp,encoding="utf-8").read() if os.path.isfile(sp) else ""
        return self._send(200,"application/json; charset=utf-8",json.dumps({
            "script": script, "script_mode": mode, "script_rev": script_rev,
            "episodes": eps, "characters": chars_data, "scenes": scenes_data,
            "props": props_data, "style": _rd("style.json") or {}},ensure_ascii=False).encode())

    @route('GET', '/api/skills')
    def route_get_api_skills(self, ctx):
        u, q = ctx.url, ctx.query
        import importlib.util as _iu
        _sp=_iu.spec_from_file_location("pm_mod",os.path.join(TOOLS,"prompt_modules.py"))
        _pm=_iu.module_from_spec(_sp); _sp.loader.exec_module(_pm)
        return self._send(200,"application/json; charset=utf-8",json.dumps(
            {"skills":skill_lib_list()+_pm.list_system_skills()},ensure_ascii=False).encode())

    @route('GET', '/api/skills/get')
    def route_get_api_skills_get(self, ctx):
        u, q = ctx.url, ctx.query
        sid=q.get("id",[""])[0]
        import importlib.util as _iu
        _sp=_iu.spec_from_file_location("pm_mod",os.path.join(TOOLS,"prompt_modules.py"))
        _pm=_iu.module_from_spec(_sp); _sp.loader.exec_module(_pm)
        syse=[e for e in _pm.list_system_skills() if e["id"]==sid]
        if syse:
            return self._send(200,"application/json; charset=utf-8",json.dumps(
                {"id":sid,"text":syse[0]["text"],"kind":"system","overridden":syse[0]["overridden"]},ensure_ascii=False).encode())
        return self._send(200,"application/json; charset=utf-8",json.dumps(
            {"id":sid,"text":skill_lib_call("load_skill_text",sid),"kind":"style"},ensure_ascii=False).encode())

    @route('GET', '/api/knowledge/list')
    def route_get_api_knowledge_list(self, ctx):
        u, q = ctx.url, ctx.query
        return self._send(200,"application/json; charset=utf-8",json.dumps({
            "skills":knowledge_load(), "cards":knowledge_user()},ensure_ascii=False).encode())

    @route('GET', '/api/knowledge/preview')
    def route_get_api_knowledge_preview(self, ctx):
        u, q = ctx.url, ctx.query
        text=q.get("text",[""])[0]
        return self._send(200,"application/json; charset=utf-8",json.dumps(
            {"hits":knowledge_query(text)},ensure_ascii=False).encode())

    @route('GET', '/api/versions')
    def route_get_api_versions(self, ctx):
        u, q = ctx.url, ctx.query
        rel=q.get("p",[""])[0]
        pth=os.path.normpath(os.path.join(VIDEO,rel))
        if not under(VIDEO,pth):
            return self._send(400,"application/json",json.dumps({"err":"路径越界"},ensure_ascii=False).encode())
        import importlib.util as _iu
        _sp=_iu.spec_from_file_location("versions",os.path.join(TOOLS,"versions.py"))
        m=_iu.module_from_spec(_sp); _sp.loader.exec_module(m)
        return self._send(200,"application/json; charset=utf-8",json.dumps(
            {"versions":m.list_versions(pth)},ensure_ascii=False).encode())

    @route('GET', '/api/watchdog')
    def route_get_api_watchdog(self, ctx):
        u, q = ctx.url, ctx.query
        with self.JLOCK:
            jobs=[{"id":v.get("id"),"step":v.get("step"),"status":v.get("status"),
                   "attempts":v.get("attempts",1)} for v in (self.JOBS or {}).values()
                  if v.get("attempts",1)>1 or v.get("status")=="running"]
        return self._send(200,"application/json; charset=utf-8",json.dumps({
            "interval_min":30,"log":list(getattr(self,"HEARTBEAT_LOG",[])),"active":jobs},ensure_ascii=False).encode())

    @route('GET', '/api/env')
    def route_get_api_env(self, ctx):
        u, q = ctx.url, ctx.query
        return self._send(200,"application/json; charset=utf-8",json.dumps(detect_env(),ensure_ascii=False).encode())

    @route('GET', '/api/comfy/workflows')
    def route_get_api_comfy_workflows(self, ctx):
        u, q = ctx.url, ctx.query
        # 环境页的 ComfyUI API 工作流选择器：只列 workbench/workflows 下的 JSON。
        root=os.path.realpath(os.path.join(TOOLS, "..", "workflows"))
        items=[]
        try:
            os.makedirs(root, exist_ok=True)
            cm=tools_mod("comfyui_client.py")
            for base, dirs, files in os.walk(root):
                dirs[:] = [x for x in dirs if not x.startswith(".")]
                for fn in sorted(files):
                    if not fn.lower().endswith(".json") or fn.startswith("."):
                        continue
                    path=os.path.join(base,fn)
                    rel=os.path.relpath(path,root).replace(os.sep,"/")
                    item={"path":rel,"name":fn,"size":os.path.getsize(path),"refs":[],"has_negative":False,"has_save_image":False}
                    try:
                        graph=cm.load_workflow_template(rel) if cm else {}
                        raw=json.dumps(graph,ensure_ascii=False)
                        item["refs"]=sorted({int(x) for x in re.findall(r"\{\{ref(\d+)\}\}",raw)})
                        item["has_negative"]="{{negative}}" in raw
                        item["has_save_image"]=any(isinstance(n,dict) and n.get("class_type")=="SaveImage" for n in graph.values())
                    except Exception as exc:
                        item["error"]=str(exc)
                    items.append(item)
        except Exception as exc:
            return self._send(500,"application/json",json.dumps({"ok":False,"err":str(exc)},ensure_ascii=False).encode())
        return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"workflows":items},ensure_ascii=False).encode())

    @route('GET', '/api/env/config')
    def route_get_api_env_config(self, ctx):
        u, q = ctx.url, ctx.query
        return self._send(200,"application/json; charset=utf-8",json.dumps({"vendors":vendors_masked()},ensure_ascii=False).encode())

    @route('GET', '/api/env/chrome-use')
    def route_get_api_env_chrome_use(self, ctx):
        u, q = ctx.url, ctx.query
        return self._send_run_json(200,{"ok":True,"status":image_use_runtime.status()})

    @route('GET', '/api/explain/get')
    def route_get_api_explain_get(self, ctx):
        u, q = ctx.url, ctx.query
        d=proj_dir(q.get("project",[""])[0]); nm=safe_proj(q.get("name",[""])[0])
        p=os.path.join(d,"拉片",nm,"讲解.md") if d and nm else ""
        if p and under(d,p) and os.path.isfile(p):
            return self._send(200,"application/json; charset=utf-8",json.dumps({"md":open(p,encoding="utf-8",errors="replace").read()},ensure_ascii=False).encode())
        return self._send(404,"application/json",json.dumps({"err":"讲解不存在（先运行讲解生成）"},ensure_ascii=False).encode())

    @route('GET', '/api/explain/preflight')
    def route_get_api_explain_preflight(self, ctx):
        u, q = ctx.url, ctx.query
        # 讲解前置态：拉片数据 / 关键帧在盘情况 / vision 厂商 / 已有讲解
        d=proj_dir(q.get("project",[""])[0]); nm=safe_proj(q.get("name",[""])[0])
        ad=os.path.join(d,"拉片",nm) if d and nm else ""
        r={"ok":False}
        if ad and os.path.isfile(os.path.join(ad,"analysis.json")):
            try:
                ana=json.load(open(os.path.join(ad,"analysis.json"),encoding="utf-8"))
                shots=ana.get("shots") or []
                kf_total=0; kf_disk=0
                for s in shots:
                    ks=(s.get("keyframes") or [])[:3]; kf_total+=len(ks)
                    kf_disk+=sum(1 for k in ks if os.path.isfile(os.path.join(ad,k)))
                vision=""
                try:
                    pv=json.load(open(PROV,encoding="utf-8"))
                    for v in pv.get("vendors",[]):
                        if v.get("enabled") and (v.get("models") or {}).get("vision") and v.get("base_url") and v.get("api_key"):
                            vision=v.get("id"); break
                except Exception: pass
                md=os.path.join(ad,"讲解.md"); has=os.path.isfile(md)
                r={"ok":True,"shot_count":len(shots),"kf_total":kf_total,"kf_disk":kf_disk,
                   "vision_vendor":vision,"has_doc":has,
                   "doc_mtime":time.strftime("%Y-%m-%d %H:%M:%S",time.localtime(os.path.getmtime(md))) if has else ""}
            except Exception as e:
                r={"ok":False,"err":str(e)}
        return self._send(200,"application/json; charset=utf-8",json.dumps(r,ensure_ascii=False).encode())

    @route('GET', '/api/create')
    def route_get_api_create(self, ctx):
        u, q = ctx.url, ctx.query
        d=proj_dir(q.get("project",[""])[0])
        p=os.path.join(d,"创作","creation.json") if d else ""
        if p and os.path.isfile(p):
            try:
                with open(p,encoding='utf-8') as fp: data=json.load(fp)
                if not isinstance(data,dict) or not isinstance(data.get('items',[]),list) or not all(isinstance(i,dict) for i in data.get('items',[])):
                    raise ValueError('创作索引结构不合法')
            except (ValueError,UnicodeError):
                return self._send_run_json(500,{'ok':False,'err':'创作索引 creation.json 损坏，请从版本恢复；未覆盖原文件','code':'CREATION_INDEX_CORRUPT'})
            data['items'] = [i for i in data.get('items', []) if i.get('type') in ('image', 'video', 'music', 'speech')]
            return self._send_run_json(200, data)
        return self._send(200,"application/json",json.dumps({"items":[]},ensure_ascii=False).encode())

    @route('GET', '/api/create/chatgpt/jobs')
    def route_get_api_create_chatgpt_jobs(self, ctx):
        u, q = ctx.url, ctx.query
        if chatgpt_queue is None:
            return self._send(500,"application/json",json.dumps({"ok":False,"err":"ChatGPT 队列模块缺失"},ensure_ascii=False).encode())
        d=proj_dir(q.get("project",[""])[0])
        if not d:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"project 必填"},ensure_ascii=False).encode())
        try:
            limit=int(q.get("limit",["100"])[0])
            rows=chatgpt_queue.list_jobs(d, board=q.get("board",[None])[0], status=q.get("status",[None])[0], limit=limit)
            return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"jobs":rows},ensure_ascii=False).encode())
        except Exception as exc:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":str(exc)},ensure_ascii=False).encode())

    @route('GET', '/api/create/chatgpt/job')
    def route_get_api_create_chatgpt_job(self, ctx):
        u, q = ctx.url, ctx.query
        if chatgpt_queue is None:
            return self._send(500,"application/json",json.dumps({"ok":False,"err":"ChatGPT 队列模块缺失"},ensure_ascii=False).encode())
        d=proj_dir(q.get("project",[""])[0]); iid=str(q.get("id",[""])[0] or "")
        if not d or not iid:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"project/id 必填"},ensure_ascii=False).encode())
        try:
            return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"job":chatgpt_queue.get_job(d,iid)},ensure_ascii=False).encode())
        except Exception as exc:
            return self._send(404,"application/json",json.dumps({"ok":False,"err":str(exc)},ensure_ascii=False).encode())

    @route('GET', '/api/create/chatgpt/run')
    def route_get_api_create_chatgpt_run(self, ctx):
        u, q = ctx.url, ctx.query
        if chatgpt_run_api is None:
            return self._send_run_json(500,{"ok":False,"err":"ChatGPT 运行接口模块缺失"})
        d=proj_dir(q.get("project",[""])[0]); run_id=str(q.get("run_id",[""])[0] or "")
        if not d:
            return self._send_run_json(400,{"ok":False,"err":"project 必填"})
        try:
            run=image_use_runner.status(d,run_id)
            return self._send_run_json(200,{"ok":True,"run":run})
        except chatgpt_run_api.ApiError as exc:
            return self._send_run_error(exc)

    @route('GET', '/api/storyboard/panel/draft')
    def route_get_api_storyboard_panel_draft(self, ctx):
        u, q = ctx.url, ctx.query
        d=proj_dir(q.get("project",[""])[0]); name=safe_proj(q.get("board",[""])[0])
        pid=safe_proj(q.get("panel_id",[""])[0])
        if not d or not name or not pid:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"画格草稿参数不合法"},ensure_ascii=False).encode())
        filename=os.path.splitext(name)[0]+"_"+pid+".json"
        path=os.path.join(d,"创作","画格草稿",filename)
        if not under(d,path) or not os.path.isfile(path):
            return self._send(404,"application/json",json.dumps({"ok":False,"err":"画格草稿不存在"},ensure_ascii=False).encode())
        return self._send(200,"application/json; charset=utf-8",open(path,"rb").read())

    @route('GET', '/api/create/coverage')
    def route_get_api_create_coverage(self, ctx):
        u, q = ctx.url, ctx.query
        d=proj_dir(q.get("project",[""])[0]); board_name=safe_proj(q.get("board",[""])[0])
        if not d or not board_name:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"project/board 必填"},ensure_ascii=False).encode())
        try:
            nm,board,_=load_storyboard(d,board_name)
            mf=os.path.join(d,"创作","creation.json")
            data=json.load(open(mf,encoding="utf-8")) if os.path.isfile(mf) else {"items":[]}
            mod=tools_mod("creation_store.py")
            mtype=str(q.get("type",["image"])[0])
            if mtype not in ("image","video"): raise ValueError("type 须为 image|video")
            result=mod.coverage_matrix(data.get("items",[]) if isinstance(data,dict) else [],
                                       [s.get("id") for s in board.get("shots",[]) if isinstance(s,dict) and s.get("id")],
                                       media_type=mtype)
            result.update({"ok":True,"board":nm})
            return self._send(200,"application/json; charset=utf-8",json.dumps(result,ensure_ascii=False).encode())
        except Exception as exc:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":str(exc)},ensure_ascii=False).encode())

    def _static_or_not_found(self, ctx):
        u = ctx.url
        if u.path.startswith(("/api", "/src", "/media")):
            return self._send(404, "text/plain", b"404")
        if os.path.isdir(WEBDIST):
            p=os.path.normpath(os.path.join(WEBDIST,u.path.lstrip("/")))
            if under(WEBDIST,p) and os.path.isfile(p):
                ct={".html":"text/html; charset=utf-8",".js":"text/javascript",".css":"text/css",".svg":"image/svg+xml",
                    ".png":"image/png",".jpg":"image/jpeg",".jpeg":"image/jpeg",".json":"application/json",
                    ".woff2":"font/woff2",".woff":"font/woff",".ico":"image/x-icon",".mp4":"video/mp4"}.get(os.path.splitext(p)[1].lower(),"application/octet-stream")
                # 带 hash 的 assets 长缓存；index.html 等入口不缓存（防旧 chunk 引用失效）
                cc={"Cache-Control":"public, max-age=31536000, immutable"} if u.path.startswith("/assets/") \
                    else {"Cache-Control":"no-store, no-cache, must-revalidate","Pragma":"no-cache"}
                return self._send(200,ct,open(p,"rb").read(),cc)
            if not u.path.startswith(("/api/","/api","/src","/media")):
                idx=os.path.join(WEBDIST,"index.html")
                if "text/html" in (self.headers.get("Accept") or "") and os.path.isfile(idx):
                    return self._send(200,"text/html; charset=utf-8",open(idx,"rb").read(),
                        {"Cache-Control":"no-store, no-cache, must-revalidate","Pragma":"no-cache"})
        # 其余静态
        p=os.path.normpath(os.path.join(ROOT,u.path.lstrip("/")))
        if under(ROOT,p) and os.path.isfile(p):
            if _deny_file(p):
                return self._send(403,"text/plain; charset=utf-8","禁止访问敏感文件".encode())
            return self._send(200,"application/octet-stream",open(p,"rb").read())
        self._send(404,"text/plain",b"404")

    @route('POST', '/api/jobs/clear')
    def route_post_api_jobs_clear(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        # 清空已结束的历史任务（done/failed/error/interrupted），running 保留；内存与磁盘 jobs/*.json 同步删。
        cleared=0
        with self.JLOCK:
            done_ids=[jid for jid,j in self.JOBS.items() if j.get("status")!="running"]
            for jid in done_ids: del self.JOBS[jid]
        try:
            for f in os.listdir(JOBSDIR):
                m=re.match(r"job_(\d+)\.json$",f)
                if not m: continue
                p=os.path.join(JOBSDIR,f)
                try:
                    rec=json.load(open(p,encoding="utf-8"))
                except Exception: continue
                if rec.get("status")=="running": continue
                try: os.remove(p); cleared+=1
                except OSError: pass
        except Exception: pass
        cleared=max(cleared,len(done_ids))
        return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"cleared":cleared},ensure_ascii=False).encode())

    @route('POST', '/api/project/new')
    def route_post_api_project_new(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        # 新建项目：type=拆片(默认，配视频) | 制作(纯剧本创作，建 剧本/创作/ 等目录)
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        name=str(body.get("project") or "").replace("/","").replace("\\","").strip()
        ptype=str(body.get("type") or "拆片")
        if ptype not in ("拆片","制作"):
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"type 须为 拆片|制作"},ensure_ascii=False).encode())
        if not name or len(name)>60:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"项目名不合法"},ensure_ascii=False).encode())
        d=os.path.join(VIDEO,"projects",name)
        if os.path.isdir(d):
            return self._send(409,"application/json",json.dumps({"ok":False,"err":"项目已存在:"+name},ensure_ascii=False).encode())
        subs=["拉片素材","分镜","创作","素材","演员","推演","台词"]+(["剧本"] if ptype=="制作" else ["拉片","白模","深度","逐帧","成片"])
        for sub in subs: os.makedirs(os.path.join(d,sub),exist_ok=True)
        json.dump({"type":ptype,"created_at":time.strftime("%Y-%m-%d %H:%M")},
                  open(os.path.join(d,"项目.json"),"w",encoding="utf-8"),ensure_ascii=False,indent=1)
        return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"type":ptype},ensure_ascii=False).encode())

    @route('GET', '/api/media-gateway')
    def route_get_media_gateway(self, ctx):
        from media_gateway import load
        cfg = load()
        return self._send(200, 'application/json', json.dumps({k:cfg[k] for k in ('base_url','ttl_seconds')}).encode())

    @route('POST', '/api/media-gateway')
    def route_post_media_gateway(self, ctx):
        try:
            from media_gateway import save
            cfg = save(json.loads(self.rfile.read(ctx.content_length)))
            return self._send(200, 'application/json', json.dumps({'ok':True, **cfg}).encode())
        except Exception as e: return self._send_run_error(e)

    @route('HEAD', '/api/public-reference')
    def route_head_public_reference(self, ctx):
        return self.route_get_public_reference(ctx)

    @route('GET', '/api/public-reference')
    def route_get_public_reference(self, ctx):
        try:
            from media_gateway import verify
            import mimetypes
            path = verify(os.path.join(VIDEO, 'projects'), {k:v[0] for k,v in ctx.query.items()})
        except Exception:
            return self._send(403, 'text/plain', b'invalid or expired media link')
        with path.open('rb') as stream:
            size = os.fstat(stream.fileno()).st_size
            start, end, status = 0, size - 1, 200
            value = self.headers.get('Range')
            if value:
                match = re.fullmatch(r'bytes=(\d*)-(\d*)', value)
                if not match or not any(match.groups()): return self._send(416,'text/plain',b'invalid range')
                if match[1]:
                    start = int(match[1]); end = min(int(match[2]),end) if match[2] else end
                else: start = max(0,size-int(match[2]))
                if start > end or start >= size: return self._send(416,'text/plain',b'invalid range')
                status = 206
            self.send_response(status)
            self.send_header('Content-Type', mimetypes.guess_type(str(path))[0] or 'application/octet-stream')
            self.send_header('Content-Length',str(max(0,end-start+1)))
            self.send_header('Accept-Ranges','bytes')
            self.send_header('Cache-Control','private, no-store')
            if status == 206: self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
            self.end_headers()
            if self.command == 'HEAD': return
            stream.seek(start); left = end-start+1
            while left > 0:
                chunk = stream.read(min(left, 262144))
                if not chunk: break
                self.wfile.write(chunk); left -= len(chunk)

    @route('GET', '/api/reference-media')
    def route_get_reference_media(self, ctx):
        try:
            from reference_media import list_media
            project = safe_proj(ctx.query.get('project', [''])[0])
            if not project: raise ValueError('请选择项目')
            rows = list_media(proj_dir(project), ctx.query.get('kind', [''])[0])
            return self._send(200, 'application/json', json.dumps({'ok': True, 'items': rows}, ensure_ascii=False).encode())
        except Exception as e:
            return self._send_run_error(e)

    @route('POST', '/api/reference-media/upload')
    def route_post_reference_media_upload(self, ctx):
        try:
            from reference_media import upload
            project = safe_proj(ctx.query.get('project', [''])[0])
            if not project: raise ValueError('请选择项目')
            row = upload(proj_dir(project), ctx.query.get('kind', [''])[0], ctx.query.get('name', [''])[0], self.rfile, ctx.content_length)
            return self._send(200, 'application/json', json.dumps({'ok': True, **row}, ensure_ascii=False).encode())
        except Exception as e:
            self.close_connection = True
            return self._send_run_error(e)

    @route('POST', '/api/import')
    def route_post_api_import(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        proj=q.get("project",[""])[0].replace("/","").replace("\\","")
        fn=os.path.basename(q.get("name",[""])[0])
        if not proj or not fn.lower().endswith((".mp4",".mkv",".mov")):
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"项目名或文件名不合法"}).encode())
        d=os.path.join(VIDEO,"projects",proj,"拉片素材"); os.makedirs(d,exist_ok=True)
        dst=os.path.join(d,fn)
        if os.path.exists(dst): return self._send(409,"application/json",json.dumps({"ok":False,"err":"同名文件已存在:"+fn}).encode())
        with open(dst,"wb") as f:
            left=ln
            while left>0:
                b=self.rfile.read(min(1<<20,left))
                if not b: break
                f.write(b); left-=len(b)
        return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"path":"projects/"+proj+"/拉片素材/"+fn},ensure_ascii=False).encode())

    @route('POST', '/api/import_src')
    def route_post_api_import_src(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        # 浏览器可用版本：POST JSON {project, src}；src 为绝对路径(Downloads/工作区内)或 Downloads 文件名
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        proj=(body.get("project") or "").replace("/","").replace("\\","")
        src=str(body.get("src") or "")
        sp=safe_video_abs(src) if os.path.isabs(src) else safe_dl(os.path.basename(src))
        if not proj or not sp:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"项目或源文件不合法"}).encode())
        d=os.path.join(VIDEO,"projects",proj,"拉片素材"); os.makedirs(d,exist_ok=True)
        fn=os.path.basename(sp); dst=os.path.join(d,fn)
        if os.path.exists(dst): return self._send(409,"application/json",json.dumps({"ok":False,"err":"同名文件已存在:"+fn}).encode())
        try: shutil.copyfile(sp,dst)
        except Exception as e: return self._send(500,"application/json",json.dumps({"ok":False,"err":str(e)}).encode())
        return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"path":"projects/"+proj+"/拉片素材/"+fn},ensure_ascii=False).encode())

    @route('POST', '/api/openblend')
    def route_post_api_openblend(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        # 浏览器可用版本：POST JSON {path: 项目内相对路径}（兼容 p）
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        rel=str(body.get("path") or body.get("p") or "")
        p=os.path.normpath(os.path.join(VIDEO,rel))
        if not under(VIDEO,p) or not os.path.isfile(p) or not p.lower().endswith(".blend"):
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"blend 文件不合法"}).encode())
        try:
            os.startfile(p)
            return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"opened":os.path.basename(p)},ensure_ascii=False).encode())
        except Exception as e:
            return self._send(500,"application/json",json.dumps({"ok":False,"err":str(e)}).encode())

    @route('POST', '/api/file/delete')
    def route_post_api_file_delete(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        # 删除项目内产物（角标按钮）：产物区白名单，素材/ 与项目根目录文件不可删
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        d=proj_dir(body.get("project")); rel=str(body.get("path") or "")
        if not d or not rel:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"参数不合法"},ensure_ascii=False).encode())
        p=os.path.normpath(os.path.join(d,rel.replace("/",os.sep)))
        if not under(d,p) or p==d:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"路径越界"},ensure_ascii=False).encode())
        rel_in=os.path.relpath(p,d).replace(os.sep,"/")
        zone=rel_in.split("/")[0]
        PRODUCT_ZONES={"拉片","分镜","白模","深度","逐帧","成片","三维探索","白模3D","创作","素材","演员","推演","台词"}
        ext=os.path.splitext(p)[1].lower()
        if "/" not in rel_in:
            # 项目根目录散放文件：仅 AI 归属 sidecar（可再生），台词脚本.json 等校对产物不开放角标删除
            if not (rel_in.startswith("AI归属") and ext==".json"):
                return self._send(400,"application/json",json.dumps({"ok":False,"err":"根目录仅支持删除 AI归属*.json"},ensure_ascii=False).encode())
        elif zone=="拉片素材":
            if ext not in (".srt",".txt"):
                return self._send(400,"application/json",json.dumps({"ok":False,"err":"拉片素材/ 内只允许删除 srt/txt 提取产物，源视频不在删除范围"},ensure_ascii=False).encode())
        elif zone not in PRODUCT_ZONES:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"只能删除产物区文件（"+ "/".join(sorted(PRODUCT_ZONES))+"）"},ensure_ascii=False).encode())
        if os.path.isdir(p):
            # 目录删除仅限 逐帧/ 与 创作/（逐帧产物、创作条目目录）；拉片版本目录走「删除版本」（含 _versions 清理）
            if zone not in ("逐帧","创作"):
                return self._send(400,"application/json",json.dumps({"ok":False,"err":"该目录请走对应页面的删除入口（拉片版本删除/创作条目删除）"},ensure_ascii=False).encode())
            shutil.rmtree(p,ignore_errors=True)
            return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"deleted":rel_in,"kind":"dir"},ensure_ascii=False).encode())
        if not os.path.isfile(p) or not p.lower().endswith(MEDIA_EXT):
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"文件不存在或类型不在产物范围"},ensure_ascii=False).encode())
        try: os.remove(p)
        except Exception as e:
            return self._send(500,"application/json",json.dumps({"ok":False,"err":str(e)},ensure_ascii=False).encode())
        return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"deleted":rel_in,"kind":"file"},ensure_ascii=False).encode())

    @route('POST', '/api/job')
    def route_post_api_job(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        jid=int(q.get("id",["0"])[0])
        j=self.job_record(jid)
        return self._send(200,"application/json; charset=utf-8",json.dumps(j or {"err":"无此任务","lost":True},ensure_ascii=False).encode())

    ENV_INSTALL_LOCK = threading.RLock()

    @route('POST', '/api/env/install')
    def route_post_api_env_install(self, ctx):
        try:
            body = json.loads(self.rfile.read(ctx.content_length).decode("utf-8"))
            groups = tools_mod("install_environment.py").normalize_groups(body.get("groups") if isinstance(body, dict) else None)
        except (ValueError, UnicodeError) as exc:
            return self._send(400, "application/json", json.dumps({"err": str(exc)}, ensure_ascii=False).encode())
        with self.ENV_INSTALL_LOCK:
            with self.JLOCK:
                active = any(j.get("status") == "running" or j.get("process_alive") for j in self.JOBS.values())
            if active:
                return self._send(409, "application/json", json.dumps({"err": "请等待运行中的任务结束后安装环境"}, ensure_ascii=False).encode())
            jid = self.spawn_job("environment_install", [sys.executable, os.path.join(TOOLS, "install_environment.py"), *groups])
        return self._send(202, "application/json", json.dumps({"ok": True, "id": jid, "job": True}).encode())

    @route('POST', '/api/env/config')
    def route_post_api_env_config(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        new=body.get("vendors")
        if not isinstance(new,list):
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"vendors 须为数组"},ensure_ascii=False).encode())
        old={v.get("id"):v for v in load_vendors()}
        clean=[]
        for v in new:
            if not isinstance(v,dict) or not str(v.get("id") or "").strip():
                return self._send(400,"application/json",json.dumps({"ok":False,"err":"厂商缺少 id"},ensure_ascii=False).encode())
            vid=str(v["id"]).strip()
            o=old.get(vid,{})
            om=o.get("models") or {}; oe=o.get("endpoints") or {}
            nm=v.get("models") if isinstance(v.get("models"),dict) else {}
            ne=v.get("endpoints") if isinstance(v.get("endpoints"),dict) else {}
            item={"id":vid,"catalog_revision":o.get("catalog_revision",1),
                  "label":str(v.get("label") or o.get("label") or vid),
                  "enabled":bool(v.get("enabled",o.get("enabled",False))),
                  "base_url":str(v.get("base_url") if v.get("base_url") is not None else o.get("base_url","") or ""),
                  "note":str(v.get("note") if v.get("note") is not None else o.get("note","") or ""),
                  "models":{k:str(nm.get(k) if nm.get(k) is not None else om.get(k,"") or "") for k in KINDS},
                  "endpoints":{k:str(ne.get(k) if ne.get(k) is not None else oe.get(k,"") or "") for k in KINDS},
                  "reference_limit": (v.get("reference_limit") if v.get("reference_limit") is not None else o.get("reference_limit"))
                }
            k=str(v.get("api_key") or "").strip()
            item["api_key"]="" if looks_masked(k) else k   # 掩码值视为未改动
            item["api_key"]=item["api_key"] or o.get("api_key","")   # 空 key 保留旧值
            # 扩展字段（如 doubao 语音 ASR 的 TOS/IAM 配置）：密钥类空值保留旧值，其余直取（可清空）
            oe=o.get("extra") if isinstance(o.get("extra"),dict) else {}
            nenv=v.get("extra") if isinstance(v.get("extra"),dict) else {}
            secret_extra=("asr_api_key","asr_ak","asr_sk","speech_api_key")
            keys=set(oe)|set(nenv)
            if keys:
                item["extra"]={}
                for kk in keys:
                    nv=nenv.get(kk)
                    if kk in secret_extra:
                        sv=str(nv).strip() if nv else ""
                        item["extra"][kk]=("" if looks_masked(sv) else sv) or str(oe.get(kk,"") or "")
                    else:
                        item["extra"][kk]=str(nv if nv is not None else oe.get(kk,"") or "")
            clean.append(item)
        out={"vendors":normalize_vendors(clean)}
        try:
            legacy=json.load(open(PROV,encoding="utf-8")).get("providers_legacy")
            if isinstance(legacy,list): out["providers_legacy"]=legacy
        except Exception: pass
        json.dump(out,open(PROV,"w",encoding="utf-8"),ensure_ascii=False,indent=1)
        return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"count":len(clean)},ensure_ascii=False).encode())

    @route('POST', '/api/env/test')
    def route_post_api_env_test(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        vid=str(body.get("id") or "")
        kind=str(body.get("kind") or "").strip() or None
        draft=body.get("draft") if isinstance(body.get("draft"),dict) and body.get("draft") else None
        if kind and kind not in KINDS:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"kind 须为 text/vision/image/image_edit/video/music/speech"},ensure_ascii=False).encode())
        v=next((x for x in load_vendors() if x.get("id")==vid),None)
        if not v and not draft:
            return self._send(404,"application/json",json.dumps({"ok":False,"err":"厂商不存在"},ensure_ascii=False).encode())
        v=vendor_with_draft(v,draft)   # 有 draft 用草稿配置（api_key 空回落落盘）
        # 测试允许未启用配置；启用只控制生成入口。
        v = dict(v, enabled=True)
        models=v.get("models") or {}
        if vid=="chatgpt-queue":
            state=image_use_runtime.status()
            return self._send_run_json(200,{"ok":state["installed"],"kind":"image","note":state["note"],"err":"请先安装 chrome-use" if not state["installed"] else ""})
        if vid=="local-comfyui" and (kind in (None,"image","image_edit","video")):
            try:
                af=tools_mod("llm_openai.py")
                client=af.VendorClient.from_config(v)
                from comfyui_client import ComfyUIClient
                adapter=ComfyUIClient(v.get("base_url"),v.get("api_key",""))
                adapter.probe(timeout=8)
                available=client.list_models(timeout=8)
                selected=models.get(kind or "image")
                if not selected or selected not in available:
                    raise ValueError("ComfyUI 模型未配置或不在 diffusion_models 中")
                if kind=="video":
                    required={"MiniMaxH3ImageToVideo","MiniMaxH3ReferenceToVideo","SaveVideo"}
                    nodes=adapter._json("GET","/object_info",timeout=15)
                    if not required.issubset(nodes):
                        raise ValueError("ComfyUI 缺少 H3 视频节点")
                return self._send(200,"application/json; charset=utf-8",json.dumps(
                    {"ok":True,"kind":kind or "image","note":"ComfyUI 已连接；模型可用，未提交生成任务"},ensure_ascii=False).encode())
            except Exception as exc:
                return self._send(200,"application/json; charset=utf-8",json.dumps(
                    {"ok":False,"kind":kind or "image","err":scrub_err(exc)},ensure_ascii=False).encode())
        if kind:   # 指定能力：image/video/music 只校验配置，text/vision 发最小 chat
            if kind in ("image","image_edit","video","music","speech"):
                missing=[x for x in ("base_url","api_key") if not v.get(x)]
                if not models.get(kind): missing.append(f"models.{kind}")
                if missing:
                    return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":False,"err":"配置不完整，缺: "+"、".join(missing)},ensure_ascii=False).encode())
                if vid == "doubao" and kind == "video":
                    try:
                        from llm_openai import VendorClient
                        VendorClient.from_config(v).validate_video_config()
                    except Exception as exc:
                        return self._send_run_json(200, {"ok":False,"err":scrub_err(exc)})
                return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"note":"仅校验配置（该能力走异步生成接口，由生成任务实测）"},ensure_ascii=False).encode())
            if not models.get(kind):
                return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":False,"err":f"厂商未配置 {kind} 模型"},ensure_ascii=False).encode())
        else:      # 未指定：vision -> text 顺序挑有模型的能力测
            kind=next((k for k in ("vision","text") if models.get(k)),None)
            if not kind:
                return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":False,"err":"该厂商未配置 vision/text 模型，可用 kind 参数指定其他能力"},ensure_ascii=False).encode())
        try:
            af=tools_mod("llm_openai.py")
            if not af:
                return self._send(500,"application/json",json.dumps({"ok":False,"err":"llm_openai.py 缺失"},ensure_ascii=False).encode())
            t0=time.time()
            af.VendorClient.from_config(v).chat([{"role":"user","content":"ping"}],kind=kind,max_tokens=1,timeout=15)
            return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"kind":kind,"latency_ms":int((time.time()-t0)*1000)},ensure_ascii=False).encode())
        except Exception as e:
            return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":False,"kind":kind,"err":scrub_err(e)},ensure_ascii=False).encode())

    @route('POST', '/api/env/models')
    def route_post_api_env_models(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        vid=str(body.get("id") or "")
        draft=body.get("draft") if isinstance(body.get("draft"),dict) and body.get("draft") else None
        if draft:
            v=next((x for x in load_vendors() if x.get("id")==vid),{})
            base=str(draft.get("base_url") or "").strip()
            key=str(draft.get("api_key") or "").strip()
            if not key and vid:   # draft 未带 key 时回落落盘 key
                v=next((x for x in load_vendors() if x.get("id")==vid),None)
                if v: key=v.get("api_key","")
            if not base and vid!="chatgpt-queue":
                return self._send(400,"application/json",json.dumps({"ok":False,"err":"草稿缺少 base_url"},ensure_ascii=False).encode())
            if not key and vid not in ("local-comfyui","chatgpt-queue"):
                return self._send(400,"application/json",json.dumps({"ok":False,"err":"草稿未配置 api_key，且落盘配置中也没有可用 key"},ensure_ascii=False).encode())
            cfg={"id":vid or "draft","base_url":base,"api_key":key,"enabled":True,
                 "models":(draft.get("models") or (v or {}).get("models") or {}),"endpoints":{}}
        else:
            v=next((x for x in load_vendors() if x.get("id")==vid),None)
            if not v:
                return self._send(404,"application/json",json.dumps({"ok":False,"err":"厂商不存在"},ensure_ascii=False).encode())
            if not v.get("api_key") and vid not in ("local-comfyui","chatgpt-queue"):
                return self._send(400,"application/json",json.dumps({"ok":False,"err":"厂商未配置 api_key，请先在厂商设置中填写"},ensure_ascii=False).encode())
            cfg=v
        try:
            af=tools_mod("llm_openai.py")
            if not af:
                return self._send(500,"application/json",json.dumps({"ok":False,"err":"llm_openai.py 缺失"},ensure_ascii=False).encode())
            models=af.VendorClient.from_config(cfg,check_enabled=False).list_models(timeout=15)
            return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"models":models},ensure_ascii=False).encode())
        except Exception as e:
            return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":False,"err":scrub_err(e)},ensure_ascii=False).encode())

    @route('POST', '/api/project/normalize')
    def route_post_api_project_normalize(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        d=proj_dir(body.get("project"))
        if not d:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"项目不存在"},ensure_ascii=False).encode())
        nm=tools_mod("normalize_project.py")
        if not nm:
            return self._send(500,"application/json",json.dumps({"ok":False,"err":"normalize_project.py 缺失"},ensure_ascii=False).encode())
        try:
            ok,plan=nm.normalize(d,apply=bool(body.get("apply")))
            return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":ok,"apply":bool(body.get("apply")),"plan":plan},ensure_ascii=False).encode())
        except Exception as e:
            return self._send(500,"application/json",json.dumps({"ok":False,"err":scrub_err(e)},ensure_ascii=False).encode())

    @route('POST', '/api/explain/run')
    def route_post_api_explain_run(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        d=proj_dir(body.get("project")); nm=safe_proj(body.get("name"))
        ad=os.path.join(d,"拉片",nm) if d and nm else ""
        if not d or not nm or not under(os.path.join(d,"拉片"),os.path.realpath(ad)) or not os.path.isfile(os.path.join(ad,"analysis.json")):
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"项目或分析版本不存在"},ensure_ascii=False).encode())
        if not tools_mod("explain_shots.py"):
            return self._send(500,"application/json",json.dumps({"ok":False,"err":"explain_shots.py 缺失"},ensure_ascii=False).encode())
        cmd=[sys.executable,os.path.join(TOOLS,"explain_shots.py"),ad,"--providers",PROV]
        vendor=str(body.get("vendor_id") or body.get("provider") or "")
        if vendor: cmd+=["--vendor",vendor]
        jid=self.spawn_job("explain",cmd)
        return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"id":jid,"job":True},ensure_ascii=False).encode())

    @route('POST', '/api/create/prompt/regenerate')
    def route_post_api_create_prompt_regenerate(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        d=proj_dir(body.get("project"))
        board_name=safe_proj(body.get("board"))
        shot_id=str(body.get("shot_id") or "").strip()
        media_type=str(body.get("type") or "image").strip()
        if not d or not board_name or not shot_id or media_type not in ("image","video"):
            return self._send(400,"application/json; charset=utf-8",json.dumps({"ok":False,"err":"project/board/shot_id/type 参数不合法"},ensure_ascii=False).encode())
        try:
            load_storyboard(d,board_name)
        except ValueError as exc:
            return self._send(400,"application/json; charset=utf-8",json.dumps({"ok":False,"err":str(exc)},ensure_ascii=False).encode())
        cmd=[sys.executable,os.path.join(TOOLS,"regenerate_shot_prompt.py"),d,
             "--board",board_name,"--shot",shot_id,"--type",media_type]
        jid=self.spawn_job("prompt_regenerate",cmd)
        return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"id":jid,"job":True},ensure_ascii=False).encode())

    @route('POST', '/api/storyboard/panel/draft/generate')
    def route_post_api_storyboard_panel_draft_generate(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        d=proj_dir(body.get("project")); name=safe_proj(body.get("board"))
        pid=safe_proj(body.get("panel_id"))
        if not d or not name or not pid:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"画格草稿参数不合法"},ensure_ascii=False).encode())
        try:
            _,board,_=load_storyboard(d,name)
            if not any(isinstance(x,dict) and x.get("id")==pid for x in board.get("panels",[])):
                raise ValueError("画格不存在")
        except Exception as exc:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":str(exc)},ensure_ascii=False).encode())
        cmd=[sys.executable,os.path.join(TOOLS,"generate_panel_draft.py"),d,
             "--board",name,"--panel",pid]
        jid=self.spawn_job("panel_draft",cmd)
        return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"id":jid,"job":True},ensure_ascii=False).encode())

    @route('POST', '/api/storyboard/grid/save')
    def route_post_api_storyboard_grid_save(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        d=proj_dir(body.get("project")); name=safe_proj(body.get("board"))
        grid=body.get("grid")
        if not d or not name or not isinstance(grid,dict):
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"宫格参数不合法"},ensure_ascii=False).encode())
        try:
            mod=tools_mod("storyboard_panel_service.py")
            board=mod.save_grid(d,name,grid)
            return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"grids":board["grids"]},ensure_ascii=False).encode())
        except Exception as exc:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":str(exc)},ensure_ascii=False).encode())

    @route('POST', '/api/storyboard/panel/save')
    def route_post_api_storyboard_panel_save(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        d=proj_dir(body.get("project")); name=safe_proj(body.get("board"))
        panel=body.get("panel")
        if not d or not name or not isinstance(panel,dict):
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"project/board/panel 参数不合法"},ensure_ascii=False).encode())
        try:
            mod=tools_mod("storyboard_panel_service.py")
            board=mod.save_panel(d,name,panel)
            return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"panel":next(x for x in board["panels"] if x.get("id")==panel["id"])},ensure_ascii=False).encode())
        except Exception as exc:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":str(exc)},ensure_ascii=False).encode())

    @route('POST', '/api/storyboard/panel/preview')
    def route_post_api_storyboard_panel_preview(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        d=proj_dir(body.get("project")); name=safe_proj(body.get("board"))
        pid=str(body.get("panel_id") or "")
        draft=body.get("draft"); refs=body.get("refs") or []
        if not d or not name or not pid or not isinstance(draft,dict) or not isinstance(refs,list):
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"画格预览参数不合法"},ensure_ascii=False).encode())
        try:
            _,board,_=load_storyboard(d,name)
            panel=next((x for x in board.get("panels",[]) if isinstance(x,dict) and x.get("id")==pid),None)
            if panel is None: raise ValueError("画格不存在")
            mod=tools_mod("storyboard_panel_service.py")
            request=mod.preview_panel(d,panel,draft,refs)
            return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"compiled_request":request},ensure_ascii=False).encode())
        except Exception as exc:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":str(exc)},ensure_ascii=False).encode())

    @route('POST', '/api/create/assemble')
    def route_post_api_create_assemble(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        d=proj_dir(body.get("project")); board_name=body.get("board"); shot_id=body.get("shot_id")
        if not d or not board_name or not shot_id:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"project/board/shot_id 必填"},ensure_ascii=False).encode())
        try:
            bundle=assemble_create_shot(d,board_name,shot_id,body.get("prompt_user"),body.get("type") or "image", body.get("vendor_id") or body.get("provider_id") or "", body.get("mode") or "generate")
            return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,**bundle},ensure_ascii=False).encode())
        except Exception as exc:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":str(exc)},ensure_ascii=False).encode())

    @route('POST', '/api/create/chatgpt/queue')
    def route_post_api_create_chatgpt_queue(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        if chatgpt_queue is None:
            return self._send(500,"application/json",json.dumps({"ok":False,"err":"ChatGPT 队列模块缺失"},ensure_ascii=False).encode())
        try:
            body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or "{}")
            d=proj_dir(body.get("project")); board_name=safe_proj(body.get("board"))
            shot_ids=body.get("shot_ids") or []
            if not d or not board_name or not isinstance(shot_ids,list) or len(shot_ids)>200:
                raise ValueError("project/board/shot_ids 参数不合法")
            if str(body.get("type") or "image") != "image":
                raise ValueError("ChatGPT 队列 V1 只支持图片")
            jobs=chatgpt_queue.queue_shots(d,board_name,shot_ids,str(body.get("task_type") or "reference_image"))
            return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"jobs":jobs},ensure_ascii=False).encode())
        except (chatgpt_queue.QueueError, ValueError) as exc:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":str(exc)},ensure_ascii=False).encode())
        except Exception as exc:
            return self._send(500,"application/json",json.dumps({"ok":False,"err":str(exc)},ensure_ascii=False).encode())

    @route('POST', '/api/asset/chatgpt/queue')
    def route_post_api_asset_chatgpt_queue(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        if chatgpt_queue is None:
            return self._send(500,"application/json",json.dumps({"ok":False,"err":"ChatGPT 队列模块缺失"},ensure_ascii=False).encode())
        try:
            body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or "{}")
            d=proj_dir(body.get("project")); refs=body.get("asset_refs") or []
            if not d or not isinstance(refs,list) or len(refs)>500:
                raise ValueError("project/asset_refs 参数不合法")
            jobs=chatgpt_queue.queue_assets(d, refs)
            return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"jobs":jobs},ensure_ascii=False).encode())
        except (chatgpt_queue.QueueError, ValueError) as exc:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":str(exc)},ensure_ascii=False).encode())
        except Exception as exc:
            return self._send(500,"application/json",json.dumps({"ok":False,"err":str(exc)},ensure_ascii=False).encode())

    @route('POST', '/api/create/chatgpt/run/create', '/api/create/chatgpt/run/import', '/api/create/chatgpt/run/control')
    def route_post_api_create_chatgpt_run_create(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        if chatgpt_run_api is None:
            return self._send_run_json(500,{"ok":False,"err":"ChatGPT 运行接口模块缺失"})
        try:
            body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or "{}")
            d=proj_dir(body.get("project"))
            if not d:
                return self._send_run_json(400,{"ok":False,"err":"project 必填"})
            token=self._run_token()
            if u.path=="/api/create/chatgpt/run/create":
                if not image_use_runtime.status()['installed']:
                    return self._send_run_json(409,{"ok":False,"err":"请先安装 chrome-use / image-use"})
                result=chatgpt_run_api.create_run(d,body)
                try: image_use_runner.start(d,result['run_id'],result['run_token'])
                except Exception as exc:
                    image_use_runner.control(d,result['run_id'],result['run_token'],'cancel')
                    return self._send_run_error(exc)
                return self._send_run_json(200,{"ok":True,"run":result})
            if u.path=="/api/create/chatgpt/run/import":
                result=chatgpt_run_api.import_result(d,body,token)
                return self._send_run_json(200,{"ok":True,"result":result})
            if u.path=="/api/create/chatgpt/run/control":
                result=image_use_runner.control(d,body.get("run_id"),token,body.get("action"))
                return self._send_run_json(200,{"ok":True,"run":result})
            return self._send_run_json(404,{"ok":False,"err":"运行接口不存在"})
        except chatgpt_run_api.ApiError as exc:
            return self._send_run_error(exc)
        except Exception as exc:
            return self._send_run_error(exc)

    @route('POST', '/api/create/chatgpt/import')
    def route_post_api_create_chatgpt_import(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        if chatgpt_import is None:
            return self._send(500,"application/json",json.dumps({"ok":False,"err":"ChatGPT 导入模块缺失"},ensure_ascii=False).encode())
        try:
            ctype=str(self.headers.get("Content-Type") or "")
            raw=self.rfile.read(ln)
            project=""
            manifest_or_zip=None
            files={}
            if ctype.lower().startswith("multipart/form-data"):
                header=(f"Content-Type: {ctype}\r\nMIME-Version: 1.0\r\n\r\n").encode("utf-8")
                message=BytesParser(policy=email_default_policy).parsebytes(header+raw)
                for part in message.iter_parts():
                    name=part.get_param("name", header="content-disposition")
                    filename=part.get_filename()
                    payload=part.get_payload(decode=True) or b""
                    if name=="project":
                        project=payload.decode("utf-8","replace").strip()
                    elif filename:
                        safe_name=str(filename).replace("\\","/").lstrip("/")
                        if os.path.basename(safe_name)=="manifest.json" and not safe_name.startswith("images/"):
                            try: manifest_or_zip=json.loads(payload.decode("utf-8"))
                            except Exception: manifest_or_zip=payload
                        elif filename.lower().endswith(".zip"):
                            manifest_or_zip=payload
                        else:
                            files[safe_name]=payload
            else:
                body=json.loads(raw.decode("utf-8","replace") or "{}")
                project=str(body.get("project") or "")
                if body.get("package_base64"):
                    manifest_or_zip=base64.b64decode(body["package_base64"])
                else:
                    manifest_or_zip=body.get("manifest")
                    encoded=body.get("files") if isinstance(body.get("files"),dict) else {}
                    files={str(k):base64.b64decode(v) for k,v in encoded.items()}
            d=proj_dir(project)
            if not d or manifest_or_zip is None:
                raise ValueError("project 和生成包必填")
            result=chatgpt_import.import_package(d,manifest_or_zip,files)
            return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,**result},ensure_ascii=False).encode())
        except chatgpt_import.ImportError as exc:
            return self._send(422,"application/json",json.dumps({"ok":False,"err":str(exc)},ensure_ascii=False).encode())
        except Exception as exc:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":str(exc)},ensure_ascii=False).encode())

    @route('POST', '/api/create/batch')
    def route_post_api_create_batch(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        d=proj_dir(body.get("project")); board_name=safe_proj(body.get("board")); mtype=body.get("type") or "image"
        image_mode=str(body.get("mode") or "generate").strip().lower()
        if image_mode not in ("generate", "edit") or (image_mode == "edit" and mtype != "image"):
            image_mode = "generate"
        vendor_id=str(body.get("vendor_id") or body.get("provider_id") or "")
        if not d or not board_name or not vendor_id or mtype not in ("image","video"):
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"project/board/type/vendor_id 参数不合法"},ensure_ascii=False).encode())
        if not next((x for x in load_vendors() if x.get("id")==vendor_id and x.get("enabled")),None):
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"厂商不存在或未启用"},ensure_ascii=False).encode())
        if vendor_id == "doubao" and mtype == "video":
            try:
                from llm_openai import VendorClient
                VendorClient.from_config(next(x for x in load_vendors() if x.get("id")==vendor_id)).validate_video_config()
            except Exception as exc:
                return self._send_run_json(400, {"ok":False,"err":scrub_err(exc)})
        cmd=[sys.executable,os.path.join(TOOLS,"create_batch.py"),"--project",d,"--board",board_name,
             "--type",mtype,"--mode",image_mode,"--vendor",vendor_id,"--providers",PROV]
        shot_ids=body.get("shot_ids") or []
        if not isinstance(shot_ids,list) or len(shot_ids)>200:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"shot_ids 须为数组且数量合理"},ensure_ascii=False).encode())
        for sid in shot_ids:
            if str(sid).strip(): cmd += ["--shot-id",str(sid)]
        jid=self.spawn_job("create_batch",cmd)
        return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"id":jid,"job":True},ensure_ascii=False).encode())

    @route('GET', '/api/studio/settings')
    def route_get_studio_settings(self, ctx):
        try:
            mod = tools_mod('production_studio.py')
            return self._send_run_json(200, {'ok': True, **mod.load_settings()})
        except Exception as exc:
            return self._send_run_json(500, {'ok': False, 'err': scrub_err(exc)})

    @route('POST', '/api/studio/settings')
    def route_post_studio_settings(self, ctx):
        body = json.loads(self.rfile.read(ctx.content_length).decode('utf-8', 'replace') or b'{}')
        try:
            mod = tools_mod('production_studio.py')
            data = mod.save_settings(body)
            return self._send_run_json(200, {'ok': True, **data})
        except Exception as exc:
            return self._send_run_json(400, {'ok': False, 'err': scrub_err(exc)})

    @route('GET', '/api/studio/data', '/api/studio/voices')
    def route_get_production_studio(self, ctx):
        q = ctx.query
        d = proj_dir((q.get('project') or [''])[0])
        try:
            if not d: raise ValueError('项目不存在')
            if ctx.url.path.endswith('/voices'):
                cfg = next((v for v in load_vendors() if v.get('id') == 'minimax'), None)
                result = tools_mod('voice_assets.py').state(d, cfg)
            else:
                result = tools_mod('production_studio.py').state(d, (q.get('board') or [''])[0])
                resolver = tools_mod('prompt_assembler.py')
                previews = []
                for shot in result['board'].get('shots', []):
                    for row in resolver.resolve_shot_refs(shot, d, actors=result['board'].get('actors'),
                            board_name=(q.get('board') or [''])[0], max_refs=100, board=result['board']):
                        previews.append(dict(row, shot_id=shot['id']))
                result['asset_previews'] = previews
                result['capabilities'] = {v['id']: tools_mod('production_requests.py').capabilities(v)
                                          for v in load_vendors() if v.get('enabled')}
            return self._send_run_json(200, result)
        except Exception as exc:
            return self._send_run_json(400, {'ok': False, 'err': scrub_err(exc)})

    @route('POST', '/api/studio/job', '/api/studio/save', '/api/studio/adopt', '/api/studio/voice-bind')
    def route_post_production_studio(self, ctx):
        body = json.loads(self.rfile.read(ctx.content_length).decode('utf-8') or '{}')
        d = proj_dir(body.get('project'))
        try:
            if not d: raise ValueError('项目不存在')
            mod = tools_mod('production_studio.py')
            if ctx.url.path.endswith('/job'):
                result = tools_mod('production_jobs.py').enqueue(d, body, self.spawn_job, PROV)
            elif ctx.url.path.endswith('/adopt'):
                result = tools_mod('production_jobs.py').adopt(d, body)
            elif ctx.url.path.endswith('/voice-bind'):
                result = tools_mod('voice_assets.py').bind(d, body)
            else:
                def mutate(board):
                    previous_shots = [dict(s) for s in board['shots']]
                    if body.get('shots') is not None:
                        patches = body['shots']
                        if [s.get('id') for s in patches] != [s['id'] for s in board['shots']]:
                            raise ValueError('镜头顺序或数量已变化，请刷新')
                        board['shots'] = mod.merge_shots(board, patches)
                    if body.get('scope') == 'S':
                        shot = next((s for s in board['shots'] if s['id'] == body.get('target')), None)
                        if not shot: raise ValueError('转场镜头不存在')
                        patch = {k: v for k, v in (body.get('patch') or {}).items()
                                 if k in ('prompt_image', 'prompt_video', 'prompt_grid', 'negative', 'video_duration', 'generation_options')}
                        updated = mod.merge_shots({'shots': [shot]}, [{'id': shot['id'], **patch}])[0]
                        shot.update(updated)
                    elif body.get('initialize'):
                        if board.get('video_units'): raise ValueError('已存在 V 编排，请使用合并/拆分')
                        board['video_units'] = mod.default_units(board)
                    elif body.get('units') is not None:
                        units = mod.clean_units(board, body['units'])
                        for unit in units:
                            for field in ('timeline', 'label', 'stale'): unit.pop(field, None)
                            if unit['id'] == body.get('confirm_summary'):
                                unit['source_hash'] = mod.source_hash(mod.shot_list(board, unit))
                        board['video_units'] = units
                    elif body.get('shots') is None: raise ValueError('保存内容为空')
                    mod.sync_shot_durations(board, previous_shots)
                mod.project_store.update_json(mod.board_path(d, body['board']), mutate,
                    expected_revision=body.get('revision'), snapshot=mod.versions.snapshot)
                result = {'ok': True}
            return self._send_run_json(200, result)
        except Exception as exc:
            return self._send_run_json(409 if exc.__class__.__name__ == 'RevisionConflict' else 400 if isinstance(exc, ValueError) else 500,
                                       {'ok': False, 'err': scrub_err(exc)})

    @route('POST', '/api/create/run')
    def route_post_api_create_run(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        d=proj_dir(body.get("project"))
        mtype=body.get("type"); image_mode=str(body.get("mode") or "generate").strip().lower()
        if image_mode not in ("generate", "edit") or mtype != "image":
            image_mode = "generate"
        prompt_user=str(body.get("prompt_user") or body.get("prompt") or "").strip()
        prompt_assembled=str(body.get("prompt_assembled") or "").strip()
        prompt_json=body.get("prompt_json") if isinstance(body.get("prompt_json"),dict) else None
        asset_refs=body.get("asset_refs") if isinstance(body.get("asset_refs"),list) else []
        negative=str(body.get("negative") or "").strip()
        board_name=safe_proj(body.get("board")); shot_id=str(body.get("shot_id") or "").strip()
        vendor_id=str(body.get("vendor_id") or body.get("provider_id") or "")
        refs=body.get("refs") or []
        compiled_request=None
        panel_id=str(body.get("panel_id") or "").strip()
        if panel_id:
            if mtype!="image" or not d or not board_name:
                return self._send(400,"application/json",json.dumps({"ok":False,"err":"画格只支持分镜生图"},ensure_ascii=False).encode())
            draft=body.get("panel_draft")
            if not isinstance(draft,dict):
                return self._send(400,"application/json",json.dumps({"ok":False,"err":"panel_draft 必须是对象"},ensure_ascii=False).encode())
            try:
                _,panel_board,_=load_storyboard(d,board_name)
                panel=next((x for x in panel_board.get("panels",[]) if isinstance(x,dict) and x.get("id")==panel_id),None)
                if panel is None: raise ValueError("画格不存在")
                if shot_id and shot_id not in panel.get("shot_ids",[]): raise ValueError("画格未关联当前镜头")
                mod=tools_mod("storyboard_panel_service.py")
                compiled_request=mod.preview_panel(d,panel,draft,refs)
                prompt_user=compiled_request["prompt"]
                prompt_assembled=compiled_request["prompt"]
                negative=compiled_request["negative_prompt"]
                refs=compiled_request["image_refs"]
            except Exception as exc:
                return self._send(400,"application/json",json.dumps({"ok":False,"err":str(exc)},ensure_ascii=False).encode())
        if not compiled_request:
            try:
                mention_mod=tools_mod("create_ref_mentions.py")
                refs=mention_mod.select_mentioned_refs(prompt_user,refs)
            except ValueError as exc:
                return self._send(400,"application/json",json.dumps({"ok":False,"err":str(exc)},ensure_ascii=False).encode())
        bundle=None   # 无 board/shot_id 时跳过分镜装配，下游引用一律走 (bundle or {}) 兜底
        if d and board_name and shot_id and mtype in ("image", "video"):
            try:
                bundle=assemble_create_shot(d,board_name,shot_id,prompt_user,mtype,vendor_id,image_mode)
                if not compiled_request and not prompt_assembled: prompt_assembled=bundle.get("prompt_assembled","")
                if not prompt_json: prompt_json=bundle.get("prompt_json")
                if not asset_refs: asset_refs=bundle.get("asset_refs") or []
                if not compiled_request and not negative: negative=bundle.get("negative","")
                if not compiled_request and not refs and not body.get("refs"): refs=[]
            except Exception as exc:
                return self._send(400,"application/json",json.dumps({"ok":False,"err":str(exc)},ensure_ascii=False).encode())
        # prompt 是本次实际发送的完整文本；prompt_user/prompt_assembled 仅作可追溯分层，
        # 避免用户编辑装配结果后被服务端重复拼接。
        prompt=prompt_user or prompt_assembled
        if not d or mtype not in ("image","video","music","speech") or not prompt or not vendor_id:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"参数不合法（project/type/prompt/vendor_id 必填）"},ensure_ascii=False).encode())
        vendor_cfg = next((v for v in load_vendors() if v.get("id") == vendor_id and v.get("enabled")), None)
        binding = {}
        try:
            media_mod = tools_mod("creation_media.py")
            if mtype in ("music", "speech"):
                if vendor_id != "minimax": raise ValueError("当前音乐/语音执行器支持 MiniMax，请选择已适配厂商")
                if refs: raise ValueError("音乐和语音不接受图片参考，请移除图片")
                if mtype == "speech":
                    binding = media_mod.speech_binding(d, str(body.get("character_id") or ""), str(body.get("voice_id") or ""), vendor_id)
                else:
                    board_name = ""; shot_id = ""
            model_slot = "image_edit" if mtype == "image" and image_mode == "edit" else mtype
            if mtype == "image":
                selected_slot = str(body.get("model_slot") or ("image_edit" if body.get("mode") == "edit" else ""))
                model_slot, image_mode, model_name = media_mod.image_route(vendor_cfg or {}, bool(refs), selected_slot)
            else:
                model_name = ((vendor_cfg or {}).get("models") or {}).get(model_slot, "")
        except ValueError as exc:
            return self._send_run_json(400, {"ok":False,"err":str(exc)})
        if not model_name:
            return self._send(400, "application/json",
                json.dumps({"ok":False, "err":f"厂商未配置 {model_slot} 模型"}, ensure_ascii=False).encode())
        if vendor_id == "doubao" and mtype == "video":
            try:
                from llm_openai import VendorClient
                VendorClient.from_config(vendor_cfg).validate_video_config(model_name)
            except Exception as exc:
                return self._send_run_json(400, {"ok":False,"err":scrub_err(exc)})
        # ComfyUI 的普通 Z-Image 内置图没有参考图输入位，因此“生图”模式
        # 仍需自定义参考图工作流；“改图”模式直接使用已选的 image_edit
        # 模型和内置 Qwen Image Edit 链路，自定义工作流只是可选覆盖。
        # 在创建创作条目之前阻断，避免先写入失败任务再由子进程报错。
        if vendor_id == "local-comfyui" and mtype == "image" and refs:
            extra_cfg = (vendor_cfg or {}).get("extra") if isinstance((vendor_cfg or {}).get("extra"), dict) else {}
            if image_mode == "edit":
                # image_edit 模型已在上面校验；空 workflow_path 代表内置图。
                workflow_path = str(extra_cfg.get("image_edit_workflow_path") or "").strip()
            else:
                workflow_path = str(extra_cfg.get("workflow_path") or "").strip()
            if image_mode != "edit" and not workflow_path:
                return self._send(400, "application/json",
                    json.dumps({"ok":False, "err":"ComfyUI 生图参考图工作流未配置；请在环境页选择 API 工作流，并在 LoadImage.image 中设置 {{{{ref1}}}}、{{{{ref2}}}}… 占位符后再生成。"}, ensure_ascii=False).encode())
        if vendor_id == "local-comfyui" and mtype == "image" and image_mode == "edit" and not refs:
            return self._send(400, "application/json", json.dumps({"ok":False, "err":"ComfyUI 改图必须至少引用 1 张参考图"}, ensure_ascii=False).encode())
        # 豆包 Agent Plan 的视频任务端点不接受 SD1.5/SDXL 这类本地扩散模型名。
        # 在写入创作清单前阻断，避免产生一个必失败的后台任务。
        if vendor_id == "doubao" and mtype == "video" and "/tasks" in str(((vendor_cfg or {}).get("endpoints") or {}).get("video") or ""):
            if re.search(r"(?:^|[-_])(?:sd1\.5|sd15|sdxl|stable[-_]?diffusion)(?:[-_.]|$)", str(model_name or ""), re.IGNORECASE):
                return self._send(400, "application/json",
                    json.dumps({"ok":False, "err":f"模型 {model_name} 不支持豆包 Agent Plan 视频接口；请改用套餐支持的 Seedance/Agent Plan 模型，或切换 local-comfyui 并配置 SD1.5/SDXL 视频工作流。"}, ensure_ascii=False).encode())
        ref_cap = reference_limit(vendor_id, model_name, mtype, vendor_cfg) if reference_limit else 3
        if not isinstance(refs,list) or len(refs)>ref_cap:
            detail = f"refs 须为数组且不超过 {ref_cap} 张（{vendor_id}/{model_name or '未配置模型'} 的参考图输入上限）"
            if vendor_id == "local-comfyui" and mtype == "video":
                detail += "；当前视频适配器是 MiniMax H3 内置工作流，固定最多 3 张，请在提示词中只保留 3 个 @ref，或改用支持更多参考图的云端视频模型"
            return self._send(400,"application/json",json.dumps({"ok":False,"err":detail},ensure_ascii=False).encode())
        refpaths=[]; ref_records=[]
        refmod = tools_mod("asset_refs.py")
        for r in refs:
            purpose="手动引用"; raw_ref=r
            if isinstance(r,dict):
                raw_ref=r.get("path") or ""; purpose=str(r.get("purpose") or purpose)
            try:
                rel = refmod.normalize_project_ref(d, raw_ref) if refmod else str(raw_ref)
                p = os.path.realpath(os.path.join(d, rel))
            except (ValueError, TypeError):
                p = ""
            if not p or not under(d,p) or not os.path.isfile(p):
                return self._send(400,"application/json",json.dumps({"ok":False,"err":"参考图不在项目目录内: "+str(raw_ref)},ensure_ascii=False).encode())
            refpaths.append(p)
            with open(p,"rb") as ref_file:
                content_hash="sha256:"+hashlib.file_digest(ref_file,"sha256").hexdigest()
            origin=r if isinstance(r,dict) else {}
            ref_record={"path":os.path.relpath(p,d).replace(os.sep,"/"),"purpose":purpose,
                        "version_id":content_hash,
                        "source_item_id":str(origin.get("source_item_id") or ""),
                        "panel_id":str(origin.get("panel_id") or ""),
                        "ref_token":str(origin.get("ref_token") or ""),
                        "reference_role":str(origin.get("reference_role") or origin.get("role") or "unspecified")}
            if origin.get("target_time_seconds") is not None or origin.get("time_seconds") is not None:
                ref_record["target_time_seconds"]=origin.get("target_time_seconds", origin.get("time_seconds"))
            if origin.get("usage"):
                ref_record["usage"]=str(origin.get("usage"))
            ref_records.append(ref_record)
        if compiled_request is not None:
            compiled_request["image_refs"]=ref_records
        if not next((x for x in load_vendors() if x.get("id")==vendor_id and x.get("enabled")),None):
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"厂商不存在或未启用"},ensure_ascii=False).encode())
        video_options = None
        if mtype == 'video':
            try:
                vp = tools_mod('video_profiles.py')
                raw = dict(body.get('video_options') or {})
                cap = vp.capabilities(vendor_cfg)
                raw.setdefault('mode', 'first_frame' if cap['default_mode'] == 'first_frame' else 'reference' if refpaths else 'text')
                video_options = vp.settings(vendor_cfg, raw)
                first = next((p for p,r in zip(refpaths,ref_records) if r['reference_role']=='first_frame'), None)
                last = next((p for p,r in zip(refpaths,ref_records) if r['reference_role']=='last_frame'), None)
                if video_options['mode'] == 'first_frame' and len(refpaths) == 1:
                    first=refpaths[0]; ref_records[0]['reference_role']='first_frame'
                vp.validate_media(cap,video_options['mode'],refpaths,first,last)
                if cap['transport']=='public_url' and refpaths: raise ValueError('Agnes 本地参考图需公网 URL，请在创作生成页绑定对应地址')
            except Exception as exc: return self._send_run_json(400, {'ok':False,'err':scrub_err(exc)})
        item_id=time.strftime("%Y%m%d_%H%M%S")+"_%06d"%(int(time.time()*1e6)%1000000)
        outdir=os.path.join(d,"创作",item_id); os.makedirs(outdir,exist_ok=True)
        mf=os.path.join(d,"创作","creation.json")
        store_mod=tools_mod("creation_store.py")
        asset_context = bundle.get("asset_context", {}) if isinstance(bundle, dict) else {}
        prompt_stage = str((bundle or {}).get("prompt_stage") or ("storyboard_image" if mtype == "image" else "video")) if isinstance(bundle, dict) else ("storyboard_image" if mtype == "image" else "video")
        prompt_system = str((bundle or {}).get("system_prompt") or "") if isinstance(bundle, dict) else ""
        prompt_revision = str(body.get("prompt_revision") or ((bundle or {}).get("prompt_revision") if isinstance(bundle, dict) else "") or "")
        asset_revisions = body.get("asset_revisions") if isinstance(body.get("asset_revisions"), dict) else ((bundle or {}).get("asset_revisions") if isinstance(bundle, dict) else {})
        acting_source_hash = str(body.get("source_hash") or "")
        acting_mode = str(body.get("acting_mode") or "")
        actor_warnings = body.get("actor_warnings") if isinstance(body.get("actor_warnings"),list) else []
        item=store_mod.make_item(item_id,mtype,prompt,board=board_name,shot_id=shot_id,
                                 prompt_user=prompt_user,prompt_assembled=prompt_assembled,
                                 negative=negative,refs=ref_records,vendor_id=vendor_id,
                                 note=str(body.get("note") or ""), asset_context=asset_context,
                                 prompt_json=prompt_json, asset_refs=asset_refs,
                                 source_hash=acting_source_hash, acting_mode=acting_mode,
                                 actor_performance_used=bool(body.get("actor_performance_used")), actor_warnings=actor_warnings,
                                 panel_id=panel_id, compiled_request=compiled_request, panel_draft=body.get("panel_draft"), image_mode=image_mode,
                                 prompt_stage=prompt_stage, prompt_system=prompt_system, prompt_revision=prompt_revision,
                                 asset_revisions=asset_revisions) if store_mod else {
            "id":item_id,"type":mtype,"prompt":prompt,"refs":ref_records,"vendor_id":vendor_id,
            "status":"running","created_at":time.strftime("%Y-%m-%dT%H:%M:%S"),"outputs":[],"asset_context":asset_context,"prompt_json":prompt_json,"asset_refs":asset_refs,"source_hash":acting_source_hash,"acting_mode":acting_mode,"actor_performance_used":bool(body.get("actor_performance_used")),"actor_warnings":actor_warnings,"image_mode":image_mode,
            "prompt_stage":prompt_stage,"prompt_system":prompt_system,"prompt_revision":prompt_revision,"asset_revisions":dict(asset_revisions or {})}
        item.update(binding)
        item["model_slot"] = model_slot
        item["model"] = model_name
        try: data=json.load(open(mf,encoding="utf-8"))
        except Exception: data={"items":[]}
        if project_store:
            def _append_manifest(cur):
                if not isinstance(cur, dict):
                    raise project_store.InvalidDocument("creation.json 顶层必须是对象")
                items = cur.setdefault("items", [])
                if not isinstance(items, list):
                    raise project_store.InvalidDocument("creation.json.items 必须是数组")
                items.append(item)
            try:
                versions_mod = tools_mod("versions.py")
                snapshot = getattr(versions_mod, "snapshot", None) if versions_mod else None
                project_store.update_json(mf, _append_manifest, create_default={"items": []}, snapshot=snapshot)
            except Exception as exc:
                return self._send(500,"application/json",json.dumps({"ok":False,"err":"写入创作清单失败: "+str(exc)},ensure_ascii=False).encode())
        else:
            data.setdefault("items",[]).append(item)
            json.dump(data,open(mf,"w",encoding="utf-8"),ensure_ascii=False,indent=1)
        cmd=[sys.executable,os.path.join(TOOLS,"create_media.py"),"--type",mtype,
             "--prompt",prompt,"--vendor",vendor_id,"--providers",PROV,
             "--outdir",outdir,"--manifest",mf,"--item-id",item_id]
        cmd += ["--model", str(model_name)]
        if video_options: cmd += ['--video-options', json.dumps(video_options,ensure_ascii=False)]
        if binding: cmd += ["--voice-id", binding["voice_id"]]
        if prompt_stage: cmd += ["--prompt-stage", prompt_stage]
        if prompt_revision: cmd += ["--prompt-revision", prompt_revision]
        if mtype == "image":
            if image_mode == "edit": cmd += ["--mode", "edit"]
            aspect = ((compiled_request or {}).get("aspect_ratio") or ((prompt_json or {}).get("camera") or {}).get("aspect_ratio")
                      if isinstance(prompt_json, dict) else None) or "16:9"
            cmd += ["--size", str(aspect)]
        if compiled_request: cmd += ["--compiled"]
        if negative: cmd += ["--negative",negative]
        for i,rp in enumerate(refpaths):
            cmd += ["--refs",rp]
            if i < len(ref_records):
                cmd += ["--ref-purpose",str(ref_records[i].get("purpose") or "手动引用")]
                cmd += ["--ref-role",str(ref_records[i].get("reference_role") or "unspecified")]
                if ref_records[i].get("target_time_seconds") is not None:
                    cmd += ["--ref-time",str(ref_records[i].get("target_time_seconds"))]
        jid=self.spawn_job("create",cmd)
        return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"id":jid,"job":True,"item_id":item_id},ensure_ascii=False).encode())

    @route('POST', '/api/create/save')
    def route_post_api_create_save(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        d=proj_dir(body.get("project")); item=body.get("item")
        mf=os.path.join(d,"创作","creation.json") if d else ""
        if not d or not isinstance(item,dict) or not item.get("id") or not os.path.isfile(mf):
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"参数不合法"},ensure_ascii=False).encode())
        keys=("prompt","board","shot_id","prompt_user","prompt_assembled","prompt_json","asset_refs","negative","refs","vendor_id","note","status","outputs","asset_context","source_hash","acting_mode","actor_performance_used","actor_warnings","prompt_stage","prompt_system","prompt_revision","asset_revisions")
        found=[False]
        def _save_item(data):
            if not isinstance(data,dict) or not isinstance(data.get("items"),list):
                raise ValueError("creation.json.items 必须是数组")
            for current in data["items"]:
                if isinstance(current,dict) and current.get("id")==item["id"]:
                    for k in keys:
                        if k in item: current[k]=item[k]
                    current["updated_at"]=time.strftime("%Y-%m-%dT%H:%M:%S")
                    found[0]=True; break
        if project_store:
            try:
                versions_mod = tools_mod("versions.py")
                snapshot = getattr(versions_mod, "snapshot", None) if versions_mod else None
                project_store.update_json(mf, _save_item, snapshot=snapshot)
            except Exception as exc:
                return self._send(400,"application/json",json.dumps({"ok":False,"err":str(exc)},ensure_ascii=False).encode())
        else:
            data=json.load(open(mf,encoding="utf-8")); _save_item(data)
            if found[0]: json.dump(data,open(mf,"w",encoding="utf-8"),ensure_ascii=False,indent=1)
        if not found[0]:
            return self._send(404,"application/json",json.dumps({"ok":False,"err":"条目不存在"},ensure_ascii=False).encode())
        return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"id":item["id"]},ensure_ascii=False).encode())

    @route('POST', '/api/create/delete')
    def route_post_api_create_delete(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        d=proj_dir(body.get("project")); iid=safe_proj(body.get("id"))
        if not d or not iid:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"参数不合法"},ensure_ascii=False).encode())
        outdir=os.path.realpath(os.path.join(d,"创作",iid))
        if not under(os.path.join(d,"创作"),outdir) or not os.path.isdir(outdir):
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"条目目录不存在"},ensure_ascii=False).encode())
        shutil.rmtree(outdir,ignore_errors=True)
        mf=os.path.join(d,"创作","creation.json")
        if os.path.isfile(mf):
            if project_store:
                def _remove_item(cur):
                    if not isinstance(cur,dict) or not isinstance(cur.get("items"),list):
                        raise project_store.InvalidDocument("creation.json.items 必须是数组")
                    cur["items"]=[x for x in cur["items"] if x.get("id")!=iid]
                try:
                    versions_mod=tools_mod("versions.py")
                    snapshot=getattr(versions_mod,"snapshot",None) if versions_mod else None
                    project_store.update_json(mf,_remove_item,snapshot=snapshot)
                except Exception as exc:
                    return self._send(500,"application/json",json.dumps({"ok":False,"err":"更新创作清单失败: "+str(exc)},ensure_ascii=False).encode())
            else:
                _versions_snapshot(mf)
                try: data=json.load(open(mf,encoding="utf-8"))
                except Exception: data={"items":[]}
                data["items"]=[x for x in data.get("items",[]) if x.get("id")!=iid]
                json.dump(data,open(mf,"w",encoding="utf-8"),ensure_ascii=False,indent=1)
        return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"deleted":iid},ensure_ascii=False).encode())

    @route('POST', '/api/analysis/run')
    def route_post_api_analysis_run(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        d=proj_dir(body.get("project"))
        video=safe_video_abs(body.get("video"))
        if not d or not video:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"项目或视频路径不合法"},ensure_ascii=False).encode())
        af=tools_mod("analyze_film.py")
        if not af:
            return self._send(500,"application/json",json.dumps({"ok":False,"err":"analyze_film.py 缺失"},ensure_ascii=False).encode())
        nm=safe_proj(body.get("name")) or time.strftime("分析_%Y%m%d_%H%M%S")
        outdir=os.path.join(d,"拉片",nm)
        cmd=[sys.executable,os.path.join(TOOLS,"analyze_film.py"),video,"--out",outdir]
        if body.get("no_ai"):
            cmd.append("--no-ai")
        else:
            vendor=str(body.get("vendor_id") or "")
            if not vendor:   # 服务端自动选第一个 enabled+vision+key 的厂商
                vendor=next((v.get("id") for v in load_vendors()
                             if v.get("enabled") and (v.get("models") or {}).get("vision") and v.get("api_key")),"")
            if vendor: cmd+=["--vendor",vendor]
        if body.get("thresh"): cmd+=["--thresh",str(float(body["thresh"]))]
        if body.get("min_dur") is not None: cmd+=["--min-dur",str(float(body["min_dur"]))]
        if body.get("max_ai"): cmd+=["--max-ai",str(int(body["max_ai"]))]
        if body.get("workers"): cmd+=["--workers",str(max(1,min(int(body["workers"]),16)))]
        note=str(body.get("note") or "")
        if note: cmd+=["--note",note]
        jid=self.spawn_job("analysis",cmd)
        return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"id":jid,"job":True,"name":nm},ensure_ascii=False).encode())

    @route('POST', '/api/analysis/export')
    def route_post_api_analysis_export(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        d=proj_dir(body.get("project")); nm=safe_proj(body.get("name"))
        aj=os.path.join(d,"拉片",nm,"analysis.json") if d and nm else ""
        if not d or not nm or not under(os.path.join(d,"拉片"),os.path.realpath(aj)) or not os.path.isfile(aj):
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"项目或分析版本不存在"},ensure_ascii=False).encode())
        ex=tools_mod("export_analysis_xlsx.py")
        if not ex:
            return self._send(500,"application/json",json.dumps({"ok":False,"err":"export_analysis_xlsx.py 缺失"},ensure_ascii=False).encode())
        jid=self.spawn_job("analysis",[sys.executable,os.path.join(TOOLS,"export_analysis_xlsx.py"),aj])
        return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"id":jid,"job":True,"file":"projects/"+safe_proj(body.get("project"))+"/拉片/"+nm+"/"+nm+"_分镜脚本.xlsx"},ensure_ascii=False).encode())

    @route('POST', '/api/analysis/merge')
    def route_post_api_analysis_merge(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        # 把 台词脚本.json（台词页合并结果）并入已有版本 dialogue：无 AI、同步、秒级
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        d=proj_dir(body.get("project")); nm=safe_proj(body.get("name"))
        ad=os.path.join(d,"拉片",nm) if d and nm else ""
        if not d or not nm or not under(os.path.join(d,"拉片"),os.path.realpath(ad)) or not os.path.isfile(os.path.join(ad,"analysis.json")):
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"项目或分析版本不存在"},ensure_ascii=False).encode())
        if not os.path.isfile(os.path.join(d,"台词","台词脚本.json")):
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"项目还没有台词脚本.json（先在台词页合并）"},ensure_ascii=False).encode())
        if not tools_mod("analyze_film.py"):
            return self._send(500,"application/json",json.dumps({"ok":False,"err":"analyze_film.py 缺失"},ensure_ascii=False).encode())
        _env=dict(os.environ); _env["PYTHONIOENCODING"]="utf-8"
        jid=self.spawn_job("analysis",[sys.executable,os.path.join(TOOLS,"analyze_film.py"),"--merge-lines",ad])
        return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"id":jid,"job":True},ensure_ascii=False).encode())

    @route('POST', '/api/analysis/ai')
    def route_post_api_analysis_ai(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        # 对已存在版本跑 AI 填充；only_empty=True 只补空字段镜，False=全部重识（覆盖已有字段，产生 LLM 费用）；ffmpeg 步骤与 AI 解耦
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        d=proj_dir(body.get("project")); nm=safe_proj(body.get("name"))
        ad=os.path.join(d,"拉片",nm) if d and nm else ""
        if not d or not nm or not under(os.path.join(d,"拉片"),os.path.realpath(ad)) or not os.path.isfile(os.path.join(ad,"analysis.json")):
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"项目或分析版本不存在"},ensure_ascii=False).encode())
        if not tools_mod("analyze_film.py"):
            return self._send(500,"application/json",json.dumps({"ok":False,"err":"analyze_film.py 缺失"},ensure_ascii=False).encode())
        cmd=[sys.executable,os.path.join(TOOLS,"analyze_film.py"),"--fill",ad]
        if body.get("only_empty"): cmd.append("--only-empty")
        vendor=str(body.get("vendor_id") or "")
        if not vendor:
            vendor=next((v.get("id") for v in load_vendors()
                         if v.get("enabled") and (v.get("models") or {}).get("vision") and v.get("api_key")),"")
        if vendor: cmd+=["--vendor",vendor]
        if body.get("max_ai"): cmd+=["--max-ai",str(int(body["max_ai"]))]
        jid=self.spawn_job("analysis_ai",cmd)
        return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"id":jid,"job":True},ensure_ascii=False).encode())

    @route('POST', '/api/analysis/reshot')
    def route_post_api_analysis_reshot(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        # 单镜/多镜重识别（AI 调用约 40s/镜，必须走 job）
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        d=proj_dir(body.get("project")); nm=safe_proj(body.get("name"))
        shots=body.get("shots")
        ad=os.path.join(d,"拉片",nm) if d and nm else ""
        if not d or not nm or not under(os.path.join(d,"拉片"),os.path.realpath(ad)) or not os.path.isfile(os.path.join(ad,"analysis.json")):
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"项目或分析版本不存在"},ensure_ascii=False).encode())
        if not isinstance(shots,list) or not shots or not all(isinstance(x,str) and re.fullmatch(r"S\d+",x.strip()) for x in shots):
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"shots 须为镜号数组（如 [\"S3\"]）"},ensure_ascii=False).encode())
        if not tools_mod("analyze_film.py"):
            return self._send(500,"application/json",json.dumps({"ok":False,"err":"analyze_film.py 缺失"},ensure_ascii=False).encode())
        cmd=[sys.executable,os.path.join(TOOLS,"analyze_film.py"),"--fill",ad,"--shots",",".join(x.strip() for x in shots)]
        vendor=str(body.get("vendor_id") or "")
        if not vendor:
            vendor=next((v.get("id") for v in load_vendors()
                         if v.get("enabled") and (v.get("models") or {}).get("vision") and v.get("api_key")),"")
        if vendor: cmd+=["--vendor",vendor]
        jid=self.spawn_job("analysis_reshot",cmd)
        return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"id":jid,"job":True},ensure_ascii=False).encode())

    @route('POST', '/api/analysis/save')
    def route_post_api_analysis_save(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        d=proj_dir(body.get("project")); nm=safe_proj(body.get("name"))
        an=body.get("analysis")
        if not d or not nm or not isinstance(an,dict):
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"参数不合法"},ensure_ascii=False).encode())
        va=tools_mod("validate_analysis.py")
        errors,warnings=(va.validate(an) if va else (["校验器缺失"],[]))
        if errors:
            return self._send(400,"application/json; charset=utf-8",json.dumps({"ok":False,"errors":errors,"warnings":warnings},ensure_ascii=False).encode())
        outdir=os.path.join(d,"拉片",nm)
        os.makedirs(outdir,exist_ok=True)
        an["name"]=nm
        _versions_snapshot(os.path.join(outdir,"analysis.json"))
        json.dump(an,open(os.path.join(outdir,"analysis.json"),"w",encoding="utf-8"),ensure_ascii=False,indent=1)
        af=tools_mod("analyze_film.py")
        if af: af.write_markdown(an,outdir)   # 复用 analyze_film 的 md 生成
        vf=os.path.join(d,"拉片","_versions.json")
        vers=[]
        if os.path.isfile(vf):
            try: vers=json.load(open(vf,encoding="utf-8"))
            except Exception: vers=[]
        hit=[v for v in vers if v.get("name")==nm]
        entry={"name":nm,"created_at":an.get("created_at") or time.strftime("%Y-%m-%dT%H:%M:%S"),
               "status":"done","shot_count":len(an.get("shots") or []),
               "source":an.get("source",""),"note":"workbench 保存"}
        if hit: hit[0].update(entry)
        else: vers.append(entry)
        _versions_snapshot(vf)
        json.dump(vers,open(vf,"w",encoding="utf-8"),ensure_ascii=False,indent=1)
        return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"name":nm,"warnings":warnings},ensure_ascii=False).encode())

    @route('POST', '/api/analysis/delete')
    def route_post_api_analysis_delete(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        d=proj_dir(body.get("project")); nm=safe_proj(body.get("name"))
        if not d or not nm:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"参数不合法"},ensure_ascii=False).encode())
        outdir=os.path.realpath(os.path.join(d,"拉片",nm))
        if not under(os.path.join(d,"拉片"),outdir) or not os.path.isdir(outdir):
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"版本目录不存在"},ensure_ascii=False).encode())
        shutil.rmtree(outdir,ignore_errors=True)
        vf=os.path.join(d,"拉片","_versions.json")
        if os.path.isfile(vf):
            try: vers=[v for v in json.load(open(vf,encoding="utf-8")) if v.get("name")!=nm]
            except Exception: vers=[]
            json.dump(vers,open(vf,"w",encoding="utf-8"),ensure_ascii=False,indent=1)
        return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"deleted":nm},ensure_ascii=False).encode())

    @route('POST', '/api/frames/extract')
    def route_post_api_frames_extract(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        d=proj_dir(body.get("project"))
        video=safe_video_abs(body.get("video"))
        if not d or not video:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"项目或视频路径不合法"},ensure_ascii=False).encode())
        if not tools_mod("extract_frames_1fps.py"):
            return self._send(500,"application/json",json.dumps({"ok":False,"err":"extract_frames_1fps.py 缺失"},ensure_ascii=False).encode())
        outdir=os.path.join(d,"逐帧","每秒")
        jid=self.spawn_job("frames",[sys.executable,os.path.join(TOOLS,"extract_frames_1fps.py"),video,"--out",outdir])
        return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"id":jid,"job":True},ensure_ascii=False).encode())

    @route('POST', '/api/lines/merge')
    def route_post_api_lines_merge(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        d=proj_dir(body.get("project"))
        if not d:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"项目不存在"},ensure_ascii=False).encode())
        if not tools_mod("merge_lines.py"):
            return self._send(500,"application/json",json.dumps({"ok":False,"err":"merge_lines.py 缺失"},ensure_ascii=False).encode())
        out=os.path.join(d,"台词","台词脚本.json")
        jid=self.spawn_job("lines",[sys.executable,os.path.join(TOOLS,"merge_lines.py"),d,"--out",out])
        return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"id":jid,"job":True},ensure_ascii=False).encode())

    @route('POST', '/api/lines/attribute')
    def route_post_api_lines_attribute(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        # AI 台词人物归属：vision 按镜头批量标注 speaker（依赖拉片关键帧 + 已合并台词脚本）
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        d=proj_dir(body.get("project"))
        if not d:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"项目不存在"},ensure_ascii=False).encode())
        if not tools_mod("attribute_speakers.py"):
            return self._send(500,"application/json",json.dumps({"ok":False,"err":"attribute_speakers.py 缺失"},ensure_ascii=False).encode())
        cmd=[sys.executable,os.path.join(TOOLS,"attribute_speakers.py"),d,"--providers",PROV]
        nm=safe_proj(body.get("analysis") or "")
        if nm: cmd+=["--analysis",nm]
        jid=self.spawn_job("lines",cmd)
        return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"id":jid,"job":True},ensure_ascii=False).encode())

    @route('POST', '/api/lines/save')
    def route_post_api_lines_save(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        d=proj_dir(body.get("project")); sc=body.get("script")
        if not d or not isinstance(sc,dict) or not isinstance(sc.get("lines"),list):
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"script 须含 lines 数组"},ensure_ascii=False).encode())
        _versions_snapshot(os.path.join(d,"台词","台词脚本.json"))
        os.makedirs(os.path.join(d,"台词"),exist_ok=True)
        json.dump(sc,open(os.path.join(d,"台词","台词脚本.json"),"w",encoding="utf-8"),ensure_ascii=False,indent=1)
        return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"lines":len(sc["lines"])},ensure_ascii=False).encode())

    @route('POST', '/api/white/from_analysis')
    def route_post_api_white_from_analysis(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        # 拉片解构 -> dialogue 分镜桥接（后台任务，前端按任务 ID 等待）
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        d=proj_dir(body.get("project"))
        if not d:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"项目不存在"},ensure_ascii=False).encode())
        gen=os.path.join(TOOLS,"analysis_to_storyboard.py")
        if not os.path.isfile(gen):
            return self._send(500,"application/json",json.dumps({"ok":False,"err":"analysis_to_storyboard.py 缺失"},ensure_ascii=False).encode())
        cmd=[sys.executable,gen,d,"--style",body.get("style") if body.get("style") in ("stand","seated") else "stand"]
        nm=safe_proj(body.get("analysis") or "")
        if nm: cmd+=["--analysis",nm]
        _env=dict(os.environ); _env["PYTHONIOENCODING"]="utf-8"
        jid=self.spawn_job("analysis",cmd)
        return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"id":jid,"job":True},ensure_ascii=False).encode())

    @route('POST', '/api/previz/export')
    def route_post_api_previz_export(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        # 逐镜导出「预演包」：干净预演帧+表演提示词+manifest（图生视频参考输入），改异步任务执行
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        d=proj_dir(body.get("project"))
        rel=str(body.get("json") or "")
        if not d:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"项目不存在"},ensure_ascii=False).encode())
        sb=os.path.normpath(os.path.join(d,rel)) if not os.path.isabs(rel) else os.path.normpath(rel)
        # 兼容旧版前端只提交分镜文件名的请求，自动在项目/分镜下解析。
        if (not os.path.isfile(sb) and rel and not os.path.isabs(rel)
                and os.path.basename(rel) == rel):
            fallback=os.path.normpath(os.path.join(d, "分镜", rel))
            if under(d, fallback):
                sb=fallback
        if not under(d,sb) or not os.path.isfile(sb) or not sb.lower().endswith(".json"):
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"分镜 JSON 不合法"},ensure_ascii=False).encode())
        if not tools_mod("export_previz.py"):
            return self._send(500,"application/json",json.dumps({"ok":False,"err":"export_previz.py 缺失"},ensure_ascii=False).encode())
        cmd=[sys.executable,os.path.join(TOOLS,"export_previz.py"),sb]
        if body.get("frame") in ("mid","start"): cmd+=["--frame",body["frame"]]
        # 改异步任务：分镜镜头多时同步会超时；产物路径看任务日志 OUTPUT: 行
        # （export_previz 打印 OUTPUT:<目录>，目录约定 白模/预演包_<分镜名>/）。
        jid=self.spawn_job("previz",cmd)
        return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"id":jid,"job":True},ensure_ascii=False).encode())

    @route('POST', '/api/whiterange/run')
    def route_post_api_whiterange_run(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        d=proj_dir(body.get("project"))
        shots=str(body.get("shots") or "").strip()
        rel=str(body.get("json") or "")
        if not d:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"项目不存在"},ensure_ascii=False).encode())
        sb=os.path.normpath(os.path.join(d,rel)) if not os.path.isabs(rel) else os.path.normpath(rel)
        if not shots or not under(d,sb) or not os.path.isfile(sb) or not sb.lower().endswith(".json"):
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"项目/分镜 JSON/镜头区间不合法"},ensure_ascii=False).encode())
        if not tools_mod("render_shot_range.py"):
            return self._send(500,"application/json",json.dumps({"ok":False,"err":"render_shot_range.py 缺失"},ensure_ascii=False).encode())
        outdir=os.path.join(d,"白模")
        cmd=[sys.executable,os.path.join(TOOLS,"render_shot_range.py"),sb,"--shots",shots,"--outdir",outdir]
        if body.get("engine") in ("dialogue","previs"): cmd+=["--engine",body["engine"]]
        jid=self.spawn_job("whiterange",cmd)
        return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"id":jid,"job":True},ensure_ascii=False).encode())

    @route('POST', '/api/script/import')
    def route_post_api_script_import(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        d=proj_dir(body.get("project"))
        txt=str(body.get("text") or "").strip()
        if not d or len(txt)<80:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"项目不存在或剧本过短(<80字)"},ensure_ascii=False).encode())
        os.makedirs(os.path.join(d,"剧本"),exist_ok=True)
        _versions_snapshot(os.path.join(d,"剧本","剧本.txt"))
        open(os.path.join(d,"剧本","剧本.txt"),"w",encoding="utf-8").write(txt)
        return self._send(200,"application/json",json.dumps({"ok":True,"chars":len(txt)},ensure_ascii=False).encode())

    @route('POST', '/api/acting/context')
    def route_post_api_acting_context(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        d=proj_dir(body.get("project")); nm=safe_proj(body.get("storyboard") or body.get("name") or "")
        p=os.path.realpath(os.path.join(d,"分镜",nm)) if d and nm else ""
        context=body.get("context")
        if not d or not nm.lower().endswith(".json") or not under(d,p) or not os.path.isfile(p) or not isinstance(context,dict):
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"项目/分镜/context 不合法"},ensure_ascii=False).encode())
        try:
            mod=tools_mod("actor_pipeline.py"); board,revision=mod._read_board(p)
            if hasattr(mod, "hydrate_actor_cards"):
                context=mod.hydrate_actor_cards(context, d, board)
            issues=mod.validate_context(context,board) if hasattr(mod,"validate_context") else []
            if issues:
                return self._send(422,"application/json",json.dumps({"ok":False,"err":"演员上下文校验失败："+ "；".join(x["message"] for x in issues[:5]),"issues":issues},ensure_ascii=False).encode())
            expected=body.get("revision")
            snapshot=getattr(tools_mod("versions.py"),"snapshot",None)
            def mutate(current):
                current["acting_context"]=context
            _,new_revision=project_store.update_json(p,mutate,expected_revision=expected or revision,snapshot=snapshot)
            return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"revision":new_revision,"context":context},ensure_ascii=False).encode())
        except project_store.RevisionConflict as exc:
            return self._send(409,"application/json",json.dumps({"ok":False,"err":str(exc)},ensure_ascii=False).encode())
        except Exception as exc:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":str(exc)},ensure_ascii=False).encode())

    @route('POST', '/api/acting/prepare')
    def route_post_api_acting_prepare(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        d=proj_dir(body.get("project")); nm=safe_proj(body.get("storyboard") or body.get("name") or "")
        p=os.path.realpath(os.path.join(d,"分镜",nm)) if d and nm else ""
        if not d or not nm.lower().endswith(".json") or not under(d,p) or not os.path.isfile(p):
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"项目/分镜不合法"},ensure_ascii=False).encode())
        try:
            mod=tools_mod("actor_pipeline.py")
            vendor_id=str(body.get("vendor_id") or "")
            if vendor_id and not any(v.get('id')==vendor_id and v.get('enabled') and (v.get('models') or {}).get('text') for v in load_vendors()):
                raise ValueError('准备厂商不存在、未启用或未配置 text 模型')
            if body.get('shot_ids') is not None and (not isinstance(body['shot_ids'],list) or not all(isinstance(x,str) for x in body['shot_ids'])):
                raise ValueError('shot_ids 必须是字符串数组')
            if body.get('context') is not None and not isinstance(body['context'],dict):
                raise ValueError('context 必须是对象')
            import uuid
            request_dir=os.path.join(d,'演员','准备请求'); os.makedirs(request_dir,exist_ok=True)
            request_path=os.path.join(request_dir,uuid.uuid4().hex+'.json')
            request={'board_path':p,'revision':mod._read_board(p)[1], 'vendor_id':vendor_id,
                     'shot_ids':body.get('shot_ids'),'context':body.get('context')}
            with open(request_path,'w',encoding='utf-8') as fp: json.dump(request,fp,ensure_ascii=False)
            jid=self.spawn_job('acting',[sys.executable,os.path.join(TOOLS,'acting_prepare_job.py'),request_path])
            return self._send_run_json(200,{'ok':True,'id':jid,'job':True})
        except Exception as exc:
            return self._send(422,"application/json",json.dumps({"ok":False,"err":str(exc)},ensure_ascii=False).encode())

    @route('POST', '/api/acting/apply')
    def route_post_api_acting_apply(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        d=proj_dir(body.get("project")); nm=safe_proj(body.get("storyboard") or body.get("name") or "")
        p=os.path.realpath(os.path.join(d,"分镜",nm)) if d and nm else ""
        ref=str(body.get("run_id") or body.get("candidate") or "")
        if not d or not nm.lower().endswith(".json") or not ref or not under(d,p) or not os.path.isfile(p):
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"项目/分镜/run_id 不合法"},ensure_ascii=False).encode())
        try:
            mod=tools_mod("actor_pipeline.py")
            result=mod.apply_candidate(p,ref,expected_revision=body.get("revision"))
            return self._send(200,"application/json; charset=utf-8",json.dumps(result,ensure_ascii=False).encode())
        except project_store.RevisionConflict as exc:
            return self._send(409,"application/json",json.dumps({"ok":False,"err":str(exc)},ensure_ascii=False).encode())
        except Exception as exc:
            return self._send(422,"application/json",json.dumps({"ok":False,"err":str(exc)},ensure_ascii=False).encode())

    @route('POST', '/api/acting/lock')
    def route_post_api_acting_lock(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        d=proj_dir(body.get("project")); nm=safe_proj(body.get("storyboard") or body.get("name") or "")
        p=os.path.realpath(os.path.join(d,"分镜",nm)) if d and nm else ""
        shot_ids=body.get("shot_ids") or ([body.get("shot_id")] if body.get("shot_id") else [])
        if not d or not nm.lower().endswith(".json") or not isinstance(shot_ids,list) or not shot_ids or not under(d,p) or not os.path.isfile(p):
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"项目/分镜/shot_ids 不合法"},ensure_ascii=False).encode())
        try:
            mod=tools_mod("actor_pipeline.py")
            result=mod.set_shot_lock(p,shot_ids,bool(body.get("locked")),expected_revision=body.get("revision"))
            return self._send(200,"application/json; charset=utf-8",json.dumps(result,ensure_ascii=False).encode())
        except project_store.RevisionConflict as exc:
            return self._send(409,"application/json",json.dumps({"ok":False,"err":str(exc)},ensure_ascii=False).encode())
        except Exception as exc:
            return self._send(422,"application/json",json.dumps({"ok":False,"err":str(exc)},ensure_ascii=False).encode())

    @route('POST', '/api/acting/compile')
    def route_post_api_acting_compile(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        d=proj_dir(body.get("project")); nm=safe_proj(body.get("storyboard") or body.get("name") or "")
        shot_id=str(body.get("shot_id") or "")
        p=os.path.realpath(os.path.join(d,"分镜",nm)) if d and nm else ""
        if not d or not nm.lower().endswith(".json") or not shot_id or not under(d,p) or not os.path.isfile(p):
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"项目/分镜/shot_id 不合法"},ensure_ascii=False).encode())
        try:
            board=json.load(open(p,encoding="utf-8"))
            actor_mod=tools_mod("actor_pipeline.py")
            if actor_mod:
                board["acting_context"] = actor_mod.hydrate_actor_cards(board.get("acting_context") or actor_mod.default_context(board, d), d, board)
            mod=tools_mod("prompt_compiler.py")
            result=mod.compile_shot(board, shot_id, mode=str(body.get("mode") or "baseline"),
                                    media_type=str(body.get("media_type") or "video"),
                                    supports_audio=bool(body.get("supports_audio")))
            return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,**result},ensure_ascii=False).encode())
        except Exception as exc:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":str(exc)},ensure_ascii=False).encode())

    @route('POST', '/api/acting/evaluate')
    def route_post_api_acting_evaluate(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        d=proj_dir(body.get("project")); nm=safe_proj(body.get("storyboard") or body.get("name") or "")
        p=os.path.realpath(os.path.join(d,"分镜",nm)) if d and nm else ""
        if not d or not nm.lower().endswith(".json") or not under(d,p) or not os.path.isfile(p):
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"项目/分镜不合法"},ensure_ascii=False).encode())
        shots=body.get("shot_ids") or []
        if not isinstance(shots,list) or len(shots)>50:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"shot_ids 须为数组且不超过50个"},ensure_ascii=False).encode())
        modes=body.get("modes") or ["baseline","style","stateful"]
        if not isinstance(modes,list) or any(str(x) not in ("baseline","style","stateful") for x in modes):
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"modes 只能是 baseline/style/stateful"},ensure_ascii=False).encode())
        try:
            repeats=max(1,min(int(body.get("repeats") or 2),20))
        except (TypeError,ValueError):
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"repeats 必须是1~20的整数"},ensure_ascii=False).encode())
        cmd=[sys.executable,os.path.join(TOOLS,"evaluate_actor_ab.py"),p,
             "--modes",",".join(str(x) for x in modes),
             "--repeats",str(repeats)]
        if shots: cmd += ["--shots",",".join(str(x) for x in shots)]
        if body.get("experiment_id"): cmd += ["--experiment-id",str(body["experiment_id"])]
        if body.get("model"): cmd += ["--model",str(body["model"])]
        vid=str(body.get("vendor_id") or "")
        if vid:
            vendor=next((x for x in load_vendors() if x.get("id")==vid and x.get("enabled") and (x.get("models") or {}).get("text")),None)
            if not vendor:
                return self._send(400,"application/json",json.dumps({"ok":False,"err":"评分厂商不存在、未启用或未配置 text 模型"},ensure_ascii=False).encode())
            cmd += ["--vendor",vid,"--providers",PROV]
        jid=self.spawn_job("acting_eval",cmd)
        return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"id":jid,"job":True},ensure_ascii=False).encode())

    @route('POST', '/api/acting/run')
    def route_post_api_acting_run(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        d=proj_dir(body.get("project")); nm=safe_proj(body.get("storyboard") or body.get("name") or "")
        shot_id=str(body.get("shot_id") or ""); vendor_id=str(body.get("vendor_id") or body.get("provider_id") or "")
        p=os.path.realpath(os.path.join(d,"分镜",nm)) if d and nm else ""
        if not d or not nm.lower().endswith(".json") or not shot_id or not vendor_id or not under(d,p) or not os.path.isfile(p):
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"项目/分镜/shot_id/vendor_id 不合法"},ensure_ascii=False).encode())
        try:
            actor_mod=tools_mod("actor_pipeline.py")
            board,_=actor_mod._read_board(p)
            main_ids=actor_mod.actor_ids_for_shot(board, shot_id, d) if hasattr(actor_mod, "actor_ids_for_shot") else []
            if not main_ids:
                return self._send(422,"application/json",json.dumps({"ok":False,"err":"本镜没有主角演员，演员表现只支持主角；请在分镜中关联主角后再生成"},ensure_ascii=False).encode())
        except Exception as exc:
            return self._send(422,"application/json",json.dumps({"ok":False,"err":str(exc)},ensure_ascii=False).encode())
        vendor=next((x for x in load_vendors() if x.get("id")==vendor_id and x.get("enabled") and (x.get("models") or {}).get("text")),None)
        if not vendor:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"厂商不存在、未启用或未配置 text 模型"},ensure_ascii=False).encode())
        cmd=[sys.executable,os.path.join(TOOLS,"run_actor.py"),"--storyboard",p,"--shot",shot_id,
             "--vendor",vendor_id,"--providers",PROV]
        actor_mode=str(body.get("mode") or "stateful")
        if actor_mode not in ("style", "stateful"):
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"演员 mode 只能是 style/stateful"},ensure_ascii=False).encode())
        cmd += ["--mode", actor_mode]
        context=str(body.get("context") or "")
        if context:
            cp=os.path.realpath(os.path.join(d,context))
            if not under(d,cp) or not os.path.isfile(cp) or not cp.lower().endswith(".json"):
                return self._send(400,"application/json",json.dumps({"ok":False,"err":"演员上下文路径不合法"},ensure_ascii=False).encode())
            cmd += ["--context", cp]
        cmd += ["--draft-only"]
        jid=self.spawn_job("acting",cmd)
        return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"id":jid,"draft_only":True},ensure_ascii=False).encode())

    @route('POST', '/api/skills/save', '/api/skills/create', '/api/skills/toggle', '/api/skills/delete', '/api/skills/style', '/api/skills/reset')
    def route_post_api_skills_save(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        import importlib.util as _iu2
        _sp=_iu2.spec_from_file_location("skill_lib",os.path.join(TOOLS,"skill_lib.py"))
        _SL=_iu2.module_from_spec(_sp); _sp.loader.exec_module(_SL)
        if u.path=="/api/skills/save":
            if body.get("kind")=="system":
                ok=_SL and _pm_save(str(body.get("id") or ""),str(body.get("text") or ""))
            else:
                ok=_SL.save_skill(str(body.get("id") or ""),str(body.get("text") or ""))
            return self._send(200,"application/json",json.dumps({"ok":bool(ok)},ensure_ascii=False).encode())
        if u.path=="/api/skills/reset":
            ok=_pm_reset(str(body.get("id") or ""))
            return self._send(200,"application/json",json.dumps({"ok":bool(ok)},ensure_ascii=False).encode())
        if u.path=="/api/skills/create":
            sid=_SL.create_skill(str(body.get("category") or "directing"),str(body.get("name") or "未命名"),
                                 str(body.get("target") or "storyboard"),str(body.get("description") or ""),
                                 str(body.get("text") or ""))
            return self._send(200,"application/json",json.dumps({"ok":True,"id":sid},ensure_ascii=False).encode())
        if u.path=="/api/skills/toggle":
            ok=_SL.toggle_skill(str(body.get("id") or ""),bool(body.get("enabled")))
            return self._send(200,"application/json",json.dumps({"ok":bool(ok)},ensure_ascii=False).encode())
        if u.path=="/api/skills/delete":
            ok=_SL.delete_skill(str(body.get("id") or ""))
            return self._send(200,"application/json",json.dumps({"ok":bool(ok)},ensure_ascii=False).encode())
        d=proj_dir(body.get("project"))
        if not d:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"项目不存在"},ensure_ascii=False).encode())
        old_style = _SL.project_style(d)
        new_style = body.get("style") or {}
        if not isinstance(new_style,dict):
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"style 必须是对象"},ensure_ascii=False).encode())
        _SL.set_project_style(d,new_style)
        refreshed = 0
        if (old_style.get("image"), old_style.get("anchor")) != (new_style.get("image"), new_style.get("anchor")):
            try:
                refreshed = chatgpt_queue.refresh_queued_asset_jobs(d)
            except Exception as exc:
                return self._send(500,"application/json",json.dumps({"ok":False,"err":f"风格已保存，但待生成资产队列刷新失败：{exc}"},ensure_ascii=False).encode())
        return self._send(200,"application/json",json.dumps({"ok":True,"refreshed_asset_jobs":refreshed},ensure_ascii=False).encode())

    @route('POST', '/api/script/episode/delete')
    def route_post_api_script_episode_delete(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        # 删除分集清单并解除该集资产来源标签；全局资产与已生成产物保留。
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or "{}")
        d=proj_dir(body.get("project"))
        episode=str(body.get("episode") or "").strip()
        if not d or not episode:
            return self._send(400,"application/json; charset=utf-8",json.dumps({"ok":False,"err":"项目或 episode 参数不合法"},ensure_ascii=False).encode())
        try:
            mod=tools_mod("episode_service.py")
            if mod is None: raise ValueError("episode_service.py 缺失")
            result=mod.delete_episode(d,episode)
            return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,**result},ensure_ascii=False).encode())
        except Exception as exc:
            return self._send(400,"application/json; charset=utf-8",json.dumps({"ok":False,"err":str(exc)},ensure_ascii=False).encode())

    @route('POST', '/api/script/expand')
    def route_post_api_script_expand(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        # 创作构想 -> 大纲(=分集.json)；--episode 再扩写指定集为分场剧本
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        d=proj_dir(body.get("project"))
        if not d:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"项目不存在"},ensure_ascii=False).encode())
        idea=str(body.get("idea") or "").strip()
        if not idea and not os.path.isfile(os.path.join(d,"剧本","构想.txt")):
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"缺少创作构想"},ensure_ascii=False).encode())
        cmd=[sys.executable,os.path.join(TOOLS,"creation_pipeline.py"),"expand",d,
             "--eps",str(int(body.get("eps") or 6))]
        if idea: cmd+=["--idea",idea]
        ep=str(body.get("episode") or "")
        if ep: cmd+=["--episode",ep]
        jid=self.spawn_job("creation",cmd)
        return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"id":jid,"job":True},ensure_ascii=False).encode())

    @route('POST', '/api/assets/create')
    def route_post_api_assets_create(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        # 新增母素材/子素材：前端只提交最小字段，父子关系归一化后写回三件套 JSON。
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        d=proj_dir(body.get("project"))
        kind=str(body.get("kind") or "").strip()
        name=str(body.get("name") or "").strip()
        if not d or kind not in ("character","scene","prop") or not name:
            return self._send(400,"application/json; charset=utf-8",json.dumps({"ok":False,"err":"项目、kind 或素材名不合法"},ensure_ascii=False).encode())
        keys={"character":"characters","scene":"scenes","prop":"props"}
        filenames={"character":"人物.json","scene":"场景.json","prop":"道具.json"}
        try:
            relmod=tools_mod("asset_relations.py")
            # 读取三件套；不存在的档案按空数组创建，方便手工补充新母素材。
            file_data={}; combined={}
            for k,key in keys.items():
                path=os.path.join(d,"素材",filenames[k])
                if os.path.isfile(path):
                    raw=json.load(open(path,encoding="utf-8"))
                else:
                    raw={key:[]}
                rows=raw.get(key) if isinstance(raw,dict) else raw
                if not isinstance(rows,list): rows=[]
                file_data[k]=(path,raw,key)
                # combined 必须使用副本；否则 append 新素材会同时修改 raw，
                # 后面的 current_rows==next_rows 判断会误认为无需落盘。
                combined[key]=list(rows)
        except Exception as exc:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":f"读取资产档案失败：{exc}"},ensure_ascii=False).encode())
        ident=str(body.get("id") or "").strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,63}",ident):
            ident=re.sub(r"[^a-z0-9]+","_",name.lower()).strip("_")[:56] or "asset"
        if any(str(item.get("id"))==ident for k in keys for item in combined[keys[k]]):
            return self._send(409,"application/json",json.dumps({"ok":False,"err":f"资产 id 已存在：{ident}"},ensure_ascii=False).encode())
        parent=str(body.get("parent_ref") or "").strip() or None
        relation=str(body.get("relation") or "").strip() or None
        prompt=str(body.get("prompt") or "").strip()
        ep=str(body.get("episode") or "").strip()
        item={"id":ident,"name":name,"asset_revision":1,"source_episode_ids":[ep] if ep else []}
        if kind=="character":
            item.update({"role":str(body.get("role") or ("配角" if parent else "主角")),"is_collective":False,"basis":"手工新增素材","sheet_prompt":prompt})
        elif kind=="scene":
            item.update({"time":str(body.get("time") or "日"),"light":str(body.get("light") or "日光"),"interior":bool(body.get("interior",True)),"geometry":[],"image_prompt":prompt})
        else:
            item.update({"kind":str(body.get("prop_kind") or ("关联素材" if parent else "叙事")),"asset_required":True,"owner":parent or "","actions":[],"image_prompt":prompt,"shot_hint":None})
        if parent:
            item["parent_ref"]=parent
            item["relation"]=relation or ("located_in" if parent.startswith("@scene:") else "component_of")
        related=body.get("related_refs")
        if isinstance(related,list) and related:
            item["related_refs"]=related
        combined[keys[kind]].append(item)
        # 提示词中的 @kind:id 是全局资产依赖：保存为 prompt_refs，
        # 同时并入 related_refs，后续资产图/分镜生成会自动带上引用图。
        if relmod and hasattr(relmod, "extract_asset_refs"):
            prompt_refs=relmod.extract_asset_refs(prompt, combined)
            if prompt_refs:
                item["prompt_refs"]=prompt_refs
                merged=list(item.get("related_refs") or [])
                for prompt_ref in prompt_refs:
                    if prompt_ref not in merged:
                        merged.append(prompt_ref)
                item["related_refs"]=merged
        normalized,issues=relmod.normalize_asset_relations(combined) if relmod else (combined,[])
        ref=f"@{kind}:{ident}"
        blocking=[issue for issue in issues if issue.get("ref")==ref]
        if blocking:
            return self._send(422,"application/json; charset=utf-8",json.dumps({"ok":False,"err":"新素材关系无法解析","issues":blocking},ensure_ascii=False).encode())
        try:
            versions_mod=tools_mod("versions.py")
            snapshot=getattr(versions_mod,"snapshot",None) if versions_mod else None
            for k,(path,raw,key) in file_data.items():
                next_rows=normalized.get(key,[])
                current_rows=raw.get(key,[]) if isinstance(raw,dict) else raw
                if current_rows==next_rows: continue
                def mutate(current,key=key,next_rows=next_rows):
                    if not isinstance(current,dict): return {key:next_rows}
                    current[key]=next_rows; return current
                if project_store:
                    project_store.update_json(path,mutate,create_default={key:next_rows},snapshot=snapshot)
                else:
                    if snapshot and os.path.isfile(path): snapshot(path)
                    os.makedirs(os.path.dirname(path),exist_ok=True)
                    raw[key]=next_rows
                    with open(path,"w",encoding="utf-8") as fh: json.dump(raw,fh,ensure_ascii=False,indent=1)
        except Exception as exc:
            return self._send(500,"application/json",json.dumps({"ok":False,"err":f"素材写入失败：{exc}"},ensure_ascii=False).encode())
        registry_mod=tools_mod("asset_registry.py")
        rows=registry_mod.AssetRegistry(d).list() if registry_mod else []
        created=next((row for row in rows if row.get("ref")==ref),{"ref":ref,"kind":kind,"id":ident,"name":name})
        return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"asset":created,"assets":rows},ensure_ascii=False).encode())

    @route('POST', '/api/assets/edit')
    def route_post_api_assets_edit(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        # 修改单个全局资产设定；只递增目标素材修订号，并返回受影响镜头。
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or "{}")
        d=proj_dir(body.get("project")); ref=str(body.get("ref") or "").strip()
        patch=body.get("patch") if isinstance(body.get("patch"),dict) else body.get("fields")
        if not d or not ref or not isinstance(patch,dict):
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"项目、ref、patch 参数不合法"},ensure_ascii=False).encode())
        try:
            mod=tools_mod("asset_service.py")
            if mod is None: raise ValueError("asset_service.py 缺失")
            result=mod.edit_asset(d,ref,patch,expected_revision=body.get("expected_revision"))
            registry_mod=tools_mod("asset_registry.py")
            rows=registry_mod.AssetRegistry(d).list() if registry_mod else []
            return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,**result,"assets":rows},ensure_ascii=False).encode())
        except Exception as exc:
            return self._send(400,"application/json; charset=utf-8",json.dumps({"ok":False,"err":str(exc)},ensure_ascii=False).encode())

    @route('POST', '/api/assets/relations')
    def route_post_api_assets_relations(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        # 保存项目级资产父子/派生/关联关系；关系只写入三件套 JSON，不展开长设定。
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        d=proj_dir(body.get("project"))
        updates=body.get("updates")
        if not d or not isinstance(updates,list) or not updates:
            return self._send(400,"application/json; charset=utf-8",json.dumps({"ok":False,"err":"项目或 updates 不合法"},ensure_ascii=False).encode())
        relmod=tools_mod("asset_relations.py")
        if relmod is None:
            return self._send(500,"application/json",json.dumps({"ok":False,"err":"资产关系模块缺失"},ensure_ascii=False).encode())
        kind_keys={"character":"characters","scene":"scenes","prop":"props"}
        file_data={}
        combined={}
        try:
            for kind,key in kind_keys.items():
                path=os.path.join(d,"素材",{"character":"人物.json","scene":"场景.json","prop":"道具.json"}[kind])
                if not os.path.isfile(path):
                    return self._send(404,"application/json",json.dumps({"ok":False,"err":f"资产档案不存在：{os.path.basename(path)}"},ensure_ascii=False).encode())
                raw=json.load(open(path,encoding="utf-8"))
                rows=raw.get(key) if isinstance(raw,dict) else raw
                if not isinstance(rows,list): rows=[]
                file_data[kind]=(path,raw,key)
                # 关系更新会原地修改 combined 中的 item；使用深副本，
                # 才能与磁盘原值比较并真正触发 project_store 写入。
                combined[key]=copy.deepcopy(rows)
        except Exception as exc:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":f"读取资产档案失败：{exc}"},ensure_ascii=False).encode())
        changed=[]
        for update in updates:
            if not isinstance(update,dict):
                continue
            raw_ref=str(update.get("ref") or "").strip().lstrip("@")
            if ":" not in raw_ref:
                return self._send(400,"application/json",json.dumps({"ok":False,"err":"关系更新缺少 @kind:id ref"},ensure_ascii=False).encode())
            head,ident=raw_ref.split(":",1)
            kind=relmod._kind(head) if hasattr(relmod,"_kind") else head
            key=kind_keys.get(kind)
            if not key or not ident:
                return self._send(400,"application/json",json.dumps({"ok":False,"err":f"关系 ref 不合法：{update.get('ref')}"},ensure_ascii=False).encode())
            item=next((x for x in combined[key] if isinstance(x,dict) and str(x.get("id") or "") == ident),None)
            if item is None:
                return self._send(404,"application/json",json.dumps({"ok":False,"err":f"找不到资产：@{kind}:{ident}"},ensure_ascii=False).encode())
            before_item=copy.deepcopy(item)
            for field in ("parent_ref","relation","derived_from"):
                if field in update:
                    value=update.get(field)
                    if value in (None,""):
                        item.pop(field,None)
                    else:
                        item[field]=value
            if "related_refs" in update:
                value=update.get("related_refs")
                if value in (None,""):
                    item.pop("related_refs",None)
                else:
                    item["related_refs"]=value if isinstance(value,list) else [value]
            if item != before_item:
                state_mod=tools_mod("production_state.py")
                if state_mod and hasattr(state_mod,"bump_asset_revision"):
                    state_mod.bump_asset_revision(item)
            changed.append(f"@{kind}:{ident}")
        normalized,issues=relmod.normalize_asset_relations(combined)
        changed_set=set(changed)
        # 父子关系固定为两层：母素材 -> 子素材。只检查本次请求改动的
        # 节点，避免历史项目中已有的旧关系阻塞无关的关系保存。
        normalized_by_ref={
            f"@{kind}:{item.get('id')}": item
            for kind,key in kind_keys.items()
            for item in (normalized.get(key) or [])
            if isinstance(item,dict) and item.get('id')
        }
        deep_parent_issues=[]
        for ref in changed_set:
            item=normalized_by_ref.get(ref)
            parent_ref=item.get('parent_ref') if item else None
            parent_item=normalized_by_ref.get(parent_ref) if parent_ref else None
            if parent_item and parent_item.get('parent_ref'):
                deep_parent_issues.append({
                    "ref": ref,
                    "field": "parent_ref",
                    "message": "资产关系最多两层，不能把资产挂到子素材下",
                })
        if deep_parent_issues:
            return self._send(422,"application/json; charset=utf-8",json.dumps(
                {"ok":False,"err":"资产关系最多两层，不能挂到子素材下","issues":deep_parent_issues},
                ensure_ascii=False).encode())
        blocking=[issue for issue in issues if issue.get("ref") in changed_set]
        if blocking:
            return self._send(422,"application/json; charset=utf-8",json.dumps({"ok":False,"err":"资产关系无法保存","issues":blocking},ensure_ascii=False).encode())
        try:
            versions_mod=tools_mod("versions.py")
            snapshot=getattr(versions_mod,"snapshot",None) if versions_mod else None
            revisions=body.get("expected_revisions") if isinstance(body.get("expected_revisions"),dict) else {}
            for kind,(path,raw,key) in file_data.items():
                next_rows=normalized.get(key,[])
                current_rows=raw.get(key,[]) if isinstance(raw,dict) else raw
                if current_rows == next_rows:
                    continue
                def mutate(current, key=key, next_rows=next_rows):
                    if not isinstance(current,dict):
                        return {key:next_rows}
                    current[key]=next_rows
                    return current
                expected=revisions.get(os.path.basename(path)) or revisions.get(kind)
                if project_store:
                    project_store.update_json(path,mutate,expected_revision=expected,snapshot=snapshot)
                else:
                    if snapshot: snapshot(path)
                    raw[key]=next_rows
                    with open(path,"w",encoding="utf-8") as fh:
                        json.dump(raw,fh,ensure_ascii=False,indent=1)
        except Exception as exc:
            if project_store and isinstance(exc,project_store.RevisionConflict):
                return self._send(409,"application/json",json.dumps({"ok":False,"err":str(exc)},ensure_ascii=False).encode())
            return self._send(500,"application/json",json.dumps({"ok":False,"err":f"关系写入失败：{exc}"},ensure_ascii=False).encode())
        try:
            registry_mod=tools_mod("asset_registry.py")
            rows=registry_mod.AssetRegistry(d).list() if registry_mod else []
        except Exception:
            rows=[]
        return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"updated":changed,"assets":rows,"warnings":[issue for issue in issues if issue.get("ref") not in changed_set]},ensure_ascii=False).encode())

    @route('POST', '/api/asset/image')
    def route_post_api_asset_image(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        # 资产设定图生图（人物三视图/场景/道具）
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        d=proj_dir(body.get("project"))
        if not d:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"项目不存在"},ensure_ascii=False).encode())
        cmd=[sys.executable,os.path.join(TOOLS,"gen_asset_images.py"),d,
             "--kind",str(body.get("kind") or "all")]
        if body.get("id"): cmd+=["--id",str(body["id"])]
        if body.get("force"): cmd+=["--force"]
        states_mode=str(body.get("states") or "include").strip()
        if states_mode in ("only","skip"): cmd+=["--states",states_mode]
        state_id=str(body.get("state_id") or "").strip()
        if state_id:
            if not re.fullmatch(r"[\w\-]{1,64}", state_id):
                return self._send(400,"application/json; charset=utf-8",json.dumps({"ok":False,"err":"state_id 不合法"},ensure_ascii=False).encode())
            cmd+=["--state-id",state_id]
        vendor_id=str(body.get("vendor_id") or "").strip()
        if vendor_id:
            vendor=next((v for v in load_vendors() if v.get("id")==vendor_id),None)
            if not vendor or not vendor.get("enabled") or not (vendor.get("models") or {}).get("image"):
                return self._send(400,"application/json; charset=utf-8",json.dumps(
                    {"ok":False,"err":"所选生图厂商未启用或未配置 image 模型"},ensure_ascii=False).encode())
            preflight = asset_image_preflight(d, str(body.get("kind") or "all"), body.get("id"), vendor)
            if not preflight.get("ok"):
                return self._send(400,"application/json; charset=utf-8",json.dumps(
                    {"ok":False,
                     "err":preflight.get("err") or "资产图片任务前置检查失败",
                     "asset_plan":preflight.get("plans") or []},ensure_ascii=False).encode())
            cmd+=["--vendor",vendor_id]
        # ComfyUI 是单队列串行执行：并发提交资产生图只会在 ComfyUI 侧排队，
        # 拖到客户端轮询超时还写不出文件（job_264 实证）；且并发写 资产图.json
        # 有索引竞争。同一时刻只允许一个 gen_asset_images 任务。
        with self.JLOCK:
            busy_asset=[j for j in self.JOBS.values() if j.get("status")=="running"
                        and any("gen_asset_images.py" in str(c) for c in (j.get("fullcmd") or []))]
        if busy_asset:
            return self._send(409,"application/json; charset=utf-8",json.dumps(
                {"ok":False,"err":f"已有资产生图任务运行中（#{busy_asset[0]['id']}），请等它完成再提交——生图后端单队列串行，并发提交只会排队超时"},ensure_ascii=False).encode())
        jid=self.spawn_job("creation",cmd)
        return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"id":jid,"job":True},ensure_ascii=False).encode())

    @route('POST', '/api/versions/restore')
    def route_post_api_versions_restore(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        rel=str(body.get("p") or ""); ts=str(body.get("ts") or "")
        pth=os.path.normpath(os.path.join(VIDEO,rel))
        if not under(VIDEO,pth) or not re.fullmatch(r"\d{8}_\d{6}(?:_\d+)?",ts):
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"路径或版本号不合法"},ensure_ascii=False).encode())
        import importlib.util as _iu
        _sp=_iu.spec_from_file_location("versions",os.path.join(TOOLS,"versions.py"))
        m=_iu.module_from_spec(_sp); _sp.loader.exec_module(m)
        try: m.restore(pth,ts)
        except Exception as e:
            return self._send_run_error(e)
        return self._send(200,"application/json",json.dumps({"ok":True},ensure_ascii=False).encode())

    @route('POST', '/api/storyboard/save')
    def route_post_api_storyboard_save(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        # 汇总表格编辑结果回写：整组 shots 替换（对象来自同文件，字段完整），覆写前走版本快照
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        d=proj_dir(body.get("project")); nm=safe_proj(body.get("name") or "")
        p=os.path.join(d,"分镜",nm) if d and nm else ""
        shots=body.get("shots")
        if not d or not nm.lower().endswith(".json") or not under(d,p) or not os.path.isfile(p) or not isinstance(shots,list) or not shots:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"项目/分镜/shots 不合法"},ensure_ascii=False).encode())
        # 字段级校验+清洗：dur 必须可转数值并夹取到 [1,60]；文本字段裁剪防超大；id 必填去重。
        # 坏行拒收（不让空串/非数字进分镜把下游 float() 打崩），版本快照兜底回滚。
        seen_ids=set(); cleaned=[]
        for idx,shot in enumerate(shots):
            if not isinstance(shot,dict) or not shot.get("id"):
                return self._send(400,"application/json",json.dumps({"ok":False,"err":f"第{idx+1}行缺 id"},ensure_ascii=False).encode())
            sid=str(shot["id"])
            if sid in seen_ids:
                return self._send(400,"application/json",json.dumps({"ok":False,"err":f"镜号重复: {sid}"},ensure_ascii=False).encode())
            seen_ids.add(sid)
            try:
                dur=float(shot.get("dur") if shot.get("dur") not in ("",None) else 4.0)
            except (TypeError,ValueError):
                return self._send(400,"application/json",json.dumps({"ok":False,"err":f"{sid} 时长不是数字: {shot.get('dur')!r}"},ensure_ascii=False).encode())
            if not (1.0<=dur<=60.0):
                return self._send(400,"application/json",json.dumps({"ok":False,"err":f"{sid} 时长超出 1~60s: {dur}"},ensure_ascii=False).encode())
            shot["dur"]=round(dur,2)
            for k in ("action","prompt","content","sound","lighting","rig","lens"):
                v=shot.get(k)
                if isinstance(v,str) and len(v)>2000:
                    shot[k]=v[:2000]
            cleaned.append(shot)
        try:
            tools_mod('production_studio.py').save_shots(d, nm, cleaned, body.get('revision'))
        except Exception as exc:
            return self._send_run_error(exc)
        return self._send(200,"application/json",json.dumps({"ok":True,"shots":len(shots)},ensure_ascii=False).encode())

    @route('POST', '/api/storyboard/delete')
    def route_post_api_storyboard_delete(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        # 删除分镜文件（及同名单镜 xlsx）；.versions/ 历史快照保留，可回滚恢复。
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        d=proj_dir(body.get("project")); nm=safe_proj(body.get("name") or "")
        p=os.path.join(d,"分镜",nm) if d and nm else ""
        if not d or not nm.lower().endswith(".json") or not under(d,p) or not os.path.isfile(p):
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"分镜不存在"},ensure_ascii=False).encode())
        deleted=[]
        for target in (p, os.path.join(d,"分镜",nm[:-5]+"_分镜脚本.xlsx")):
            if os.path.isfile(target) and under(d,target):
                os.remove(target); deleted.append(os.path.basename(target))
        return self._send(200,"application/json",json.dumps({"ok":True,"deleted":deleted},ensure_ascii=False).encode())

    @route('POST', '/api/storyboard/xlsx')
    def route_post_api_storyboard_xlsx(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        d=proj_dir(body.get("project")); nm=safe_proj(body.get("name") or "")
        p=os.path.join(d,"分镜",nm) if d and nm else ""
        if not d or not nm.lower().endswith(".json") or not under(d,p) or not os.path.isfile(p):
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"分镜不存在"},ensure_ascii=False).encode())
        jid=self.spawn_job("creation",[sys.executable,os.path.join(TOOLS,"export_storyboard_xlsx.py"),p])
        return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"id":jid,"job":True},ensure_ascii=False).encode())

    @route('POST', '/api/strategy/build')
    def route_post_api_strategy_build(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        d=proj_dir(body.get("project"))
        sb=str(body.get("storyboard") or "")
        if not d or not safe_proj(sb) or not sb.lower().endswith(".json") or not under(os.path.join(d,"分镜"),os.path.join(d,"分镜",sb)) or not os.path.isfile(os.path.join(d,"分镜",sb)):
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"项目/分镜不合法"},ensure_ascii=False).encode())
        jid=self.spawn_job("creation",[sys.executable,os.path.join(TOOLS,"strategy_map.py"),
                                       os.path.join(d,"分镜",sb)])
        return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"id":jid,"job":True},ensure_ascii=False).encode())

    @route('POST', '/api/production/prompts', '/api/production/episode', '/api/production/affected')
    def route_post_api_production_prompts(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        # 制作链路的确定性重建：只更新提示词/依赖索引，不调用媒体厂商。
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or "{}")
        d=proj_dir(body.get("project"))
        if not d:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"项目不存在"},ensure_ascii=False).encode())
        try:
            rebuild=tools_mod("rebuild_production.py")
            if rebuild is None:
                raise ValueError("rebuild_production.py 缺失")
            if u.path=="/api/production/affected":
                refs=body.get("changed_refs") or body.get("asset_refs") or []
                if not isinstance(refs,list) or not refs:
                    raise ValueError("changed_refs 必须是非空数组")
                from production_state import mark_stale_for_asset
                report=mark_stale_for_asset(d,[str(ref) for ref in refs])
                return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,**report},ensure_ascii=False).encode())
            episode=str(body.get("episode") or "").strip() or None
            shot_ids=body.get("shot_ids")
            if shot_ids is not None and not isinstance(shot_ids,list):
                raise ValueError("shot_ids 必须是数组")
            if u.path=="/api/production/episode":
                if not episode:
                    raise ValueError("episode 必填")
                report=rebuild.rebuild_episode(d,episode)
            else:
                report=rebuild.rebuild_prompts(d,episode=episode,shot_ids=shot_ids)
            return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,**report},ensure_ascii=False).encode())
        except Exception as exc:
            return self._send(400,"application/json; charset=utf-8",json.dumps({"ok":False,"err":str(exc)},ensure_ascii=False).encode())

    @route('POST', '/api/knowledge/card', '/api/knowledge/card/delete')
    def route_post_api_knowledge_card(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        if u.path=="/api/knowledge/card":
            cid=knowledge_save(str(body.get("skill") or "").strip(),str(body.get("trigger") or ""),
                               str(body.get("prescription") or "").strip(),str(body.get("example") or ""),
                               cid=str(body.get("id") or "") or None)
            return self._send_run_json(200 if cid else 400,{"ok":bool(cid),"id":cid,**({} if cid else {"err":"知识卡内容不合法"})})
        ok=knowledge_delete(str(body.get("id") or ""))
        return self._send_run_json(200 if ok else 404,{"ok":ok,**({} if ok else {"err":"知识卡不存在"})})

    @route('POST', '/api/script/episodes', '/api/script/overview', '/api/script/extract', '/api/script/storyboard', '/api/creation/package', '/api/creation/assemble', '/api/knowledge/build')
    def route_post_api_script_episodes(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        tool_path=os.path.join(TOOLS,"creation_pipeline.py")
        if u.path=="/api/knowledge/build":
            tool_path=os.path.join(TOOLS,"knowledge.py")
            jid=self.spawn_job("knowledge",[sys.executable,tool_path,"build"])
            return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"id":jid,"job":True},ensure_ascii=False).encode())
        d=proj_dir(body.get("project"))
        if not d:
            return self._send(400,"application/json",json.dumps({"ok":False,"err":"项目不存在"},ensure_ascii=False).encode())
        if u.path in ("/api/creation/package","/api/creation/assemble"):  # package 新名；assemble 旧名兼容
            sb=str(body.get("storyboard") or "")
            if not safe_proj(sb) or not sb.lower().endswith(".json") or not under(os.path.join(d,"分镜"),os.path.join(d,"分镜",sb)) or not os.path.isfile(os.path.join(d,"分镜",sb)):
                return self._send(400,"application/json",json.dumps({"ok":False,"err":"分镜不存在"},ensure_ascii=False).encode())
            cmd=[sys.executable,tool_path,"assemble",d,"--storyboard",sb]
        else:
            sub={"episodes":"episodes","overview":"overview","extract":"extract","storyboard":"storyboard"}[u.path.split("/")[-1]]
            cmd=[sys.executable,tool_path,sub,d]
            ep=str(body.get("episode") or "").strip()
            if sub in ("extract","storyboard"):
                text_error = storyboard_episode_error(d, ep or None)
                if text_error:
                    return self._send(400,"application/json; charset=utf-8",json.dumps({"ok":False,"err":text_error},ensure_ascii=False).encode())
                if ep:
                    cmd+=["--episode",ep]
        jid=self.spawn_job("creation",cmd)
        return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"id":jid,"job":True},ensure_ascii=False).encode())

    @route('POST', '/api/run')
    def route_post_api_run(self, ctx):
        u, q = ctx.url, ctx.query
        ln = ctx.content_length
        body=json.loads(self.rfile.read(ln).decode("utf-8","replace") or b"{}")
        step=body.get("step"); raw=body.get("args",[])
        mp={"shots":"extract_shots.py","subtitles":"extract_subtitles.py","speech":"extract_speech.py",
            "fixasr":"fix_transcript.py",
            "depth":"motion_depth.py",
            "scene_env":"gen_scene_env.py"}
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
                self.JOBS[jid]={"id":jid,"step":step,"status":"running","out":"已生成: "+sp+"\n发送到 Blender MCP…","err":"",
                                "cmd":["blender-mcp:9876",os.path.basename(sp)],"fullcmd":["blender-mcp:9876",sp],
                                "attempts":1,"attempt_id":f"{jid}-a1","write_revoked":False,"process_alive":False,
                                "started_at":time.time(),"updated_at":time.time()}
            self._evict_jobs()
            self._persist_job(jid)
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
                            out=("已生成脚本: "+sp+"\n"+resp)[-8000:],
                            updated_at=time.time())
                    self._persist_job(jid)
                except Exception as e:
                    with self.JLOCK:
                        self.JOBS[jid].update(status="failed",ok=False,
                            err="MCP 发送失败（脚本已生成，可在 ⑥ 页签点「发送构建脚本」重试；Blender 是否开着 9876？）: "+str(e),
                            updated_at=time.time())
                    self._persist_job(jid)
            threading.Thread(target=_mcp2,daemon=True).start()
            return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"id":jid,"job":True},ensure_ascii=False).encode())
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
            # 包一层：渲完帧序列后自动 ffmpeg 合成 mp4，避免产物只有 PNG 看不到结果
            jid=self.spawn_job(step,[sys.executable,os.path.join(TOOLS,"blender_render_wrap.py"),blend])
            return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"id":jid,"job":True},ensure_ascii=False).encode())
        if step=="blender_build":
            sp=os.path.normpath(os.path.join(VIDEO,raw[0] if raw else ""))
            if not under(VIDEO,sp) or not os.path.isfile(sp):
                return self._send(400,"application/json",json.dumps({"ok":False,"err":"构建脚本不存在"}).encode())
            code=open(sp,encoding="utf-8",errors="replace").read()
            with self.JLOCK:
                self.JOBSEQ[0]+=1; jid=self.JOBSEQ[0]
                self.JOBS[jid]={"id":jid,"step":step,"status":"running","out":"","err":"",
                                "cmd":["blender-mcp:9876",os.path.basename(sp)],"fullcmd":["blender-mcp:9876",sp],
                                "attempts":1,"attempt_id":f"{jid}-a1","write_revoked":False,"process_alive":False,
                                "started_at":time.time(),"updated_at":time.time()}
            self._evict_jobs()
            self._persist_job(jid)
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
                        self.JOBS[jid].update(status="done" if ok else "failed",ok=ok,out=resp[-8000:],
                            updated_at=time.time())
                    self._persist_job(jid)
                except Exception as e:
                    with self.JLOCK:
                        self.JOBS[jid].update(status="failed",ok=False,err="MCP 连接失败（Blender 是否开着 9876？）: "+str(e),
                            updated_at=time.time())
                    self._persist_job(jid)
            threading.Thread(target=_mcp,daemon=True).start()
            return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"id":jid,"job":True},ensure_ascii=False).encode())
        if step=="h264":
            src=os.path.normpath(os.path.join(VIDEO,raw[0]))
            if not under(VIDEO,src) or not os.path.isfile(src):
                return self._send(400,"application/json",json.dumps({"ok":False,"err":"文件不存在"}).encode())
            dst=src.rsplit(".",1)[0]+"_H264.mp4"
            jid=self.spawn_job(step,["ffmpeg","-y","-i",src,"-c:v","libx264","-pix_fmt","yuv420p","-movflags","+faststart",dst])
            return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"id":jid,"job":True},ensure_ascii=False).encode())
        scr=mp.get(step)
        if not scr and step in ("render","plan","lapdoc","docs","fill","draft","explain_ai","silhouette"):
            return self._send(410,"application/json",json.dumps({"ok":False,"err":f"步骤 {step} 已下线（legacy）——render 用 /api/whiterange/run；fill 用 /api/analysis/ai；explain_ai 用 /api/explain/run；其余已由创作/拆片专用路由取代"},ensure_ascii=False).encode())
        if not scr or not tool(scr): return self._send(500,"application/json",json.dumps({"ok":False,"err":"工具缺失:"+str(scr)}).encode())
        args=[]
        for i,a in enumerate(raw):
            a=str(a)
            if a.startswith("projects/"):
                p=os.path.normpath(os.path.join(VIDEO,a))
                if not under(VIDEO,p):
                    return self._send(400,"application/json",json.dumps({"ok":False,"err":"路径越界:"+a}).encode())
                if i>0 and str(raw[i-1])=="--out":
                    os.makedirs(os.path.dirname(p),exist_ok=True)
                elif i>0 and str(raw[i-1])=="--outdir":
                    os.makedirs(p,exist_ok=True)
                elif not (os.path.exists(p) or os.path.isdir(os.path.dirname(p))):
                    return self._send(400,"application/json",json.dumps({"ok":False,"err":"路径不存在:"+a}).encode())
                args.append(p)
            else:
                # 绝对路径（C:\ / /c/ / UNC）与含 ../ 的相对路径统一过 under-root
                # 校验（工作区根 + Downloads 只读源豁免），防止 --out 写出工作区。
                if looks_like_path_arg(a) and not run_arg_path_ok(a):
                    return self._send(400,"application/json",json.dumps({"ok":False,"err":"路径越界:"+a}).encode())
                args.append(a)
        jid=self.spawn_job(step,[sys.executable,tool(scr)]+args)
        return self._send(200,"application/json; charset=utf-8",json.dumps({"ok":True,"id":jid,"job":True},ensure_ascii=False).encode())


ROUTES = build_route_table(H)

def _pm_load():
    import importlib.util as _iu
    _sp=_iu.spec_from_file_location("pm_mod",os.path.join(TOOLS,"prompt_modules.py"))
    m=_iu.module_from_spec(_sp); _sp.loader.exec_module(m)
    return m
def _knowledge_mod():
    import importlib.util as _iu
    _sp=_iu.spec_from_file_location("knowledge",os.path.join(TOOLS,"knowledge.py"))
    m=_iu.module_from_spec(_sp); _sp.loader.exec_module(m)
    return m
def knowledge_load():
    return _knowledge_mod().load()
def knowledge_user():
    return _knowledge_mod().load_user()
def knowledge_query(text):
    return _knowledge_mod().query(text, k=4)
def knowledge_save(*a, **kw):
    return _knowledge_mod().save_card(*a, **kw)
def knowledge_delete(cid):
    return _knowledge_mod().delete_card(cid)
def _pm_save(sid, text):
    return _pm_load().save_system(sid, text)
def _pm_reset(sid):
    return _pm_load().reset_system(sid)
def skill_lib_list():
    import importlib.util as _iu
    spec=_iu.spec_from_file_location("skill_lib",os.path.join(TOOLS,"skill_lib.py"))
    m=_iu.module_from_spec(spec); spec.loader.exec_module(m)
    return m.list_skills()
def skill_lib_call(fn, *a):
    import importlib.util as _iu
    spec=_iu.spec_from_file_location("skill_lib",os.path.join(TOOLS,"skill_lib.py"))
    m=_iu.module_from_spec(spec); spec.loader.exec_module(m)
    return getattr(m, fn)(*a)
def heartbeat429(handler_cls):
    """429/卡死看门狗：每 30 分钟巡检任务表——
    ①failed 且错误命中 429/超时类 -> 不再自动重跑（避免限流期反复烧钱），封顶 attempts 并标记「待人工重试」；
    ②running 且 60 分钟无任何输出更新 -> 先撤销旧 attempt 并终止子进程树，退出后再重试（子进程卡死自愈）。
    巡检日志进 handler_cls.HEARTBEAT_LOG 供 /api/watchdog 查看。"""
    def _loop():
        while True:
            time.sleep(1800)
            acted=[]
            try:
                now=time.time()
                removed=cleanup_job_logs(now)
                if removed: acted.append(f"清理过期任务日志 {removed} 个")
                with handler_cls.JLOCK:
                    jobs=[dict(v) for v in (handler_cls.JOBS or {}).values()]
                for j in jobs:
                    jid=j.get("id")
                    blob=((j.get("out") or "")+(j.get("err") or "")).lower()
                    cmd_blob = " ".join(str(x) for x in (j.get("fullcmd") or j.get("cmd") or [])).lower()
                    # 生图 HTTP 超时可能只是客户端提前断开，服务端仍在生成；自动重跑会重复扣费。
                    # 让生图任务保持失败待人工确认，429/卡死等其它任务仍按原策略自愈。
                    image_create = (j.get("step") in ("create", "create_batch") and "--type image" in cmd_blob)
                    image_timeout = image_create and ("超时" in blob or "timeout" in blob)
                    hit=(j.get("status")=="failed" and j.get("attempts",1)<3
                         and not image_timeout
                         and (re.search(r"\b429\b|rate[\s_-]?limit", blob) is not None
                              or any(w in blob for w in ("超时","timeout","无输出","卡死"))))
                    stamp=j.get("updated_at", j.get("started_at", now))
                    # 生图请求等待期间可能长时间没有 stdout；只要子进程仍存活就不要按“卡死”杀掉重跑。
                    stale=(j.get("status")=="running" and now-float(stamp or 0)>3600
                           and not (image_create and j.get("process_alive")))
                    if not hit and not stale and j.get("status")!="cancelling":
                        continue
                    jj=None; full=None; retry=False
                    with handler_cls.JLOCK:
                        jj=handler_cls.JOBS.get(jid)
                        if not jj or jj.get("status") not in ("failed","running","cancelling"):
                            continue
                        if stale:   # 卡死：先撤销旧 attempt 写权限并终止进程，退出后才允许重试
                            jj["status"]="cancelling"; jj["ok"]=False; jj["write_revoked"]=True
                            jj["err"]="看门狗：60分钟无输出，正在终止旧任务"
                            proc=handler_cls.PROCS.get(jid)
                            if proc is not None:
                                try:
                                    # Windows: /T 连子进程树一起杀（白模/转码常再起 ffmpeg 子进程）
                                    subprocess.run(["taskkill","/T","/F","/PID",str(proc.pid)],capture_output=True,timeout=15)
                                except Exception:
                                    try: proc.terminate()
                                    except Exception: pass
                            handler_cls._persist_job(handler_cls, jid)
                            acted.append(f"#{jid} 卡死，终止旧任务")
                            continue
                        if jj.get("status")=="cancelling":
                            proc=handler_cls.PROCS.get(jid)
                            if proc is not None and proc.poll() is None:
                                jj["process_alive"]=True
                                continue
                            jj["process_alive"]=False
                            if jj.get("attempts", 1) >= 3:
                                jj["status"]="failed"; jj["ok"]=False
                                jj["err"]="看门狗：旧任务已终止，达到最大重试次数"
                                jj["finished_at"]=time.time()
                                continue
                        if jj.get("status")=="failed":
                            # 429/超时类失败不再自动重跑（避免限流期反复烧钱），标记待人工重试并封顶
                            jj["attempts"]=3
                            jj["err"]=((jj.get("err") or "")+"[看门狗：429/超时类失败，待人工重试]")[-4000:]
                            jj["updated_at"]=time.time()
                            handler_cls._persist_job(handler_cls, jid)
                            acted.append(f"#{jid} 429/超时失败，待人工重试")
                            continue
                        full=list(jj.get("fullcmd") or [])
                        if not full:
                            # 旧 schema 任务无 fullcmd（重启恢复的历史任务）：不可自动重跑，
                            # 直接封顶 attempts 防止每轮被反复标记/跳过，日志留痕一次。
                            jj["attempts"]=3
                            jj["err"]=(jj.get("err") or "")+"[无重跑命令，跳过自动重试]"
                            acted.append(f"#{jid} 无fullcmd封顶")
                            continue
                        retry=True
                        jj["attempts"]=jj.get("attempts",1)+1
                        aid=f"{jid}-a{jj['attempts']}"
                        jj.update(status="running",attempt_id=aid,write_revoked=False,process_alive=False,err="",started_at=time.time(),updated_at=time.time(),
                                  out=((jj.get("out") or "")+f"\n[看门狗] 卡死任务已终止，自动重试 第{jj['attempts']}次 {time.strftime('%H:%M')}\n")[-8000:])
                    if retry and full:
                        H._persist_job(H,jid)
                        H._job_work(H,jid,full,aid)
                        acted.append(f"#{jid} 自动重试(第{jj['attempts']}次)")
            except Exception as e:
                acted.append("巡检异常:"+str(e))
            if acted:
                handler_cls.HEARTBEAT_LOG.append(time.strftime("%m-%d %H:%M")+"  "+"；".join(acted))
                handler_cls.HEARTBEAT_LOG[:] = handler_cls.HEARTBEAT_LOG[-50:]
    t=threading.Thread(target=_loop,daemon=True); t.start(); return t
if __name__=="__main__":
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] 启动 Slate；Python {sys.version.split()[0]}；解释器 {sys.executable}", flush=True)
    if not os.path.isfile(os.path.join(WEBDIST, "index.html")):
        print("[部署未完成] 缺少新版前端：在 workbench/web 执行 npm ci 和 npm run build；首页将返回 503。", flush=True)
    load_llm_cfg()
    ensure_providers()
    restore_jobs(H)
    removed=cleanup_job_logs()
    if removed: print(f"已清理 {removed} 个 {JOB_LOG_KEEP_DAYS} 天前的任务日志")
    args=[a for a in sys.argv[1:] if a!="--lan"]
    port=int(args[0]) if args and args[0].isdigit() else 8775
    # 本机与局域网使用同一服务实例；
    # --lan 保留为兼容参数（已无作用）。安全兜底=key 文件/日志经 _deny_file 一律 403。
    host="0.0.0.0"
    if not hasattr(H,"HEARTBEAT_LOG"): H.HEARTBEAT_LOG=[]
    heartbeat429(H)
    try:
        httpd = ThreadingHTTPServer((host,port),H)
    except OSError as exc:
        raise SystemExit(f"启动失败，无法监听端口 {port}：{exc}。请检查是否已有工作台服务正在运行。")
    print(f"工作台: http://localhost:{port}（监听 0.0.0.0，局域网可达）", flush=True)
    httpd.serve_forever()

























