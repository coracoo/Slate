# 环境配置（LLM / Vision / Blender）

Skill 核心拉片/白模是离线的（opencv/rapidocr/faster-whisper/本地引擎），无需密钥。
以下为可选的增强能力。

## 1. LLM + Vision（智谱 GLM，可选）
用于：自动补分镜描述、生成关键镜头讲解/潜台词、对接触印片做语义分析。
- 客户端：`scripts/glm_client.py`（标准库实现，无第三方依赖）。
- 配置环境变量（二选一设 key）：
  - `ZHIPUAI_API_KEY` 或 `GLM_API_KEY`（智谱开放平台 key，必填）
  - `GLM_BASE`（默认 https://open.bigmodel.cn/api/paas/v4）
  - `GLM_TEXT_MODEL`（默认 glm-5.3-flash）
  - `GLM_VISION_MODEL`（默认 glm-4.5v）
- Windows 持久配置（PowerShell，用户级）：
  ```powershell
  [Environment]::SetEnvironmentVariable("GLM_API_KEY","你的key","User")
  ```
  配置后重开终端生效；`glm_client.is_configured()` 返回 True。
- 相关脚本：
  - `fill_descriptions.py`：为分镜/接触印片补描述（vision）。
  - `explain_storyboard.py`：生成关键镜头讲解（叙事/潜台词/镜头语言）。
  - `draft_storyboard.py` / `lapian_docs.py`：由接触印片+切点草拟分镜文档。
- 未配置 key 时这些脚本报“未配置 GLM_API_KEY”，不影响离线流程。

## 2. Blender 3D 白模（可选，需要 Blender）
- 脚本：`scripts/blender_previs.py`。
- 输入 storyboard JSON，输出 `projects/<项目>/白模3D/gen_<slug>.py`（Blender Python 构建脚本）。
- 通过 Blender MCP 或在 Blender 内执行该脚本，生成 3D 场景/走位。
- 坐标约定：JSON (x横,y上,z深) → Blender (x, z, -y)。
- 适用：需要真三维走位/机位推演时；2D 对白白模（dialogue_engine）仍是首选、无需 Blender。

## 3. 离线依赖（必备，无需密钥）
- python 包：numpy / Pillow / opencv-python / openpyxl / rapidocr_onnxruntime / faster-whisper。
- 外部：ffmpeg（H.264 转码）、Blender（仅 3D 白模）。
- 字体：Windows msyh.ttc（白模渲染叠加文字）。
