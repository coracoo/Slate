# -*- coding: utf-8 -*-
"""创作媒体路由与素材归档；不发起网络请求。"""
import json
import os
import re
import shutil
import uuid
from pathlib import Path


def image_route(cfg, has_refs, slot=""):
    """显式模型槽优先；未选槽时按参考图和已配置能力选择。"""
    models = cfg.get("models") or {}
    if slot and slot not in ("image", "image_edit"):
        raise ValueError("图片模型槽只能为 image 或 image_edit")
    slot = slot or ("image_edit" if has_refs and models.get("image_edit") else "image")
    if not models.get(slot):
        raise ValueError(f"厂商未配置 {slot} 模型")
    if slot == "image_edit" and not has_refs:
        raise ValueError("所选模型需要参考图片，请先加入参考图")
    # 云端多模态图片模型可在 image 槽内接收参考图，无须切换到另一能力槽。
    return slot, "edit" if slot == "image_edit" else "generate", models[slot]


def speech_binding(project, character_id, voice_id, vendor_id):
    """校验角色身份与音色；音色是厂商 ID，不把角色外观描述当音色。"""
    path = Path(project) / "素材" / "人物.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    rows = data.get("characters", []) if isinstance(data, dict) else data
    actor = next((r for r in rows if r.get("id") == character_id), None)
    if not actor:
        raise ValueError("语音必须绑定本项目中的有效角色")
    if not str(voice_id).strip():
        raise ValueError("请填写所选厂商的音色 ID")
    return {"character_id": character_id, "character_name": actor.get("name") or character_id,
            "voice_id": str(voice_id).strip(), "vendor_id": vendor_id}


def _safe(value):
    value = str(value)
    if not re.fullmatch(r"[\w.-]+", value) or value in (".", ".."):
        raise ValueError("素材标识不合法")
    return value


def register_output(project, item, output):
    """按任务 ID 幂等归档音频/视频，原始产出与生成记录均保留。"""
    import project_store
    import versions
    root = Path(project).resolve()
    iid = _safe(item["id"])
    kind = item["type"]
    if kind not in ("music", "speech", "video"):
        return {}
    source = Path(output).resolve()
    if not source.is_relative_to(root) or not source.is_file() or not source.stat().st_size:
        raise ValueError("待归档产出不存在或不在项目中")
    if kind == "speech":
        folder = root / "素材" / "语音" / _safe(item.get("character_id", ""))
    else:
        folder = root / ("拉片素材" if kind == "video" else "素材") / ("创作视频" if kind == "video" else "音乐")
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (iid + source.suffix.lower())
    if not target.exists():
        temp = target.with_name('.' + target.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            shutil.copyfile(source, temp)
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)
    rel = target.relative_to(root).as_posix()
    entry = {k: item.get(k, "") for k in ("id", "type", "board", "shot_id", "vendor_id", "character_id", "character_name", "voice_id")}
    entry["path"] = rel
    def mutate(data):
        data.setdefault("items", {})[iid] = entry
    project_store.update_json(str(root / "素材" / "生成媒体.json"), mutate,
                              create_default={"items": {}}, snapshot=versions.snapshot)
    return {"material_path": rel, **({"source_video": rel} if kind == "video" else {})}
