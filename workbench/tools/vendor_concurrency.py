# -*- coding: utf-8 -*-
"""厂商并发口径：云端 API 默认可并发，本机两类例外必须串行。

  local-comfyui   本机 ComfyUI 只有一个队列，同时塞多张只会互相抢显存、一起变慢
  chatgpt-queue   浏览器网页自动化（chrome-use 驱动一个标签页），本质是串行单张队列

其余厂商（火山/MiniMax/通义/阿里云万相/Gemini/可灵/Kimi/GLM/DeepSeek/Agnes/openai 兼容
以及任何 new-api 网关）都是服务端算力，本地不限并发——瓶颈只在网络与我们自己的进程数。
限流（429/5xx）由各调用点的指数退避重试兜底（文本 creation_pipeline.chat_retry、
生图 llm_openai.generate_image），不在这里预先压。生视频刻意不自动重试——
看门狗对 429 失败只标记「待人工重试」，避免限流期反复烧钱，这里沿用该口径。

用法:
  from vendor_concurrency import parallel_cap
  workers = parallel_cap(vendor_id, requested=4)     # 串行厂商返回 1
"""
import sys, os
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 必须串行的厂商 id（本机资源型/浏览器自动化型）
SERIAL_VENDORS = {"local-comfyui", "chatgpt-queue"}
# 兜底判据：id 里带这些片段的也按串行处理（用户自建的同质厂商条目）
SERIAL_ID_HINTS = ("comfyui", "comfy", "chatgpt", "browser")
DEFAULT_CAP = 8          # 云端厂商的默认并发上限（防手滑一次提几百个）
HARD_CAP = 16


def is_serial(vendor_id):
    text = str(vendor_id or "").strip().lower()
    if text in SERIAL_VENDORS:
        return True
    return any(hint in text for hint in SERIAL_ID_HINTS)


def parallel_cap(vendor_id, requested=4):
    """本次批量生成允许的并发数：串行厂商恒为 1，云端厂商取请求值并封顶。"""
    try:
        want = int(requested or 1)
    except (TypeError, ValueError):
        want = 1
    want = max(1, want)
    if is_serial(vendor_id):
        return 1
    return max(1, min(want, DEFAULT_CAP, HARD_CAP))
