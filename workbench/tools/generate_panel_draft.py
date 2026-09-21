# -*- coding: utf-8 -*-
"""用文本模型从画格事实生成结构化画面草稿；系统策略与剧情输入严格分离。"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from creation_pipeline import FAST_THINK, chat_retry, parse_json, pick_vendor
from llm_openai import VendorClient
from storyboard_panels import fingerprint
from storyboard_panel_service import image_policy

REF_RE = re.compile(r"@(?:character|scene|prop):[\w-]+")


def generate_panel_draft(project_dir, panel, board, *, client=None, vendor_id=None):
    pid = str(panel.get("id") or "")
    if not pid:
        raise ValueError("画格缺少 ID")
    linked = set(str(x) for x in panel.get("shot_ids") or [])
    facts = []
    for shot in board.get("shots") or []:
        if isinstance(shot, dict) and str(shot.get("id")) in linked:
            facts.append({key: shot.get(key) for key in
                          ("id", "content", "action", "shot_size", "angle", "lighting")
                          if shot.get(key)})
    if not facts:
        raise ValueError("画格未关联有效镜头")
    policy = image_policy(project_dir)
    from prompt_modules import PANEL_DRAFT_SYS, sys_for
    system = sys_for("panel_draft", PANEL_DRAFT_SYS,
                     style_positive=str(policy.get("positive") or "保持项目统一画风"))
    user = {"panel": panel, "linked_shot_facts": facts}
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user, ensure_ascii=False)}]
    if client is None:
        client = VendorClient(pick_vendor(vendor_id))
    result = parse_json(chat_retry(client, messages, max_tokens=1400, timeout=180, extra=FAST_THINK))
    if not isinstance(result, dict):
        raise ValueError("模型返回的画格草稿不是对象")
    description = str(result.get("visual_description") or "").strip()
    if not description:
        raise ValueError("模型未返回画格描述")
    allowed = set(panel.get("visible_refs") or [])
    used = list(dict.fromkeys(result.get("used_refs") or []))
    unknown = (set(used) | set(REF_RE.findall(description))) - allowed
    if unknown:
        raise ValueError("模型引用了画格以外的资产：" + "、".join(sorted(unknown)))
    return {"panel_id": pid, "visual_description": description,
            "local_negative": [str(x) for x in (result.get("local_negative") or []) if str(x).strip()],
            "used_refs": used, "panel_spec_hash": fingerprint(panel),
            "policy_version": policy["version"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--board", required=True)
    ap.add_argument("--panel", required=True)
    ap.add_argument("--vendor")
    args = ap.parse_args()
    board_path = os.path.join(args.project, "分镜", os.path.basename(args.board))
    with open(board_path, encoding="utf-8") as fh:
        board = json.load(fh)
    panel = next((p for p in board.get("panels") or [] if p.get("id") == args.panel), None)
    if panel is None:
        raise ValueError("画格不存在")
    draft = generate_panel_draft(args.project, panel, board, vendor_id=args.vendor)
    outdir = os.path.join(args.project, "创作", "画格草稿")
    os.makedirs(outdir, exist_ok=True)
    filename = os.path.basename(args.board).removesuffix(".json") + "_" + args.panel + ".json"
    path = os.path.join(outdir, filename)
    try:
        import versions
        versions.snapshot(path)
    except ImportError:
        pass
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(draft, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    print("[完成] 画格草稿已生成：" + path)


if __name__ == "__main__":
    main()
