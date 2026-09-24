<script setup lang="ts">
// -*- coding: utf-8 -*-
/** 产出版本切换器：徽标显示历史数，展开看各版本（图片带缩略图），可预览/回滚（最新文件原位不动） */
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { getJSON, postJSON, mediaUrl, deleteVersion } from '../api'
import { toast } from '../stores/app'
import OverlayViewer from './OverlayViewer.vue'
import StoryboardGrid from './StoryboardGrid.vue'

interface Ver { ts: string; rel: string; current: boolean }
const props = defineProps<{ path: string; kind?: 'image' | 'file' }>()
const emit = defineEmits<{ (e: 'restored'): void }>()

const open = ref(false)
const vers = ref<Ver[]>([])
const loading = ref(false)
const root = ref<HTMLElement | null>(null)
const panel = ref<HTMLElement | null>(null)
/** 预览真源只存项目内相对路径，渲染时统一经 mediaUrl 包装（此前混存完整 URL 导致二次编码 404）。 */
const preview = ref<{ rel: string; kind: 'image' | 'video' | 'html' } | null>(null)
/** 文本/JSON 文档大窗预览：storyboard JSON 用只读分镜表格渲染，其余 JSON 美化、纯文本原样。 */
const docPreview = ref<{ title: string; shots?: any[]; text?: string } | null>(null)
const docLoading = ref(false)
/** 下拉面板 Teleport 到 body 后的 fixed 定位（跟随触发按钮右对齐）。 */
const panelStyle = ref<Record<string, string>>({})

function updatePanelPos() {
  const r = root.value?.getBoundingClientRect()
  if (!r) return
  panelStyle.value = {
    position: 'fixed',
    top: `${r.bottom + 4}px`,
    left: `${Math.max(8, r.right - 256)}px`,
    zIndex: '70'
  }
}

const count = computed(() => Math.max(0, vers.value.length - 1))

async function load() {
  if (!props.path) { vers.value = []; return }
  loading.value = true
  try {
    vers.value = (await getJSON<{ versions: Ver[] }>(`/api/versions?p=${encodeURIComponent(props.path)}`)).versions || []
  } catch { vers.value = [] }
  finally { loading.value = false }
}
watch(() => props.path, load, { immediate: true })
watch(open, (v) => {
  if (v) {
    updatePanelPos()
    window.addEventListener('scroll', updatePanelPos, true)
    window.addEventListener('resize', updatePanelPos)
  } else {
    window.removeEventListener('scroll', updatePanelPos, true)
    window.removeEventListener('resize', updatePanelPos)
  }
})

function closeOnOutside(event: PointerEvent) {
  const target = event.target as Node | null
  if (open.value && target && !root.value?.contains(target) && !panel.value?.contains(target)) open.value = false
}
onMounted(() => document.addEventListener('pointerdown', closeOnOutside))
onBeforeUnmount(() => document.removeEventListener('pointerdown', closeOnOutside))

async function removeVer(v: Ver) {
  if (!confirm('删除这条历史版本？（不可恢复；当前文件不受影响）')) return
  try {
    await deleteVersion(props.path, v.ts)
    await load()
    toast('已删除该版本', 'ok')
  } catch (e) { toast(e instanceof Error ? e.message : '删除失败', 'err') }
}
async function restore(v: Ver) {
  if (!confirm(`回滚到 ${v.ts}？（当前版本会先自动快照，不会丢失）`)) return
  try {
    await postJSON('/api/versions/restore', { p: props.path, ts: v.ts })
    toast('已回滚', 'ok')
    await load()
    open.value = false
    emit('restored')
  } catch (e) { toast(e instanceof Error ? e.message : '回滚失败', 'err') }
}
const extKind = (rel: string): 'image' | 'video' | 'html' | null => {
  if (/\.(png|jpe?g|webp|gif|bmp)$/i.test(rel)) return 'image'
  if (/\.(mp4|webm|mov)$/i.test(rel)) return 'video'
  if (/\.html?$/i.test(rel)) return 'html'
  return null
}
function showPreview(v: Ver) {
  const k = extKind(v.rel)
  if (k) { preview.value = { rel: v.rel, kind: k }; return }
  // 文本/JSON 不再新标签裸开：大窗弹窗展示，storyboard JSON 复用只读分镜表格
  if (/\.(json|md|txt|log|csv)$/i.test(v.rel)) { openDocPreview(v); return }
  window.open(mediaUrl(v.rel), '_blank')  // xlsx 等二进制无法渲染，仍新标签下载/打开
}
async function openDocPreview(v: Ver) {
  docLoading.value = true
  try {
    const r = await fetch(mediaUrl(v.rel))
    if (!r.ok) throw new Error(`HTTP ${r.status}`)
    const raw = await r.text()
    let shots: any[] | undefined
    let text = raw
    try {
      const data = JSON.parse(raw)
      if (data && Array.isArray(data.shots)) shots = data.shots
      else text = JSON.stringify(data, null, 2)
    } catch { /* 非 JSON：纯文本原样展示 */ }
    docPreview.value = { title: v.rel.split('/').pop() || v.rel, shots, text: shots ? undefined : text }
  } catch (e) {
    toast(e instanceof Error ? e.message : '预览加载失败', 'err')
  } finally {
    docLoading.value = false
  }
}
function closePreview() { preview.value = null }
const fmt = (ts: string) => { const m = ts.match(/^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})(.*)$/); return m ? m[2] + '-' + m[3] + ' ' + m[4] + ':' + m[5] + ':' + m[6] + (m[7] || '') : ts }
</script>

<template>
  <div ref="root" class="relative inline-block">
    <button class="rounded-full bg-white/10 px-2 py-0.5 text-2xs font-bold text-slate-300 transition hover:bg-white/20"
      :title="`历史版本 ${count} 份`" @click.stop="open = !open; load()">
      ◂ 版本 {{ count }}
    </button>
    <Teleport to="body">
      <div v-if="open" ref="panel" :style="panelStyle" class="max-h-72 w-64 overflow-y-auto rounded-lg border border-line bg-slate-950/95 p-2 shadow-xl"
        @click.stop>
        <div v-if="loading" class="py-2 text-center text-2xs text-slate-500">加载…</div>
        <div v-for="v in vers" :key="v.rel" class="mb-1 flex items-center gap-2 rounded p-1 hover:bg-white/5">
          <button v-if="kind === 'image'" class="shrink-0 overflow-hidden rounded" title="点击查看大图" @click.stop="showPreview(v)"><img :src="mediaUrl(v.rel)" class="h-10 w-16 rounded object-cover" alt="版本" loading="lazy" /></button>
          <div class="min-w-0 flex-1">
            <div class="text-xs-plus" :class="v.current ? 'font-bold text-emerald-300' : 'text-slate-300'">
              {{ v.current ? '最新' : fmt(v.ts) }}
            </div>
          </div>
          <button class="text-2xs text-sky-300 hover:underline" @click.stop="showPreview(v)">打开</button>
          <button v-if="!v.current" class="text-2xs text-amber-300 hover:underline" @click.stop="restore(v)">恢复</button>
          <button v-if="!v.current" class="text-2xs text-rose-300 hover:underline" @click.stop="removeVer(v)">删除</button>
        </div>
        <div v-if="!vers.length" class="py-2 text-center text-2xs text-slate-500">暂无历史版本</div>
      </div>
    </Teleport>
    <OverlayViewer :visible="!!preview" :src="preview ? mediaUrl(preview.rel) : ''" :kind="preview?.kind" :title="preview ? preview.rel.split('/').pop() : ''" @close="closePreview" />
    <Teleport to="body">
      <div v-if="docPreview || docLoading" class="overlay z-[75] p-6" @click.self="docPreview = null">
        <div class="modal-h flex w-full max-w-6xl flex-col overflow-hidden rounded-xl border border-line bg-slate-950/95 shadow-2xl" @click.stop>
          <div class="flex items-center gap-3 border-b border-line px-4 py-2.5">
            <span class="truncate text-sm font-bold text-slate-200">{{ docPreview?.title || '加载中…' }}</span>
            <span v-if="docPreview?.shots" class="shrink-0 text-xs-plus text-slate-500">{{ docPreview.shots.length }} 镜 · 只读预览</span>
            <button class="btn btn-ghost btn-sm ml-auto shrink-0" @click="docPreview = null">关闭</button>
          </div>
          <div class="min-h-0 flex-1 overflow-auto p-4">
            <div v-if="docLoading" class="py-10 text-center text-xs text-slate-500">加载版本内容…</div>
            <StoryboardGrid v-else-if="docPreview?.shots" :shots="docPreview.shots" />
            <pre v-else class="whitespace-pre-wrap break-all text-xs-plus leading-relaxed text-slate-300">{{ docPreview?.text }}</pre>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>
