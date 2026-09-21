# 对白白模 storyboard 字段字典（dialogue_engine 契约）

顶层：project, w(960), h(540), fps(24), set{}, actors{}, shots[]。

## set（环境/道具，极简）
- type: "tatami"（影响地面色）。
- table: {x, z, w, d, h, dishes} 矮桌；镜头级可用 shots[i].table 覆盖。

## actors（角色）
键为 ID（A/B/C…）：
- name 显示名（用于【名】台词前缀）。
- pos [x,z] 站位；x 左右、z 前后（相机在 z 负方向看 +z）。
- shirt [r,g,b] 上衣颜色（0–255）。
- style: "seated"（盘腿坐姿）或 "stand"（站姿）。

## shots[] 每镜
通用：id, dur(秒), cam, fov, line(台词), speaker(说话角色ID),
     move(运镜，顶部条), action(动作), prompt(AI提示词，右上)。

### cam 机位（自动算出）
- wide：全景建立，桌侧后退看人物中点。
- near：某角色身后看桌（前景背影最大）；配 focus=角色ID，可选 serve=角色ID（盛饭手臂）。
- two：双人中景；配 host、target。
- cu ：单人特写；配 target=被拍角色。
- ots：越肩反打；配 host（前景肩）、target（后景人物）。正反打自动成立。

### 1:1 复刻/覆盖（可选）
- pos / look：显式相机世界坐标 [x,y,z]，优先于自动机位。
- staging：{角色ID:[x,z], …} 仅本镜临时站位（不影响全局）。
- table：{x,z,w,d,h} 仅本镜临时道具。
- serve：盛饭/伸手动作的角色 ID。
- scene：room|field（可选）——field 镜画土地+战旗剪影+暖沙雾（不画桌案），3D 在 x=60 平移出战场区。
- pose：{角色ID: seated|stand|ride}（可选）——逐镜姿态覆盖；ride 画马匹灰模+骑手抬高 1.28m。
- 运镜参数（可选，按镜内进度插值）：dolly:"in"|"out"（推/拉）、pan:度数（摇）、whip:1（甩加速）、
  truck:米（移）、orbit:度数（环绕）、crane:米（升降）、zoom:倍率（变焦）。2D 引擎逐帧插值，3D 起止关键帧。

### 镜内台词轨（长镜头不切但台词多条）
- lines: [{at(镜内秒), dur(显示时长), speaker, line}] —— 固定全景长镜头里按时间逐条出现台词。
  引擎按帧进度选当前句，渲染为【角色名】台词。

### 演员表演层（可选）
- performance: {status, packet, input_hash, attempts, warnings[]} —— 演员模型生成的结构化草稿。
- packet.shot_id 必须等于本镜 id；packet.actors[] 每项为 {actor_id, beats[]}。
- beats[] 只允许可见表演字段（at, duration, intent, posture, gaze, gesture, expression, voice）；
  每个 beat 必须完整落在 dur 内。演员层不得改 dur、cam、pos/look、move、lines、走位或剧情事实。
- status 不是 ready 的草稿不得进入图生视频/预演产物；input_hash 用于判断分镜变化后的过期状态。

## 输出叠加
顶部=镜号+运镜；右上=●动作 + ●提示词；底部=有 speaker 时【角色名】台词，无台词有 action 时显示（action）。

## 校验
- 对白用：scripts/validate_dialogue.py <json>
- 走位/动作白模（previs_engine，含 path 走位/pitch 姿态）用：scripts/validate_storyboard.py <json>
