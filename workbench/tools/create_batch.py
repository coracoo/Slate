# -*- coding: utf-8 -*-
"""按 storyboard 串行执行创作任务，每个镜头对应一个 creation.json 条目。"""
import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CORE = os.path.abspath(os.path.join(HERE, "..", "..", "previs_system", "tools"))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
if CORE not in sys.path:
    sys.path.insert(0, CORE)
import creation_store
import prompt_assembler
import prompt_compiler
import project_store
from reference_limits import reference_limit


def _board(project, name):
    nm = os.path.basename(str(name or ""))
    path = os.path.realpath(os.path.join(project, "分镜", nm))
    root = os.path.realpath(os.path.join(project, "分镜"))
    if not nm.lower().endswith(".json") or os.path.commonpath([root, path]) != root or not os.path.isfile(path):
        raise ValueError("分镜不存在")
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or not isinstance(data.get("shots"), list):
        raise ValueError("分镜格式无效")
    return nm, data


def _append(path, item):
    def mutate(data):
        if not isinstance(data, dict):
            raise project_store.InvalidDocument("creation.json 顶层必须是对象")
        data.setdefault("items", []).append(item)
    project_store.update_json(path, mutate, create_default={"items": []})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--board", required=True)
    ap.add_argument("--type", choices=["image", "video"], default="image")
    ap.add_argument("--mode", choices=["generate", "edit"], default="generate", help="图片模式")
    ap.add_argument("--vendor", required=True)
    ap.add_argument("--providers", required=True)
    ap.add_argument("--shot-id", action="append", default=[], help="只执行指定镜头，可重复；"
                    "整板执行必须同时给 --all（不给又不用 --all 会直接拒绝，避免误烧全板）")
    ap.add_argument("--all", action="store_true", dest="all_shots",
                    help="显式声明整板执行（与 --shot-id 二选一）")
    a = ap.parse_args()
    project = os.path.abspath(a.project)
    board_name, board = _board(project, a.board)
    manifest = os.path.join(project, "创作", "creation.json")
    os.makedirs(os.path.dirname(manifest), exist_ok=True)
    selected = {str(x) for x in (a.shot_id or []) if str(x).strip()}
    if not selected and not a.all_shots:
        # 曾默认"未选=整板"：调用方漏传选择器就按全板计费（服务端已加闸，直跑 CLI 绕得过）
        print("[错误] 未指定 --shot-id；确认要整板执行请显式加 --all", flush=True)
        sys.exit(2)
    unknown = selected - {str(s.get("id")) for s in (board.get("shots") or [])}
    if unknown:
        print(f"[错误] 以下镜号不在分镜 {board_name} 中：{sorted(unknown)}", flush=True)
        sys.exit(2)
    shots = [s for s in (board.get("shots") or []) if not selected or str(s.get("id")) in selected]
    try:
        with open(a.providers, encoding="utf-8") as fh:
            vendors = json.load(fh).get("vendors") or []
    except Exception:
        vendors = []
    vendor_cfg = next((v for v in vendors if v.get("id") == a.vendor), None)
    model_slot = "image_edit" if a.type == "image" and a.mode == "edit" else a.type
    vendor_model = ((vendor_cfg or {}).get("models") or {}).get(model_slot, "")
    if a.vendor == "doubao" and a.type == "video":
        from llm_openai import VendorClient
        VendorClient.from_config(vendor_cfg or {}).validate_video_config(vendor_model)
    ref_cap = reference_limit(a.vendor, vendor_model, a.type, vendor_cfg)
    failed = 0
    for idx, shot in enumerate(shots, 1):
        if not isinstance(shot, dict) or not shot.get("id"):
            continue
        stage = "storyboard_image" if a.type == "image" else "video"
        compiled = prompt_compiler.compile_stage_prompt(stage, shot, project, mode=a.mode, board=board)
        bundle = {"prompt_assembled": compiled.get("content_prompt", ""),
                  "negative": compiled.get("negative_prompt", ""),
                  "asset_refs": compiled.get("asset_refs") or [],
                  "asset_context": compiled.get("asset_context") or {},
                  "prompt_json": compiled.get("prompt_json"),
                  "prompt_stage": compiled.get("stage", stage),
                  "prompt_system": compiled.get("system_prompt", ""),
                  "prompt_revision": compiled.get("prompt_revision", ""),
                  "asset_revisions": compiled.get("asset_revisions") or {}}
        ref_shot = prompt_assembler.reference_shot_for_prompt(shot, bundle["prompt_assembled"], a.type)
        refs = prompt_assembler.resolve_shot_refs(ref_shot, project, actors=board.get("actors"), board_name=board_name, max_refs=ref_cap, board=board, media_type=a.type)
        # 批量入口也消费已应用的演员表演，避免单镜与批量生成出现提示词分叉。
        actor_bundle = prompt_compiler.compile_shot(board, shot["id"], mode="stateful", media_type=a.type)
        prompt_text = bundle["prompt_assembled"]
        if a.type == "video" and actor_bundle.get("performance_used"):
            beats = [line for line in str(actor_bundle.get("prompt") or "").splitlines()
                     if line.startswith("演员表演节拍")]
            if beats:
                prompt_text += "\n" + beats[0]
        iid = time.strftime("%Y%m%d_%H%M%S") + "_%06d" % (int(time.time() * 1e6) % 1000000)
        outdir = os.path.join(project, "创作", iid)
        item = creation_store.make_item(iid, a.type, prompt_text, board=board_name,
                                        shot_id=str(shot["id"]), prompt_assembled=prompt_text,
                                        negative=bundle["negative"], refs=refs, vendor_id=a.vendor,
                                        asset_context=bundle.get("asset_context"), prompt_json=actor_bundle.get("prompt_json") or bundle.get("prompt_json"), asset_refs=actor_bundle.get("asset_refs") or bundle.get("asset_refs"), source_hash=actor_bundle.get("source_hash", ""),
                                        acting_mode=actor_bundle.get("mode_used", "stateful"), actor_performance_used=bool(actor_bundle.get("performance_used")),
                                        actor_warnings=actor_bundle.get("warnings") or [], image_mode=a.mode,
                                        prompt_stage=bundle.get("prompt_stage"), prompt_system=bundle.get("prompt_system"),
                                        prompt_revision=bundle.get("prompt_revision"), asset_revisions=bundle.get("asset_revisions"))
        _append(manifest, item)
        cmd = [sys.executable, os.path.join(HERE, "create_media.py"), "--type", a.type,
               "--mode", a.mode, "--prompt", prompt_text, "--negative", bundle["negative"],
               "--vendor", a.vendor, "--providers", a.providers, "--outdir", outdir,
               "--manifest", manifest, "--item-id", iid]
        if bundle.get("prompt_stage"):
            cmd += ["--prompt-stage", str(bundle["prompt_stage"])]
        if bundle.get("prompt_revision"):
            cmd += ["--prompt-revision", str(bundle["prompt_revision"])]
        if a.type == "image":
            aspect = (((actor_bundle.get("prompt_json") or bundle.get("prompt_json") or {}).get("camera") or {}).get("aspect_ratio")
                      if isinstance(actor_bundle.get("prompt_json") or bundle.get("prompt_json"), dict) else None) or "16:9"
            cmd += ["--size", str(aspect)]
        for ref in refs:
            cmd += ["--refs", os.path.join(project, ref["path"]), "--ref-purpose", str(ref.get("purpose") or "手动引用")]
            cmd += ["--ref-role", str(ref.get("reference_role") or "unspecified")]
            if ref.get("target_time_seconds") is not None:
                cmd += ["--ref-time", str(ref.get("target_time_seconds"))]
        print(f"[批量] {idx}/{len(shots)} {shot['id']}", flush=True)
        result = subprocess.run(cmd, cwd=project, check=False)
        if result.returncode != 0:
            failed += 1
            print(f"[批量] {shot['id']} 失败，继续后续镜头", flush=True)
    print(f"BATCH_DONE failed={failed} total={len(shots)}")
    if failed:
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        sys.exit(1)





