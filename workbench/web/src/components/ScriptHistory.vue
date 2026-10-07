<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { fetchEpisodeHistory } from '../api'
import TextDiff from './TextDiff.vue'

const props = defineProps<{ project: string; episode?: string }>()
const open = ref(false)
const loading = ref(false)
const error = ref('')
const versions = ref<Array<{ id: string; label: string; current: boolean; text: string }>>([])
const selected = ref('')
const compare = ref(false)
const picked = computed(() => versions.value.find(v => v.id === selected.value))
const current = computed(() => versions.value.find(v => v.current)?.text || '')
let sequence = 0
async function show() {
  open.value = true
  loading.value = true
  error.value = ''
  versions.value = []
  selected.value = ''
  compare.value = false
  const request = ++sequence
  try {
    const data = await fetchEpisodeHistory(props.project, props.episode)
    if (request !== sequence) return
    versions.value = data.versions
    selected.value = data.versions.find(v => !v.current)?.id || data.versions[0]?.id || ''
  } catch (e) {
    if (request === sequence) error.value = e instanceof Error ? e.message : '读取剧本历史失败'
  } finally { if (request === sequence) loading.value = false }
}
function close() { sequence++; open.value = false; loading.value = false }
watch(() => [props.project, props.episode], close)
</script>

<template>
  <button class="btn btn-ghost btn-sm" @click="show">{{episode ? '本集历史' : '剧本历史'}}</button>
  <Teleport to="body">
    <div v-if="open" class="overlay z-[75] p-4" @click.self="close" @keydown.esc="close">
      <section class="flex w-full max-w-6xl flex-col rounded-xl border border-white/20 bg-slate-950 p-5 shadow-xl" style="height:80vh" aria-label="剧本历史查看">
        <header class="mb-4 flex flex-wrap items-center gap-3">
          <h2 class="mr-auto font-bold">{{episode || '全剧'}} · 正文历史</h2>
          <button v-if="picked && !picked.current" class="btn btn-ghost btn-sm" @click="compare=!compare">{{compare ? '阅读历史稿' : '对比当前稿'}}</button>
          <button class="btn btn-ghost btn-sm" @click="close">关闭</button>
        </header>
        <p v-if="loading" class="text-slate-300">读取中…</p>
        <p v-else-if="error" role="alert" class="text-red-300">{{error}}</p>
        <div v-else class="grid min-h-0 flex-1 gap-4 md:grid-cols-[220px_minmax(0,1fr)]">
          <aside class="min-h-0 space-y-2 overflow-y-auto">
            <button v-for="v in versions" :key="v.id" class="w-full rounded-lg border p-3 text-left text-sm" :class="selected===v.id?'border-sky-400 bg-sky-400/15':'border-white/10 bg-white/5'" @click="selected=v.id">
              {{v.current ? '当前稿' : v.label}}<small class="block text-slate-400">{{v.text.length}} 字</small>
            </button>
            <p v-if="!versions.length" class="text-sm text-slate-400">尚无保存的正文。</p>
          </aside>
          <div class="min-h-0 overflow-y-auto">
            <TextDiff v-if="compare && picked && !picked.current" :before="picked.text" :after="current" before-label="历史稿" after-label="当前稿" />
            <pre v-else class="whitespace-pre-wrap break-words font-sans text-base leading-8 text-slate-200">{{picked?.text}}</pre>
          </div>
        </div>
      </section>
    </div>
  </Teleport>
</template>
