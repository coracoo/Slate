# 台词提取规范（ASR + OCR 两路并行）

## OCR 烧录字幕（extract_subtitles.py）
- 原理：裁画面底部字幕区 → 逐 0.5s rapidocr → 去重合并 → SRT。
- 适用：视频有内嵌字幕（剧/番/带字幕片源）。**字幕是片方正本，优先采信。**
- 依赖：rapidocr_onnxruntime（轻量、离线、自带中文模型）。
- 输出：`<名>_字幕.srt`、`<名>_台词.txt`（[起-止] 文本）。
- 注意：OCR 可能错字（己/已），需人工校对一遍。

## ASR 语音转写（extract_speech.py）
- 原理：ffmpeg 抽 16k 单声道 wav → faster-whisper 转写（VAD 过滤）→ SRT。
- 适用：无字幕片源、字幕未覆盖的口语/笑声/停顿、需要语气节奏。
- 依赖：faster-whisper（本地 CPU，int8）；首次下载模型。中文用 --lang zh；
  精度不足时 --model medium 或 large-v3。
- 注意：背景音乐/多人轻声/外语背景会降低准确率（如生活流片 small 模型易误识别）。

## 交叉校正
1. 有烧录字幕：以 OCR 为准，ASR 仅补充字幕外内容与时间。
2. 无字幕：以 ASR 为准，必要时升模型。
3. 两路都输出带时间轴 SRT；按镜头说话人特写把台词归属到 JSON 的 speaker/line。
4. 台词回填到 storyboard：依据每镜起止时间对齐，底部字幕渲染为【角色名】台词。

## 命令
```
python scripts/extract_subtitles.py <video> --start S --end E
python scripts/extract_speech.py   <video> --start S --end E --model small --lang zh
```
