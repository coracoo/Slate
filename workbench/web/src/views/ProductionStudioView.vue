<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { app, projectFiles, toast } from '../stores/app'
import { trackJob } from '../stores/jobs'
import { fetchCreate, fetchEnvConfig, mediaUrl, type Vendor } from '../api'
import { studioData, studioPost, submitStudioJob, type StudioState, type VideoUnit, type ProductionShot, type ProductionItem } from '../utils/productionStudio'
import { useBoardSelection } from '../utils/useBoardSelection'
import VideoSettings from '../components/VideoSettings.vue'
import MediaReferences from '../components/MediaReferences.vue'
import type { VideoSettingsValue } from '../utils/videoSettings'
import { defaultShotPrompt, defaultUnitPrompt, retimeUnit } from '../utils/shotPromptEditor'

const board = ref(''), data = ref<StudioState | null>(null), vendors = ref<Vendor[]>([]), items = ref<ProductionItem[]>([])
const boards = computed(() => projectFiles('分镜', /\.json$/))
useBoardSelection(board, boards, 'create')
const selectedUnit = ref(''), selectedShot = ref(''), scope = ref<'S' | 'V'>('V'), kind = ref<'image' | 'video'>('video')
const vendor = ref(''), textVendor = ref(''), visionVendor = ref(''), busy = ref(false), dirty = ref(false), duration = ref(5)
const refMode = ref('keyframes'), tailMode = ref(''), tailItem = ref(''), includeVoices = ref(true), error = ref('')
const concatQuality = ref<'master' | 'proxy'>('master')
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
const assetTokens = computed(() => [...new Set(shots.value.flatMap(s => [s.scene_ref, ...(s.actor_refs || []), ...(s.prop_refs || [])]).filter(Boolean))])
const assetPreviews = computed(() => {
  const seen = new Set<string>(), ids = new Set(shots.value.map(s => s.id))
  return (data.value?.asset_previews || []).filter(r => ids.has(r.shot_id) && !seen.has(r.path) && !!seen.add(r.path))
})
function pathUrl(path: string) { return mediaUrl(path.startsWith('projects/') ? path : `projects/${app.current}/${path}`) }
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
    }
    for (const u of result.board.video_units || []) {
      // 宫格文案按需人工配置（仅故事板宫格参考模式使用），默认不回填
      u.prompt_video = defaultUnitPrompt(u, result.board.shots, 'prompt_video')
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
  refMode.value = options.ref_mode || 'keyframes'; tailMode.value = options.tail_mode || ''; tailItem.value = options.tail_item || ''
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
    if (app.current === project && board.value === name && !dirty.value) await load()
    else toast('优化已完成；当前有未保存修改，请先处理再刷新', 'info')
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
      continuity: tailMode.value && (tailMode.value !== 'tail_first_frame' || ['first_frame','first_last'].includes(videoOptions.value.mode || '')) ? {mode: tailMode.value, item_id: tailItem.value, vision_vendor: visionVendor.value} : {}} : {}),
    ...(action === 'concat' ? { quality: concatQuality.value } : {})}
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
watch([() => app.current, board], () => {
  error.value = ''
  try { const old = JSON.parse(localStorage.getItem(selectionKey()) || '{}'); selectedUnit.value = old.unit || ''; selectedShot.value = old.shot || ''; scope.value = old.scope === 'S' ? 'S' : 'V'; kind.value = old.kind === 'image' ? 'image' : 'video' } catch { /* 选择记录损坏不影响分镜源 */ }
  void load(); void gallery()
}, {immediate: true})
watch(kind, rememberSelection)
watch(board, name => { if (app.current && name) localStorage.setItem(`wb.${app.current}.create.board`, name) })
watch(models, rows => { if (!rows.some(v => v.id === vendor.value)) vendor.value = rows[0]?.id || '' })
void fetchEnvConfig().then(r => { vendors.value = r.vendors; textVendor.value = textModels.value[0]?.id || ''; visionVendor.value = visionModels.value[0]?.id || '' })
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
    <p v-if="!app.current" class="p-10 text-slate-400">请先选择项目。</p>
    <div v-else class="studio-columns">
      <aside class="glass studio-sidebar">
        <label class="block text-xs text-slate-400">分集 / 分镜 JSON<select v-model="board" class="control mt-2"><option v-for="b in boards" :key="b">{{ b }}</option></select></label>
        <label class="block text-xs text-slate-400">编排模型<select v-model="textVendor" class="control mt-1"><option v-for="v in textModels" :key="v.id" :value="v.id">{{ v.models.text }}</option></select></label>
        <button class="btn w-full" :disabled="busy || !board || !textVendor" @click="job('group')">自动合并分镜</button>
        <button v-if="!units.length" class="btn btn-ghost w-full" :disabled="!board" @click="initialize">先按场景建立 V 分组</button>
        <button v-for="u in units" :key="u.id" class="unit-card" :class="{'selected': u.id === selectedUnit}" @click="chooseUnit(u)">
          <b>{{ u.label }} · {{ u.title }}</b><small>{{ u.shot_ids.join(' · ') }} · {{ u.duration }}s</small>
          <small class="unit-status" :class="u.stale || u.video_stale ? 'is-pending' : 'is-ready'">{{ u.stale ? '汇总待更新' : u.video_stale ? '已采用视频待确认' : u.video_binding ? '已采用视频' : '待生成视频' }}</small>
        </button>
        <button class="btn w-full" :disabled="busy || !units.length" @click="concatQuality = 'master'; job('concat')">拼接已采用 V → 集视频（交付母版）</button>
        <button class="btn btn-ghost w-full text-[10px]" :disabled="busy || !units.length" title="统一 720p/24fps 快速看片用，不作为交付出口" @click="concatQuality = 'proxy'; job('concat')">快速预览（720p 代理，不交付）</button>
      </aside>
      <main class="glass min-w-0 space-y-4 p-4">
        <section v-if="unit" class="space-y-2 border-b border-white/10 pb-4">
          <div class="flex items-center gap-2"><b class="text-sky-300">{{ unit.label }}</b><input v-model="unit.title" class="control" @input="dirty = true" /></div>
          <label class="block text-xs text-slate-400">完整分镜视频提示词<button class="ml-2 text-sky-300" :disabled="busy || !!optimizing || !textVendor" @click="optimize('V', unit.id, 'prompt_video')">重新生成 / 优化</button><textarea v-model="unit.prompt_video" class="control mt-1" rows="4" @input="dirty = true" /></label>
          <details><summary class="text-xs text-violet-300">宫格布局提示词（可选，仅故事板宫格参考模式需要）</summary><button class="text-xs text-sky-300" :disabled="busy || !!optimizing || !textVendor" @click="optimize('V', unit.id, 'prompt_grid')">重新生成 / 优化</button><textarea v-model="unit.prompt_grid" class="control mt-2" rows="3" placeholder="留空即可——宫格由已采用关键帧自动排版；选择故事板宫格参考模式时再按需填写布局说明" @input="dirty = true" /></details>
          <div class="flex flex-wrap gap-2"><span v-for="beat in unit.timeline" :key="beat.shot_id" class="rounded-lg bg-sky-950/40 px-2 py-1 text-xs text-sky-200">{{ beat.shot_id }} · {{ beat.start }}–{{ beat.end }}s</span></div>
          <div class="flex flex-wrap gap-2"><button class="btn btn-sm" :disabled="busy" @click="saveUnits(true)">保存并确认 V 汇总</button><button class="btn btn-sm btn-ghost" :disabled="busy" @click="mergeNext">与下一个 V 合并</button></div>
          <p class="text-xs text-slate-500">改动 S 后，V 汇总会标记过期；请更新叙事承接再确认。宫格由已采用关键帧排版生成。</p>
        </section>
        <div class="reference-and-shots">
          <aside class="space-y-2 text-xs"><b class="text-slate-300">本段引用素材</b><a v-for="r in assetPreviews" :key="r.path" :href="pathUrl(r.path)" target="_blank" class="block overflow-hidden rounded-lg bg-black/30"><img :src="pathUrl(r.path)" class="aspect-video w-full object-contain" :alt="r.purpose" /><small class="block p-1 text-slate-400">{{ r.purpose }}</small></a><details><summary class="text-slate-400">资产引用</summary><div v-for="token in assetTokens" :key="token" class="break-all rounded-lg bg-sky-950/40 p-2 text-sky-200">{{ token }}</div></details><RouterLink class="block text-slate-400" to="/studio/asset">查看 / 生成素材 →</RouterLink></aside>
          <section class="min-w-0 space-y-4">
            <article v-for="s in shots" :key="s.id" class="shot-card" :class="{'selected': scope === 'S' && selectedShot === s.id}">
              <header class="mb-3 flex flex-wrap items-center justify-between gap-2"><button class="font-bold text-sky-200" @click="chooseShot(s)">{{ s.id }} 转场镜头</button><label class="text-xs text-slate-400">时长（秒）<input :value="s.dur" type="number" min="0.1" step="0.1" class="control inline-block w-24 ml-2" @input="editShotDuration(s, $event)" /></label><button v-if="unit && unit.shot_ids[0] !== s.id" class="text-xs text-slate-400" @click="split(s.id)">从此镜拆为新 V</button></header>
              <button class="keyframe-preview" @click="chooseShot(s)"><img v-if="s.keyframe" :src="pathUrl(s.keyframe.path)" :alt="s.id + ' 已采用关键帧'" /><span v-else>尚未采用关键帧</span></button>
              <div class="mt-3 space-y-3">
                <label v-for="f in promptFields" :key="f.key" class="block text-xs text-sky-300">
                  <span>{{ f.label }}</span><button class="ml-3 text-xs text-cyan-200" :aria-label="'优化 ' + s.id + ' ' + f.label" :disabled="busy || !!optimizing || !textVendor" @click="optimize('S', s.id, f.key)">{{ optimizing === s.id + ':' + f.key ? '优化中…' : '重新生成 / 优化' }}</button>
                  <textarea v-model="s[f.key]" :aria-label="s.id + ' ' + f.label" class="control mt-2" rows="4" @input="dirty = true" />
                </label>
                <p class="text-2xs text-slate-500">序号、时段、景别、镜头、运镜、画面（内容/人物/动作/声音/台词）、光影。以当前文字为基础优化。</p>
                <div class="flex gap-2"><button class="btn btn-sm" :disabled="busy" @click="savePrompts">保存 S 提示词</button><button class="btn btn-sm btn-ghost" @click="chooseShot(s)">选择本 S 创作</button></div>
              </div>
            </article>
          </section>
        </div>
        <section v-if="unit" class="space-y-2 border-t border-white/10 pt-4"><p class="text-xs text-slate-400">当前场景：{{ unit.scene_ref || '请在分镜生成中补充 scene_ref' }}</p><label class="block text-xs text-slate-400">本 V 统一负面提示词<textarea v-model="unit.negative" class="control mt-1" rows="2" @input="dirty = true" /></label></section>
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
            <h3 class="text-sm font-bold text-sky-200">关键帧 · 尾帧 · 音色</h3>
            <fieldset v-if="videoOptions.mode === 'reference'"><legend>关键帧</legend><div class="space-y-2 mt-2">
              <button class="ref-choice" :class="{active: refMode === 'keyframes'}" :disabled="busy" @click="refMode = 'keyframes'; saveOptions()">按 S 顺序引用关键帧</button>
              <button class="ref-choice" :class="{active: refMode === 'grid'}" :disabled="busy" @click="refMode = 'grid'; saveOptions()">故事板宫格</button>
            </div></fieldset>
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
        <section class="space-y-3 border-t border-white/10 pt-4"><div class="flex justify-between text-sm"><b>当前目标产出</b><button @click="gallery">刷新</button></div>
          <article v-for="item in candidates" :key="item.id" class="rounded-lg bg-black/25 p-2"><p class="mb-2 text-xs text-slate-400">{{ item.status }} · {{ item.created_at }} <span v-if="item.actual_duration">· {{ item.actual_duration }}s</span></p>
            <video v-if="item.type === 'video' && outputPath(item)" :src="pathUrl(outputPath(item))" controls preload="metadata" class="w-full" />
            <img v-else-if="outputPath(item)" :src="pathUrl(outputPath(item))" class="w-full" :alt="item.shot_id" />
            <p v-if="item.note" class="mt-2 break-words text-xs text-amber-200">{{ item.note }}</p><button v-if="item.status === 'done'" class="btn btn-sm mt-2" @click="adopt(item)">采用此{{ item.type === 'image' ? '关键帧' : '视频' }}</button>
          </article><p v-if="!candidates.length" class="text-xs text-slate-500">暂无候选</p>
        </section>
      </aside>
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
.studio-columns{display:grid;grid-template-columns:220px minmax(0,1fr) 310px;gap:16px;align-items:start}.control{width:100%;border:1px solid #ffffff18;border-radius:8px;padding:8px;background:#0c1420;color:#dbe5f2;font-size:12px;resize:vertical}.control:focus{outline:1px solid #38bdf8}select.control option{background:#101a27}.unit-card{display:block;width:100%;text-align:left;padding:14px 12px;border:1px solid #ffffff10;border-radius:12px;background:#0a1420}.unit-card b{font-size:13px}.unit-card small{display:block;margin-top:7px;font-size:11px;color:#91a2b8}.selected{border-color:#38bdf888!important;background:#12304844}.reference-and-shots{display:grid;grid-template-columns:125px minmax(0,1fr);gap:14px}.shot-card{border:1px solid #ffffff16;padding:14px;border-radius:14px;background:#090f1880}.keyframe-preview{display:flex;align-items:center;justify-content:center;width:100%;min-height:100px;background:#050a11;border-radius:8px;color:#627086;font-size:12px}.keyframe-preview img{width:100%;max-height:270px;object-fit:contain}summary{cursor:pointer}@media(max-width:1250px){.studio-columns{grid-template-columns:180px minmax(0,1fr)}.studio-columns>aside:last-child{grid-column:1/-1}.reference-and-shots{grid-template-columns:100px 1fr}}@media(max-width:800px){.studio-columns{display:block}.studio-columns>*{margin-bottom:12px}.reference-and-shots{display:block}.reference-and-shots>aside{margin-bottom:14px}}
</style>
