<script setup lang="ts">
// -*- coding: utf-8 -*-
/** 项目风格选择：从 Skill 库按 target 过滤启用项，选中即写 项目 剧本/style.json。
 *  target=anchor 写项目默认锚定；显式选择 image 风格时，生图以 image 风格为准。
 *  E10 显性选择：后端 /api/script/data 返回 style.json 显式值（含默认填入）；
 *  "auto" 是显式的"自动"，下拉显示为「自动（仅知识库）」，选它会显式写 "auto"
 *  （而不是删键——删键会被后端默认填入重新解析成唯一启用者，违背用户选择）。 */
import { ref, computed, watch } from 'vue'
import { getJSON, postJSON, type SkillItem } from '../api'
import StyledSelect from './StyledSelect.vue'
import { app, toast } from '../stores/app'

const props = defineProps<{ target: 'storyboard' | 'image' | 'script' | 'acting' | 'anchor'; label: string; hint?: string }>()
const emit = defineEmits<{ changed: [] }>()
const current = defineModel<string>({ default: '' })

const isAnchor = computed(() => props.target === 'anchor')
const customMode = ref(false)
const customText = ref('')

const skills = ref<SkillItem[]>([])
const opts = computed(() => isAnchor.value
  ? ['自定义画风'].concat(skills.value.map((x) => x.id))
  : ['自动'].concat(skills.value.map((x) => x.id)))
const labels = computed<Record<string, string>>(() => {
  const m = Object.fromEntries(skills.value.map((s) => [s.id, `${s.name} — ${s.description.slice(0, 18)}`]))
  if (isAnchor.value) m['自定义画风'] = '自己写一句画风描述'
  else m['自动'] = '自动（仅知识库）'
  return m
})

async function load() {
  if (!app.current) return
  try {
    skills.value = ((await getJSON<{ skills: SkillItem[] }>('/api/skills')).skills || [])
      .filter((s) => s.target === (props.target === 'anchor' ? 'image' : props.target) && s.enabled)
  } catch { skills.value = [] }
}
watch(() => app.current, load, { immediate: true })

// 回显项目当前显式选择（"auto"/缺失都归为「自动」；anchor 模式回显锚定句本身）
watch(() => app.current, async () => {
  try {
    const d = await getJSON<{ style?: Record<string, string> }>(`/api/script/data?project=${encodeURIComponent(app.current)}`)
    if (isAnchor.value) {
      const a = d.style?.anchor || ''
      current.value = a || '自定义画风'
      customMode.value = !!a
      customText.value = a
    } else {
      const v = (d.style?.[props.target] || '').trim()
      current.value = v && v !== 'auto' ? v : '自动'
    }
  } catch { current.value = isAnchor.value ? '自定义画风' : '自动' }
}, { immediate: true })

async function persist(mutate: (style: Record<string, string>) => void) {
  if (!app.current) return false
  try {
    const d = await getJSON<{ style?: Record<string, string> }>(`/api/script/data?project=${encodeURIComponent(app.current)}`)
    const style = { ...(d.style || {}) }
    mutate(style)
    await postJSON('/api/skills/style', { project: app.current, style })
    emit('changed')
    return true
  } catch (e) {
    toast(e instanceof Error ? e.message : '保存失败', 'err')
    return false
  }
}

async function onChange(v: string) {
  current.value = v
  if (!app.current) return
  if (isAnchor.value) {
    if (v === '自定义画风') {
      customMode.value = true
      return
    }
    const hit = skills.value.find((x) => x.id === v)
    customText.value = hit ? `${hit.name}风格` : v
    customMode.value = false
    const saved = await persist((st) => {
      st.anchor = customText.value
      st.image = v
    })
    if (saved) toast(`画风锚定：${customText.value}`, 'ok', 2500)
    return
  }
  const saved = await persist((style) => {
    // E10：「自动」写成显式 "auto"——删键会被默认填入当成"未选择"重新解析
    style[props.target] = v === '自动' ? 'auto' : v
  })
  if (saved) toast(`${props.label}：${v === '自动' ? '自动（仅知识库）' : v}`, 'ok', 2500)
}

async function saveCustom() {
  const t = customText.value.trim()
  if (!t) { toast('请填写画风描述', 'err'); return }
  const saved = await persist((st) => { st.anchor = t })
  if (saved) toast(`画风锚定已保存：${t}`, 'ok', 3000)
}
</script>

<template>
  <div class="inline-block">
    <label class="block text-xs text-slate-400">
      {{ label }}<span v-if="hint" class="ml-1 inline-flex h-3.5 w-3.5 cursor-help items-center justify-center rounded-full border border-slate-500 align-middle text-2xs text-slate-400" :title="hint">?</span>
      <StyledSelect :model-value="current" class="mt-1 w-44" :options="opts" :labels="labels"
        @update:model-value="current = $event" @change="onChange" />
    </label>
    <div v-if="isAnchor && customMode" class="mt-1.5">
      <textarea v-model="customText" rows="2" class="input w-full text-xs leading-relaxed"
        placeholder="一句话默认画风，如：日式动漫赛璐璐风格，柔和色彩，干净描线（显式选择生图风格时以该选择为准）"></textarea>
      <button class="btn btn-ghost btn-sm mt-1" @click="saveCustom">保存锚定</button>
    </div>
  </div>
</template>
