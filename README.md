# Slate（场记）

**面向短片制作与拉片分析的本地 Web 工作台。** 从剧本、分镜、人物与场景资产，到关键帧、视频片段、角色音色和集视频，在一个项目中管理。制作数据以分镜 JSON 为依据，生成结果和请求记录保存在本机。

仓库：[coracoo/Slate](https://github.com/coracoo/Slate) · [详细操作教程](workbench/README.md) · [示例数据](examples/README.md) · [隐私与发布检查](SECURITY.md)

## 功能

- **制作线**：①剧本生成 → ②分镜生成 → ③素材生成 → ④音色绑定 → ⑤演员表现 → ⑥平面推演 → ⑦创作生成；⑧自由创作提供独立生成入口。
- **三层视频**：S 转场镜头 → V 分镜视频 → E 集视频。参考帧、视频、宫格分别使用不同提示词；支持编辑、模型优化、关键帧采用和尾帧续接。
- **拉片线**：影片解构、台词 OCR/ASR、逐帧提取、深度动作、镜头讲解，以及辅助 2D/3D 白模。
- **生成入口**：本地 ComfyUI、ChatGPT 网页生图（image-use / chrome-use）和已适配的云端 API。
- **本地资产**：人物、场景与派生状态，道具、角色音色、音乐、语音、关键帧和视频。参考音视频支持本地上传、URL、项目素材选择。
- **后台任务**：异步提交与轮询、产出记录、失败原因和顶部通知。未知结果不盲目重发。

## 1. 首次部署（Windows）

目前主要支持 **Windows 本机部署**。浏览器自动化安装器下载 Windows x64 程序；macOS/Linux 和无桌面服务器的完整链路尚未验证。部署需要完成前端构建。

### 安装前准备

| 依赖 | 用途 |
|---|---|
| Git | 克隆和更新源码 |
| uv + CPython **3.12** | 用 uv 安装并固定 Python 3.12；不要用系统 Python 3.11 创建环境 |
| Node.js 22.18+（或更新 LTS）+ npm | 前端构建与 TypeScript 测试 |
| FFmpeg / FFprobe | 视频处理、抽帧、尾帧提取、拼接；安装后加入 PATH |
| Chrome | 仅 ChatGPT 网页生图需要 |
| ComfyUI | 仅本地模型生成需要，另行安装并启动 |
| Blender | 仅 3D 白模需要；Blender MCP 为可选构建入口 |

在 PowerShell 中执行：

```powershell
git clone https://github.com/coracoo/Slate.git
cd Slate

# 若尚未安装 uv，请先按 https://docs.astral.sh/uv/getting-started/installation/ 安装
uv python install 3.12
uv venv --python 3.12 --seed .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-win-py312.lock

# 构建前端
Push-Location workbench/web
npm ci
npm run build
Pop-Location

# 启动服务
.\.venv\Scripts\python.exe workbench/server.py 8775
常驻运行（推荐挂机）：`./.venv/Scripts/python.exe workbench/keepalive.py 8775` —— 进程退出自动重启（退避+冷却），崩溃留痕 `workbench/logs/keepalive.log`；优雅停止：创建 `workbench/STOP` 文件
```

通过 `.\.venv\Scripts\python.exe --version` 检查解释器版本，必须显示 3.12.x。不要使用默认的 `python -m venv`，也不要用其他 Python 版本安装这份锁文件。

`requirements-win-py312.lock` 包含 Windows / Python 3.12 的固定依赖版本与文件哈希。

打开 **http://127.0.0.1:8775**。首页可新建“制作”项目，无需先上传影片。进入“环境检查”，填写 API Key、模型和地址后，再手动启用需要的厂商；首次启动不会启用任何 AI 厂商。

服务当前监听 `0.0.0.0:8775`。局域网访问需要放行防火墙；管理接口面向可信网络，不要直接把整个工作台映射到公网。GPU 不是工作台本身的要求，本地模型和深度推理的资源需求另算。

### 更新与开发

在终端按 `Ctrl+C` 停止服务，执行 `git pull --ff-only`，安装锁文件依赖并重新运行前端 `npm ci`、`npm run build`，再启动服务。前端开发在后端已启动时运行 `npm run dev`，Vite 代理 API 到 8775。

### 批量安装环境

进入“环境检查 → 批量安装环境”，选择基础依赖、深度/剪影依赖或 ChatGPT 本机程序，点击“后台安装所选环境”。任务面板显示日志，完成或失败时顶部提醒。安装期间保持前台服务运行；基础依赖安装后重启服务。

安装目标为项目 `.venv`（Python 3.12）。FFmpeg、Blender、ComfyUI 及 Chrome 扩展授权需单独配置。

### 深度与剪影（可选）

在工作台“深度动作”页面使用。安装推理依赖后，首次运行需下载模型权重：

```powershell
.\.venv\Scripts\python.exe -m pip install torch torchvision transformers "rembg[cpu]"
```

GPU 加速需要与设备匹配的 PyTorch；此组依赖独立于基础安装锁文件。

### 日志

服务日志输出到启动终端；安装和生成日志在任务面板查看。

## 2. ChatGPT 网页生图：安装程序与浏览器扩展

**这一入口需要 image-use 程序、chrome-use 程序和 Chrome 扩展。** 网页生图服务由 [leeguooooo/image-use](https://github.com/leeguooooo/image-use) 提供，Slate 负责排队、参考图、结果校验及导入。

1. 工作台打开 **环境检查 → ChatGPT · chrome-use**。
2. 点“复制一键安装命令”，在运行工作台的机器上执行。使用上面的虚拟环境时，也可以直接运行：

   ```powershell
   .\.venv\Scripts\python.exe workbench/tools/install_image_use.py
   ```

3. 点“安装 Chrome 扩展”：进入 [chrome-use 商店页](https://chromewebstore.google.com/detail/chrome-use/knfcmbamhjmaonkfnjhldjedeobeafmk)。无法使用商店时，可点页面的“下载上游扩展包”，解压后在 `chrome://extensions` 开启开发者模式，加载含 `manifest.json` 的目录。
4. 在同一 Chrome 中启用扩展、打开 ChatGPT 并登录有生图能力的账号。浏览器授权与登录需由用户完成。
5. 返回环境检查，点“重新检查”。“本机程序已安装”只说明程序文件存在，不代表 ChatGPT 已登录或生图已验证。
6. 启用 ChatGPT 厂商，在素材页加入任务，使用“ChatGPT · 单张串行”执行。先用一个任务验证，再增加队列。

**任务按单张串行执行**：上传参考图 → 生成 → 下载校验 → 按任务导入 → 下一张。登录失效、额度不足或结果不明时，检查会话和日志后再恢复。

详细说明见 [网页生图操作](workbench/README.md#chatgpt-单张串行)。

## 3. 模型与 API 配置

在 **环境检查** 编辑 Base URL、API Key 和能力槽：文本、视觉、生图、改图、视频、音乐、语音。编辑后可直接测试或拉取模型；启用只控制是否出现在生成选项中。测试媒体配置不会主动提交收费生成。

| 入口 | 当前用途与注意点 |
|---|---|
| ComfyUI | 本地图片/改图和内置 H3 视频工作流；模型、节点和资源须在 ComfyUI 安装匹配 |
| ChatGPT | image-use / chrome-use 单张串行网页生图 |
| 火山 Agent Plan | `https://ark.cn-beijing.volces.com/api/plan/v3`；使用套餐支持型号 |
| 火山普通 API | `https://ark.cn-beijing.volces.com/api/v3`；独立配置，按量计费，不与 Plan 自动互换 |
| MiniMax | H3 视频、Music、Speech 和音色；模型权限及计费额度分别确认 |
| 通义 / 阿里云 | 通义文本、视觉和图片；阿里云 Wan 视频使用相应业务空间与地域地址 |
| Gemini、可灵、Agnes | 按已接入型号和协议生成；可灵当前视频适配为 3.0 Turbo 首帧接口 |
| OpenAI 兼容、DeepSeek | 根据服务商实际接口配置能力，不默认具有全部媒体能力 |

视频型号与分辨率以代码中的 [能力表](workbench/tools/video_profiles.py) 为准；界面按型号过滤。未知型号或不兼容参数在提交前拒绝，协议已接入不代表账号已开通权限。比如 MiniMax H3 需要对应按量 API 权限，TokenPlan/Credit 凭据可能被服务端拒绝。

## 4. 本地参考音视频与公网出口

创作生成的参考音频、参考视频均可 **上传本地文件 / 填写 URL / 选择项目素材**。本地文件先存入项目，提交后固定请求副本。

云端需要读取本地音视频时，到 **环境检查 → 公网素材出口** 填写：

```text
https://media.example.com:8443/previs
```

工作台生成的地址为 `基础地址 + /api/public-reference + 临时签名参数`，默认有效期 24 小时。反向代理将 `/previs/api/public-reference` 转发到 `http://127.0.0.1:8775/api/public-reference`，保留查询参数、GET/HEAD 和 Range 请求头。仅公开此素材读取路径即可。

**填写地址不会自动建立 DNS、端口映射或隧道。** 生成期间工作台与代理须保持在线，服务商能访问该 URL 才算连通。公网出口当前负责音视频；Agnes 图片仍需填写对应公网图片 URL。出口签名密钥仅存本机，不提交仓库。

## 5. 使用流程

- **从剧本制作**：新建制作项目 → 剧本/分集 → 分镜 → 提炼素材和生成母图/派生图 → 角色音色（可选）→ 创作生成中采用 S 关键帧 → 合并相邻场景镜头为 V → 生成并采用 V 视频 → 拼接集视频。
- **分析已有影片**：新建拆片项目并导入视频 → 拉片结构 → 台词分析 → 逐帧/深度 → 按需生成白模或用于自由创作。
- **无需 API 的快速验证**：复制 [示例项目](examples/demo_project)，或运行 [示例白模](examples/README.md)。

完整步骤、故障恢复和文件落点见 [工作台 README](workbench/README.md)。

## 目录与验证

```text
workbench/          后端、Vue 前端、厂商适配、任务、Skill 和测试
previs_system/      白模引擎、分镜契约与导出工具
.codex/skills/      自包含 video-previs Skill（与浏览器扩展不同）
examples/           无密钥示例配置与项目
projects/           本机项目数据，Git 忽略
```

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s workbench/tests
.\.venv\Scripts\python.exe workbench/tools/audit_public_repo.py --history
Push-Location workbench/web
node --test tests/*.test.mjs
npm run build
Pop-Location
```

仓库不附带真实 API Key、个人项目、模型权重、用户知识卡、Chrome 登录信息或生成产物。配置和项目数据备份由使用者管理，具体边界见 [SECURITY.md](SECURITY.md)。
