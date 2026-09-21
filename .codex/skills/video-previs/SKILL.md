---
name: video-previs
description: >
  为对话/短剧场景做 2D 白模(animatic/blocking previs)：抽帧拉片+切点检测、
  台词提取（OCR烧录字幕 + ASR语音，两路并行）、storyboard JSON、自动正反打/越肩/
  特写/全景机位，直接出 MP4。奇观大场面不要用本技能，走 AI 概念图。
metadata:
  short-description: 2D 对白/短剧白模预演（拉片+台词→分镜JSON→自动机位出片）
---

# video-previs — 2D 对白白模预演

把拉片得到的镜头语言 + 台词，用确定性 2D 程序渲染成可播放白模。**只锁机位/走位/轴线/景别/节奏/台词，不做美术**。

## 何时用 / 不用
- 用：对话、短剧、室内对峙、采访、围坐等**固定机位人物关系镜头**。
- 不用：城市飞跃/爆炸/大场面（环境即镜头主体），交 AI 关键帧概念图。

## 流程
### 1. 拉片（程序）
`python scripts/extract_shots.py <video> --start S --end E [--step 3] [--thresh 9]`
→ 接触印片 `frames_<名>/contact_*.jpg` + 打印切点区间。密集抽帧**人工归并**，定每镜景别/运镜/前后景/画左画右。

### 2. 台词提取（ASR + OCR 两路并行，必须都做）
- **OCR 烧录字幕（首选，准）**：视频有内嵌字幕时
  `python scripts/extract_subtitles.py <video> --start S --end E`
  → `<名>_字幕.srt` + `_台词.txt`（带时间轴）。依赖 rapidocr（离线轻量）。
- **ASR 语音（补充）**：无字幕/对白超出字幕/需要听语气停顿
  `python scripts/extract_speech.py <video> --start S --end E --model small --lang zh`
  → `<名>_ASR.srt` + `_ASR_台词.txt`。依赖 faster-whisper（本地 CPU，首次下模型）。
- **交叉校正规则**：
  - 有烧录字幕 → 以 **OCR 为准**（字幕是片方正本）；ASR 仅补字幕未覆盖的口语/停顿/笑声。
  - 无字幕 → 以 **ASR 为准**，可换 `--model medium/large-v3` 提精度。
  - 两路都产出**带时间轴的 SRT**；据每镜说话人特写镜头，把台词归属到 `speaker`。

### 3. 填 storyboard JSON
复制 `templates/dialogue_storyboard.template.json`，填 actors + shots（字段见 `references/storyboard_schema.md`）。
- shots 时长/切点用步骤1；`line/speaker` 用步骤2 的带时间轴台词对齐到镜头；
- 硬信息（切点、时长、景别、谁说的、画左画右）**从原片读，不猜**。

### 4. 渲染出片
- `python scripts/dialogue_engine.py <storyboard.json> out/白模.mp4`
- 转通用编码：`ffmpeg -i in.mp4 -c:v libx264 -pix_fmt yuv420p -movflags +faststart out.mp4`
- 抽帧与原片并排核对；只改 JSON 秒级重渲。

### 5. 深度动作捕捉（白模之后）
从原片生成深度/动作遮罩视频，用于校准走位、动作与景别：
- 深度图（首选，全场景近亮远暗，忠实模型输出）：
  `python scripts/motion_depth.py <video> --start S --end E --fps 12 --out 深度图.mp4`
  - 默认 `--enhance none`（忠实原始，不改远近关系）；需要时才 `--enhance stretch|clahe` 轻微提对比。
  - 可选 `--color inferno`（伪彩）、`--invert`、`--global-norm`（全段统一尺度）。
- 人像剪影（仅人物，备选）：`python scripts/motion_silhouette.py <video> --style gray|binary`
- 质量验证：`python scripts/depth_verify.py <深度.mp4>`（输出亮度分布/前景背景分离度/增强对照图）。
- 注意：单目深度在转场叠化帧、剧烈镜头运动处可能失真，以原始解读为准，不要默认拉伸/时序修改。

## 关键规则
- 相机由站位解析（wide/near/two/cu/ots），勿手摆；1:1 复刻才用显式 pos/look 或镜头级 staging/table。
- **一次只改被要求的那一镜，已确认镜头绝不修改。**
- 构图不对先查 blocking（谁左谁右、谁最近最大、围什么道具），再查机位。
- 输出一律 H.264/yuv420p。每步抽帧与原片并排自检。

## 深入
- `references/storyboard_schema.md`：JSON 字段与机位算法。
- `references/workflow.md`：拉片 vs 复刻、技术路线、经验。

## 引擎与校验
- 对白/短剧：`scripts/dialogue_engine.py`（自动正反打/越肩/特写/全景，支持镜内台词轨 lines[]、镜头级 staging/table、near 背影）。
  配套校验：`scripts/validate_dialogue.py <json>`（渲染前先校验 cam/角色引用/时长）。
- 走位/动作白模（含 path 走位、yaw+pitch 姿态）：`scripts/previs_engine.py`；配套校验 `scripts/validate_storyboard.py`。
- 分镜建议（台词→wide/ots/cu）：`scripts/plan_coverage.py`。
- 导出：`export_storyboard_xlsx.py`（Excel）、`export_dialogue.py`（带时间戳 srt/txt）。
- 一键：`run_dialogue_pipeline.py <json>`（校验→渲染→Excel→台词→H.264）。

## 学习向产物
- 关键镜头讲解（为什么这么拍）按 `references/critical_shots_template.md` 三段式：叙事潜台词 / 场景美术 / 镜头语言动机 + 可迁移判断准则。

## 字段规范
- 对白白模完整字段见 `references/storyboard_schema.md`（cam/pos-look/staging/lines 等）。

## 可视化工作台（dashboard）
- 入口：`dashboard/server.py`（自包含 Web 工作台）。启动：`python dashboard/launch.py 11872` 或 `python dashboard/server.py 11872`，浏览器开 http://127.0.0.1:11872 。
- 功能：浏览 projects/ 各项目产物（拉片/台词/分镜/白模/讲解），点标签分阶段查看，文本卡内嵌全文+点击弹窗，视频支持 Range 拖动；后端 `/api/run` 调用本 Skill 脚本。
- 自包含：dashboard 自动向上查找 projects/，脚本优先用同 Skill 的 `scripts/`，可独立运行。

## 可选增强（需配置/外部程序）
- **3D 白模（Blender）**：scripts/blender_previs.py <json> 生成 Blender 构建脚本 gen_<slug>.py，经 Blender MCP/Blender 内执行，做真三维走位推演。配置见 references/environment_setup.md。
- **LLM/Vision（智谱 GLM，可选）**：设环境变量 GLM_API_KEY（或 ZHIPUAI_API_KEY）后启用：
  - ill_descriptions.py 用 vision 给接触印片/分镜补描述；
  - xplain_storyboard.py 生成关键镜头讲解（叙事/潜台词/镜头语言）；
  - draft_storyboard.py/lapian_docs.py 由接触印片+切点草拟分镜文档。
  - 未配置 key 时这些脚本跳过/报错，不影响离线拉片与白模。
- 完整 ENV（GLM key/model、Blender、离线依赖、ffmpeg）见 
eferences/environment_setup.md。

