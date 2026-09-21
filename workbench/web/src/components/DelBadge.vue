<script setup lang="ts">
// -*- coding: utf-8 -*-
/** 产物删除角标：悬停卡片时右上角浮现垃圾桶；确认后调 /api/file/delete 并刷新项目树。
 * 用法：父卡片加 `group relative`，<DelBadge :path="'深度/xxx.mp4'" :label="'xxx.mp4'" @deleted="..." />
 * path 为项目内相对路径；@deleted 后父组件自行清理选中态等本地状态。 */
import { ref } from 'vue'
import { deleteFile } from '../api'
import { app, toast, loadBasics } from '../stores/app'
import { icons } from './icons'

const props = defineProps<{ path: string; label?: string }>()
const emit = defineEmits<{ (e: 'deleted', path: string): void }>()
const busy = ref(false)

async function del() {
  if (!app.current || busy.value) return
  if (!confirm(`确定删除「${props.label || props.path}」？删除不可恢复`)) return
  busy.value = true
  try {
    await deleteFile(app.current, props.path)
    toast(`已删除 ${props.label || props.path}`, 'ok')
    await loadBasics()
    emit('deleted', props.path)
  } catch (e) {
    toast(e instanceof Error ? e.message : '删除失败', 'err')
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <button
    class="absolute right-1.5 top-1.5 z-10 rounded-full bg-black/70 p-1.5 text-slate-400 opacity-0 backdrop-blur transition hover:bg-rose-500/25 hover:text-rose-300 focus-visible:opacity-100 group-hover:opacity-100"
    :class="{ 'opacity-100 animate-pulse text-rose-300': busy }"
    :disabled="busy"
    :title="`删除 ${label || path}`"
    @click.stop="del"
  >
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <path :d="icons.trash" stroke-linecap="round" stroke-linejoin="round" />
    </svg>
  </button>
</template>
