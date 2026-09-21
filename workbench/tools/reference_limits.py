# -*- coding: utf-8 -*-
"""厂商/模型的参考图输入能力。

参考图数量不是业务层的固定常量：分镜可关联完整资产清单，真正提交时才
按当前执行器能力校验。云端 Seedream/Seedance 与 GPT Image 系列支持至少
10 张输入；ComfyUI 仍受工作流 LoadImage 槽位限制，默认三张。
"""
import re

DEFAULT_REFERENCE_LIMIT = 3
EXPANDED_REFERENCE_LIMIT = 10


def _explicit_limit(cfg):
    """读取配置中可选的 reference_limit，非法值返回 None。"""
    if not isinstance(cfg, dict):
        return None
    value = cfg.get("reference_limit")
    if isinstance(value, dict):
        # 允许按能力槽位配置，例如 {"image": 10, "video": 10}。
        value = value.get("image") or value.get("video")
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def reference_limit(vendor_id="", model="", kind="", cfg=None):
    """返回当前厂商/模型一次请求允许的参考图数量。

    显式配置优先；未配置时识别 Seedream、Seedance、GPT Image/GPT2 系列
    为 10 张。未知 OpenAI 兼容模型仍保守使用 3 张，避免把不支持多图的
    接口请求打到线上。
    """
    vid = str(vendor_id or "").lower()
    name = str(model or "").lower()
    if kind == 'video':
        from video_profiles import capabilities
        profile = capabilities({**(cfg or {}), 'id': vid}, model)
        if profile['known']: return max(profile['max_refs'], 2 if profile['last_frame'] else 0)
    # 当前 ComfyUI 工作流适配器只有三个 LoadImage 槽位，配置不能突破执行器硬边界。
    if vid in {"local-comfyui", "comfyui"}:
        return DEFAULT_REFERENCE_LIMIT
    explicit = _explicit_limit(cfg)
    if explicit is not None:
        return explicit
    if vid in {"chatgpt-queue", "gemini"}:
        return EXPANDED_REFERENCE_LIMIT
    if vid == 'minimax' and kind == 'video':
        return 9
    if vid == 'aliyun' and kind == 'video':
        return 9
    if vid == 'kling' and kind == 'video':
        return 1
    # 豆包 Agent Plan 的 Seedream 生图、Seedance 图生视频多图输入。
    if "seedream" in name or "seedance" in name or ("gpt" in vid and vid not in {"local-comfyui", "comfyui"}):
        return EXPANDED_REFERENCE_LIMIT
    # GPT Image 1/1.5/2 以及用户配置中的 gpt2/gpt2.5 别名。
    if "gpt-image" in name or "gptimage" in name or re.search(r"gpt[ _-]?(?:image[ _-]?)?2(?:[._-]?5)?\b", name):
        return EXPANDED_REFERENCE_LIMIT
    return DEFAULT_REFERENCE_LIMIT


def reference_limit_for_client(client, kind=""):
    """从 VendorClient 取当前能力槽位的上限。"""
    model = ""
    try:
        model = client.model(kind) if kind else ""
    except Exception:
        model = ""
    return reference_limit(getattr(client, "id", ""), model, kind, getattr(client, "cfg", None))
