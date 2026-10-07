<script setup lang="ts">
// -*- coding: utf-8 -*-
/** 项目风格选择：从 Skill 库按 target 过滤启用项，选中即写 项目 剧本/style.json。
 *  target=anchor 写项目默认锚定；显式选择 image 风格时，生图以 image 风格为准。
 *  E10 显性选择：后端 /api/script/data 返回 style.json 显式值（含默认填入）；
 *  "auto" 是显式的"自动"，下拉显示为「自动（仅知识库）」，选它会显式写 "auto"
 *  （而不是删键——删键会被后端默认填入重新解析成唯一启用者，违背用户选择）。 */
import { ref, computed, watch } from 'vue'
import { type SkillItem } from '../api'
import StyledSelect from './StyledSelect.vue'
import { app, toast } from '../stores/app'
import { fetchStyleOptions, saveProjectStyle } from '../utils/styleOptions'

type StyleTarget = 'storyboard' | 'image' | 'script' | 'acting' | 'anchor'
  | 'script_structure' | 'script_pacing' | 'script_continuity'
  | 'storyboard_camera' | 'storyboard_keyframe' | 'storyboard_motion' | 'image_identity'
const props = defineProps<{ target: StyleTarget; label: string; hint?: string }>()
const emit = defineEmits<{ changed: [] }>()
const current = defineModel<string>({ default: '' })

const isAnchor = computed(() => props.target === 'anchor')
const isDimension = computed(() => props.target.startsWith('script_') || props.target.startsWith('storyboard_') || props.target === 'image_identity')
const legacyTarget = computed(() => props.target.startsWith('script_') ? 'script' : props.target === 'image_identity' ? 'image' : 'storyboard')
const emptyChoice = computed(() => isDimension.value ? '不使用' : '自动')
const customMode = ref(false)
const customText = ref('')

const skills = ref<SkillItem[]>([])
const allSkills = ref<SkillItem[]>([])
const projectStyle = ref<Record<string, string>>({})
const opts = computed(() => isAnchor.value
  ? ['自定义画风'].concat(skills.value.map((x) => x.id))
  : [emptyChoice.value].concat(skills.value.map((x) => x.id)))
const labels = computed<Record<string, string>>(() => {
  const m = Object.fromEntries(skills.value.map((s) => [s.id, `${s.name} — ${s.description.slice(0, 18)}`]))
  if (isAnchor.value) m['自定义画风'] = '自己写一句画风描述'
  else m[emptyChoice.value] = isDimension.value ? '不使用此方法' : '自动（仅知识库）'
  return m
})

let loadSequence = 0
async function load() {
  const project = app.current, sequence = ++loadSequence
  if (!project) return
  try {
    const d = await fetchStyleOptions(project)
    if (sequence !== loadSequence || project !== app.current) return
    allSkills.value = d.skills || []
    skills.value = allSkills.value
      .filter((s) => (isDimension.value ? s.dimension === props.target
        : s.target === (props.target === 'anchor' ? 'image' : props.target)
          && (!['image', 'anchor'].includes(props.target) || s.dimension !== 'image_identity')) && s.enabled)
    projectStyle.value = d.style || {}
    if (isAnchor.value) {
      const a = projectStyle.value.anchor || ''
      current.value = a || '自定义画风'
      customMode.value = !!a
      customText.value = a
    } else {
      const direct = (projectStyle.value[props.target] || '').trim()
      const legacy = isDimension.value ? (projectStyle.value[legacyTarget.value] || '').trim() : ''
      const value = direct === 'auto' ? ''
        : (direct && skills.value.some((s) => s.id === direct) ? direct : '')
          || (skills.value.some((s) => s.id === legacy) ? legacy : '')
      current.value = value && value !== 'auto' ? value : emptyChoice.value
    }
  } catch { if (sequence === loadSequence) { skills.value = []; toast('风格选项载入失败，请刷新重试', 'err') } }
}
watch(() => app.current, load, { immediate: true })

async function persist(mutate: (style: Record<string, string>) => void) {
  const project = app.current
  if (!project) return false
  try {
    const style = await saveProjectStyle(project,mutate)
    if (project !== app.current) return false
    projectStyle.value = style
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
    const anchor = customText.value
    const saved = await persist((st) => {
      st.anchor = anchor
      st.image = v
    })
    if (saved) toast(`画风锚定：${customText.value}`, 'ok', 2500)
    return
  }
  const saved = await persist((style) => {
    if (props.target === 'image' && !style.image_identity) {
      const old = allSkills.value.find((s) => s.id === style.image)
      if (old?.dimension === 'image_identity') style.image_identity = old.id
    }
    // E10：「自动」写成显式 "auto"——删键会被默认填入当成"未选择"重新解析
    style[props.target] = v === emptyChoice.value ? 'auto' : v
  })
  if (saved) toast(`${props.label}：${v === emptyChoice.value ? labels.value[emptyChoice.value] : v}`, 'ok', 2500)
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
