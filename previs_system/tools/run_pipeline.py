# -*- coding: utf-8 -*-
"""一键流水线：校验 -> 白模渲染 -> 派生文档。任一失败即停。"""
import sys,os,subprocess
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass
HERE=os.path.dirname(os.path.abspath(__file__))
ROOT=os.path.dirname(HERE)
def run(cmd):
    print(">>>"," ".join(cmd)); r=subprocess.run(cmd)
    if r.returncode!=0: sys.exit(f"步骤失败: {cmd[-1]}")
if __name__=="__main__":
    jpath=os.path.abspath(sys.argv[1]); outdir=os.path.join(ROOT,"out"); os.makedirs(outdir,exist_ok=True)
    import json; proj=json.load(open(jpath,encoding="utf-8"))["project"]
    py=sys.executable
    run([py,os.path.join(HERE,"validate_storyboard.py"),jpath])
    run([py,os.path.join(ROOT,"engine","previs_engine.py"),jpath,os.path.join(outdir,proj+"_白模.mp4")])
    run([py,os.path.join(HERE,"export_docs.py"),jpath,outdir])
    print(f"✅ 全部完成：{proj}\n   out/{proj}_白模.mp4\n   out/{proj}_分镜脚本.md / .xlsx\n   out/{proj}_提示词.md")
