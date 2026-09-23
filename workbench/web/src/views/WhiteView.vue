<script setup lang="ts">
import { useBoardSelection } from '../utils/useBoardSelection'
// -*- coding: utf-8 -*-
/** ① 辅助·白模：选分镜 JSON + 镜头区间 + 引擎 → 渲染；产物视频卡片。 */
import { ref, computed, watch } from 'vue'
import { runWhiterange, whiteFromAnalysis, exportPreviz, fetchAnalysisList, fetchWhiteBoard, fmtT, mediaUrl, deleteFile, type WhiteBoard } from '../api'
import { app, projectFiles, materialVideos, toast, loadBasics } from '../stores/app'
import { trackJob } from '../stores/jobs'
import StyledSelect from '../components/StyledSelect.vue'
import DelBadge from '../components/DelBadge.vue'
import Versions from '../components/Versions.vue'
import UploadButton from '../components/UploadButton.vue'
import EmptyState from '../components/EmptyState.vue'
import { icons } from '../components/icons'

const storyboard = ref('')
const shots = ref('S1-S999')
const engine = ref<'auto' | 'dialogue' | 'previs'>('auto')
const running = ref(false)

/* 从拉片解构生成分镜 */
const analyses = ref<{ name: string; status?: string; shot_count?: number }[]>([])
const analysis = ref('')
const pose = ref<'stand' | 'seated'>('stand')
const converting = ref(false)
const POSE_LABELS = { stand: '站立（战场/室外）', seated: '盘坐（室内/桌边）' }

const boards = computed(() => projectFiles('分镜', /\.json$/i))
const ENGINE_LABELS = {
  auto: 'auto（自动选择）',
  dialogue: 'dialogue（对白）',
  previs: 'previs（走位/动作）'
}

const products = computed(() =>
  projectFiles('白模', /\.mp4$/i).map((f) => ({
    file: f,
    path: `projects/${app.current}/白模/${f}`
  }))
)

/* ---------- 原片 vs 白模 对比（双向联动播放） ---------- */
const srcVideo = ref('')
const srcOptions = computed(() => materialVideos().map((v) => v.rel))
watch([srcOptions, srcVideo], () => {
  if ((!srcVideo.value || !srcOptions.value.includes(srcVideo.value)) && srcOptions.value.length) srcVideo.value = srcOptions.value.at(-1) || ''
}, { immediate: true })

const cmpVideo = ref('')
const cmpOptions = computed(() => products.value.map((p) => p.path))
const cmpLabels = computed(() => Object.fromEntries(products.value.map((p) => [p.path, p.file])))
watch([cmpOptions, cmpVideo], () => {
  if ((!cmpVideo.value || !cmpOptions.value.includes(cmpVideo.value)) && cmpOptions.value.length) {
    cmpVideo.value = cmpOptions.value[cmpOptions.value.length - 1] // 默认最新产物
  }
}, { immediate: true })

const leftEl = ref<HTMLVideoElement | null>(null)
const rightEl = ref<HTMLVideoElement | null>(null)
let syncing = false
function syncFrom(src: 'l' | 'r') {
  if (syncing) return
  const a = src === 'l' ? leftEl.value : rightEl.value
  const b = src === 'l' ? rightEl.value : leftEl.value
  if (!a || !b) return
  syncing = true
  try {
    if (Math.abs(a.currentTime - b.currentTime) > 0.15) b.currentTime = a.currentTime
    if (a.paused) b.pause()
    else b.play().catch(() => {})
  } finally {
    setTimeout(() => (syncing = false), 60)
  }
}

/* ---------- 分镜可视化：选中 JSON 后展示镜头表，点选两个镜头定区间 ---------- */
const board = ref<WhiteBoard | null>(null)
const boardLoading = ref(false)
const anchor = ref<number | null>(null)   // 点选区间的锚点（镜头 index）

const CAM_LABELS: Record<string, string> = {
  wide: '全景', two: '双人', cu: '特写', near: '近景背影', ots: '越肩', off: '自定义'
}
const boardShots = computed(() => board.value?.shots || [])
const boardDur = computed(() => boardShots.value.reduce((a, s) => a + (s.dur || 0), 0))
const boardActors = computed(() =>
  Object.entries(board.value?.actors || {}).map(([aid, a]) => ({
    aid,
    name: a.name || aid,
    color: `rgb(${(a.shirt || [120, 120, 130]).join(',')})`
  }))
)

function actorName(aid?: string | null): string {
  if (!aid) return ''
  return board.value?.actors?.[aid]?.name || aid
}
function actorColor(aid?: string | null): string {
  if (!aid) return '#94a3b8'
  const s = board.value?.actors?.[aid]?.shirt
  return s ? `rgb(${s.join(',')})` : '#94a3b8'
}
function shotNum(id: string): number {
  const m = id.match(/\d+/)
  return m ? parseInt(m[0], 10) : 0
}
/** 当前区间文本 -> index 集合，用于高亮 */
const rangeIdx = computed(() => {
  const m = shots.value.replace(/\s/g, '').match(/^S?(\d+)-S?(\d+)$/i)
  const set = new Set<number>()
  if (!m) return set
  const a = parseInt(m[1], 10)
  const b = parseInt(m[2], 10)
  boardShots.value.forEach((s, i) => {
    const n = shotNum(s.id)
    if (n >= Math.min(a, b) && n <= Math.max(a, b)) set.add(i)
  })
  return set
})

function pickShot(i: number) {
  const id = boardShots.value[i]?.id
  if (!id) return
  if (anchor.value === null) {
    anchor.value = i
    setShots(`${id}-${id}`)
  } else {
    const lo = Math.min(anchor.value, i)
    const hi = Math.max(anchor.value, i)
    setShots(`${boardShots.value[lo].id}-${boardShots.value[hi].id}`)
    anchor.value = null
  }
}

/** 区间输入框更新；点选产生的更新不清锚点，手动编辑则清除 */
let pickGuard = false
function setShots(v: string) {
  pickGuard = true
  shots.value = v
  pickGuard = false
}
watch(shots, () => {
  if (!pickGuard) anchor.value = null
})

async function loadBoard() {
  board.value = null
  anchor.value = null
  if (!app.current || !storyboard.value) return
  boardLoading.value = true
  try {
    board.value = await fetchWhiteBoard(app.current, storyboard.value)
  } catch (e) {
    toast(e instanceof Error ? e.message : '分镜读取失败', 'err')
    // 选中的分镜已被删除/改名（如 localStorage 残留 剧本_全本.json）：自动回落到最新分镜，避免反复 404
    if (boards.value.length && !boards.value.includes(storyboard.value)) {
      storyboard.value = boards.value[0] || ''
    }
  } finally {
    boardLoading.value = false
  }
}
watch(storyboard, loadBoard)

async function run() {
  if (!app.current || !storyboard.value) {
    toast('请先选择分镜 JSON', 'err')
    return
  }
  if (!/^\s*S?\d+\s*-\s*S?\d+\s*$/i.test(shots.value)) {
    toast('镜头区间格式如 S3-S8', 'err')
    return
  }
  running.value = true
  try {
    const r = await runWhiterange({
      project: app.current,
      json: `分镜/${storyboard.value}`,
      shots: shots.value.replace(/\s/g, '').toUpperCase(),
      engine: engine.value
    })
    toast(`白模任务 #${r.id} 已启动`, 'ok')
    const j = await trackJob(r.id, `白模渲染 ${storyboard.value} ${shots.value}`)
    if (j.success) { toast('白模渲染完成', 'ok'); await loadBasics() }
    else toast('渲染失败，详情见任务抽屉', 'err')
  } catch (e) {
    toast(e instanceof Error ? e.message : '启动失败', 'err')
  } finally {
    running.value = false
  }
}

async function genFromAnalysis() {
  if (!app.current) return
  converting.value = true
  try {
    const r = await whiteFromAnalysis({
      project: app.current,
      analysis: analysis.value || undefined,
      style: pose.value
    }) as any
    if (r?.job && r.id) { const j = await trackJob(r.id, '从解构生成分镜'); if (!j.success) throw new Error(j.err || '生成失败') }
    await loadBasics()
    // 产物名规则：与 analysis 同名（analysis_to_storyboard 落 分镜/<analysis>.json）
    const sbName = r.name || (analysis.value ? analysis.value + '.json' : '')
    // 只有文件真的出现在分镜列表才切换选中，避免拿到幽灵名字反复 404
    if (sbName && projectFiles('分镜', /\.json$/i).includes(sbName)) {
      if (storyboard.value === sbName) loadBoard()  // 同名重生成：watch 不触发，强制刷新分镜看板
      else storyboard.value = sbName
      toast(`已生成分镜：${sbName}`, 'ok', 4000)
    } else {
      toast(sbName ? `生成任务已结束，但列表中未找到 ${sbName}，请检查任务日志` : '生成完成', sbName ? 'err' : 'ok', 5000)
    }
  } catch (e) {
    toast(e instanceof Error ? e.message : '生成失败', 'err')
  } finally {
    converting.value = false
  }
}

/* 预演包导出：逐镜干净帧+表演提示词+manifest，供图生视频当参考输入（异步 job，日志末行为产物目录） */
const exporting = ref(false)
async function doExportPreviz() {
  if (!app.current || !storyboard.value) return
  exporting.value = true
  try {
    const r = await exportPreviz({ project: app.current, json: '分镜/' + storyboard.value })
    if (!r.id) throw new Error(r.err || '导出任务未启动')
    toast(`预演包导出任务 #${r.id} 已启动`, 'ok')
    const j = await trackJob(r.id, `导出预演包 ${storyboard.value}`)
    if (j.success) {
      const dir = (j.out || '').trim().split('\n').filter(Boolean).pop() || ''
      await loadBasics()
      toast(`预演包已导出${dir ? `：${dir}` : ''}`, 'ok', 5000)
    } else {
      toast('预演包导出失败，详情见任务抽屉', 'err')
    }
  } catch (e) {
    toast(e instanceof Error ? e.message : '导出失败', 'err')
  } finally {
    exporting.value = false
  }
}

async function refreshAnalyses() {
  if (!app.current) {
    analyses.value = []
    return
  }
  try {
    analyses.value = await fetchAnalysisList(app.current)
    if (!analyses.value.find((a) => a.name === analysis.value)) {
      analysis.value = analyses.value[analyses.value.length - 1]?.name || ''
    }
  } catch {
    analyses.value = []
  }
}

/** 删除当前对比选中的白模视频。 */
async function delCmp() {
  if (!app.current || !cmpVideo.value) return
  if (!confirm(`确定删除「${cmpVideo.value.split('/').pop()}」？删除不可恢复`)) return
  try {
    await deleteFile(app.current, cmpVideo.value.replace(`projects/${app.current}/`, ''))
    toast('已删除', 'ok')
    await loadBasics()   // products 联动刷新，cmpVideo watch 会自动换到剩余最新
  } catch (e) {
    toast(e instanceof Error ? e.message : '删除失败', 'err')
  }
}

watch(() => app.current, () => {
  storyboard.value = ''
  srcVideo.value = ''
  // 从拉片页「去渲染」跳转时自动选中对应分镜
  const pending = localStorage.getItem('wb.white.board')
  if (pending) {
    localStorage.removeItem('wb.white.board')
    if (projectFiles('分镜', /\.json$/i).includes(pending)) storyboard.value = pending
  }
  refreshAnalyses()
}, { immediate: true })

// 未选中且有分镜时默认选第一个（在跳转逻辑之后，跳转命中时优先）
watch([boards, storyboard], () => {
  // 分镜选择由 useBoardSelection 统一恢复。 //
}, { immediate: true })
useBoardSelection(storyboard, boards, 'white')
</script>

<template>
  <div class="page">
    <header class="mb-6">
      <h1 class="grad-text text-2xl font-black">① 辅助·白模</h1>
      <p class="mt-1 text-xs text-slate-500">按分镜 JSON 渲染指定镜头区间的白模视频（锁机位/走位/景别/节奏，不做美术）</p>
    </header>

    <!-- 参数条 -->
    <div class="glass mb-5 flex flex-wrap items-end gap-3 p-4">
      <label class="min-w-64 text-xs text-slate-400">
        分镜 JSON（分镜/）
        <StyledSelect v-model="storyboard" class="mt-1" :options="boards" :storage-key="`wb.${app.current}.white.board`" placeholder="— 选择分镜 —" />
      </label>
      <label class="w-36 text-xs text-slate-400">
        镜头区间
        <input v-model="shots" class="input mt-1 tabular-nums" placeholder="S3-S8" />
      </label>
      <label class="w-44 text-xs text-slate-400">
        引擎
        <StyledSelect
          v-model="engine"
          class="mt-1"
          :options="['auto', 'dialogue', 'previs']"
          :labels="ENGINE_LABELS"
          :storage-key="`wb.${app.current}.white.engine`"
        />
      </label>
      <button class="btn" :disabled="running || !storyboard" @click="run">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path :d="icons.cube" stroke-linecap="round" stroke-linejoin="round"/></svg>
        {{ running ? '渲染中…' : '渲染白模' }}
      </button>
      <button
        class="btn btn-ghost"
        :disabled="exporting || !storyboard"
        title="逐镜导出干净预演帧+表演提示词+manifest（无字幕HUD，供图生视频模型当参考输入）"
        @click="doExportPreviz"
      >
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3v12m0 0l-4-4m4 4l4-4M4 17v2a2 2 0 002 2h12a2 2 0 002-2v-2" stroke-linecap="round" stroke-linejoin="round"/></svg>
        {{ exporting ? '导出中…' : '导出预演包' }}
      </button>
    </div>

    <!-- 从拉片解构生成分镜 -->
    <div v-if="app.current" class="glass mb-5 flex flex-wrap items-end gap-3 p-4">
      <div class="mr-auto">
        <p class="text-xs font-bold text-slate-300">没有分镜？从拉片结构一键生成</p>
        <p class="mt-0.5 text-xs-plus text-slate-500">把解构镜头表翻译成白模引擎契约：景别→机位、台词→镜内字幕（取 AI 归属后的真实人名）</p>
      </div>
      <label class="min-w-56 text-xs text-slate-400">
        拉片版本
        <StyledSelect v-model="analysis" class="mt-1" :options="analyses.map((a) => a.name)" :storage-key="`wb.${app.current}.white.analysis`" placeholder="— 选择解构版本 —" />
      </label>
      <label class="w-44 text-xs text-slate-400">
        人物姿态
        <StyledSelect v-model="pose" class="mt-1" :options="['stand', 'seated']" :labels="POSE_LABELS" :storage-key="`wb.${app.current}.white.pose`" />
      </label>
      <button class="btn btn-ghost" :disabled="converting || !analyses.length" @click="genFromAnalysis">
        {{ converting ? '生成中…' : '从解构生成分镜' }}
      </button>
    </div>

    <EmptyState v-if="!app.current" title="请先在左侧选择项目" />

    <!-- 分镜内容可视化 -->
    <section v-if="app.current && storyboard" class="mb-5">
      <div v-if="boardLoading" class="glass p-10 text-center text-sm text-slate-500">读取分镜中…</div>
      <template v-else-if="board">
        <div class="mb-2 flex flex-wrap items-center gap-3">
          <h3 class="text-xs font-bold text-slate-500">
            分镜内容 · {{ board.title || storyboard }} — {{ boardShots.length }} 镜 · 共 {{ fmtT(boardDur) }}
          </h3>
          <div class="flex flex-wrap items-center gap-1.5">
            <span
              v-for="a in boardActors"
              :key="a.aid"
              class="flex items-center gap-1 rounded-full bg-white/5 px-2 py-0.5 text-2xs text-slate-300"
            >
              <i class="h-2 w-2 rounded-full" :style="{ background: a.color }"></i>{{ a.name }}
            </span>
          </div>
          <span class="text-2xs text-slate-500">点两个镜头选定渲染区间</span>
        </div>
        <div class="glass max-h-96 space-y-1 overflow-y-auto p-2">
          <button
            v-for="(s, i) in boardShots"
            :key="s.id || i"
            class="flex w-full items-start gap-3 rounded-lg px-3 py-2 text-left transition"
            :class="rangeIdx.has(i)
              ? 'bg-sky-400/15 ring-1 ring-sky-400/40'
              : anchor === i
                ? 'bg-amber-400/15 ring-1 ring-amber-400/50'
                : 'hover:bg-white/5'"
            @click="pickShot(i)"
          >
            <span class="shrink-0 rounded bg-white/10 px-1.5 py-0.5 text-xs-plus font-black text-sky-300">{{ s.id }}</span>
            <span class="shrink-0 rounded bg-white/5 px-1.5 py-0.5 text-2xs text-slate-400">{{ CAM_LABELS[s.cam || ''] || s.cam || '—' }}</span>
            <span class="shrink-0 text-2xs tabular-nums text-slate-500">{{ (s.dur || 0).toFixed(1) }}s</span>
            <span class="min-w-0 flex-1">
              <span v-if="s.lines?.length" class="block space-y-0.5">
                <span v-for="(L, li) in s.lines.slice(0, 2)" :key="li" class="block truncate text-xs-plus" :style="{ color: actorColor(L.speaker) }">
                  【{{ actorName(L.speaker) || '?' }}】{{ L.line }}
                </span>
                <span v-if="s.lines.length > 2" class="block text-2xs text-slate-500">…共 {{ s.lines.length }} 条台词</span>
              </span>
              <span v-else-if="s.line" class="block truncate text-xs-plus text-slate-300">{{ s.line }}</span>
              <span v-if="s.action" class="mt-0.5 block truncate text-2xs text-slate-500">动作：{{ s.action }}</span>
            </span>
            <span class="shrink-0 text-2xs text-slate-500">{{ s.move || '' }}</span>
          </button>
        </div>
      </template>
    </section>

    <!-- 原片 vs 白模 对比（有白模视频时默认展开） -->
    <section v-if="app.current && products.length" class="glass mb-5 p-4">
      <h3 class="mb-3 flex items-center gap-2 text-xs font-bold text-slate-400">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#38bdf8" stroke-width="2"><path :d="icons.film" stroke-linecap="round" stroke-linejoin="round"/></svg>
        原片 vs 白模（播放/暂停/拖动双向联动）
      </h3>
      <div class="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <div class="overflow-hidden rounded-xl border border-line-soft bg-black/40">
          <video
            ref="leftEl"
            :src="srcVideo ? mediaUrl(srcVideo) : ''"
            controls
            class="aspect-video w-full bg-black"
            preload="metadata"
            @play="syncFrom('l')"
            @pause="syncFrom('l')"
            @seeked="syncFrom('l')"
          ></video>
          <div class="flex items-center gap-2 px-3 py-2">
            <span class="rounded-full bg-amber-400/10 px-2 py-0.5 text-2xs font-bold text-amber-300">原片</span>
            <span class="min-w-0 flex-1 truncate text-xs-plus text-slate-400" :title="srcVideo">
              {{ srcVideo ? srcVideo.split('/').pop() : '该项目 拉片素材/ 下没有视频' }}
            </span>
            <UploadButton @uploaded="loadBasics" />
          </div>
        </div>
        <div class="overflow-hidden rounded-xl border border-line-soft bg-black/40">
          <video
            ref="rightEl"
            :src="cmpVideo ? mediaUrl(cmpVideo) : ''"
            controls
            class="aspect-video w-full bg-black"
            preload="metadata"
            @play="syncFrom('r')"
            @pause="syncFrom('r')"
            @seeked="syncFrom('r')"
          ></video>
          <div class="flex items-center gap-2 px-3 py-2">
            <span class="rounded-full bg-sky-400/10 px-2 py-0.5 text-2xs font-bold text-sky-300">白模</span>
            <StyledSelect
              v-if="cmpOptions.length > 1"
              v-model="cmpVideo"
              class="min-w-0 flex-1"
              :options="cmpOptions"
              :labels="cmpLabels"
            />
            <span v-else class="min-w-0 flex-1 truncate text-xs-plus text-slate-400" :title="cmpVideo">{{ cmpVideo.split('/').pop() }}</span>
            <a class="btn btn-ghost btn-sm shrink-0" :href="mediaUrl(cmpVideo)" :download="cmpVideo.split('/').pop()">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path :d="icons.download" stroke-linecap="round" stroke-linejoin="round"/></svg>
              下载
            </a>
            <button
              class="btn btn-danger btn-sm shrink-0"
              :title="`删除当前对比白模 ${cmpVideo.split('/').pop()}`"
              @click="delCmp"
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path :d="icons.trash" stroke-linecap="round" stroke-linejoin="round"/></svg>
            </button>
          </div>
        </div>
      </div>
    </section>

    <!-- 产物 -->
    <section v-if="app.current">
      <h3 class="mb-2 text-xs font-bold text-slate-500">白模产物（白模/*.mp4）</h3>
      <div v-if="!products.length" class="glass p-12 text-center">
        <svg class="mx-auto mb-3 opacity-40" width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="#38bdf8" stroke-width="1.5"><path :d="icons.cube" stroke-linecap="round" stroke-linejoin="round"/></svg>
        <p class="text-sm text-slate-300">该项目还没有白模视频</p>
        <p class="mt-2 text-xs text-slate-500">没有分镜 JSON？先用上方「从解构生成分镜」把拉片版本转成白模分镜，再选择区间渲染</p>
      </div>
      <div v-else class="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <div v-for="p in products" :key="p.path" class="glass glass-hover group relative overflow-hidden" :style="{ '--glow': 'rgba(56,189,248,0.35)' }">
          <DelBadge :path="`白模/${p.file}`" :label="p.file" />
          <video :src="mediaUrl(p.path)" controls class="aspect-video w-full bg-black" preload="metadata"></video>
          <div class="flex items-center justify-between gap-1.5 p-2.5">
            <span class="truncate text-xs-plus text-slate-300" :title="p.file">{{ p.file }}</span>
            <Versions :path="p.path" kind="file" @restored="loadBasics" />
            <a class="btn btn-ghost btn-sm shrink-0" :href="mediaUrl(p.path)" :download="p.file">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path :d="icons.download" stroke-linecap="round" stroke-linejoin="round"/></svg>
            </a>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

