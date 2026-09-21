# storyboard.json 字段规范（v1）

> 这是管线的唯一数据源契约。`validate_storyboard.py` 是本规范的**可执行版本**（规则与下方一一对应）；
> 引擎 `previs_engine.py` 与导出器 `export_docs.py` 只消费这些字段。改规范要同时改校验器。

## 顶层字段

| 字段 | 类型 | 必填 | 默认 | 说明 |
|---|---|---|---|---|
| `project` | string(slug) | ✔ | — | 项目标识，用于输出文件名 |
| `title` | string | ✔ | — | 人类可读标题 |
| `scene` | string | ✔ | — | 场景预设（引擎内置，如 `street_stall`；新场景在引擎 `build_scene` 加预设） |
| `w`,`h` | int(偶数) | — | 960×540 | 输出分辨率 |
| `fps` | int 1–60 | — | 24 | 帧率 |
| `actors` | object | ✔ | — | 角色字典，键为角色 ID |
| `shots` | array | ✔ | — | 镜头数组，按时间顺序 |

## actors（角色）

主角约定用 ID **`c`**（走位/相机跟随主体，必须有 `path`）；其余为静态角色（`static:true` + `pos`）。
常用静态角色：`v`（对手/摊主）、`h`（配角/伙计）。

| 字段 | 类型 | 适用 | 说明 |
|---|---|---|---|
| `name` | string | 全部 | 角色名（必填） |
| `shirt` | [r,g,b] 0–255 | 全部 | 上衣颜色（必填，3 整数） |
| `apron` | [r,g,b] 或 null | 全部 | 围裙/外挂色块，无则 null |
| `scale` | number>0 | 全部 | 身高缩放，默认 1.0 |
| `path` | [[t,x,z],...] | 主角 c | 走位关键帧；**首点 t 必须为 0**、t 严格递增、≥2 点；末点 t 应 ≥ 总时长 |
| `rideUntil` | number | 主角 c | 此时间前为骑乘姿态（人坐在车上） |
| `scooterPark` | [x,z] | 主角 c | 下车后车辆停放点 |
| `static` | true | 非主角 | 标记固定站位角色 |
| `pos` | [x,z] | 非主角 | 固定站位坐标 |

## shots[]（镜头）

所有镜头通用字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | string | ✔ | 镜头号，**全片唯一** |
| `shot` | string | — | 景别（中景/近景/特写/越肩/双人中景…），用于文档 |
| `dur` | number>0 | ✔ | 时长（秒） |
| `mode` | enum | ✔ | `follow` / `cu` / `ots` / `wide` / `keys` |
| `fov` | number 20–90 | — | 焦距（默认 42；越小越特写） |
| `ride` | bool | — | 该镜是否骑乘 |
| `point` | bool | — | 对手角色做"伸手指人"动作 |
| `line` | string | — | 烧录字幕/台词（**写明谁说的**） |
| `prompt_cn` / `prompt_en` | string | — | 该镜 AI 提示词（派生文档/喂模型用） |

### 按 mode 的必需字段

| mode | 含义 | 必需字段 | 相机行为 |
|---|---|---|---|
| `follow` | 跟随跟拍 | `off:[x,y,z]` | 相机=主角坐标+off，look 主角 |
| `wide` | 广角双人 | `off:[x,y,z]` | 相机=主角+off，look 主角与对手中点 |
| `cu` | 特写推镜 | `focus`:角色ID, `dist`:number>0 | 相机自动站在 focus↔对方连线上、距 dist，对焦 focus |
| `ots` | 越肩 OTS | `host`:角色ID | 相机自动到 host 身后越肩，前景虚化 host 肩，看向对方 |
| `keys` | 自由关键帧 | `off:[x,y,z]`（可扩展 from/to） | 预留：手摆机位 |

> 机位坐标不用手填：`cu/ots` 由角色站位**解析算出**，保证轴线正确、不穿帮。

## 校验规则（validate_storyboard.py 对应）

- 顶层 project/title/scene/actors/shots 必备；w/h 偶数；fps 1–60。
- 主角 c 必备 path（≥2 点、首点 t=0、t 严格递增）；非主角静态角色必备 pos。
- 颜色字段必须为 3 个 0–255 整数或 null；scale>0。
- 镜头 id 唯一、dur>0、mode 合法、fov 20–90。
- follow/wide/keys 需 off；cu 需 focus(存在)+dist>0；ots 需 host(存在)。
- 警告（不阻断）：缺 line、缺 prompt_cn、path 结束早于总时长。

## 三条命令

```powershell
# 1) 校验（通过 exit=0，有错 exit=1）
python tools/validate_storyboard.py projects/<p>.json
# 2) 白模成片
python engine/previs_engine.py   projects/<p>.json out/<p>_白模.mp4
# 3) 派生文档（md / xlsx / 提示词）
python tools/export_docs.py       projects/<p>.json out
# 或一键：
python tools/run_pipeline.py      projects/<p>.json
```

---

## v2 增补（高空/3D 动作场景）

- **3D 走位路径**：主角 `path` 关键帧支持 `[t,x,y,z]`（y=高度）。3 分量 `[t,x,z]` 视为 y=0（地面场景）。
- **场景预设**：`scene` 现支持 `street_stall`（街景对话）与 `city_aerial`（摩天楼峡谷+大道，高空）。新场景在引擎 `build_scene` 加预设。
- **新增镜头 mode**：
  - `pov`：俯冲主观。机位在主角后上方、看向脚下前方地面（垂直俯瞰），用于高空俯冲 POV。无需 off。
  - `streetup`：街面仰拍。机位在地面低位、仰视高空主角，用于"街面峡谷仰视"。无需 off。
- **`off2`**（`follow`/`wide` 可选）：`off:[x,y,z]` + `off2:[x,y,z]`，相机偏移在镜头内由 off 插值到 off2，实现**环绕/推轨/横摇**（如绕角色半环绕）。
- **单主角特写**：项目可只有 `actors.c`（无 v/h）。`cu` 在无对手时，相机沿主角朝向正前方 dist 处拍面部（高空推近面具眼等）。
- **高空主角着色**：`city_aerial` 中主角用 `shirt`(红) + `apron`(蓝) 双色块标识战衣。

### 已验证项目
- `maigua_v1.json`：街景对话（follow/cu/ots/wide，地面走位+摩托），29.4s。
- `spiderman_0_30.json`：高空动作（follow 大远景/pov 俯冲/streetup 仰拍/off2 环绕/3D 路径），30.6s。
- 两类迥异场景由**同一引擎 + 数据**产出，证明规范与引擎可复用。

---

## v3 增补（角色身体朝向 / 姿态）

角色不再只是"站直的物体"，支持**全身 3D 朝向**：`yaw`（水平朝向，由走位/对手自动算）+ `pitch`（俯仰）+ `roll`（翻滚）。

- **pitch（俯仰，度）**：`0`=站立；`90`=水平、头朝前（超人式趴飞 / 平躺前扑）；`180`=垂直、头朝下（俯冲扎下）；中间值=倾斜。
- **roll（翻滚，度）**：绕身体长轴翻转（趴/仰躺、空翻）。
- **怎么给姿态**：写在主角 `path` 关键帧里随动作动画，逐段平滑插值：
  - `[t, x, y, z]` → pitch/roll=0（站立）
  - `[t, x, y, z, pitch]`
  - `[t, x, y, z, pitch, roll]`
  - 例：楼顶站立 `pitch 0` → 跃出 `55` → 自由落体趴飞 `85` → 头朝下俯冲 `150` → 落地回 `0`。
- **飞行姿态**：pitch>25° 时引擎自动张开四肢（外展手臂/分腿），表现自由落体/摆荡。
- 街景对话角色恒为 pitch=0（站立）；`pitch` 主要用于高空/动作场景主角。
- 校验器接受路径关键帧长度 3/4/5/6。

---

## v4 增补（人形骨模与配色）

- 人物改为**带关节的人形骨模**（头/躯干/大臂小臂/大腿小腿独立骨段+关节球），而非方块堆叠；任何姿态都读得出是人。
- 姿态由 `pitch` 驱动三档骨架姿势并平滑过渡：站(0)→四肢张开自由落体(90)→头朝下俯冲手臂前伸/并腿(170)。
- 角色可选配色字段（actors.*）：`legs`(腿RGB)、`head`(头RGB)、`hand`(手RGB)；缺省为深裤+肤色。
  - 例：蜘蛛侠 `shirt`红(躯干/臂) + `legs`蓝 + `head`红(面罩) + `hand`红。

---

## v5 增补（标准资产库 + 场景复用）

背景与道具不再用随机方块，而是由**参数化标准资产 builder** 构建、场景只摆实例，跨项目复用：

- 内置资产（engine）：
  - `tower(x,z,w,h,d,style)` 高层楼：`concrete`/`glass`(带塔檐+天线)/`res`(带水箱阁楼)，含顶部收分。
  - `rooftop(x,z,topH)` 楼顶起跳平台（台面+设备间+竖杆）。
  - `tree(x,z,h)` 树、`lamp(x,z)` 路灯、`car(x,z,yaw,col)` 车。
  - 街景：临街铺面楼（带门洞暗部）、瓜摊、摩托等。
- 场景预设由"精选布局"摆放这些资产（city_aerial 城市峡谷+大道车道线 / street_stall 临街铺面），保证轮廓可读、构图可控，非随机堆砌。
- 雾按场景配置（`fogNear/fogFar/fogFloor`）：高空雾远(far~120)保留建筑色，街景雾近(far~52)；背景不再糊成灰团。
- 角色配色可配：`legs/head/hand` RGB（蜘蛛侠蓝腿红面罩）。
- 越肩(OTS)前景宿主只渲染虚化肩块（不画整身），中景才是清晰人物。

> 下一步可扩展：在 storyboard.json 里加 `props:[{type,tower,x,z,w,h,style,...}]`，让场景布局也完全由数据驱动、零改代码即可搭新布景。

---

## v5.1 增补（动作道具：接触面与蛛丝）

提示词里的"动作"必须有对应的**场景道具/接触面**，否则做不出来。已支持：

- **接触表面**：场景可放置倾斜/垂直表面供角色"贴上"。如高空场景内置一块倾斜玻璃屋顶（`obox` 倾斜面 + 支撑），分镜路径在对应时刻把角色放到该面上、pitch 调成贴附(~70-85° 四肢张开)，即表现"砸上玻璃幕墙→贴附滑落"。
- **蛛丝/连线**：镜头加 `"web": true` + `"anchor":[x,y,z]`，渲染一条从角色手部到锚点（如塔顶）的细线，表现吐丝摆荡。
- 经验：previs 要编码的不只是"相机+走位"，还有**动作发生所依赖的道具/表面**（玻璃面、楼顶边缘、挂点）；这些应在场景资产里就位，路径/姿态再与之对齐。

> 局限：当前 2.5D 投影渲染的"面接触"为近似（人物定位到面附近），精确沿面滑动/碰撞需 Three.js/Blender 等真 3D 引擎。
