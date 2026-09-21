# -*- coding: utf-8 -*-
"""
镜头语言讲解生成器（AI 短片分析工作台 阶段二 · providers v2）
用法: python explain_shots.py <analysis目录> [--providers providers.json] [--vendor 厂商id]
流程: 读 analysis.json -> 有可用厂商（enabled 且 models.vision 非空配好 key）时逐镜发
      3 关键帧+镜元数据，要求返回 JSON {narrative, visual, technique, takeaway}（讲解四段式）；
      无厂商/失败时退化为规则版（用景别+运镜+光线组合基础讲解）-> 写 讲解.md
依赖: 无（vision 讲解可选，需 providers.json 中厂商 models.vision 非空且配好 key）
退出码: 0=成功 1=失败；末行打印 OUTPUT:<md路径>
"""
import sys, os, json, argparse, datetime, base64
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, HERE)
import llm_openai

EXPLAIN_PROMPT = """你是电影镜头语言讲师。下面是同一镜头的 3 张关键帧（按时间顺序）和拉片元数据。
请只输出一个 JSON 对象（不要输出任何其他文字），字段均为字符串:
- "narrative": 叙事功能——这一镜在整段戏里承担什么叙事作用（推进/铺垫/转折/抒情等），一到两句
- "visual": 画面与美术——构图、色彩、光线、人物调度、场景氛围，一到两句
- "technique": 镜头语言技法——景别/运镜/角度/剪辑节奏具体怎么服务于情绪，一到两句
- "takeaway": 可迁移准则——拍摄同类场景时可直接照搬的一条经验
镜元数据:
{meta}
全部字段必须是字符串，不要用 null。"""

# 规则版讲解素材
SIZE_NOTE = {
    "特写": "特写剥夺环境信息、放大情绪，适合强调人物内心或关键细节",
    "近景": "近景聚焦人物表情与对话，是叙事交流的基准景别",
    "中近景": "中近景兼顾表情与手势，常用于对话中的反应与态度",
    "中景": "中景交代人物动作与关系，叙事信息密度最均衡",
    "全景": "全景建立人物与空间的关系，交代场面调度的全貌",
    "远景": "远景以环境为主体，营造规模感或孤独感",
}
MOVE_NOTE = {
    "推": "推镜把观众注意力强行收束到主体上，逼近情绪核心",
    "拉": "拉镜把主体放回环境，常用于结束一段情绪或揭示全貌",
    "摇": "摇镜在原地扫过空间，适合交代位置关系或跟随视线",
    "移": "移镜平行跟随主体，带来旁观游历的临场感",
    "跟": "跟随镜头贴身记录运动，让观众与角色同速前行",
    "升降": "升降改变垂直视角，揭示或压制空间层次",
    "手持": "手持的轻微晃动制造纪实感与不安",
    "固定": "固定机位让画面稳定，把注意力完全交给表演与调度",
}
ANGLE_NOTE = {"仰拍": "仰拍抬高主体地位", "俯拍": "俯拍削弱主体、交代全局",
              "过肩": "过肩构图建立对话双方的空间关系"}

def rule_explain(s, idx, total):
    """无 AI 时的规则版讲解：用 analysis 已有字段组合基础说明。"""
    size = s.get("shot_size") or "未标注景别"
    move = s.get("camera_move") or "固定"
    angle = s.get("angle") or "平视"
    parts = []
    parts.append(f"{s.get('story') or s.get('action') or '（拉片未记录剧情，需补 story 字段）'}。"
                 f"这是全片第 {idx}/{total} 镜，时长 {s.get('duration', 0):.2f}s。")
    visual = [f"景别为{size}，{SIZE_NOTE.get(size, '该景别承担相应的信息取舍')}。"]
    if s.get("lighting"): visual.append(f"光线: {s['lighting']}。")
    if s.get("action"): visual.append(f"画面动作: {s['action']}。")
    parts.append("".join(visual))
    tech = []
    if move in MOVE_NOTE: tech.append(MOVE_NOTE[move] + "。")
    if angle in ANGLE_NOTE: tech.append(ANGLE_NOTE[angle] + "。")
    if not tech: tech.append("机位保持稳定，依靠表演与调度承载信息。")
    parts.append("".join(tech))
    parts.append(
                 (f"{size}+{move}的组合适合「{s.get('story') or s.get('action') or '同类场景'}」这类叙事需求，"
                  f"复刻时先锁机位运动再定景别。"))
    return {"narrative": parts[0], "visual": parts[1],
            "technique": parts[2], "takeaway": parts[3]}

def ai_explain(client, shot, keyframe_paths):
    """逐镜调 vision provider，返回四段式 dict；失败抛异常由上层降级。"""
    meta = {
        "id": shot.get("id"), "t_in": shot.get("t_in"), "t_out": shot.get("t_out"),
        "duration": shot.get("duration"), "shot_size": shot.get("shot_size"),
        "camera_move": shot.get("camera_move"), "angle": shot.get("angle"),
        "lighting": shot.get("lighting"), "action": shot.get("action"),
        "story": shot.get("story"), "dialogue": shot.get("dialogue"),
        "prompt_cn": shot.get("prompt_cn"),
    }
    content = [{"type": "text", "text": EXPLAIN_PROMPT.format(meta=json.dumps(meta, ensure_ascii=False))}]
    for kp in keyframe_paths:
        content.append(llm_openai.VendorClient.image_part(kp))
    msgs = [{"role": "user", "content": content}]
    txt = client.chat(msgs, kind="vision", max_tokens=2048, timeout=180, temperature=0.4)
    # 从回复中抠 JSON（容错: 允许 ```json 包裹）
    m = txt.find("{"); n = txt.rfind("}")
    if m < 0 or n <= m: raise RuntimeError("AI 未返回 JSON: " + txt[:120])
    obj = json.loads(txt[m:n + 1])
    return {k: str(obj.get(k) or "").strip() for k in ("narrative", "visual", "technique", "takeaway")}

def pick_vision_vendor(providers_path, prefer_id=None):
    """返回 (VendorClient, providers_path) 或 (None, providers_path)。
    自动挑第一个 enabled 且 models.vision 非空、配好 base_url+key 的厂商。"""
    vendors = llm_openai.load_vendors(providers_path)
    if not vendors:
        return None, providers_path
    pp = os.path.abspath(providers_path)
    cands = [v for v in vendors if (v.get("models") or {}).get("vision") and v.get("enabled")]
    if prefer_id:
        cands = [v for v in vendors if v.get("id") == prefer_id and v.get("enabled")] or cands
    for v in cands:
        if not (v.get("base_url") and v.get("api_key")):
            continue
        try:
            return llm_openai.VendorClient(v["id"], pp), pp
        except Exception:
            continue
    return None, pp

def build_markdown(analysis, explains, mode):
    a = analysis
    L = []
    L.append(f"# {a.get('name','')} 镜头语言讲解\n")
    L.append(f"- 源片: {a.get('source','')}")
    L.append(f"- 生成: {datetime.datetime.now().isoformat(timespec='seconds')} · 模式: {mode}")
    L.append(f"- 镜头数: {len(a.get('shots', []))}\n")
    # 全片节奏小结
    shots = a.get("shots", [])
    durs = [s.get("duration", 0) for s in shots]
    avg = sum(durs) / len(durs) if durs else 0
    moves = {}
    for s in shots: moves[s.get("camera_move") or "固定"] = moves.get(s.get("camera_move") or "固定", 0) + 1
    top = sorted(moves.items(), key=lambda x: -x[1])[:3]
    L.append("## 全片节奏小结\n")
    L.append(f"- 平均镜头时长 {avg:.2f}s" + ("，节奏偏快" if avg < 4 else "，节奏舒缓" if avg > 7 else "，节奏适中") + "。")
    L.append(f"- 主要运镜: " + "、".join(f"{k}×{v}" for k, v in top) + "。")
    cuts = sum(1 for i in range(1, len(shots))
               if shots[i].get("shot_size") != shots[i - 1].get("shot_size"))
    L.append(f"- 景别切换 {cuts} 次" + ("，景别对比强烈" if cuts > len(shots) / 2 else "，景别变化平稳") + "。\n")
    for s, e in zip(shots, explains):
        L.append(f"\n## {s.get('id','?')}  {s.get('t_in',0):.2f}s – {s.get('t_out',0):.2f}s"
                 f"（{s.get('duration',0):.2f}s · {s.get('shot_size','')} / {s.get('camera_move','')} / {s.get('angle','')}）\n")
        L.append(f"**叙事功能**: {e['narrative']}")
        L.append(f"\n**画面与美术**: {e['visual']}")
        L.append(f"\n**镜头语言技法**: {e['technique']}")
        L.append(f"\n**可迁移准则**: {e['takeaway']}\n")
    return "\n".join(L) + "\n"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("analysis_dir", help="analysis.json 所在目录")
    ap.add_argument("--providers", default=os.path.join(HERE, "..", "providers.json"),
                    help="providers.json 路径")
    ap.add_argument("--vendor", "--provider", dest="vendor", default=None,
                    help="指定厂商 id（--provider 为兼容别名）")
    a = ap.parse_args()
    ad = os.path.abspath(a.analysis_dir)
    aj = os.path.join(ad, "analysis.json")
    if not os.path.isfile(aj):
        print(f"[错误] analysis.json 不存在: {ad}"); sys.exit(1,flush=True)
    analysis = json.load(open(aj, encoding="utf-8"))
    shots = analysis.get("shots") or []
    if not shots:
        print("[错误] analysis.json 无 shots"); sys.exit(1,flush=True)

    client, pp = pick_vision_vendor(a.providers, a.vendor)
    mode = "AI vision 讲解"
    explains = []
    if client:
        print(f"[提示] 使用厂商: {client.id}",flush=True)
        for i, s in enumerate(shots):
            kps = [os.path.join(ad, k) for k in (s.get("keyframes") or [])[:3]
                   if os.path.isfile(os.path.join(ad, k))]
            if not kps:
                print(f"[警告] {s.get('id')} 无关键帧，该镜降级为规则版",flush=True)
                explains.append(rule_explain(s, i + 1, len(shots))); continue
            try:
                explains.append(ai_explain(client, s, kps))
                print(f"  {s.get('id')} 讲解完成",flush=True)
            except Exception as e:
                print(f"[警告] {s.get('id')} AI 讲解失败，降级规则版: {e}",flush=True)
                explains.append(rule_explain(s, i + 1, len(shots)))
    else:
        mode = "规则版（配置 vision 模型后重跑可得 AI 深度讲解）"
        print("[提示] 无可用 vision 厂商（未启用/models.vision 未配/未配 key），生成规则版讲解",flush=True)
        explains = [rule_explain(s, i + 1, len(shots)) for i, s in enumerate(shots)]

    md = build_markdown(analysis, explains, mode)
    out = os.path.join(ad, "讲解.md")
    open(out, "w", encoding="utf-8").write(md)
    print(f"完成: {len(shots)} 镜 -> {out}",flush=True)
    print(f"OUTPUT:{out}",flush=True)

if __name__ == "__main__":
    main()
