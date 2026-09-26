# -*- coding: utf-8 -*-
"""剧情战略图 v2：场景平面图（layout DSL）为底 + 分镜逐镜动态推演

依赖链（用户定调）：做场景必须先有平面图（站位/道具/方位/细节）→ 战略图以平面图为底，
逐镜叠加：在场角色（staging 未移出）、场景陈设/出入口/点位、机位与视场扇形、
运动方向（N/E/S/W 罗盘语义）。不再把所有角色都丢进去——不在场=staging 移出=不画。

数据源：
  分镜.json    shots[].scene_ref/@scene → 定位该镜场景；staging=在场过滤；pos/look=机位；camera_move=运动
  素材/场景.json scenes[].layout{bounds,entry,furniture,markers,spawn} + compass（平面底图）
  --plan <plan.json>  plan v1 平面图作底图（room/props/zones/openings/走位轨迹；plan_adapt 转换坐标）。
                      镜头数据缺省时以 plan 补（相机 pos/look、演员站位），已有字段以分镜为准。
                      不带分镜时为平面图独立渲染模式（每相机一"镜"，纯底图浏览）。

产物: 推演/战略图_<分镜名>.html（自包含，零依赖）
用法: python strategy_map.py <分镜.json> [--out 路径] [--plan plan.json]
      python strategy_map.py --plan plan.json [--out 路径]   # 独立渲染 plan
"""
import sys, os, json, argparse
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def load_scenes(board_path):
    proj = os.path.dirname(os.path.dirname(board_path))
    p = os.path.join(proj, "素材", "场景.json")
    if not os.path.isfile(p):
        return {}
    try:
        rows = json.load(open(p, encoding="utf-8")).get("scenes") or []
        return {str(r.get("id")): r for r in rows if isinstance(r, dict) and r.get("id")}
    except Exception:
        return {}


def shot_scene_id(shot, scenes):
    for k in ("scene_ref", "scene_asset", "location_id"):
        v = str(shot.get(k) or "")
        if v.startswith("@scene:"):
            v = v[7:]
        if v and v in scenes:
            return v
    nm = str(shot.get("location") or "")
    if nm:
        for sid, sc in scenes.items():
            if str(sc.get("name") or "") == nm:
                return sid
    txt = str(shot.get("action") or "") + str(shot.get("prompt") or "")
    for sid in scenes:
        if "@scene:" + sid in txt:
            return sid
    return next(iter(scenes), None)


HTML = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>战略图 - __TITLE__</title>
<style>
  :root { color-scheme: dark; }
  body { margin:0; background:#0b1020; color:#e2e8f0; font:14px/1.6 "Microsoft YaHei",sans-serif; }
  header { padding:10px 18px; border-bottom:1px solid #ffffff14; display:flex; gap:14px; align-items:center; flex-wrap:wrap; }
  h1 { font-size:16px; margin:0; letter-spacing:2px; }
  #stage { position:relative; }
  canvas { display:block; }
  .panel { padding:8px 18px 14px; display:flex; gap:12px; align-items:center; flex-wrap:wrap; }
  button { background:#1e293b; color:#e2e8f0; border:1px solid #ffffff22; border-radius:8px; padding:5px 14px; cursor:pointer; }
  button:hover { border-color:#38bdf8; }
  input[type=range] { flex:1; min-width:220px; }
  .info { min-height:64px; padding:10px 18px; border-top:1px solid #ffffff14; white-space:pre-wrap; }
  .tag { border-radius:6px; padding:1px 8px; font-size:12px; background:#ffffff14; }
  #legend { padding:6px 18px; font-size:11px; color:#94a3b8; display:flex; gap:14px; flex-wrap:wrap; }
</style></head><body>
<header><h1>战略图（场景平面 + 镜头推演）</h1><span class="tag" id="cnt"></span>
<span id="sceneName" class="tag" style="background:#34d39922;color:#6ee7b7"></span>__PLAN_NOTE__
<span style="color:#94a3b8;font-size:12px">底图=场景平面(layout) · 圆点=在场角色 · ▲=机位+视场 · 橙箭头=运动 · 虚线=走位轨迹</span></header>
<div id="stage"><canvas id="cv"></canvas></div>
<div id="legend"><span>▭ 大件陈设</span><span>━ 出入口(方位)</span><span>◆ 点位</span><span>N=北(远景)</span></div>
<div class="panel">
  <button onclick="step(-1)">◀ 上一镜</button>
  <input id="slider" type="range" min="0" value="0">
  <button onclick="step(1)">下一镜 ▶</button>
  <label style="font-size:12px;color:#94a3b8"><input id="trail" type="checkbox" checked> 全程走位轨迹</label>
  <span id="pos" style="font-size:12px;color:#94a3b8"></span>
</div>
<div class="info" id="info"></div>
<script>
const DATA = __DATA__;
const shots = DATA.shots, actors = DATA.actors, scenes = DATA.scenes;
const cv = document.getElementById('cv'), ctx = cv.getContext('2d');
const slider = document.getElementById('slider');
let idx = 0, showTrail = true;
slider.max = shots.length - 1;

function sceneOf(s) { return scenes[s._scene_id] || null; }

function worldBounds(s) {
  const sc = sceneOf(s) || {};
  const lay = sc.layout || {};
  const w = (lay.bounds && lay.bounds.w) || 14, d = (lay.bounds && lay.bounds.d) || 12;
  return { x0: -w / 2, x1: w / 2, z0: -d / 2, z1: d / 2 };
}

let VIEW = null;
function computeView() {
  let x0 = -8, x1 = 8, z0 = -8, z1 = 8;
  for (const s of shots) {
    const b = worldBounds(s);
    x0 = Math.min(x0, b.x0 - 1); x1 = Math.max(x1, b.x1 + 1);
    z0 = Math.min(z0, b.z0 - 1); z1 = Math.max(z1, b.z1 + 1);
    if (s.pos) { x0 = Math.min(x0, s.pos[0] - 1); x1 = Math.max(x1, s.pos[0] + 1); z0 = Math.min(z0, s.pos[2] - 1); z1 = Math.max(z1, s.pos[2] + 1); }
    for (const aid in actors) {
      const p = actorPos(s, aid); if (p.off) continue;
      x0 = Math.min(x0, p.x - 1); x1 = Math.max(x1, p.x + 1); z0 = Math.min(z0, p.z - 1); z1 = Math.max(z1, p.z + 1);
    }
  }
  VIEW = { x0, x1, z0, z1 };
}
function MX(x) { return (x - VIEW.x0) / (VIEW.x1 - VIEW.x0) * cv.width; }
function MZ(z) { return cv.height - (z - VIEW.z0) / (VIEW.z1 - VIEW.z0) * cv.height; }

function actorPos(s, aid) {
  const p = (s._actor_positions || {})[aid];
  return p ? {x:p[0], z:p[1], off:false} : {x:0, z:0, off:true};
}

function drawCompass(sc) {
  const cx = cv.width - 46, cy = 52, r = 24;
  ctx.strokeStyle = '#64748b'; ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.arc(cx, cy, r, 0, 7); ctx.stroke();
  ctx.fillStyle = '#e2e8f0'; ctx.font = 'bold 12px sans-serif'; ctx.textAlign = 'center';
  ctx.fillText('N', cx, cy - r + 13); ctx.fillText('S', cx, cy + r - 5);
  ctx.fillText('E', cx + r - 5, cy + 4); ctx.fillText('W', cx - r + 5, cy + 4);
  ctx.strokeStyle = '#f59e0b'; ctx.lineWidth = 2;
  ctx.beginPath(); ctx.moveTo(cx, cy + 8); ctx.lineTo(cx, cy - 8);
  ctx.lineTo(cx - 3.5, cy - 3); ctx.moveTo(cx, cy - 8); ctx.lineTo(cx + 3.5, cy - 3); ctx.stroke();
  const comp = (sc || {}).compass || {};
  if (comp.N) { ctx.fillStyle = '#94a3b8'; ctx.font = '10px sans-serif'; ctx.textAlign = 'left';
    ctx.fillText('北: ' + String(comp.N).slice(0, 14), cx - r - 4, cy - r - 4); }
}

function dirWords(text) {
  const m = String(text || '');
  const out = [];
  if (/向东|右移|画右/.test(m)) out.push('东');
  if (/向西|左移|画左/.test(m)) out.push('西');
  if (/向北|前进|纵深/.test(m)) out.push('北');
  if (/向南|后退|退向/.test(m)) out.push('南');
  return out;
}

function drawScenePlane(s) {
  const sc = sceneOf(s);
  const lay = (sc || {}).layout || {};
  const b = worldBounds(s);
  ctx.fillStyle = (sc && sc.interior === false) ? '#2a2416' : '#151a26';
  ctx.fillRect(MX(b.x0), MZ(b.z1), MX(b.x1) - MX(b.x0), MZ(b.z0) - MZ(b.z1));
  ctx.strokeStyle = '#ffffff18'; ctx.lineWidth = 1;
  for (let gx = Math.ceil(b.x0); gx <= b.x1; gx++) { ctx.beginPath(); ctx.moveTo(MX(gx), MZ(b.z1)); ctx.lineTo(MX(gx), MZ(b.z0)); ctx.stroke(); }
  for (let gz = Math.ceil(b.z0); gz <= b.z1; gz++) { ctx.beginPath(); ctx.moveTo(MX(b.x0), MZ(gz)); ctx.lineTo(MX(b.x1), MZ(gz)); ctx.stroke(); }
  ctx.strokeStyle = '#6ee7b766'; ctx.lineWidth = 2;
  ctx.strokeRect(MX(b.x0), MZ(b.z1), MX(b.x1) - MX(b.x0), MZ(b.z0) - MZ(b.z1));
  if (sc) { ctx.fillStyle = '#6ee7b7'; ctx.font = 'bold 13px "Microsoft YaHei"'; ctx.textAlign = 'left';
    ctx.fillText(sc.name || s._scene_id, MX(b.x0) + 8, MZ(b.z1) + 18); }
  ctx.font = '10px sans-serif';
  for (const e of lay.entry || []) {
    const p = e.pos || [0, (e.at === 'N' ? b.z1 : e.at === 'S' ? b.z0 : 0)];
    const X = MX(p[0]), Y = MZ(p[1]);
    ctx.strokeStyle = '#38bdf8'; ctx.lineWidth = 3;
    ctx.beginPath(); ctx.moveTo(X - 10, Y); ctx.lineTo(X + 10, Y); ctx.stroke();
    ctx.fillStyle = '#38bdf8'; ctx.textAlign = 'center';
    ctx.fillText((e.kind || '口') + (e.at ? '·' + e.at : ''), X, Y - 6);
  }
  for (const f of lay.furniture || []) {
    const X = MX((f.pos || [0, 0])[0]), Y = MZ((f.pos || [0, 0])[1]);
    const w = ((f.size && f.size[0]) || 1.5) / (VIEW.x1 - VIEW.x0) * cv.width;
    const h = ((f.size && f.size[1]) || 1) / (VIEW.z1 - VIEW.z0) * cv.height;
    ctx.fillStyle = '#334155cc';
    ctx.strokeStyle = '#94a3b8';
    if (f.shape === 'circle') {
      ctx.beginPath(); ctx.arc(X, Y, w / 2, 0, 7); ctx.fill(); ctx.stroke();
    } else {
      ctx.fillRect(X - w / 2, Y - h / 2, w, h);
      ctx.strokeRect(X - w / 2, Y - h / 2, w, h);
    }
    if (f.label || f.kind) { ctx.fillStyle = '#cbd5e1'; ctx.textAlign = 'center'; ctx.fillText(f.label || f.kind, X, Y + 3); }
  }
  // plan v1 扩展层：墙多边形 / 命名区域（虚线框）/ plan 走位轨迹（虚线+落点）
  if ((lay.walls || []).length >= 3) {
    ctx.strokeStyle = '#94a3b8'; ctx.lineWidth = 3; ctx.beginPath();
    lay.walls.forEach((p, i) => { const X = MX(p[0]), Y = MZ(p[1]); if (i) ctx.lineTo(X, Y); else ctx.moveTo(X, Y); });
    ctx.closePath(); ctx.stroke();
  }
  for (const z of lay.zones || []) {
    const r = z.rect || [0, 0, 0, 0];
    ctx.setLineDash([6, 4]); ctx.strokeStyle = '#f472b688'; ctx.lineWidth = 1.5;
    ctx.strokeRect(MX(r[0]), MZ(r[3]), MX(r[2]) - MX(r[0]), MZ(r[1]) - MZ(r[3]));
    ctx.setLineDash([]);
    if (z.label) { ctx.fillStyle = '#f9a8d4'; ctx.font = '11px sans-serif'; ctx.textAlign = 'left';
      ctx.fillText(z.label, MX(r[0]) + 4, MZ(r[3]) + 14); }
  }
  for (const pt of lay.paths || []) {
    ctx.setLineDash([5, 5]); ctx.strokeStyle = '#34d39999'; ctx.lineWidth = 2; ctx.beginPath();
    (pt.points || []).forEach((p, i) => { const X = MX(p[0]), Y = MZ(p[1]); if (i) ctx.lineTo(X, Y); else ctx.moveTo(X, Y); });
    ctx.stroke(); ctx.setLineDash([]);
    const lp = (pt.points || []).slice(-1)[0];
    if (lp) { ctx.fillStyle = '#34d399'; ctx.beginPath(); ctx.arc(MX(lp[0]), MZ(lp[1]), 4, 0, 7); ctx.fill(); }
  }
  for (const m of lay.markers || []) {
    const X = MX((m.pos || [0, 0])[0]), Y = MZ((m.pos || [0, 0])[1]);
    ctx.fillStyle = '#a78bfa';
    ctx.beginPath(); ctx.moveTo(X, Y - 7); ctx.lineTo(X + 7, Y); ctx.lineTo(X, Y + 7); ctx.lineTo(X - 7, Y); ctx.closePath(); ctx.fill();
    if (m.label) { ctx.fillStyle = '#c4b5fd'; ctx.textAlign = 'center'; ctx.fillText(m.label, X, Y - 10); }
  }
}

function draw() {
  cv.width = document.getElementById('stage').clientWidth; cv.height = Math.max(460, cv.width * 0.64);
  computeView();
  const s = shots[idx];
  ctx.fillStyle = '#0b1020'; ctx.fillRect(0, 0, cv.width, cv.height);
  drawScenePlane(s);
  if (showTrail) {
    ctx.setLineDash([4, 5]);
    for (const aid in actors) {
      ctx.strokeStyle = `rgba(${(actors[aid].shirt || [180, 180, 180]).join(',')},0.3)`;
      ctx.beginPath(); let first = true;
      for (const sh of shots) {
        if (sh._scene_id !== s._scene_id) continue;
        const p = actorPos(sh, aid); if (p.off) { first = true; continue; }
        const X = MX(p.x), Y = MZ(p.z);
        if (first) { ctx.moveTo(X, Y); first = false; } else ctx.lineTo(X, Y);
      }
      ctx.stroke();
    }
    ctx.setLineDash([]);
  }
  if (s.pos && s.look) {
    const cx = s.pos[0], cz = s.pos[2];
    const ang = Math.atan2(s.look[0] - cx, s.look[2] - cz);
    const fov = (s.fov || 48) * Math.PI / 180, R = 6;
    ctx.fillStyle = 'rgba(56,189,248,0.13)';
    ctx.beginPath(); ctx.moveTo(MX(cx), MZ(cz));
    ctx.lineTo(MX(cx + R * Math.sin(ang - fov / 2)), MZ(cz + R * Math.cos(ang - fov / 2)));
    ctx.lineTo(MX(cx + R * Math.sin(ang + fov / 2)), MZ(cz + R * Math.cos(ang + fov / 2)));
    ctx.closePath(); ctx.fill();
    ctx.strokeStyle = '#0ea5e9'; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(MX(cx), MZ(cz)); ctx.lineTo(MX(s.look[0]), MZ(s.look[2])); ctx.stroke();
    const X = MX(cx), Y = MZ(cz);
    ctx.fillStyle = '#0ea5e9';
    ctx.beginPath(); ctx.moveTo(X, Y - 9); ctx.lineTo(X - 7, Y + 6); ctx.lineTo(X + 7, Y + 6); ctx.closePath(); ctx.fill();
    ctx.fillStyle = '#e0f2fe'; ctx.font = 'bold 13px sans-serif'; ctx.textAlign = 'left';
    ctx.fillText(s.id, X + 10, Y - 8);
    const mv = s.camera_move || '';
    const dw = dirWords((s.action || '') + ' ' + (s.move || '')).join('/');
    const arr = (tx, tz, label) => {
      ctx.strokeStyle = '#f97316'; ctx.lineWidth = 3;
      ctx.beginPath(); ctx.moveTo(MX(cx), MZ(cz)); ctx.lineTo(MX(tx), MZ(tz)); ctx.stroke();
      ctx.fillStyle = '#f97316'; ctx.beginPath(); ctx.arc(MX(tx), MZ(tz), 4, 0, 7); ctx.fill();
      if (label) { ctx.font = 'bold 11px sans-serif'; ctx.textAlign = 'left'; ctx.fillText(label, MX(tx) + 6, MZ(tz)); }
    };
    if (mv === '推') arr(cx + 2 * Math.sin(ang), cz + 2 * Math.cos(ang), '推' + (dw ? '·' + dw : ''));
    if (mv === '拉') { const sx = cx + 2 * Math.sin(ang), sz = cz + 2 * Math.cos(ang);
      ctx.strokeStyle = '#f97316'; ctx.lineWidth = 3; ctx.beginPath(); ctx.moveTo(MX(sx), MZ(sz)); ctx.lineTo(MX(cx), MZ(cz)); ctx.stroke(); }
    if (['移', '轨道', '斯坦尼康', '手持'].includes(mv)) { const per = ang + Math.PI / 2;
      arr(cx + 2 * Math.sin(per), cz + 2 * Math.cos(per), '移' + (dw ? '·' + dw : '')); }
    if (mv === '摇') { const t = ang + Math.PI / 5; arr(cx + 2.6 * Math.sin(t), cz + 2.6 * Math.cos(t), '摇'); }
    if (mv === '环绕') { const t = ang + Math.PI * 0.7; arr(cx + 2.6 * Math.sin(t), cz + 2.6 * Math.cos(t), '环绕'); }
    if (dw && !['推', '拉', '摇', '环绕', '移', '轨道', '斯坦尼康', '手持'].includes(mv)) {
      const dirMap = { '东': [1, 0], '西': [-1, 0], '北': [0, 1], '南': [0, -1] };
      const dv = dirMap[dw[0]] || [1, 0];
      arr(s.look[0] + dv[0] * 1.5, s.look[2] + dv[1] * 1.5, dw);
    }
  }
  ctx.font = '12px "Microsoft YaHei"';
  const speakers = new Set((s.lines || []).map(l => l.speaker));
  for (const aid in actors) {
    const p = actorPos(s, aid); if (p.off) continue;
    const X = MX(p.x), Y = MZ(p.z);
    ctx.beginPath(); ctx.arc(X, Y, 8, 0, 7);
    ctx.fillStyle = `rgb(${(actors[aid].shirt || [180, 180, 180]).join(',')})`; ctx.fill();
    ctx.strokeStyle = '#0b1020'; ctx.lineWidth = 2; ctx.stroke();
    if (speakers.has(aid)) { ctx.strokeStyle = '#fbbf24'; ctx.lineWidth = 2.5; ctx.beginPath(); ctx.arc(X, Y, 12, 0, 7); ctx.stroke(); }
    ctx.fillStyle = speakers.has(aid) ? '#fde68a' : '#f1f5f9'; ctx.textAlign = 'left';
    ctx.fillText(actors[aid].name || aid, X + 11, Y + 4);
  }
  drawCompass(sceneOf(s));
  document.getElementById('sceneName').textContent = ((sceneOf(s) || {}).name || '未绑定场景') + (s._scene_id ? ' · ' + s._scene_id : '');
  const lines = (s.lines || []).map(l => `【${(actors[l.speaker] || { name: l.speaker }).name}】${l.line}`).join('\\n');
  const dw2 = dirWords((s.action || '') + ' ' + (s.move || '')).join('/');
  document.getElementById('info').textContent =
    `${s.id} · ${s.dur}s · ${s.move || ''} · 方向${dw2 ? ': ' + dw2 : '(未标注)'}\\n${s.action || ''}${lines ? '\\n' + lines : ''}`;
  document.getElementById('pos').textContent = `第 ${idx + 1} / ${shots.length} 镜`;
  slider.value = idx;
}
function step(d) { idx = Math.max(0, Math.min(shots.length - 1, idx + d)); draw(); }
slider.oninput = () => { idx = +slider.value; draw(); };
document.getElementById('trail').onchange = e => { showTrail = e.target.checked; draw(); };
window.onresize = draw;
window.onmessage = (ev) => { if (ev.data && ev.data.type === 'goto' && Number.isInteger(ev.data.idx)) { idx = Math.max(0, Math.min(shots.length - 1, ev.data.idx)); draw(); } };
document.getElementById('cnt').textContent = shots.length + ' 镜';
draw();
</script></body></html>"""


def render_strategy_html(data):
    """把数据填进自包含模板。

    产物经 /media 以 text/html 同源回吐，且在推演页 iframe 里自动加载：
    json.dumps 不转义 `<`，任何角色名/台词/场景名里出现 `</script>` 就能跳出自定义脚本，
    以管理员同源身份调全部 /api（HttpOnly cookie 拦不住）。故对 < > 做 \\u 转义，
    title 另做 HTML 文本转义防 </title> 跳出。先填 DATA 再填 TITLE，避免互相污染。
    """
    payload = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e")
    title = str(data.get("title") or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # 底图来源标记：零匹配回退不能只在任务日志里响一声——产物本身（/media 直开、推演页 iframe）
    # 也要看得见，否则操作者拿着一张"别的场景的平面"当本场景用。文本同 title 一样转义。
    note = str(data.get("plan_note") or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    warn = bool(data.get("plan_note_warn"))
    badge = (f'<span class="tag" style="background:{"#f59e0b22" if warn else "#38bdf822"};'
             f'color:{"#fbbf24" if warn else "#7dd3fc"}">{note}</span>') if note else ""
    return (HTML.replace("__DATA__", payload).replace("__TITLE__", title)
                .replace("__PLAN_NOTE__", badge))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("storyboard", nargs="?", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--plan", default=None, help="plan v1 平面图 JSON（作底图；镜头缺省字段以 plan 补）")
    a = ap.parse_args()
    plan = None
    if a.plan:
        from plan_adapt import load_plan
        plan = load_plan(a.plan)
        if plan is None:
            print(f"[错误] plan 不存在或解析失败: {a.plan}"); sys.exit(1)
    if not a.storyboard and plan is None:
        print("[错误] 需要分镜.json 或 --plan 之一"); sys.exit(1)

    if a.storyboard:
        jp = os.path.abspath(a.storyboard)
        cfg = json.load(open(jp, encoding="utf-8"))
        shots = cfg.get("shots") or []
        if not shots:
            print("[错误] 无 shots"); sys.exit(1)
        scenes = load_scenes(jp)
        actors = dict(cfg.get("actors") or {})
        from shot_presence import actor_positions
        if plan is not None:
            # plan 底图模式：底图只认 plan（room/props/zones），镜头已有字段以分镜为准
            from plan_adapt import plan_scene, plan_cameras_world, plan_actor_positions, plan_actors_map
            scenes = {"_plan": plan_scene(plan)}
            for aid, info in plan_actors_map(plan).items():
                actors.setdefault(aid, info)
            cams = plan_cameras_world(plan)
            plan_pos = plan_actor_positions(plan)
        for i, s in enumerate(shots):
            s["_scene_id"] = "_plan" if plan is not None else shot_scene_id(s, scenes)
            s["_actor_positions"] = actor_positions(s, cfg.get('actors') or {}, scenes.get(s['_scene_id']))
            if plan is not None:
                if not s["_actor_positions"] and plan_pos:
                    s["_actor_positions"] = dict(plan_pos)
                if not s.get("pos") and cams:
                    cam = cams[i % len(cams)]
                    s["pos"], s["look"] = cam["pos"], cam["look"]
                    s.setdefault("fov", cam["fov"])
        title = cfg.get("title") or os.path.splitext(os.path.basename(jp))[0]
        out = a.out or os.path.join(os.path.dirname(os.path.dirname(jp)),
                                    "推演", f"战略图_{os.path.splitext(os.path.basename(jp))[0]}.html")
    else:
        # 独立渲染 plan：每相机一"镜"（无相机则一个总览镜）
        from plan_adapt import plan_scene, plan_cameras_world, plan_actor_positions, plan_actors_map
        scenes = {"_plan": plan_scene(plan)}
        actors = plan_actors_map(plan)
        cams = plan_cameras_world(plan)
        plan_pos = plan_actor_positions(plan)
        shots = []
        for i, cam in enumerate(cams or [{"id": "总览", "pos": None, "look": None, "fov": 50}]):
            shots.append({"id": str(cam.get("id") or f"cam{i+1}"), "dur": 5,
                          "pos": cam.get("pos"), "look": cam.get("look"),
                          "fov": cam.get("fov") or 50, "move": "plan", "action": "",
                          "_scene_id": "_plan", "_actor_positions": dict(plan_pos)})
        title = f"平面图_{plan.get('name') or 'plan'}"
        out = a.out or os.path.join(os.path.dirname(os.path.abspath(a.plan)), f"战略图_{title}.html")

    # 底图来源标记（零匹配回退必须在产物上看得见，不能只留任务日志里那句 [警告]）
    plan_note, plan_warn = "", False
    if plan is not None and a.storyboard:
        from plan_adapt import shot_scene_ref
        name = str(plan.get("name") or os.path.basename(str(a.plan)))
        ref = str(plan.get("scene_ref") or "")
        hit = sum(1 for s in shots if ref and shot_scene_ref(s) == ref)
        if not ref:
            plan_note, plan_warn = f"底图「{name}」未关联场景资产（无 scene_ref），空间一致性未经校验", True
        elif not hit:
            plan_note, plan_warn = f"底图「{name}」与 {len(shots)} 镜 scene_ref 零匹配（回退用图），空间一致性未经校验", True
        else:
            plan_note = f"底图「{name}」匹配 {hit}/{len(shots)} 镜"

    data = {"title": title, "actors": actors, "scenes": scenes, "shots": shots,
            "plan_note": plan_note, "plan_note_warn": plan_warn}
    os.makedirs(os.path.dirname(out), exist_ok=True)
    html = render_strategy_html(data)
    from versions import snapshot
    snapshot(out)
    open(out, "w", encoding="utf-8").write(html)
    if plan is not None:
        print(f"[完成] 战略图（plan 底图）{len(shots)} 镜 -> {out}")
    else:
        bound = sum(1 for s in shots if s.get("_scene_id"))
        lay_n = sum(1 for sc in scenes.values() if isinstance((sc.get("layout") or {}).get("bounds"), dict))
        print(f"[完成] 战略图 {len(shots)} 镜（场景绑定 {bound}/{len(shots)}；{lay_n}/{len(scenes)} 场景带 layout 底图）-> {out}")
        if scenes and lay_n < len(scenes):
            print("[警告] 部分场景无 layout（旧版 geometry 文本）——底图退化为空白场地；重提炼场景可补 layout DSL")
        if not scenes:
            print("[警告] 项目无 素材/场景.json——战略图退化为无底图模式")
    print("OUTPUT:" + out)


if __name__ == "__main__":
    main()
