# -*- coding: utf-8 -*-
"""为一个固定分镜镜头生成并保存演员表演草稿。"""
import argparse
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
CORE = os.path.abspath(os.path.join(HERE, "..", "..", "previs_system", "tools"))
for path in (HERE, CORE):
    if path not in sys.path:
        sys.path.insert(0, path)

from actor_pipeline import build_request, run, save_performance_candidate, default_context, hydrate_actor_cards
import prompt_modules
from llm_openai import VendorClient
import skill_lib
import project_store
from artifact_provenance import artifact_hash
import versions


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--storyboard", required=True)
    ap.add_argument("--shot", required=True)
    ap.add_argument("--vendor", required=True)
    ap.add_argument("--providers", default=os.path.join(HERE, "..", "providers.json"))
    ap.add_argument("--context", default=None)
    ap.add_argument("--draft-only", action="store_true", help="只保存演员候选，不改正式分镜")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--mode", choices=("style", "stateful"), default="stateful")
    a = ap.parse_args()
    board_path = os.path.abspath(a.storyboard)
    if not os.path.isfile(board_path):
        print("[错误] 分镜不存在：" + board_path); return 1
    board, board_revision = project_store.read_json(board_path)
    project_dir = os.path.dirname(os.path.dirname(board_path))
    if a.context:
        with open(a.context, encoding="utf-8") as fh:
            context = json.load(fh)
    else:
        # 状态化运行始终带有可校验的最小连续性上下文；风格模式刻意不注入状态。
        context = (board.get("acting_context") or default_context(board, project_dir)) if a.mode == "stateful" else None
    if a.mode == "stateful":
        context = hydrate_actor_cards(context, project_dir, board)
    acting_skill = skill_lib.style_for(project_dir, "acting")
    request = build_request(board, a.shot, context=(context if a.mode == "stateful" else None), system_prompt=None, project_dir=project_dir)
    request["mode"] = a.mode
    actor_system, _ = prompt_modules.actor_perform_prompt(request, acting_skill or None)
    request["system_prompt"] = actor_system
    client = VendorClient(a.vendor, a.providers)

    def call_llm(messages):
        content = client.chat(messages, kind="text", max_tokens=5000, timeout=420,
                               temperature=0.35)
        return {"content": content}

    result = run(request, call_llm, max_attempts=2)
    if result.get("status") != "ready":
        print("[错误] 演员表演未通过校验：" + "; ".join(
            str(item.get("message")) for item in result.get("errors") or []))
        if a.draft_only:
            try:
                candidate = save_performance_candidate(board_path, a.shot, result, request=request, context=context, run_id=a.run_id, mode=a.mode)
                print("CANDIDATE:" + candidate["path"])
            except Exception as exc:
                print("[错误] 无效演员候选保存失败：" + str(exc))
        return 1
    if a.draft_only:
        candidate = save_performance_candidate(board_path, a.shot, result, request=request, context=context, run_id=a.run_id, mode=a.mode)
        print(f"[完成] 演员表演候选 {a.shot} -> {candidate['path']}")
        print("CANDIDATE:" + candidate["path"])
        return 0
    result["mode"] = a.mode
    def attach(current):
        for shot in current.get("shots") or []:
            if str(shot.get("id")) == str(a.shot):
                result["source_hash"] = artifact_hash(current, "prompt", "actor-v1")
                shot["performance"] = result
                return
        raise ValueError(f"找不到镜头：{a.shot}")
    try:
        project_store.update_json(board_path, attach, expected_revision=board_revision, snapshot=versions.snapshot)
    except project_store.RevisionConflict:
        print("[错误] 分镜在演员生成期间发生变化，已拒绝写入旧表演草稿；请重新生成")
        return 1
    print(f"[完成] 演员表演草稿 {a.shot} -> {board_path}")
    print("OUTPUT:" + board_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())









