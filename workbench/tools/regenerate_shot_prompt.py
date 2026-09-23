# -*- coding: utf-8 -*-
"""按单镜事实分别重写静态参考图与动态视频提示词。"""
import argparse
import json
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
CORE = os.path.abspath(os.path.join(HERE, "..", "..", "previs_system", "tools"))
if CORE not in sys.path:
    sys.path.insert(0, CORE)

import project_store
import versions
from creation_pipeline import FAST_THINK, chat_retry, parse_json, pick_vendor
from llm_openai import VendorClient

REF_RE = re.compile(r"@(character|scene|prop|style):[\w-]+")


def _refs(shot, style):
    refs = []
    for key, kind in (("actor_refs", "character"), ("prop_refs", "prop")):
        for item in shot.get(key) or []:
            raw = str(item or "").strip()
            if raw:
                refs.append(raw if raw.startswith("@") else f"@{kind}:{raw}")
    scene = str(shot.get("scene_ref") or "").strip()
    if scene:
        refs.append(scene if scene.startswith("@") else "@scene:" + scene)
    style_id = str(style.get("image") or "").strip()
    if style_id == "auto":
        style_id = ""  # E10：显式"自动"不是真实画风引用，不能拼出 @style:auto
    if style_id:
        refs.append("@style:" + style_id)
    return list(dict.fromkeys(refs))


def _facts(shot, style):
    """只传镜头事实；旧 prompt、演员目标与人物外观均不参与重写。"""
    keys = ("id", "content", "action", "shot_size", "angle", "camera_move", "rig", "lens",
            "lighting", "scene_ref", "actor_refs", "prop_refs", "negative", "lines", "dur")
    facts = {key: shot[key] for key in keys if shot.get(key)}
    facts["aspect_ratio"] = shot.get("aspect_ratio") or "16:9"
    facts["asset_refs"] = _refs(shot, style)
    facts["style_anchor"] = str(style.get("anchor") or "").strip()
    return facts


def regenerate_prompt(project, board_name, shot_id, *, media_type="image", vendor_id=None, client=None):
    if media_type not in ("image", "video"):
        raise ValueError("提示词类型只能是 image 或 video")
    project = os.path.abspath(os.fspath(project))
    board_name = os.path.basename(str(board_name or ""))
    if not board_name.endswith(".json") or board_name in (".", ".."):
        raise ValueError("分镜文件名无效")
    path = os.path.join(project, "分镜", board_name)
    board, _ = project_store.read_json(path)
    shot = next((s for s in board.get("shots") or [] if str(s.get("id")) == str(shot_id)), None)
    if not isinstance(shot, dict):
        raise ValueError("镜头不存在：" + str(shot_id))
    style_path = os.path.join(project, "剧本", "style.json")
    try:
        with open(style_path, encoding="utf-8") as fh:
            style = json.load(fh)
    except (OSError, ValueError):
        style = {}
    facts = _facts(shot, style)
    if not facts.get("action") and not facts.get("content"):
        raise ValueError("镜头缺少动作与剧情内容，无法可靠重写")
    allowed = set(facts["asset_refs"])
    common = (
        "你是短剧分镜提示词编剧。只根据给定 JSON 镜头事实重写 prompt_text，"
        "不要引用旧提示词或续写下一镜。返回严格 JSON：{\"prompt_text\":\"...\"}。"
        "只用 asset_refs 中的 @ 引用，不展开人物外貌和资产设定；绝不添加本镜 actor_refs 外的角色。"
        "固定 16:9 画幅，准确保持景别、角度、场景、动作和 negative。"
        "画风只用 style_anchor 与 @style 引用，不写‘白模动画风’或‘写实校园’等冲突标签。"
        "不要把声音或台词写成画面文字，不生成对白框、字幕和水印。"
    )
    if media_type == "image":
        system = common + (
            "这是单张静态分镜参考图提示词，约100～200字。"
            "以 content 所述事件作为画面冻结时刻；action 只提供这一时刻之前的必要上下文，后续动作留给视频提示词。"
            "只选择一个明确的关键时刻，写该时刻的姿态、视线、人物大小和画面位置；"
            "多个先后动作不可同时呈现；未出现在冻结时刻的人物即使列在 actor_refs 中也不能画入。"
            "只对可见资产使用 @ 引用，禁止句不要含 @ 引用。如果只有盖板弹开，不能画下一镜才出现的坠落人物。"
            "不要写连续动作过程、声音、运镜轨迹或时间轴。负面约束用简短禁止句。"
        )
    else:
        system = common + (
            "这是生视频提示词，约100～220字。按本镜 dur 描述起始状态、动作推进、结束状态与运镜轨迹，"
            "明确连续动作和人物演绎节奏。只能使用本镜 action 的动作阶段；不要写‘输出单张关键帧’或静帧。"
            "声音若有可描述为音轨，台词由后期叠加。"
        )
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": json.dumps(facts, ensure_ascii=False)}]
    if client is None:
        vendor = pick_vendor(vendor_id)
        client = VendorClient(vendor)
    answer = chat_retry(client, messages, max_tokens=1200, timeout=180, extra=FAST_THINK)
    data = parse_json(answer)
    prompt = str(data.get("prompt_text") or "").strip()
    if len(prompt) < 40:
        raise ValueError("模型返回的提示词过短，分镜未修改")
    unknown_refs = set(match.group(0) for match in REF_RE.finditer(prompt)) - allowed
    if unknown_refs:
        raise ValueError("模型引用了本镜之外的资产：" + "、".join(sorted(unknown_refs)))

    def apply(current):
        target = next((s for s in current.get("shots") or [] if str(s.get("id")) == str(shot_id)), None)
        if not isinstance(target, dict):
            raise ValueError("写回时镜头已不存在")
        if _facts(target, style) != facts:
            raise ValueError("生成期间分镜事实已变化，请重新生成提示词")
        if media_type == "image":
            target["prompt"] = prompt  # 旧消费方兼容
            target["prompt_text"] = prompt
            target["prompt_image"] = prompt
            target["prompt_source"] = "regenerated"
        else:
            target["prompt_video"] = prompt
            target["prompt_video_source"] = "regenerated"

    project_store.update_json(path, apply, snapshot=versions.snapshot)
    return {"shot_id": str(shot_id), "board": board_name, "media_type": media_type, "prompt_text": prompt}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("project")
    parser.add_argument("--board", required=True)
    parser.add_argument("--shot", required=True)
    parser.add_argument("--vendor")
    parser.add_argument("--type", choices=("image", "video"), default="image")
    args = parser.parse_args()
    result = regenerate_prompt(args.project, args.board, args.shot, media_type=args.type, vendor_id=args.vendor)
    print("[完成] 提示词已重写并保存：" + result["board"] + " / " + result["shot_id"])


if __name__ == "__main__":
    main()

