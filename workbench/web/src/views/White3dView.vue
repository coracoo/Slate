<script setup lang="ts">
import { useBoardSelection } from '../utils/useBoardSelection'
// -*- coding: utf-8 -*-
/** ⑥ 3D 白模：选分镜 JSON → 生成 Blender 构建脚本 → 发 Blender MCP 构建存盘 .blend；产物卡片（打开/CLI 渲染/代码查看）。 */
import { ref, computed, watch } from 'vue'
import { runGeneric, openBlend, fetchText, mediaUrl, ApiError, deleteFile } from '../api'
import { app, projectFiles, materialVideos, toast, loadBasics } from '../stores/app'
import { trackJob } from '../stores/jobs'
import { icons } from '../components/icons'
import StyledSelect from '../components/StyledSelect.vue'
import DelBadge from '../components/DelBadge.vue'
import EmptyState from '../components/EmptyState.vue'

const GLOW = 'rgba(34,211,238,0.35)'

const storyboard = ref('')
const phase = ref<'idle' | 'genscript' | 'build'>('idle')
const envLoading = ref(false)
const genScript = ref('')

const boards = computed(() => projectFiles('分镜', /\.json$/i))
watch([boards, storyboard], () => {
  // 分镜选择由 useBoardSelection 统一恢复。 // // 默认选最后一个分镜（含项目切换回填）
}, { immediate: true })

/** Blender MCP 未连时的统一提示。 */
function mcpToast(msg: string) {
  if (/MCP|9876/i.test(msg)) {
    toast('Blender MCP(127.0.0.1:9876) 未连接，请先启动 Blender', 'err', 4000)
  } else {
    toast(msg, 'err', 4000)
  }
}

/** 删除当前对比选中的 3D 渲染视频。 */
async function delCmp() {
  if (!app.current || !cmpVideo.value) return
  if (!confirm(`确定删除「${cmpVideo.value.split('/').pop()}」？删除不可恢复`)) return
  try {
    await deleteFile(app.current, cmpVideo.value.replace(`projects/${app.current}/`, ''))
    toast('已删除', 'ok')
    await loadBasics()
  } catch (e) {
    toast(e instanceof Error ? e.message : '删除失败', 'err')
  }
}

async function generate() {
  if (!app.current || !storyboard.value) {
    toast('请先选择分镜 JSON', 'err')
    return
  }
  phase.value = 'genscript'
  genScript.value = ''
  try {
    const r = await runGeneric({ step: 'blender_previs', args: [`projects/${app.current}/分镜/${storyboard.value}`] })
    // 契约形态 A：同步生成成功，返回 {ok, gen_script} → 接着发 blender_build
    if (r.ok && r.gen_script) {
      genScript.value = r.gen_script
      phase.value = 'build'
      const b = await runGeneric({ step: 'blender_build', args: [r.gen_script] })
      if (!b.id) throw new Error(b.err || '构建任务未启动')
      const j = await trackJob(b.id, `Blender 构建 ${storyboard.value}`)
      if (j.success) { toast('Blender 构建完成（.blend 已存盘）', 'ok'); await loadBasics() }
      else mcpToast(j.err || 'Blender 构建失败')
      return
    }
    // 契约形态 B（当前 server.py）：一步合并任务 {ok, id}（生成脚本 + 发 MCP）
    if (r.ok && r.id) {
      phase.value = 'build'
      const j = await trackJob(r.id, `3D 白模 ${storyboard.value}`)
      const m = (j.out || '').match(/已生成脚本[:：]\s*(.+)/)
      if (m) genScript.value = m[1].trim()
      if (j.success) { toast('生成脚本 + Blender 构建完成', 'ok'); await loadBasics() }
      else mcpToast(j.err || '任务失败')
      return
    }
    throw new Error(r.err || '生成脚本失败')
  } catch (e) {
    const msg = e instanceof ApiError || e instanceof Error ? e.message : '未知错误'
    mcpToast(msg)
  } finally {
    phase.value = 'idle'
  }
}

async function genEnv() {
  if (!app.current || !storyboard.value) {
    toast('请先选择分镜 JSON', 'err')
    return
  }
  envLoading.value = true
  try {
    const r = await runGeneric({ step: 'scene_env', args: [`projects/${app.current}/分镜/${storyboard.value}`] })
    if (!r.id) throw new Error(r.err || '任务未启动')
    const j = await trackJob(r.id, `LLM 场景陈设 ${storyboard.value}`)
    if (j.success) { toast('场景陈设已生成分镜 env（重新生成构建脚本即可带上）', 'ok', 5000) }
    else throw new Error(j.err || '场景生成失败')
  } catch (e) {
    toast(e instanceof Error ? e.message : '场景生成失败', 'err')
  } finally {
    envLoading.value = false
  }
}

/* ---------- 产物 ---------- */
interface Prod {
  sub: string
  file: string
  path: string
}

function products(re: RegExp): Prod[] {
  const out: Prod[] = []
  for (const sub of ['白模3D', '根目录']) {
    for (const f of projectFiles(sub)) {
      if (re.test(f)) {
        out.push({
          sub,
          file: f,
          path: `projects/${app.current}/${sub === '根目录' ? '' : sub + '/'}${f}`
        })
      }
    }
  }
  return out
}

const blends = computed(() => products(/\.blend$/i))
const videos = computed(() => products(/\.mp4$/i))
const scripts = computed(() => products(/^(gen|build)_[^/]*\.py$/i))
const hasProducts = computed(() => blends.value.length + videos.value.length + scripts.value.length > 0)

/* ---------- 原片 vs 3D 白模 对比 ---------- */
const srcVideo = ref('')
const srcOptions = computed(() => materialVideos().map((v) => v.rel))
watch([srcOptions, srcVideo], () => {
  if ((!srcVideo.value || !srcOptions.value.includes(srcVideo.value)) && srcOptions.value.length) srcVideo.value = srcOptions.value.at(-1) || ''
}, { immediate: true })

const cmpVideo = ref('')
const cmpOptions = computed(() => videos.value.map((v) => v.path))
const cmpLabels = computed(() => Object.fromEntries(videos.value.map((v) => [v.path, `${v.file}（${v.sub}）`])))
watch([cmpOptions, cmpVideo], () => {
  // 默认选最新一个（同名字典序最大；即最近一次生成的产物）
  if ((!cmpVideo.value || !cmpOptions.value.includes(cmpVideo.value)) && cmpOptions.value.length) {
    cmpVideo.value = cmpOptions.value[cmpOptions.value.length - 1]
  }
}, { immediate: true })

/* 双播放器联动：播放/暂停/拖动互相同步（带防递归锁） */
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

/* ---------- 产物操作 ---------- */
const opening = ref<Record<string, boolean>>({})
const rendering = ref<Record<string, boolean>>({})

async function openLocal(p: Prod) {
  opening.value[p.path] = true
  try {
    const r = await openBlend(p.path)
    toast(`已在本机打开 ${r.opened || p.file}`, 'ok')
  } catch (e) {
    toast(e instanceof Error ? e.message : '打开失败', 'err')
  } finally {
    opening.value[p.path] = false
  }
}

async function renderCli(p: Prod) {
  rendering.value[p.path] = true
  try {
    const r = await runGeneric({ step: 'blender_render', args: [p.path] })
    if (!r.id) throw new Error(r.err || '渲染任务未启动')
    toast(`CLI 渲染任务 #${r.id} 已启动`, 'ok')
    const j = await trackJob(r.id, `CLI 渲染 ${p.file}`)
    if (j.success) { toast('渲染完成', 'ok'); await loadBasics() }
    else toast(j.err || '渲染失败', 'err')
  } catch (e) {
    toast(e instanceof Error ? e.message : '启动失败', 'err')
  } finally {
    rendering.value[p.path] = false
  }
}

const codeView = ref<{ path: string; text: string } | null>(null)
const codeLoading = ref(false)

async function viewCode(p: Prod) {
  codeLoading.value = true
  codeView.value = { path: p.path, text: '' }
  try {
    codeView.value = { path: p.path, text: await fetchText(p.path) }
  } catch {
    toast('代码读取失败', 'err')
    codeView.value = null
  } finally {
    codeLoading.value = false
  }
}

async function resendBuild(p: Prod) {
  try {
    const r = await runGeneric({ step: 'blender_build', args: [p.path] })
    if (!r.id) throw new Error(r.err || '构建任务未启动')
    toast(`已发送 ${p.file} 到 Blender MCP`, 'ok')
    const j = await trackJob(r.id, `Blender 构建 ${p.file}`)
    if (j.success) toast('Blender 构建完成', 'ok')
    else mcpToast(j.err || 'Blender 构建失败')
  } catch (e) {
    mcpToast(e instanceof Error ? e.message : '发送失败')
  }
}

watch(() => app.current, () => {
  storyboard.value = ''
  genScript.value = ''
  srcVideo.value = ''
})
useBoardSelection(storyboard, boards, 'white3d')
</script>

<template>
  <div class="page">
    <header class="mb-6">
      <h1 class="grad-text text-2xl font-black">② 辅助·Blender</h1>
      <p class="mt-1 text-xs text-slate-500">
        分镜 JSON → 生成 Blender 构建脚本 → 经 Blender MCP(127.0.0.1:9876) 构建并自动存盘 .blend
      </p>
      <p class="mt-1 rounded-lg bg-cyan-400/10 px-3 py-1.5 text-xs-plus text-cyan-300">
        仅支持 dialogue 契约（显式 pos/look 站位）的分镜；previs 自动机位模式不支持 3D。
      </p>
    </header>

    <!-- 参数条 -->
    <div class="glass mb-5 flex flex-wrap items-end gap-3 p-4">
      <label class="min-w-64 text-xs text-slate-400">
        分镜 JSON（分镜/）
        <StyledSelect v-model="storyboard" class="mt-1" :options="boards" :storage-key="`wb.${app.current}.white3d.board`" placeholder="— 选择分镜 —" />
      </label>
      <button class="btn" :disabled="phase !== 'idle' || !storyboard" @click="generate">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path :d="icons.wand" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
        {{ phase === 'genscript' ? '生成脚本中…' : phase === 'build' ? '发送 Blender 构建中…' : '✨ 生成 3D 场景' }}
      </button>
      <button class="btn btn-ghost" :disabled="envLoading || !storyboard"
        title="LLM 读场景描述生成陈设灰模（桌案/帷幔/大帐/拒马…），写回分镜 env，重新生成构建脚本即可带上"
        @click="genEnv">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M12 3l1.8 4.6L18 9l-4.2 1.4L12 15l-1.8-4.6L6 9l4.2-1.4L12 3zM19 15l.9 2.3L22 18l-2.1.7L19 21l-.9-2.3L16 18l2.1-.7L19 15z" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
        {{ envLoading ? 'AI 布置场景中…' : 'AI 生成场景陈设' }}
      </button>
      <div v-if="phase !== 'idle'" class="flex items-center gap-2 text-xs text-cyan-300">
        <svg class="animate-spin" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
          <path d="M12 3a9 9 0 1 0 9 9" stroke-linecap="round" />
        </svg>
        {{ phase === 'genscript' ? '① 生成脚本中 → ② 发送 Blender 构建中' : '② 发送 Blender 构建中（MCP 执行 + 存盘 .blend）' }}
      </div>
    </div>

    <!-- 生成脚本回显 -->
    <div v-if="genScript" class="glass mb-5 flex items-center gap-2 px-4 py-2.5 text-xs">
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#22d3ee" stroke-width="2">
        <path :d="icons.terminal" stroke-linecap="round" stroke-linejoin="round" />
      </svg>
      <span class="text-slate-500">生成脚本：</span>
      <code class="truncate text-cyan-300" :title="genScript">{{ genScript }}</code>
    </div>

    <!-- 原片 vs 3D 白模 对比（有渲染视频时默认展开） -->
    <section v-if="app.current && videos.length" class="glass mb-5 p-4">
      <h3 class="mb-3 flex items-center gap-2 text-xs font-bold text-slate-400">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="#22d3ee" stroke-width="2">
          <path :d="icons.film" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
        原片 vs 3D 白模（播放/暂停/拖动双向联动）
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
            <span class="rounded-full bg-cyan-400/10 px-2 py-0.5 text-2xs font-bold text-cyan-300">3D 白模</span>
            <StyledSelect
              v-if="cmpOptions.length > 1"
              v-model="cmpVideo"
              class="min-w-0 flex-1"
              :options="cmpOptions"
              :labels="cmpLabels"
            />
            <span v-else class="min-w-0 flex-1 truncate text-xs-plus text-slate-400" :title="cmpVideo">{{ cmpVideo.split('/').pop() }}</span>
            <button
              class="btn btn-danger btn-sm shrink-0"
              :title="`删除当前 3D 渲染视频 ${cmpVideo.split('/').pop()}`"
              @click="delCmp"
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path :d="icons.trash" stroke-linecap="round" stroke-linejoin="round"/></svg>
            </button>
          </div>
        </div>
      </div>
    </section>

    <EmptyState v-if="!app.current" title="请先在左侧选择项目" />

    <!-- 产物区 -->
    <section v-else>
      <h3 class="mb-2 text-xs font-bold text-slate-500">3D 产物（白模3D/ 与项目根目录）</h3>
      <div v-if="!hasProducts" class="glass p-12 text-center">
        <svg class="mx-auto mb-3 opacity-40" width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="#22d3ee" stroke-width="1.5">
          <path :d="icons.box3d" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
        <p class="text-sm text-slate-300">该项目还没有 3D 产物</p>
        <p class="mt-2 text-xs text-slate-500">
          在上方选择 dialogue 契约分镜 JSON 后点「✨ 生成 3D 场景」<br />
          需先启动 Blender 并开启 MCP（127.0.0.1:9876）
        </p>
      </div>

      <div v-else class="space-y-6">
        <!-- .blend -->
        <div v-if="blends.length">
          <h4 class="mb-2 text-2xs font-bold text-cyan-400">BLEND 场景（{{ blends.length }}）</h4>
          <div class="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <article
              v-for="p in blends"
              :key="p.path"
              class="glass glass-hover group relative p-3"
              :style="{ '--glow': GLOW }"
            >
              <DelBadge :path="p.path.replace(`projects/${app.current}/`, '')" :label="p.file" />
              <div class="mb-2 flex items-center gap-2">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#22d3ee" stroke-width="2">
                  <path :d="icons.box3d" stroke-linecap="round" stroke-linejoin="round" />
                </svg>
                <span class="min-w-0 flex-1 truncate text-sm font-bold text-slate-100" :title="p.path">{{ p.file }}</span>
                <span class="shrink-0 rounded-full bg-cyan-400/10 px-2 py-0.5 text-2xs text-cyan-300">{{ p.sub }}</span>
              </div>
              <div class="flex gap-2">
                <button class="btn btn-sm flex-1 justify-center" :disabled="opening[p.path]" @click="openLocal(p)">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path :d="icons.play" stroke-linejoin="round" />
                  </svg>
                  {{ opening[p.path] ? '打开中…' : '本机打开' }}
                </button>
                <button class="btn btn-ghost btn-sm flex-1 justify-center" :disabled="rendering[p.path]" @click="renderCli(p)">
                  <svg v-if="rendering[p.path]" class="animate-spin" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                    <path d="M12 3a9 9 0 1 0 9 9" stroke-linecap="round" />
                  </svg>
                  <svg v-else width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path :d="icons.film" stroke-linecap="round" stroke-linejoin="round" />
                  </svg>
                  {{ rendering[p.path] ? '渲染中…' : 'CLI 渲染' }}
                </button>
              </div>
            </article>
          </div>
        </div>

        <!-- gen_*.py -->
        <div v-if="scripts.length">
          <h4 class="mb-2 text-2xs font-bold text-cyan-400">构建脚本（{{ scripts.length }}）</h4>
          <div class="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <article
              v-for="p in scripts"
              :key="p.path"
              class="glass glass-hover group relative cursor-pointer p-3"
              :style="{ '--glow': GLOW }"
              @click="viewCode(p)"
            >
              <DelBadge :path="p.path.replace(`projects/${app.current}/`, '')" :label="p.file"
                @deleted="(path) => { if (codeView?.path === 'projects/' + app.current + '/' + path) codeView = null }" />
              <div class="flex items-center gap-2">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#fbbf24" stroke-width="2">
                  <path :d="icons.terminal" stroke-linecap="round" stroke-linejoin="round" />
                </svg>
                <span class="min-w-0 flex-1 truncate text-xs font-bold text-slate-200" :title="p.path">{{ p.file }}</span>
              </div>
              <div class="mt-2 flex items-center justify-between gap-2">
                <span class="text-2xs text-slate-500">点击查看代码</span>
                <button
                  class="btn btn-ghost btn-sm"
                  @click.stop="resendBuild(p)"
                >发送到 Blender</button>
              </div>
            </article>
          </div>
        </div>
      </div>
    </section>

    <!-- 代码弹窗 -->
    <Teleport to="body">
      <div
        v-if="codeView"
        class="overlay p-6"
        @click.self="codeView = null"
      >
        <div class="glass modal-h flex w-full max-w-2xl flex-col p-4">
          <div class="mb-2 flex items-center gap-2">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#fbbf24" stroke-width="2">
              <path :d="icons.terminal" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
            <code class="min-w-0 flex-1 truncate text-xs text-cyan-300" :title="codeView.path">{{ codeView.path }}</code>
            <button class="text-slate-500 hover:text-slate-200" @click="codeView = null">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path :d="icons.close" stroke-linecap="round" />
              </svg>
            </button>
          </div>
          <pre class="log-tail flex-1 overflow-auto rounded-lg bg-black/40 p-3">{{ codeLoading ? '读取中…' : codeView.text }}</pre>
        </div>
      </div>
    </Teleport>
  </div>
</template>

