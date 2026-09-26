<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { app, projectFiles, toast } from '../stores/app'
import { getJSON, mediaUrl } from '../api'
import { trackJob } from '../stores/jobs'
import { fetchCreate, postJSON } from '../api'
import { reconcilePending, studioData, submitRedoJob, type ProductionItem, type StudioState } from '../utils/productionStudio'
import { useBoardSelection } from '../utils/useBoardSelection'

interface Frame {t: number; path: string}
interface FramesResult {item_id: string; duration: number; step: number; frames: Frame[]}
interface VendorLite {id: string; label?: string; enabled?: boolean; models?: Record<string, string>}

const data = ref<StudioState | null>(null), items = ref<ProductionItem[]>([])
const board = ref(''), unitId = ref(''), error = ref('')
const sourceItem = ref<ProductionItem | null>(null), frames = ref<FramesResult | null>(null), framesBusy = ref(false)
const headT = ref<number | null>(null), tailT = ref<number | null>(null), prompt = ref('')
const vendor = ref(''), vendors = ref<VendorLite[]>([]), busy = ref(false), merging = ref<string | null>(null)
const boards = computed(() => projectFiles('分镜', /\.json$/))
useBoardSelection(board, boards, 'shotRedo')
watch(board, name => { if (app.current && name) localStorage.setItem(`wb.${app.current}.shotRedo.board`, name) })
const units = computed(() => data.value?.board.video_units || [])
const unit = computed(() => units.value.find(u => u.id === unitId.value))
const versions = computed(() => items.value.filter(i => i.type === 'video' && i.unit_id === unitId.value && ['done', 'error'].includes(i.status)))
const pending = computed(() => versions.value.filter(i => i.action === 'redo_segment' && i.redo && !i.redo.merged))
function outputPath(item: ProductionItem) { const p = item.outputs?.[0] as string | {path: string} | undefined; return typeof p === 'string' ? p : p?.path || '' }
function fileUrl(path: string) { return mediaUrl(path.startsWith('projects/') ? path : `projects/${app.current}/${path}`) }
function frameUrl(path: string) { return mediaUrl(`projects/${app.current}/${path}`) }

async function load() {
  const project = app.current, name = board.value
  if (!project) return
  try {
    if (name) {
      const r = await studioData(project, name)
      if (project !== app.current) return
      data.value = r
      if (!r.board.video_units?.some(u => u.id === unitId.value)) unitId.value = r.board.video_units?.[0]?.id || ''
    }
    const g = await fetchCreate(project)
    if (project !== app.current) return
    items.value = g.items as ProductionItem[]
    if (sourceItem.value && !items.value.some(i => i.id === sourceItem.value!.id)) sourceItem.value = null
  } catch (e) { error.value = String(e) }
}
async function loadVendors() {
  try {
    const r = await getJSON<{vendors: VendorLite[]}>('/api/env/config')
    vendors.value = (r.vendors || []).filter(v => v.enabled && v.models?.video)
    const saved = app.current ? localStorage.getItem(`wb.${app.current}.shotRedo.vendor`) || '' : ''
    if (!vendors.value.some(v => v.id === vendor.value)) vendor.value = vendors.value.some(v => v.id === saved) ? saved : vendors.value[0]?.id || ''
  } catch { /* 环境配置不可用时保持当前值 */ }
}
watch(vendor, v => { if (v && app.current) localStorage.setItem(`wb.${app.current}.shotRedo.vendor`, v) })
watch(unitId, () => { sourceItem.value = null; frames.value = null; headT.value = null; tailT.value = null; prompt.value = unit.value?.prompt_video || '' })
watch(() => app.current, () => { void load(); void loadVendors(); void reconcilePending(String(app.current || '')) }, {immediate: true})

async function extractFrames(item: ProductionItem) {
  sourceItem.value = item
  frames.value = null; headT.value = null; tailT.value = null
  framesBusy.value = true; error.value = ''
  try {
    const r = await getJSON<FramesResult & {ok?: boolean; err?: string}>(`/api/production/redo_frames?project=${encodeURIComponent(app.current || '')}&item_id=${encodeURIComponent(item.id)}`)
    frames.value = r
  } catch (e) { error.value = String(e) } finally { framesBusy.value = false }
}
function setHead(t: number) { headT.value = headT.value === t ? null : t }
function setTail(t: number) { tailT.value = tailT.value === t ? null : t }
async function reshoot() {
  const project = app.current
  if (!project || !unit.value || !sourceItem.value) return
  if (headT.value === null && tailT.value === null) { error.value = '首尾都为空等于整段重做，请走⑦创作生成的 V 生成渠道；这里请至少点选一个边界帧'; return }
  const duration = frames.value?.duration || 0
  const t0 = headT.value ?? 0, t1 = tailT.value ?? duration
  if (t1 - t0 < 0.5) { error.value = '重拍窗口过短（<0.5s），请重新选择边界'; return }
  busy.value = true; error.value = ''
  try {
    const body = {project, board: board.value, target: unitId.value, t0, t1, prompt: prompt.value,
      source_item_id: sourceItem.value.id, defer_merge: true, vendor_id: vendor.value, video_options: {}}
    const r = await submitRedoJob(body)
    if (r.id) void trackJob(r.id, `重拍 ${t0}–${t1}s`).then(async () => { if (app.current === project) await load() })
    toast('重拍任务已提交，完成后点该片段的「一键合并」', 'ok')
  } catch (e) { error.value = String(e) } finally { busy.value = false }
}
async function merge(item: ProductionItem) {
  const project = app.current
  if (!project) return
  merging.value = item.id; error.value = ''
  try {
    const r = await postJSON<{ok: boolean; version_name?: string; err?: string}>('/api/production/redo_merge',
      {project, board: board.value, target: unitId.value, item_id: item.id})
    if (!r.ok) throw new Error(r.err || '合并失败')
    toast(`已合并为 ${r.version_name}（候选，未自动采用）`, 'ok'); await load()
  } catch (e) { error.value = String(e) } finally { merging.value = null }
}
function redoTag(item: ProductionItem) {
  if (!item.redo) return ''
  return item.redo.merged ? `${item.redo.version_name || '已合并'}` : `重拍 ${item.redo.t0}–${item.redo.t1}s · 待合并`
}
</script>

<template>
  <div class="page-wide">
    <header class="mb-5 flex items-end justify-between"><div><h1 class="grad-text text-2xl font-black">片段重拍</h1><p class="mt-1 text-xs text-slate-500">选 V → 选版本 → 抽帧选首尾（可空）→ 重拍 → 一键合并，落库为 V标签_首_尾_vN 新候选；整段 V 重做请走⑦创作生成</p></div></header>
    <p v-if="error" role="alert" class="mb-4 rounded-lg bg-rose-950/50 p-3 text-rose-200">{{ error }}</p>
    <label class="mb-4 block max-w-md text-xs text-slate-400">分镜<select v-model="board" class="control mt-1"><option v-for="b in boards" :key="b">{{ b }}</option></select></label>
    <div class="grid items-start gap-5 lg:grid-cols-[230px_minmax(0,1fr)]">
      <aside class="glass space-y-2 p-3">
        <h2 class="mb-2 text-sm font-bold">V 列表</h2>
        <button v-for="u in units" :key="u.id" class="block w-full rounded-xl border p-3 text-left" :class="u.id === unitId ? 'border-sky-400/50 bg-sky-900/20' : 'border-white/10'" @click="unitId = u.id">
          <b>{{ u.label }} · {{ u.title }}</b>
          <small class="mt-1 block text-slate-400">{{ u.duration }}s · {{ u.video_binding ? (u.video_stale ? '已采用·待确认' : '已采用') : '待生成' }}</small>
        </button>
        <p v-if="!units.length" class="text-xs text-slate-500">该分镜暂无 V 分组，请先到⑦创作生成建立分组。</p>
      </aside>
      <main class="space-y-5 min-w-0">
        <section class="glass space-y-3 p-4">
          <h2 class="font-bold text-sky-200">{{ unit ? `${unit.label} · 视频版本` : '请选择 V' }} <span class="text-xs font-normal text-slate-500">点「拉片抽帧」选一个版本作为重拍底片</span></h2>
          <div class="grid gap-3 xl:grid-cols-2">
            <article v-for="i in versions" :key="i.id" class="rounded-xl border p-3" :class="sourceItem?.id === i.id ? 'border-sky-400/60 bg-sky-900/10' : 'border-white/10'">
              <p class="mb-2 text-xs text-slate-400">{{ i.created_at }} · {{ i.status }}<template v-if="i.actual_duration"> · {{ i.actual_duration }}s</template>
                <span v-if="i.redo" class="ml-1 rounded px-1.5 py-0.5 text-[10px]" :class="i.redo.merged ? 'bg-emerald-900/60 text-emerald-200' : 'bg-violet-900/60 text-violet-200'">{{ redoTag(i) }}</span>
                <span v-if="unit?.video_binding && outputPath(unit.video_binding as unknown as ProductionItem) === outputPath(i)" class="ml-1 rounded bg-sky-900/60 px-1.5 py-0.5 text-[10px] text-sky-200">已采用</span>
              </p>
              <video :src="fileUrl(outputPath(i))" controls preload="none" class="my-2 w-full rounded-lg bg-black" />
              <div class="flex flex-wrap gap-2">
                <button class="btn btn-sm" :disabled="i.status !== 'done' || framesBusy" @click="extractFrames(i)">{{ sourceItem?.id === i.id ? '重新拉片抽帧' : '拉片抽帧（设为底片）' }}</button>
                <button v-if="i.action === 'redo_segment' && i.redo && !i.redo.merged && i.status === 'done'" class="btn btn-sm" :disabled="merging === i.id" @click="merge(i)">{{ merging === i.id ? '合并中…' : '一键合并' }}</button>
              </div>
            </article>
            <p v-if="!versions.length" class="text-xs text-slate-500">该 V 暂无视频版本，请先到⑦创作生成生成。</p>
          </div>
        </section>
        <section v-if="sourceItem" class="glass space-y-3 p-4">
          <h2 class="font-bold text-sky-200">重拍窗口 <span class="text-xs font-normal text-slate-500">底片 {{ sourceItem.created_at }} · 首 {{ headT ?? '空（片头）' }}s · 尾 {{ tailT ?? '空（片末）' }}s</span></h2>
          <p v-if="framesBusy" class="text-xs text-slate-400">抽帧拉片中…</p>
          <div v-else-if="frames" class="space-y-3">
            <div class="flex gap-2 overflow-x-auto pb-2">
              <figure v-for="f in frames.frames" :key="f.path" class="shrink-0">
                <img :src="frameUrl(f.path)" class="w-[112px] rounded-md border-2" :class="headT === f.t ? 'border-sky-400' : tailT === f.t ? 'border-rose-400' : 'border-transparent'" />
                <figcaption class="mt-1 flex items-center justify-between text-[10px] text-slate-400">
                  <span>{{ f.t }}s</span>
                  <span class="flex gap-1">
                    <button class="rounded bg-sky-900/60 px-1 text-sky-200" @click="setHead(f.t)">首</button>
                    <button class="rounded bg-rose-900/60 px-1 text-rose-200" @click="setTail(f.t)">尾</button>
                  </span>
                </figcaption>
              </figure>
            </div>
            <p class="text-xs text-slate-500">抽帧步长 {{ frames.step }}s · 首空=从片头开始，尾空=拍到片末；首尾都空须走整段重做渠道。</p>
            <textarea v-model="prompt" class="control" rows="3" placeholder="重拍提示词（默认带出该 V 的视频提示词，可改）"></textarea>
            <div class="flex flex-wrap items-center gap-2">
              <select v-model="vendor" class="control max-w-[220px]"><option v-for="v in vendors" :key="v.id" :value="v.id">{{ v.label || v.id }}</option></select>
              <button class="btn" :disabled="busy || !vendor" @click="reshoot">{{ busy ? '提交中…' : `重拍 ${headT ?? 0}–${tailT ?? (frames.duration || 0)}s` }}</button>
            </div>
          </div>
        </section>
        <section v-if="pending.length" class="glass space-y-2 p-4">
          <h2 class="font-bold text-sky-200">待合并片段</h2>
          <p class="text-xs text-slate-500">重拍完成后在此一键合并：ffmpeg 把「底片[0,首] + 新片段 + 底片[尾,末]」拼回，落库为 V标签_首_尾_vN 新候选。</p>
        </section>
      </main>
    </div>
  </div>
</template>
