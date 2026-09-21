<script setup lang="ts">
import { useBoardSelection } from '../utils/useBoardSelection'
// -*- coding: utf-8 -*-
/** 创作包总览页：战略图（全高 iframe 推演）+ 平面图网格 + 逐镜包明细——组装产物的导演工作台 */
import { ref, computed, watch } from 'vue'
import {
  fetchWhiteBoard, fetchProjectFile, creationAssemble, buildStrategy, exportPreviz,
  mediaUrl, type WhiteBoard
} from '../api'
import { app, projectFiles, toast } from '../stores/app'
import { trackJob } from '../stores/jobs'
import StyledSelect from '../components/StyledSelect.vue'
import OverlayViewer from '../components/OverlayViewer.vue'

interface Shot { id: string; dur?: number; move?: string; scene?: string; scene_ref?: string; action?: string; prompt?: string; lines?: { speaker: string; line: string }[]; staging?: Record<string, unknown> }
interface PkgShot { id: string; dur?: number; move?: string; action?: string; script?: { speaker?: string; text?: string }[]; diagram?: string | null; white_ref?: string | null; prompt?: string }
interface Pkg { storyboard: string; title?: string; strategy_map?: string | null; shots: PkgShot[]; materials?: string[] }

const boards = computed(() => (projectFiles('分镜') || []).filter((f) => f.endsWith('.json') && f.startsWith('剧本_')))
const board = ref('')
const shots = ref<Shot[]>([])
const pkg = ref<Pkg | null>(null)
const loading = ref(false)
const busy = ref('')
const tab = ref<'strategy' | 'plan' | 'previz' | 'shots'>('strategy')
const TABS = computed(() => [
  { k: 'strategy', label: '战略图推演' },
  { k: 'plan', label: '平面图 (' + planDir.value.length + ')' },
  { k: 'previz', label: '预演包 (' + previzDir.value.length + ')' },
  { k: 'shots', label: '逐镜包 (' + (pkg.value?.shots?.length ?? 0) + ')' }
] as const)
const currentShot = ref(0)
const overlay = ref<{ visible: boolean; src: string; kind: 'image' | 'html'; title: string }>({ visible: false, src: '', kind: 'image', title: '' })

const baseName = computed(() => board.value.replace(/\.json$/, ''))
const strategyUrl = computed(() =>
  baseName.value ? `/media?p=${encodeURIComponent(`projects/${app.current}/推演/战略图_${baseName.value}.html`)}` : '')
const planDir = computed(() => (projectFiles('推演') || [])
  .filter((f) => f.startsWith(`平面图_${baseName.value}/`) && /\.(png|jpe?g)$/i.test(f))
  .map((f) => `projects/${app.current}/推演/${f}`))
const previzDir = computed(() => (projectFiles('白模') || [])
  .filter((f) => f.startsWith(`预演包_${baseName.value}/`) && /\.(png|jpe?g)$/i.test(f))
  .map((f) => `projects/${app.current}/白模/${f}`))

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
    if (j.success) { toast(`${label}完成`, 'ok'); done?.() } else throw new Error(j.err || `${label}失败`)
  } catch (e) {
    toast(e instanceof Error ? e.message : `${label}失败`, 'err')
  } finally { busy.value = '' }
}
const doAssemble = () => run('组装创作包', () => creationAssemble(app.current!, board.value), load)
const doStrategy = () => run('生成战略图', () => buildStrategy(app.current!, board.value), load)
const doPreviz = () => run('导出预演包', () => exportPreviz({ project: app.current!, json: board.value, frame: 'mid' }), load)

function sceneOf(s: Shot): string {
  const v = String(s.scene_ref || '')
  return v.startsWith('@scene:') ? v.slice('@scene:'.length) : (s.scene || '')
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
            {{ busy === '组装创作包' ? '组装中…' : '组装创作包' }}
          </button>
          <button class="btn btn-ghost btn-sm flex-1 justify-center" :disabled="!!busy || !board" @click="doStrategy">
            {{ busy === '生成战略图' ? '生成中…' : '刷新战略图' }}
          </button>
          <button class="btn btn-ghost btn-sm flex-1 justify-center" :disabled="!!busy || !board"
            title="逐镜导出干净预演帧+表演提示词+manifest（图生视频参考输入）" @click="doPreviz">
            {{ busy === '导出预演包' ? '导出中…' : '导出预演包' }}
          </button>
        </div>
      </div>
      <div class="glass min-h-0 flex-1 overflow-y-auto p-2">
        <p v-if="!shots.length" class="py-10 text-center text-sm text-slate-500">选择分镜</p>
        <button v-for="(s, i) in shots" :key="s.id"
          class="mb-1 block w-full rounded-lg p-2 text-left transition"
          :class="currentShot === i ? 'bg-sky-400/15 ring-1 ring-sky-400/40' : 'hover:bg-white/5'"
          @click="gotoShot(i)">
          <div class="flex items-center gap-1.5">
            <span class="rounded bg-sky-400/15 px-1.5 text-xs-plus font-black text-sky-300">{{ s.id }}</span>
            <span class="text-2xs text-slate-500">{{ s.dur }}s · {{ (s.lines || []).length }}台词</span>
          </div>
          <div class="mt-0.5 truncate text-2xs text-slate-500">{{ sceneOf(s) || '—' }}</div>
          <div class="mt-0.5 line-clamp-1 text-2xs text-slate-400">{{ s.action || s.prompt }}</div>
        </button>
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
        <span v-if="pkg?.strategy_map === null && tab === 'strategy'" class="text-2xs text-amber-300">尚无战略图——点左上「组装创作包」生成</span>
      </div>

      <!-- 战略图：全高 iframe -->
      <div v-if="tab === 'strategy'" class="glass min-h-0 flex-1 overflow-hidden p-1">
        <iframe v-if="strategyUrl" id="strategyFrame" :src="strategyUrl" class="h-full w-full rounded-lg border-0 bg-white"
          title="战略图"></iframe>
        <div v-else class="grid h-full place-items-center text-xs text-slate-500">选择分镜后展示战略图</div>
      </div>

      <!-- 平面图网格 -->
      <div v-else-if="tab === 'plan'" class="glass min-h-0 flex-1 overflow-y-auto p-3">
        <div v-if="!planDir.length" class="grid h-full place-items-center text-xs text-slate-500">
          暂无平面图——组装创作包时自动生成（shot_diagram）
        </div>
        <div v-else class="grid grid-cols-2 gap-2 xl:grid-cols-3">
          <button v-for="p in planDir" :key="p" class="group overflow-hidden rounded-lg border border-line"
            @click="overlay = { visible: true, kind: 'image', src: mediaUrl(p), title: p.split('/').pop() || '' }">
            <img :src="mediaUrl(p)" class="aspect-video w-full object-cover" loading="lazy" alt="平面图" />
            <div class="truncate px-1.5 py-1 text-2xs text-slate-400">{{ p.split('/').pop() }}</div>
          </button>
        </div>
      </div>

      <!-- 预演包：干净帧给图生视频 -->
      <div v-else-if="tab === 'previz'" class="glass min-h-0 flex-1 overflow-y-auto p-3">
        <div v-if="!previzDir.length" class="grid h-full place-items-center gap-2 text-xs text-slate-500">
          <span>暂无预演包——点左上「导出预演包」生成</span>
          <span class="text-2xs text-slate-500">逐镜干净帧（无HUD）+ 表演提示词 txt + manifest，喂 Seedance/Wan 的参考输入</span>
        </div>
        <div v-else class="grid grid-cols-2 gap-2 xl:grid-cols-3">
          <button v-for="p in previzDir" :key="p" class="overflow-hidden rounded-lg border border-line transition hover:border-sky-400/50"
            @click="overlay = { visible: true, kind: 'image', src: mediaUrl(p), title: p.split('/').pop() || '' }">
            <img :src="mediaUrl(p)" class="aspect-video w-full object-cover" loading="lazy" alt="预演帧" />
            <div class="truncate px-1.5 py-1 text-2xs text-slate-400">{{ p.split('/').pop() }}</div>
          </button>
        </div>
      </div>

      <!-- 逐镜包 -->
      <div v-else class="glass min-h-0 flex-1 overflow-y-auto p-3">
        <div v-if="!pkg" class="grid h-full place-items-center text-xs text-slate-500">尚未组装——点左上「组装创作包」</div>
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
        </div>
      </div>
    </section>

  <OverlayViewer :visible="overlay.visible" :src="overlay.src" :kind="overlay.kind" :title="overlay.title" @close="overlay.visible = false" />
  </div>
</template>
