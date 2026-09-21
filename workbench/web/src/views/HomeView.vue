<script setup lang="ts">
// -*- coding: utf-8 -*-
/** 首页：电影感项目海报网格 + 模块完成度进度环 + 新建项目/从 Downloads 导入。 */
import { ref, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import { app, selectProject, toast, loadBasics } from '../stores/app'
import { importVideo, importSrc, normalizeProject, newProject } from '../api'
import { icons } from '../components/icons'
import StyledSelect from '../components/StyledSelect.vue'
import ProgressRing from '../components/ProgressRing.vue'
import type { Project } from '../api'

interface CardStats {
  videos: number
  analyses: number
  rings: { label: string; value: number; color: string }[]
  total: number
}

const router = useRouter()

const MODULES: { key: string; label: string; re: RegExp; color: string }[] = [
  { key: '拉片', label: '拉片', re: /analysis\.json$|\.md$/i, color: '#e879f9' },
  { key: '分镜', label: '台词/分镜', re: /srt|台词|json|xlsx/i, color: '#fbbf24' },
  { key: '逐帧', label: '逐帧', re: /\.(jpg|jpeg|png)$/i, color: '#34d399' },
  { key: '白模', label: '白模', re: /\.mp4$/i, color: '#38bdf8' }
]

function stats(p: Project): CardStats {
  const videos =
    (p.dirs['拉片素材'] || []).filter((f) => /\.(mp4|mov|mkv)$/i.test(f)).length +
    (p.dirs['成片'] || []).filter((f) => /\.(mp4|mov|mkv)$/i.test(f)).length
  const lapian = (p.dirs['拉片'] || []).filter((f) => !f.startsWith('[帧序列]'))
  const analyses = new Set(
    lapian.filter((f) => f.includes('/')).map((f) => f.split('/')[0])
  ).size || (lapian.some((f) => /analysis\.json|拉片.*\.md/i.test(f)) ? 1 : 0)

  const rings = MODULES.map((m) => {
    const files = p.dirs[m.key] || []
    // 帧目录大量 jpg 会被服务端折叠成 "[帧序列] xxx/（共N个文件）"，同样算已有产物
    const ok = files.some(
      (f) => (!f.startsWith('[帧序列]') && m.re.test(f)) || (m.key === '逐帧' && f.startsWith('[帧序列]'))
    )
    return { label: m.label, color: m.color, value: (ok ? 1 : 0) as number }
  })
  const total = rings.reduce((a, b) => a + b.value, 0) / rings.length
  return { videos, analyses, rings, total }
}

const cards = computed(() =>
  app.projects.map((p, i) => ({ p, i, s: stats(p) }))
)

function openProject(name: string) {
  selectProject(name)
}

const posterHue = (i: number) => [
  'linear-gradient(135deg,#312e81,#701a75)',
  'linear-gradient(135deg,#083344,#134e4a)',
  'linear-gradient(135deg,#4a044e,#831843)',
  'linear-gradient(135deg,#1e3a8a,#0c4a6e)',
  'linear-gradient(135deg,#3b0764,#312e81)',
  'linear-gradient(135deg,#7c2d12,#713f12)'
][i % 6]

/* ---------- 新建项目 ---------- */
const createVisible = ref(false)
const createName = ref('')
const createFile = ref<File | null>(null)
const createType = ref<'拆片' | '制作'>('拆片')
const creating = ref(false)

const createNameError = computed(() => {
  const n = createName.value.trim()
  if (!n) return ''
  if (/[/\\]/.test(n)) return '项目名不能含 / 或 \\'
  if (app.projects.some((p) => p.name === n)) return '项目已存在'
  return ''
})

async function submitCreate() {
  const name = createName.value.trim()
  if (!name) {
    toast('请填写项目名', 'err')
    return
  }
  if (createNameError.value) {
    toast(createNameError.value, 'err')
    return
  }
  creating.value = true
  try {
    if (createType.value === '制作') {
      await newProject(name, '制作')
      toast(`制作项目「${name}」已创建，去剧本创作页开始`, 'ok')
    } else {
      if (!createFile.value) {
        toast('拆片项目请选择一个本地视频作为第一个素材', 'err')
        creating.value = false
        return
      }
      await importVideo(name, createFile.value.name, createFile.value)
      toast(`拆片项目「${name}」已创建，视频已导入 拉片素材/`, 'ok')
    }
    createVisible.value = false
    createName.value = ''
    createFile.value = null
    await loadBasics()
    selectProject(name)
    if (createType.value === '制作') router.push('/studio')
  } catch (e) {
    toast(e instanceof Error ? e.message : '创建失败', 'err')
  } finally {
    creating.value = false
  }
}

/* ---------- 从 Downloads 导入 ---------- */
const importVisible = ref(false)
const importProject = ref('')
const importSrcName = ref('')
const importing = ref(false)

const isLocal = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'

function openImport() {
  if (!isLocal) { toast('从 Downloads 导入仅限服务器本机使用；局域网设备请用「新建项目」上传', 'info', 5000); return }
  importProject.value = app.current || app.projects[0]?.name || ''
  importSrcName.value = ''
  importVisible.value = true
}

async function submitImport() {
  if (!importProject.value || !importSrcName.value) {
    toast('请选择项目与源视频', 'err')
    return
  }
  importing.value = true
  try {
    const r = await importSrc(importProject.value, importSrcName.value)
    toast(`已导入 ${r.path.split('/').pop()} → 拉片素材/`, 'ok')
    importVisible.value = false
    await loadBasics()
    selectProject(importProject.value)
  } catch (e) {
    toast(e instanceof Error ? e.message : '导入失败', 'err')
  } finally {
    importing.value = false
  }
}

watch(createVisible, (v) => {
  if (v) {
    createName.value = ''
    createFile.value = null
  }
})

/* ---------- 整理项目（dry-run → 确认执行） ---------- */
const normVisible = ref(false)
const normProject = ref('')
const normPlan = ref<string[]>([])
const normPhase = ref<'idle' | 'preview' | 'applying' | 'done'>('idle')
const normError = ref('')

function openNorm() {
  normProject.value = app.current || app.projects[0]?.name || ''
  normPlan.value = []
  normPhase.value = 'idle'
  normError.value = ''
  normVisible.value = true
}

async function runNormDry() {
  if (!normProject.value) {
    toast('请选择项目', 'err')
    return
  }
  normPhase.value = 'applying'
  normError.value = ''
  try {
    const r = await normalizeProject(normProject.value, false)
    if (!r.ok || !r.plan) throw new Error(r.err || '生成计划失败')
    normPlan.value = r.plan
    normPhase.value = 'preview'
  } catch (e) {
    normError.value = e instanceof Error ? e.message : '生成计划失败'
    normPhase.value = 'idle'
  }
}

async function runNormApply() {
  normPhase.value = 'applying'
  try {
    const r = await normalizeProject(normProject.value, true)
    if (!r.ok || !r.plan) throw new Error(r.err || '执行失败')
    normPlan.value = r.plan
    normPhase.value = 'done'
    toast(`「${normProject.value}」整理完成`, 'ok')
    await loadBasics()
  } catch (e) {
    normError.value = e instanceof Error ? e.message : '执行失败'
    normPhase.value = 'preview'
  }
}
</script>

<template>
  <div class="page">
    <!-- 大标题 -->
    <header class="mb-10">
      <h1 class="grad-text text-4xl font-black tracking-[0.08em] md:text-5xl" style="--c1:#22d3ee;--c2:#e879f9">
        AI 短片分析工作台
      </h1>
      <p class="mt-3 max-w-2xl text-sm leading-relaxed text-slate-400">
        从源视频到台词时间轴、拉片解构、逐帧取样、深度校准与白模预演——一切以结构化数据为源，
        让每一镜的镜头语言都可拆解、可复刻、可再创作。
      </p>
    </header>

    <!-- 两大入口 -->
    <div class="mb-10 grid gap-4 md:grid-cols-2">
      <button
        class="glass glass-hover group relative overflow-hidden p-6 text-left"
        @click="router.push('/studio')"
      >
        <div class="mb-2 flex items-center gap-3">
          <span class="flex h-11 w-11 items-center justify-center rounded-xl bg-gradient-to-br from-pink-500 to-fuchsia-500 text-white shadow-lg">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3l1.8 4.6L18 9l-4.2 1.4L12 15l-1.8-4.6L6 9l4.2-1.4L12 3zM19 15l.9 2.3L22 18l-2.1.7L19 21l-.9-2.3L16 18l2.1-.7L19 15z" stroke-linecap="round" stroke-linejoin="round"/></svg>
          </span>
          <div>
            <div class="text-lg font-black text-slate-100">制作 · 剧本创作</div>
            <div class="text-xs-plus tracking-wider text-pink-300/80">SCRIPT → SHOTS → WHITEBOX</div>
          </div>
        </div>
        <p class="text-xs leading-relaxed text-slate-400">
          本地剧本 → LLM 分集 → 人物/场景/道具 → 分镜（知识库注入）→ Blender 白模 + 平面运镜图 + 创作包。
          无片可拆、从零创作从这里进。
        </p>
      </button>
      <button
        class="glass glass-hover group relative overflow-hidden p-6 text-left"
        @click="router.push('/lapian')"
      >
        <div class="mb-2 flex items-center gap-3">
          <span class="flex h-11 w-11 items-center justify-center rounded-xl bg-gradient-to-br from-fuchsia-500 to-violet-500 text-white shadow-lg">
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 5h18v14H3zM7 5v14M17 5v14" stroke-linecap="round" stroke-linejoin="round"/></svg>
          </span>
          <div>
            <div class="text-lg font-black text-slate-100">拆片 · 拉片解构</div>
            <div class="text-xs-plus tracking-wider text-fuchsia-300/80">VIDEO → STRUCTURE</div>
          </div>
        </div>
        <p class="text-xs leading-relaxed text-slate-400">
          成片反推结构：拉片解构 / 台词归属 / 逐帧 / 深度校准 / 白模对位。
          手里有片要学镜头语言、做逐帧复刻从这里进。
        </p>
      </button>
    </div>

    <!-- 导入工具条 -->
    <div class="mb-8 flex flex-wrap items-center gap-3">
      <button
        class="btn px-6 py-3 text-base shadow-[0_0_28px_-6px_rgba(34,211,238,0.7)] transition-shadow hover:shadow-[0_0_36px_-4px_rgba(34,211,238,0.9)]"
        @click="createVisible = true"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path :d="icons.plus" stroke-linecap="round"/></svg>
        新建项目
      </button>
      <button class="btn btn-ghost" @click="openImport">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path :d="icons.download" stroke-linecap="round" stroke-linejoin="round"/></svg>
        从 Downloads 导入
      </button>
      <button class="btn btn-ghost" @click="openNorm">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path :d="icons.folder" stroke-linecap="round" stroke-linejoin="round"/></svg>
        整理项目
      </button>
      <span class="text-xs-plus text-slate-500">新建 = 项目目录 + 首个素材视频；导入 = 把 Downloads 源视频复制进已有项目 拉片素材/</span>
    </div>

    <div v-if="app.loading" class="py-20 text-center text-sm text-slate-500">正在扫描项目目录…</div>

    <div v-else-if="!app.projects.length" class="glass mx-auto max-w-md p-10 text-center" style="--glow: rgba(34,211,238,0.4)">
      <svg class="mx-auto mb-4 opacity-50" width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="#22d3ee" stroke-width="1.5"><path :d="icons.folder" stroke-linecap="round" stroke-linejoin="round"/></svg>
      <p class="font-bold text-slate-200">还没有任何项目</p>
      <p class="mt-2 text-xs leading-relaxed text-slate-500">
        从新建项目开始：填项目名 + 选一个本地视频<br />工作台会创建 projects/&lt;项目名&gt;/ 并导入首个素材
      </p>
      <button
        class="btn mx-auto mt-5 px-6 py-2.5 text-sm shadow-[0_0_28px_-6px_rgba(34,211,238,0.7)]"
        @click="createVisible = true"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path :d="icons.plus" stroke-linecap="round"/></svg>
        立即新建项目
      </button>
    </div>

    <!-- 海报网格 -->
    <div v-else class="grid grid-cols-1 gap-6 sm:grid-cols-2 xl:grid-cols-3">
      <article
        v-for="{ p, i, s } in cards"
        :key="p.name"
        class="glass glass-hover group relative cursor-pointer overflow-hidden"
        :class="{ 'ring-2 ring-cyan-400/40': app.current === p.name }"
        :style="{ '--glow': 'rgba(129,140,248,0.4)' }"
        @click="openProject(p.name)"
      >
        <!-- 海报头 -->
        <div class="relative h-28 overflow-hidden" :style="{ background: posterHue(i) }">
          <div class="absolute inset-0 opacity-25" style="background: radial-gradient(circle at 70% 30%, rgba(255,255,255,0.5), transparent 55%)"></div>
          <span class="absolute -right-2 -top-6 text-[96px] font-black leading-none text-white/10 transition-transform duration-300 group-hover:scale-110">
            {{ String(i + 1).padStart(2, '0') }}
          </span>
          <div class="absolute bottom-3 left-4 right-4">
            <h2 class="truncate text-lg font-extrabold text-white drop-shadow">{{ p.name }}
              <span v-if="p.type === '制作'" class="ml-1 align-middle rounded bg-pink-400/25 px-1.5 py-0.5 text-2xs font-black text-pink-200">制作</span>
            </h2>
            <p class="text-xs-plus text-white/60">projects/{{ p.name }}/</p>
          </div>
          <span
            v-if="app.current === p.name"
            class="absolute right-3 top-3 rounded-full bg-cyan-400/90 px-2 py-0.5 text-2xs font-bold text-cyan-950"
          >当前项目</span>
        </div>

        <!-- 统计 -->
        <div class="flex items-center justify-between gap-3 p-4">
          <div class="space-y-1.5 text-xs text-slate-400">
            <div class="flex items-center gap-1.5">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#38bdf8" stroke-width="2"><path :d="icons.film" stroke-linecap="round" stroke-linejoin="round"/></svg>
              素材视频 <b class="text-slate-200">{{ s.videos }}</b> 个
            </div>
            <div class="flex items-center gap-1.5">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#e879f9" stroke-width="2"><path :d="icons.clapper" stroke-linecap="round" stroke-linejoin="round"/></svg>
              分析版本 <b class="text-slate-200">{{ s.analyses }}</b> 个
            </div>
            <div class="mt-2 h-1.5 w-32 overflow-hidden rounded-full bg-white/10">
              <div
                class="h-full rounded-full transition-all duration-700"
                :style="{ width: s.total * 100 + '%', background: 'linear-gradient(90deg,#22d3ee,#e879f9)' }"
              />
            </div>
          </div>
          <ProgressRing :value="s.total" :size="62" color="#22d3ee" />
        </div>

        <!-- 模块环 -->
        <div class="flex justify-between border-t border-line-soft px-4 pb-5 pt-3">
          <div v-for="r in s.rings" :key="r.label" class="flex flex-col items-center gap-1">
            <ProgressRing :value="r.value" :size="40" :color="r.color" />
            <span class="text-2xs text-slate-500">{{ r.label }}</span>
          </div>
        </div>
      </article>
    </div>

    <!-- 底部提示 -->
    <p class="mt-10 text-center text-xs text-slate-500">
      提示：点击卡片即切换当前项目（保存在本地）；各功能页均作用于当前项目。
    </p>

    <!-- 新建项目弹窗 -->
    <Teleport to="body">
      <div
        v-if="createVisible"
        class="overlay p-6"
        @click.self="createVisible = false"
      >
        <div class="glass w-full max-w-md p-5" style="--glow: rgba(34,211,238,0.4)">
          <h3 class="mb-3 text-base font-bold text-slate-100">新建项目</h3>
          <label class="mb-1 block text-2xs text-slate-500">项目名（将创建 projects/&lt;项目名&gt;/）</label>
          <input v-model="createName" class="input" placeholder="如：08_我的短片" />
          <p v-if="createNameError" class="mt-1 text-xs-plus text-rose-300">{{ createNameError }}</p>

          <label class="mb-1 mt-3 block text-2xs text-slate-500">
            {{ createType === '制作' ? '上传参考视频（可选，作为拉片素材引用）' : '上传本地视频（必选，写入 拉片素材/）' }}
          </label>
          <div class="mb-3 flex gap-2">
            <button
              v-for="t in (['拆片', '制作'] as const)"
              :key="t"
              class="flex-1 rounded-lg border px-3 py-2 text-xs font-bold transition"
              :class="createType === t
                ? 'border-cyan-400/60 bg-cyan-400/15 text-cyan-200'
                : 'border-line bg-white/5 text-slate-400 hover:text-slate-200'"
              @click="createType = t"
            >
              {{ t }}项目
              <span class="block text-2xs font-normal opacity-70">{{ t === '制作' ? '纯剧本创作，无需视频' : '有视频，走拉片解构' }}</span>
            </button>
          </div>
          <input
            type="file"
            accept=".mp4,.mkv,.mov"
            class="block w-full text-xs text-slate-400 file:mr-3 file:rounded-lg file:border-0 file:px-3 file:py-1.5 file:text-xs file:font-bold file:text-slate-950 hover:file:brightness-110"
            @change="createFile = ($event.target as HTMLInputElement).files?.[0] || null"
          />
          <p v-if="createFile" class="mt-1 truncate text-xs-plus text-cyan-300">{{ createFile.name }}（{{ (createFile.size / 1048576).toFixed(1) }} MB）</p>

          <div class="mt-5 flex justify-end gap-2">
            <button class="btn btn-ghost" @click="createVisible = false">取消</button>
            <button class="btn" :disabled="creating || !createName.trim() || !!createNameError || (createType === '拆片' && !createFile)" @click="submitCreate">
              {{ creating ? '创建中…' : '创建' }}
            </button>
          </div>
        </div>
      </div>

      <!-- 整理项目弹窗 -->
      <div
        v-if="normVisible"
        class="overlay p-6"
        @click.self="normVisible = false"
      >
        <div class="glass modal-h flex w-full max-w-lg flex-col p-5" style="--glow: rgba(34,211,238,0.4)">
          <h3 class="mb-3 text-base font-bold text-slate-100">整理项目</h3>
          <label class="mb-1 block text-2xs text-slate-500">项目</label>
          <StyledSelect
            v-model="normProject"
            :options="app.projects.map((p) => p.name)"
            placeholder="— 选择项目 —"
            storage-key="wb.home.norm.project"
            :disabled="normPhase === 'applying'"
          />

          <div v-if="normPhase === 'preview' || normPhase === 'done'" class="mt-3 min-h-0 flex-1">
            <p class="mb-1 text-2xs font-bold" :class="normPhase === 'done' ? 'text-emerald-300' : 'text-amber-300'">
              {{ normPhase === 'done' ? '执行结果' : '整理计划（dry-run，未改动任何文件）' }}
            </p>
            <pre class="log-tail max-h-72 overflow-auto rounded-lg bg-black/40 p-3">{{ normPlan.join('\n') }}</pre>
          </div>
          <p v-if="normError" class="mt-2 text-xs-plus text-rose-300">{{ normError }}</p>

          <div class="mt-4 flex justify-end gap-2">
            <button class="btn btn-ghost" @click="normVisible = false">关闭</button>
            <button
              v-if="normPhase === 'idle'"
              class="btn"
              :disabled="!normProject"
              @click="runNormDry"
            >
              生成计划
            </button>
            <template v-else-if="normPhase === 'preview'">
              <button class="btn btn-ghost" @click="runNormDry">重新生成计划</button>
              <button class="btn" @click="runNormApply">确认执行</button>
            </template>
          </div>
        </div>
      </div>

      <!-- 从 Downloads 导入弹窗 -->
      <div
        v-if="importVisible"
        class="overlay p-6"
        @click.self="importVisible = false"
      >
        <div class="glass w-full max-w-md p-5" style="--glow: rgba(34,211,238,0.4)">
          <h3 class="mb-3 text-base font-bold text-slate-100">从 Downloads 导入</h3>
          <label class="mb-1 block text-2xs text-slate-500">目标项目</label>
          <StyledSelect
            v-model="importProject"
            :options="app.projects.map((p) => p.name)"
            placeholder="— 选择项目 —"
            storage-key="wb.home.import.project"
          />
          <label class="mb-1 mt-3 block text-2xs text-slate-500">源视频（~/Downloads）</label>
          <StyledSelect
            v-model="importSrcName"
            :options="app.sources.map((s) => s.name)"
            :labels="Object.fromEntries(app.sources.map((s) => [s.name, `${s.name}（${s.mb} MB）`]))"
            placeholder="— 选择视频 —"
            storage-key="wb.home.import.src"
          />
          <div class="mt-5 flex justify-end gap-2">
            <button class="btn btn-ghost" @click="importVisible = false">取消</button>
            <button class="btn" :disabled="importing || !importProject || !importSrcName" @click="submitImport">
              {{ importing ? '导入中…' : '导入' }}
            </button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>
