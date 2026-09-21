<script setup lang="ts">
// -*- coding: utf-8 -*-
/** ③ 逐帧：每秒一帧网格 + IntersectionObserver 懒加载 + lightbox + 复制引用。 */
import { ref, computed, watch, nextTick, onBeforeUnmount } from 'vue'
import { runFramesExtract, fetchFrames, mediaUrl, fmtT, deleteFile, type FramesInfo } from '../api'
import { app, materialVideos, toast, loadBasics, absDiskPath } from '../stores/app'
import { trackJob } from '../stores/jobs'
import OverlayViewer from '../components/OverlayViewer.vue'
import StyledSelect from '../components/StyledSelect.vue'
import EmptyState from '../components/EmptyState.vue'
import { icons } from '../components/icons'

const info = ref<FramesInfo | null>(null)
const extracting = ref(false)
const video = ref('')
const jump = ref('')

const materialList = computed(() => materialVideos())
const videoOptions = computed(() => materialList.value.map((v) => v.rel))
const videoLabels = computed(() => Object.fromEntries(materialList.value.map((v) => [v.rel, v.file])))
watch([videoOptions, video], () => {
  if (!video.value && videoOptions.value.length) video.value = videoOptions.value.at(-1) || '' // 默认选最后一个素材视频（含项目切换回填）
}, { immediate: true })

let loadSeq = 0
async function load(opts: { resetVideo?: boolean } = {}) {
  const seq = ++loadSeq
  const project = app.current
  if (opts.resetVideo) video.value = '' // 仅项目切换时重置，提取完成后的刷新保留用户选择
  if (!project) return
  try {
    const result = await fetchFrames(project)
    if (seq !== loadSeq || project !== app.current) return
    info.value = result
  } catch {
    // 404 = 尚未提取，属正常
    if (seq !== loadSeq || project !== app.current) return
    info.value = null
  }
  await nextTick()
  setupObserver()
}

async function extract() {
  const mv = materialList.value.find((v) => v.rel === video.value)
  if (!app.current || !mv) {
    toast('请先选择项目素材视频', 'err')
    return
  }
  extracting.value = true
  try {
    const r = await runFramesExtract({ project: app.current, video: mv.abs })
    toast(`逐帧任务 #${r.id} 已启动`, 'ok')
    const j = await trackJob(r.id, `逐帧提取 ${mv.file}`)
    if (j.success) {
      toast('提取完成', 'ok')
      await load()
    } else {
      toast('提取失败，详情见任务抽屉', 'err')
    }
  } catch (e) {
    toast(e instanceof Error ? e.message : '启动失败', 'err')
  } finally {
    extracting.value = false
  }
}

/* ---------- 懒加载 ---------- */
let observer: IntersectionObserver | null = null
const visible = ref<Set<number>>(new Set())

function setupObserver() {
  observer?.disconnect()
  visible.value = new Set()
  observer = new IntersectionObserver(
    (entries) => {
      let changed = false
      for (const en of entries) {
        const i = Number((en.target as HTMLElement).dataset.i)
        if (en.isIntersecting && !visible.value.has(i)) {
          visible.value.add(i)
          changed = true
        }
      }
      if (changed) visible.value = new Set(visible.value)
    },
    { rootMargin: '300px' }
  )
  document
    .querySelectorAll<HTMLElement>('[data-frame]')
    .forEach((el) => observer?.observe(el))
}

function frameUrl(file: string) {
  return mediaUrl(`projects/${app.current}/逐帧/每秒/${file}`)
}

function absPath(file: string) {
  return absDiskPath(`projects/${app.current}/逐帧/每秒/${file}`)
}

async function copyRef(file: string) {
  try {
    await navigator.clipboard.writeText(absPath(file))
    toast('绝对路径已复制到剪贴板', 'ok')
  } catch {
    toast('复制失败（浏览器权限）', 'err')
  }
}

/* ---------- lightbox ---------- */
const lbVisible = ref(false)
const lbIndex = ref(0)
const lbImages = computed(() => (info.value?.frames || []).map((f) => frameUrl(f.file)))

function openFrame(i: number) {
  lbIndex.value = i
  lbVisible.value = true
}

/* ---------- 时间码跳转 ---------- */
function jumpTo() {
  if (!info.value?.frames?.length) return
  const parts = jump.value.split(':').map(Number)
  let sec = 0
  if (parts.length === 3) sec = parts[0] * 3600 + parts[1] * 60 + parts[2]
  else if (parts.length === 2) sec = parts[0] * 60 + parts[1]
  else sec = Number(jump.value) || 0
  let best = 0
  info.value.frames.forEach((f, i) => {
    if (f.t <= sec) best = i
  })
  const el = document.querySelector<HTMLElement>(`[data-frame][data-i="${best}"]`)
  el?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  el?.classList.add('ring-2', 'ring-emerald-400')
  setTimeout(() => el?.classList.remove('ring-2', 'ring-emerald-400'), 1600)
}

onBeforeUnmount(() => observer?.disconnect())

watch(() => app.current, () => load({ resetVideo: true }), { immediate: true })

/** 删除 逐帧/每秒/ 整目录（逐帧产物可再生）。 */
async function delFrames() {
  if (!app.current) return
  if (!confirm(`确定删除「${app.current}/逐帧/每秒/」全部 ${info.value?.frames?.length ?? '?'} 帧？可重新提取`)) return
  try {
    await deleteFile(app.current, '逐帧/每秒')
    toast('逐帧产物已删除', 'ok')
    info.value = null
    await loadBasics()
  } catch (e) {
    toast(e instanceof Error ? e.message : '删除失败', 'err')
  }
}
</script>

<template>
  <div class="page">
    <header class="mb-6">
      <h1 class="grad-text text-2xl font-black">③ 逐帧拉片</h1>
      <p class="mt-1 text-xs text-slate-500">每秒抽一帧，用于细读动作与构图；点击帧放大，可复制绝对路径引用</p>
    </header>

    <!-- 工具条 -->
    <div class="glass mb-5 flex flex-wrap items-end gap-3 p-4">
      <label class="min-w-56 text-xs text-slate-400">
        源视频（项目 拉片素材/）
        <StyledSelect
          v-model="video"
          class="mt-1"
          :options="videoOptions"
          :labels="videoLabels"
          :storage-key="`wb.${app.current}.frames.video`"
          placeholder="— 选择素材视频 —"
        />
      </label>
      <p v-if="app.current && !materialList.length" class="mb-2 self-center rounded-lg bg-amber-400/10 px-3 py-1.5 text-xs-plus text-amber-300">
        素材为空：先到首页「从 Downloads 导入」
      </p>
      <button class="btn" :disabled="extracting || !app.current" @click="extract">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path :d="icons.frames" stroke-linecap="round" stroke-linejoin="round"/></svg>
        {{ extracting ? '提取中…' : '开始提取' }}
      </button>
      <div class="flex-1"></div>
      <label v-if="info?.frames?.length" class="flex items-center gap-2 text-xs text-slate-400">
        跳转
        <input
          v-model="jump" class="input w-28 tabular-nums" placeholder="mm:ss 或秒"
          @keyup.enter="jumpTo"
        />
        <button class="btn btn-ghost btn-sm" @click="jumpTo">跳转</button>
      </label>
    </div>

    <EmptyState v-if="!app.current" title="请先在左侧选择项目" />

    <div v-else-if="!info || !info.frames?.length" class="glass p-12 text-center">
      <svg class="mx-auto mb-3 opacity-40" width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="#34d399" stroke-width="1.5"><path :d="icons.frames" stroke-linecap="round" stroke-linejoin="round"/></svg>
      <p class="text-sm text-slate-300">该项目还没有逐帧结果</p>
      <p class="mt-2 text-xs text-slate-500">选择上方视频后点「开始提取」，产物写入 逐帧/每秒/</p>
    </div>

    <template v-else>
      <div class="mb-3 flex items-center gap-2 text-xs text-slate-500">
        <span class="min-w-0 flex-1 truncate">
          源：{{ info.source || '—' }} · fps={{ info.fps ?? '?' }} · 共 {{ info.frames.length }} 帧（每秒一帧）
        </span>
        <button
          class="btn btn-danger btn-sm shrink-0"
          title="删除 逐帧/每秒/ 整个目录（可重新提取）"
          @click="delFrames"
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path :d="icons.trash" stroke-linecap="round" stroke-linejoin="round"/></svg>
          删除逐帧产物
        </button>
      </div>
      <div class="grid grid-cols-2 gap-2 sm:grid-cols-4 md:grid-cols-6 xl:grid-cols-8">
        <div
          v-for="(f, i) in info.frames"
          :key="f.file"
          :data-frame="f.file"
          :data-i="i"
          class="group relative cursor-zoom-in overflow-hidden rounded-lg border border-line transition hover:border-emerald-400/50"
          @click="openFrame(i)"
        >
          <img
            v-if="visible.has(i)"
            :src="frameUrl(f.file)"
            class="aspect-video w-full object-cover transition-transform duration-300 group-hover:scale-105"
            loading="lazy"
            alt="帧"
          />
          <div v-else class="aspect-video w-full bg-white/5"></div>
          <span class="absolute left-1 top-1 rounded bg-black/70 px-1 py-0.5 text-2xs font-bold tabular-nums text-emerald-300">
            {{ fmtT(f.t) }}
          </span>
          <button
            class="absolute bottom-1 right-1 rounded bg-black/70 p-1 text-slate-300 opacity-0 transition group-hover:opacity-100"
            title="复制绝对路径"
            @click.stop="copyRef(f.file)"
          >
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path :d="icons.copy" stroke-linecap="round" stroke-linejoin="round"/></svg>
          </button>
        </div>
      </div>
    </template>

    <OverlayViewer
      :images="lbImages"
      :index="lbIndex"
      :visible="lbVisible"
      @close="lbVisible = false"
      @update-index="lbIndex = $event"
    />
  </div>
</template>
