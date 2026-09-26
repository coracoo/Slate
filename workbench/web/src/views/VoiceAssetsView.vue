<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { app, projectFiles, toast } from '../stores/app'
import { getJSON, mediaUrl } from '../api'
import { trackJob } from '../stores/jobs'
import { studioPost, submitStudioJob } from '../utils/productionStudio'
import { useBoardSelection } from '../utils/useBoardSelection'

interface State {id: string; label: string; image?: string}
interface Variant {voice_asset_id: string; revision: number; name: string; state?: string}
interface Voice {id: string; revision: number; name: string; voice_id: string; sample: string; tts_verified: boolean; derived_from?: string; character_id?: string; origin?: string; vendor_id?: string; description?: string}
interface Actor {id: string; name: string; voice?: string; voice_binding?: {voice_asset_id: string; revision: number}; voice_variants?: Variant[]; states?: State[]}
interface Catalog {voice_id: string; voice_name?: string; description?: string[]; category: string; preview_audio?: string}

const actors = ref<Actor[]>([]), voices = ref<Voice[]>([]), catalog = ref<Catalog[]>([])
const actorId = ref(''), search = ref('')
const description = ref(''), preview = ref('你好，这是角色的声音，请听听是否符合他的性格。'), voiceName = ref('')
const busy = ref(false), error = ref(''), board = ref('')
const stateSel = ref<Record<string, string>>({})
const fileInput = ref<HTMLInputElement | null>(null), uploading = ref(false)
const actor = computed(() => actors.value.find(a => a.id === actorId.value))
const speechVoice = ref('')
watch(actorId, () => { speechVoice.value = ''; syncStateSel() })
function syncStateSel() {
  const map: Record<string, string> = {}
  for (const s of actor.value?.states || []) map[s.id] = actor.value?.voice_variants?.find(v => v.state === s.id)?.voice_asset_id || ''
  stateSel.value = map
}
const boards = computed(() => projectFiles('分镜', /\.json$/))
useBoardSelection(board, boards, 'voices')
watch(board, name => { if (app.current && name) localStorage.setItem(`wb.${app.current}.voices.board`, name) })
const ORIGIN: Record<string, string> = {voice_sample: 'TTS 试听', voice_design: 'AI 创作', upload: '本地上传', voice_clone: '本地复刻'}
interface VendorLite {id: string; label?: string; enabled?: boolean; models?: Record<string, string>; endpoints?: Record<string, string>}
const speechVendors = ref<VendorLite[]>([]), speechVendor = ref('minimax')
watch(speechVendor, v => { if (v && app.current) localStorage.setItem(`wb.${app.current}.voices.speechVendor`, v) })
async function loadSpeechVendors() {
  try {
    const r = await getJSON<{vendors: VendorLite[]}>('/api/env/config')
    speechVendors.value = (r.vendors || []).filter(v => v.enabled && ((v.models?.speech) || (v.endpoints?.speech)))
    const saved = app.current ? localStorage.getItem(`wb.${app.current}.voices.speechVendor`) || '' : ''
    if (!speechVendors.value.some(v => v.id === speechVendor.value)) speechVendor.value = speechVendors.value.some(v => v.id === saved) ? saved : 'minimax'
  } catch { /* 环境配置不可用时保持 MiniMax */ }
}
async function cloneVoice(v: Voice) {
  if (!app.current) return
  try { await submit('voice_clone', {voice_asset_id: v.id, preview_text: preview.value}) } catch (e) { error.value = String(e) }
}
async function load() {
  const project = app.current; if (!project) return
  try {
    const r = await getJSON<{characters: Actor[]; voices: Voice[]; catalog: Catalog[]}>(`/api/studio/voices?project=${encodeURIComponent(project)}`)
    if (project !== app.current) return
    actors.value = r.characters; voices.value = r.voices; catalog.value = r.catalog
    if (!actors.value.some(a => a.id === actorId.value)) actorId.value = actors.value[0]?.id || ''
    if (!boards.value.includes(board.value)) board.value = boards.value[0] || ''
    syncStateSel()
  } catch (e) { error.value = String(e) }
}
async function submit(action: string, extra: Record<string, unknown> = {}, recover = false, vendorId = 'minimax') {
  const project = app.current
  const body = {project, action, vendor_id: vendorId, ...extra}
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
async function bindGlobal(v: Voice) {
  if (!actor.value) return
  try { await studioPost('voice-bind', {project: app.current, character_id: actorId.value, voice_asset_id: v.id, revision: v.revision}); await load(); toast(`${v.name} 已绑定为 ${actor.value.name} 的全剧默认音色`, 'ok') }
  catch (e) { error.value = String(e) }
}
async function bindState(stateId: string) {
  const voiceId = stateSel.value[stateId] || ''
  try {
    await studioPost('voice-bind', {project: app.current, character_id: actorId.value, state: stateId, voice_asset_id: voiceId})
    await load(); toast(voiceId ? '状态音色已绑定' : '已恢复跟随全剧默认', 'ok')
  } catch (e) { error.value = String(e) }
}
async function uploadVoice(f: Event) {
  const input = f.target as HTMLInputElement; const file = input.files?.[0]; input.value = ''
  const project = app.current
  if (!file || !project) return
  uploading.value = true; error.value = ''
  try {
    const r = await fetch(`/api/voice/upload?project=${encodeURIComponent(project)}&name=${encodeURIComponent(file.name)}`,
      {method: 'POST', headers: {'Content-Type': 'application/octet-stream'}, body: file})
    const data = await r.json()
    if (!r.ok) throw new Error(data.err || '上传失败')
    toast(`音色「${data.voice.name}」已入库`, 'ok'); await load()
  } catch (e) { error.value = String(e) } finally { uploading.value = false }
}
function stateVoiceName(stateId: string) {
  const vid = actor.value?.voice_variants?.find(v => v.state === stateId)?.voice_asset_id
  return vid ? voices.value.find(v => v.id === vid)?.name || vid : ''
}
function url(path: string) { return mediaUrl(`projects/${app.current}/${path}`) }
watch(() => app.current, () => { void load(); void loadSpeechVendors() }, {immediate: true})
</script>

<template>
  <div class="page-wide">
    <header class="mb-5 flex items-end justify-between"><div><h1 class="grad-text text-2xl font-black">④ 音色绑定</h1><p class="mt-1 text-xs text-slate-500">流程：拉取云端默认音色 → 生成试听入库 → 试听后绑定为角色全剧默认；分状态音色按素材派生状态单独配置</p></div><RouterLink to="/studio/asset" class="text-sm text-sky-300">返回素材生成</RouterLink></header>
    <p v-if="error" role="alert" class="mb-4 rounded-lg bg-rose-950/50 p-3 text-rose-200">{{ error }}</p>
    <div class="grid items-start gap-5 lg:grid-cols-[230px_minmax(0,1fr)]">
      <aside class="glass space-y-2 p-3"><h2 class="mb-4 text-sm font-bold">人物素材</h2><button v-for="a in actors" :key="a.id" class="block w-full rounded-xl border p-3 text-left" :class="a.id === actorId ? 'border-sky-400/50 bg-sky-900/20' : 'border-white/10'" @click="actorId = a.id"><b>{{ a.name }}</b><small class="mt-1 block text-slate-400">{{ a.voice_binding ? '已绑定全剧默认' : '待绑定' }}<template v-if="a.states?.length"> · {{ a.states.length }} 状态</template></small></button><p class="text-xs text-slate-500">子素材沿用母角色音色。旁白是独立说话人、可以绑定音色：<b class="text-slate-400">未绑定则不生成、不注入</b>（旁白留在台词轨，出声由后期人声轨处理）。状态音色在主区分状态区配置。</p></aside>
      <main class="space-y-5">
        <div class="grid items-start gap-5 xl:grid-cols-2">
          <section class="glass space-y-3 p-4">
            <div class="flex flex-wrap items-center gap-2">
              <h2 class="font-bold text-sky-200">云端音色库</h2>
              <span class="rounded-md border border-white/15 bg-white/5 px-2 py-1 text-xs text-slate-300" title="音色库 / 试听 / 创作 / 复刻当前仅支持 MiniMax；台词配音可在底部选择其他语音厂商">MiniMax</span>
              <button class="btn btn-sm" :disabled="busy" @click="submit('voice_catalog')">{{ catalog.length ? '重新拉取' : '拉取默认音色' }}</button>
            </div>
            <p class="text-xs text-slate-500">拉取免费（仅缓存音色列表，按厂商账户隔离）。MiniMax 列表接口不返回试听音频：试听需点「生成试听并保存」生成一次样本（调 TTS 计费，一句文本费用极低），样本永久入库后试听不再花钱。</p>
            <div class="flex gap-2"><input v-model="search" class="voice-input" placeholder="搜索名称 / 描述 / ID" /><button class="btn btn-sm btn-ghost shrink-0" :disabled="busy" @click="submit('recover', {}, true)">接管未确认请求</button></div>
            <div class="max-h-[430px] space-y-2 overflow-auto pr-1">
              <article v-for="v in catalog.filter(c => `${c.voice_name} ${c.voice_id} ${c.description?.join(' ')}`.includes(search))" :key="v.voice_id" class="rounded-lg border border-white/10 p-3">
                <div class="flex items-center justify-between gap-3">
                  <div class="min-w-0"><b class="text-sm">{{ v.voice_name || v.voice_id }}</b><p class="truncate text-xs text-slate-400" :title="v.description?.join('；')">{{ v.description?.join('；') }}</p></div>
                  <button v-if="!v.preview_audio" class="btn btn-sm shrink-0" :disabled="busy" @click="submit('voice_sample', {voice_id: v.voice_id, name: v.voice_name, preview_text: preview})">生成试听并保存</button>
                </div>
                <audio v-if="v.preview_audio" :src="v.preview_audio" controls preload="none" class="mt-2 w-full" />
              </article>
              <p v-if="!catalog.length" class="text-xs text-slate-500">尚未拉取：点「拉取默认音色」获取 MiniMax 云端音色列表。</p>
            </div>
          </section>
          <section class="glass space-y-3 p-4">
            <div class="flex flex-wrap items-center gap-2">
              <h2 class="font-bold text-sky-200">本地音色库</h2>
              <input ref="fileInput" type="file" accept=".mp3,.wav,.m4a,.ogg,.flac" class="hidden" @change="uploadVoice" />
              <button class="btn btn-sm" :disabled="uploading" @click="fileInput?.click()">{{ uploading ? '上传中…' : '上传音色文件' }}</button>
              <span class="text-xs text-slate-500">{{ voices.length }} 个</span>
            </div>
            <div class="max-h-[520px] space-y-3 overflow-auto pr-1">
              <article v-for="v in voices" :key="v.id" class="rounded-xl border border-white/10 p-3">
                <div class="flex items-center gap-2"><b class="text-sm">{{ v.name }} · r{{ v.revision }}</b>
                  <span class="rounded px-1.5 py-0.5 text-[10px]" :class="v.origin === 'upload' ? 'bg-emerald-900/60 text-emerald-200' : v.origin === 'voice_design' ? 'bg-violet-900/60 text-violet-200' : 'bg-sky-900/60 text-sky-200'">{{ ORIGIN[v.origin || ''] || '本地' }}</span>
                  <span v-if="v.tts_verified" class="rounded bg-slate-700/60 px-1.5 py-0.5 text-[10px] text-slate-300">TTS 已验证</span>
                </div>
                <p class="mt-1 text-xs text-slate-500">{{ v.voice_id || '（待正式 TTS 后获得云端 voice_id）' }}</p>
                <p class="truncate text-[11px] text-slate-600" :title="v.sample">{{ v.sample }}</p>
                <audio :src="url(v.sample)" controls preload="none" class="my-2 w-full" />
                <div class="flex flex-wrap gap-2">
                  <button class="btn btn-sm" :disabled="!actor || busy || actor.voice_binding?.voice_asset_id === v.id" @click="bindGlobal(v)">{{ actor?.voice_binding?.voice_asset_id === v.id ? '当前全剧默认' : '设为 ' + (actor?.name || '角色') + ' 全剧默认' }}</button>
                  <button v-if="!v.voice_id" class="btn btn-sm btn-ghost" :disabled="busy" title="调用 MiniMax 音色复刻：样本需 mp3/m4a/wav、10秒–5分钟、≤20MB" @click="cloneVoice(v)">克隆为云端音色</button>
                </div>
                <p v-if="v.origin === 'voice_clone'" class="mt-2 text-xs text-amber-300">{{ v.description || '复刻音色 7 天内需正式合成台词，否则云端将删除' }}</p>
                <p v-if="v.derived_from" class="mt-2 text-xs text-sky-300">派生音色 · {{ actors.find(a => a.id === v.character_id)?.name }}</p>
              </article>
              <p v-if="!voices.length" class="text-xs text-slate-500">空库：从左侧云端音色生成试听、上传本地音频，或用下方 AI 音色创作。</p>
            </div>
          </section>
        </div>
        <section class="glass space-y-3 p-4">
          <h2 class="font-bold text-sky-200">分状态音色 <span class="text-xs font-normal text-slate-500">{{ actor?.name || '请选择角色' }} · 左侧派生状态图，右侧音色参考</span></h2>
          <p v-if="!actor?.states?.length" class="text-xs text-slate-500">该角色暂无派生状态；请到「② 素材提炼」为角色定义状态资产（states），生成状态图后回到这里配音色。</p>
          <div v-for="s in actor?.states || []" :key="s.id" class="grid items-center gap-3 rounded-xl border border-white/10 p-3 md:grid-cols-[120px_minmax(0,1fr)_auto]">
            <div class="flex h-[90px] items-center justify-center overflow-hidden rounded-lg border border-white/10 bg-black/30">
              <img v-if="s.image" :src="url(s.image)" class="h-full w-full object-contain" :alt="s.label" />
              <span v-else class="text-[11px] text-slate-600">暂无状态图</span>
            </div>
            <div class="min-w-0">
              <b class="text-sm">{{ s.label }}</b>
              <p class="text-xs" :class="stateVoiceName(s.id) ? 'text-emerald-300' : 'text-slate-500'">{{ stateVoiceName(s.id) ? '当前：' + stateVoiceName(s.id) : '跟随全剧默认音色' }}</p>
              <select v-model="stateSel[s.id]" class="voice-input mt-2"><option value="">（跟随全剧默认）</option><option v-for="v in voices" :key="v.id" :value="v.id">{{ v.name }} · r{{ v.revision }}</option></select>
            </div>
            <button class="btn btn-sm" :disabled="busy" @click="bindState(s.id)">{{ stateSel[s.id] ? '绑定该状态' : '恢复默认' }}</button>
          </div>
        </section>
        <section class="glass space-y-3 p-4">
          <h2 class="font-bold text-sky-200">AI 音色创作 <span class="text-xs font-normal text-slate-500">按文字描述创作新音色，试听自动保存到本地音色库</span></h2>
          <div class="grid gap-3 md:grid-cols-2">
            <input v-model="voiceName" class="voice-input" placeholder="音色名称" />
            <input v-model="preview" class="voice-input" placeholder="试听文本" />
          </div>
          <textarea v-model="description" class="voice-input" rows="3" placeholder="例如：年轻男性，音色温和，吐字清晰，语速舒缓，略带沙哑"></textarea>
          <button class="btn" :disabled="busy || !description.trim()" @click="submit('voice_design', {description, name: voiceName, preview_text: preview})">创作音色并保存试听</button>
        </section>
        <details class="glass p-4">
          <summary class="cursor-pointer text-sm font-bold text-slate-300">台词配音（按绑定音色生成本集角色台词）</summary>
          <div class="mt-3 space-y-3">
            <label class="block text-xs text-slate-400">语音厂商（MiniMax 用绑定音色；本地 / OpenAI 兼容 TTS 用厂商配置的默认音色）<select v-model="speechVendor" class="voice-input mt-2"><option v-for="v in speechVendors" :key="v.id" :value="v.id">{{ v.label || v.id }}{{ v.id === 'minimax' ? '' : '（OpenAI 兼容）' }}</option></select></label>
            <label class="block text-xs text-slate-400">本次配音音色<select v-model="speechVoice" class="voice-input mt-2" :disabled="speechVendor !== 'minimax'"><option value="">{{ speechVendor !== 'minimax' ? '厂商默认音色' : '全剧默认音色' }}</option><option v-for="v in actor?.voice_variants || []" :key="v.voice_asset_id" :value="v.voice_asset_id">{{ v.name }}{{ v.state ? '（状态）' : '' }}</option></select></label>
            <div class="flex flex-wrap gap-2"><select v-model="board" class="voice-input max-w-xs"><option v-for="b in boards" :key="b">{{ b }}</option></select><button class="btn" :disabled="busy || !board || (speechVendor === 'minimax' && !actor?.voice_binding)" @click="submit('speech', {board, character_id: actorId, voice_asset_id: speechVoice || undefined}, false, speechVendor)">生成本集角色台词</button></div>
            <p class="text-xs text-slate-500">台词直接读取分镜 lines，每句单独归档。生成试听、AI 音色创作和台词配音会调用所选厂商；播放已保存样本不调用 API。</p>
          </div>
        </details>
      </main>
    </div>
  </div>
</template>

<style scoped>.voice-input{width:100%;border:1px solid #ffffff20;border-radius:8px;background:#0a1522;color:#d9e5f3;padding:10px;font-size:13px}</style>
