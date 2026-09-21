# 拉片 → 解析 → 白模成片 · 标准作业流程（SOP / Pipeline v1）

> 目标：把"看片拆解"变成一条可复用、可迭代的数据管线。
> 核心思想：**一切以一份分镜 JSON（storyboard）为唯一数据源（Single Source of Truth）**。
> 拉片的产出不是文档，而是结构化数据；文档、Excel、AI 提示词、白模视频都是这份数据的"视图"。

```
视频素材
  │  ① 拉片  抽帧 / 切点检测 / 记录运镜·景别·台词·走位
  ▼
storyboard.json   ← 唯一数据源（人工校对 + 字段规范化）
  │
  ├──② 解析/派生──► 分镜表.md / .xlsx、中英文提示词、拍摄清单
  │
  └──③ 白模成片──► previs_engine.py 读 JSON
                    └─► 自动运镜/走位/字幕 → 白模 MP4（无AI）
                          │
                          └─④（可选）导出首帧/轨迹 → 喂 AI 图生视频做结构控制
```

---

## 目录规范

```
previs_pipeline/
├─ engine/
│   └─ previs_engine.py      # 通用渲染引擎（读 JSON 出 MP4，不针对某个项目）
├─ projects/
│   └─ <项目名>.json          # 每个片子一份分镜 JSON（数据源）
└─ out/
    └─ <项目名>_白模.mp4      # 引擎产物
```
- 一个项目 = 一个 JSON。多支视频就多份 JSON（如 `maigua_v1.json`、`maigua_v2.json`）。
- 引擎与数据分离：**换片子只改 JSON，不改代码**。

---

## 阶段 ① 拉片（采集，产出"草稿分镜"）

工具（opencv/ffmpeg）：
1. **元数据**：fps、时长、分辨率、横竖屏。
2. **每秒 1 帧接触印片**（contact sheet）快速看全片结构。
3. **自动切点检测**（帧差/直方图差异）出候选切点，再用 0.5s 密集拼图**人工归并**成叙事节拍。
4. 逐镜记录：时间码、景别、运镜、机位、画面内容、台词/声音、出场人物、场景。

产出：一张逐镜表（先在纸上/表格里），重点写清——
- **运镜**：固定 / 跟拍(follow) / 推镜(cu) / 越肩(ots) / 广角(wide) / 环绕 / 俯冲…
- **角色走位**：谁从哪到哪（起点→终点坐标或相对位置）。
- **台词归属**：哪句是谁说的（务必校对）。

## 阶段 ② 解析（结构化，产出 storyboard.json）

把逐镜表填进 JSON。字段规范：

- **项目级**
  - `w/h/fps`：输出分辨率与帧率（默认 960×540@24）。
  - `scene`：场景预设（引擎内置，如 `street_stall`；新场景在引擎里加预设函数）。
  - `actors`：角色字典。键 `c/v/h…` 为角色 ID。
    - `name`、`shirt`(RGB)、`apron`(RGB或null)、`scale`
    - 主角 `c`：`path`=[[t,x,z],…] 走位关键帧；`rideUntil`（此前骑车）；`scooterPark`（车停放点）
    - 配角：`pos`=[x,z] 固定站位、`static:true`
- **镜头级 `shots[]`**
  - `id`、`shot`(景别)、`dur`(秒)
  - `mode`：`follow` 跟随 / `cu` 特写 / `ots` 越肩 / `wide` 广角 /（可扩展 `keys` 自由关键帧）
  - `follow/wide`：`off`=[x,y,z] 相机相对主角偏移；`fov`
  - `cu`：`focus`=角色ID、`dist`=机距、`fov`
  - `ots`：`host`=前景角色ID（越谁的肩）、`fov`（自动看向对方）
  - `ride`(bool)、`point`(bool，伸手指人)
  - `line`：烧录字幕（含台词归属）
  - `prompt_cn` / `prompt_en`：该镜 AI 提示词（**派生文档/喂模型用**）

> 机位不用手写坐标：`cu/ots` 由角色站位**解析算出**（自动站在两人连线上、隔桌对焦），这是不穿帮、轴线正确的关键。

## 阶段 ③ 白模成片（渲染，产出 MP4）

```powershell
python previs_pipeline/engine/previs_engine.py previs_pipeline/projects/<项目>.json
# 或指定输出：
python previs_pipeline/engine/previs_engine.py <项目>.json out/<名>.mp4
```
引擎自动：搭场景灰模 → 按 `path` 驱动走位 → 按 `mode` 解析相机/运镜 → 越肩前景虚化 → 烧镜号/运镜模式/台词字幕 → 编码 MP4。

**迭代闭环**：看白模 → 改 JSON（改 `dur/fov/off/dist/走位/台词`）→ 重渲（秒级），直到构图、节奏、轴线都对。

## 阶段 ④（可选）对接 AI 视频
- 导出每镜**首帧/末帧**（白模截图）当图生视频的结构参考，配 `prompt_cn/en`；
- AI 负责"灰模→真人真景 + 打光上色"，机位/走位/轴线已被白模锁死。
- 进阶：引擎可扩展输出**深度图/相机轨迹**做更强控制（或迁移到 Blender/UE 用同一份 JSON）。

---

## 质量检查清单（每支片子过一遍）

- [ ] 切点归并后镜头数、总时长与素材一致
- [ ] 每镜 `mode / fov / dur` 已填；特写不顶头、越肩前景不挡人
- [ ] 角色走位 `path` 连贯（骑车→停车→步行终点衔接）
- [ ] **台词归属正确**（谁说的写在谁的镜头/`line`）
- [ ] 越肩正反打方向交替、轴线不跳
- [ ] 景别随冲突递进（中景→近景→特写）
- [ ] JSON 可被引擎渲染、无字段缺失

## 命名与版本
- 项目名 `片子_集数或段落`，如 `maigua_v1`、`spiderman_0_30`。
- 改分镜就迭代 JSON；重要版本另存（`_v2.json`），白模产物同名带版本。
- 数据源（JSON）入版本库；MP4 为可再生的派生物，不必长期留存。

## 新增一个项目的最短路径
1. 抽帧 + 切点检测，写逐镜草稿；
2. 复制 `projects/maigua_v1.json` 改 `actors` 与 `shots`；
3. 新场景就在引擎加一个 `build_scene` 预设（灰模即可）；
4. 跑引擎 → 看白模 → 改 JSON → 重渲；
5. 确认后由 JSON 派生 md/xlsx/提示词，并导出首帧喂 AI。
