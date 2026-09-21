<script setup lang="ts">
// -*- coding: utf-8 -*-
/** 全局后台任务抽屉：右下角悬浮，显示运行中/历史任务、绿字日志尾部、耗时、PROGRESS 进度条。 */
import { ref, computed, watch, nextTick, onBeforeUnmount } from 'vue'
import { jobs, logTail, runningCount, type TrackedJob } from '../stores/jobs'
import { fmtClock, fetchJobs, fetchJob, clearFinishedJobs, type JobSummary, type JobInfo } from '../api'
import { icons } from './icons'

const open = ref(false)
const tab = ref<'cur' | 'his'>('cur')
const hasRunning = computed(() => runningCount() > 0)

/* ---------- 历史任务（磁盘落盘记录，跨会话可查） ---------- */
const history = ref<JobSummary[]>([])
const hisLoading = ref(false)
const hisDetail = ref<Record<number, JobInfo | 'loading' | 'error'>>({})

async function loadHistory() {
  hisLoading.value = true
  try {
    history.value = (await fetchJobs()).jobs
  } catch {
    history.value = []
  } finally {
    hisLoading.value = false
  }
}

/** 清空已结束的历史任务（running 保留）；历史 tab 每 5s 自动刷新，无需手动刷新按钮。 */
const clearing = ref(false)
async function clearFinished() {
  const done = history.value.filter((h) => h.status !== 'running').length
  if (!done || clearing.value) return
  if (!window.confirm(`清空 ${done} 条已结束的历史任务记录？运行中的任务会保留。`)) return
  clearing.value = true
  try {
    await clearFinishedJobs()
    hisDetail.value = {}
    await loadHistory()
  } catch {
    // 失败时保留下一轮自动刷新再试
  } finally {
    clearing.value = false
  }
}

watch([open, tab], ([o, t]) => {
  if (o && t === 'his') {
    loadHistory()
    // 历史 tab 打开期间每 5s 自动刷新：摘要与已展开且仍在 running 的明细都重拉，
    // 否则任务在抽屉打开时完成后状态会一直冻结成「运行中」（#266 实证）。
    hisTimer = window.setInterval(() => {
      loadHistory()
      for (const [id, detail] of Object.entries(hisDetail.value)) {
        if (detail !== 'loading' && detail !== 'error' && detail.status === 'running') {
          fetchJob(Number(id))
            .then((j) => { hisDetail.value = { ...hisDetail.value, [Number(id)]: j } })
            .catch(() => { /* 保留旧明细，下轮再试 */ })
        }
      }
    }, 5000)
  } else if (hisTimer) {
    window.clearInterval(hisTimer)
    hisTimer = undefined
  }
})
let hisTimer: number | undefined
onBeforeUnmount(() => { if (hisTimer) window.clearInterval(hisTimer) })

function toggleHisDetail(id: number) {
  if (hisDetail.value[id]) {
    const d = { ...hisDetail.value }
    delete d[id]
    hisDetail.value = d
    return
  }
  hisDetail.value = { ...hisDetail.value, [id]: 'loading' }
  fetchJob(id)
    .then((j) => {
      hisDetail.value = { ...hisDetail.value, [id]: j }
    })
    .catch(() => {
      hisDetail.value = { ...hisDetail.value, [id]: 'error' }
    })
}

function fmtTs(epoch?: number): string {
  if (!epoch) return ''
  const d = new Date(epoch * 1000)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

const STEP_LABELS: Record<string, string> = {
  depth: '深度图', silhouette: '剪影', explain: '镜头讲解', lines: '台词', analysis: '拉片解构',
  analysis_ai: 'AI 解构', frames: '逐帧提取', create: '模拟创作', whiterange: '白模区间', render: '白模渲染'
}
const stepLabel = (s?: string) => (s && STEP_LABELS[s]) || s || '任务'

const dotColor = (s: string, ok?: boolean) =>
  s === 'running' ? '#34d399' : ok === false || s === 'error' || s === 'failed' ? '#f87171' : '#38bdf8'

interface Progress {
  done: number
  total: number
  pct: number
}

const PROGRESS_RE = /PROGRESS\s+(\d+)\/(\d+)\s*\((\d+(?:\.\d+)?)%\)/

/** 从任务日志中找最后一个 PROGRESS 行（深度/剪影打印 `PROGRESS done/total (pct%)`），无则 null。 */
function progressOf(j: TrackedJob): Progress | null {
  const lines = (j.out || '').trim().split('\n')
  for (let i = lines.length - 1; i >= 0; i--) {
    const m = lines[i].match(PROGRESS_RE)
    if (m) {
      return { done: Number(m[1]), total: Number(m[2]), pct: Math.min(100, Number(m[3])) }
    }
  }
  return null
}

/** 任务 + 解析后的进度，供模板一次性取用。 */
const jobRows = computed(() => jobs.map((j) => ({ j, p: progressOf(j) })))

/** 完整日志展开状态（按 trackId）；展开时可滚动看全部输出。 */
const expanded = ref(new Set<number>())
const logEls = new Map<number, HTMLElement>()

function setLogEl(id: number, el: unknown) {
  if (el) logEls.set(id, el as HTMLElement)
  else logEls.delete(id)
}

function toggleExpand(id: number) {
  const s = new Set(expanded.value)
  if (s.has(id)) s.delete(id)
  else s.add(id)
  expanded.value = s
  nextTick(() => scrollLogToBottom(id))
}

function scrollLogToBottom(id: number) {
  const el = logEls.get(id)
  if (el) el.scrollTop = el.scrollHeight
}

/** 运行中且已展开的任务：日志更新时自动跟随到底部。 */
watch(jobRows, () => {
  nextTick(() => {
    for (const { j } of jobRows.value) {
      if (j.status === 'running' && expanded.value.has(j.trackId)) scrollLogToBottom(j.trackId)
    }
  })
})
</script>

<template>
  <div class="fixed bottom-4 right-4 z-[70] flex flex-col items-end gap-2">
    <Transition name="fade-slide">
      <div
        v-if="open"
        class="glass job-drawer-panel pop-in modal-h-sm flex w-[380px] flex-col overflow-hidden"
        style="--glow: rgba(52, 211, 153, 0.3)"
      >
        <div class="flex items-center justify-between border-b border-line px-4 py-2.5">
          <div class="flex items-center gap-2 text-sm font-bold text-slate-200">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#34d399" stroke-width="2"><path :d="icons.terminal" stroke-linecap="round" stroke-linejoin="round"/></svg>
            后台任务
            <span v-if="hasRunning" class="ml-1 rounded-full bg-emerald-400/15 px-2 py-0.5 text-2xs font-semibold text-emerald-300">
              {{ runningCount() }} 个运行中
            </span>
          </div>
          <div class="flex items-center gap-1">
            <button
              class="rounded-md px-2 py-0.5 text-xs-plus transition"
              :class="tab === 'cur' ? 'chip-active' : 'chip'"
              @click="tab = 'cur'"
            >当前</button>
            <button
              class="rounded-md px-2 py-0.5 text-xs-plus transition"
              :class="tab === 'his' ? 'chip-active' : 'chip'"
              @click="tab = 'his'"
            >历史</button>
            <button class="ml-1 text-slate-500 hover:text-slate-300" @click="open = false">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path :d="icons.close" stroke-linecap="round"/></svg>
            </button>
          </div>
        </div>
        <div v-if="tab === 'cur'" class="flex-1 overflow-y-auto p-3">
          <p v-if="!jobs.length" class="py-10 text-center text-sm text-slate-500">暂无后台任务</p>
          <div
            v-for="{ j, p } in jobRows"
            :key="j.trackId"
            class="job-drawer-card mb-2 rounded-lg border p-2.5"
          >
            <div class="mb-1 flex items-center gap-2 text-xs">
              <span
                class="inline-block h-2 w-2 rounded-full"
                :class="{ 'pulse-dot': j.status === 'running' }"
                :style="{ background: dotColor(j.status, j.success ?? j.ok) }"
              />
              <span class="flex-1 truncate font-semibold text-slate-200">{{ j.label }}</span>
              <span class="text-slate-500">#{{ j.id }}</span>
              <span class="tabular-nums text-slate-500">{{ fmtClock(j.liveElapsed) }}</span>
            </div>
            <div v-if="j.status === 'running' && p" class="mb-1.5">
              <div class="progress-track">
                <div
                  class="progress-bar"
                  :class="{ 'progress-done': p.pct >= 100 }"
                  :style="{ width: p.pct + '%' }"
                ></div>
              </div>
              <div class="mt-0.5 text-right text-2xs tabular-nums text-cyan-300/80">
                {{ Math.round(p.pct) }}% · {{ p.done }}/{{ p.total }} 帧
              </div>
            </div>
            <pre
              v-if="expanded.has(j.trackId) ? (j.out || '').trim() : logTail(j)"
              :ref="(el) => setLogEl(j.trackId, el)"
              class="log-tail rounded bg-black/40 p-1.5"
              :class="expanded.has(j.trackId) ? 'max-h-72 overflow-auto' : 'max-h-28 overflow-hidden'"
            >{{ expanded.has(j.trackId) ? (j.out || '').trim() : logTail(j) }}</pre>
            <button
              v-if="(j.out || '').trim()"
              class="mt-0.5 text-2xs text-cyan-300/60 transition hover:text-cyan-200"
              @click="toggleExpand(j.trackId)"
            >{{ expanded.has(j.trackId) ? '▲ 收起日志' : '▼ 完整日志' }}</button>
            <pre
              v-if="j.err"
              class="log-tail mt-1 overflow-auto rounded bg-red-950/40 p-1.5"
              :class="expanded.has(j.trackId) ? 'max-h-72' : 'max-h-20'"
              style="color:#fca5a5"
            >{{ expanded.has(j.trackId) ? j.err.trim() : j.err.split('\n').slice(-5).join('\n') }}</pre>
            <div v-if="j.status !== 'running'" class="mt-1 text-2xs" :style="{ color: dotColor(j.status, j.success ?? j.ok) }">
              {{ (j.success ?? j.ok) ? '✓ 完成' : '✗ 失败' }}（{{ fmtClock(j.liveElapsed) }}）
            </div>
          </div>
        </div>

        <!-- 历史任务：磁盘落盘记录，跨会话/重启可查，点击展开完整日志 -->
        <div v-else class="flex-1 overflow-y-auto p-3">
          <div class="mb-2 flex items-center justify-between">
            <span class="text-2xs text-slate-500">最近 {{ history.length }} 条（含重启前）· 每 5s 自动刷新</span>
            <button
              class="btn-danger transition"
              :class="{ 'opacity-40': clearing }"
              title="清空已完成（运行中的保留）"
              @click="clearFinished"
            >
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path :d="icons.trash" stroke-linecap="round" stroke-linejoin="round"/></svg>
            </button>
          </div>
          <p v-if="!history.length && !hisLoading" class="py-10 text-center text-sm text-slate-500">暂无历史任务</p>
          <div
            v-for="h in history"
            :key="h.id"
            class="job-drawer-card mb-1.5 rounded-lg border p-2"
          >
            <button class="flex w-full items-center gap-2 text-left text-xs" @click="toggleHisDetail(h.id)">
              <span
                class="inline-block h-2 w-2 shrink-0 rounded-full"
                :style="{ background: dotColor(h.status || '', h.ok) }"
              />
              <span class="rounded bg-white/5 px-1.5 py-0.5 text-2xs font-semibold text-slate-300">{{ stepLabel(h.step) }}</span>
              <span class="text-slate-500">#{{ h.id }}</span>
              <span class="tabular-nums text-2xs text-slate-500">{{ fmtTs(h.started_at) }}</span>
              <span class="flex-1"></span>
              <span v-if="h.elapsed" class="tabular-nums text-2xs text-slate-500">{{ fmtClock(h.elapsed) }}</span>
              <span class="text-2xs" :style="{ color: dotColor(h.status || '', h.ok) }">
                {{ h.status === 'running' ? '运行中' : h.status === 'interrupted' ? '已中断' : h.ok ? '✓' : '✗' }}
              </span>
            </button>
            <div v-if="hisDetail[h.id]" class="mt-1.5">
              <p v-if="hisDetail[h.id] === 'loading'" class="py-2 text-center text-2xs text-slate-500">加载日志…</p>
              <p v-else-if="hisDetail[h.id] === 'error'" class="py-2 text-center text-2xs text-red-400">日志读取失败</p>
              <template v-else>
                <pre v-if="(hisDetail[h.id] as JobInfo).out" class="log-tail max-h-56 overflow-auto whitespace-pre-wrap rounded bg-black/40 p-1.5">{{ (hisDetail[h.id] as JobInfo).out.trim() }}</pre>
                <pre v-if="(hisDetail[h.id] as JobInfo).err" class="log-tail mt-1 max-h-32 overflow-auto whitespace-pre-wrap rounded bg-red-950/40 p-1.5" style="color:#fca5a5">{{ (hisDetail[h.id] as JobInfo).err.trim() }}</pre>
                <p v-if="!(hisDetail[h.id] as JobInfo).out && !(hisDetail[h.id] as JobInfo).err" class="py-1 text-center text-2xs text-slate-500">（无日志）</p>
              </template>
            </div>
          </div>
        </div>
      </div>
    </Transition>

    <button
      class="btn relative"
      style="--c1: #34d399; --c2: #22d3ee"
      @click="open = !open"
    >
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path :d="icons.terminal" stroke-linecap="round" stroke-linejoin="round"/></svg>
      任务
      <span
        v-if="hasRunning"
        class="absolute -right-1.5 -top-1.5 flex h-5 w-5 items-center justify-center rounded-full bg-emerald-400 text-2xs font-bold text-emerald-950"
      >{{ runningCount() }}</span>
    </button>
  </div>
</template>

<style scoped>
/* 任务抽屉需要稳定的深色底，避免页面内容透过玻璃层干扰日志阅读。 */
.job-drawer-panel {
  background: rgba(15, 23, 42, 0.96);
  border-color: rgba(148, 163, 184, 0.24);
  box-shadow:
    0 22px 64px -24px rgba(0, 0, 0, 0.9),
    0 0 0 1px rgba(2, 6, 23, 0.35);
}

.job-drawer-card {
  background: rgba(2, 6, 23, 0.78);
  border-color: rgba(148, 163, 184, 0.14);
}

/* PROGRESS 进度条：青→绿渐变 + 45° 条纹滚动；pct>=100 时完成闪光 */
.progress-track {
  height: 6px;
  border-radius: 9999px;
  background: rgba(255, 255, 255, 0.08);
  overflow: hidden;
}
.progress-bar {
  height: 100%;
  border-radius: 9999px;
  background:
    repeating-linear-gradient(45deg, rgba(255, 255, 255, 0.22) 0 8px, transparent 8px 16px),
    linear-gradient(90deg, #22d3ee, #34d399);
  background-size: 22.63px 100%, 100% 100%;
  transition: width 0.4s ease;
  animation: progress-stripes 0.8s linear infinite;
}
.progress-done {
  animation:
    progress-stripes 0.8s linear infinite,
    progress-flash 0.9s ease-out 2;
}
@keyframes progress-stripes {
  to {
    background-position: 22.63px 0, 0 0;
  }
}
@keyframes progress-flash {
  0%,
  100% {
    filter: brightness(1);
    box-shadow: 0 0 0 rgba(52, 211, 153, 0);
  }
  40% {
    filter: brightness(1.9);
    box-shadow: 0 0 14px rgba(52, 211, 153, 0.85);
  }
}
</style>
