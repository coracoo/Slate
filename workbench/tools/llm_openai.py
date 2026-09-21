# -*- coding: utf-8 -*-
"""
通用厂商薄客户端（AI 短片分析工作台 阶段二 · providers v2）
用法: from llm_openai import VendorClient
      c = VendorClient("glm", providers_json="workbench/providers.json")
      c.chat([{"role":"user","content":"hi"}], max_tokens=1, timeout=15)
      c.chat([{"role":"user","content":[{"type":"text","text":"看图"},
             {"type":"image_url","image_url":{"url":"data:image/jpeg;base64,..."}}]}])
      c.generate_image(prompt, out_path="img_1.png")
      c.generate_video(prompt, image_refs=["ref.jpg"], out_path="vid_1.mp4")
      c.list_models()
设计: 一个厂商 = 一次 base_url + api_key；能力差异在 models[kind] 与 endpoints[kind]，
      由后端按 kind dispatch（text|vision|image|image_edit|video|music|speech）。
说明: 仅标准库 urllib（自动读取 https_proxy/http_proxy 环境变量）；
      豆包 Ark 视频走 /contents/generations/tasks 任务制（拿到真 key 后按实际响应微调）。
"""
import sys, os, json, time, base64, mimetypes, urllib.request, urllib.error, urllib.parse, re
_TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
if _TOOL_DIR not in sys.path: sys.path.insert(0, _TOOL_DIR)
from reference_limits import reference_limit
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

DEFAULT_PROVIDERS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "providers.json")

from provider_catalog import KINDS
DEFAULT_ENDPOINTS = {"text": "/chat/completions", "vision": "/chat/completions",
                     "image": "/images/generations", "image_edit": "/images/generations",
                     "video": "/videos/generations",
                     "music": "/music/generations", "speech": "/audio/speech"}

def load_vendors(path=None):
    """读 providers.json 的 vendors 数组（v2 结构）。"""
    p = path or DEFAULT_PROVIDERS
    try:
        data = json.load(open(p, encoding="utf-8"))
        return data.get("vendors") or []
    except Exception:
        return []

def mask_key(k):
    """api_key 掩码：前4****后4；过短则退回占位文案。"""
    if not k: return ""
    return (k[:4] + "****" + k[-4:]) if len(k) > 8 else "已配置"

class VendorError(Exception):
    """带中文说明的厂商调用错误。"""

class VendorClient:
    def __init__(self, vendor_id, providers_json=None, check_enabled=True):
        vendors = load_vendors(providers_json)
        cfg = next((v for v in vendors if v.get("id") == vendor_id), None)
        if not cfg:
            raise VendorError(f"厂商不存在: {vendor_id}（请在 providers.json 中配置）")
        self._setup(cfg, check_enabled)

    @classmethod
    def from_config(cls, cfg, check_enabled=True):
        """直接以内存配置构造（草稿测试用，不落盘、不查 vendors 列表）。"""
        self = cls.__new__(cls)
        self._setup(cfg, check_enabled)
        return self

    def _setup(self, cfg, check_enabled):
        self.id = cfg.get("id", "draft")
        if self.id == "local-codex":
            raise VendorError("本机 Codex 登录态生图已退役，请使用 ChatGPT · chrome-use")
        self.cfg = cfg
        if check_enabled and not self.cfg.get("enabled", False):
            raise VendorError(f"厂商 {self.id} 未启用")
        base = (self.cfg.get("base_url") or "").rstrip("/")
        if base and self.id != "local-codex" and not base.startswith(("http://", "https://")):
            raise VendorError(f"厂商 {self.id} 的 base_url 不合法: {base}")
        if self.id == "doubao" and base != "https://ark.cn-beijing.volces.com/api/plan/v3":
            raise VendorError("豆包 Agent Plan 只能使用 https://ark.cn-beijing.volces.com/api/plan/v3，已拒绝普通 /api/v3 接口")
        self.base = base
        self.models = self.cfg.get("models") or {}
        self.endpoints = dict(self.cfg.get("endpoints") or {})
        if self.id in ("doubao", "doubao-api"):
            required = {"image": "/images/generations", "video": "/contents/generations/tasks"}
            for kind, endpoint in required.items():
                # 草稿/单能力厂商可以只配置 Seedream 生图或只配置 Seedance 视频；
                # 未启用的能力不应因为另一能力的端点缺省而阻断初始化。
                if not self.models.get(kind) and not self.endpoints.get(kind):
                    continue
                actual = str(self.endpoints.get(kind) or endpoint).strip().rstrip("/")
                if actual == self.base + endpoint:
                    actual = endpoint
                if actual != endpoint:
                    raise VendorError(f"火山方舟 {kind} 创建接口应填写完整地址 {self.base + endpoint}，或相对路径 {endpoint}；Base URL 保持 {self.base}")
                self.endpoints[kind] = actual

    def model(self, kind):
        """取该厂商某能力的模型名；未配置抛中文错误。"""
        m = self.models.get(kind) or ""
        if not m:
            raise VendorError(f"厂商 {self.id} 未配置 {kind} 模型")
        return m

    def endpoint(self, kind):
        return self.endpoints.get(kind) or DEFAULT_ENDPOINTS[kind]

    def validate_video_config(self, model=None):
        """无网络配置预检，供入口和批处理在落任务之前共用。"""
        resolved = model or self.model("video")
        if self.id == "doubao" and resolved == "doubao-seedance-1.5-pro":
            raise VendorError(
                "火山 Agent Plan 不支持当前 Seedance 1.5 Pro 配置。请在环境检查中填写"
                "当前套餐支持的视频模型 ID（以控制台为准）；不会自动切换按量计费接口。"
            )

    def _url(self, kind):
        ep = self.endpoint(kind)
        if not ep.startswith("/"): ep = "/" + ep
        return self.base + ep

    def _headers(self):
        h = {"Content-Type": "application/json"}
        if self.cfg.get("api_key"): h["Authorization"] = "Bearer " + self.cfg["api_key"]
        if self.id == "gemini":
            h.pop("Authorization", None)
            h["x-goog-api-key"] = self.cfg.get("api_key", "")
        if self.id == "aliyun": h["X-DashScope-Async"] = "enable"
        return h

    def _post(self, url, payload, timeout):
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                     headers=self._headers(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            body = ""
            try: body = e.read().decode("utf-8", "replace")
            except Exception: pass
            if self.id == "doubao":
                try:
                    detail = json.loads(body).get("error") or {}
                except (ValueError, AttributeError):
                    detail = {}
                if isinstance(detail, dict) and detail.get("code") == "UnsupportedModel":
                    raise VendorError(
                        f"HTTP {e.code} UnsupportedModel：火山 Agent Plan 不支持当前模型 "
                        f"{payload.get('model', '')}。请在环境检查中填写当前套餐支持的模型 ID；"
                        "不会自动改用按量计费接口。服务端详情：" + str(detail.get("message") or "")[:1500]
                    ) from e
            raise VendorError(f"HTTP {e.code}: {(body or str(e.reason))[:1500]}")
        except urllib.error.URLError as e:
            raise VendorError(f"连接失败: {e.reason}")
        except TimeoutError:
            raise VendorError(f"请求超时（>{timeout}s）")
        except Exception as e:
            raise VendorError(f"请求异常: {e}")

    def _get(self, url, timeout, params=None):
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            body = ""
            try: body = e.read().decode("utf-8", "replace")[:300]
            except Exception: pass
            raise VendorError(f"HTTP {e.code}: {body or e.reason}")
        except urllib.error.URLError as e:
            raise VendorError(f"连接失败: {e.reason}")
        except TimeoutError:
            raise VendorError(f"请求超时（>{timeout}s）")
        except Exception as e:
            raise VendorError(f"请求异常: {e}")

    def chat(self, messages, model=None, kind=None, max_tokens=2048, timeout=60, temperature=0.5, extra=None):
        """chat/completions。kind 缺省时按消息是否包含图片自动选择 vision/text。extra: 厂商私有参数
        （如 Ark 的 {"thinking":{"type":"disabled"}} 关闭思考提速），失败由调用方兜底重试。"""
        if not self.base: raise VendorError(f"厂商 {self.id} 未配置 base_url")
        has_image = any(isinstance(m, dict) and isinstance(m.get("content"), list) and
                        any(isinstance(part, dict) and part.get("type") in ("image_url", "image")
                            for part in m.get("content") or []) for m in (messages or []))
        resolved_kind = kind or ("vision" if has_image and self.models.get("vision") else "text")
        if model is None:
            model = self.models.get(resolved_kind) or ""
        if not model: raise VendorError(f"厂商 {self.id} 未配置 {kind or 'vision/text'} 模型")
        if self.id == "gemini":
            from native_media import gemini_chat
            return gemini_chat(self,messages,model,resolved_kind,max_tokens,temperature,timeout,extra)
        payload = {"model": model, "messages": messages,
                   "max_tokens": max_tokens, "temperature": temperature}
        if isinstance(extra, dict): payload.update(extra)
        # 视觉调用必须走 vision 槽位端点；文本调用仍走 text。部分厂商两者相同，
        # 但不能把不同端点的配置静默忽略。
        data = self._post(self._url(resolved_kind), payload, timeout)
        try:
            return data["choices"][0]["message"]["content"]
        except Exception:
            raise VendorError("响应结构无法解析（缺少 choices[0].message.content）: " +
                              json.dumps(data, ensure_ascii=False)[:300])

    @staticmethod
    def image_part(path_or_b64, mime="image/jpeg"):
        """本地图片文件 -> vision content part（data URI）。"""
        if os.path.isfile(path_or_b64):
            raw = open(path_or_b64, "rb").read()
        else:
            raw = base64.b64decode(path_or_b64)
        return {"type": "image_url",
                "image_url": {"url": f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")}}

    def _download_or_decode(self, data, out_path):
        """images 接口返回 data[0].url(http) 或 b64_json，落盘到 out_path。"""
        try: item = (data.get("data") or [{}])[0]
        except Exception: item = {}
        if not isinstance(item, dict): item = {}
        url = item.get("url"); b64 = item.get("b64_json")
        if url and url.startswith(("http://", "https://")):
            urllib.request.urlretrieve(url, out_path)
            return out_path
        if b64:
            open(out_path, "wb").write(base64.b64decode(b64))
            return out_path
        if url:
            open(out_path, "wb").write(urllib.request.urlopen(url, timeout=30).read())
            return out_path
        raise VendorError("生图响应无法解析（既无 data[0].url 也无 b64_json）: " +
                          json.dumps(data, ensure_ascii=False)[:300])

    def _supports_image_refs(self, model):
        """判断当前厂商/模型是否支持 Seedream 风格的 image 参考图字段。"""
        name = str(model or "").lower()
        # 火山方舟模型名可能是资源 ID（doubao-seedream-5-0-260128）或自定义别名（seedream-5-0）；
        # 豆包 Seedream 与 GPT Image 系列均支持多图参考。保留显式识别，
        # 未识别的 OpenAI 兼容模型继续拒绝无图降级，避免静默丢引用。
        if self.id in ("doubao", "doubao-api") and ("seedream" in name or "seedance" in name):
            return True
        if "gpt" in str(self.id).lower() and str(self.id).lower() not in {"local-comfyui", "comfyui"}:
            return True
        return ("gpt-image" in name or "gptimage" in name or
                bool(re.search(r"gpt[ _-]?(?:image[ _-]?)?2(?:[._-]?5)?\b", name)))

    @staticmethod
    def _encode_image_ref(ref):
        """将本地图片或已存在的 URL/Data URI 转成火山 image 字段可接受的值。"""
        value = str(ref or "").strip()
        if value.startswith(("http://", "https://", "data:image/")):
            return value
        if not os.path.isfile(value):
            raise VendorError(f"参考图不存在或格式不支持: {value}")
        mime = mimetypes.guess_type(value)[0] or "image/jpeg"
        if not mime.startswith("image/"):
            raise VendorError(f"参考图不是图片文件: {value}")
        with open(value, "rb") as f:
            raw = f.read()
        return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")

    def generate_image(self, prompt, out_path, model=None, timeout=900, extra=None, negative_prompt=None,
                       image_refs=None, strict_negative=False, mode="generate"):
        """POST endpoints.image（缺省 /images/generations），产物存 out_path。
        negative_prompt: 显式负面提示词，走 payload.negative_prompt；厂商不认（HTTP 4xx）
        自动去掉该参数重试一次（同时把负面词以"禁止："文本段并入正面提示词兜底）。
        image_refs: 豆包 Seedream 模型按 image 字段传 URL 或 data:image/*;base64 数据；未适配厂商仍拒绝降级。"""
        refs = list(image_refs or [])
        image_kind = "image_edit" if str(mode or "generate").lower() == "edit" else "image"
        resolved_model = model or self.models.get(image_kind) or ""
        limit = reference_limit(self.id, resolved_model, image_kind, getattr(self, "cfg", None))
        if len(refs) > limit:
            raise VendorError(f"当前生图适配器最多支持 {limit} 张参考图，不能静默丢弃")
        if self.id == "chatgpt-queue":
            from image_use_runtime import generate_image
            return generate_image(prompt + "\n禁止：" + (negative_prompt or "") + "\n画幅：" + (extra or {}).get("ratio","16:9"),out_path,refs=refs,timeout=timeout)
        if self.id in {"gemini", "qwen"}:
            from native_media import gemini_image, qwen_image
            fn = gemini_image if self.id == "gemini" else qwen_image
            return fn(self,prompt,out_path,refs,resolved_model,timeout,extra,str(negative_prompt or ""))
        if not self.base: raise VendorError(f"厂商 {self.id} 未配置 base_url")
        if self.id == "local-comfyui":
            from comfyui_client import ComfyUIClient, ComfyUIError
            adapter = ComfyUIClient(self.base, (self.cfg or {}).get("api_key", ""))
            try:
                selected_model = model or self.model(image_kind)
                extra_cfg = (self.cfg or {}).get("extra") or {}
                workflow_key = "image_edit_workflow_path" if image_kind == "image_edit" else "workflow_path"
                output = adapter.generate_image(
                    prompt, out_path, model=selected_model,
                    negative_prompt=negative_prompt or "", image_refs=image_refs,
                    ratio=(extra or {}).get("ratio", "16:9") if isinstance(extra, dict) else "16:9",
                    workflow_path=extra_cfg.get(workflow_key, ""),
                    timeout=max(timeout, 600))
                self.last_request = getattr(adapter, "last_request", None)
                return output
            except ComfyUIError as exc:
                raise VendorError(str(exc)) from exc
        neg = str(negative_prompt or "").strip()
        payload = {"model": model or self.model(image_kind), "prompt": prompt, "n": 1}
        if neg: payload["negative_prompt"] = neg
        if isinstance(extra, dict): payload.update(extra)
        if refs:
            if not self._supports_image_refs(payload.get("model")):
                raise VendorError(f"当前厂商/模型尚未适配生图参考图（{self.id}/{payload.get('model')}），已拒绝无图降级")
            payload["image"] = [self._encode_image_ref(ref) for ref in refs]
        try:
            data = self._post(self._url(image_kind), payload, timeout)
        except VendorError as e:
            m_code = re.match(r"HTTP (\d+)", str(e))  # 只对 400/422 参数错误重试；401/403 鉴权错误重试白跑还二次计费
            code = int(m_code.group(1)) if m_code else 0
            if neg and not strict_negative and code in (400, 422):
                payload.pop("negative_prompt", None)
                payload["prompt"] = prompt + " 禁止：" + neg
                data = self._post(self._url(image_kind), payload, timeout)
            else:
                raise
        return self._download_or_decode(data, out_path)

    def generate_video(self, prompt, image_refs=None, out_path=None, model=None, timeout=180,
                       poll_interval=5, poll_max=600, extra=None, first_frame=None, last_frame=None):
        """视频生成。豆包 Ark（endpoint 含 /tasks）走任务制：POST 创建 ->
        轮询 GET {base}{endpoint}/{task_id}；其余兼容同步返回或 {base}/tasks/{id} 轮询。"""
        refs = list(image_refs or [])
        resolved_model = model or self.models.get("video") or ""
        # 已由服务端明确拒绝的历史默认值，提交前拦截，避免重复失败任务。
        if self.id == "doubao":
            self.validate_video_config(resolved_model)
        from video_profiles import capabilities, settings, validate_media
        profile = capabilities(self.cfg, resolved_model)
        for frame in (first_frame,last_frame):
            if frame and frame not in refs: refs.append(frame)
        extra = dict(extra or {})
        audio_refs = extra.pop('audio_refs', [])
        video_refs = extra.pop('video_refs', [])
        # 老入口没有模式字段时仍按实际素材推导，首尾帧必须显式传入。
        extra.setdefault('mode', 'first_last' if first_frame and last_frame else 'first_frame' if first_frame else 'last_frame' if last_frame else 'reference' if refs or audio_refs or video_refs else 'text')
        if profile['default_mode'] == 'first_frame' and extra['mode'] == 'reference' and len(refs) == 1:
            extra['mode'] = 'first_frame'; first_frame = refs[0]
        try:
            extra = settings(self.cfg, extra, model=resolved_model)
            validate_media(profile, extra['mode'], refs, first_frame, last_frame, audio_refs, video_refs)
        except ValueError as exc: raise VendorError(str(exc)) from exc
        mode = extra.pop('mode')
        if profile['endpoint']: self.endpoints['video'] = profile['endpoint']
        extra.update(audio_refs=audio_refs, video_refs=video_refs)
        if profile['adapter'] == 'agnes':
            from agnes_video import generate
            return generate(self, prompt, refs, out_path, resolved_model, timeout, poll_interval, poll_max, extra, mode, first_frame, last_frame)
        if self.id in {"minimax", "aliyun", "kling"} and first_frame and first_frame not in refs:
            refs.insert(0,first_frame)
        limit = reference_limit(self.id, resolved_model, "video", getattr(self, "cfg", None))
        if len(refs) > limit:
            raise VendorError(f"当前视频适配器最多支持 {limit} 张参考图，不能静默丢弃")
        if self.id in {"minimax", "aliyun", "kling"}:
            from native_media import video
            return video(self,prompt,refs,out_path,model,timeout,poll_interval,poll_max,extra,first_frame,last_frame)
        if not self.base: raise VendorError(f"厂商 {self.id} 未配置 base_url")
        if self.id == "local-comfyui":
            from comfyui_client import ComfyUIClient, ComfyUIError
            from comfyui_h3 import generate_h3
            if first_frame or last_frame:
                raise VendorError('当前 ComfyUI H3 参考工作流未接强制首帧输入，请使用尾帧画面参考')
            if not out_path:
                raise VendorError("ComfyUI H3 需要本地视频输出路径")
            adapter = ComfyUIClient(self.base, (self.cfg or {}).get("api_key", ""))
            try:
                result = generate_h3(adapter, prompt, list(image_refs or []), out_path,
                                     seconds=float((extra or {}).get("duration", 5)))
                self.last_request = getattr(adapter, "last_request", None)
                return result
            except ComfyUIError as exc:
                raise VendorError(str(exc)) from exc
        if first_frame and first_frame not in refs:
            raise VendorError("显式首帧必须包含在参考图清单中")
        # 火山方舟 Agent Plan 任务端点只接受已适配的 Seedance 等模型。
        # SD1.5/SDXL 是本地扩散模型名称，送到 /contents/generations/tasks
        # 会得到 404 UnsupportedModel；在发起任务前给出可执行的路由提示。
        selected_model = str(model or self.model("video") or "").strip()
        if "/tasks" in self.endpoint("video") and re.search(
                r"(?:^|[-_])(?:sd1\.5|sd15|sdxl|stable[-_]?diffusion)(?:[-_.]|$)",
                selected_model, re.IGNORECASE):
            raise VendorError(
                f"模型 {selected_model} 不支持豆包 Agent Plan 视频接口。"
                "请在豆包的“生视频”槽改为套餐支持的 Seedance/Agent Plan 模型；"
                "如果要跑 SD1.5/SDXL，请切换到 local-comfyui，并在 ComfyUI 中提供对应的视频 API 工作流。"
            )
        content = [{"type": "text", "text": prompt}]
        # Ark content 接受远程 URL 或 data URI；本地文件按真实 MIME 编码，避免 PNG 被伪装成 JPEG。
        encoded_refs = []
        for ref in refs:
            value = str(ref or "").strip()
            if value.startswith(("http://", "https://", "data:image/")):
                encoded_refs.append(value)
                continue
            if not os.path.isfile(value):
                raise VendorError(f"参考图不存在或格式不支持: {value}")
            mime = mimetypes.guess_type(value)[0] or "image/jpeg"
            if not mime.startswith("image/"):
                raise VendorError(f"参考图不是图片文件: {value}")
            with open(value, "rb") as fh:
                raw = fh.read()
            raw_b64 = base64.b64encode(raw).decode("ascii")
            encoded_refs.append(f"data:{mime};base64," + raw_b64)
        for ref, encoded in zip(refs, encoded_refs):
            role = 'first_frame' if first_frame and ref == first_frame else 'last_frame' if last_frame and ref == last_frame else 'reference_image'
            content.append({"type": "image_url", "image_url": {"url": encoded}, 'role': role})
        extra = dict(extra or {})
        for ref in extra.pop('audio_refs', []):
            from video_media_input import encode
            content.append({'type': 'audio_url', 'audio_url': {'url': encode(ref)}, 'role': 'reference_audio'})
        for ref in extra.pop('video_refs', []):
            from video_media_input import encode
            content.append({'type': 'video_url', 'video_url': {'url': encode(ref)}, 'role': 'reference_video'})
        ep = self.endpoint("video")
        if "/tasks" in ep:   # 豆包 Ark 任务制
            payload = {"model": model or self.model("video"), "content": content}
            if isinstance(extra, dict): payload.update(extra)
            data = self._post(self._url("video"), payload, timeout)
            task_id = data.get("id") or (data.get("task_id"))
            if not task_id:
                raise VendorError("Ark 任务创建响应无 id: " + json.dumps(data, ensure_ascii=False)[:300])
            self.last_request = {'task_id': task_id, 'endpoint': ep, 'model': selected_model, 'provider': self.id}
            if callable(getattr(self, 'on_task_submitted', None)): self.on_task_submitted(self.last_request)
            url = self._poll_ark_task(task_id, ep, poll_interval, poll_max)
        else:
            payload = {"model": model or self.model("video"), "prompt": prompt}
            if isinstance(extra, dict): payload.update(extra)
            if refs:
                payload["images"] = encoded_refs
                if first_frame:
                    payload["first_frame"] = encoded_refs[refs.index(first_frame)]
            data = self._post(self._url("video"), payload, timeout)
            url = self._extract_video_url(data)
            task_id = data.get("id") or data.get("task_id") or \
                      ((data.get("task") or {}).get("id") if isinstance(data.get("task"), dict) else None)
            if not url and task_id:
                url = self._poll_task(task_id, poll_interval, poll_max)
        if not url:
            raise VendorError("视频生成响应无法解析（既无视频地址也无可轮询的任务 id）: " +
                              json.dumps(data, ensure_ascii=False)[:300])
        if out_path:
            urllib.request.urlretrieve(url, out_path)
            return out_path
        return url

    def _poll_ark_task(self, task_id, ep, interval, max_wait):
        """豆包 Ark: GET {base}{ep}/{task_id}，status=succeeded 后取 content 视频地址。
        注: 拿到真 key 后按实际响应结构微调（content 可能是字符串 URL 或对象）。"""
        deadline = time.time() + max_wait
        while time.time() < deadline:
            time.sleep(interval)
            data = self._get(self.base + ep + "/" + urllib.parse.quote(str(task_id)), 30)
            st = str(data.get("status") or "").lower()
            if st in ("queued", "running", "pending"): continue
            if st in ("succeeded", "success", "done", "completed", "finished"):
                c = data.get("content")
                if isinstance(c, str) and c.startswith(("http://", "https://")): return c
                if isinstance(c, dict):
                    u = c.get("video_url") or c.get("url")
                    if isinstance(u, str) and u.startswith(("http://", "https://")): return u
                raise VendorError("Ark 任务完成但无视频地址: " + json.dumps(data, ensure_ascii=False)[:300])
            raise VendorError("Ark 任务失败: " + json.dumps(data, ensure_ascii=False)[:300])
        raise VendorError(f"轮询超时（>{max_wait}s），任务 {task_id} 仍未完成")

    @staticmethod
    def _extract_video_url(data):
        if not isinstance(data, dict): return None
        for k in ("url", "video_url", "output", "video"):
            v = data.get(k)
            if isinstance(v, str) and v.startswith(("http://", "https://")): return v
        for k in ("data", "output", "result"):
            v = data.get(k)
            if isinstance(v, list) and v and isinstance(v[0], dict):
                u = v[0].get("url") or v[0].get("video_url")
                if isinstance(u, str) and u.startswith(("http://", "https://")): return u
        return None

    def _poll_task(self, task_id, interval, max_wait):
        """轮询 GET {base}/tasks/{task_id}（通用约定）。尽力而为。"""
        deadline = time.time() + max_wait
        while time.time() < deadline:
            time.sleep(interval)
            data = self._get(self.base + "/tasks/" + urllib.parse.quote(str(task_id)), 30)
            st = str(data.get("status") or (data.get("task") or {}).get("status") or "").lower()
            if st in ("queued", "running", "pending"): continue
            if st in ("succeeded", "success", "done", "completed", "finished"):
                url = self._extract_video_url(data)
                if url: return url
                raise VendorError("任务已完成但响应中无视频地址: " +
                                  json.dumps(data, ensure_ascii=False)[:300])
            if st in ("failed", "error", "cancelled", "canceled"):
                raise VendorError("视频生成任务失败: " + json.dumps(data, ensure_ascii=False)[:300])
        raise VendorError(f"轮询超时（>{max_wait}s），任务 {task_id} 仍未完成")

    def generate_music(self, prompt, out_path, model=None, timeout=300, extra=None):
        from native_media import audio
        return audio(self,"music",prompt,out_path,model,timeout,extra)

    def generate_speech(self, text, out_path, model=None, timeout=300, extra=None):
        from native_media import audio
        return audio(self,"speech",text,out_path,model,timeout,extra)

    def list_models(self, timeout=15):
        """GET {base}/models 拉取模型列表。OpenAI 风格解析 data[].id；
        Gemini（generativelanguage.googleapis.com）用 ?key= 鉴权、解析 models[].name。"""
        if self.id == "chatgpt-queue": return ["chatgpt-web"]
        if self.id == 'agnes': return ['agnes-video-2.5', 'agnes-video-2.5-flash']
        if self.id in {"minimax", "kling", "aliyun"}:
            return sorted(set(x for x in self.models.values() if x))
        if self.id == "local-comfyui":
            from comfyui_client import ComfyUIClient, ComfyUIError
            try:
                return ComfyUIClient(self.base, (self.cfg or {}).get("api_key", "")).list_diffusion_models(timeout)
            except ComfyUIError as exc:
                raise VendorError(str(exc)) from exc
        if not self.base: raise VendorError(f"厂商 {self.id} 未配置 base_url")
        if not self.cfg.get("api_key"): raise VendorError(f"厂商 {self.id} 未配置 api_key")
        try:
            data = self._get(self.base + "/models", timeout)
        except VendorError as exc:
            if self.id == "doubao" and str(exc).startswith("HTTP 404:"):
                raise VendorError(
                    "Agent Plan 模型列表接口 /api/plan/v3/models 返回 404。"
                    "当前无法通过此接口获取套餐模型，请在能力槽手动填写控制台提供的模型 ID 后保存。"
                    "此结果不代表图片或视频生成接口不可用；不会切换到普通 /api/v3。"
                ) from exc
            raise
        if isinstance(data.get("data"), list):
            ids = [m.get("id") for m in data["data"] if isinstance(m, dict) and m.get("id")]
            if ids: return ids
        if isinstance(data.get("models"), list):
            ids = [m.get("name") for m in data["models"] if isinstance(m, dict) and m.get("name")]
            if ids: return ids
        raise VendorError("响应结构无法解析（既无 data[] 也无 models[]）: " +
                          json.dumps(data, ensure_ascii=False)[:300])









