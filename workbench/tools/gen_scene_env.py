# -*- coding: utf-8 -*-
"""LLM 生成场景陈设 DSL（env），写回分镜 JSON 供 blender_previs 拼装

方法论：白模=空间符号，场景陈设只求"尺寸位置正确"的灰模几何（方法论见
previs_system/docs/白模方法论-空间符号与三分法.md）。本工具把每镜场景文本
（prompt_cn/action）交给 LLM，产出严格 DSL：

  env = {"room":  {"boxes":[[cx,cy,cz,sx,sy,sz,r,g,b],...], "cyls":[[cx,cy,cz,r,h,r,g,b],...],
                   "spheres":[[cx,cy,cz,r,r,g,b],...]},
         "field": {...}}

坐标（米）：x 左右、y 纵深(+y 远离镜头)、z 高(地面=0)；cx/cy/cz 为中心，s* 为全长。
消费方 blender_previs dialogue_template：room 区建在原点，field 区平移 x=FX。

已有元素（LLM 不要重复输出）：room=地板/墙/天花板/桌案(set.table)；field=地面/
两翼旗阵(沿 z=±14)/远处军阵块(x≈±13)/乱石。角色净空 x∈[-4,4], y∈[-2,5]。

用法: python gen_scene_env.py <分镜.json> [--vendor 厂商id] [--max-per 24]
退出码 0=成功 1=失败；stdout 末行 OUTPUT:<分镜路径>
"""
import sys, os, json, re, argparse
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from llm_openai import VendorClient, load_vendors, VendorError

PROMPT_SYS = (
    "你是 3D 预演场景师。把场景文字描述转成极简灰模几何清单（空间符号：只要尺寸位置正确的"
    "长方体/圆柱/球，不做细节）。只输出 JSON，不要解释、不要 markdown 代码块。"
)
try:
    import prompt_modules as _PM
    PROMPT_SYS = _PM.sys_for("scene_env", PROMPT_SYS)
except Exception:
    pass
PROMPT_USER = """把下面的场景描述转成几何清单 JSON，schema：
{{"boxes": [[cx,cy,cz,sx,sy,sz,r,g,b], ...], "cyls": [[cx,cy,cz,r,h,r,g,b], ...], "spheres": [[cx,cy,cz,r,r,g,b], ...]}}
坐标系（单位米）：x 左右，y 纵深（+y 远离镜头），z 高度（地面=0）；c* 是中心坐标，s* 是全长；r,g,b 取 0~1。
硬性规则：
1. 每个场景最多 {max_per} 个几何体；宁少勿多，只放描述里明确出现或该场景必然存在的陈设。
2. 角色活动净空 x∈[-4,4]、y∈[-2,5] 内不放任何几何体（角色从这里走动）。
3. 已存在、禁止重复输出——{known}。
4. 大件为主（>0.5m），位置贴墙/贴边/远处；颜色用低饱和灰调近似材质（r,g,b 0.2~0.7）。
场景描述（多镜文本，取并集理解）：
{desc}

输出格式：{{"room": {{...}}, "field": {{...}}}}——只输出文本里出现的场景键，键值 schema 同上。"""


def pick_vendor(vendor_id):
    vs = [v for v in load_vendors() if v.get("enabled") and (v.get("models") or {}).get("text")]
    if vendor_id:
        vs = [v for v in vs if v["id"] == vendor_id]
    # 有 key 的排前（没 key 大概率 401，但本地网关可能免 key，保留为候选）
    vs.sort(key=lambda v: not v.get("api_key"))
    if not vs:
        raise VendorError("没有已启用且配置了 text 模型的厂商（检查 providers.json / ⑦ 环境页）")
    return vs[0]["id"]


def parse_json(txt):
    m = re.search(r"\{.*\}", txt, re.S)
    if not m:
        raise ValueError("LLM 输出里没有 JSON 对象")
    return json.loads(m.group(0))


def clamp_env(env, max_per):
    """数值兜底：全转 float、尺寸夹到 [0.05, 40]、颜色夹到 [0,1]、限数量。"""
    def f3(v, lo, hi):
        return [max(lo, min(hi, float(x))) for x in v]
    out = {}
    for zone in ("room", "field"):
        z = env.get(zone)
        if not isinstance(z, dict):
            continue
        oz = {}
        boxes = [f3(b[:6], -40, 40) + f3(b[6:9] if len(b) >= 9 else [0.45, 0.45, 0.47], 0.0, 1.0)
                 for b in (z.get("boxes") or []) if isinstance(b, (list, tuple)) and len(b) >= 6][:max_per]
        oz["boxes"] = [b[:3] + [max(0.05, abs(x)) for x in b[3:6]] + b[6:9] for b in boxes]
        cyls = [f3(c[:5], -40, 40) + f3(c[5:8] if len(c) >= 8 else [0.45, 0.45, 0.47], 0.0, 1.0)
                for c in (z.get("cyls") or []) if isinstance(c, (list, tuple)) and len(c) >= 5][:max_per]
        oz["cyls"] = [c[:3] + [max(0.03, abs(c[3])), max(0.05, abs(c[4]))] + c[5:8] for c in cyls]
        sphs = [f3(s[:4], -40, 40) + f3(s[4:7] if len(s) >= 7 else [0.45, 0.45, 0.47], 0.0, 1.0)
                for s in (z.get("spheres") or []) if isinstance(s, (list, tuple)) and len(s) >= 4][:max_per]
        oz["spheres"] = [s[:3] + [max(0.03, abs(s[3]))] + s[4:7] for s in sphs]
        if oz["boxes"] or oz["cyls"] or oz["spheres"]:
            out[zone] = oz
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("storyboard")
    ap.add_argument("--vendor", default=None)
    ap.add_argument("--max-per", type=int, default=24)
    a = ap.parse_args()
    jp = os.path.abspath(a.storyboard)
    if not os.path.isfile(jp):
        print(f"[错误] 分镜不存在: {jp}"); sys.exit(1)
    cfg = json.load(open(jp, encoding="utf-8"))
    shots = cfg.get("shots") or []
    zones = {}
    for zone, keys in (("room", ("room",)), ("field", ("field",))):
        txts = [f"{s.get('action','')} {s.get('prompt_cn','')}" for s in shots
                if (s.get("scene") or "room") in keys]
        txts = [t.strip() for t in txts if t.strip()]
        if txts:
            zones[zone] = "；".join(dict.fromkeys(txts))[:3000]
    if not zones:
        print("[错误] 分镜没有 scene 文本可生成"); sys.exit(1)
    vid = pick_vendor(a.vendor)
    cli = VendorClient(vid)
    print(f"[信息] LLM 厂商: {vid} / {cli.model('text')}")
    KNOWN = {"room": "地板、四周墙、天花板、中央桌案（set.table）",
             "field": "地面、两翼旗阵（z≈±14 一带）、远处军阵块（x≈±13）、散落的乱石"}
    known_txt = "；".join(KNOWN[k] for k in zones)
    user = PROMPT_USER.format(max_per=a.max_per, known=known_txt,
                              desc=json.dumps(zones, ensure_ascii=False))
    txt = cli.chat([{"role": "system", "content": PROMPT_SYS},
                    {"role": "user", "content": user}],
                   kind="text", max_tokens=3000, temperature=0.4, timeout=420)
    env = clamp_env(parse_json(txt), a.max_per)
    if not env:
        print("[错误] LLM 返回为空或不可解析:\n" + txt[:500]); sys.exit(1)
    bak = jp + ".bak_env"
    if os.path.isfile(jp) and not os.path.isfile(bak):
        import shutil; shutil.copyfile(jp, bak)
    cfg["env"] = env
    from versions import snapshot
    snapshot(jp)
    json.dump(cfg, open(jp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for zone, z in env.items():
        print(f"[完成] env.{zone}: boxes {len(z['boxes'])} / cyls {len(z['cyls'])} / spheres {len(z['spheres'])}")
    print("[提示] 重新到 3D 白模页生成构建脚本即可带上陈设；备份在 " + os.path.basename(bak))
    print("OUTPUT:" + jp)


if __name__ == "__main__":
    main()
