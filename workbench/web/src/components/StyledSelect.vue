<script setup lang="ts">
// -*- coding: utf-8 -*-
/** 玻璃拟态 combobox：与 .input 同风格的触发器（可手输）+ 自定义下拉浮层。
 *  选项 > filterThreshold 时显示过滤搜索框；支持键盘 ↑↓/回车/Esc、点外部关闭。 */
import { ref, computed, watch, nextTick, onMounted, onBeforeUnmount } from 'vue'
import { restoreSelection } from '../utils/productionUi'

const props = withDefaults(
  defineProps<{
    modelValue?: string
    options: string[]
    /** 选项 → 显示文案（缺省 = 选项本身）。 */
    labels?: Record<string, string>
    placeholder?: string
    disabled?: boolean
    filterThreshold?: number
    /** 可选的浏览器记忆键；相同项目控件应使用稳定且互不冲突的键。 */
    storageKey?: string
  }>(),
  { modelValue: '', labels: undefined, placeholder: '', disabled: false, filterThreshold: 10, storageKey: '' }
)

const emit = defineEmits<{
  'update:modelValue': [string]
  change: [string]
}>()

const root = ref<HTMLElement | null>(null)
const panel = ref<HTMLElement | null>(null)
const filterInput = ref<HTMLInputElement | null>(null)
const open = ref(false)
const filter = ref('')
const active = ref(-1)

/** 下拉面板 Teleport 到 body 后的 fixed 定位（宽度跟随触发器，避免被 overflow 容器裁剪）。 */
const panelStyle = ref<Record<string, string>>({})
function updatePanelPos() {
  const r = root.value?.getBoundingClientRect()
  if (!r) return
  panelStyle.value = {
    position: 'fixed',
    left: `${r.left}px`,
    top: `${r.bottom + 4}px`,
    width: `${r.width}px`,
    zIndex: '80'
  }
}

function readStoredSelection(): string {
  if (!props.storageKey || typeof window === 'undefined') return ''
  try { return window.localStorage.getItem(props.storageKey) || '' } catch { return '' }
}

function applyStoredSelection() {
  const next = restoreSelection(props.options, props.modelValue, readStoredSelection())
  if (next && next !== props.modelValue) emit('update:modelValue', next)
}

const showFilter = computed(() => props.options.length > props.filterThreshold)

const filtered = computed(() => {
  const q = filter.value.trim().toLowerCase()
  if (!q) return props.options
  return props.options.filter(
    (o) => o.toLowerCase().includes(q) || (props.labels?.[o] || '').toLowerCase().includes(q)
  )
})

const labelOf = (o: string) => props.labels?.[o] ?? o

function syncActive() {
  const i = filtered.value.indexOf(props.modelValue ?? '')
  active.value = i >= 0 ? i : 0
}

async function toggle() {
  if (props.disabled) return
  open.value = !open.value
  if (open.value) {
    filter.value = ''
    syncActive()
    if (showFilter.value) {
      await nextTick()
      filterInput.value?.focus()
    }
  }
}

function choose(o: string) {
  emit('update:modelValue', o)
  emit('change', o)
  if (props.storageKey && typeof window !== 'undefined') {
    try { window.localStorage.setItem(props.storageKey, o) } catch { /* 隐私模式或配额不足时不阻断选择 */ }
  }
  open.value = false
}

function onInput(e: Event) {
  const value = (e.target as HTMLInputElement).value
  emit('update:modelValue', value)
  if (props.storageKey && props.options.includes(value)) {
    try { window.localStorage.setItem(props.storageKey, value) } catch {}
  }
  if (!open.value) {
    open.value = true
    syncActive()
  }
}

function scrollActive() {
  const el = panel.value?.querySelector(`[data-ssi="${active.value}"]`)
  el?.scrollIntoView({ block: 'nearest' })
}

function move(dir: 1 | -1) {
  const n = filtered.value.length
  if (!n) return
  active.value = dir === 1 ? (active.value + 1) % n : (active.value - 1 + n) % n
  scrollActive()
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault()
    if (!open.value) {
      open.value = true
      syncActive()
    } else {
      move(e.key === 'ArrowDown' ? 1 : -1)
    }
  } else if (e.key === 'Enter') {
    if (open.value && active.value >= 0 && filtered.value[active.value] !== undefined) {
      e.preventDefault()
      choose(filtered.value[active.value])
    }
  } else if (e.key === 'Escape') {
    open.value = false
  }
}

function onFilterKeydown(e: KeyboardEvent) {
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault()
    move(e.key === 'ArrowDown' ? 1 : -1)
  } else if (e.key === 'Enter') {
    if (active.value >= 0 && filtered.value[active.value] !== undefined) {
      e.preventDefault()
      choose(filtered.value[active.value])
    }
  } else if (e.key === 'Escape') {
    open.value = false
  }
}

function onDocClick(e: MouseEvent) {
  const target = e.target as Node
  if (root.value && !root.value.contains(target) && !panel.value?.contains(target)) open.value = false
}

watch(open, (v) => {
  if (!v) {
    filter.value = ''
    window.removeEventListener('scroll', updatePanelPos, true)
    window.removeEventListener('resize', updatePanelPos)
  } else {
    updatePanelPos()
    window.addEventListener('scroll', updatePanelPos, true)
    window.addEventListener('resize', updatePanelPos)
  }
})

// 注意：只有 choose()（用户真实选择）才写 localStorage；view 侧异步加载期间的
// 程序化赋值（默认选最后一项等）绝不写回，否则会把用户记忆覆盖成默认值。

watch(() => [props.storageKey, props.options.join('\u0000')], () => {
  if (props.storageKey) applyStoredSelection()
}, { flush: 'post' })

onMounted(() => {
  applyStoredSelection()
  document.addEventListener('click', onDocClick)
})
onBeforeUnmount(() => {
  document.removeEventListener('click', onDocClick)
  window.removeEventListener('scroll', updatePanelPos, true)
  window.removeEventListener('resize', updatePanelPos)
})
</script>

<template>
  <div ref="root" class="relative">
    <input
      class="input w-full pr-8"
      :class="{ 'opacity-50': disabled }"
      :value="modelValue ?? ''"
      :placeholder="placeholder"
      :disabled="disabled"
      @input="onInput"
      @keydown="onKeydown"
      @focus="open = true; syncActive()"
    />
    <button
      type="button"
      class="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-1 text-slate-500 transition-colors hover:text-slate-200"
      :disabled="disabled"
      @mousedown.prevent
      @click="toggle"
    >
      <svg
        width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
        class="transition-transform duration-200" :class="{ 'rotate-180': open }"
      >
        <path d="M6 9l6 6 6-6" stroke-linecap="round" stroke-linejoin="round" />
      </svg>
    </button>

    <Teleport to="body">
      <div
        v-if="open"
        ref="panel"
        :style="panelStyle"
        class="overflow-hidden rounded-xl border border-line bg-[#0d1420]/95 shadow-2xl backdrop-blur-xl"
      >
        <div v-if="showFilter" class="border-b border-line-soft p-1.5">
          <input
            ref="filterInput"
            v-model="filter"
            class="input !py-1.5 text-xs"
            placeholder="过滤…"
            @keydown="onFilterKeydown"
          />
        </div>
        <ul class="max-h-56 overflow-y-auto p-1">
          <li
            v-for="(o, i) in filtered"
            :key="o"
            :data-ssi="i"
            class="flex cursor-pointer items-center justify-between gap-2 rounded-lg px-2.5 py-1.5 text-xs transition-colors"
            :class="i === active ? 'bg-white/10 text-slate-100' : 'text-slate-400 hover:bg-white/5 hover:text-slate-200'"
            @mouseenter="active = i"
            @mousedown.prevent="choose(o)"
          >
            <span class="truncate">{{ labelOf(o) }}</span>
            <svg
              v-if="o === modelValue"
              width="12" height="12" viewBox="0 0 24 24" fill="none"
              :stroke="'var(--accent, #22d3ee)'" stroke-width="3" class="shrink-0"
            >
              <path d="M5 13l4 4L19 7" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
          </li>
          <li v-if="!filtered.length" class="px-2.5 py-2 text-center text-xs-plus text-slate-500">无匹配项</li>
        </ul>
      </div>
    </Teleport>
  </div>
</template>
