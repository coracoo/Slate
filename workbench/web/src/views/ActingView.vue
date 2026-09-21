<script setup lang="ts">
import { useBoardSelection } from '../utils/useBoardSelection'
// -*- coding: utf-8 -*-
/** 演员管理：角色卡、连续性记忆、镜头表演候选与显式应用。 */
import { ref, computed, watch, onMounted } from 'vue'
import {
  fetchActingContext, fetchActingCandidates, saveActingContext, prepareActing,
  runActing, applyActingCandidate, setActingLock, evaluateActing, fetchEnvConfig, compileActingPrompt,
  fetchActingEvals, type ActingContextResponse, type ActingCandidate, type ActingShot, type ActingEval, type Vendor
} from '../api'
import { app, toast, projectFiles, loadBasics } from '../stores/app'
import { trackJob } from '../stores/jobs'
import StyledSelect from '../components/StyledSelect.vue'
import StyleSelect from '../components/StyleSelect.vue'
import EmptyState from '../components/EmptyState.vue'

const boards = computed(() => projectFiles('分镜').filter((f) => f.startsWith('剧本_') && f.endsWith('.json')))
const board = ref('')
onMounted(() => { if (!app.projects.length) void loadBasics() })
const data = ref<ActingContextResponse | null>(null)
const candidates = ref<ActingCandidate[]>([])
const loading = ref(false)
const saving = ref(false)
const running = ref(false)
const runningShot = ref('')
const applying = ref('')
const evaluating = ref(false)
const preparing = ref(false)
const revision = ref('')
const selected = ref<Set<string>>(new Set())
const mode = ref<'style' | 'stateful'>('stateful')
const vendorId = ref('')
const vendors = ref<Vendor[]>([])
const preview = ref<{ shot_id: string; prompt: string; performance_used: boolean; asset_refs?: string[]; prompt_json?: Record<string, unknown> | null } | null>(null)
const evals = ref<ActingEval[]>([])
const latestEval = computed(() => evals.value[0] || null)
const total30 = (s: { total?: number | null }) => (s && typeof s.total === 'number' ? String(s.total) : '—')
let evalSeq = 0
async function loadEvals() {
  const request = ++evalSeq, project = app.current, name = board.value
  evals.value = []
  if (!project || !name) return
  try {
    const result = await fetchActingEvals(project, name)
    if (request === evalSeq && project === app.current && name === board.value) evals.value = result.evals || []
  } catch (e) { if (request === evalSeq) toast(e instanceof Error ? e.message : '评分加载失败', 'err') }
}

const actorCards = ref<Record<string, Record<string, unknown>>>({})
const memoryText = ref('')
let requestSeq = 0

/* 防丢稿：load() 后快照已保存值；触发整页 load() 的操作前检测脏编辑并确认 */
let savedCardsJson = '{}'
let savedNotes = ''
function contextDirty() {
  return JSON.stringify(actorCards.value) !== savedCardsJson || memoryText.value !== savedNotes
}
function confirmDiscardEdits() {
  return !contextDirty() || confirm('有未保存的编辑，继续将被覆盖')
}
function snapshotContext() {
  savedCardsJson = JSON.stringify(actorCards.value)
  savedNotes = memoryText.value
}

const textVendors = computed(() => vendors.value.filter((v) => v.enabled && (v.models?.text || '')))
const vendorOptions = computed(() => textVendors.value.map((v) => v.id))
const vendorLabels = computed<Record<string, string>>(() =>
  Object.fromEntries(textVendors.value.map((v) => [v.id, (v.label || v.id) + ' · ' + (v.models?.text || '')]))
)
const shots = computed(() => data.value?.shots || [])
const selectableShots = computed(() => shots.value.filter((s) => !s.performance_locked && !!s.actor_ids?.length))
const allSelected = computed(() => selectableShots.value.length > 0 && selectableShots.value.every((s) => selected.value.has(s.id)))
const someSelected = computed(() => selected.value.size > 0 && !allSelected.value)
const actorEntries = computed(() => {
  const actors = data.value?.actors || {}
  if (Array.isArray(actors)) return actors.filter((x) => x.is_main !== false).map((x) => [String(x.id || ''), x] as [string, Record<string, unknown>])
  return Object.entries(actors).filter(([, actor]) => actor?.is_main !== false)
})

function statusLabel(status: string) {
  return ({ ready: '已有表演', locked: '已锁定', stale: '已过期', invalid: '校验失败', context_ready: '上下文已准备', pending: '待生成' } as Record<string, string>)[status] || status || '待生成'
}
function statusClass(status: string) {
  return ({ ready: 'bg-emerald-400/15 text-emerald-300', locked: 'bg-amber-400/15 text-amber-300', stale: 'bg-orange-400/15 text-orange-300', invalid: 'bg-rose-400/15 text-rose-300', context_ready: 'bg-cyan-400/15 text-cyan-300' } as Record<string, string>)[status] || 'bg-white/5 text-slate-500'
}
function performanceSummary(s: ActingShot) {
  const p = s.performance as any
  const actors = p?.packet?.actors || p?.actors || []
  if (!Array.isArray(actors)) return ''
  return actors.flatMap((actor: any) => (actor?.beats || []).map((beat: any) => [beat?.intent, beat?.posture, beat?.gaze, beat?.gesture, beat?.expression].filter(Boolean).join('；'))).filter(Boolean).join(' · ')
}
function toggle(id: string) {
  if (!selectableShots.value.some((shot) => shot.id === id)) return
  const next = new Set(selected.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  selected.value = next
}
function toggleAll() {
  selected.value = allSelected.value ? new Set() : new Set(selectableShots.value.map((s) => s.id))
}
function card(id: string) {
  return actorCards.value[id] || (actorCards.value[id] = {
    personality: '', goal: '', relationship: '', expression_rules: '', arc_stage: '',
    source: '', locked_fields: []
  })
}
function field(id: string, key: string): string {
  const value = card(id)[key]
  return value == null ? '' : String(value)
}
function setField(id: string, key: string, value: string) {
  card(id)[key] = key === 'locked_fields'
    ? value.split(',').map((item) => item.trim()).filter(Boolean)
    : value
}
function contextPayload(): Record<string, unknown> {
  const base = (data.value?.context || {}) as Record<string, unknown>
  return { ...base, actor_cards: actorCards.value, notes: memoryText.value }
}

async function load() {
  const seq = ++requestSeq
  ++evalSeq; evals.value = []; candidates.value = []; preview.value = null; data.value = null; actorCards.value = {}; memoryText.value = ''; selected.value = new Set()
  if (!app.current || !board.value) { data.value = null; return }
  loading.value = true
  try {
    const result = await fetchActingContext(app.current, board.value)
    if (seq !== requestSeq || board.value !== result.board) return
    data.value = result
    revision.value = result.revision
    candidates.value = result.candidates || []
    const ctx = result.context || {}
    actorCards.value = (ctx.actor_cards as Record<string, Record<string, unknown>> || {})
    memoryText.value = String(ctx.notes || '')
    snapshotContext()
    selected.value = new Set(result.shots.filter((s) => !s.performance_locked && !!s.actor_ids?.length).map((s) => s.id))
    if (!vendorId.value && textVendors.value.length) vendorId.value = textVendors.value.at(-1)!.id
    void loadEvals()
  } catch (e) {
    if (seq === requestSeq) { data.value = null; toast(e instanceof Error ? e.message : '演员数据加载失败', 'err') }
  } finally {
    if (seq === requestSeq) loading.value = false
  }
}
async function loadVendors() {
  try {
    vendors.value = (await fetchEnvConfig()).vendors || []
    if (!vendorId.value) vendorId.value = textVendors.value.at(-1)?.id || ''
  } catch { vendors.value = [] }
}
async function saveContext(notify = true) {
  if (!app.current || !board.value || !data.value) return
  saving.value = true
  try {
    const result = await saveActingContext({ project: app.current, storyboard: board.value, revision: revision.value, context: contextPayload() })
    revision.value = result.revision
    if (data.value) data.value.context = result.context
    snapshotContext()
    if (notify) toast('演员记忆与角色卡已保存', 'ok')
  } catch (e) {
    toast(e instanceof Error ? e.message : '演员记忆保存失败', 'err')
    await load()
  } finally { saving.value = false }
}
async function prepare() {
  if (!app.current || !board.value || !data.value) {
    toast('请先选择有效分镜', 'err'); return
  }
  if (preparing.value) return
  preparing.value = true
  const project = app.current, name = board.value
  try {
    const shotIds = Array.from(selected.value).filter((id) => selectableShots.value.some((shot) => shot.id === id))
    if (!shotIds.length) { toast('请选择至少一个有关联主角的镜头', 'err'); return }
    const result = await prepareActing({ project: app.current, storyboard: board.value, shot_ids: shotIds, context: contextPayload(), vendor_id: vendorId.value || undefined })
    const job = await trackJob(result.id, '演员读剧本')
    if (!job.success) throw new Error(job.err || '演员上下文准备失败')
    if (project !== app.current || name !== board.value) return
    await refreshCandidates()
    toast('演员读完剧本，上下文候选已生成，可在候选区审核应用', 'ok')
  } catch (e) { toast(e instanceof Error ? e.message : '上下文准备失败', 'err') }
  finally { preparing.value = false }
}
async function runSelected() {
  const shotIds = Array.from(selected.value).filter((id) => selectableShots.value.some((shot) => shot.id === id))
  if (!app.current || !board.value || !vendorId.value || !shotIds.length) {
    toast('请选择镜头和文本厂商', 'err'); return
  }
  running.value = true
  try {
    // 先落盘当前角色卡/记忆，演员子进程会从正式分镜读取同一份上下文。
    if (data.value) await saveContext(false)
    for (const sid of shotIds) {
      runningShot.value = sid
      const result = await runActing({ project: app.current, storyboard: board.value, shot_id: sid, vendor_id: vendorId.value, mode: mode.value })
      if (!result.id) throw new Error(result.err || '演员任务未启动')
      const job = await trackJob(result.id, '演员表演候选 ' + sid)
      if (!job.success) throw new Error(job.err || ('镜头 ' + sid + ' 生成失败'))
    }
    toast('表演候选已生成，请在下方选择并应用', 'ok')
    await refreshCandidates()
  } catch (e) { toast(e instanceof Error ? e.message : '演员生成失败', 'err') }
  finally { running.value = false; runningShot.value = '' }
}
function canGenerate(s: ActingShot) {
  return !!app.current && !!board.value && !!vendorId.value && !s.performance_locked && !!s.actor_ids?.length
}
async function runOne(s: ActingShot) {
  if (!canGenerate(s) || running.value) return
  if (!confirmDiscardEdits()) return
  running.value = true
  runningShot.value = s.id
  try {
    if (data.value) await saveContext(false)
    const result = await runActing({ project: app.current!, storyboard: board.value, shot_id: s.id, vendor_id: vendorId.value, mode: mode.value })
    if (!result.id) throw new Error(result.err || '演员任务未启动')
    const job = await trackJob(result.id, '演员表演候选 ' + s.id)
    if (!job.success) throw new Error(job.err || ('镜头 ' + s.id + ' 生成失败'))
    toast(`${s.id} 演员表现已生成，请在候选草稿中应用`, 'ok', 5000)
    await refreshCandidates()
    await load()
  } catch (e) { toast(e instanceof Error ? e.message : '演员生成失败', 'err') }
  finally { running.value = false; runningShot.value = '' }
}
async function startEvaluation() {
  const shotIds = Array.from(selected.value).filter((id) => selectableShots.value.some((shot) => shot.id === id))
  if (!app.current || !board.value || !shotIds.length) {
    toast('请选择至少一个镜头', 'err'); return
  }
  evaluating.value = true
  try {
    const result = await evaluateActing({
      project: app.current, storyboard: board.value,
      shot_ids: shotIds, modes: ['baseline', 'style', 'stateful'], repeats: 2,
      vendor_id: vendorId.value || undefined
    })
    if (!result.id) throw new Error(result.err || '评分任务未启动')
    const j = await trackJob(result.id, '演员评分')
    if (j.success) { toast('演员评分完成，结果见下方评分面板', 'ok'); await loadEvals() }
    else toast(j.err || '演员评分失败，详情见任务抽屉', 'err')
  } catch (e) { toast(e instanceof Error ? e.message : '演员评分失败', 'err') }
  finally { evaluating.value = false }
}async function refreshCandidates() {
  const project = app.current, name = board.value, request = requestSeq
  if (!project || !name) return
  try {
    const result = await fetchActingCandidates(project, name)
    if (request === requestSeq && project === app.current && name === board.value) candidates.value = result.candidates || []
  } catch (e) { if (request === requestSeq) toast(e instanceof Error ? e.message : '候选加载失败', 'err') }
}

async function compile(s: ActingShot) {
  if (!app.current || !board.value) return
  try {
    const r = await compileActingPrompt({ project: app.current, storyboard: board.value, shot_id: s.id, mode: mode.value, media_type: 'video' })
    preview.value = { shot_id: s.id, prompt: r.prompt, performance_used: r.performance_used, asset_refs: r.asset_refs || [], prompt_json: r.prompt_json || null }
  } catch (e) { toast(e instanceof Error ? e.message : '提示词编译失败', 'err') }
}
async function lockShot(s: ActingShot) {
  if (!app.current || !board.value || !data.value) return
  if (!confirmDiscardEdits()) return
  try {
    const r = await setActingLock({ project: app.current, storyboard: board.value, shot_ids: [s.id], locked: !s.performance_locked, revision: revision.value })
    revision.value = r.revision
    toast(r.locked ? `已锁定 ${s.id}` : `已解锁 ${s.id}`, 'ok')
    await load()
  } catch (e) {
    toast(e instanceof Error ? e.message : '镜头锁定失败', 'err')
    await load()
  }
}async function apply(c: ActingCandidate) {
  if (!app.current || !board.value || !['performance', 'context'].includes(c.candidate_kind)) return
  if (!confirmDiscardEdits()) return
  applying.value = c.run_id
  try {
    const r = await applyActingCandidate({ project: app.current, storyboard: board.value, run_id: c.run_id, revision: revision.value })
    revision.value = r.revision
    toast(r.changed_shots.length ? '已应用 ' + r.changed_shots.join('、') + ' 的表演' : '候选已处理', 'ok')
    await load()
  } catch (e) {
    toast(e instanceof Error ? e.message : '应用候选失败', 'err')
    await load()
  } finally { applying.value = '' }
}

watch(() => [app.current, app.projects, boards.value.join('|')], () => {

  requestSeq++
  void loadVendors()
  if (board.value) void load()
  else data.value = null
}, { immediate: true })
watch(board, () => { requestSeq++; void load() })
useBoardSelection(board, boards, 'acting')
</script>

<template>
  <div class="page">
    <header class="mb-6">
      <h1 class="grad-text text-2xl font-black">⑤ 演员表现</h1>
      <p class="mt-1 text-xs text-slate-500">主角演员由人物.json 的主角角色档案自动进入；这里的“新生成/重新生成”指本镜表演候选，生成表情、视线、姿态和节奏后可在右侧审核应用。配角和群演不会进入演员层，机位、走位、台词和时长始终由分镜锁定。</p>
    </header>
    <EmptyState v-if="!app.current" title="请先在左侧选择项目" />
    <template v-else>
      <div class="glass mb-4 flex flex-wrap items-end gap-3 p-4">
        <label class="text-xs text-slate-400">分镜
          <StyledSelect v-model="board" class="mt-1 min-w-56" :options="boards" :storage-key="`wb.${app.current}.acting.board`" placeholder="— 选择分镜 —" />
        </label>
        <StyleSelect target="acting" label="表演风格" />
        <label class="text-xs text-slate-400">文本厂商
          <StyledSelect v-model="vendorId" class="mt-1 min-w-56" :options="vendorOptions" :labels="vendorLabels" :storage-key="`wb.${app.current}.acting.vendor`" placeholder="— 选择 —" />
        </label>
        <button class="btn" :disabled="saving || loading || !data" @click="() => saveContext()">{{ saving ? '保存中…' : '保存角色卡/记忆' }}</button>
        <button class="btn btn-ghost" :disabled="!data || loading || preparing || !board" @click="prepare">演员读剧本</button>
        <button class="btn btn-ghost" :disabled="running || !selected.size || !vendorId" @click="runSelected" title="为选中且关联主角的镜头新生成或重新生成演员表演候选；只补表情、视线、姿态和节奏">{{ running ? '生成中…' : '演员生成表现' }}</button>
        <button class="btn btn-ghost" :disabled="evaluating || !selected.size || !vendorId" @click="startEvaluation" title="A=裸分镜 B=+导演风格 C=+风格+角色卡记忆；LLM 裁判逐镜对比评分，不调用生视频">{{ evaluating ? '评分中…' : '演员评分' }}</button>
      </div>

      <div v-if="latestEval" class="glass mb-4 p-4">
        <div class="mb-2 flex flex-wrap items-center gap-2">
          <h2 class="text-sm font-bold text-slate-200">演员评分 · {{ latestEval.experiment_id }}</h2>
          <span class="text-2xs text-slate-500">A=裸分镜 · B=+导演风格 · C=+风格+角色卡记忆（满分30，LLM 裁判文本对比，不代表成片效果）</span>
        </div>
        <div v-if="latestEval.summary?.wins" class="mb-2 text-xs-plus text-slate-300">
          胜出：<b class="text-emerald-300">{{ (['A','B','C'] as const).map((k) => k + ' ' + (latestEval.summary?.wins?.[k] || 0) + ' 镜').join(' · ') }}</b>
          <span v-if="latestEval.summary?.avg_total" class="ml-3 text-slate-500">均分 {{ (['A','B','C'] as const).map((k) => k + ' ' + (latestEval.summary?.avg_total?.[k] ?? '—')).join(' / ') }}</span>
        </div>
        <div class="overflow-x-auto">
          <table class="w-full text-left text-xs-plus">
            <thead class="text-slate-500">
              <tr><th class="py-1 pr-3">镜头</th><th class="py-1 pr-3">A 裸分镜</th><th class="py-1 pr-3">B +风格</th><th class="py-1 pr-3">C +风格+记忆</th><th class="py-1 pr-3">最佳</th><th class="py-1">评语</th></tr>
            </thead>
            <tbody>
              <tr v-for="[sid, sc] in Object.entries(latestEval.scores)" :key="sid" class="border-t border-line-soft">
                <td class="py-1.5 pr-3 font-bold text-cyan-300">{{ sid }}</td>
                <td v-for="k in (['A','B','C'] as const)" :key="k" class="py-1.5 pr-3" :class="sc.best === k ? 'font-bold text-emerald-300' : 'text-slate-300'" :title="sc[k]?.reason || ''">
                  {{ total30(sc[k] || {}) }}<span v-if="sc[k]?.dims" class="ml-1 text-2xs text-slate-500">{{ sc[k]?.dims?.specificity }}/{{ sc[k]?.dims?.consistency }}/{{ sc[k]?.dims?.discipline }}</span>
                </td>
                <td class="py-1.5 pr-3">{{ sc.best || '—' }}</td>
                <td class="py-1.5 text-slate-400" :title="sc.reason">{{ sc.reason || '' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-if="!Object.keys(latestEval.scores || {}).length" class="py-2 text-center text-xs-plus text-slate-500">本次登记未包含评分（评分需要选择文本厂商）</div>
      </div>

      <div v-if="loading" class="glass p-10 text-center text-sm text-slate-500">加载演员数据…</div>
      <div v-else-if="!data" class="glass p-10 text-center text-sm text-slate-500">该项目暂无可用分镜</div>
      <div v-else class="grid gap-4 xl:grid-cols-[minmax(0,1.45fr)_minmax(360px,0.8fr)]">
        <section class="space-y-4">
          <div class="glass p-4">
            <div class="mb-3 flex items-center gap-2">
              <h2 class="text-sm font-bold text-slate-200">主角角色卡与连续性记忆</h2>
              <span class="text-2xs text-slate-500">仅主角 · 字段保存在 acting_context，不覆盖视觉站位</span>
            </div>
            <p class="mb-3 text-xs-plus leading-5 text-slate-500">角色卡优先读取人物.json 的 acting 字段；缺失时从大纲角色提示自动补齐。这里只显示主角，重新提炼资产后可获得完整的性格、目标、关系和表达规则。</p>
            <textarea v-model="memoryText" class="textarea mb-3 min-h-20" placeholder="本项目表演记忆：关系变化、情绪基线、不可违背的角色规则…"></textarea>
            <div class="grid gap-3 md:grid-cols-2">
              <article v-for="[id, actor] in actorEntries" :key="id" class="rounded-xl border border-line bg-black/20 p-3">
                <div class="mb-2 flex items-center gap-2">
                  <b class="text-sm text-slate-100">{{ actor.name || id }}</b>
                  <span class="rounded bg-white/5 px-1.5 py-0.5 text-2xs text-slate-500">{{ id }}</span>
                </div>
                <div class="space-y-2">
                  <label v-for="item in [{k:'personality',n:'性格'}, {k:'goal',n:'当镜目标'}, {k:'relationship',n:'关系'}, {k:'expression_rules',n:'表达规则'}, {k:'arc_stage',n:'弧线阶段'}]" :key="item.k" class="block text-2xs text-slate-500">
                    {{ item.n }}<input class="input mt-0.5 text-xs" :value="field(id, item.k)" @input="setField(id, item.k, ($event.target as HTMLInputElement).value)" />
                  </label>
                  <label class="block text-2xs text-slate-500">
                    来源<input class="input mt-0.5 text-xs" :value="field(id, 'source')" @input="setField(id, 'source', ($event.target as HTMLInputElement).value)" placeholder="剧本/人物档案/人工设定" />
                  </label>
                  <label class="block text-2xs text-slate-500">
                    锁定字段（逗号分隔）<input class="input mt-0.5 text-xs" :value="field(id, 'locked_fields')" @input="setField(id, 'locked_fields', ($event.target as HTMLInputElement).value)" placeholder="personality,goal" />
                  </label>
                </div>
              </article>
            </div>
            <div v-if="!actorEntries.length" class="rounded-lg border border-amber-400/20 bg-amber-400/5 p-3 text-xs-plus leading-relaxed text-amber-200/80">
              当前分镜没有识别到主角。请先在人物资产中把角色标记为“主角”，并在分镜中关联该角色。
            </div>
          </div>

          <div class="glass p-4">
            <div class="mb-3 flex items-center gap-2">
              <h2 class="text-sm font-bold text-slate-200">镜头表演</h2>
              <label class="ml-auto flex cursor-pointer items-center gap-1.5 text-2xs text-slate-400">
                <input type="checkbox" :checked="allSelected" :indeterminate="someSelected" @change="toggleAll" />{{ allSelected ? '全不选' : '全选' }}
              </label>
            </div>
            <div class="space-y-2">
              <article v-for="s in shots" :key="s.id" class="rounded-xl border border-line bg-black/20 p-3">
                <div class="flex items-start gap-2">
                  <input class="mt-1" type="checkbox" :checked="selected.has(s.id)" :disabled="s.performance_locked || !s.actor_ids?.length" @change="toggle(s.id)" />
                  <div class="min-w-0 flex-1">
                    <div class="flex items-center gap-2">
                      <b class="text-xs text-cyan-300">{{ s.id }}</b>
                      <span class="text-2xs text-slate-500">{{ s.dur }}s</span>
                      <span class="rounded px-1.5 py-0.5 text-2xs" :class="statusClass(s.performance_status)">{{ statusLabel(s.performance_status) }}</span>
                      <span class="flex-1"></span>
                      <button class="btn btn-sm" :disabled="running || !canGenerate(s)" @click="runOne(s)" :title="s.actor_ids?.length ? '新生成或重新生成本镜主角演员表现；生成后到右侧候选草稿点击应用' : '本镜没有主角，不生成演员表现'">
                        {{ runningShot === s.id ? '生成中…' : (s.performance_status === 'pending' ? '新生成主角表演' : '重新生成主角表演') }}
                      </button>
                      <button class="btn btn-ghost btn-sm" @click="compile(s)">{{ mode === 'stateful' ? '编译状态提示词' : '编译风格提示词' }}</button>
                      <button class="btn btn-ghost btn-sm" @click="lockShot(s)">{{ s.performance_locked ? '解锁' : '锁定' }}</button>
                    </div>
                    <div class="mt-1 flex flex-wrap items-center gap-1.5 text-2xs">
                      <span v-if="s.actor_names?.length" class="rounded bg-cyan-400/10 px-1.5 py-0.5 text-cyan-200">主角：{{ s.actor_names.join('、') }}</span>
                      <span v-else class="rounded bg-slate-400/10 px-1.5 py-0.5 text-slate-500">本镜无主角演员</span>
                    </div>
                    <p class="mt-1 text-xs-plus leading-relaxed text-slate-400">{{ s.action || s.prompt || '—' }}</p>
                    <p v-if="performanceSummary(s)" class="mt-1 rounded bg-emerald-400/5 px-2 py-1 text-2xs leading-relaxed text-emerald-200/80">表演：{{ performanceSummary(s) }}</p>
                  </div>
                </div>
              </article>
            </div>
            <div v-if="preview" class="mt-3 rounded-lg border border-cyan-400/20 bg-cyan-500/5 p-3">
              <div class="mb-1 text-2xs text-cyan-300">实际发送提示词 · {{ preview.shot_id }} · {{ preview.performance_used ? '已使用演员表演' : '基础分镜' }}</div>
               <div v-if="preview.asset_refs?.length" class="mb-2 flex flex-wrap gap-1">
                 <span v-for="ref in preview.asset_refs" :key="ref" class="rounded border border-cyan-400/30 bg-cyan-400/10 px-1.5 py-0.5 text-2xs text-cyan-200">{{ ref }}</span>
               </div>
              <pre class="max-h-52 overflow-auto whitespace-pre-wrap text-xs-plus leading-relaxed text-slate-300">{{ preview.prompt }}</pre>
            </div>
          </div>
        </section>

        <aside class="glass p-4">
          <div class="mb-3 flex items-center gap-2">
            <h2 class="text-sm font-bold text-slate-200">候选草稿</h2>
            <span class="flex-1"></span>
            <button class="btn btn-ghost btn-sm" @click="refreshCandidates">刷新</button>
          </div>
          <div class="mb-3 flex gap-1.5">
            <button v-for="m in [{k:'style',n:'风格'}, {k:'stateful',n:'状态'}]" :key="m.k" class="flex-1 rounded-lg border px-2 py-1.5 text-2xs" :class="mode === m.k ? 'border-cyan-400/50 bg-cyan-400/10 text-cyan-200' : 'border-line text-slate-500'" @click="mode = m.k as typeof mode">{{ m.n }}</button>
          </div>
          <div v-if="!candidates.length" class="py-10 text-center text-sm text-slate-500">还没有候选草稿</div>
          <div v-for="c in candidates" :key="c.run_id" class="mb-2 rounded-lg border border-line bg-black/20 p-2.5">
            <div class="flex items-center gap-2">
              <b class="text-2xs text-slate-300">{{ c.candidate_kind === 'performance' ? '表演' : '上下文' }}</b>
              <span class="text-2xs text-slate-500">{{ c.status }}</span>
              <span class="flex-1"></span>
              <button class="btn btn-sm" :disabled="applying === c.run_id" @click="apply(c)">{{ applying === c.run_id ? '应用中…' : '应用' }}</button>
            </div>
            <div class="mt-1 text-2xs text-slate-500">{{ (c.shot_ids || []).join('、') }} · {{ c.created_at || '' }}</div>
          </div>
        </aside>
      </div>
    </template>
  </div>
</template>







