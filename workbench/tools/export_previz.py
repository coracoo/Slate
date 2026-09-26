# -*- coding: utf-8 -*-
"""逐镜导出「预演包」：干净预演帧 + 表演提示词 + manifest（图生视频输入包）

方法论来源: previs_system/docs/白模方法论-空间符号与三分法.md
白模帧在本链路里是图生视频模型(Seedance/Wan 等)的参考输入，必须是无 HUD 污染的干净画面。
本工具用 2D 引擎(clean 模式)逐镜出 1 张代表帧 + 同名 .txt(表演提示词+台词) + manifest.json
(含角色参考图清单，约定 projects/<项目>/拉片素材/角色参考/<角色名>.jpg|png)。

3D 灰模渲染(更高质量参考帧)走 blender_previs 生成脚本 → Blender 渲染，本包只登记不阻塞。

用法: python export_previz.py <分镜.json> [--frame mid|start] [--outdir 目录名]
输出: <项目>/白模/预演包_<分镜名>/  (S01.png / S01.txt / manifest.json)
stdout 末行: OUTPUT:<目录>  COUNT:<镜数>
"""
import sys, os, json, glob, argparse, importlib.util
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
import numpy as np
import cv2
try:
    from asset_refs import resolve_actor_ref
except ImportError:
    resolve_actor_ref = None
try:
    from artifact_provenance import artifact_hash
except ImportError:
    artifact_hash = None

VIDEO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def find_engine():
    """与 render_shot_range 相同的解析顺序：Skill 副本优先。"""
    for d in [os.path.join(VIDEO, ".codex", "skills", "video-previs", "scripts"),
              os.path.join(VIDEO, "previs_system", "engine")]:
        f = os.path.join(d, "dialogue_engine.py")
        if os.path.isfile(f):
            spec = importlib.util.spec_from_file_location("dialogue_engine_previz", f)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            return m
    return None


def find_ref(proj, name, actor_id=None):
    """按资产索引/id优先解析角色参考图，兼容旧拉片素材/角色参考目录。"""
    if resolve_actor_ref:
        found = resolve_actor_ref(proj, actor_id or "", name or "")
        if found:
            return os.path.relpath(found, proj).replace("\\", "/")
    for ext in (".jpg", ".jpeg", ".png"):
        p = os.path.join(proj, "拉片素材", "角色参考", name + ext)
        if os.path.isfile(p):
            return os.path.relpath(p, proj).replace("\\", "/")
    return None


def prompt_text(sh, actors, compiled=None):
    """txt 正文：头行元信息(给人/脚本看) + 表演提示词 + 台词。
    提示词按三分法只写表演意图；机位/景别信息已由预演帧表达，放头行注释不进提示词正文。"""
    head = f"# {sh['id']} | {sh.get('dur',2)}s | {sh.get('move','固定')} | scene={sh.get('scene','room')}"
    body = [head, ""]
    compiled_text = (compiled or {}).get("text") if isinstance(compiled, dict) else None
    if compiled_text or sh.get("prompt"):
        body.append(str(compiled_text or sh["prompt"]))
    lines = sh.get("lines") or []
    if lines and not compiled_text:
        body.append("")
        body.append("[台词]")
        for L in lines:
            nm = actors.get(L.get("speaker"), {}).get("name", L.get("speaker", "?"))
            body.append(f"{nm}：{L.get('line','')}")
    return "\n".join(body).strip() + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("storyboard")
    ap.add_argument("--frame", default="mid", choices=["mid", "start"], help="取帧位置：镜中(默认，代表构图)或镜始(首帧条件)")
    ap.add_argument("--outdir", default=None, help="输出目录名（默认 白模/预演包_<分镜名>）")
    a = ap.parse_args()
    jp = os.path.abspath(a.storyboard)
    if not os.path.isfile(jp):
        print(f"[错误] 分镜不存在: {jp}"); sys.exit(1)
    proj = os.path.dirname(os.path.dirname(jp))   # <项目>/分镜/x.json -> <项目>
    cfg = json.load(open(jp, encoding="utf-8"))
    try:
        from actor_pipeline import default_context, hydrate_actor_cards
        cfg["acting_context"] = hydrate_actor_cards(cfg.get("acting_context") or default_context(cfg), proj, cfg)
    except Exception as exc:
        # 预演仍可继续，但把角色卡回填异常写入日志，避免静默丢失演员信息。
        print(f"[警告] 演员卡自动填充失败：{exc}")
    shots = cfg.get("shots") or []
    if not shots:
        print("[错误] 分镜无 shots"); sys.exit(1)
    M = find_engine()
    if M is None:
        print("[错误] 找不到 dialogue_engine.py"); sys.exit(1)
    eng = M.E(cfg, clean=True)

    base = os.path.splitext(os.path.basename(jp))[0]
    outdir = os.path.join(proj, "白模", a.outdir or ("预演包_" + base))
    os.makedirs(outdir, exist_ok=True)

    try:
        from prompt_compiler import compile_shot
    except ImportError:
        compile_shot = None
    manifest = []
    import versions as _V
    t0 = 0.0
    for i, sh in enumerate(shots, 1):
        dur = float(sh.get("dur", 2))
        t = t0 + (dur * 0.5 if a.frame == "mid" else min(0.1, dur * 0.1))
        img = eng.frame(t)                      # BGR numpy
        ok, buf = cv2.imencode(".png", img)
        sid = str(sh.get("id", f"S{i}"))
        png = os.path.join(outdir, sid + ".png")
        _V.snapshot(png)
        buf.tofile(png)                          # 中文路径安全写盘
        compiled = compile_shot(cfg, sid, mode="stateful", media_type="video") if compile_shot else {}
        txt = os.path.join(outdir, sid + ".txt")
        _V.snapshot(txt)        # 覆写前留版（与 png 同规则：同目录 .versions/，保留最近 20 份）
        with open(txt, "w", encoding="utf-8") as fh:
            fh.write(prompt_text(sh, cfg.get("actors", {}), compiled))
        refs = {ac.get("name", aid): find_ref(proj, ac.get("name", ""), aid)
                for aid, ac in (cfg.get("actors") or {}).items()}
        manifest.append({
            "id": sid, "dur": round(dur, 2), "move": sh.get("move", "固定"),
            "cam": sh.get("cam"), "scene": sh.get("scene", "room"),
            "poses": sh.get("pose", {}), "frame": sid + ".png", "prompt_file": sid + ".txt",
            "lines": [{"speaker": (cfg.get("actors") or {}).get(L.get("speaker"), {}).get("name", L.get("speaker")),
                       "text": L.get("line")} for L in (sh.get("lines") or [])],
            "refs": refs,
            "prompt_json": compiled.get("prompt_json"),
            "asset_refs": compiled.get("asset_refs", []),
            "mode_used": compiled.get("mode_used", "baseline"),
            "performance_used": bool(compiled.get("performance_used")),
            "warnings": compiled.get("warnings", []),
            "source_hash": compiled.get("source_hash", ""),
        })
        t0 += dur
    mpath = os.path.join(outdir, "manifest.json")
    payload = {"storyboard": os.path.relpath(jp, proj).replace("\\", "/"),
               "project": cfg.get("project", os.path.basename(proj)),
               "frame_mode": a.frame, "count": len(manifest), "shots": manifest,
               "source_hash": artifact_hash(cfg, "previz", "2") if artifact_hash else "",
               "artifact_kind": "previz", "tool_version": "2"}
    _V.snapshot(mpath)
    with open(mpath, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    print(f"[完成] 预演包 {len(manifest)} 镜 -> {outdir}")
    print("OUTPUT:" + outdir)
    print("COUNT:" + str(len(manifest)))


if __name__ == "__main__":
    main()


