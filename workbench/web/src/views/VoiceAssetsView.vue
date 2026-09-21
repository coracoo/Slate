<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { app, projectFiles, toast } from '../stores/app'
import { getJSON, mediaUrl } from '../api'
import { trackJob } from '../stores/jobs'
import { studioPost, submitStudioJob } from '../utils/productionStudio'
import { useBoardSelection } from '../utils/useBoardSelection'

interface Voice {id: string; revision: number; name: string; voice_id: string; sample: string; tts_verified: boolean; derived_from?: string; character_id?: string}
interface Actor {id: string; name: string; voice?: string; voice_binding?: {voice_asset_id: string; revision: number}; voice_variants?: {voice_asset_id: string; name: string}[]}
interface Catalog {voice_id: string; voice_name?: string; description?: string[]; category: string}
const actors = ref<Actor[]>([]), voices = ref<Voice[]>([]), catalog = ref<Catalog[]>([]), actorId = ref(''), search = ref('')
const description = ref(''), preview = ref('你好，这是角色的声音，请听听是否符合他的性格。'), voiceName = ref(''), busy = ref(false), error = ref(''), board = ref('')
const actor = computed(() => actors.value.find(a => a.id === actorId.value))
const variantSource = ref<Voice | null>(null), variantName = ref(''), speechVoice = ref('')
watch(actorId, () => { speechVoice.value = ''; variantSource.value = null; variantName.value = '' })
async function saveVariant() {
  if (!actor.value || !variantSource.value || !variantName.value.trim()) return
  busy.value = true
  try {
    await studioPost('voice-bind', {project: app.current, character_id: actorId.value, voice_asset_id: variantSource.value.id,
      revision: variantSource.value.revision, mode: 'variant', name: '角色音乐·' + variantName.value.trim()})
    variantSource.value = null; variantName.value = ''; await load(); toast('派生音色已保存，全剧默认音色保持不变', 'ok')
  } catch (e) { error.value = String(e) } finally { busy.value = false }
}
const filtered = computed(() => catalog.value.filter(v => `${v.voice_name} ${v.voice_id} ${v.description?.join(' ')}`.includes(search.value)))
const boards = computed(() => projectFiles('分镜', /\.json$/))
useBoardSelection(board, boards, 'voices')
watch(board, name => { if (app.current && name) localStorage.setItem(`wb.${app.current}.voices.board`, name) })
async function load() {
  const project = app.current; if (!project) return
  try {
    const r = await getJSON<{characters: Actor[]; voices: Voice[]; catalog: Catalog[]}>(`/api/studio/voices?project=${encodeURIComponent(project)}`)
    if (project !== app.current) return
    actors.value = r.characters; voices.value = r.voices; catalog.value = r.catalog
    if (!actors.value.some(a => a.id === actorId.value)) actorId.value = actors.value[0]?.id || ''
    if (!boards.value.includes(board.value)) board.value = boards.value[0] || ''
  } catch (e) { error.value = String(e) }
}
async function submit(action: string, extra: Record<string, unknown> = {}, recover = false) {
  const project = app.current
  const body = {project, action, vendor_id: 'minimax', ...extra}
  busy.value = true; error.value = ''
  try {
    const r = await submitStudioJob(body, recover)
    if (r.id) {
      const result = await trackJob(r.id, '音色素材')
      if (!result.success) throw new Error(result.err || '音色任务失败，请查看任务日志')
    }
    if (app.current === project) await load()
  } catch (e) { error.value = String(e) } finally { busy.value = false }
}
async function bind(voice: Voice) {
  if (!actor.value) return
  try { await studioPost('voice-bind', {project: app.current, character_id: actorId.value, voice_asset_id: voice.id, revision: voice.revision}); await load(); toast('角色音色已绑定', 'ok') }
  catch (e) { error.value = String(e) }
}
function url(path: string) { return mediaUrl(`projects/${app.current}/${path}`) }
watch(() => app.current, () => { void load() }, {immediate: true})
</script>

<template>
  <div class="page-wide">
    <header class="mb-5 flex items-end justify-between"><div><h1 class="grad-text text-2xl font-black">④ 音色绑定</h1><p class="mt-1 text-xs text-slate-500">角色默认音色全剧通用；派生音色单独命名保存，可选用于台词配音</p></div><RouterLink to="/studio/asset" class="text-sm text-sky-300">返回素材生成</RouterLink></header>
    <p v-if="error" role="alert" class="mb-4 rounded-lg bg-rose-950/50 p-3 text-rose-200">{{ error }}</p>
    <button class="mb-3 text-xs text-sky-300" :disabled="busy" @click="submit('recover', {}, true)">接管未确认请求</button>
    <div class="grid items-start gap-5 lg:grid-cols-[230px_minmax(0,1fr)]">
      <aside class="glass space-y-2 p-3"><h2 class="mb-4 text-sm font-bold">人物素材</h2><button v-for="a in actors" :key="a.id" class="block w-full rounded-xl border p-3 text-left" :class="a.id === actorId ? 'border-sky-400/50 bg-sky-900/20' : 'border-white/10'" @click="actorId = a.id"><b>{{ a.name }}</b><small class="mt-1 block text-slate-400">{{ a.voice_binding ? '已绑定音色' : '待绑定' }}</small></button><p class="text-xs text-slate-500">状态 / 子素材沿用母角色音色。旁白保留在独立台词轨。</p></aside>
      <main class="space-y-5">
        <section class="glass space-y-3 p-4"><h2 class="font-bold text-sky-200">{{ actor?.name || '请选择角色' }}</h2><p class="text-xs text-slate-400">{{ actor?.voice || '可按人物性格与年龄设计音色' }}</p>
          <label class="block text-xs text-slate-400">试听文本<input v-model="preview" class="voice-input mt-2" /></label>
          <label class="block text-xs text-slate-400">本次配音音色<select v-model="speechVoice" class="voice-input mt-2"><option value="">全剧默认音色</option><option v-for="v in actor?.voice_variants || []" :key="v.voice_asset_id" :value="v.voice_asset_id">{{ v.name }}</option></select></label>
          <div class="flex flex-wrap gap-2"><select v-model="board" class="voice-input max-w-xs"><option v-for="b in boards" :key="b">{{ b }}</option></select><button class="btn" :disabled="busy || !board || !actor?.voice_binding" @click="submit('speech', {board, character_id: actorId, voice_asset_id: speechVoice || undefined})">按绑定音色生成本集角色台词</button></div>
          <p class="text-xs text-slate-500">台词直接读取分镜 lines，每句单独归档。生成试听、AI 音色创作和台词配音会调用 MiniMax；播放已保存样本不调用 API。</p>
        </section>
        <section class="glass p-4"><h2 class="mb-3 font-bold">项目音色素材</h2><div class="grid gap-3 xl:grid-cols-2"><article v-for="v in voices" :key="v.id" class="rounded-xl border border-white/10 p-3"><b class="text-sm">{{ v.name }} · r{{ v.revision }}</b><p class="my-1 text-xs text-slate-500">{{ v.voice_id }} · {{ v.tts_verified ? 'TTS 已验证' : '设计试听已保存，待正式 TTS 验证' }}</p><audio :src="url(v.sample)" controls preload="none" class="my-3 w-full" /><button class="btn btn-sm" :disabled="!actor || busy" @click="bind(v)">{{ actor?.voice_binding?.voice_asset_id === v.id ? '全剧默认音色' : '设为全剧默认 · ' + (actor?.name || '角色') }}</button><button class="btn btn-sm btn-ghost ml-2" :disabled="!actor?.voice_binding || busy" @click="variantSource = v; variantName = ''">保存为派生音色</button><p v-if="v.derived_from" class="mt-2 text-xs text-sky-300">派生音色 · {{ actors.find(a => a.id === v.character_id)?.name }}</p></article><p v-if="!voices.length" class="text-xs text-slate-500">从下面的云端音色库生成试听，或创作一个新音色。</p></div></section>
        <section v-if="variantSource" class="glass space-y-3 p-4"><h2 class="font-bold">为 {{ actor?.name }} 保存派生音色</h2><p class="text-xs text-slate-400">来源：{{ variantSource.name }}。填写名称后保存，全剧默认绑定保持不变。</p><label class="flex items-center gap-2 text-sm"><span class="shrink-0">角色音乐·</span><input v-model="variantName" class="voice-input" placeholder="请编辑派生名称，例如幼年、受伤" /></label><button class="btn" :disabled="busy || !variantName.trim()" @click="saveVariant">保存派生音色</button><button class="btn btn-ghost ml-2" @click="variantSource = null">取消</button></section>
        <section class="glass space-y-3 p-4"><h2 class="font-bold">AI 音色创作</h2><input v-model="voiceName" class="voice-input" placeholder="音色名称" /><textarea v-model="description" class="voice-input" rows="3" placeholder="例如：年轻男性，音色温和，吐字清晰，语速舒缓，略带沙哑" /><button class="btn" :disabled="busy || !description.trim()" @click="submit('voice_design', {description, name: voiceName, preview_text: preview})">创作音色并保存试听</button></section>
        <section class="glass space-y-3 p-4"><div class="flex justify-between"><h2 class="font-bold">MiniMax 云端音色库</h2><button class="btn btn-sm" :disabled="busy" @click="submit('voice_catalog')">从 API 刷新</button></div><input v-model="search" class="voice-input" placeholder="按名称、描述或音色 ID 搜索" /><div class="max-h-[500px] space-y-2 overflow-auto"><article v-for="v in filtered" :key="v.voice_id" class="flex items-center justify-between gap-3 rounded-lg border border-white/10 p-3"><div class="min-w-0"><b class="text-sm">{{ v.voice_name || v.voice_id }}</b><p class="text-xs text-slate-400">{{ v.description?.join('；') }}</p></div><button class="btn btn-sm shrink-0" :disabled="busy" @click="submit('voice_sample', {voice_id: v.voice_id, name: v.voice_name, preview_text: preview})">生成试听并保存</button></article></div></section>
      </main>
    </div>
  </div>
</template>

<style scoped>.voice-input{width:100%;border:1px solid #ffffff20;border-radius:8px;background:#0a1522;color:#d9e5f3;padding:10px;font-size:13px}</style>
