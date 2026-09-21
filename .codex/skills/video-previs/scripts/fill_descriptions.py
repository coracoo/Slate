# -*- coding: utf-8 -*-
"""
AI 代填画面描述：storyboard.json + 源视频 -> 每镜抽帧喂视觉模型 -> 回填 shot 景别与 prompt_cn。
跳过已有实质描述的镜头（prompt_cn 含"待填"或为空才填）；--force 全部重填。
用法: python fill_descriptions.py <storyboard.json> --video <video> [--force]
"""
import sys,os,json,argparse,cv2
HERE=os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0,HERE)
import glm_client as G

SHOT2MODE={"全景":"wide","双人中景":"two","特写":"cu","近景":"cu","中景":"two","越肩":"ots"}
SYS="你是电影分镜师。看给出的视频帧，为该镜头输出严格 JSON：{\"shot\":\"<全景|双人中景|中景|近景|特写|越肩>\",\"prompt_cn\":\"<一句中文画面描述，40-70字，含景别、主体、动作、环境>\"}。只输出 JSON。"

def shot_frames(video,t0,t1,fps,maxn=3,wmax=512):
    cap=cv2.VideoCapture(video)
    n=0; parts=[]
    try:
        for k in range(maxn):
            t=t0+(t1-t0)*(0.15+0.35*k)
            cap.set(cv2.CAP_PROP_POS_FRAMES,int(t*fps))
            ok,f=cap.read()
            if not ok: continue
            h,w=f.shape[:2]
            if w>wmax: f=cv2.resize(f,(wmax,int(h*wmax/w)))
            ok,enc=cv2.imencode(".jpg",f,[cv2.IMWRITE_JPEG_QUALITY,78])
            if ok: parts.append(G.image_part(enc.tobytes())); n+=1
    finally: cap.release()
    return parts

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("src"); ap.add_argument("--video",required=True); ap.add_argument("--force",action="store_true")
    a=ap.parse_args()
    if not G.is_configured(): sys.exit("未配置 ZHIPUAI_API_KEY/GLM_API_KEY，无法 AI 代填")
    jp=os.path.abspath(a.src); vp=os.path.abspath(a.video)
    cfg=json.load(open(jp,encoding="utf-8"))
    fps=cfg.get("fps",24)
    t=0; done=0
    for i,sh in enumerate(cfg["shots"],1):
        dur=float(sh.get("dur",0)); t0,t1=t,t+dur; t=t1
        filled=(sh.get("prompt_cn") or "").strip()
        if filled and "待填" not in filled and not a.force:
            print(f"- 跳过 {sh.get('id',i)}（已有描述）"); continue
        parts=shot_frames(vp,t0,t1,fps)
        if not parts:
            print(f"! {sh.get('id',i)}: 抽帧失败，保留原值"); continue
        msg=" ".join(f"镜头{sh.get('id',i)}，时间段 {t0:.1f}-{t1:.1f}s，台词/字幕：{sh.get('line') or '（无）'}。" for _ in [0])
        try:
            out=G.chat([{"role":"system","content":SYS},
                        {"role":"user","content":[{"type":"text","text":msg}]+parts}],
                       model=G.vision_model(),timeout=90)
            d=G.extract_json(out)
            if d.get("shot") in SHOT2MODE:
                sh["shot"]=d["shot"]; sh["mode"]=SHOT2MODE[d["shot"]]
            if d.get("prompt_cn"): sh["prompt_cn"]=d["prompt_cn"][:120]
            done+=1
            print(f"✓ {sh.get('id',i)}: {sh.get('shot')} | {sh['prompt_cn'][:48]}")
        except Exception as e:
            print(f"! {sh.get('id',i)}: {e}")
    json.dump(cfg,open(jp,"w",encoding="utf-8"),ensure_ascii=False,indent=1)
    print(f"# 完成：{done}/{len(cfg['shots'])} 镜已代填 -> {os.path.basename(jp)}")
if __name__=="__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
