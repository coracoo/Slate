<script setup lang="ts">
// -*- coding: utf-8 -*-
/** 子素材缩略卡：母素材卡内嵌的二级卡片，生成时继承母图参考；
 *  可拖拽改挂（母/子卡片是嵌套 draggable，原生事件已在此 stop，父级只收转发事件）。 */
import type { AssetRegistryItem } from '../api'
import Versions from './Versions.vue'

/** kind → 素材目录名（与后端 projects/<项目>/素材/<目录>/<id>.png 约定一致）。 */
const KIND_DIR: Record<string, string> = { character: '人物', scene: '场景', prop: '道具' }

defineProps<{
  child: AssetRegistryItem
  project: string
  imageUrlOf: (row?: AssetRegistryItem) => string
  relationLabel: (row: AssetRegistryItem) => string
  genning: string
  genDisabled: boolean
  dragTarget: string
}>()

const emit = defineEmits<{
  (e: 'show', row: AssetRegistryItem): void
  (e: 'details', row: AssetRegistryItem): void
  (e: 'gen', row: AssetRegistryItem): void
  (e: 'drag-start', row: AssetRegistryItem, ev: DragEvent): void
  (e: 'drag-over', row: AssetRegistryItem, ev: DragEvent): void
  (e: 'drop', row: AssetRegistryItem, ev: DragEvent): void
  (e: 'drag-end', ev: DragEvent): void
  (e: 'restored'): void
}>()
</script>

<template>
  <div draggable="true" class="rounded-lg bg-black/15 p-2 transition" :class="dragTarget === child.ref ? 'ring-2 ring-cyan-300/70' : ''"
    @dragstart.stop="emit('drag-start', child, $event)"
    @dragover.stop.prevent="emit('drag-over', child, $event)"
    @drop.stop.prevent="emit('drop', child, $event)"
    @dragend.stop="emit('drag-end', $event)">
    <button v-if="imageUrlOf(child)" class="block h-24 w-full overflow-hidden rounded-lg border border-line bg-black/20" @click="emit('show', child)"><img :src="imageUrlOf(child)" class="h-full w-full object-contain" :alt="child.name" /></button>
    <div v-else class="flex h-24 items-center justify-center rounded-lg border border-dashed border-line text-2xs text-slate-500">子图未生成</div>
    <div class="mt-1 flex items-center gap-1"><button class="min-w-0 flex-1 truncate text-left text-xs text-slate-200 underline-offset-2 transition hover:text-cyan-200 hover:underline" @click="emit('details', child)">{{ child.name }}</button></div>
    <div class="text-2xs text-slate-500">{{ relationLabel(child) }}</div>
    <div class="mt-1 flex gap-1"><button class="btn btn-ghost btn-sm flex-1" :disabled="genDisabled" @click="emit('gen', child)">{{ genning === child.kind + child.id ? '生成中…' : '生成子图' }}</button><Versions :path="'projects/' + project + '/素材/' + (KIND_DIR[child.kind] || '人物') + '/' + child.id + '.png'" kind="image" @restored="emit('restored')" /></div>
  </div>
</template>
