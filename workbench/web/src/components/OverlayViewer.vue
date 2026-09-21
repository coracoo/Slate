<script setup lang="ts">
// -*- coding: utf-8 -*-
/** 全屏弹窗查看器（全站唯一）：图片/视频/内嵌 HTML（战略图等自包含页面），支持多图左右轮播；标题栏+下载+Esc/遮罩关闭。 */
import { watch, onMounted, onBeforeUnmount, computed } from 'vue'
import { lockBodyScroll } from '../utils/scrollLock'

const props = defineProps<{
  visible: boolean
  src?: string        // 单文件 URL（/media?p=...）
  kind?: 'image' | 'video' | 'html'
  title?: string
  download?: string   // 下载文件名（可选）
  images?: string[]   // 多图轮播模式（优先于 src，纯图片）
  index?: number
}>()
const emit = defineEmits<{ close: []; updateIndex: [number] }>()

const carousel = computed(() => (props.images?.length || 0) > 0)
const count = computed(() => props.images?.length || 0)
const n = computed(() => props.index || 0)
const cur = computed(() => (carousel.value ? props.images?.[n.value] || '' : props.src || ''))
const kind = computed(() => {
  if (carousel.value) return 'image'
  return props.kind || (/\.html?($|\?)/i.test(props.src || '') ? 'html' : 'image')
})
function prev() { if (count.value > 1) emit('updateIndex', (n.value - 1 + count.value) % count.value) }
function next() { if (count.value > 1) emit('updateIndex', (n.value + 1) % count.value) }
function onKey(e: KeyboardEvent) {
  if (!props.visible) return
  if (e.key === 'Escape') emit('close')
  if (e.key === 'ArrowLeft') prev()
  if (e.key === 'ArrowRight') next()
}
onMounted(() => window.addEventListener('keydown', onKey))
let releaseScroll: (() => void) | undefined
onBeforeUnmount(() => { window.removeEventListener('keydown', onKey); releaseScroll?.() })
watch(() => props.visible, (v) => {
  releaseScroll?.(); releaseScroll = v ? lockBodyScroll() : undefined
}, { immediate: true })
</script>

<template>
  <Teleport to="body">
    <div v-if="visible" class="overlay z-[90]" @click.self="emit('close')">
      <!-- 顶栏悬浮，不挤占内容空间：图片在视口正中 -->
      <div class="absolute inset-x-0 top-0 z-10 flex items-center gap-3 px-4 py-2.5 text-xs text-slate-200">
        <span class="truncate font-bold">{{ title || (carousel ? (n + 1) + ' / ' + count : '预览') }}</span>
        <span class="flex-1"></span>
        <a v-if="download" :href="cur" :download="download"
          class="rounded-lg bg-white/10 px-3 py-1 font-bold text-slate-100 hover:bg-white/20">下载</a>
        <button class="rounded-lg bg-white/10 px-3 py-1 font-bold text-slate-100 hover:bg-white/20" @click="emit('close')">关闭 ✕</button>
      </div>
      <div class="flex h-full items-center justify-center p-10" @click.self="emit('close')">
        <button v-if="carousel && count > 1" class="absolute left-2 top-1/2 z-10 -translate-y-1/2 rounded-full bg-white/10 px-3 py-4 font-bold text-slate-100 hover:bg-white/20" aria-label="上一张" @click.stop="prev">‹</button>
        <img v-if="kind === 'image'" :src="cur" class="max-h-full max-w-full rounded-lg object-contain shadow-2xl" alt="预览" />
        <video v-else-if="kind === 'video'" :src="cur" controls autoplay class="max-h-full max-w-full rounded-lg"></video>
        <iframe v-else :src="cur" class="h-full w-full rounded-lg border border-line bg-white" title="内嵌预览"></iframe>
        <button v-if="carousel && count > 1" class="absolute right-2 top-1/2 z-10 -translate-y-1/2 rounded-full bg-white/10 px-3 py-4 font-bold text-slate-100 hover:bg-white/20" aria-label="下一张" @click.stop="next">›</button>
        <div v-if="carousel && count > 1" class="absolute bottom-2 left-1/2 -translate-x-1/2 rounded-full bg-white/10 px-3 py-1 text-xs text-slate-300">{{ n + 1 }} / {{ count }}</div>
      </div>
    </div>
  </Teleport>
</template>
