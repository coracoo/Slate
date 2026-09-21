# -*- coding: utf-8 -*-
"""ComfyUI 本地服务适配：API 工作流 -> 队列 -> history -> view。"""
import copy
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

class ComfyUIError(Exception):
    pass


def image_model_family(model):
    """先识别模型家族；未知模型必须由自定义工作流承接。"""
    name = str(model or "").lower().replace("-", "_")
    if re.search(r"qwen_image_2[._]1", name):
        return "qwen21"
    if "qwen" in name and "edit" in name:
        return "qwen_edit"
    if "z_image" in name:
        return "z_image"
    return "unknown"


def build_qwen21_workflow(prompt, negative, model, *, width, height, seed, reference_images=()):
    """按 Comfy-Org Qwen Image 2.1 模板组装，文生图与参考图共用编码节点。"""
    effective = str(prompt)
    if negative:
        effective += "\n画面中不要出现：" + str(negative)
    graph = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": model, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen3vl_8b_int8_convrot.safetensors", "type": "qwen_image", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_2.1_vae_bf16.safetensors"}},
        "4": {"class_type": "TextEncodeQwenImage21", "inputs": {"clip": ["2", 0], "prompt": effective, "negative_prompt": str(negative or ""), "resolution": 1024}},
        "6": {"class_type": "EmptyLatentImage", "inputs": {"width": int(width), "height": int(height), "batch_size": 1}},
        "7": {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["4", 0], "negative": ["4", 1], "latent_image": ["6", 0], "seed": int(seed), "steps": 25, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["3", 0]}},
        "10": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": "VideoWorkbench_Qwen21"}},
    }
    for i, name in enumerate(reference_images, 1):
        nid = str(20 + i)
        graph[nid] = {"class_type": "LoadImage", "inputs": {"image": name}}
        graph["4"]["inputs"][f"images.image_{i}"] = [nid, 0]
    if reference_images:
        graph["4"]["inputs"]["vae"] = ["3", 0]
        # 使用指定画幅的画布，参考图仅作为条件，避免母图三视图尺寸接管输出。
    return graph


def build_z_image_workflow(prompt, negative, model, *, width, height, seed):
    """适配当前实例已安装的 Z-Image Turbo INT8；负面要求显式并入正向文本。"""
    effective = str(prompt).strip()
    if negative:
        effective += "\n画面中不要出现：" + str(negative).strip()
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": model, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen_3_4b_fp8_mixed.safetensors",
                                                  "type": "lumina2", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": "ae.safetensors"}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": effective}},
        "5": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 1.73}},
        "6": {"class_type": "EmptySD3LatentImage", "inputs": {"width": int(width), "height": int(height),
                                                          "batch_size": 1}},
        "7": {"class_type": "KSampler", "inputs": {"model": ["5", 0], "seed": int(seed), "steps": 8,
                                                "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple",
                                                "positive": ["4", 0], "negative": ["9", 0],
                                                "latent_image": ["6", 0], "denoise": 1.0}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["3", 0]}},
        "9": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": ""}},
        "10": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": "VideoWorkbench"}},
    }


def build_qwen_image_edit_workflow(prompt, negative, model, *, width, height, seed,
                                   reference_images):
    """构造内置 Qwen Image Edit 2511 API 工作流。

    ComfyUI 的 Qwen Image Edit 节点把参考图作为条件输入，而不是把图片
    误当成普通文生图提示词。这里使用官方工作流的标准 40 步采样图，最多
    接收三张参考图；自定义 API 工作流仍可由 ``workflow_path`` 覆盖此图。
    ``reference_images`` 应该是已经上传到 ComfyUI 的文件名。
    """
    refs = [str(x or "").strip() for x in (reference_images or []) if str(x or "").strip()]
    if not refs:
        raise ComfyUIError("Qwen Image Edit 至少需要一张参考图")
    if len(refs) > 3:
        raise ComfyUIError("Qwen Image Edit 最多支持 3 张参考图")
    # 这些模型名与官方 Qwen Image Edit 2511 工作流的节点类型一致；实际
    # UNET 名称由环境页选择，CLIP/VAE 使用 ComfyUI 官方常用文件名。
    graph = {
        "161": {"class_type": "UNETLoader", "inputs": {
            "unet_name": str(model), "weight_dtype": "default"}},
        "162": {"class_type": "CLIPLoader", "inputs": {
            "clip_name": "qwen_2.5_vl_7b_fp8_scaled.safetensors",
            "type": "qwen_image", "device": "default"}},
        "146": {"class_type": "VAELoader", "inputs": {
            "vae_name": "qwen_image_vae.safetensors"}},
        "145": {"class_type": "ModelSamplingAuraFlow", "inputs": {
            "model": ["161", 0], "shift": 3.1}},
        "152": {"class_type": "CFGNorm", "inputs": {
            "model": ["145", 0], "strength": 1.0, "pre_cfg": False}},
        "147": {"class_type": "FluxKontextMultiReferenceLatentMethod", "inputs": {
            "conditioning": ["149", 0],
            "reference_latents_method": "index_timestep_zero"}},
        "148": {"class_type": "FluxKontextMultiReferenceLatentMethod", "inputs": {
            "conditioning": ["151", 0],
            "reference_latents_method": "index_timestep_zero"}},
        "156": {"class_type": "VAEEncode", "inputs": {
            "pixels": ["160", 0], "vae": ["146", 0]}},
        "169": {"class_type": "KSampler", "inputs": {
            "model": ["152", 0], "positive": ["148", 0], "negative": ["147", 0],
            "latent_image": ["156", 0], "seed": int(seed), "steps": 40,
            "cfg": 4.0, "sampler_name": "euler", "scheduler": "simple",
            "denoise": 1.0}},
        "158": {"class_type": "VAEDecode", "inputs": {
            "samples": ["169", 0], "vae": ["146", 0]}},
        "195": {"class_type": "SaveImage", "inputs": {
            "images": ["158", 0], "filename_prefix": "VideoWorkbench_QwenEdit"}},
    }

    # 输入节点按 ref1/ref2/ref3 的顺序固定，便于排查引用顺序和结果。
    load_ids = ["41", "83", "84"]
    scale_ids = ["160", "180", "181"]
    encoded_refs = []
    for index, (name, load_id, scale_id) in enumerate(zip(refs, load_ids, scale_ids), 1):
        graph[load_id] = {"class_type": "LoadImage", "inputs": {
            "image": name, "upload": "image"}}
        graph[scale_id] = {"class_type": "FluxKontextImageScale", "inputs": {
            "image": [load_id, 0]}}
        encoded_refs.append([scale_id, 0])

    negative_inputs = {
        "clip": ["162", 0], "prompt": str(negative or ""), "vae": ["146", 0],
    }
    positive_inputs = {
        "clip": ["162", 0], "prompt": str(prompt or ""), "vae": ["146", 0],
    }
    for index, image_ref in enumerate(encoded_refs, 1):
        negative_inputs["image" + str(index)] = image_ref
        positive_inputs["image" + str(index)] = image_ref
    graph["149"] = {"class_type": "TextEncodeQwenImageEditPlus", "inputs": negative_inputs}
    graph["151"] = {"class_type": "TextEncodeQwenImageEditPlus", "inputs": positive_inputs}
    # 保持 JSON 节点插入顺序中 LoadImage 在编码节点之前，日志和测试都能
    # 直接看到 ref1、ref2、ref3 的实际上传顺序。
    ordered = {}
    for key in ("161", "162", "146", "145", "152", *load_ids[:len(refs)],
                *scale_ids[:len(refs)], "149", "151", "147", "148", "156", "169", "158", "195"):
        if key in graph:
            ordered[key] = graph[key]
    return ordered


def render_workflow(template, *, prompt, negative, model, width, height, seed, uploaded_refs):
    """填 API 格式 JSON 占位符；存在参考图却无相应输入位时明确拒绝。"""
    if not isinstance(template, dict) or not template:
        raise ComfyUIError("ComfyUI 工作流必须是 API 格式节点对象")
    raw = json.dumps(template, ensure_ascii=False)
    slots = set(re.findall(r"\{\{ref(\d+)\}\}", raw))
    valid_slots = set()
    for node in template.values():
        if isinstance(node, dict) and node.get("class_type") == "LoadImage":
            image_slot = (node.get("inputs") or {}).get("image")
            match = re.fullmatch(r"\{\{ref(\d+)\}\}", image_slot) if isinstance(image_slot, str) else None
            if match:
                valid_slots.add(match.group(1))
    if slots != valid_slots:
        raise ComfyUIError("参考图占位符必须设置在 LoadImage.image 输入上")
    if len(uploaded_refs) > len(slots) or any(str(i) not in slots for i in range(1, len(uploaded_refs) + 1)):
        raise ComfyUIError("工作流没有足够的参考图 LoadImage 输入位；不能无图降级")
    if len(slots) > len(uploaded_refs):
        raise ComfyUIError("工作流要求的参考图数量多于本次输入")
    if negative and "{{negative}}" not in raw:
        raise ComfyUIError("自定义工作流未声明 {{negative}} 输入位，不能静默忽略负面提示词")
    params = {"prompt": str(prompt), "negative": str(negative), "model": str(model),
              "width": int(width), "height": int(height), "seed": int(seed)}
    for i, name in enumerate(uploaded_refs, 1):
        params["ref" + str(i)] = name
    def replace(value):
        if isinstance(value, dict):
            return {k: replace(v) for k, v in value.items()}
        if isinstance(value, list):
            return [replace(x) for x in value]
        if not isinstance(value, str):
            return value
        m = re.fullmatch(r"\{\{([\w]+)\}\}", value)
        if m:
            key = m.group(1)
            if key not in params:
                raise ComfyUIError("未知工作流占位符: " + key)
            return params[key]
        def sub(match):
            key = match.group(1)
            if key not in params:
                raise ComfyUIError("未知工作流占位符: " + key)
            return str(params[key])
        return re.sub(r"\{\{([\w]+)\}\}", sub, value)
    graph = replace(copy.deepcopy(template))
    if not any(isinstance(v, dict) and v.get("class_type") == "SaveImage" for v in graph.values()):
        raise ComfyUIError("工作流缺少 SaveImage 节点，无法回收产物")
    return graph


class ComfyUIClient:
    def __init__(self, base_url, api_key=""):
        self.base = str(base_url or "").rstrip("/")
        self.api_key = str(api_key or "")
        if not self.base.startswith(("http://", "https://")):
            raise ComfyUIError("ComfyUI base_url 必须是 HTTP(S) 地址")

    def _request(self, method, path, data=None, content_type="application/json", timeout=15):
        headers = {}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        if data is not None:
            headers["Content-Type"] = content_type
        request = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:600]
            raise ComfyUIError(f"ComfyUI HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ComfyUIError("ComfyUI 连接失败: " + str(exc)) from exc

    def _json(self, method, path, payload=None, timeout=15):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        return json.loads(self._request(method, path, data, timeout=timeout).decode("utf-8"))

    def _bytes(self, path, timeout=30):
        return self._request("GET", path, timeout=timeout)

    def probe(self, timeout=8):
        stats = self._json("GET", "/system_stats", timeout=timeout)
        if not isinstance(stats, dict) or "system" not in stats:
            raise ComfyUIError("ComfyUI /system_stats 返回异常")
        return stats

    def list_diffusion_models(self, timeout=15):
        result = self._json("GET", "/models/diffusion_models", timeout=timeout)
        return result if isinstance(result, list) else []

    def upload_image(self, path, timeout=60):
        if not os.path.isfile(path):
            raise ComfyUIError("参考图不存在: " + str(path))
        boundary = "----VideoWorkbench" + uuid.uuid4().hex
        name = os.path.basename(path)
        with open(path, "rb") as fh:
            raw = fh.read()
        if len(raw) > 30 * 1024 * 1024:
            raise ComfyUIError("单张 ComfyUI 参考图超过 30 MB")
        parts = [
            ("--" + boundary + "\r\nContent-Disposition: form-data; name=\"image\"; filename=\"" +
             name.replace('"', "") + "\"\r\nContent-Type: application/octet-stream\r\n\r\n").encode("utf-8"),
            raw,
            ("\r\n--" + boundary + "\r\nContent-Disposition: form-data; name=\"type\"\r\n\r\ninput" +
             "\r\n--" + boundary + "--\r\n").encode("utf-8"),
        ]
        response = self._request("POST", "/upload/image", b"".join(parts),
                                 "multipart/form-data; boundary=" + boundary, timeout=timeout)
        result = json.loads(response.decode("utf-8"))
        if not isinstance(result, dict) or not result.get("name"):
            raise ComfyUIError("ComfyUI 上传参考图未返回文件名")
        return str(result["name"])

    def run_video_workflow(self, graph, out_path, *, poll_interval=5, timeout=3600):
        queued = self._json("POST", "/prompt", {"prompt": graph}, timeout=30)
        prompt_id = str((queued or {}).get("prompt_id") or "")
        if not prompt_id:
            raise ComfyUIError("ComfyUI 未接受视频工作流：" + json.dumps(queued, ensure_ascii=False)[:500])
        self._save_task(out_path, prompt_id, "video")
        deadline = time.monotonic() + timeout
        history_path = "/history/" + urllib.parse.quote(prompt_id, safe="")
        while time.monotonic() < deadline:
            history = self._json("GET", history_path, timeout=15)
            entry = history.get(prompt_id) if isinstance(history, dict) else None
            if isinstance(entry, dict):
                status = entry.get("status") or {}
                if status.get("status_str") in ("error", "failed"):
                    raise ComfyUIError("ComfyUI H3 执行失败: " + json.dumps(status, ensure_ascii=False)[:500])
                for output in (entry.get("outputs") or {}).values():
                    if not isinstance(output, dict):
                        continue
                    # 原生 SaveVideo 也通过 images 返回 MP4；不能按字段名判断媒介。
                    candidates = [item for key in ("videos", "gifs", "images") for item in (output.get(key) or [])]
                    for video in candidates:
                        if not isinstance(video, dict) or not video.get("filename"):
                            continue
                        if os.path.splitext(str(video["filename"]))[1].lower() not in (".mp4", ".webm", ".mov", ".mkv", ".avi"):
                            continue
                        params = urllib.parse.urlencode({
                            "filename": video["filename"], "subfolder": video.get("subfolder", ""),
                            "type": video.get("type", "output")})
                        raw = self._bytes("/view?" + params, timeout=120)
                        if not raw:
                            raise ComfyUIError("ComfyUI 返回空视频")
                        with open(out_path, "wb") as fh:
                            fh.write(raw)
                        return out_path
                if status.get("completed") or status.get("status_str") == "success":
                    raise ComfyUIError("ComfyUI H3 工作流结束，但没有 SaveVideo 输出；prompt_id=" + prompt_id)
            time.sleep(poll_interval)
        raise ComfyUIError("ComfyUI H3 视频任务轮询超时，未取消上游任务；prompt_id=" + prompt_id)

    def _save_task(self, out_path, prompt_id, media_type):
        """接受后立即落盘远端 ID，长任务出错仍可按 ID 回收，不必重新生成。"""
        state = {"prompt_id":prompt_id, "base_url":self.base, "media_type":media_type,
                 "submitted_at":time.time(), "output_path":os.path.abspath(out_path)}
        with open(str(out_path) + ".comfy-task.json", "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
        print("[ComfyUI] 已入队 prompt_id=" + prompt_id, flush=True)

    def _queue_position(self, prompt_id, timeout=15):
        """返回 prompt_id 在 ComfyUI 队列中的状态：running / pending / None（不在队列）。"""
        try:
            data = self._json("GET", "/queue", timeout=timeout)
        except ComfyUIError:
            return None
        for entry in data.get("queue_running") or []:
            if isinstance(entry, list) and len(entry) > 1 and str(entry[1]) == str(prompt_id):
                return "running"
        for entry in data.get("queue_pending") or []:
            if isinstance(entry, list) and len(entry) > 1 and str(entry[1]) == str(prompt_id):
                return "pending"
        return None

    def run_workflow(self, graph, out_path, *, poll_interval=2, timeout=600, queue_timeout=3600):
        queued = self._json("POST", "/prompt", {"prompt": graph}, timeout=30)
        prompt_id = str((queued or {}).get("prompt_id") or "")
        if not prompt_id:
            raise ComfyUIError("ComfyUI 未接受工作流：" + json.dumps(queued, ensure_ascii=False)[:500])
        self._save_task(out_path, prompt_id, "image")
        deadline = time.monotonic() + timeout
        queue_deadline = time.monotonic() + max(queue_timeout, timeout)
        history_path = "/history/" + urllib.parse.quote(prompt_id, safe="")
        last_note = 0.0
        while True:
            history = self._json("GET", history_path, timeout=15)
            entry = history.get(prompt_id) if isinstance(history, dict) else None
            if isinstance(entry, dict):
                status = entry.get("status") or {}
                if status.get("status_str") in ("error", "failed"):
                    errors = [msg[1] for msg in status.get("messages", [])
                              if isinstance(msg, list) and len(msg) > 1 and msg[0] == "execution_error"]
                    detail = errors[-1] if errors else {}
                    message = detail.get("exception_message") or json.dumps(status, ensure_ascii=False)
                    raise ComfyUIError(f"ComfyUI 工作流执行失败；prompt_id={prompt_id}；"
                                       f"节点 {detail.get('node_id', '?')} {detail.get('node_type', '')}：{message}")
                for output in (entry.get("outputs") or {}).values():
                    for image in (output.get("images") or []) if isinstance(output, dict) else []:
                        if image.get("type") not in ("output", "temp"):
                            continue
                        params = urllib.parse.urlencode({
                            "filename": image.get("filename", ""),
                            "subfolder": image.get("subfolder", ""),
                            "type": image.get("type", "output"),
                        })
                        raw = self._bytes("/view?" + params, timeout=60)
                        if not raw:
                            raise ComfyUIError("ComfyUI 返回空图片")
                        with open(out_path, "wb") as fh:
                            fh.write(raw)
                        return out_path
                if status.get("completed") or status.get("status_str") == "success":
                    raise ComfyUIError("ComfyUI 工作流结束，但没有 SaveImage 输出；prompt_id=" + prompt_id)
            now = time.monotonic()
            if now < deadline:
                time.sleep(poll_interval)
                continue
            # 执行超时先看是不是还排在 ComfyUI 队列里：排队宽限到 queue_deadline 并周期打日志，
            # 避免把前面任务的排队等待误判成本工作流失败。
            pos = self._queue_position(prompt_id)
            if pos and now < queue_deadline:
                if now - last_note >= 30:
                    print("[ComfyUI] 工作流" + ("排队中" if pos == "pending" else "执行中") + "，等待出图...", flush=True)
                    last_note = now
                time.sleep(poll_interval)
                continue
            if pos:
                raise ComfyUIError("ComfyUI 工作流排队超时：队列积压，任务仍在 ComfyUI 队列中，稍后可能会出图，请稍后检查文件")
            raise ComfyUIError("ComfyUI 工作流轮询超时：任务已不在队列也无历史记录，ComfyUI 可能重启丢失了该任务")


def dimensions_for_ratio(ratio):
    sizes = {
        "16:9": (1024, 576), "9:16": (576, 1024), "1:1": (1024, 1024),
        "4:3": (1024, 768), "3:4": (768, 1024),
        "3:2": (1152, 768), "2:3": (768, 1152),
        "21:9": (1344, 576),
    }
    return sizes.get(str(ratio or "16:9"), (1024, 576))


def load_workflow_template(workflow_path):
    """只允许加载工作台 workflows 目录内的 API 格式 JSON。"""
    root = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "workflows"))
    path = os.path.realpath(os.path.join(root, str(workflow_path or "")))
    if not (path.startswith(root + os.sep) and path.lower().endswith(".json") and os.path.isfile(path)):
        raise ComfyUIError("工作流文件须位于 workbench/workflows 下，且为 .json")
    with open(path, encoding="utf-8") as fh:
        result = json.load(fh)
    if isinstance(result, dict) and isinstance(result.get("prompt"), dict):
        result = result["prompt"]
    if not isinstance(result, dict) or not result:
        raise ComfyUIError("工作流不是 ComfyUI API 格式节点 JSON")
    return result


def _generate_image(self, prompt, out_path, *, model, negative_prompt="", image_refs=None,
                    ratio="16:9", workflow_path="", timeout=600):
    refs = list(image_refs or [])
    if len(refs) > 3:
        raise ComfyUIError("当前 ComfyUI 适配器最多支持 3 张参考图")
    width, height = dimensions_for_ratio(ratio)
    seed = int.from_bytes(os.urandom(8), "big") & ((1 << 63) - 1)
    mode = "custom_workflow" if workflow_path else ""
    if workflow_path:
        template = load_workflow_template(workflow_path)
        slots = set(re.findall(r"\{\{ref(\d+)\}\}", json.dumps(template, ensure_ascii=False)))
        if len(refs) != len(slots) or any(str(i) not in slots for i in range(1, len(refs) + 1)):
            raise ComfyUIError("自定义工作流的参考图输入位与本次参考图数量不匹配")
        uploaded = [self.upload_image(path) for path in refs]
        graph = render_workflow(template, prompt=prompt, negative=negative_prompt,
                                model=model, width=width, height=height, seed=seed,
                                uploaded_refs=uploaded)
        effective_prompt = prompt
        negative_mode = "workflow_input"
    else:
        family = image_model_family(model)
        if family == "qwen21":
            uploaded = [self.upload_image(path) for path in refs]
            graph = build_qwen21_workflow(prompt, negative_prompt, model,
                                         width=width, height=height, seed=seed,
                                         reference_images=uploaded)
            effective_prompt = graph["4"]["inputs"]["prompt"]
            negative_mode = "positive_text_fallback" if negative_prompt else "none"
            mode = "qwen21_builtin_edit" if refs else "qwen21_builtin_t2i"
        elif refs:
            if family == "qwen_edit":
                # 选中 image_edit 模型即可使用内置 Qwen 图生图链路；只有
                # 用户主动填写自定义 workflow_path 时才走占位符工作流。
                uploaded = [self.upload_image(path) for path in refs]
                graph = build_qwen_image_edit_workflow(
                    prompt, negative_prompt, model, width=width, height=height,
                    seed=seed, reference_images=uploaded,
                )
                effective_prompt = prompt
                negative_mode = "qwen_builtin_conditioning"
                mode = "qwen_builtin_edit"
            else:
                raise ComfyUIError("默认 Z-Image 工作流没有参考图输入位；请配置带 LoadImage 占位符的 API 工作流")
        elif family == "z_image":
            graph = build_z_image_workflow(prompt, negative_prompt, model,
                                           width=width, height=height, seed=seed)
            effective_prompt = graph["4"]["inputs"]["text"]
            negative_mode = "positive_text_fallback" if negative_prompt else "none"
            mode = "z_image_builtin"
        else:
            raise ComfyUIError(f"模型 {model} 没有匹配的内置文生图工作流；请选择兼容模型或配置专用 API 工作流")
    self.last_request = {
        "workflow": graph, "model": model, "seed": seed, "width": width, "height": height,
        "effective_prompt": effective_prompt, "negative_mode": negative_mode,
        "reference_count": len(refs), "mode": mode or "z_image_builtin",
    }
    print(f"[ComfyUI] 工作流={mode}；模型={model}；参考图={len(refs)}", flush=True)
    return self.run_workflow(graph, out_path, timeout=timeout)


ComfyUIClient.generate_image = _generate_image
