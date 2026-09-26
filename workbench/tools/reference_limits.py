# -*- coding: utf-8 -*-
"""厂商/模型的参考图输入能力。

参考图数量不是业务层的固定常量：分镜可关联完整资产清单，真正提交时才
按当前执行器能力校验。云端 Seedream/Seedance 与 GPT Image 系列支持至少
10 张输入；ComfyUI 的上限看**具体工作流/模型家族**（2511 三个槽位、2.1 按张数动态建节点、
Z-Image 没有输入位），见 reference_limit() 内的分支。
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
    # ComfyUI 的上限由**工作流自己**决定，不是厂商级常量：
    # · 内置 Qwen Image Edit 2511 只有 ref1/ref2/ref3 三个 LoadImage 位；
    # · 内置 Qwen Image 2.1 的组装器是「每张参考图建一个 LoadImage 节点」，没有三张的硬上限；
    # · Z-Image 那条根本没有参考图输入位。
    # 原先这里对 local-comfyui 一律回 3，等于把 2.1 也按 2511 卡死——⑦ 的「整 V 直出宫格」
    # 带 4 张以上参考图就是这么被本地拒掉的（还没出门就拒，白白失败一次任务）。
    if vid in {"local-comfyui", "comfyui"}:
        from comfyui_client import image_model_family
        family = image_model_family(model)
        if family == "qwen_edit":
            return DEFAULT_REFERENCE_LIMIT      # 2511 内置链只有 ref1/ref2/ref3 三个 LoadImage 硬槽
        if family == "z_image":
            return 0                            # 该链没有参考图输入位
        explicit = _explicit_limit(cfg)
        if explicit is not None:
            return explicit                     # 配置里可按本机实际下调
        return 16 if family == "qwen21" else DEFAULT_REFERENCE_LIMIT
        # 2.1 的 TextEncodeQwenImage21 是 autogrow image_1~16（object_info 实测）
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
