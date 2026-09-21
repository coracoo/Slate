# examples — 关键 JSON 契约样例

每个文件对应工作台的一条数据契约，可直接运行验证。改动字段规范（SPEC）时请同步更新这里——样例就是契约的"活文档"。

| 文件 | 契约 | 怎么跑 |
|---|---|---|
| `storyboard_dialogue.json` | 对白契约分镜（显式 pos/look 或 cam，lines/staging/scene） | `python previs_system/engine/dialogue_engine.py examples/storyboard_dialogue.json out.mp4` 直接渲染白模；校验：`python .codex/skills/video-previs/scripts/validate_dialogue.py examples/storyboard_dialogue.json` |
| `analysis.example.json` | 拉片解构 analysis.json（受控词表 8 景别/15 运镜/8 角度/9 转场） | `python workbench/tools/validate_analysis.py examples/analysis.example.json` |
| `lines.example.json` | 台词脚本（speakers + lines，ASR/OCR 合并产物） | 工作台台词页「导入」或 `workbench/tools/merge_lines.py` 产出同构 |
| `providers.example.json` | 厂商配置 v2（vendors 结构，**不放真实 key**） | 复制到 `workbench/providers.json` 后在「环境检查」页填 key |
| `demo_project/` | 项目目录骨架（项目.json + 分镜/剧本_示例.json + 剧本/style.json） | 复制到 `projects/示例_Slate/`，刷新工作台即出现在项目列表 |

## 运行示例

先完成[首页的 Windows 部署](../README.md#1-首次部署windows)，包括安装依赖、构建新版前端，再运行以下 PowerShell 命令（工作目录为仓库根目录）：

```powershell
# 渲染示例对白白模
.\.venv\Scripts\python.exe previs_system/engine/dialogue_engine.py examples/storyboard_dialogue.json out.mp4

# 将示例项目复制到本机项目目录；目标已存在时请换名，避免覆盖
New-Item -ItemType Directory -Force projects | Out-Null
Copy-Item -LiteralPath examples/demo_project -Destination projects/示例_Slate -Recurse

# 启动工作台，浏览器打开 http://127.0.0.1:8775
.\.venv\Scripts\python.exe workbench/server.py 8775
```

白模渲染缺少 FFmpeg 时可退回 mp4v 编码，但浏览器播放兼容性有限；字幕提取、逐帧抽帧和视频处理需要 FFmpeg。请按首页教程配置 FFmpeg / FFprobe。当前部署教程面向 Windows，macOS/Linux 尚未完成适配验证。

## 注意

- `providers.json` / `llm_config.json` / `media_gateway.json` 等本机配置可能含 API key 或令牌，已被 .gitignore 排除，**永远不要提交**。
- 白模只锁机位/走位/景别/节奏/台词，不做美术；换片子只改 JSON，不改代码。
- 走位（previs）契约样例见 `previs_system/templates/storyboard.template.json`。

## ChatGPT 生图接入

使用环境检查页的 chrome-use / image-use 安装入口，按一任务一张图生成与导入。详见主 README 的安装步骤。

## 服务商配置

新部署请通过「环境检查」配置服务商，并分别测试连接与拉取模型。示例配置只展示结构，不包含真实密钥。能力与接入方式以[工作台教程](../workbench/README.md)为准。
