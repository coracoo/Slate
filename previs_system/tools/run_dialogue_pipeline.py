# -*- coding: utf-8 -*-
"""对白白模一键流水线：校验 -> 渲染对白mp4 -> 导出分镜xlsx -> ffmpeg转H.264。
用法: python run_dialogue_pipeline.py <storyboard.json> [--outdir DIR]
依赖: numpy/Pillow/opencv/openpyxl/ffmpeg。脚本会自动在 engine/ 与 tools/ 找组件。"""
import sys,os,subprocess,json,shutil,glob
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
def find_ffmpeg():
    p=shutil.which("ffmpeg")
    if p: return p
    cands=glob.glob(os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_*\ffmpeg-*\bin\ffmpeg.exe"))
    for c in cands:
        if os.path.exists(c): return c
    return None
def main():
    jpath=os.path.abspath(sys.argv[1])
    cfg=json.load(open(jpath,encoding="utf-8")); proj=cfg["project"]
    # 组件目录：优先 skill scripts，其次 previs_system
    here=os.path.dirname(os.path.abspath(__file__))            # tools/
    sysroot=os.path.dirname(here)                              # previs_system/
    skill=os.path.join(sysroot,"..",".codex","skills","video-previs","scripts")
    def pick(*rel):
        for base in [skill, sysroot+os.sep+"engine", sysroot+os.sep+"tools", here]:
            f=os.path.join(base,*rel) if not isinstance(rel,tuple) else os.path.join(base,*rel)
            if os.path.exists(f): return os.path.abspath(f)
        return None
    eng=pick("dialogue_engine.py"); xlsx=pick("export_storyboard_xlsx.py"); dlg=pick("export_dialogue.py")
    outdir=os.path.join(sysroot,"..","projects",proj,"白模")
    if "--outdir" in sys.argv: outdir=sys.argv[sys.argv.index("--outdir")+1]
    os.makedirs(outdir,exist_ok=True)
    py=sys.executable
    raw=os.path.join(outdir,proj+"_白模_raw.mp4"); mp4=os.path.join(outdir,proj+"_白模.mp4"); xl=os.path.join(outdir,proj+"_分镜表.xlsx")
    def run(cmd,must=True):
        print(">>>"," ".join(cmd)); r=subprocess.run(cmd)
        if must and r.returncode!=0: sys.exit("步骤失败")
    print("1) 渲染对白白模…")
    run([py,eng,jpath,raw])
    print("2) 导出分镜 Excel…")
    run([py,xlsx,jpath,xl])
    print("2b) 导出带时间戳台词(SRT/txt)…")
    if dlg: run([py,dlg,jpath,outdir],must=False)
    ff=find_ffmpeg()
    if ff:
        print("3) 转 H.264…")
        run([ff,"-y","-loglevel","error","-i",raw,"-c:v","libx264","-pix_fmt","yuv420p","-movflags","+faststart","-an",mp4])
        try: os.remove(raw)
        except Exception: pass
    else:
        os.rename(raw,mp4); print("(未找到 ffmpeg，保留 mp4v 编码)")
    print(f"完成：{proj}\n  {mp4}\n  {xl}")
if __name__=="__main__": main()
