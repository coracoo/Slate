<script setup lang="ts">
import { useBoardSelection } from '../utils/useBoardSelection'
// -*- coding: utf-8 -*-
/** ② 分镜生成：按集生成分镜（知识注入）→ 逐镜明细；生成拍摄资料包 → 包明细 */
import { ref, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  fetchScriptData, fetchWhiteBoard, fetchCreate, scriptStoryboard,
  previewKnowledge, saveStoryboardShots, exportStoryboardXlsx, deleteStoryboard, compileActingPrompt, runActing, fetchEnvConfig,
  rebuildProductionPrompts, rebuildProductionEpisode,
  mediaUrl, type ScriptBundle, type WhiteBoard, type KnowledgeSkill, type CreateItem
} from '../api'
import { app, projectFiles, toast, currentProject } from '../stores/app'
import { trackJob } from '../stores/jobs'
import StyledSelect from '../components/StyledSelect.vue'
import StyleSelect from '../components/StyleSelect.vue'
import Versions from '../components/Versions.vue'
import EmptyState from '../components/EmptyState.vue'
import { icons } from '../components/icons'

interface Shot {
  id: string; dur: number; cam?: string; fov?: number; pos?: number[]; look?: number[]
  shot_size?: string; camera_move?: string; angle?: string; transition?: string
  scene?: string; speaker?: string; action?: string; prompt?: string; move?: string
  content?: string; sound?: string; lighting?: string; rig?: string; lens?: string
  lines?: { at: number; dur?: number; speaker: string; line: string }[]
  scene_ref?: string; actor_refs?: string[]; prop_refs?: string[]; asset_refs?: string[]
  prompt_image?: string; prompt_video?: string; prompt_grid?: string; prompt_revisions?: Record<string, string>
  asset_revisions?: Record<string, number>; prompt_version?: string
}
const data = ref<ScriptBundle | null>(null)
const episodes = computed(() => data.value?.episodes || [])
const episodeReady = (episode: { text?: string }) => Boolean(String(episode.text || '').trim())
const readyEpisodes = computed(() => episodes.value.filter(episodeReady))
const readyEpisodeIds = computed(() => readyEpisodes.value.map((episode) => episode.id))
const allReadySelected = computed(() => readyEpisodeIds.value.length > 0
  && readyEpisodeIds.value.every((id) => epsSel.value.includes(id)))
const epsSel = ref<string[]>([])
function toggleEp(id: string) {
  epsTouched.value = true
  const i = epsSel.value.indexOf(id)
  if (i >= 0) epsSel.value.splice(i, 1)
  else epsSel.value.push(id)
}
function toggleAllEpisodes() {
  epsTouched.value = true
  epsSel.value = allReadySelected.value ? [] : [...readyEpisodeIds.value]
}
const router = useRouter()
const busy = ref('')
const boards = ref<string[]>([])
const board = ref('')
const boardRev = ref<number | null>(null)
/** 剧本修订号（①剧本分集每次变更 +1）；分镜低于它说明是旧剧本生成的。 */
const scriptRev = computed(() => Number(data.value?.script_rev || 0))
const boardStale = computed(() => boardRev.value !== null && scriptRev.value > boardRev.value)
let boardLoadSeq = 0
const shots = ref<Shot[]>([])
const shotOutputs = ref<Record<string, { status: CreateItem['status']; count: number; image?: string }>>({})
const detail = ref<Shot | null>(null)
const actingPrompt = ref('')
const actingCompiling = ref(false)
const vendors = ref<{ id: string; label?: string; enabled: boolean; models?: Record<string, string> }[]>([])
const actingVendor = computed(() => vendors.value.find((v) => v.enabled && v.models?.text) || null)
const chars = computed(() => data.value?.characters?.characters || [])
const scenesMap = computed<Record<string, string>>(() => {
  const rows = (data.value as any)?.scenes?.scenes || []
  return Object.fromEntries(rows.map((r: any) => [r.id || r.name || '', r.name || r.id || '']).filter(([k]: any) => k))
})
const scenesAssets = computed(() => (data.value as any)?.scenes?.scenes || [])
/** 场景列：scene_ref 场景名优先；room/field 是对话契约的预设地形（军帐室内/野外战场），翻译成中文展示 */
function sceneCell(s: Shot): { label: string; cls: string; title: string } {
  const ref = String(s.scene_ref || '').replace(/^@scene:/, '')
  if (ref) return { label: scenesMap.value[ref] || ref, cls: 'bg-violet-400/15 text-violet-300', title: `场景资产：@scene:${ref}` }
  if (s.scene === 'field') return { label: '外景', cls: 'bg-amber-400/15 text-amber-300', title: 'field＝野外/战场预设地形（未关联场景资产）' }
  if (s.scene === 'room') return { label: '室内', cls: 'bg-white/10 text-slate-400', title: 'room＝室内预设地形（未关联场景资产）' }
  return { label: '—', cls: 'bg-white/5 text-slate-500', title: '未设置场景' }
}

async function load() {
  if (!app.current) return
  try { data.value = await fetchScriptData(app.current) } catch { data.value = null }
  try { vendors.value = (await fetchEnvConfig()).vendors || [] } catch { vendors.value = [] }
  boards.value = (projectFiles('分镜') || []).filter((f) => f.endsWith('.json') && f.startsWith('剧本_') && !f.includes('/') && !f.startsWith('.'))

  if (board.value) void loadBoard()
}
async function loadBoard() {
  const seq = ++boardLoadSeq
  const project = app.current
  const name = board.value
  shots.value = []
  shotOutputs.value = {}
  boardRev.value = null
  if (!project || !name) return
  try {
    const b: WhiteBoard = await fetchWhiteBoard(project, name)
    if (seq !== boardLoadSeq) return
    shots.value = ((b.shots || []) as unknown as Shot[]).map(s => ({...s, prompt_image: s.prompt_image || s.prompt || '', dur: Number(s.dur) > 0 ? Number(s.dur) : 4}))
    boardRev.value = typeof b.script_rev === 'number' ? b.script_rev : null
  } catch {
    if (seq === boardLoadSeq) shots.value = []
  }
  try {
    const created = await fetchCreate(project)
    if (seq !== boardLoadSeq) return
    const grouped: Record<string, CreateItem[]> = {}
    for (const item of created.items || []) {
      if (item.board !== name || !item.shot_id) continue
      ;(grouped[item.shot_id] ||= []).push(item)
    }
    const summary: typeof shotOutputs.value = {}
    for (const [shotId, items] of Object.entries(grouped)) {
      const latest = items[items.length - 1]
      const image = [...items].reverse().flatMap((item) =>
        item.type === 'image' ? (item.outputs || []).map((out) => outputPath(item, out)) : []
      )[0]
      summary[shotId] = { status: latest.status, count: items.length, image }
    }
    shotOutputs.value = summary
  } catch {
    if (seq === boardLoadSeq) shotOutputs.value = {}
  }
}

function outputPath(item: CreateItem, output: string): string {
  const p = String(output || '').replace(/\\/g, '/')
  if (p.startsWith('projects/')) return p
  if (p.startsWith('创作/')) return `projects/${app.current}/${p}`
  return `projects/${app.current}/创作/${item.id}/${p}`
}

function shotOutput(s: Shot) {
  return shotOutputs.value[s.id]
}

function outputStatusLabel(status?: CreateItem['status']): string {
  if (status === 'done') return '已产出'
  if (status === 'error') return '产出失败'
  if (status) return '生成中'
  return '未产出'
}
watch([() => app.current, () => currentProject.value?.name], load, { immediate: true })
watch(board, loadBoard)

/** 一键多选：每个选中集各起一个任务（并行跑，互不等待）；未选=全本单任务 */
const sbRunning = ref<string[]>([])
const kbHits = ref<KnowledgeSkill[]>([])
const viewTab = ref<'grid' | 'cards'>('grid')
const gridDirty = ref(false)
const savingGrid = ref(false)

/** 汇总表格：行内编辑 dur/action/prompt，整组回写（版本快照保护） */
function markDirty() { gridDirty.value = true }
async function saveGrid() {
  if (!app.current || !board.value || !shots.value.length) return
  savingGrid.value = true
  try {
    const r = await saveStoryboardShots(app.current, board.value, shots.value)
    toast(`已保存 ${r.shots} 镜（旧版自动进 .versions）`, 'ok')
    gridDirty.value = false
    loadBoard()
  } catch (e) { toast(e instanceof Error ? e.message : '保存失败', 'err') }
  finally { savingGrid.value = false }
}
async function doXlsx() {
  if (!app.current || !board.value) return
  try {
    const r = await exportStoryboardXlsx(app.current, board.value) as any
    if (r?.job && r.id) { const j = await trackJob(r.id, '导出 xlsx'); if (!j.success) throw new Error(j.err || '导出失败') }
    r.file = r.file || `分镜/${board.value.replace(/\.json$/, '')}_分镜脚本.xlsx`
    const a = document.createElement('a')
    a.href = `/media?p=${encodeURIComponent(r.file)}`
    a.download = r.file.split('/').pop() || '分镜脚本.xlsx'
    a.click()
  } catch (e) { toast(e instanceof Error ? e.message : '导出失败', 'err') }
}
function linesOf(s: Shot): string {
  return (s.lines || []).map((l) => `【${l.speaker}】${l.line}`).join(' / ')
}
/** 删除当前分镜（同名单镜 xlsx 一并删除；.versions 历史快照保留可恢复） */
async function removeBoard() {
  if (!app.current || !board.value) return
  if (!window.confirm(`删除分镜「${board.value}」？\n同名单镜 Excel 一并删除；.versions 历史快照保留，可恢复。`)) return
  try {
    const r = await deleteStoryboard(app.current, board.value)
    if (!r.ok) throw new Error(r.err || '删除失败')
    toast(`已删除：${(r.deleted || []).join('、') || board.value}`, 'ok')
    board.value = ''
    await load()
  } catch (e) { toast(e instanceof Error ? e.message : '删除失败', 'err') }
}
/** 摄像机位（视角）可读描述：高度差+角度词+水平距离 */
function viewOf(s: Shot): string {
  const ang = s.angle || '平视'
  if (ang === '鸟瞰') return '顶拍·俯瞰'
  let h = ''
  const pos = s.pos || [], look = s.look || []
  if (pos.length === 3 && look.length === 3) {
    const d = look[1] - pos[1]
    if (d > 0.8) h = '仰拍'
    else if (d < -1.2) h = '高机位下压'
    else h = '眼平'
  }
  const dist = pos.length === 3 && look.length === 3
    ? Math.hypot(look[0] - pos[0], look[2] - pos[2]).toFixed(1) : '?'
  return `${h || ang}·${dist}m`
}
const LENS_BY_SIZE: Record<string, string> = {
  '大特写': '100mm', '特写': '85mm', '近景': '85mm', '中近景': '50mm',
  '中景': '50mm', '全景': '35mm', '大全景': '24mm', '远景': '24mm', '大远景': '18mm'
}
const RIG_BY_MOVE: Record<string, string> = {
  '固定': '固定', '手持': '手持', '甩': '手持', '移': '滑轨', '轨道': '轨道',
  '环绕': '滑轨', '升降': '无人机', '无人机': '无人机', '推': '滑轨', '拉': '滑轨',
  '变焦': '固定', '跟': '稳定器', '斯坦尼康': '斯坦尼康'
}
/** 一键补默认：只为空白格填推断值（镜头焦距←景别、器械←运镜），不覆盖已填 */
function fillDefaults() {
  for (const s of shots.value) {
    if (!s.lens) s.lens = LENS_BY_SIZE[s.shot_size || ''] || '50mm'
    if (!s.rig) s.rig = RIG_BY_MOVE[s.camera_move || ''] || (s.camera_move ? '固定' : '')
  }
  markDirty()
  toast('已按景别/运镜补默认镜头与器械（空白格），可继续手改', 'info')
}

/** 垫上下文预览：按选中集的梗概+原文查将注入的知识卡片 */
async function loadPreview() {
  if (!episodes.value.length) { kbHits.value = []; return }
  const sel = epsSel.value.length ? episodes.value.filter((e) => epsSel.value.includes(e.id)) : episodes.value
  const text = sel.map((e) => `${e.summary || ''}${e.hook || ''}${e.text || ''}`).join(' ').slice(0, 800)
  if (!text.trim()) { kbHits.value = []; return }
  try { kbHits.value = (await previewKnowledge(text)).hits || [] } catch { kbHits.value = [] }
}
watch(epsSel, loadPreview)
watch(episodes, loadPreview, { immediate: true })
const epsTouched = ref(false)   // 用户手动改过选择后不再自动全选
watch(readyEpisodeIds, (ids) => {
  if (!epsTouched.value) { epsSel.value = [...ids]; return }   // 默认全选可生成集
  const valid = new Set(ids)
  const next = epsSel.value.filter((id) => valid.has(id))
  if (next.length !== epsSel.value.length) epsSel.value = next
}, { immediate: true })
async function doSb() {
  if (!app.current || busy.value) return
  const readyIds = new Set(readyEpisodeIds.value)
  const skipped = epsSel.value.filter((id) => !readyIds.has(id))
  const selected = epsSel.value.length ? epsSel.value.filter((id) => readyIds.has(id)) : []
  if (skipped.length) {
    epsSel.value = selected
    toast(`已跳过 ${skipped.join('、')}：暂无剧本文本，请先扩写`, 'info', 5000)
  }
  const targets = selected.length ? selected : ['']
  sbRunning.value = targets.map((e) => e || '全本')
  const results = await Promise.allSettled(
    targets.map(async (ep) => {
      const r = await scriptStoryboard(app.current!, ep || undefined)
      if (!r.id) throw new Error(r.err || '任务未启动')
      return trackJob(r.id, `分镜 ${ep || '全本'}`)
    })
  )
  const ok = results.filter((r) => r.status === 'fulfilled' && (r.value as { success?: boolean }).success).length
  const fail = results.length - ok
  const failures = results.flatMap((result) => {
    if (result.status === 'rejected') {
      return [result.reason instanceof Error ? result.reason.message : String(result.reason)]
    }
    const value = result.value as { success?: boolean; err?: string }
    return value.success ? [] : [value.err || '任务失败']
  })
  if (fail === 0) toast(`分镜生成完成：${ok} 个任务`, 'ok', 5000)
  else toast(`完成 ${ok} / 失败 ${fail}：${failures.slice(0, 2).join('；')}`, 'err', 7000)
  sbRunning.value = []
  load()
}
function spk(id?: string) {
  return chars.value.find((c) => c.id === id)?.name || id || ''
}

function goPackage() { router.push('/package') }

function boardEpisode(name: string): string {
  const match = String(name || '').match(/^剧本_(.+)\.json$/)
  const value = match?.[1] || ''
  return value && value !== '全本' ? value : ''
}
async function doRebuildPrompts() {
  if (!app.current || !board.value || busy.value) return
  busy.value = '只更新提示词'
  try {
    const r = await rebuildProductionPrompts({ project: app.current, episode: boardEpisode(board.value) || undefined })
    toast(`已更新 ${r.updated_prompts || 0} 镜提示词；未调用媒体模型`, 'ok', 5000)
    await loadBoard()
  } catch (e) { toast(e instanceof Error ? e.message : '提示词重建失败', 'err', 6000) }
  finally { busy.value = '' }
}
async function doRebuildEpisode() {
  if (!app.current || !board.value || busy.value) return
  const episode = boardEpisode(board.value)
  if (!episode) { toast('当前是全本分镜，无法按集重建；请使用“只更新提示词”', 'info', 4500); return }
  busy.value = '重建本集提示词'
  try {
    const r = await rebuildProductionEpisode({ project: app.current, episode })
    toast(`已重建 ${episode}：${r.updated_prompts || 0} 镜；未调用媒体模型`, 'ok', 5000)
    await loadBoard()
  } catch (e) { toast(e instanceof Error ? e.message : '本集重建失败', 'err', 6000) }
  finally { busy.value = '' }
}
async function doActingPrompt(s: Shot) {
  if (!app.current || !board.value) return
  actingCompiling.value = true
  try {
    const r = await compileActingPrompt({ project: app.current, storyboard: board.value, shot_id: s.id, mode: 'stateful' })
    actingPrompt.value = r.prompt || ''
    toast('演员提示词已编译（未调用模型）', 'ok', 3500)
  } catch (e) { toast(e instanceof Error ? e.message : '演员提示词编译失败', 'err') }
  finally { actingCompiling.value = false }
}
async function doRunActing(s: Shot) {
  if (!app.current || !board.value || !actingVendor.value) {
    toast('请先在环境页配置并启用 text 厂商', 'err', 4500); return
  }
  busy.value = `演员 ${s.id}`
  try {
    const r = await runActing({ project: app.current, storyboard: board.value, shot_id: s.id, vendor_id: actingVendor.value.id })
    if (!r.id) throw new Error(r.err || '演员任务未启动')
    const j = await trackJob(r.id, `演员表演 ${s.id}`)
    if (!j.success) throw new Error(j.err || '演员任务失败')
    toast('演员候选已生成 ' + s.id + '，请到「⑤ 演员表现」审核并应用', 'ok', 5000)
    await loadBoard()
  } catch (e) { toast(e instanceof Error ? e.message : '演员任务失败', 'err', 6000) }
  finally { busy.value = '' }
}
useBoardSelection(board, boards, 'shots')
</script>

<template>
  <div class="page">
    <header class="mb-6">
      <h1 class="grad-text text-2xl font-black">② 分镜生成</h1>
      <p class="mt-1 text-xs text-slate-500">LLM 同时生成转场镜头的参考帧、视频、宫格提示词及 V 分组。三类提示词可分别编辑，创作台使用同一份分镜。</p>
    </header>

    <EmptyState v-if="!app.current" title="请先在左侧选择项目" />

    <template v-else>
      <!-- 生成分镜 -->
      <section class="glass mb-5 p-4">
        <!-- 行 0：集选择（独立分块 · 默认全选可生成集） -->
        <div class="mb-3 rounded-xl border border-pink-400/25 bg-pink-400/5 p-3">
          <div class="flex flex-wrap items-center gap-2">
            <h3 class="shrink-0 text-sm font-black text-pink-200">选择集</h3>
            <span class="shrink-0 text-xs text-slate-400">可多选 · 有正文 {{ readyEpisodes.length }}/{{ episodes.length }} · 默认已全选可生成集</span>
            <span class="flex-1"></span>
            <button v-if="readyEpisodes.length" class="shrink-0 rounded-full px-2.5 py-0.5 text-2xs font-bold"
              :class="allReadySelected ? 'bg-pink-400/25 text-pink-200' : 'bg-white/10 text-slate-300 hover:bg-white/20'"
              @click="toggleAllEpisodes">
              {{ allReadySelected ? '取消全选' : '全选可生成集' }}
            </button>
          </div>
          <div class="mt-2 flex flex-wrap gap-1.5">
            <button v-for="e in episodes" :key="e.id" :disabled="!episodeReady(e)"
              class="rounded-full px-2.5 py-1 text-xs-plus font-bold transition disabled:cursor-not-allowed disabled:opacity-40"
              :class="epsSel.includes(e.id) ? 'chip-active' : 'chip'"
              :title="episodeReady(e) ? '已就绪，可生成分镜' : '暂无剧本文本，请先在①剧本生成页扩写'"
              @click="episodeReady(e) && toggleEp(e.id)">{{ e.id }} {{ e.title }}<span v-if="!episodeReady(e)">（待扩写）</span></button>
          </div>
          <p v-if="!readyEpisodes.length" class="mt-2 text-xs text-amber-300/80">还没有可生成的集——先到 ① 剧本生成页扩写正文</p>
        </div>
        <!-- 行 1：生成分镜动作 -->
        <div class="flex flex-wrap items-center gap-2">
          <span class="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-pink-400/15 text-xs font-black text-pink-300">1</span>
          <h3 class="shrink-0 text-sm font-bold text-slate-200">生成分镜</h3>
          <span v-if="epsSel.length" class="shrink-0 text-2xs text-slate-500">将生成：{{ epsSel.join('、') }}</span>
          <span class="flex-1"></span>
          <button class="btn shrink-0" :disabled="!!busy || !!sbRunning.length || !epsSel.length" @click="doSb" title="按选中的分集生成分镜 JSON；会更新镜头动作、机位和提示词">
            {{ sbRunning.length ? `生成中（${sbRunning.join(' ')}）…` : epsSel.length > 1 ? `LLM 生成分镜（${epsSel.length} 集并行）` : 'LLM 生成分镜' }}
          </button>
        </div>
        <!-- 行 2：已有分镜与操作（槽位固定，控件底部对齐，不随上行换行偏移） -->
        <div class="mt-3 flex flex-wrap items-end gap-2 border-t border-line-soft pt-3">
          <label class="w-56 shrink-0 text-xs text-slate-400">已有分镜
            <StyledSelect v-model="board" class="mt-1" :options="boards" :storage-key="`wb.${app.current}.shots.board`" placeholder="— 选择 —" />
          </label>
          <button class="btn shrink-0" :disabled="!board" title="走位战略图 / 平面图 / 预演包 / 逐镜包——分镜定稿后的组装产物都在平面推演页" @click="goPackage">去平面推演 →</button>
          <button class="btn btn-ghost shrink-0" :disabled="!!busy || !board" @click="doRebuildPrompts" title="只按当前全局资产和分镜事实重建静态参考图/生视频提示词，不调用模型，不生成媒体">只更新提示词</button>
          <button class="btn btn-ghost shrink-0" :disabled="!!busy || !board || !boardEpisode(board)" @click="doRebuildEpisode" title="重建当前分集的全部分镜提示词；不修改分集正文、大纲或已有图片视频">重建本集</button>
          <Versions v-if="board" :path="`projects/${app.current}/分镜/${board}`" kind="file" @restored="loadBoard" />
          <span v-if="board && boardRev !== null"
            class="shrink-0 rounded-full px-2.5 py-0.5 text-2xs"
            :class="boardStale ? 'bg-amber-400/15 text-amber-200' : 'bg-white/5 text-slate-400'"
            :title="boardStale ? `剧本已更新到 v${scriptRev}，本图基于旧剧本 v${boardRev} 生成，建议重新生成分镜` : '本分镜生成时对应的剧本修订号'">
            基于剧本 v{{ boardRev }}<template v-if="boardStale"> · 已过期(当前 v{{ scriptRev }})</template>
          </span>
          <button v-if="board" class="btn btn-danger shrink-0 px-2" title="删除本分镜（.versions 历史快照保留）" @click="removeBoard">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path :d="icons.trash" stroke-linecap="round" stroke-linejoin="round"/></svg>
          </button>
          <span class="flex-1"></span>
          <StyleSelect target="storyboard" label="导演风格" />
          <StyleSelect target="acting" label="演员风格" />
        </div>
        <!-- 资产前置提示（非阻断）：scene_ref 是平面图/空间一致性链路的根基，提炼应在分镜生成之前 -->
        <div v-if="!chars.length || !scenesAssets.length" class="mb-3 rounded-xl border border-amber-400/25 bg-amber-400/5 p-3 text-xs-plus leading-relaxed text-amber-200/90">
          <b>资产未提炼。</b>正确顺序：① 剧本生成 → <b>③ 素材提炼（人物/场景/道具）</b> → 回本页生成分镜——分镜会自动关联场景资产（scene_ref）与人物/道具引用，是平面推演与视频空间一致性的根基，无需任何手动绑定。
          <template v-if="shots.length">
            当前分镜已生成但未关联场景——<b>提炼后回到本页重新生成分镜即可自动补上</b>（已采用的关键帧/视频会保留）。
          </template>
          <template v-else>纯对白/白模用法可以不提炼直接生成，但平面推演与参考帧回流将不可用。</template>
        </div>
        <p v-if="!chars.length && false" class="mt-2 text-xs-plus text-amber-300/80"></p>
        <div v-if="kbHits.length" class="mt-2 flex flex-wrap items-center gap-1.5">
          <span class="text-2xs font-bold text-emerald-400/80">将垫入上下文的拉片卡片</span>
          <span v-for="h in kbHits" :key="h.id"
            class="rounded-full bg-emerald-400/10 px-2 py-0.5 text-2xs text-emerald-200"
            :title="h.prescription">
            {{ h.skill }}<span v-if="(h as any).source === 'user'" class="ml-1 text-violet-300">我的</span>
          </span>
        </div>
      </section>

      <!-- 分镜查看：汇总表格 / 逐镜明细 双 tab -->
      <section class="glass mb-5 p-4">
        <div class="mb-3 flex flex-wrap items-center gap-3">
          <span class="flex h-6 w-6 items-center justify-center rounded-full bg-pink-400/15 text-xs font-black text-pink-300">2</span>
          <button class="rounded-lg px-3 py-1 text-xs font-bold transition"
            :class="viewTab === 'grid' ? 'chip-active' : 'chip'"
            @click="viewTab = 'grid'">汇总表格</button>
          <button class="rounded-lg px-3 py-1 text-xs font-bold transition"
            :class="viewTab === 'cards' ? 'chip-active' : 'chip'"
            @click="viewTab = 'cards'">逐镜明细</button>
          <span class="text-xs-plus text-slate-500">{{ shots.length }} 镜</span>
          <span class="flex-1"></span>
          <template v-if="viewTab === 'grid'">
            <span v-if="gridDirty" class="text-xs-plus text-amber-300">有未保存修改</span>
            <button class="btn btn-ghost" :disabled="!shots.length" title="按景别/运镜为空白格填默认镜头焦距与器械"
              @click="fillDefaults">补默认</button>
            <button class="btn btn-ghost" :disabled="savingGrid" @click="doXlsx">导出 Excel</button>
            <button class="btn" :disabled="!gridDirty || savingGrid" @click="saveGrid">
              {{ savingGrid ? '保存中…' : '保存修改' }}
            </button>
          </template>
        </div>

        <!-- 汇总表格（Excel 式）：镜号/场景/时长/机位视角/器械/镜头/运镜/内容/动作/声音/光影/台词/三提示词（景别数据保留在 JSON 与逐镜明细） -->
        <div v-if="viewTab === 'grid'">
          <div v-if="!shots.length" class="py-10 text-center text-sm text-slate-500">选择或生成一个剧本分镜</div>
          <div v-else class="modal-h-sm overflow-auto rounded-lg border border-line">
            <table class="tbl-view border-collapse">
              <thead>
                <tr>
                  <th class="sticky-col">镜号</th>
                  <th class="min-w-24">场景</th>
                  <th class="w-16">时长s</th>
                  <th>机位(视角)</th>
                  <th>器械</th>
                  <th>镜头</th>
                  <th>运镜</th>
                  <th class="min-w-40">内容</th>
                  <th class="min-w-36">动作</th>
                  <th class="min-w-28">声音</th>
                  <th class="min-w-28">光影</th>
                  <th class="min-w-40">台词</th>
                  <th class="min-w-64">参考帧提示词</th><th class="min-w-64">视频提示词</th><th class="min-w-64">宫格提示词</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="s in shots" :key="s.id">
                  <td class="sticky-col whitespace-nowrap font-black text-sky-300">{{ s.id }}</td>
                  <td class="whitespace-nowrap">
                    <span class="rounded px-1.5 py-0.5 text-2xs font-bold" :class="sceneCell(s).cls" :title="sceneCell(s).title">{{ sceneCell(s).label }}</span>
                  </td>
                  <td>
                    <input v-model.number="s.dur" type="number" step="0.5" min="1" max="15"
                      class="cell-input w-14 text-center tabular-nums text-slate-200"
                      @input="markDirty" />
                  </td>
                  <td class="whitespace-nowrap text-slate-400" :title="`${JSON.stringify(s.pos)} → ${JSON.stringify(s.look)}`">{{ viewOf(s) }}</td>
                  <td><input v-model="s.rig" class="cell-input w-20 text-slate-300" @input="markDirty" /></td>
                  <td><input v-model="s.lens" class="cell-input w-16 text-slate-300" @input="markDirty" /></td>
                  <td class="whitespace-nowrap text-slate-400">{{ s.camera_move }}</td>
                  <td><textarea v-model="s.content" rows="2" class="cell-input text-slate-300" @input="markDirty"></textarea></td>
                  <td><textarea v-model="s.action" rows="2" class="cell-input text-slate-200" @input="markDirty"></textarea></td>
                  <td><textarea v-model="s.sound" rows="2" class="cell-input text-slate-400" @input="markDirty"></textarea></td>
                  <td><textarea v-model="s.lighting" rows="2" class="cell-input text-slate-400" @input="markDirty"></textarea></td>
                  <td class="max-w-56 text-slate-400">{{ linesOf(s) || '—' }}</td>
                  <td><textarea v-model="s.prompt_image" rows="3" class="cell-input text-slate-300" @input="markDirty"></textarea></td>
                  <td><textarea v-model="s.prompt_video" rows="3" class="cell-input text-slate-300" @input="markDirty"></textarea></td>
                  <td><textarea v-model="s.prompt_grid" rows="3" class="cell-input text-slate-300" @input="markDirty"></textarea></td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- 逐镜明细（卡片） -->
        <div v-else>
        <div v-if="!shots.length" class="py-10 text-center text-sm text-slate-500">选择或生成一个剧本分镜</div>
        <div class="grid gap-2 md:grid-cols-2">
          <button v-for="s in shots" :key="s.id" class="rounded-lg bg-white/5 p-2.5 text-left transition hover:bg-white/10" @click="detail = s">
            <div class="flex flex-wrap items-center gap-1.5">
              <span class="rounded bg-sky-400/15 px-1.5 py-0.5 text-xs-plus font-black text-sky-300">{{ s.id }}</span>
              <span class="text-xs-plus text-slate-300">{{ s.move || s.shot_size }}</span>
              <span class="rounded bg-white/5 px-1 text-2xs text-slate-500">{{ s.cam }}</span>
              <span class="ml-auto text-2xs text-slate-500">{{ s.dur }}s</span>
            </div>
            <div class="mt-2 flex gap-2">
              <div class="h-14 w-24 shrink-0 overflow-hidden rounded-md border border-line bg-black/30">
                <img v-if="shotOutput(s)?.image" :src="mediaUrl(shotOutput(s)!.image!)" class="h-full w-full object-cover" loading="lazy" :alt="`${s.id} 参考图`" />
                <div v-else class="flex h-full items-center justify-center text-2xs text-slate-500">暂无缩略图</div>
              </div>
              <div class="min-w-0 flex-1">
                <div class="line-clamp-2 text-xs-plus text-slate-400">{{ s.action || s.prompt || '未填写动作摘要' }}</div>
                <div class="mt-1 flex items-center gap-1.5 text-2xs text-slate-500">
                  <span>{{ outputStatusLabel(shotOutput(s)?.status) }}</span>
                  <span v-if="shotOutput(s)?.count">· {{ shotOutput(s)?.count }} 次产出</span>
                </div>
              </div>
            </div>
          </button>
        </div>
        </div>
      </section>

      <!-- 镜明细抽屉 -->
      <Teleport to="body">
        <div v-if="detail" class="overlay-end" @click.self="detail = null">
          <div class="drawer-panel">
            <div class="mb-4 flex items-center gap-3">
              <span class="rounded bg-sky-400/15 px-2 py-0.5 text-sm font-black text-sky-300">{{ detail.id }}</span>
              <b class="text-lg text-slate-100">{{ detail.move }}</b>
              <span class="text-xs text-slate-500">{{ detail.dur }}s · {{ detail.scene }}</span>
              <button class="btn btn-ghost ml-auto" @click="detail = null">关闭</button>
            </div>
            <details class="mb-3 rounded-lg border border-line-soft bg-black/15 px-2 py-1.5">
              <summary class="cursor-pointer text-2xs text-slate-500">高级：主角演员表现候选（可选）</summary>
              <div class="mt-2 flex flex-wrap items-center gap-2">
                <button class="btn" :disabled="actingCompiling" @click="detail && doActingPrompt(detail)">
                  {{ actingCompiling ? '编译中…' : '编译主角演员表现' }}
                </button>
                <button class="btn btn-ghost" :disabled="!!busy || !actingVendor" @click="detail && doRunActing(detail)">
                  {{ actingVendor ? '生成主角演员候选' : '请先配置 text 厂商' }}
                </button>
                <span class="text-2xs text-slate-500">只补表情、视线和节奏，不改变镜头机位、走位和台词</span>
              </div>
            </details>
            <div class="space-y-3 text-xs">
              <div class="grid grid-cols-2 gap-2">
                <div class="rounded-lg bg-white/5 p-2.5"><b class="text-slate-400">景别/角度</b><p class="mt-1 text-slate-200">{{ detail.shot_size }} · {{ detail.angle }}</p></div>
                <div class="rounded-lg bg-white/5 p-2.5"><b class="text-slate-400">运镜/转场</b><p class="mt-1 text-slate-200">{{ detail.camera_move }} · {{ detail.transition }}</p></div>
                <div class="rounded-lg bg-white/5 p-2.5"><b class="text-slate-400">机位 pos</b><p class="mt-1 font-mono text-slate-200">{{ JSON.stringify(detail.pos) }}</p></div>
                <div class="rounded-lg bg-white/5 p-2.5"><b class="text-slate-400">视点 look</b><p class="mt-1 font-mono text-slate-200">{{ JSON.stringify(detail.look) }}</p></div>
              </div>
              <div class="rounded-lg bg-white/5 p-2.5"><b class="text-emerald-300">动作</b><p class="mt-1 text-slate-300">{{ detail.action || '—' }}</p></div>
              <div class="rounded-lg bg-violet-400/5 p-2.5"><b class="text-violet-300">静态参考图提示词</b><p class="mt-1 whitespace-pre-wrap text-slate-300">{{ detail.prompt_image || detail.prompt || '—' }}</p></div>
              <div class="rounded-lg bg-sky-400/5 p-2.5"><b class="text-sky-300">生视频提示词</b><p class="mt-1 whitespace-pre-wrap text-slate-300">{{ detail.prompt_video || '尚未重建' }}</p></div><div class="rounded-lg bg-violet-400/5 p-2.5"><b class="text-violet-300">宫格布局提示词</b><p class="mt-1 whitespace-pre-wrap text-slate-300">{{ detail.prompt_grid || '待 LLM 补全' }}</p></div>
              <div v-if="detail.asset_refs?.length" class="rounded-lg bg-cyan-400/5 p-2.5"><b class="text-cyan-300">关联资产</b><p class="mt-1 break-all text-slate-300">{{ detail.asset_refs.join('、') }}</p></div>
              <div v-if="detail.asset_revisions" class="rounded-lg bg-amber-400/5 p-2.5"><b class="text-amber-300">资产修订</b><p class="mt-1 break-all text-slate-300">{{ Object.entries(detail.asset_revisions).map(([ref, rev]) => `${ref} v${rev}`).join(' · ') }}</p></div>
              <div class="rounded-lg bg-white/5 p-2.5"><div class="flex items-center gap-2"><b class="text-slate-400">本镜产出</b><span class="text-2xs text-slate-500">{{ outputStatusLabel(shotOutput(detail)?.status) }}<span v-if="shotOutput(detail)?.count"> · {{ shotOutput(detail)?.count }} 次</span></span></div><img v-if="shotOutput(detail)?.image" :src="mediaUrl(shotOutput(detail)!.image!)" class="mt-2 aspect-video w-full rounded-md border border-line bg-black object-contain" loading="lazy" :alt="`${detail.id} 参考图`" /></div>
              <div v-if="actingPrompt" class="rounded-lg bg-emerald-400/10 p-2.5"><b class="text-emerald-300">演员层编译结果</b><p class="mt-1 whitespace-pre-wrap text-slate-300">{{ actingPrompt }}</p></div>
              <div class="rounded-lg bg-white/5 p-2.5"><b class="text-amber-300">台词轨</b>
                <div v-for="(L, i) in detail.lines || []" :key="i" class="mt-1 text-slate-300">
                  <span class="text-slate-500">at {{ L.at }}s</span> 【{{ spk(L.speaker) }}】{{ L.line }}
                </div>
                <div v-if="!detail.lines?.length" class="mt-1 text-slate-500">无台词</div>
              </div>
            </div>
          </div>
        </div>
      </Teleport>

    </template>

  </div>
</template>


