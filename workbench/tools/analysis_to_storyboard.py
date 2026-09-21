# -*- coding: utf-8 -*-
"""拉片解构 analysis.json -> dialogue storyboard.json（白模桥接）

拉片解构（analysis.json）与白模引擎（dialogue_engine.py）是两套数据契约，
本工具把解构镜头表翻译成 dialogue 契约分镜，落到 项目/分镜/ 下，
之后即可在 ⑤ 白模页按镜头区间渲染。

映射规则：
  景别: 特写/近景 -> cu(target=主说话人)；中景/中近景 -> two(双人) 或 cu(单人)；全景/远景 -> wide
        wide 距离按档细分：全景6.2m / 大全景8m / 远景10.5m / 大远景14.5m（越高越远）
  角度: analysis 的 angle 字段直达相机几何——仰视=相机降到0.5m视点上抬 / 俯视=4.5m下压 /
        鸟瞰=14m顶拍 / 虫视=贴地；过肩/主观/荷兰角/平视 不改几何
  运镜: analysis 的 camera_move 映射引擎插值参数——推/拉=dolly、摇/甩=pan(甩加速)、
        移/轨道/手持/斯坦尼康=truck、环绕=orbit、升降/无人机=crane、变焦=zoom
  人物: 在场=镜头台词说话人 ∪ action/prompt 文本点名的角色名；文本只提"将领/武将"等
        泛称时以前 3 角色作替身；只提"大军/士兵"等群体则留空场（白模只剩旗帜/军阵）
  姿态: 每镜 pose 表——军帐(room)=seated 跪坐议事；战场(field)=stand，action 提骑马/
        勒马/乘马等则 ride（引擎画马匹灰模+抬高骑手）
  台词: 只取 台词/台词脚本.json（AI 归属后的真实人名），未合并台词则拒绝生成（避免不准的台词进分镜）；
        每句台词按时间重叠最大只归属一个镜头，不重复出现
  角色: 按台词总时长降序分配 actor id（c=主角），静态站位按"室内议事"惯例：
        双席侧前(1/3)、对位(2)、主位后中(4)——居中主位在最深处面向镜头
  场景: 按 prompt/story/action 关键词逐镜判 scene=room(军帐/厅堂)|field(战场/旷野)；
        同场景连续镜头为一段，全景把不在场角色移出画外，
        3D 构建按 scene 分场景搭建（军帐=房间，战场=旷野+战旗+远处军阵）
用法: python analysis_to_storyboard.py <项目目录> [--analysis 版本名] [--style stand|seated] [--out 文件名.json]
stdout 末行打印 OUTPUT:<绝对路径>；退出码 0=成功 1=失败
"""
import sys, os, json, glob, argparse
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ACTOR_IDS = ["c", "v", "h", "a", "b", "d", "e", "f", "g", "k"]
PALETTE = [(40, 90, 160), (170, 60, 60), (60, 140, 90), (150, 120, 50),
           (120, 70, 150), (60, 150, 150), (180, 100, 40), (100, 100, 110),
           (140, 60, 110), (70, 110, 60)]
# 环绕场景中心的静态站位。前 4 位按"室内议事"惯例排：双席侧前 + 对位 + 主位后中
# （主位最深、面向镜头，两侧侧身向主位——对应对话戏常见的"居中者在最深处"构图，
#  而不是把第 4 人放在最靠近镜头处）
POSITIONS = [(-1.7, -0.9), (1.8, 0.3), (1.7, -0.9), (0.0, 2.2),
             (-2.5, 1.4), (2.5, 1.6), (-2.6, -1.7), (2.6, -1.8),
             (-3.3, 0.6), (3.3, 0.9)]

# 场景关键词：命中"室外/战场"系 -> scene=field，否则 room（军帐/厅堂/室内默认）
FIELD_WORDS = ("战场", "阵前", "军阵", "列阵", "旷野", "荒野", "原野", "郊野", "行军",
               "战旗", "旌旗", "军旗", "骑马", "马上", "马背", "坠马", "兵马", "大军",
               "两军", "对峙", "烟尘", "战尘", "出征", "旷", "野", "沙场", "黄沙")
ROOM_WORDS = ("军帐", "帐内", "帐中", "帐", "室内", "厅堂", "案几", "案前", "案后",
              "帷幔", "帐幔", "烛", "地毯", "跪坐", "端坐案")
# 在场判定：泛指个人 -> 前3角色作替身；纯群体 -> 空场（只留旗帜/军阵）
PERSON_WORDS = ("将领", "武将", "文臣", "将军", "男子", "人物", "官员", "老者", "军师",
                "都督", "主帅", "司徒", "太尉", "众人", "众将", "文武", "老臣")
CROWD_WORDS = ("大军", "军阵", "士兵", "士卒", "兵士", "阵列", "列阵", "军队", "人马", "兵马")
# 骑马姿态关键词（命中最多的镜头文本即判本镜骑马）
RIDE_WORDS = ("骑马", "马上", "马背", "乘马", "勒马", "纵马", "坠马", "马蹄", "战马")

def shot_scene(s):
    """镜头场景判定：室内(军帐/厅堂)=room，室外战场=field。prompt_cn 最详，story/action 次之。"""
    txt = str(s.get("prompt_cn") or "") + str(s.get("story") or "") + str(s.get("action") or "")
    f = sum(txt.count(w) for w in FIELD_WORDS)
    r = sum(txt.count(w) for w in ROOM_WORDS)
    if f and f >= r:
        return "field"
    return "room"

# 显式机位（pos/look）下的 fov：构图由相机距离/视高控制，fov 只微调松紧
SIZE_FOV_STAND = {"特写": 40, "近景": 42, "中近景": 44, "中景": 46, "全景": 48, "大全景": 50, "远景": 50}
SIZE_FOV_SEATED = {"特写": 38, "近景": 40, "中近景": 42, "中景": 44, "全景": 48, "大全景": 50, "远景": 50}


def latest_analysis(proj):
    vf = os.path.join(proj, "拉片", "_versions.json")
    if os.path.isfile(vf):
        try:
            vs = json.load(open(vf, encoding="utf-8"))
            done = [v for v in vs if v.get("status") == "done"
                    and os.path.isfile(os.path.join(proj, "拉片", v.get("name", ""), "analysis.json"))]
            if done:
                return done[-1]["name"]
        except Exception:
            pass
    cands = [d for d in glob.glob(os.path.join(proj, "拉片", "*"))
             if os.path.isfile(os.path.join(d, "analysis.json"))]
    if not cands:
        return None
    return os.path.basename(max(cands, key=os.path.getmtime))


def load_lines(proj):
    """合并台词（真实人名）-> [{t_in,t_out,text,speaker}]；找不到返回 []。"""
    for name in (os.path.join("台词", "台词脚本.json"),):
        p = os.path.join(proj, name)
        if os.path.isfile(p):
            try:
                d = json.load(open(p, encoding="utf-8"))
                rows = d.get("lines", d) if isinstance(d, dict) else d
                out = [{"t_in": float(r["t_in"]), "t_out": float(r["t_out"]),
                        "text": str(r.get("text", "")), "speaker": str(r.get("speaker", "") or "")}
                       for r in rows if r.get("text")]
                if out:
                    print(f"[信息] 台词来源: {name}（{len(out)} 行）")
                    return out
            except Exception as e:
                print(f"[警告] {name} 读取失败: {e}")
    return []


def build_actors(lines, style):
    """按台词总时长降序分配 actor id / 站位 / 服色。"""
    dur = {}
    for L in lines:
        sp = L["speaker"]
        if not sp or sp == "unknown":
            continue
        dur[sp] = dur.get(sp, 0.0) + max(0.3, L["t_out"] - L["t_in"])
    names = sorted(dur, key=lambda k: -dur[k])[:len(ACTOR_IDS)]
    if not names:
        names = ["角色A"]
    actors = {}
    for i, nm in enumerate(names):
        actors[ACTOR_IDS[i]] = {
            "name": nm, "shirt": list(PALETTE[i % len(PALETTE)]),
            "pos": list(POSITIONS[i % len(POSITIONS)]), "static": True,
        }
        if style == "stand":
            actors[ACTOR_IDS[i]]["style"] = "stand"
    id_of = {nm: ACTOR_IDS[i] for i, nm in enumerate(names)}
    return actors, id_of


def assign_lines(ana, lines):
    """每条台词只归属一个镜头（时间重叠最大者），避免一句台词重复出现在多个镜头。
    返回 {shot_index: [L, ...]}（元素为台词脚本原始行）。"""
    windows = [(float(s.get("t_in", 0)), float(s.get("t_out", 0))) for s in ana["shots"]]
    assigned = {}
    for L in lines:
        best, best_ov = -1, 0.15
        for i, (t0, t1) in enumerate(windows):
            ov = min(L["t_out"], t1) - max(L["t_in"], t0)
            if ov > best_ov:
                best, best_ov = i, ov
        if best >= 0:
            assigned.setdefault(best, []).append(L)
    return assigned


def convert(ana, lines, id_of, actors, style):
    size_fov = SIZE_FOV_STAND if style == "stand" else SIZE_FOV_SEATED
    pos_of = {aid: tuple(ac["pos"]) for aid, ac in actors.items()}
    # 显式相机几何（pos/look 直达引擎，跳过自动机位）：按姿态给每档景别 (距离, 相机高, 视点高)
    # 站立人物头高 ~1.86(含头球上沿 ~2.06)；盘坐 ~1.06(上沿 ~1.2)
    CU_GEO = {
        "stand":  {"特写": (2.4, 1.75, 1.70), "近景": (3.0, 1.60, 1.55),
                   "中近景": (3.8, 1.60, 1.45), "中景": (4.6, 1.50, 1.30)},
        "seated": {"特写": (1.8, 1.20, 1.10), "近景": (2.4, 1.15, 1.00),
                   "中近景": (2.9, 1.15, 0.95), "中景": (3.4, 1.15, 0.90)},
    }
    cu_geo = CU_GEO[style]
    # wide 距离档（距离, 相机高, 视点高）：景别越大越远越高，大远景要看到军阵全貌
    WIDE_GEO = {"全景": (6.2, 2.0, 1.1), "大全景": (8.0, 2.2, 1.15),
                "远景": (10.5, 2.6, 1.25), "大远景": (14.5, 3.4, 1.4)}

    def apply_angle(pos, look, ang):
        """angle 受控词 -> 相机几何。只改 pos/look 高度与视点，不动水平方位。
        仰视只降相机不抬视点（视点本就取人脸高，再抬会越过头顶出画）。"""
        if ang == "仰视":
            return [pos[0], min(pos[1], 0.5), pos[2]], look
        if ang == "俯视":
            # 俯角要有感：高机位按相机-目标水平距离的比例抬（大远景 ~50%距离高）
            rise = max(4.5, abs(pos[2] - look[2]) * 0.5)
            return [pos[0], max(pos[1], rise), pos[2]], [look[0], min(look[1], 0.3), look[2]]
        if ang == "鸟瞰":
            return [look[0], 14.0, look[2] - 3.5], [look[0], 0.0, look[2]]
        if ang == "虫视":
            return [pos[0], 0.25, pos[2]], look
        return pos, look    # 平视/过肩/主观/荷兰角 不改几何

    def explicit_cam(cam, spk_ids, size, ang, tgt_ride=False, ride_h=0.0):
        """返回 (pos, look, fov_override)；显式机位 1:1 控制构图，角度/骑马二次修正。
        tgt_ride: cu 焦点人物骑马（头高 ~3.1m，视线/相机随抬，距离略拉远）
        ride_h:   wide/two 场景里存在骑马者时的整体抬升/拉远。"""
        if cam == "cu" and spk_ids:
            ax, az = pos_of.get(spk_ids[0], (0.0, 2.0))
            n = (ax * ax + az * az) ** 0.5
            dx, dz = (-ax / n, -az / n) if n > 0.1 else (0.0, -1.0)   # 朝场景中心拍脸
            dd, ch, lh = cu_geo.get(size, cu_geo["近景"])
            if tgt_ride:
                dd *= 1.25; ch += ride_h * 0.55; lh += ride_h
            pos, look = apply_angle([round(ax + dx * dd, 2), ch, round(az + dz * dd, 2)],
                                    [ax, lh, az], ang)
            return pos, look, None
        if cam == "two" and len(spk_ids) >= 2:
            ax, az = pos_of.get(spk_ids[0], (-1.8, 0.0))
            bx, bz = pos_of.get(spk_ids[1], (1.8, 0.0))
            mx, mz = (ax + bx) / 2, (az + bz) / 2
            dx, dz = bx - ax, bz - az
            sep = (dx * dx + dz * dz) ** 0.5 or 1.0
            px, pz = -dz / sep, dx / sep                              # 垂直于轴线
            perp = max(3.0, sep * 1.2)
            # 按两人间距自适应 fov，保证两人都进画（留 0.7m 边距）
            import math as _m
            fov2 = max(46, min(60, 2 * _m.degrees(_m.atan((sep / 2 + 0.7) / perp))))
            tch, tlh = (1.6, 1.35) if style == "stand" else (1.2, 0.95)
            tch += ride_h * 0.55; tlh += ride_h
            pos, look = apply_angle([round(mx + px * -perp, 2), tch, round(mz + pz * -perp, 2)],
                                    [mx, tlh, mz], ang)
            return pos, look, round(fov2, 1)
        dd, ch, lh = WIDE_GEO.get(size, WIDE_GEO["全景"])
        if ride_h:
            dd *= 1.7; ch += 1.2; lh += 0.6   # 骑马全景：人形总高 ~3.1m，不拉远会冲出画框
        pos, look = apply_angle([0.0, ch, -dd], [0.0, lh, 0.0], ang)
        return pos, look, None                                        # wide

    shots_out = []
    line_of = assign_lines(ana, lines)
    # 场景分段：同场景连续镜头为一段；段内所有说过话的角色视为"在场"
    scenes = [shot_scene(s) for s in ana["shots"]]
    seg_of = []
    for i in range(len(scenes)):
        if i > 0 and scenes[i] != scenes[i - 1]:
            seg_of.append(seg_of[-1] + 1)
        else:
            seg_of.append(0 if not seg_of else seg_of[-1])
    present = {}   # seg_id -> set(actor_id)
    for si, s in enumerate(ana["shots"]):
        who = set()
        for L in line_of.get(si, []):
            aid = id_of.get(L["speaker"])
            if aid:
                who.add(aid)
        present[seg_of[si]] = present.get(seg_of[si], set()) | who
    for si, s in enumerate(ana["shots"]):
        t0, t1 = float(s.get("t_in", 0)), float(s.get("t_out", 0))
        dur = max(1.0, float(s.get("duration") or (t1 - t0) or 2.0))
        size = str(s.get("shot_size", "") or "")
        # 镜内台词：已由 assign_lines 去重分配（每句只属于一个镜头）
        inside = []
        for L in line_of.get(si, []):
            lo, hi = max(L["t_in"], t0), min(L["t_out"], t1)
            if hi - lo <= 0:
                lo, hi = L["t_in"], L["t_out"]   # 台词被分到本镜但窗口相离（镜头时间轴重叠），按原台词时间计
            aid = id_of.get(L["speaker"])
            inside.append({"at": round(max(0.0, lo - t0), 2), "dur": round(max(0.8, hi - lo), 2),
                           "speaker": aid, "line": L["text"]})
        inside.sort(key=lambda x: x["at"])
        spk_ids = []
        for L in inside:
            if L["speaker"] and L["speaker"] not in spk_ids:
                spk_ids.append(L["speaker"])
        # 镜头文本（在场/姿态/骑马判定共用）
        txt = str(s.get("action") or "") + " " + str(s.get("prompt_cn") or "") + " " + str(s.get("story") or "")
        # 景别 -> 机位（大远景/远景/全景/大全景一律 wide）
        if size in WIDE_GEO:
            cam = "wide"
        elif len(spk_ids) >= 2:
            cam = "two"
        elif spk_ids:
            cam = "cu"
        else:
            cam = "wide"
        ang = str(s.get("angle", "") or "")
        # 姿态先于机位：军帐=跪坐议事；战场=站立，文本提骑马则全员骑马（人形总高 +1.28m）
        pose = {aid_: ("seated" if scenes[si] == "room" else
                       ("ride" if any(w in txt for w in RIDE_WORDS) else "stand"))
                for aid_ in pos_of}
        ride_h = 1.28 if any(v == "ride" for v in pose.values()) else 0.0
        pos, look, fov_ov = explicit_cam(cam, spk_ids, size, ang,
                                         tgt_ride=(pose.get(spk_ids[0]) == "ride" if spk_ids else False),
                                         ride_h=ride_h)
        sh = {"id": s.get("id", f"S{len(shots_out)+1}"), "dur": round(dur, 2),
              "cam": cam, "pos": pos, "look": look,
              "fov": fov_ov or size_fov.get(size, 52 if style == "stand" else 44),
              "scene": scenes[si], "pose": pose}
        # 在场：cu/two 只留当事人；wide 按可信度递减——文本点名 > 本镜有台词(段内说话人)
        #       > 泛称个人(前3替身) > 纯群体(空场，只留旗帜/军阵) > 段内说话人兜底
        if cam in ("cu", "two") and spk_ids:
            on = set(spk_ids)
        else:
            name_hits = {aid_ for aid_, inf in actors.items() if inf.get("name") and inf["name"] in txt}
            if name_hits:
                on = name_hits
            elif inside:
                on = set(present.get(seg_of[si], set()))
            elif any(w in txt for w in PERSON_WORDS):
                on = set(list(actors)[:3])
            elif any(w in txt for w in CROWD_WORDS):
                on = set()
            else:
                on = set(present.get(seg_of[si], set()))
        away = {aid_: [25.0, 25.0] for aid_ in pos_of if aid_ not in on}
        if away:
            sh["staging"] = away
        if cam == "two":
            sh["host"], sh["target"] = spk_ids[0], spk_ids[1]
        elif cam == "cu":
            sh["target"] = spk_ids[0]
        if inside:
            sh["lines"] = inside
            first = next((L for L in inside if L["speaker"]), inside[0])
            if first.get("speaker"):
                sh["speaker"] = first["speaker"]
            sh["line"] = first["line"]
        move = str(s.get("camera_move", "") or "")
        # camera_move 受控词 -> 引擎运镜插值参数（引擎按镜内进度 0..1 插值）
        if move == "推": sh["dolly"] = "in"
        elif move == "拉": sh["dolly"] = "out"
        elif move == "摇": sh["pan"] = 14.0
        elif move == "甩": sh["pan"] = 22.0; sh["whip"] = 1
        elif move in ("移", "轨道", "斯坦尼康", "手持"): sh["truck"] = 1.6
        elif move == "环绕": sh["orbit"] = 36.0
        elif move in ("升降", "无人机"): sh["crane"] = 2.6
        elif move == "变焦": sh["zoom"] = 1.18
        sh["move"] = "·".join(x for x in (size, move, ang) if x) or "固定"
        if s.get("action"):
            sh["action"] = str(s["action"])[:60]
        if s.get("prompt_cn"):
            sh["prompt"] = str(s["prompt_cn"])[:150]
        shots_out.append(sh)
    return shots_out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project", help="项目目录，如 projects/丞相骂王朗")
    ap.add_argument("--analysis", default=None, help="拉片版本名（默认最新 done 版本）")
    ap.add_argument("--style", default="stand", choices=["stand", "seated"], help="人物姿态（默认 stand）")
    ap.add_argument("--out", default=None, help="输出文件名（默认 分镜/<版本名>.json）")
    a = ap.parse_args()
    proj = os.path.abspath(a.project)
    if not os.path.isdir(proj):
        print(f"[错误] 项目目录不存在: {proj}"); sys.exit(1)
    ver = a.analysis or latest_analysis(proj)
    if not ver:
        print("[错误] 项目下没有拉片解构版本（拉片/*/analysis.json）"); sys.exit(1)
    aj = os.path.join(proj, "拉片", ver, "analysis.json")
    if not os.path.isfile(aj):
        print(f"[错误] analysis.json 不存在: {aj}"); sys.exit(1)
    ana = json.load(open(aj, encoding="utf-8"))
    if not isinstance(ana.get("shots"), list) or not ana["shots"]:
        print("[错误] analysis.json 缺少 shots"); sys.exit(1)

    lines = load_lines(proj)
    if not lines:
        # 台词优先于拉片：未合并台词时生成的分镜没有可靠台词，直接拒绝，提示先去台词页
        print("[错误] 未找到合并台词（台词/台词脚本.json）。请先到台词页：提取 OCR/ASR → AI 人物归属 → 合并台词，再生成白模分镜。")
        sys.exit(1)
    actors, id_of = build_actors(lines, a.style)
    shots = convert(ana, lines, id_of, actors, a.style)
    n_lines = sum(len(s.get("lines", [])) for s in shots)
    cfg = {
        "project": os.path.basename(proj) + "_" + ver,
        "title": f"{os.path.basename(proj)} · {ver}（解构自动分镜）",
        "w": 960, "h": 540, "fps": 24,
        "set": {},
        "actors": actors,
        "shots": shots,
    }
    outdir = os.path.join(proj, "分镜")
    os.makedirs(outdir, exist_ok=True)
    fn = a.out or (ver + ".json")
    out = os.path.join(outdir, fn)
    from versions import snapshot
    snapshot(out)
    json.dump(cfg, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[完成] {len(shots)} 镜 / {len(actors)} 角色（{', '.join(a2['name'] for a2 in actors.values())}）/ 镜内台词 {n_lines} 条")
    print(f"OUTPUT:{os.path.abspath(out)}")


if __name__ == "__main__":
    main()
