<script setup lang="ts">
import { useBoardSelection } from '../utils/useBoardSelection'
// -*- coding: utf-8 -*-
/** 平面推演页（⑥）：本页产物全部服务 AI 视频模型——平面图=布局参考(注入V请求)、走位战略图=运镜核对(人工)、拍摄资料包=逐镜资料固化。
 *  白模(2D)/3D 是辅助系统的独立链路：辅助·白模 出预演帧、辅助·Blender 出 3D 预演，产物同步本页素材；本页只放转跳链接。左=V 列表（与⑦同源 video_units；无 V 老分镜回退场景列表），右=选中场景详情 */
import { ref, computed, watch } from 'vue'
import {
  fetchWhiteBoard, fetchProjectFile, creationAssemble, buildStrategy,
  mediaUrl, generatePlan, fetchPlanList, buildPlanCanvas, fetchScriptData,
  type WhiteBoard, type PlanSummary
} from '../api'
import { app, projectFiles, loadBasics, toast } from '../stores/app'
import { trackJob } from '../stores/jobs'
import StyledSelect from '../components/StyledSelect.vue'
import OverlayViewer from '../components/OverlayViewer.vue'

interface Shot { id: string; dur?: number; move?: string; scene?: string; scene_ref?: string; action?: string; prompt?: string; lines?: { speaker: string; line: string }[]; staging?: Record<string, unknown> }
interface PkgShot { id: string; dur?: number; move?: string; action?: string; script?: { speaker?: string; text?: string }[]; diagram?: string | null; white_ref?: string | null; prompt?: string }
interface PkgPlan { name: string; scene_ref?: string | null; counts?: { props?: number; actors?: number; paths?: number }; validate_ok?: boolean | null; canvas_html?: string | null }
interface Pkg { storyboard: string; title?: string; strategy_map?: string | null; shots: PkgShot[]; materials?: string[]; plans?: PkgPlan[]
  /** 战略图底图来源：mode=fallback 表示这张底图与分镜的 scene_ref 零匹配（回退用最新一张），空间一致性未经校验 */
  strategy_plan?: { mode?: string; name?: string; score?: number; total?: number } | null }

const boards = computed(() => (projectFiles('分镜') || []).filter((f) => f.endsWith('.json') && f.startsWith('剧本_')))
const board = ref('')
const shots = ref<Shot[]>([])
const pkg = ref<Pkg | null>(null)
const loading = ref(false)
const busy = ref('')
const tab = ref<'strategy' | 'shots' | 'aiplan'>('aiplan')
const TABS = computed(() => [
  { k: 'aiplan', label: '平面图' },                       // 主入口：AI 平面图（plan v1）——布局参考，注入 V 视频请求
  { k: 'strategy', label: '走位战略图' },                 // 运镜/走位动态核对（人工）
  { k: 'shots', label: '拍摄资料包 (' + (pkg.value?.shots?.length ?? 0) + ') · 脚本/素材' }
] as const)
const currentShot = ref(0)
const overlay = ref<{ visible: boolean; src: string; kind: 'image' | 'html'; title: string }>({ visible: false, src: '', kind: 'image', title: '' })

const baseName = computed(() => board.value.replace(/\.json$/, ''))
const strategyFile = computed(() => (baseName.value ? `战略图_${baseName.value}.html` : ''))
/** 生成一次自增一次：/media 无缓存校验头，同名产物重做后需换 URL 才会真的重载 */
const strategyNonce = ref(0)
// iframe 直连 /media，产物不存在时只会拿到 404 纯文本（看着就是白屏），所以先查项目树里的产物清单
const strategyUrl = computed(() =>
  strategyFile.value && projectFiles('推演').includes(strategyFile.value)
    ? `${mediaUrl(`projects/${app.current}/推演/${strategyFile.value}`)}&v=${strategyNonce.value}` : '')
/** 创作包 manifest 记的底图来源：零匹配回退时在本页顶部也标一次（产物内那条只在 iframe 里）。 */
const planFallback = computed(() => (pkg.value?.strategy_plan?.mode === 'fallback' ? pkg.value.strategy_plan : null))

let loadSeq = 0
async function load() {
  const request = ++loadSeq, project = app.current, name = board.value
  shots.value = []; pkg.value = null
  if (!project || !name) return
  loading.value = true
  try {
    const b: WhiteBoard = await fetchWhiteBoard(project, name)
    if (request !== loadSeq) return
    shots.value = (b.shots || []) as unknown as Shot[]
    units.value = ((b as unknown as { video_units?: VUnit[] }).video_units || []) as VUnit[]
    const rel = `创作包_${name.replace(/\.json$/, '')}/manifest.json`
    if (projectFiles('推演').includes(rel)) {
      const result = await fetchProjectFile<Pkg>(project, `推演/${rel}`)
      if (request === loadSeq) pkg.value = result
    }
  } catch (e) { if (request === loadSeq) toast(e instanceof Error ? e.message : '推演数据加载失败', 'err') }
  finally { if (request === loadSeq) loading.value = false }
}

watch([() => app.current, board], load, { immediate: true })

/** 战略图 iframe 内部跳镜：postMessage 协议（strategy_map v2 支持 {type:'goto',idx}） */
function gotoShot(i: number) {
  currentShot.value = i
  const frame = document.getElementById('strategyFrame') as HTMLIFrameElement | null
  frame?.contentWindow?.postMessage({ type: 'goto', idx: i }, '*')
  if (tab.value !== 'strategy') tab.value = 'strategy'
}

async function run(label: string, fn: () => Promise<{ id?: number; err?: string }>, done?: () => void) {
  busy.value = label
  try {
    const r = await fn()
    if (!r.id) throw new Error(r.err || '任务未启动')
    const j = await trackJob(r.id, label)
    if (j.success) { toast(`${label}完成`, 'ok'); await done?.() } else throw new Error(j.err || `${label}失败`)
  } catch (e) {
    toast(e instanceof Error ? e.message : `${label}失败`, 'err')
  } finally { busy.value = '' }
}
const doAssemble = () => run('拍摄资料包', () => creationAssemble(app.current!, board.value), afterBuild)
const doStrategy = () => run('生成战略图', () => buildStrategy(app.current!, board.value), afterBuild)

/** 产物落盘后：先刷新项目树（战略图存在性闸读它），再重载本页数据。 */
async function afterBuild() {
  strategyNonce.value++
  await loadBasics()
  await load()
}

/* ── AI 平面图（plan v1 场景级布局：左=场景列表，右=选中场景详情，S 降级为场景内标签） ── */
const plans = ref<PlanSummary[]>([])
const planExtra = ref('')
const zoneOptions = ref<string[]>([])        // 场景资产 id（判断"有无场景资产"用）
const scenesMap = ref<Record<string, string>>({})   // 场景 id → 场景名（显示用）

/** 平面图显示名：绑了场景优先显示场景名，回退 scene_ref / 图名 */
function planLabel(p: { name: string; scene_ref?: string | null }): string {
  const ref = p.scene_ref || ''
  return (ref && scenesMap.value[ref]) || ref || p.name
}

/** shot 的场景资产 id：scene_ref='@scene:loc_x' → 'loc_x'；无 → ''（room/field 不当场景） */
function sceneRefOf(s: Shot): string {
  const v = String(s.scene_ref || '')
  return v.startsWith('@scene:') ? v.slice('@scene:'.length) : ''
}

interface SceneGroup {
  key: string                     // scene_ref 或 '__unlinked'（未关联组）
  ref: string                     // '' = 未关联场景资产
  name: string
  shots: { s: Shot; i: number }[] // i = 全片镜号（gotoShot 用）
  plan: PlanSummary | null        // scene_ref 匹配到的最新一张 plan
}
const UNLINKED = '__unlinked'
const selScene = ref('')

/** 该场景 scene_ref 匹配到的最新一张 plan（list 不按 mtime 排序，自己取 max） */
function newestPlanFor(ref: string): PlanSummary | null {
  let best: PlanSummary | null = null
  for (const p of plans.value) {
    if (p.scene_ref !== ref) continue
    if (!best || (p.mtime || 0) > (best.mtime || 0)) best = p
  }
  return best
}

/** 当前分镜 shots 按 scene_ref 聚合（首现顺序，未关联组排末尾、有才出现） */
const sceneGroups = computed<SceneGroup[]>(() => {
  const order: string[] = []
  const byRef = new Map<string, { s: Shot; i: number }[]>()
  shots.value.forEach((s, i) => {
    const ref = sceneRefOf(s)
    if (!byRef.has(ref)) { byRef.set(ref, []); order.push(ref) }
    byRef.get(ref)!.push({ s, i })
  })
  order.sort((a, b) => (a === '' ? 1 : 0) - (b === '' ? 1 : 0))
  return order.map((ref) => ({
    key: ref || UNLINKED, ref,
    name: ref ? (scenesMap.value[ref] || ref || '未命名场景') : '未关联场景',
    shots: byRef.get(ref)!,
    plan: ref ? newestPlanFor(ref) : null
  }))
})

/** plans 里 scene_ref 不在本分镜场景（或没绑场景）的——左栏底部「其他平面图」 */
const otherPlans = computed(() => {
  const linked = new Set(sceneGroups.value.filter((g) => g.ref).map((g) => g.ref))
  return plans.value.filter((p) => !p.scene_ref || !linked.has(p.scene_ref))
})

const selGroup = computed(() => sceneGroups.value.find((g) => g.key === selScene.value) || null)

/* ── V 组织（与⑦创作生成同源同批：同一份分镜 JSON 的 video_units；无 V 的老分镜回退场景列表） ── */
interface VUnit { id: string; title?: string; shot_ids?: string[]; duration?: number }
const units = ref<VUnit[]>([])
const selUnit = ref('')

interface VGroup {
  id: string; label: string; title: string; duration: number
  shots: { s: Shot; i: number }[]
  scenes: { ref: string; name: string; plan: PlanSummary | null }[]   // 成员场景去重（按首现序）
}
const vGroups = computed<VGroup[]>(() => {
  const idx = new Map(shots.value.map((s, i) => [s.id, { s, i }]))
  return units.value.map((u, n) => {
    const members = ((u.shot_ids || []).map((id) => idx.get(id)).filter(Boolean)) as { s: Shot; i: number }[]
    const seen = new Set<string>()
    const scenes: VGroup['scenes'] = []
    for (const m of members) {
      const ref = sceneRefOf(m.s)
      if (!ref || seen.has(ref)) continue
      seen.add(ref)
      scenes.push({ ref, name: scenesMap.value[ref] || ref, plan: newestPlanFor(ref) })
    }
    return {
      id: u.id, label: `V${String(n + 1).padStart(2, '0')}`,
      title: u.title || scenes[0]?.name || '',
      duration: u.duration ?? members.reduce((a, m) => a + (m.s.dur || 0), 0),
      shots: members, scenes
    }
  })
})
const hasUnits = computed(() => vGroups.value.length > 0)
const selVGroup = computed(() => vGroups.value.find((v) => v.id === selUnit.value) || null)

/** V 行 plan 状态点：聚合成员场景——任一无图=灰 / 任一校验错=红 / 任一判官未过=琥珀 / 全过=绿 */
function vDot(g: VGroup): { cls: string; textCls: string; label: string; title: string } {
  if (!g.scenes.length) return { cls: 'bg-slate-500', textCls: 'text-slate-500', label: '未关联场景', title: '成员镜头未关联场景资产，无平面图可生成' }
  const parts: string[] = []
  let missing = false, bad = false, judged = false
  for (const sc of g.scenes) {
    const p = sc.plan
    if (!p) { missing = true; parts.push(`${sc.name}：无图`); continue }
    if (p.validate && !p.validate.ok) { bad = true; parts.push(`${sc.name}：校验错误`) }
    else if (p.judge && !p.judge.ok) { judged = true; parts.push(`${sc.name}：判官未过`) }
    else parts.push(`${sc.name}：通过`)
  }
  if (missing) return { cls: 'bg-slate-500', textCls: 'text-slate-500', label: '缺平面图', title: parts.join('\n') }
  if (bad) return { cls: 'bg-rose-400', textCls: 'text-rose-300', label: '校验错误', title: parts.join('\n') }
  if (judged) return { cls: 'bg-amber-400', textCls: 'text-amber-300', label: '判官未过', title: parts.join('\n') }
  return { cls: 'bg-emerald-400', textCls: 'text-emerald-300', label: '就绪', title: parts.join('\n') }
}

/** 选中 V：主选该 V，详情面板落到其第一个关联场景 */
function selectUnit(g: VGroup) {
  selUnit.value = g.id
  const first = g.scenes[0]?.ref
  if (first && first !== selScene.value) selScene.value = first
  tab.value = 'aiplan'
}

// 分镜切换/数据刷新后重算选中：V 模式默认选第一个 V（详情落其首个场景），场景模式默认第一个场景
watch([sceneGroups, vGroups], ([gs, vs]) => {
  if (!vs.some((v) => v.id === selUnit.value)) selUnit.value = vs[0]?.id || ''
  if (!gs.some((g) => g.key === selScene.value)) {
    const firstVScene = vs[0]?.scenes[0]?.ref
    selScene.value = firstVScene || gs[0]?.key || ''
  }
}, { immediate: true })
watch(selScene, () => { planExtra.value = '' })   // 换场景清空补充描述

/** 场景行 plan 状态点：绿=校验过 / 红=校验错 / 琥珀=判官未过 / 灰=无图 */
function planDot(g: SceneGroup): { cls: string; textCls: string; label: string; title: string } {
  const p = g.plan
  if (!p) return { cls: 'bg-slate-500', textCls: 'text-slate-500', label: '无图', title: '该场景还没有平面图' }
  if (p.validate && !p.validate.ok) {
    return { cls: 'bg-rose-400', textCls: 'text-rose-300', label: '校验错误', title: (p.validate.details || []).join('\n') }
  }
  if (p.judge && !p.judge.ok) {
    return { cls: 'bg-amber-400', textCls: 'text-amber-300', label: '判官未过', title: (p.judge.reasons || []).join('\n') }
  }
  return { cls: 'bg-emerald-400', textCls: 'text-emerald-300', label: '校验通过', title: 'validate 通过' + (p.judge?.ok ? ' · 判官通过' : '') }
}

/** 详情面板判官未过原因（取自 list 直出的 judge 字段） */
const selJudge = computed(() => {
  const j = selGroup.value?.plan?.judge
  return j && !j.ok ? { reasons: j.reasons || [] } : null
})

async function loadPlans() {
  if (!app.current) { plans.value = []; return }
  try { plans.value = (await fetchPlanList(app.current)).plans || [] }
  catch (e) { toast(e instanceof Error ? e.message : '平面图列表加载失败', 'err') }
}
async function loadZoneOptions() {
  if (!app.current) { zoneOptions.value = []; scenesMap.value = {}; return }
  try {
    const d = await fetchScriptData(app.current)
    const rows = d.scenes?.scenes || []
    zoneOptions.value = rows.map((s) => s.id || s.name || '').filter(Boolean)
    scenesMap.value = Object.fromEntries(rows.map((s) => [s.id || s.name || '', s.name || s.id || '']).filter(([k]) => k))
  } catch { zoneOptions.value = []; scenesMap.value = {} }   // 拆片项目无场景资产：留空即可
}
watch(() => app.current, () => { void loadPlans(); void loadZoneOptions() }, { immediate: true })

/** 主入口：为全部场景资产生成平面图（已有同场景平面图的跳过——后端 --all-scenes --skip-existing） */
const doGenerateAllPlans = async () => {
  if (!app.current) { toast('请先选择项目', 'err'); return }
  const existing = sceneGroups.value.filter((g) => g.plan).length
  const redo = existing > 0 && confirm(
    `${existing}/${sceneGroups.value.length} 个场景已有平面图。
确定全部重新生成？（旧图有版本快照，可在版本面板恢复；取消则只生成缺图的场景）`)
  await run('批量生成平面图', () => generatePlan({ project: app.current!, all_scenes: true, redo }), loadPlans)
}

/** 选中场景生成/重新生成（可选补充描述；同名覆盖旧图，落盘前有版本快照） */
const doRegenScenePlan = () => run('生成平面图', () => {
  const ref = selGroup.value?.ref
  if (!ref) throw new Error('请先选择场景')
  return generatePlan({
    project: app.current!, scene: ref,
    extra_desc: planExtra.value.trim() || undefined
  })
}, loadPlans)

/** 打开画布：strategy_map --plan 出俯视 HTML → 新窗打开（产物路径约定 推演/战略图_平面图_<名>.html）。 */
const openPlanCanvas = (name: string) => run('渲染画布', () => buildPlanCanvas(app.current!, name), () => {
  window.open(mediaUrl(`projects/${app.current}/推演/战略图_平面图_${name}.html`), '_blank')
})
/** 打开项目内相对路径的画布 HTML（创作包 manifest plans.canvas_html）。 */
function openCanvasHtml(rel: string) {
  window.open(mediaUrl(`projects/${app.current}/${rel}`), '_blank')
}
useBoardSelection(board, boards, 'package')
</script>

<template>
  <div class="page-wide flex h-full gap-4">
    <!-- 左列：分镜与镜头列表 -->
    <aside class="flex w-72 shrink-0 flex-col gap-3">
      <div class="glass p-3">
        <label class="block text-xs text-slate-400">
          分镜
          <StyledSelect v-model="board" class="mt-1" :options="boards" :storage-key="`wb.${app.current}.package.board`" placeholder="— 选择分镜 —" />
        </label>
        <div class="mt-2 flex flex-wrap gap-1.5">
          <button class="btn btn-sm flex-1 justify-center" :disabled="!!busy || !board" @click="doAssemble">
            {{ busy === '拍摄资料包' ? '生成中…' : '生成拍摄资料包' }}
          </button>
          <button class="btn btn-ghost btn-sm flex-1 justify-center" :disabled="!!busy || !board" @click="doStrategy">
            {{ busy === '生成战略图' ? '生成中…' : '刷新战略图' }}
          </button>
          <RouterLink class="btn btn-ghost btn-sm flex-1 justify-center" to="/white"
            title="白模渲染与预演帧导出在辅助·白模专栏（产物同步本页素材）">辅助·白模 →</RouterLink>
          <RouterLink class="btn btn-ghost btn-sm flex-1 justify-center" to="/white3d"
            title="3D 白模构建/渲染在辅助·Blender 专栏（独立旁路，产物不参与⑦参考注入）">辅助·Blender →</RouterLink>
        </div>
      </div>
      <div class="glass min-h-0 flex-1 overflow-y-auto p-2">
        <button class="btn btn-sm mb-2 w-full justify-center" :disabled="!!busy || !app.current || !zoneOptions.length"
          title="为 素材/场景.json 里还没有平面图的每个场景各生成一张（已有平面图的跳过；资产提炼完成时也会自动补）"
          @click="doGenerateAllPlans">
          {{ busy === '批量生成平面图' ? '批量生成中…' : '为全部场景生成平面图' }}
        </button>
        <p v-if="!zoneOptions.length" class="mb-2 text-center text-2xs text-slate-500">无场景资产——先到 ②素材生成 页提炼</p>
        <p v-if="!sceneGroups.length" class="py-10 text-center text-sm text-slate-500">选择分镜</p>
        <p v-if="sceneGroups.length && !hasUnits" class="mb-2 text-center text-2xs text-slate-500">
          本分镜无 V 分组（老分镜）——按场景展示；到 ⑦创作生成 建立 V 后按 V 组织
        </p>
        <!-- V 列表（与⑦创作生成同源同批）：状态点 + V 名 + 成员场景 chips + S 徽标 -->
        <template v-if="hasUnits">
          <button v-for="g in vGroups" :key="g.id"
            class="mb-1 block w-full rounded-lg p-2 text-left transition"
            :class="selUnit === g.id ? 'bg-sky-400/15 ring-1 ring-sky-400/40' : 'hover:bg-white/5'"
            @click="selectUnit(g)">
            <div class="flex items-center gap-1.5">
              <span class="h-2 w-2 shrink-0 rounded-full" :class="vDot(g).cls" :title="vDot(g).title"></span>
              <span class="text-xs font-black text-sky-300">{{ g.label }}</span>
              <span class="truncate text-xs font-bold text-slate-200" :title="g.title">{{ g.title }}</span>
              <span class="ml-auto shrink-0 text-2xs text-slate-500">{{ g.duration }}s</span>
              <span class="shrink-0 text-2xs" :class="vDot(g).textCls">{{ vDot(g).label }}</span>
            </div>
            <div class="mt-1 flex flex-wrap gap-1" title="成员场景（点击切换右侧详情）">
              <button v-for="sc in g.scenes" :key="sc.ref"
                class="rounded px-1 text-2xs transition"
                :class="selScene === sc.ref ? 'bg-violet-400/25 font-bold text-violet-200' : 'bg-violet-400/10 text-violet-300 hover:bg-violet-400/20'"
                @click.stop="selScene = sc.ref; tab = 'aiplan'">{{ sc.name }}</button>
            </div>
            <div class="mt-1 flex flex-wrap gap-1" title="成员镜头（点击跳走位战略图）">
              <button v-for="e in g.shots" :key="e.s.id"
                class="rounded bg-sky-400/15 px-1 text-2xs font-black text-sky-300 hover:bg-sky-400/30"
                @click.stop="gotoShot(e.i)">{{ e.s.id }}</button>
            </div>
          </button>
        </template>
        <!-- 场景列表（老分镜回退）：状态点 + 场景名 + 镜头徽标；点击=选中场景（右侧详情） -->
        <template v-else>
          <button v-for="g in sceneGroups" :key="g.key"
          class="mb-1 block w-full rounded-lg p-2 text-left transition"
          :class="selScene === g.key ? 'bg-sky-400/15 ring-1 ring-sky-400/40' : 'hover:bg-white/5'"
          @click="selScene = g.key; tab = 'aiplan'">
          <div class="flex items-center gap-1.5">
            <span class="h-2 w-2 shrink-0 rounded-full" :class="planDot(g).cls" :title="planDot(g).title"></span>
            <span class="truncate text-xs font-bold text-slate-200">{{ g.name }}</span>
            <span class="ml-auto shrink-0 text-2xs" :class="planDot(g).textCls">{{ planDot(g).label }}</span>
          </div>
          <div class="mt-1 flex flex-wrap gap-1">
            <span v-for="e in g.shots" :key="e.s.id"
              class="rounded bg-sky-400/15 px-1 text-2xs font-black text-sky-300">{{ e.s.id }}</span>
          </div>
        </button>
        </template>
        <!-- 其他平面图：项目里有但不属于本分镜场景的 -->
        <div v-if="otherPlans.length" class="mt-2 border-t border-line pt-2">
          <div class="px-1 pb-1 text-2xs text-slate-500">其他平面图（不在本分镜场景）</div>
          <div v-for="p in otherPlans" :key="p.name" class="flex items-center gap-2 rounded-lg px-2 py-1 hover:bg-white/5">
            <span class="truncate text-xs text-slate-300">{{ planLabel(p) }}</span>
            <span class="flex-1"></span>
            <button class="text-2xs text-sky-300 hover:underline" :disabled="!!busy" @click="openPlanCanvas(p.name)">
              {{ busy === '渲染画布' ? '渲染中…' : '打开画布' }}
            </button>
          </div>
        </div>
      </div>
    </aside>

    <!-- 右主区：三 tab -->
    <section class="flex min-w-0 flex-1 flex-col gap-3">
      <div class="glass flex items-center gap-2 px-3 py-2">
        <button v-for="t in TABS" :key="t.k"
          class="rounded-lg px-3 py-1 text-xs font-bold transition"
          :class="tab === t.k ? 'bg-sky-400/20 text-sky-200' : 'bg-white/5 text-slate-400 hover:text-slate-200'"
          @click="tab = t.k">{{ t.label }}</button>
        <span class="flex-1"></span>
      </div>

      <!-- 战略图：全高 iframe（产物未生成时给空态，不再让 404 变成白屏） -->
      <div v-if="tab === 'strategy'" class="glass min-h-0 flex-1 overflow-hidden p-1">
        <!-- 底图来源不只在产物里标一次：这页切 tab、iframe 被滚掉时就没人看见了 -->
        <div v-if="planFallback" class="mb-1 rounded-lg border border-amber-400/30 bg-amber-400/10 px-3 py-1.5 text-2xs text-amber-200">
          底图「{{ planFallback.name || '未命名' }}」与本分镜 scene_ref {{ planFallback.score || 0 }}/{{ planFallback.total || 0 }} 匹配——
          用的是回退的最新一张，<b>空间一致性未经校验</b>；要按场景出图请先到 ② 关联场景资产、再到 ⑥ 生成对应平面图
        </div>
        <iframe v-if="strategyUrl" id="strategyFrame" :src="strategyUrl" class="h-full w-full rounded-lg border-0 bg-white"
          title="战略图"></iframe>
        <div v-else class="grid h-full place-items-center p-6 text-center">
          <div v-if="!board" class="text-xs text-slate-500">选择分镜后展示战略图</div>
          <div v-else>
            <div class="text-sm font-bold text-amber-300">尚无走位战略图</div>
            <div class="mt-1 text-2xs text-slate-500">推演/{{ strategyFile }} 还没有生成</div>
            <button class="btn btn-sm mt-3" :disabled="!!busy" @click="doStrategy">
              {{ busy === '生成战略图' ? '生成中…' : '生成走位战略图' }}
            </button>
            <div class="mt-2 text-2xs text-slate-500">「生成拍摄资料包」会另出一版以场景平面图为底图的战略图</div>
          </div>
        </div>
      </div>

      <!-- AI 平面图：选中场景的详情面板 -->
      <div v-else-if="tab === 'aiplan'" class="glass min-h-0 flex-1 overflow-y-auto p-3">
        <div v-if="!selGroup" class="grid h-full place-items-center text-sm text-slate-500">
          {{ board ? '左侧选择场景' : '先选择分镜，再从左侧选择场景' }}
        </div>
        <div v-else class="space-y-3">
          <!-- V 上下文（V 模式下）：当前 V + 成员场景切换（详情面板仍按场景展示 plan） -->
          <div v-if="selVGroup" class="flex flex-wrap items-center gap-2 rounded-lg bg-white/5 px-3 py-2">
            <span class="text-xs font-black text-sky-300">{{ selVGroup.label }}</span>
            <span class="truncate text-xs text-slate-300" :title="selVGroup.title">{{ selVGroup.title }}</span>
            <span class="text-2xs text-slate-500">{{ selVGroup.duration }}s · {{ selVGroup.shots.length }} 镜</span>
            <span class="flex-1"></span>
            <button v-for="sc in selVGroup.scenes" :key="sc.ref"
              class="rounded px-1.5 text-2xs transition"
              :class="selScene === sc.ref ? 'bg-violet-400/25 font-bold text-violet-200' : 'bg-violet-400/10 text-violet-300 hover:bg-violet-400/20'"
              @click="selScene = sc.ref">{{ sc.name }}</button>
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <span class="text-base font-black text-slate-100">{{ selGroup.name }}</span>
            <span v-if="selGroup.plan && selGroup.plan.name !== selGroup.name" class="text-2xs text-slate-500">{{ selGroup.plan.name }}</span>
          </div>

          <template v-if="selGroup.ref">
            <!-- 有 plan：状态区 + 操作区 -->
            <div v-if="selGroup.plan" class="rounded-lg bg-white/5 p-3">
              <div class="flex flex-wrap items-center gap-2">
                <span class="rounded px-1.5 text-2xs"
                  :class="selGroup.plan.validate?.ok ? 'bg-emerald-400/15 text-emerald-300' : 'bg-rose-400/15 text-rose-300'"
                  :title="(selGroup.plan.validate?.details || []).join('\n')">
                  {{ selGroup.plan.validate?.ok ? '校验通过' : `校验 ${selGroup.plan.validate?.errors ?? '?'} 错误` }}{{ selGroup.plan.validate?.warnings ? ` · ${selGroup.plan.validate.warnings} 警告` : '' }}
                </span>
                <span v-if="selGroup.plan.judge && !selGroup.plan.judge.ok"
                  class="rounded bg-amber-400/15 px-1.5 text-2xs text-amber-300"
                  :title="(selGroup.plan.judge.reasons || []).join('\n')">判官未通过</span>
                <span class="text-2xs text-slate-500">
                  陈设{{ selGroup.plan.counts?.props ?? 0 }} · 演员{{ selGroup.plan.counts?.actors ?? 0 }} · 路径{{ selGroup.plan.counts?.paths ?? 0 }}
                  · 机位{{ selGroup.plan.counts?.cameras ?? 0 }} · 区域{{ selGroup.plan.counts?.zones ?? 0 }}
                </span>
                <span class="flex-1"></span>
                <span class="text-2xs text-slate-500">{{ new Date(selGroup.plan.mtime * 1000).toLocaleString() }}</span>
              </div>
              <div class="mt-2 flex flex-wrap items-center gap-2">
                <button class="btn btn-ghost btn-sm" :disabled="!!busy" @click="openPlanCanvas(selGroup.plan.name)">
                  {{ busy === '渲染画布' ? '渲染中…' : '打开画布' }}
                </button>
                <input v-model="planExtra" class="input w-64 text-xs" placeholder="补充描述（可省）：陈设增减、门窗调整……" />
                <button class="btn btn-ghost btn-sm" :disabled="!!busy"
                  title="同名覆盖该场景旧平面图（落盘前有版本快照）" @click="doRegenScenePlan">
                  {{ busy === '生成平面图' ? '生成中…' : '重新生成该场景' }}
                </button>
              </div>
              <div v-if="selJudge" class="mt-2 rounded-lg border border-amber-400/40 bg-amber-400/10 p-2 text-xs text-amber-200">
                <div>判官 3 轮仍未通过，已落盘最后一版合法产物，可人工修正：</div>
                <ul class="mt-1 list-disc pl-5"><li v-for="(r, i) in selJudge.reasons" :key="i">{{ r }}</li></ul>
              </div>
            </div>
            <!-- 无 plan：生成入口 -->
            <div v-else class="rounded-lg bg-white/5 p-3">
              <div class="text-xs text-slate-400">该场景还没有平面图</div>
              <div class="mt-2 flex flex-wrap items-center gap-2">
                <input v-model="planExtra" class="input w-64 text-xs" placeholder="补充描述（可省）：陈设增减、门窗调整……" />
                <button class="btn btn-sm" :disabled="!!busy" @click="doRegenScenePlan">
                  {{ busy === '生成平面图' ? '生成中…' : '生成该场景平面图' }}
                </button>
              </div>
            </div>
          </template>
          <!-- 未关联组：无场景资产可操作 -->
          <div v-else class="rounded-lg bg-white/5 p-3 text-xs text-slate-400">
            这些镜头未关联场景资产（scene_ref 为空）——无法按场景生成平面图；如需场景底图，请在分镜中为镜头补 scene_ref。
          </div>

          <!-- 本场景镜头（点击跳走位战略图对应镜） -->
          <div>
            <div class="mb-1 text-2xs text-slate-500">本场景镜头（点击跳走位战略图）</div>
            <div class="flex flex-wrap gap-1.5">
              <button v-for="e in selGroup.shots" :key="e.s.id"
                class="rounded-lg bg-white/5 p-2 text-left transition hover:bg-white/10" @click="gotoShot(e.i)">
                <div class="flex items-center gap-1.5">
                  <span class="rounded bg-sky-400/15 px-1.5 text-xs-plus font-black text-sky-300">{{ e.s.id }}</span>
                  <span class="text-2xs text-slate-500">{{ e.s.dur }}s · {{ (e.s.lines || []).length }}台词</span>
                </div>
                <div class="mt-0.5 line-clamp-1 max-w-56 text-2xs text-slate-400">{{ e.s.action || e.s.prompt }}</div>
              </button>
            </div>
          </div>
        </div>
      </div>


      <!-- 逐镜包 -->
      <div v-else class="glass min-h-0 flex-1 overflow-y-auto p-3">
        <div v-if="!pkg" class="grid h-full place-items-center text-xs text-slate-500">尚未生成——点左上「生成拍摄资料包」</div>
        <div v-else class="space-y-2">
          <div v-for="s in pkg.shots" :key="s.id" class="rounded-lg bg-white/5 p-2.5">
            <div class="flex flex-wrap items-center gap-2">
              <span class="rounded bg-sky-400/15 px-1.5 font-black text-sky-300">{{ s.id }}</span>
              <span class="text-xs-plus text-slate-400">{{ s.move }} · {{ s.dur }}s</span>
              <span class="ml-auto flex gap-1">
                <span class="rounded px-1 text-2xs" :class="s.diagram ? 'bg-emerald-400/15 text-emerald-300' : 'bg-white/5 text-slate-500'">平面图</span>
                <span class="rounded px-1 text-2xs" :class="s.white_ref ? 'bg-emerald-400/15 text-emerald-300' : 'bg-white/5 text-slate-500'">白模参考</span>
              </span>
            </div>
            <p v-if="s.prompt" class="mt-1 text-xs-plus leading-relaxed text-slate-300">{{ s.prompt }}</p>
            <p v-if="(s.script || []).length" class="mt-1 text-xs-plus text-amber-200/80">
              {{ (s.script || []).map((l) => `【${l.speaker}】${l.text}`).join(' ') }}
            </p>
          </div>
          <p v-if="(pkg.materials || []).length" class="pt-1 text-2xs text-slate-500">素材引用：{{ (pkg.materials || []).join('、') }}</p>
          <!-- AI 平面图（plan v1 挂接）：场景名 + 校验徽标 + 打开画布（有 canvas_html 才可点） -->
          <div v-if="(pkg.plans || []).length" class="flex flex-wrap items-center gap-x-3 gap-y-1 pt-1 text-2xs text-slate-500">
            <span>AI 平面图：</span>
            <span v-for="pl in pkg.plans" :key="pl.name" class="flex items-center gap-1">
              <span class="text-slate-300">{{ planLabel(pl) }}</span>
              <span class="text-slate-500">（陈设{{ pl.counts?.props ?? 0 }}/演员{{ pl.counts?.actors ?? 0 }}/路径{{ pl.counts?.paths ?? 0 }}）</span>
              <span class="rounded px-1"
                :class="pl.validate_ok ? 'bg-emerald-400/15 text-emerald-300' : pl.validate_ok === false ? 'bg-rose-400/15 text-rose-300' : 'bg-white/5 text-slate-500'">
                {{ pl.validate_ok === null || pl.validate_ok === undefined ? '未校验' : pl.validate_ok ? '校验通过' : '校验失败' }}
              </span>
              <button v-if="pl.canvas_html" class="text-sky-300 hover:underline" @click="openCanvasHtml(pl.canvas_html!)">打开画布</button>
            </span>
          </div>
        </div>
      </div>
    </section>

  <OverlayViewer :visible="overlay.visible" :src="overlay.src" :kind="overlay.kind" :title="overlay.title" @close="overlay.visible = false" />
  </div>
</template>
