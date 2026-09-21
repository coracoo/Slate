# 视频拉片工作台

## 启动
```
python launch.py 8775
```
（或 `python server.py 8775`）然后浏览器打开 http://127.0.0.1:8775

## 功能
- 左侧：projects/ 项目卡片（自动扫描，支持筛选）+ Downloads 源视频库（38+ 视频，点击直接播放，可一键导入当前项目）+ 当前项目影片
- 步骤页：总览状态 / ①拉片(切点) ②台词时间戳(OCR+ASR) ③分镜(JSON+Excel) ④白模成片(MP4) ⑤镜头语言讲解 / ⑥3D白模(Blender) / 全部产物
- 后端可调用 Skill 脚本（/api/run），任务后台执行 + 实时轮询日志（带计时）

## ⑥ 3D 白模（Blender）
- 自动扫描 `白模3D/`、`三维探索/` 及项目根目录的 `.blend / .mp4 / build_*.py`；render/ 帧序列自动折叠
- 卡片：.py 直接在网页看代码、.blend 弹窗可「在本机 Blender 打开」或「CLI 后台渲染」
- 动作按钮：🟢 打开 .blend（os.startfile）／ ▶ CLI 渲染（blender -b blend -a，任务超时 60min）／ ▶ 发送构建脚本到 Blender（MCP 127.0.0.1:9876 execute_code）

## 源视频
源片在 C:\Users\<you>\Downloads，工作台只读挂载，不复制大文件；项目产物在 <工作区>\projects\
媒体支持：mp4/mov/mkv（Range 拖动）、jpg/png、wav/m4a/mp3、md/txt/srt/json/py 文本预览、blend/xlsx 下载

## ⑦ 分镜 JSON → Blender 3D 一键生成
- 工具：`previs_system/tools/blender_previs.py <storyboard.json>`（消费 dialogue 引擎契约：set/actors/shots 的 pos/look/fov）
- 产物：`projects/<项目>/白模3D/gen_<slug>.py` + `gen_<slug>.blend`（脚本发到 MCP 后自动存盘）
- 工作台 ⑥ 页签按钮：「✨ 从分镜JSON 一键生成 3D 场景」= 生成脚本 → 自动发送 Blender MCP(9876) 构建；之后可点「Blender CLI 后台渲染」出帧
- 支持：tatami 和室预设（榻榻米/障子/矮桌碟碗/坐姿）与 generic 通用房间预设；机位/fov/切点全部来自分镜 JSON
- 注意：previs_engine 自动机位模式（无显式 pos/look）暂不支持生成，需 dialogue 契约 JSON
