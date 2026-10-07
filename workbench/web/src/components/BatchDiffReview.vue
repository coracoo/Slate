<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, useId, watch } from 'vue'
import TextDiff from './TextDiff.vue'
import type { ReviewItem } from '../utils/reviewDiff'
import { reviewSelectionIds } from '../utils/reviewDiff'

const props = withDefaults(defineProps<{open: boolean; title: string; items: ReviewItem[]; busy?: boolean; error?: string; submitLabel?: string; initialSelection?:string[]}>(), {submitLabel:'确认并提交'})
const emit = defineEmits<{(event:'update:open', value:boolean):void; (event:'confirm', ids:string[]):void}>()
const dialog = ref<HTMLDialogElement>(), selected = ref<string[]>([])
const titleId=useId()
const available = computed(() => props.items.filter(item => !item.disabledReason))
const selectedIds = computed(() => reviewSelectionIds(available.value).filter(id => selected.value.includes(id)))
function selectItem(item:ReviewItem, checked:boolean) {
  const ids=reviewSelectionIds([item])
  selected.value=checked?[...new Set([...selected.value,...ids])]:selected.value.filter(id=>!ids.includes(id))
}
function partiallySelected(item:ReviewItem) {
  const ids=reviewSelectionIds([item]), count=ids.filter(id=>selectedIds.value.includes(id)).length
  return count>0 && count<ids.length
}
watch(() => props.open, async open => {
  await nextTick()
  if (open) {
    selected.value = props.initialSelection?.filter(id=>reviewSelectionIds(available.value).includes(id)) ?? reviewSelectionIds(available.value.filter(item => item.defaultSelected !== false))
    if (!dialog.value?.open) dialog.value?.showModal()
    if(props.initialSelection?.length) {
      await nextTick()
      Array.from(dialog.value?.querySelectorAll<HTMLElement>('[data-review-id]') || []).find(el=>el.dataset.reviewId===props.initialSelection?.[0])?.scrollIntoView({block:'center'})
    }
  } else dialog.value?.close()
}, {immediate:true, flush:'post'})
function close() { if (!props.busy) emit('update:open', false) }
function focusItem(id:string) {
  Array.from(dialog.value?.querySelectorAll<HTMLElement>('[data-review-id]') || []).find(el=>el.dataset.reviewId===id)?.scrollIntoView({block:'start'})
}
defineExpose({focusItem})
onBeforeUnmount(() => dialog.value?.close())
</script>

<template>
  <Teleport to="body">
    <dialog ref="dialog" class="batch-review" :aria-labelledby="titleId" @cancel.prevent="close" @click="($event.target === dialog) && close()">
      <div class="review-shell">
        <header class="flex flex-wrap items-center gap-3 border-b border-white/10 p-4">
          <h2 :id="titleId" class="mr-auto text-lg font-bold text-slate-100">{{ title }}</h2>
          <span class="text-sm text-slate-300">{{ items.length }} 项{{items.some(item=>item.children?.length)?` · ${items.reduce((n,item)=>n+(item.children?.length || 0),0)} 个派生`:''}}</span>
          <button class="btn btn-ghost" :disabled="busy" @click="close">关闭</button>
        </header>
        <div class="flex items-center gap-4 border-b border-white/10 px-4 py-2 text-sm">
          <button class="text-sky-200" :disabled="busy" @click="selected=reviewSelectionIds(available)">全选</button>
          <button class="text-sky-200" :disabled="busy" @click="selected=[]">清空选择</button>
          <span class="text-slate-300">已选 {{ selectedIds.length }} 项</span>
        </div>
        <div class="review-content space-y-4 p-4">
          <slot name="notice" />
          <p v-if="!items.length" class="p-8 text-center text-slate-300"><slot name="empty">没有待审核的修改。</slot></p>
          <article v-for="item in items" :key="item.id" :data-review-id="item.id" class="rounded-xl border p-4" :class="selectedIds.includes(item.id)?'border-sky-400/50 bg-sky-950/20':'border-white/15'">
            <label class="mb-3 flex items-center gap-3 text-base font-bold text-slate-100"><input type="checkbox" :checked="selectedIds.includes(item.id)" :indeterminate="partiallySelected(item)" :disabled="busy || !!item.disabledReason || !!item.selectionBlockedReason" @change="selectItem(item,($event.target as HTMLInputElement).checked)" />{{ item.title }}</label>
            <div v-if="item.children?.length" class="mb-3 flex flex-wrap gap-3" aria-label="派生选择"><label v-for="child in item.children" :key="child.id" class="flex items-center gap-2 rounded border px-3 py-2 text-sm" :class="selectedIds.includes(child.id)?'border-sky-400/50 bg-sky-950/40 text-sky-100':'border-white/15 text-slate-300'"><input v-model="selected" type="checkbox" :value="child.id" :disabled="busy || !!item.disabledReason" />{{child.title}}</label></div>
            <p v-if="item.disabledReason" class="mb-3 text-sm text-amber-200">{{ item.disabledReason }}</p>
            <p v-if="item.selectionBlockedReason" class="mb-3 text-sm text-amber-200">{{ item.selectionBlockedReason }}</p>
            <p v-if="item.note" class="mb-3 whitespace-pre-wrap text-sm text-slate-300">{{ item.note }}</p>
            <slot name="editor" :item="item" :disabled="busy || !!item.disabledReason" />
            <section v-for="(group,index) in item.groups" :key="index" class="mb-4 last:mb-0">
              <h3 class="mb-2 font-semibold text-sky-200">{{ group.label }}</h3>
              <TextDiff :before="group.before" :after="group.after" before-label="修改前" after-label="修改后" />
            </section>
          </article>
        </div>
        <footer class="border-t border-white/10 p-4">
          <slot name="actions" :selected-ids="selectedIds" />
          <p v-if="error" role="alert" class="mb-3 whitespace-pre-wrap text-sm text-rose-200">{{ error }}</p>
          <div class="flex flex-wrap justify-end gap-3"><button class="btn btn-ghost" :disabled="busy" @click="close">取消</button><slot name="submit" :selected-ids="selectedIds"><button class="btn" :disabled="busy || !selectedIds.length" @click="emit('confirm', selectedIds)">{{ busy?'提交中…':`${submitLabel}（${selectedIds.length}）` }}</button></slot></div>
        </footer>
      </div>
    </dialog>
  </Teleport>
</template>

<style scoped>
.batch-review{width:min(1280px,calc(100vw - 32px));max-width:none;max-height:calc(100dvh - 32px);margin:auto;padding:0;border:1px solid #334155;border-radius:16px;color:#e2e8f0;background:#0c1422}
.batch-review::backdrop{background:#020617b8}
.review-shell{display:flex;flex-direction:column;max-height:calc(100dvh - 36px)}
.review-content{overflow:auto;min-height:0;overscroll-behavior:contain}
.review-content :deep(.text-diff pre){max-height:36vh}
</style>
