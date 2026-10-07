<script setup lang="ts">
// -*- coding: utf-8 -*-
/** 母素材卡：名称/徽标/meta + 母图（三列统一 h-56）+ 生成与版本徽标 + 子素材栅格。
 *  人物/场景/道具三列的差异经 badge / meta / states 三个插槽与事件表达：
 *  - badge：人物的角色徽标（其余类型不用）
 *  - meta：id 行（人物=basis、场景=time/light、道具=kind）
 *  - states：人物的派生状态区（仅人物有，整段由父级提供）
 *  拖拽改挂：原生 drag 事件已在此 stop/prevent，父级只收 (row, ev) 转发事件。 */
import { computed } from 'vue'
import type { AssetRegistryItem } from '../api'
import Versions from './Versions.vue'
import AssetChildCard from './AssetChildCard.vue'
import { mediaUrl } from '../api'

/** kind → 素材目录名（与后端 projects/<项目>/素材/<目录>/<id>.png 约定一致）。 */
const KIND_DIR: Record<string, string> = { character: '人物', scene: '场景', prop: '道具' }

const props = defineProps<{
  kind: 'character' | 'scene' | 'prop'
  id: string
  name?: string
  /** 注册表条目；未注册（尚未提炼入册）时为 undefined，各交互在父级有空值守卫。 */
  asset?: AssetRegistryItem
  project: string
  imageUrlOf: (row?: AssetRegistryItem) => string
  relationLabel: (row: AssetRegistryItem) => string
  children?: AssetRegistryItem[]
  genning: string
  genDisabled: boolean
  dragTarget: string
}>()

const emit = defineEmits<{
  (e: 'details', row?: AssetRegistryItem): void
  (e: 'show', row?: AssetRegistryItem): void
  (e: 'gen'): void
  (e: 'batch-states-gen'): void
  (e: 'batch-children-gen'): void
  (e: 'state-gen', id:string): void
  (e: 'create-child'): void
  (e: 'child-gen', row: AssetRegistryItem): void
  (e: 'drag-start', row: AssetRegistryItem | undefined, ev: DragEvent): void
  (e: 'drag-over', row: AssetRegistryItem | undefined, ev: DragEvent): void
  (e: 'drop', row: AssetRegistryItem | undefined, ev: DragEvent): void
  (e: 'drag-end', ev: DragEvent): void
  (e: 'restored'): void
  (e: 'deleted'): void
}>()

const assetRef = computed(() => '@' + props.kind + ':' + props.id)
const generating = computed(() => props.genning === props.kind + props.id)
const stateCount = computed(() => props.asset?.states?.length || 0)
const childCount = computed(() => props.children?.filter(c => c.usage !== 'plan').length || 0)
/** 人物有派生状态图，重生成母图必须 skip 状态图；与父级 @gen 的 doGen 参数联动。 */
const relatedNames = computed(() => (props.asset?.related_refs || []).map((r: string) => r.split(':').pop() || r))
const genTitle = computed(() => props.kind === 'character'
  ? '只重生成此母图（不碰派生状态图），旧版本自动保存'
  : '重生成此母图并保存旧版本')

function fwdDragStart(row: AssetRegistryItem, ev: DragEvent) { emit('drag-start', row, ev) }
function fwdDragOver(row: AssetRegistryItem, ev: DragEvent) { emit('drag-over', row, ev) }
function fwdDrop(row: AssetRegistryItem, ev: DragEvent) { emit('drop', row, ev) }
</script>

<template>
  <div draggable="true" class="mb-4 rounded-xl bg-white/5 p-3 transition" :class="dragTarget === assetRef ? 'ring-2 ring-cyan-300/70' : ''"
    @dragstart.stop="emit('drag-start', asset, $event)"
    @dragover.stop.prevent="emit('drag-over', asset, $event)"
    @drop.stop.prevent="emit('drop', asset, $event)"
    @dragend.stop="emit('drag-end', $event)">
    <div class="flex items-center justify-between gap-2">
      <div>
        <button class="text-left text-sm font-bold text-slate-100 underline-offset-2 transition hover:text-cyan-200 hover:underline" @click="emit('details', asset)">{{ name }}</button>
        <slot name="badge" />
        <span v-if="asset?.related_refs?.length" class="ml-2 rounded bg-blue-400/15 px-1.5 py-0.5 text-2xs text-blue-200" :title="'引用其它素材：' + relatedNames.join('、')">引用 {{ asset.related_refs.length }}</span>
        <p class="mt-1 text-2xs text-slate-500"><slot name="meta" /></p>
      </div>
      <div class="flex gap-1"><button class="btn btn-ghost btn-sm" @click="emit('create-child')">＋子素材</button></div>
    </div>
    <button v-if="imageUrlOf(asset)" class="mt-3 block w-full overflow-hidden rounded-xl border border-line bg-black/20" @click="emit('show', asset)"><img :src="imageUrlOf(asset)" class="h-56 w-full object-contain" :alt="name + '母素材'" /></button>
    <div v-else class="mt-3 flex h-56 items-center justify-center rounded-xl border border-dashed border-line text-xs-plus text-slate-500">母图尚未生成</div>
    <button v-if="asset?.visual_status && !asset.visual_status.ready" class="mt-2 block text-left text-xs text-amber-200 underline" @click="emit('details', asset)">待确认：{{ asset.visual_status.field_labels.join('、') }}</button>
    <p v-if="asset?.visual_status?.image_status === 'outdated'" class="mt-2 text-xs text-amber-200">设定已变化 · 此图待更新</p>
    <p v-else-if="asset?.visual_status?.image_status === 'unverified'" class="mt-2 text-xs text-slate-400">历史图 · 尚未核对当前设定</p>
    <p v-if="asset?.visual_status?.postprocess_warning" class="mt-2 text-xs text-amber-200">{{ asset.visual_status.postprocess_warning }}</p>
    <p v-if="asset?.asset_revision" class="mt-2 text-xs text-slate-300">设定 v{{asset.asset_revision}}<span v-if="imageUrlOf(asset)"> · 图片依据 {{asset.media_settings_revision ? 'v'+asset.media_settings_revision : '历史设定（版本未记录）'}}</span></p>
    <p v-if="asset?.settings_change?.impact?.storyboards.length" class="mt-1 text-xs text-slate-400" :title="asset.settings_change.impact.storyboards.join('、')">本次设定变更关联 {{asset.settings_change.impact.storyboards.length}} 份分镜；已有成片保留</p>
    <section v-if="kind!=='character' && asset?.states?.length" class="mt-3 space-y-2 border-t border-line pt-3"><h4 class="text-sm">派生状态</h4><article v-for="state in asset.states" :key="state.id" class="rounded bg-white/5 p-2"><img v-if="state.path" :src="mediaUrl('projects/'+project+'/'+state.path)" class="h-24 w-full object-contain" :alt="state.label" /><p class="text-sm">{{state.label}}</p><p class="text-xs text-slate-300">{{state.look_diff}}</p><button class="btn btn-ghost btn-sm mt-2" :disabled="genDisabled" @click="emit('state-gen',state.id)">生成派生图</button></article></section>
    <div class="mt-2 flex items-center gap-1.5"><button class="btn btn-ghost btn-sm flex-1" :disabled="genDisabled" :title="genTitle" @click="emit('gen')">{{ generating ? '生成中…' : '生成/重生成母图' }}</button><Versions :path="'projects/' + project + '/素材/' + KIND_DIR[kind] + '/' + id + '.png'" kind="image" @restored="emit('restored')" /></div>
    <div class="mt-2 grid grid-cols-2 gap-2">
      <button class="btn btn-ghost btn-sm" :disabled="genDisabled || !stateCount" title="生成本素材的全部派生图，使用母图作为参考；旧图片自动保存版本" @click="emit('batch-states-gen')">批量生成派生图（{{stateCount}}）</button>
      <button class="btn btn-ghost btn-sm" :disabled="genDisabled || !childCount" title="生成当前展示的全部子素材图，使用各自指定的母图或派生参考；旧图片自动保存版本" @click="emit('batch-children-gen')">批量生成子素材（{{childCount}}）</button>
    </div>
    <div v-if="children?.length" class="mt-4 border-t border-line pt-3">
      <div class="mb-2 flex items-center justify-between"><span class="text-xs-plus font-semibold text-slate-300">子素材（{{ children.length }}）</span><span class="text-2xs text-slate-500">按各自参考图生成</span></div>
      <div class="grid gap-2 sm:grid-cols-2">
        <AssetChildCard v-for="child in children" :key="child.ref" :child="child" :project="project" :image-url-of="imageUrlOf" :relation-label="relationLabel" :genning="genning" :gen-disabled="genDisabled" :drag-target="dragTarget"
          @show="emit('show', $event)" @details="emit('details', $event)" @gen="emit('child-gen', $event)"
          @drag-start="fwdDragStart" @drag-over="fwdDragOver" @drop="fwdDrop" @drag-end="emit('drag-end', $event)" @restored="emit('restored')" @deleted="emit('deleted')" />
      </div>
    </div>
    <slot name="states" />
  </div>
</template>
