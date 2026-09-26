<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { app, projectFiles, toast } from '../stores/app'
import { trackJob } from '../stores/jobs'
import { fetchCreate, fetchEnvConfig, getJSON, mediaUrl, postJSON, type Vendor } from '../api'
import { reconcilePending, studioData, studioPost, submitStudioJob, submitRedoJob, type StudioState, type VideoUnit, type ProductionShot, type ProductionItem } from '../utils/productionStudio'
import { useBoardSelection } from '../utils/useBoardSelection'
import VideoSettings from '../components/VideoSettings.vue'
import MediaReferences from '../components/MediaReferences.vue'
import OverlayViewer from '../components/OverlayViewer.vue'
import Versions from '../components/Versions.vue'
import type { VideoSettingsValue } from '../utils/videoSettings'
import { defaultShotPrompt, defaultUnitPrompt, retimeUnit } from '../utils/shotPromptEditor'

const board = ref(''), data = ref<StudioState | null>(null), vendors = ref<Vendor[]>([]), items = ref<ProductionItem[]>([])
const boards = computed(() => projectFiles('分镜', /\.json$/))
useBoardSelection(board, boards, 'create')
const selectedUnit = ref(''), selectedShot = ref(''), scope = ref<'S' | 'V'>('V'), kind = ref<'image' | 'video'>('video')
const vendor = ref(''), textVendor = ref(''), visionVendor = ref(''), busy = ref(false), dirty = ref(false), duration = ref(5)
const refMode = ref('keyframes'), tailMode = ref(''), tailItem = ref(''), includeVoices = ref(true), error = ref('')
const videoOptions = ref<VideoSettingsValue>({mode:'reference'}), firstShot = ref(''), lastShot = ref('')
const imageUrls = ref<Record<string,string>>({}), audioUrls = ref(''), videoUrls = ref('')
const currentShots = computed(() => scope.value === 'S' ? (shot.value ? [shot.value] : []) : shots.value)
const referenceAudio = computed(() => capability.value?.audio_refs && videoOptions.value.mode === 'reference')
const canBindVoices = computed(() => referenceAudio.value && !['agnes','aliyun'].includes(vendor.value))
const urlLines = (value:string) => value.split(/\r?\n/).map(s => s.trim()).filter(Boolean)
const promptFields = [{key: 'prompt_image', label: '参考帧提示词'}, {key: 'prompt_video', label: '视频提示词'}, {key: 'prompt_grid', label: '宫格提示词'}] as const
const optimizing = ref('')
const units = computed(() => data.value?.board.video_units || [])
const allShots = computed(() => data.value?.board.shots || [])
const unit = computed(() => units.value.find(u => u.id === selectedUnit.value))
const shots = computed(() => unit.value ? allShots.value.filter(s => unit.value!.shot_ids.includes(s.id)) : allShots.value)
const shot = computed(() => allShots.value.find(s => s.id === selectedShot.value))
const models = computed(() => vendors.value.filter(v => v.enabled && (v.models[kind.value] || (kind.value === 'image' && v.models.image_edit))))
const textModels = computed(() => vendors.value.filter(v => v.enabled && v.models.text))
const visionModels = computed(() => vendors.value.filter(v => v.enabled && v.models.vision))
const capability = computed(() => data.value?.capabilities[vendor.value])
const currentTarget = computed(() => scope.value === 'S' ? selectedShot.value : selectedUnit.value)
const candidates = computed(() => items.value.filter(i => i.board === board.value && i.type === kind.value && (scope.value === 'S' ? i.shot_id === selectedShot.value : i.unit_id === selectedUnit.value)))
const videos = computed(() => items.value.filter(i => i.type === 'video' && i.status === 'done'))
const assetTokens = computed(() => [...new Set(shots.value.flatMap(s => [s.scene_ref, ...(s.actor_refs || []), ...(s.prop_refs || [])]).filter((x): x is string => !!x))])
// ── 引用素材全量小图：未生成的标出并提供「生成」（复用素材生成链路 /api/asset/image）──
interface AssetRow {kind: string; id: string; name: string; path?: string}
const assetMap = ref<Map<string, AssetRow>>(new Map()), genningAsset = ref('')
const KIND_CN: Record<string, string> = {scene: '场景', character: '人物', prop: '道具', style: '画风'}
const assetRefs = computed(() => {
  const out: {token: string; kind: string; id: string; name: string; image: string}[] = []
  for (const token of assetTokens.value) {
    const m = /^@(\w+):(.+)$/.exec(token)
    const kind = m?.[1] || '', id = m?.[2] || token
    const row = assetMap.value.get(`@${kind}:${id}`)
    out.push({token, kind, id, name: row?.name || id, image: row?.path || ''})
  }
  return out
})
async function loadAssets() {
  if (!app.current) return
  try {
    const r = await getJSON<{ok: boolean; assets: AssetRow[]}>(`/api/assets?project=${encodeURIComponent(app.current)}`)
    const map = new Map<string, AssetRow>()
    for (const a of r.assets || []) map.set(`@${a.kind}:${a.id}`, a)
    assetMap.value = map
  } catch { /* 资产库缺失时全部按未生成处理 */ }
}
async function genAsset(kind: string, id: string) {
  const project = app.current
  if (!project || genningAsset.value) return
  genningAsset.value = `${kind}:${id}`; error.value = ''
  try {
    const r = await postJSON<{ok: boolean; id?: number; err?: string}>('/api/asset/image', {project, kind, id})
    if (!r.ok && r.err) throw new Error(r.err)
    if (r.id) void trackJob(r.id, `素材生成 ${KIND_CN[kind] || kind} ${id}`).then(async () => {
      if (app.current === project) { await loadAssets(); await gallery() }
    })
    toast('素材生成任务已提交，完成后小图自动出现', 'ok')
  } catch (e) { error.value = String(e) } finally { genningAsset.value = '' }
}
// ── S 卡模式：静态参考（关键帧/宫格）| 动态视频；缩略图只保留当前选中 ──
const staticTabBy = ref<Record<string, 'keyframe' | 'grid'>>({})
function staticTabOf(s: ProductionShot): 'keyframe' | 'grid' { return staticTabBy.value[s.id] || 'keyframe' }
function setStaticTab(s: ProductionShot, tab: 'keyframe' | 'grid') { staticTabBy.value = {...staticTabBy.value, [s.id]: tab} }
function latestImage(s: ProductionShot): ProductionItem | undefined {
  return items.value.filter(i => i.type === 'image' && i.status === 'done' && i.shot_id === s.id).at(-1)
}
function keyframeThumb(s: ProductionShot): {path: string; adopted: boolean} | null {
  if (s.keyframe?.path) return {path: s.keyframe.path, adopted: true}
  const latest = latestImage(s)
  return latest ? {path: outputPath(latest), adopted: false} : null
}
function thumbFor(s: ProductionShot): {path: string; adopted: boolean} | null {
  if (staticTabOf(s) === 'grid') { const g = gridCandidateFor(s); if (g) return {path: outputPath(g), adopted: true} }
  if (s.keyframe?.path) return {path: s.keyframe.path, adopted: true}
  const latest = latestImage(s)
  return latest ? {path: outputPath(latest), adopted: false} : null
}
async function genShotImage(s: ProductionShot) { chooseShot(s); if (dirty.value && !await savePrompts()) return; kind.value = 'image'; await job('generate') }

// ── ① 整 V 直出：参考方式（默认故事板宫格）、按 S 重拼、一键生成 ──
function setRefMode(mode: 'keyframes' | 'grid') {
  refMode.value = mode
  const u = unit.value; if (!u) return
  u.generation_options = {ref_mode: refMode.value, tail_mode: tailMode.value, tail_item: tailItem.value, vision_vendor: visionVendor.value, include_voices: includeVoices.value, video_options: videoOptions.value, first_shot_id: firstShot.value, last_shot_id: lastShot.value, image_urls: imageUrls.value, audio_urls: audioUrls.value, video_urls: videoUrls.value}
  dirty.value = true   // 切参考方式只标记待保存：生成前会自动保存，不弹"已保存"打断操作
}
function recomposeUnit() {
  const u = unit.value; if (!u) return
  const sections = u.shot_ids.map(id => allShots.value.find(s => s.id === id)?.prompt_video || '').filter(Boolean)
  const m = /整段补充：([\s\S]*)$/.exec(u.prompt_video || '')
  u.prompt_video = sections.join('\n') + (m ? '\n整段补充：' + m[1] : '')
  dirty.value = true
  toast('已按当前各 S 提示词重拼，尾部整段补充保留', 'ok')
}
async function genV() {
  if (!unit.value) return
  scope.value = 'V'; kind.value = 'video'
  if (dirty.value && !await savePrompts()) return
  await job('generate')
}
function onModeSelect(event: Event, s: ProductionShot) {
  const value = (event.target as HTMLSelectElement).value
  if (value === 'keyframe' || value === 'grid') { setStaticTab(s, value); kind.value = 'image'; rememberSelection() }
}
// 宫格 = 一次生图调用：按整 V 剧情生成多格故事板图（布局九宫格/25宫格写在宫格提示词里）
const gridCandidate = computed(() => items.value.filter(i => (i.action === 'grid' || i.grid) && i.unit_id === selectedUnit.value && i.board === board.value && i.status === 'done').at(-1) || null)
const gridAdopted = computed(() => !!unit.value?.grid_binding && !!gridCandidate.value && unit.value!.grid_binding!.item_id === gridCandidate.value!.id)
async function adoptGrid(item: ProductionItem) {
  const project = app.current
  if (!project || !unit.value) return
  error.value = ''
  try {
    await studioPost('adopt', {...base(), scope: 'V', target: unit.value.id, type: 'grid', item_id: item.id, revision: data.value?.revision})
    await load(); toast('宫格已采用为整 V 直出的参考', 'ok')
  } catch (e) { error.value = String(e) }
}
function gridCandidateFor(s: ProductionShot) { return items.value.filter(i => i.action === 'grid' && i.shot_id === s.id && i.board === board.value && i.status === 'done').at(-1) || null }
async function makeGrid(s?: ProductionShot) {
  const project = app.current
  if (!project) return
  const isShot = !!s
  const target = isShot ? s!.id : unit.value?.id
  if (!target) return
  if (dirty.value && !await savePrompts()) return
  if (!vendor.value) { error.value = '请先在右侧栏选择生图模型'; return }
  busy.value = true; error.value = ''
  try {
    const r = await submitStudioJob({...base(), action: 'grid', vendor_id: vendor.value, scope: isShot ? 'S' : 'V',
      target, type: 'image', prompt_grid: isShot ? (s!.prompt_grid || '') : (unit.value?.prompt_grid || '')})
    if (r.id) void trackJob(r.id, `宫格生成 ${isShot ? s!.id : unit.value?.label || ''}`).then(async () => { if (app.current === project) await load() })
    toast(isShot ? '本 S 宫格任务已提交（一次生图调用）' : '整 V 宫格任务已提交（一次生图调用，混排全部 S）', 'ok')
  } catch (e) { error.value = String(e) } finally { busy.value = false }
}
function pathUrl(path: string) { return mediaUrl(path.startsWith('projects/') ? path : `projects/${app.current}/${path}`) }
// ── 点开大图（复用全站 OverlayViewer）──
const viewer = ref({visible: false, src: '', title: ''})
function openImage(rel: string, title: string) {
  if (!rel) return
  viewer.value = {visible: true, src: pathUrl(rel), title}
}
// ── 运行中的任务显示已运行时长（秒级跳动）──
const nowTick = ref(Date.now())
const tickTimer = window.setInterval(() => { nowTick.value = Date.now() }, 1000)
onBeforeUnmount(() => window.clearInterval(tickTimer))
function elapsedText(item: ProductionItem) {
  if (!['queued', 'running'].includes(item.status)) return ''
  const t = Date.parse(item.created_at || '')
  if (!Number.isFinite(t)) return ''
  const sec = Math.max(0, Math.floor((nowTick.value - t) / 1000))
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60)
  return (h ? h + ':' + String(m).padStart(2, '0') : String(m)) + ':' + String(sec % 60).padStart(2, '0')
}
function outputPath(item: ProductionItem) { const p = item.outputs?.[0] as string | {path: string} | undefined; return typeof p === 'string' ? p : p?.path || '' }
let seq = 0
async function load() {
  const request = ++seq, project = app.current, name = board.value
  if (!project || !name) { data.value = null; return }
  try {
    const result = await studioData(project, name)
    if (request !== seq) return
    data.value = result
    for (const s of result.board.shots) {
      const u = result.board.video_units?.find(u => u.shot_ids.includes(s.id))
      const start = u ? result.board.shots.filter(x => u.shot_ids.slice(0, u.shot_ids.indexOf(s.id)).includes(x.id)).reduce((n, x) => n + x.dur, 0) : 0
      for (const field of promptFields) s[field.key] = defaultShotPrompt(s, field.key, start)
      // 旧三件套模板把 prompt_grid 写成了关键帧式单帧描述（语义错误）：清空，让占位符教学格式生效
      if (/^【S\d+镜（/.test(s.prompt_grid || '')) s.prompt_grid = ''   // 仅清旧三件套模板签名（镜号+镜（），优化产出的分格说明不受影响
    }
    for (const u of result.board.video_units || []) {
      // 宫格文案按需人工配置（仅故事板宫格参考模式使用），默认不回填
      u.prompt_video = defaultUnitPrompt(u, result.board.shots, 'prompt_video')
      retimeUnit(u, result.board.shots)   // timeline 以当前各 S 时长重算，修掉历史陈旧节拍
    }
    if (!result.board.video_units?.some(u => u.id === selectedUnit.value)) selectedUnit.value = result.board.video_units?.[0]?.id || ''
    if (!result.board.shots.some(s => s.id === selectedShot.value)) selectedShot.value = result.board.shots[0]?.id || ''
    dirty.value = false
    syncDuration(); restoreOptions()
  } catch (e) { error.value = String(e) }
}
let galleryBusy = false
async function gallery() {
  const project = app.current
  if (!project || galleryBusy) return
  galleryBusy = true
  try {
    const r = await fetchCreate(project)
    if (project !== app.current) return
    const finished = r.items.some(i => ['done', 'error'].includes(i.status) && items.value.some(old => old.id === i.id && ['queued', 'running'].includes(old.status)))
    items.value = r.items as ProductionItem[]
    if (finished && !dirty.value) await load()
  } catch (e) { if (project === app.current) error.value = '画廊加载失败：' + String(e) }
  finally { galleryBusy = false }
}

function syncDuration() { duration.value = scope.value === 'S' ? shot.value?.video_duration || shot.value?.dur || 5 : unit.value?.duration || 5 }
function restoreOptions() {
  const options = (scope.value === 'S' ? shot.value : unit.value)?.generation_options || {}
  videoOptions.value = options.video_options || {mode:'reference'}
  firstShot.value = options.first_shot_id || currentShots.value[0]?.id || ''; lastShot.value = options.last_shot_id || currentShots.value.at(-1)?.id || ''
  imageUrls.value = {...options.image_urls}; audioUrls.value = options.audio_urls || ''; videoUrls.value = options.video_urls || ''
  refMode.value = options.ref_mode || 'grid'   // 整 V 直出默认故事板宫格（A 路线推荐）; tailMode.value = options.tail_mode || ''; tailItem.value = options.tail_item || ''
  visionVendor.value = options.vision_vendor || visionModels.value[0]?.id || ''; includeVoices.value = options.include_voices ?? true
}
function selectionKey() { return `slate:production-target:${app.current}:${board.value}` }
function rememberSelection() { localStorage.setItem(selectionKey(), JSON.stringify({unit: selectedUnit.value, shot: selectedShot.value, scope: scope.value, kind: kind.value})) }
function chooseUnit(u: VideoUnit) { selectedUnit.value = u.id; scope.value = 'V'; syncDuration(); restoreOptions(); rememberSelection() }
function chooseShot(s: ProductionShot) { selectedShot.value = s.id; scope.value = 'S'; syncDuration(); restoreOptions(); rememberSelection() }
async function saveOptions() {
  const target = scope.value === 'S' ? shot.value : unit.value; if (!target) return
  target.generation_options = {ref_mode: refMode.value, tail_mode: tailMode.value, tail_item: tailItem.value, vision_vendor: visionVendor.value, include_voices: includeVoices.value, video_options:videoOptions.value, first_shot_id:firstShot.value,last_shot_id:lastShot.value,image_urls:imageUrls.value,audio_urls:audioUrls.value,video_urls:videoUrls.value}
  dirty.value = true
  await savePrompts()
}
function base() { return {project: app.current, board: board.value, revision: data.value?.revision} }
async function savePrompts() {
  busy.value = true
  try { await studioPost('save', {...base(), shots: allShots.value, units: units.value.length ? units.value : undefined}); await load(); toast('提示词与时长已保存', 'ok'); return true }
  catch (e) { error.value = String(e); return false } finally { busy.value = false }
}
function editShotDuration(s: ProductionShot, event: Event) {
  const value = (event.target as HTMLInputElement).valueAsNumber
  if (!Number.isFinite(value) || value <= 0) return
  s.dur = value; s.video_duration = value
  for (const u of units.value.filter(u => u.shot_ids.includes(s.id))) retimeUnit(u, allShots.value)
  syncDuration(); dirty.value = true
}
async function optimize(targetScope: 'S' | 'V', target: string, field: string) {
  if (optimizing.value) return
  if (!await savePrompts()) return
  const project = app.current, name = board.value
  optimizing.value = `${target}:${field}`; error.value = ''
  try {
    const r = await submitStudioJob({...base(), action: 'optimize', vendor_id: textVendor.value, scope: targetScope, target, field})
    if (r.id) {
      const result = await trackJob(r.id, '优化提示词')
      if (!result.success) throw new Error(result.err || '优化失败，原文已保留')
    }
    if (app.current === project && board.value === name) {
      // 精确回填：只把优化后的字段写进当前编辑对象，不动其他未保存修改
      // （否则有编辑时跳过整页刷新，字段看着像没生成）
      const r2 = await studioData(project, name)
      if (app.current !== project || board.value !== name) return
      const rows = (targetScope === 'S' ? r2.board.shots : r2.board.video_units || []) as unknown as Record<string, unknown>[]
      const fresh = rows.find(x => x.id === target)?.[field]
      const pool = (targetScope === 'S' ? allShots.value : units.value) as unknown as Record<string, unknown>[]
      const local = pool.find(x => x.id === target)
      if (local && typeof fresh === 'string') local[field] = fresh
      if (!dirty.value) await load()
      else toast('优化完成，已回填当前字段', 'ok')
    }
  } catch (e) { error.value = String(e) } finally { optimizing.value = '' }
}
async function saveUnits(confirm = false, values = units.value) {
  busy.value = true
  try { await studioPost('save', {...base(), shots: allShots.value, units: values, confirm_summary: confirm ? unit.value?.id : undefined}); await load() }
  catch (e) { error.value = String(e) } finally { busy.value = false }
}
async function initialize() {
  try { await studioPost('save', {...base(), initialize: true}); await load() } catch (e) { error.value = String(e) }
}
async function saveDuration() {
  if (scope.value === 'S' && shot.value) shot.value.video_duration = duration.value
  if (scope.value === 'V' && unit.value) unit.value.duration = duration.value
  await savePrompts()
}
function editDuration(event: Event) {
  duration.value = (event.target as HTMLInputElement).valueAsNumber
  if (scope.value === 'S' && shot.value) shot.value.video_duration = duration.value
  if (scope.value === 'V' && unit.value) unit.value.duration = duration.value
  dirty.value = true
}
async function split(sid: string) {
  const u = unit.value; if (!u) return
  const at = u.shot_ids.indexOf(sid); if (at < 1) return
  const parts = [u.shot_ids.slice(0, at), u.shot_ids.slice(at)].map(ids => ({id: crypto.randomUUID(), title: u.title, scene_ref: u.scene_ref,
    shot_ids: ids, duration: allShots.value.filter(s => ids.includes(s.id)).reduce((n, s) => n + s.dur, 0), source_hash: ''}))
  await saveUnits(false, units.value.flatMap(v => v.id === u.id ? parts : [v]))
}
async function mergeNext() {
  const u = unit.value, index = units.value.findIndex(v => v.id === u?.id), next = units.value[index + 1]
  if (!u || !next) return
  const merged = {...u, shot_ids: [...u.shot_ids, ...next.shot_ids], duration: u.duration + next.duration, source_hash: '', video_binding: undefined}
  await saveUnits(false, units.value.flatMap(v => v.id === u.id ? [merged] : v.id === next.id ? [] : [v]))
}
async function job(action: string, recover = false) {
  if (action === 'group' && dirty.value && !recover && !await savePrompts()) return
  if (dirty.value && !recover) { error.value = '请先保存已编辑内容'; return }
  const project = app.current
  const body = {...base(), action, vendor_id: ['group', 'prompts'].includes(action) ? textVendor.value : vendor.value,
    scope: scope.value, target: currentTarget.value, type: kind.value,
    ...(kind.value === 'video' ? {duration: duration.value, ref_mode: videoOptions.value.mode === 'reference' ? refMode.value : 'keyframes', include_voices: includeVoices.value && !!canBindVoices.value, video_options:videoOptions.value, first_shot_id:firstShot.value,last_shot_id:lastShot.value,image_urls:imageUrls.value, audio_urls:videoOptions.value.mode === 'reference' && capability.value?.max_audio ? urlLines(audioUrls.value) : [],video_urls:videoOptions.value.mode === 'reference' && capability.value?.max_video ? urlLines(videoUrls.value) : [],
      continuity: tailMode.value && (tailMode.value !== 'tail_first_frame' || ['first_frame','first_last'].includes(videoOptions.value.mode || '')) ? {mode: tailMode.value, item_id: tailItem.value, vision_vendor: visionVendor.value} : {}} : {})}
  busy.value = true; error.value = ''
  try {
    const result = await submitStudioJob(body, recover)
    await gallery()
    if (result.id) void trackJob(result.id, action === 'generate' ? `${scope.value} ${currentTarget.value} ${kind.value === 'image' ? '关键帧' : '视频'}` : '制作编排').then(async () => {
      if (app.current === project) { await gallery(); if (!dirty.value) await load(); else toast('后台完成，请保存当前编辑后刷新', 'info') }
    })
    toast(result.reused ? '已复用同一请求' : '后台任务已提交，可继续编辑', 'ok')
  } catch (e) { error.value = String(e) } finally { busy.value = false }
}
async function adopt(item: ProductionItem) {
  try { await studioPost('adopt', {...base(), scope: scope.value, target: currentTarget.value, type: kind.value, item_id: item.id}); await load() }
  catch (e) { error.value = String(e) }
}
async function delItem(item: ProductionItem) {
  const project = app.current
  if (!project || !confirm('删除该候选及其产物文件？已采用/被重拍引用的产物会被拒绝。')) return
  error.value = ''
  try {
    const r = await postJSON<{ok: boolean; err?: string}>('/api/production/item/delete', {project, board: board.value, item_id: item.id})
    if (!r.ok) throw new Error(r.err || '删除失败')
    toast('候选已删除', 'ok'); await gallery()
  } catch (e) { error.value = String(e) }
}
// ── 局部修补（重做片段）：抽已采用 V 视频的 t0/t1 锚点帧 → 首尾帧生成中段 → 自动拼回 → 新候选（人工采用照旧）
const redoOpen = ref(false), redoT0 = ref(0), redoT1 = ref(0), redoPrompt = ref(''), redoBusy = ref(false), redoVideoDur = ref(0)
const redoLength = computed(() => Math.max(0, Math.round((redoT1.value - redoT0.value) * 10) / 10))
function openRedo() {
  const u = unit.value
  if (!u?.video_binding) return
  redoT0.value = 0; redoT1.value = 0; redoVideoDur.value = 0   // 末点等视频元数据到达后填实际时长
  redoPrompt.value = u.prompt_video || ''
  redoOpen.value = true
}
function onRedoMeta(event: Event) {
  const el = event.target as HTMLVideoElement
  if (Number.isFinite(el.duration) && el.duration > 0) {
    redoVideoDur.value = el.duration
    if (!redoT1.value || redoT1.value > el.duration) redoT1.value = Math.round(el.duration * 10) / 10
  }
}
async function submitRedo() {
  const u = unit.value, project = app.current, name = board.value
  if (!u?.video_binding || redoBusy.value) return
  if (!Number.isFinite(redoT0.value) || !Number.isFinite(redoT1.value) || redoT0.value < 0 || redoT1.value <= redoT0.value) { error.value = '修补窗口需满足 0 ≤ 起点 < 终点'; return }
  if (redoVideoDur.value && redoT1.value > redoVideoDur.value + 0.05) { error.value = `修补终点超出视频实际时长 ${redoVideoDur.value.toFixed(1)}s`; return }
  redoBusy.value = true; error.value = ''
  const video_options: Record<string, unknown> = {}
  if (videoOptions.value.resolution) video_options.resolution = videoOptions.value.resolution
  if (videoOptions.value.ratio) video_options.ratio = videoOptions.value.ratio
  try {
    const result = await submitRedoJob({project, board: name, target: u.id, t0: redoT0.value, t1: redoT1.value,
      prompt: redoPrompt.value, vendor_id: vendor.value, video_options})
    redoOpen.value = false
    await gallery()
    if (result.id) void trackJob(result.id, `局部修补 ${redoT0.value}–${redoT1.value}s`).then(async () => {
      if (app.current === project) { await gallery(); if (!dirty.value) await load(); else toast('后台完成，请保存当前编辑后刷新', 'info') }
    })
    toast(result.reused ? '已复用同一请求' : '修补任务已提交，产出为新候选待人工采用', 'ok')
  } catch (e) { error.value = String(e) } finally { redoBusy.value = false }
}
watch([() => app.current, board], () => {
  error.value = ''
  try { const old = JSON.parse(localStorage.getItem(selectionKey()) || '{}'); selectedUnit.value = old.unit || ''; selectedShot.value = old.shot || ''; scope.value = old.scope === 'S' ? 'S' : 'V'; kind.value = old.kind === 'image' ? 'image' : 'video' } catch { /* 选择记录损坏不影响分镜源 */ }
  void load(); void gallery(); void loadAssets(); void reconcilePending(String(app.current || ''))
}, {immediate: true})
watch(kind, rememberSelection)
watch(board, name => { if (app.current && name) localStorage.setItem(`wb.${app.current}.create.board`, name) })
// 生成模型选择持久化（按项目 + 类型记忆）：选过就保存，加载时优先恢复上次选择，失效才回退第一项
watch(models, rows => {
  if (!rows.length) return
  const key = `wb.${app.current}.studio.vendor.${kind.value}`
  const saved = app.current ? localStorage.getItem(key) || '' : ''
  if (!rows.some(v => v.id === vendor.value)) vendor.value = rows.some(v => v.id === saved) ? saved : rows[0]?.id || ''
})
watch(vendor, v => { if (v && app.current) localStorage.setItem(`wb.${app.current}.studio.vendor.${kind.value}`, v) })
watch(textModels, rows => {
  if (!rows.length) return
  const saved = app.current ? localStorage.getItem(`wb.${app.current}.studio.textVendor`) || '' : ''
  if (!rows.some(v => v.id === textVendor.value)) textVendor.value = rows.some(v => v.id === saved) ? saved : rows[0]?.id || ''
})
watch(textVendor, v => { if (v && app.current) localStorage.setItem(`wb.${app.current}.studio.textVendor`, v) })
void fetchEnvConfig().then(r => {
  vendors.value = r.vendors
  if (!visionModels.value.some(v => v.id === visionVendor.value)) visionVendor.value = visionModels.value[0]?.id || ''
})
const timer = window.setInterval(() => { if (items.value.some(i => ['queued', 'running'].includes(i.status))) void gallery() }, 5000)
onBeforeUnmount(() => window.clearInterval(timer))
</script>

<template>
  <div class="page-wide production-studio">
    <header class="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div><h1 class="grad-text text-2xl font-black">⑦ 创作生成</h1><p class="mt-1 text-xs text-slate-500">S 转场镜头 → V 分镜视频 → E 集视频</p></div>
      <nav class="flex gap-3 text-xs text-sky-300"><RouterLink to="/studio/shots">分镜生成</RouterLink><RouterLink to="/studio/asset/voices">角色音色</RouterLink><RouterLink to="/create/free">自由创作 · 音乐 · 全部画廊</RouterLink></nav>
    </header>
    <div v-if="error" role="alert" class="mb-3 rounded-xl bg-rose-950/40 p-3 text-sm text-rose-200">{{ error }}<button class="ml-3" @click="error = ''">×</button></div>
    <!-- 制作规格（E05）：V 总时长超过单集目标时后端 state 给出提示 -->
    <div v-if="data?.brief_notice" class="mb-3 rounded-xl border border-amber-400/30 bg-amber-400/10 px-3 py-2 text-xs-plus text-amber-200">！{{ data.brief_notice }}</div>
    <p v-if="!app.current" class="p-10 text-slate-400">请先选择项目。</p>
    <div v-else class="studio-columns">
      <aside class="glass studio-sidebar">
        <label class="block text-xs text-slate-400">分集 / 分镜 JSON<select v-model="board" class="control mt-2"><option v-for="b in boards" :key="b">{{ b }}</option></select></label>
        <label class="block text-xs text-slate-400">编排模型<select v-model="textVendor" class="control mt-1"><option v-for="v in textModels" :key="v.id" :value="v.id">{{ v.models.text }}</option></select></label>
        <button class="btn w-full" :disabled="busy || !board || !textVendor" @click="job('group')">自动合并分镜</button>
        <button v-if="!units.length" class="btn btn-ghost w-full" :disabled="!board" @click="initialize">先按场景建立 V 分组</button>
        <button v-for="u in units" :key="u.id" class="unit-card" :class="{'selected': u.id === selectedUnit}" @click="chooseUnit(u)">
          <b>{{ u.label }} · {{ u.title }}<span v-if="u.judge?.warnings?.length" class="ml-1 cursor-help text-amber-300" :title="u.judge.warnings.join('\n')">！</span></b><small>{{ u.shot_ids.join(' · ') }} · {{ u.duration }}s</small>
          <small class="unit-status" :class="u.stale || u.video_stale ? 'is-pending' : 'is-ready'">{{ u.stale ? '汇总待更新' : u.video_stale ? '已采用视频待确认' : u.video_binding ? '已采用视频' : '待生成视频' }}</small>
        </button>
      </aside>
      <main class="glass min-w-0 space-y-4 p-4">
        <section v-if="unit" class="space-y-3 rounded-xl border border-sky-400/30 bg-sky-950/20 p-4">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <h2 class="text-base font-black text-sky-200">① 整 V 直出 <span class="text-xs font-normal text-slate-400">{{ unit.label }} · 一次调用生成本 V 整段 {{ unit.duration }}s 视频</span></h2>
            <span v-if="unit.judge?.warnings?.length" class="cursor-help text-xs text-amber-300" :title="unit.judge.warnings.join('\n')">！提示词可能无法完整生成（悬浮看详情）</span>
          </div>
          <label class="block text-xs text-slate-400">V 标题<input v-model="unit.title" class="control mt-1" @input="dirty = true" /></label>
          <label class="block text-xs text-slate-400">整 V 直出提示词<button class="ml-2 text-sky-300" :disabled="busy || !!optimizing || !textVendor" @click="optimize('V', unit.id, 'prompt_video')">{{ optimizing === unit.id + ':prompt_video' ? '优化中…' : '重新生成 / 优化' }}</button><button class="ml-2 text-xs text-cyan-200" :disabled="busy" title="丢弃当前 V 汇总文本，用下方各 S 的视频提示词重新拼接（保留尾部整段补充），修掉历史叠加的重复" @click="recomposeUnit">按 S 重拼（去重复）</button><textarea v-model="unit.prompt_video" class="control mt-1" rows="6" @input="dirty = true" /></label>
          <div class="space-y-2">
            <div class="flex flex-wrap gap-2">
              <button class="ref-choice flex-1" :class="{active: refMode === 'grid'}" :disabled="busy" @click="setRefMode('grid')">故事板宫格（推荐）</button>
              <button class="ref-choice flex-1" :class="{active: refMode === 'keyframes'}" :disabled="busy" @click="setRefMode('keyframes')">按 S 关键帧序列</button>
            </div>
            <div v-if="refMode === 'keyframes'" class="space-y-2">
              <p class="text-xs text-slate-500">关键帧没有 V 级提示词——每张关键帧在 ② 分 S 出镜对应 S 卡里生成（各有自己的提示词），此处按 S 顺序引用：</p>
              <div class="flex flex-wrap gap-2">
              <template v-for="s in shots" :key="s.id">
                <figure class="w-[124px]">
                  <img v-if="keyframeThumb(s)" :src="pathUrl(keyframeThumb(s)!.path)" class="aspect-video w-full cursor-zoom-in rounded-md border border-white/10 object-cover" :alt="s.id" title="点击查看大图" @click="openImage(keyframeThumb(s)!.path, s.id + ' 关键帧')" />
                  <div v-else class="flex aspect-video w-full flex-col items-center justify-center gap-1 rounded-md border border-dashed border-white/10 text-slate-500">
                    <span class="text-[10px] text-amber-300">尚未生成</span>
                    <button class="btn btn-sm" :disabled="busy || !vendor" @click="genShotImage(s)">{{ busy ? '…' : '生成' }}</button>
                  </div>
                  <figcaption class="mt-0.5 flex items-center justify-between text-[10px]">
                    <span :class="keyframeThumb(s)?.adopted ? 'text-emerald-300' : keyframeThumb(s) ? 'text-amber-300' : 'text-slate-500'">{{ s.id }}{{ keyframeThumb(s) ? (keyframeThumb(s)!.adopted ? ' 已采用' : ' 候选') : '' }}</span>
                    <button v-if="keyframeThumb(s) && !keyframeThumb(s)!.adopted" class="text-sky-300" @click="chooseShot(s); adopt(latestImage(s)!)">采用</button>
                  </figcaption>
                </figure>
              </template>
              </div>
            </div>
            <div v-else class="space-y-2">
              <label class="block text-xs text-slate-400">宫格提示词（分格布局说明，留空 = 默认九宫格 3×3 按剧情时间顺序）<button class="ml-2 text-sky-300" :disabled="busy || !!optimizing || !textVendor" @click="optimize('V', unit.id, 'prompt_grid')">{{ optimizing === unit.id + ':prompt_grid' ? '优化中…' : '重新生成 / 优化' }}</button><textarea v-model="unit.prompt_grid" class="control mt-1" rows="2" placeholder="例：九宫格 3×3；格1 叩首、格2 拎匣入场、格3 递牌…所有格人物一致、格内无文字" @input="dirty = true" /></label>
              <div class="flex flex-wrap items-start gap-2">
                <figure v-if="gridCandidate" class="w-[124px]">
                  <div class="aspect-video w-full overflow-hidden rounded-md border bg-black" :class="gridAdopted ? 'border-emerald-400/60' : 'border-white/10'">
                    <img :src="pathUrl(outputPath(gridCandidate))" class="h-full w-full cursor-zoom-in object-cover" alt="故事板宫格" title="点击查看大图" @click="openImage(outputPath(gridCandidate), (unit?.label || '') + ' 故事板宫格')" />
                  </div>
                  <p class="mt-0.5 text-[10px]" :class="gridAdopted ? 'text-emerald-300' : 'text-amber-300'">{{ gridAdopted ? '已采用为宫格参考' : '候选 · 未采用' }}</p>
                  <button v-if="!gridAdopted" class="mt-1 block text-[10px] text-sky-300" :disabled="busy" @click="adoptGrid(gridCandidate)">采用为宫格参考</button>
                </figure>
                <div v-else class="flex aspect-video w-full max-w-[240px] flex-col items-center justify-center gap-1 rounded-md border border-dashed border-white/10 text-slate-500">
                  <span class="text-xs text-amber-300">尚未生成</span>
                  <button class="btn btn-sm" :disabled="busy || !vendor" @click="makeGrid()">{{ busy ? '生成中…' : '生成' }}</button>
                </div>
                <div class="space-y-1 self-center">
                  <button v-if="gridCandidate" class="btn btn-sm btn-ghost" :disabled="busy || !vendor" @click="makeGrid()">重新生成宫格</button>
                  <p class="text-xs text-slate-500">一次生图调用：按整 V 剧情生成多格故事板图；采用后才作为整 V 视频的参考。生图模型：{{ vendor || '未选择' }}（右侧栏可换）。</p>
                </div>
              </div>
              <p class="text-xs text-slate-500">一次生图调用：按整 V 剧情生成多格故事板图，作为整 V 视频的参考。生图模型：{{ vendor || '未选择' }}（右侧栏可换）。</p>
            </div>
          </div>
          <div class="flex flex-wrap items-center gap-2">
            <button class="btn" :disabled="busy || !vendor || !capability?.known" @click="genV()">{{ busy ? '提交中…' : `▶ 生成整 V 视频（${unit.duration}s）` }}</button>
            <span v-for="beat in unit.timeline" :key="beat.shot_id" class="rounded-lg bg-sky-950/40 px-2 py-1 text-xs text-sky-200">{{ beat.shot_id }} · {{ beat.start }}–{{ beat.end }}s</span>
          </div>
        </section>
        <div v-if="unit" class="space-y-3">
        <section class="space-y-2 rounded-xl border border-white/10 bg-black/20 p-4">
          <h2 class="text-sm font-black text-slate-300">V 汇总与管理 <span class="text-xs font-normal text-slate-500">结构操作，与生成无关</span></h2>
          <p class="text-xs text-slate-400">当前场景：{{ unit.scene_ref || '请在分镜生成中补充 scene_ref' }}</p>
          <div class="flex flex-wrap items-center gap-2">
            <button class="btn btn-sm" :disabled="busy" title="S 改动后 V 标记「汇总待更新」；确认把各 S 最新内容收进本 V，确认后才能整 V 生成" @click="saveUnits(true)">保存并确认 V 汇总</button>
            <button class="btn btn-sm btn-ghost" :disabled="busy" title="把下一个 V 的全部 S 并入本 V（同场景连续段才可合并）" @click="mergeNext">与下一个 V 合并</button>
          </div>
          <label class="block text-xs text-slate-400">本 V 统一负面提示词<textarea v-model="unit.negative" class="control mt-1" rows="2" @input="dirty = true" /></label>
          <p class="text-xs text-slate-500">改动 S 后 V 汇总会标记过期，需重新确认；逐 S 精修在下方「② 分 S 出镜」。</p>
        </section>
      </div>
      <div v-if="unit" class="space-y-3 rounded-xl border border-white/10 p-4">
        <h2 class="text-sm font-black text-slate-300">② 分 S 出镜 <span class="text-xs font-normal text-slate-500">每 S 独立：静态参考（关键帧/宫格）出图，动态视频逐镜直出</span></h2>
        <div class="reference-and-shots">
          <aside class="space-y-2 text-xs"><b class="text-slate-300">本段引用素材</b>
            <div v-for="r in assetRefs" :key="r.token" class="overflow-hidden rounded-lg border border-white/10 bg-black/30">
              <img v-if="r.image" :src="pathUrl(r.image)" class="aspect-video w-full cursor-zoom-in object-contain" :alt="r.name" title="点击查看大图" @click="openImage(r.image, r.name)" />
              <div v-else class="flex aspect-video w-full flex-col items-center justify-center gap-1 text-slate-500">
                <span class="text-amber-300">尚未生成</span>
                <button class="btn btn-sm" :disabled="!!genningAsset" @click="genAsset(r.kind, r.id)">{{ genningAsset === r.kind + ':' + r.id ? '生成中…' : '生成' }}</button>
              </div>
              <small class="block p-1 text-slate-400">{{ KIND_CN[r.kind] || '画风' }} · {{ r.name }}</small>
            </div>
            <p v-if="!assetRefs.length" class="text-slate-500">本段无资产引用</p>
            <RouterLink class="block text-slate-400" to="/studio/asset">查看 / 生成素材 →</RouterLink></aside>
          <section class="min-w-0 space-y-4">
            <article v-for="s in shots" :key="s.id" class="shot-card" :class="{'selected': scope === 'S' && selectedShot === s.id}">
              <header class="mb-3 flex flex-wrap items-center justify-between gap-2"><button class="font-bold text-sky-200" @click="chooseShot(s)">{{ s.id }} 转场镜头</button><label class="text-xs text-slate-400">时长（秒）<input :value="s.dur" type="number" min="0.1" step="0.1" class="control inline-block w-24 ml-2" @input="editShotDuration(s, $event)" /></label><button v-if="unit && unit.shot_ids[0] !== s.id" class="text-xs text-slate-400" @click="split(s.id)">从此镜拆为新 V</button></header>
              <div class="shot-grid">
                <div class="min-w-0 space-y-2">
                  <div class="flex gap-2">
                    <select class="control min-w-0 flex-1" :value="kind === 'image' ? staticTabOf(s) : ''" :aria-label="s.id + ' 静态参考类型'" @change="onModeSelect($event, s)">
                      <option value="" disabled>静态参考</option>
                      <option value="keyframe">静态参考 · 关键帧</option>
                      <option value="grid">静态参考 · 宫格</option>
                    </select>
                    <button class="ref-choice flex-1 text-center" :class="{active: kind === 'video'}" @click="kind = 'video'">动态视频</button>
                  </div>
                  <template v-if="kind === 'image'">
                    <label v-if="staticTabOf(s) === 'keyframe'" class="block text-xs text-sky-300">
                      <span>关键帧提示词</span><button class="ml-3 text-xs text-cyan-200" :aria-label="'优化 ' + s.id + ' 关键帧提示词'" :disabled="busy || !!optimizing || !textVendor" @click="optimize('S', s.id, 'prompt_image')">{{ optimizing === s.id + ':prompt_image' ? '优化中…' : '重新生成 / 优化' }}</button>
                      <textarea v-model="s.prompt_image" :aria-label="s.id + ' 关键帧提示词'" class="control mt-2" rows="4" @input="dirty = true" />
                    </label>
                    <label v-else class="block text-xs text-sky-300">
                      <span>宫格提示词（故事板宫格参考模式的布局说明）</span><button class="ml-3 text-xs text-cyan-200" :aria-label="'优化 ' + s.id + ' 宫格提示词'" :disabled="busy || !!optimizing || !textVendor" @click="optimize('S', s.id, 'prompt_grid')">{{ optimizing === s.id + ':prompt_grid' ? '优化中…' : '重新生成 / 优化' }}</button>
                      <textarea v-model="s.prompt_grid" :aria-label="s.id + ' 宫格提示词'" class="control mt-2" rows="4" placeholder="分格布局说明。例：九宫格 3×3；格1 起手、格2 俯身、格3 按地叩首…所有格人物一致、格内无文字。留空=默认九宫格 3×3 按剧情顺序" @input="dirty = true" />
                    </label>
                  </template>
                  <template v-else>
                    <label class="block text-xs text-sky-300">
                      <span>视频提示词</span><button class="ml-3 text-xs text-cyan-200" :aria-label="'优化 ' + s.id + ' 视频提示词'" :disabled="busy || !!optimizing || !textVendor" @click="optimize('S', s.id, 'prompt_video')">{{ optimizing === s.id + ':prompt_video' ? '优化中…' : '重新生成 / 优化' }}</button>
                      <textarea v-model="s.prompt_video" :aria-label="s.id + ' 视频提示词'" class="control mt-2" rows="4" @input="dirty = true" />
                    </label>
                    <p class="text-xs text-slate-500">视频生成参数与提交入口在右侧栏。</p>
                  </template>
                  <div class="flex flex-wrap gap-2">
                    <button v-if="kind === 'image'" class="btn btn-sm" :disabled="busy || !vendor" @click="staticTabOf(s) === 'keyframe' ? genShotImage(s) : makeGrid(s)">{{ busy ? '提交中…' : (staticTabOf(s) === 'keyframe' ? '生成关键帧' : '生成宫格图（本 S 一次调用）') }}</button>
                    <button class="btn btn-sm btn-ghost" :disabled="busy" @click="savePrompts">保存提示词</button>
                    <button class="btn btn-sm btn-ghost" @click="chooseShot(s)">选择本 S 创作</button>
                  </div>
                  <p class="text-2xs text-slate-500">序号、时段、景别、镜头、运镜、画面（内容/人物/动作/声音/台词）、光影。以当前文字为基础优化。</p>
                </div>
                <aside class="shot-thumb">
                  <template v-if="thumbFor(s)">
                    <img :src="pathUrl(thumbFor(s)!.path)" class="w-full cursor-zoom-in rounded-lg border border-white/10 object-contain" :alt="s.id + (thumbFor(s)!.adopted ? ' 已采用' : ' 候选')" title="点击查看大图" @click="openImage(thumbFor(s)!.path, s.id + (thumbFor(s)!.adopted ? ' 已采用关键帧' : ' 关键帧候选'))" />
                    <div class="mt-1 flex items-center justify-between text-[10px]">
                      <span :class="thumbFor(s)!.adopted ? 'text-emerald-300' : 'text-amber-300'">{{ thumbFor(s)!.adopted ? '当前采用' : '候选 · 未采用' }}</span>
                      <button v-if="!thumbFor(s)!.adopted && latestImage(s)" class="text-sky-300" @click="chooseShot(s); adopt(latestImage(s)!)">采用</button>
                    </div>
                    <Versions v-if="thumbFor(s)!.adopted && s.keyframe" :path="s.keyframe.path" kind="image" @restored="load" />
                  </template>
                  <div v-else class="flex h-full min-h-[120px] items-center justify-center rounded-lg border border-dashed border-white/10 px-2 text-center text-xs text-slate-500">尚未生成关键帧</div>
                </aside>
              </div>
            </article>
          </section>
        </div>
      </div>
      </main>
      <aside class="glass space-y-4 p-4">
        <div><span class="text-xs text-slate-400">当前创作范围</span><h2 class="mt-1 font-bold text-sky-200">{{ scope === 'S' ? `${selectedShot} · 转场镜头` : `${unit?.label || 'V'} · 分镜视频` }}</h2></div>
        <div class="flex gap-2"><button class="btn flex-1" :class="{'opacity-50': kind !== 'image'}" @click="kind = 'image'">参考关键帧</button><button class="btn flex-1" :class="{'opacity-50': kind !== 'video'}" @click="kind = 'video'">视频</button></div>
        <label class="block text-xs text-slate-400">生成模型<select v-model="vendor" class="control mt-1"><option v-for="v in models" :key="v.id" :value="v.id">{{ v.label }} · {{ v.models[kind] }}</option></select></label>
        <p v-if="vendor === 'chatgpt-queue'" class="text-xs text-slate-500">由 <a href="https://github.com/leeguooooo/image-use" target="_blank" rel="noopener">image-use / chrome-use</a> 提供。每个任务独立上传参考图、生成一张并回填。</p>
        <template v-if="kind === 'video'">
          <VideoSettings v-model="videoOptions" :capability="capability" />
          <label v-if="['first_frame','first_last'].includes(videoOptions.mode || '') && tailMode !== 'tail_first_frame'" class="block text-xs text-slate-400">首帧来源<select v-model="firstShot" class="control" @change="saveOptions"><option v-for="s in currentShots" :key="s.id" :value="s.id">{{ s.id }} {{ s.keyframe ? '已采用关键帧' : '尚未采用关键帧' }}</option></select></label>
          <label v-if="['last_frame','first_last'].includes(videoOptions.mode || '')" class="block text-xs text-slate-400">尾帧来源<select v-model="lastShot" class="control" @change="saveOptions"><option v-for="s in currentShots" :key="s.id" :value="s.id">{{ s.id }} {{ s.keyframe ? '已采用关键帧' : '尚未采用关键帧' }}</option></select></label>
          <div v-if="capability?.transport === 'public_url' && videoOptions.mode !== 'text'" class="space-y-2"><label v-for="s in currentShots" :key="s.id" class="block text-xs">{{ s.id }} 对应帧公网 URL<input v-model="imageUrls[s.id]" class="control" placeholder="https://…" @change="saveOptions" /></label></div>
          <div v-if="videoOptions.mode === 'reference'" class="space-y-2 text-xs">
            <p v-if="(!capability?.max_audio && audioUrls) || (!capability?.max_video && videoUrls)" class="text-amber-200">此前填写的不支持媒体已保留在设置中，本型号不会提交这些输入。</p>
            <MediaReferences v-if="capability?.max_audio" :key="currentTarget + 'audio'" v-model="audioUrls" :project="app.current" kind="audio" :limit="capability.max_audio" @change="saveOptions" />
            <MediaReferences v-if="capability?.max_video" :key="currentTarget + 'video'" v-model="videoUrls" :project="app.current" kind="video" :limit="capability.max_video" @change="saveOptions" />
          </div>
          <button class="btn btn-sm" @click="saveOptions">保存视频参数</button>
          <label class="block text-xs text-slate-400">总时长（秒）<input :value="duration" type="number" step="1" min="1" class="control mt-1" @input="editDuration" /></label>
          <button class="btn btn-sm" :disabled="busy || !Number.isFinite(duration) || duration <= 0" @click="saveDuration">保存时长</button>
          <p v-if="capability" class="text-xs text-slate-500">当前适配器：{{ capability.min_duration }}–{{ capability.max_duration }} 秒，最多 {{ capability.max_refs }} 张图</p>
          <section class="reference-options space-y-4 rounded-xl border border-white/10 p-3">
            <h3 class="text-sm font-bold text-sky-200">尾帧 · 音色 <span class="text-2xs font-normal text-slate-500">关键帧/宫格参考方式已在上方「① 整 V 直出」中选择</span></h3>
            <fieldset><legend>尾帧关联</legend><div class="space-y-2 mt-2">
              <button class="ref-choice" :class="{active: tailMode === ''}" :disabled="busy" @click="tailMode = ''; saveOptions()">独立镜头（不关联）</button>
              <button class="ref-choice" :class="{active: tailMode === 'tail_context'}" :disabled="busy" @click="tailMode = 'tail_context'; saveOptions()">尾帧画面参考 · Vision 理解</button>
              <button class="ref-choice" :class="{active: tailMode === 'tail_first_frame'}" :disabled="busy || !['first_frame','first_last'].includes(videoOptions.mode || '')" @click="tailMode = 'tail_first_frame'; saveOptions()">尾帧强制续接 · 作为首帧</button>
              <select v-if="tailMode" v-model="tailItem" class="control" :disabled="busy" @change="saveOptions"><option value="">选择已完成的前序视频</option><option v-for="v in videos" :key="v.id" :value="v.id">{{ v.board }} · {{ v.shot_id || v.unit_id }} · {{ v.created_at }}</option></select>
              <select v-if="tailMode === 'tail_context'" v-model="visionVendor" class="control" :disabled="busy" @change="saveOptions"><option v-for="v in visionModels" :key="v.id" :value="v.id">{{ v.models.vision }}</option></select>
            </div></fieldset>
            <fieldset><legend>角色音色</legend><div class="space-y-2 mt-2">
              <button class="ref-choice" :class="{active: includeVoices && canBindVoices}" :disabled="busy || !canBindVoices" @click="includeVoices = true; saveOptions()">引用角色已绑定音色</button>
              <button class="ref-choice" :class="{active: !includeVoices && canBindVoices}" :disabled="busy || !canBindVoices" @click="includeVoices = false; saveOptions()">不引用音色</button>
              <p v-if="!canBindVoices" class="text-xs text-slate-500">当前模式或素材传输方式不支持直接引用本地音色；可用全能参考的音频 URL，或生成独立台词音轨。</p>
            </div></fieldset>
          </section>
        </template>
        <p v-if="scope === 'V' && kind === 'image'" class="text-xs text-amber-300">请点击中间的 S 转场镜头，选择要生成的关键帧。</p>
        <button class="btn w-full" :disabled="busy || !vendor || !currentTarget || (scope === 'V' && kind === 'image') || (kind === 'video' && !capability?.known)" @click="job('generate')">{{ busy ? '提交中…' : `生成${kind === 'image' ? '关键帧' : '视频'}` }}</button>
        <p class="text-xs text-slate-500">后台执行，可刷新页面。实际时长以生成文件为准；每次产出均保留为候选。</p>
        <button class="text-xs text-sky-300" :disabled="busy" @click="job('recover', true)">接管未确认请求</button>
        <section v-if="scope === 'V' && kind === 'video' && unit?.video_binding" class="space-y-2 rounded-xl border border-white/10 p-3">
          <div class="flex items-center justify-between text-sm"><b>已采用视频</b><span v-if="unit.video_stale" class="text-xs text-amber-300">已采用视频待确认</span></div>
          <video :src="pathUrl(unit.video_binding.path)" controls preload="metadata" class="w-full" />
          <button class="btn btn-sm w-full" :disabled="busy || !vendor" title="抽首尾锚点帧重做中间段并自动拼回原片" @click="openRedo">局部修补（重做片段）</button>
          <p class="text-2xs text-slate-500">修补产出只进候选列表，人工采用后剪辑自然归位。</p>
        </section>
        <section class="space-y-3 border-t border-white/10 pt-4"><div class="flex justify-between text-sm"><b>当前目标产出</b><button @click="gallery">刷新</button></div>
          <article v-for="item in candidates" :key="item.id" class="rounded-lg bg-black/25 p-2"><p class="mb-2 text-xs text-slate-400">{{ item.status }} · {{ item.created_at }} <span v-if="elapsedText(item)" class="ml-1 font-bold text-sky-300">已运行 {{ elapsedText(item) }}</span> <span v-if="item.actual_duration">· {{ item.actual_duration }}s</span><span v-if="item.redo" class="ml-1 rounded bg-violet-900/60 px-1.5 py-0.5 text-violet-200" :title="item.redo.anchors ? `锚点：${item.redo.anchors.head} / ${item.redo.anchors.tail}` : ''">修补 {{ item.redo.t0 }}–{{ item.redo.t1 }}s</span></p>
            <video v-if="item.type === 'video' && outputPath(item)" :src="pathUrl(outputPath(item))" controls preload="metadata" class="w-full" />
            <img v-else-if="outputPath(item)" :src="pathUrl(outputPath(item))" class="w-full cursor-zoom-in" :alt="item.shot_id" title="点击查看大图" @click="openImage(outputPath(item), (item.shot_id || item.unit_id || '') + ' 产出')" />
            <p v-if="item.note" class="mt-2 break-words text-xs text-amber-200">{{ item.note }}</p><div class="mt-2 flex gap-2"><button v-if="item.status === 'done'" class="btn btn-sm" @click="adopt(item)">采用此{{ item.type === 'image' ? '关键帧' : '视频' }}</button><button v-if="!['queued','running'].includes(item.status)" class="btn btn-sm btn-ghost text-rose-300" :title="'删除候选与产物文件；已采用/被重拍引用的会被拒绝'" @click="delItem(item)">删除</button></div>
          </article><p v-if="!candidates.length" class="text-xs text-slate-500">暂无候选</p>
        </section>
      </aside>
    </div>
    <OverlayViewer :visible="viewer.visible" :src="viewer.src" kind="image" :title="viewer.title" @close="viewer.visible = false" />
    <!-- 局部修补面板：视频预览 + 起止时间 + 提示词（预填原 prompt_video 可改），提交走 trackJob 既有模式 -->
    <div v-if="redoOpen && unit?.video_binding" class="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4" @click.self="redoOpen = false">
      <div class="glass w-full max-w-xl space-y-3 p-4">
        <div class="flex items-center justify-between"><b class="text-sky-200">局部修补 · {{ unit.label }}</b><button class="text-slate-400" @click="redoOpen = false">×</button></div>
        <video :src="pathUrl(unit.video_binding.path)" controls preload="metadata" class="w-full rounded-lg" @loadedmetadata="onRedoMeta" />
        <div class="flex gap-3 text-xs text-slate-400">
          <label class="flex-1">起点（秒）<input v-model.number="redoT0" type="number" min="0" step="0.1" class="control mt-1" /></label>
          <label class="flex-1">终点（秒）<input v-model.number="redoT1" type="number" min="0" step="0.1" class="control mt-1" /></label>
        </div>
        <p class="text-2xs text-slate-500">重做 {{ redoT0 }}–{{ redoT1 }}s（{{ redoLength }}s 新片段）：抽两端锚点帧走首尾帧模式生成，成功后自动拼回原片作为新候选；片段时长须落在所选模型的时长范围内（过短会按模型下限报错）。</p>
        <label class="block text-xs text-slate-400">修补提示词<textarea v-model="redoPrompt" rows="4" class="control mt-1" /></label>
        <button class="btn w-full" :disabled="redoBusy || !vendor || !redoLength" @click="submitRedo">{{ redoBusy ? '提交中…' : '提交修补任务' }}</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.reference-options legend{font-size:12px;color:#94a3b8}.ref-choice{display:block;width:100%;text-align:left;padding:9px;border:1px solid #ffffff20;border-radius:8px;font-size:12px;color:#b9c8db;white-space:normal}.ref-choice.active{background:#075985;border-color:#38bdf8;color:#e0f2fe}.ref-choice:disabled{opacity:.45;cursor:not-allowed}

.studio-sidebar{display:flex;flex-direction:column;gap:12px;min-width:0;padding:12px;position:sticky;top:16px;max-height:calc(100dvh - 32px);overflow-y:auto;scrollbar-width:thin}
.studio-sidebar>label{min-width:0}
.studio-sidebar .btn{width:100%;justify-content:center;white-space:normal;overflow-wrap:anywhere;font-size:12px;line-height:1.5;min-height:38px}
.studio-sidebar .control{min-width:0;max-width:100%;text-overflow:ellipsis}
.studio-sidebar .unit-card{flex-shrink:0;cursor:pointer;transition:border-color .15s,background .15s;overflow-wrap:anywhere;color:#dbe5f2}
.studio-sidebar .unit-card:hover{border-color:#38bdf855;background:#12304866}
.studio-sidebar .unit-card.selected{border-color:#38bdf8!important;background:#12304880;box-shadow:inset 3px 0 #38bdf8}
.studio-sidebar .unit-card b{display:block;font-weight:600;line-height:1.6}
.studio-sidebar .unit-status{display:inline-flex;align-items:center;gap:6px;font-weight:500}
.studio-sidebar .unit-status::before{content:'';width:6px;height:6px;flex-shrink:0;border-radius:50%;background:currentColor}
.studio-sidebar .unit-status.is-pending{color:#fcd34d}
.studio-sidebar .unit-status.is-ready{color:#6ee7b7}
@media(max-width:800px){.studio-sidebar{position:static;max-height:none}}
.studio-columns{display:grid;grid-template-columns:220px minmax(0,1fr) 310px;gap:16px;align-items:start}.control{width:100%;border:1px solid #ffffff18;border-radius:8px;padding:8px;background:#0c1420;color:#dbe5f2;font-size:12px;resize:vertical}.control:focus{outline:1px solid #38bdf8}select.control option{background:#101a27}.unit-card{display:block;width:100%;text-align:left;padding:14px 12px;border:1px solid #ffffff10;border-radius:12px;background:#0a1420}.unit-card b{font-size:13px}.unit-card small{display:block;margin-top:7px;font-size:11px;color:#91a2b8}.selected{border-color:#38bdf888!important;background:#12304844}.reference-and-shots{display:grid;grid-template-columns:125px minmax(0,1fr);gap:14px}.shot-card{border:1px solid #ffffff16;padding:14px;border-radius:14px;background:#090f1880}.shot-grid{display:grid;grid-template-columns:minmax(0,1fr) 190px;gap:12px}.shot-thumb{min-width:0}.shot-thumb img{max-height:190px}.shot-thumb .btn{padding:3px 8px;font-size:10px}@media(max-width:1100px){.shot-grid{grid-template-columns:1fr}.shot-thumb img{max-height:230px}}summary{cursor:pointer}@media(max-width:1250px){.studio-columns{grid-template-columns:180px minmax(0,1fr)}.studio-columns>aside:last-child{grid-column:1/-1}.reference-and-shots{grid-template-columns:100px 1fr}}@media(max-width:800px){.studio-columns{display:block}.studio-columns>*{margin-bottom:12px}.reference-and-shots{display:block}.reference-and-shots>aside{margin-bottom:14px}}
</style>
