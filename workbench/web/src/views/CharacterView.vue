<script setup lang="ts">
/** 项目角色资料与音色统一维护，镜内表演继续显式审核采用。 */
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute } from 'vue-router'
import { app, toast } from '../stores/app'
import { fetchCharacters, saveCharacters, type CharacterProfile } from '../utils/characterProfiles'
import { appearanceFields, appearanceDraft, appearancePatch, adoptAppearanceProposal } from '../utils/characterAppearance'
import { characterSectionTarget, characterStateElementId as stateElementId } from '../utils/characterNavigation'
import BatchDiffReview from '../components/BatchDiffReview.vue'
import AppearanceReviewButton from '../components/AppearanceReviewButton.vue'
import { changedGroups, type ReviewItem } from '../utils/reviewDiff'
import VoiceAssetsView from './VoiceAssetsView.vue'
import ActingView from './ActingView.vue'

const route = useRoute()
const characters = ref<CharacterProfile[]>([]), selectedId = ref(''), revision = ref('')
const loading = ref(false), saving = ref(false), error = ref('')
const search = ref('')
const textFields = ['biography', 'bio_language', 'bio_crack', 'bio_pressure', 'bio_address', 'bio_arc', 'sheet_prompt', 'identity_anchor', 'voice'] as const
const actingLabels = [{key: 'personality', label: '性格'}, {key: 'goal', label: '全剧目标'}, {key: 'expression_rules', label: '表达与动作习惯'}, {key: 'relationship', label: '关系中的表现'}, {key: 'arc_stage', label: '弧线与阶段'}]
const bioLabels = [{key: 'bio_language', label: '说话方式'}, {key: 'bio_crack', label: '说话的破绽'}, {key: 'bio_pressure', label: '受压反应'}, {key: 'bio_address', label: '称呼规则'}, {key: 'bio_arc', label: '人物弧光'}]
type Draft = { value: Record<string, any>; original: Record<string, any>; revision: string }
// 切角色、切项目时保留未保存文本；版本仍取开始编辑时的值。
const drafts = reactive<Record<string, Draft>>({})
const draftKey = computed(() => `${app.current}/${selectedId.value}`)
const actor = computed(() => characters.value.find(row => row.id === selectedId.value))
const draft = computed(() => drafts[draftKey.value])
const dirty = computed(() => !!draft.value && JSON.stringify(draft.value.value) !== JSON.stringify(draft.value.original))
const anyDirty = () => Object.values(drafts).some(d => JSON.stringify(d.value) !== JSON.stringify(d.original))
const visibleCharacters = computed(() => characters.value.filter(row => `${row.name} ${row.role || ''}`.includes(search.value)))
const reviewOpen = ref(false), reviewItems = ref<ReviewItem[]>([]), reviewError = ref(''), reviewTitle = ref('')
let reviewProject = '', reviewRevision = ''
let reviewPatches: Record<string, Record<string, unknown>> = {}
const visualRows = computed(() => characters.value.map(row=>({...row,...drafts[`${app.current}/${row.id}`]?.value})))
const staleVisualIds = computed(() => characters.value.filter(row=>{
  const local=drafts[`${app.current}/${row.id}`]
  return local && local.revision!==revision.value
}).map(row=>row.id))
function visualSaved(patches:Record<string,Record<string,unknown>>) {
  for(const [id,patch] of Object.entries(patches)) {
    const local=drafts[`${app.current}/${id}`]
    if(!local) continue
    for(const field of ['appearance','states']) if(patch[field]) {
      const appearance=patch.appearance as Record<string, any> | undefined
      const value=field==='appearance'?appearanceDraft({...local.value.appearance,...appearance,
        sources:{...local.value.appearance.sources,...appearance?.sources},
        proposals:{...local.value.appearance.proposals,...appearance?.proposals}}):patch.states
      local.value[field]=JSON.parse(JSON.stringify(value))
      local.original[field]=JSON.parse(JSON.stringify(value))
    }
  }
}
let requestSeq = 0
async function focusRequestedState() {
  if (loading.value) return
  const id = typeof route.query.character === 'string' ? route.query.character : ''
  if (id && characters.value.some(row=>row.id===id)) selectedId.value=id
  await nextTick()
  const target = characterSectionTarget(route.query.state,route.hash)
  if (target) document.getElementById(target)?.scrollIntoView({behavior:'smooth',block:'center'})
}

function model(row: CharacterProfile) {
  const value: Record<string, any> = {}
  for (const field of textFields) value[field] = row[field] || ''
  value.acting = Object.fromEntries(actingLabels.map(field => [field.key, row.acting?.[field.key] || '']))
  value.appearance = appearanceDraft(row.appearance)
  value.relations = JSON.parse(JSON.stringify(row.relations || []))
  value.states = (row.states || []).map(({visual_status, ...state})=>JSON.parse(JSON.stringify(state)))
  return value
}
function installDraft(row: CharacterProfile, rev: string) {
  const key = `${app.current}/${row.id}`, old = drafts[key]
  const value = model(row)
  if (old && JSON.stringify(old.value) !== JSON.stringify(old.original)) {
    const edited = Object.keys(old.value).filter(field => JSON.stringify(old.value[field]) !== JSON.stringify(old.original[field]))
    // 本页绑定音色只改变音色引用，可以安全更新版本；同一文本字段被外部改动仍保留旧版本报冲突。
    if (edited.every(field => JSON.stringify(value[field]) === JSON.stringify(old.original[field]))) {
      const pending = Object.fromEntries(edited.map(field => [field, old.value[field]]))
      drafts[key] = {value: {...value, ...pending}, original: JSON.parse(JSON.stringify(value)), revision: rev}
    }
    return
  }
  drafts[key] = {value, original: JSON.parse(JSON.stringify(value)), revision: rev}
}
async function load(quiet = false) {
  const project = app.current, seq = ++requestSeq
  if (!quiet) characters.value = []
  error.value = ''
  if (!project) return
  if (!quiet) loading.value = true
  try {
    const result = await fetchCharacters(project)
    if (project !== app.current || seq !== requestSeq) return
    characters.value = result.characters; revision.value = result.revision
    for (const row of result.characters) if (!row.reserved) installDraft(row, result.revision)
    if (!quiet && typeof route.query.character === 'string' && result.characters.some(row => row.id === route.query.character)) selectedId.value = route.query.character
    if (!result.characters.some(row => row.id === selectedId.value)) selectedId.value = result.characters[0]?.id || ''
  } catch (e) { if (seq === requestSeq) error.value = String(e) }
  finally { if (seq === requestSeq) { loading.value = false; await focusRequestedState() } }
}
function save() {
  const project = app.current, id = selectedId.value, current = draft.value
  if (!project || !current || !dirty.value) return
  const patch = Object.fromEntries(Object.entries(current.value).filter(([field, value]) => JSON.stringify(value) !== JSON.stringify(current.original[field])))
  if (patch.appearance) patch.appearance = appearancePatch(current.value.appearance, current.original.appearance)
  const groups = changedGroups(current.original, current.value, {biography:'剧本小传',bio_language:'说话方式',bio_crack:'说话的破绽',bio_pressure:'受压反应',bio_address:'称呼规则',bio_arc:'人物弧光',acting:'演绎方式',appearance:'外观描绘',relations:'人物关系',states:'派生外观',voice:'声音描绘',identity_anchor:'身份外观锚点',sheet_prompt:'人工补充描绘'})
  reviewProject=project; reviewRevision=current.revision
  reviewPatches={[id]:patch}
  reviewItems.value=[{id,title:actor.value?.name || id,groups}]
  reviewTitle.value='审核角色设定'; reviewError.value=''; reviewOpen.value=true
}
async function submitReview(ids:string[]) {
  if (saving.value || !ids.length || reviewProject!==app.current) return
  const project=reviewProject
  saving.value=true; reviewError.value=''
  try {
    const result=await saveCharacters(project,ids.map(id=>({character_id:id,patch:reviewPatches[id]!})),reviewRevision)
    for (const id of result.saved) {
      const key=`${project}/${id}`
      delete drafts[key]
    }
    reviewOpen.value=false
    if (app.current === project) {
      await load(true)
      const incomplete=Object.values(result.visual_validations).filter(check=>!check.ready).length
      const stateIssues=characters.value.filter(c=>result.saved.includes(c.id)).flatMap(c=>(c.states || []).filter(s=>!s.output_asset_ref && s.visual_status?.ready===false))
      toast(`已保存并复检 ${result.saved.length} 个角色${incomplete?`；${incomplete} 个母图设定仍有缺项`:''}${stateIssues.length?`；${stateIssues.length} 条派生仍需修改，请查看派生外观中的提示`:'；派生校验通过'}`,stateIssues.length || incomplete?'info':'ok',6000)
    }
  } catch(e) { reviewError.value=`${String(e)}；审核稿已保留，请核对结果后重试。`; toast('角色审核提交未完成','err') }
  finally {saving.value=false}
}
function resetDraft() {
  if (dirty.value && !confirm('重新载入会放弃当前角色的未保存文本，是否继续？')) return
  delete drafts[draftKey.value]
  void load()
}
function addRelation() {
  const target = characters.value.find(row => !row.reserved && row.id !== selectedId.value)
  if (target && draft.value) draft.value.value.relations.push({to_ref: `@character:${target.id}`, kind: '', note: ''})
}
function beforeUnload(event: BeforeUnloadEvent) {
  if (anyDirty()) { event.preventDefault(); event.returnValue = '' }
}
window.addEventListener('beforeunload', beforeUnload)
onBeforeUnmount(() => window.removeEventListener('beforeunload', beforeUnload))
onBeforeRouteLeave(() => !anyDirty() || confirm('角色设定有未保存文本，离开将丢失这些修改。是否继续？'))
watch(() => app.current, () => { reviewOpen.value=false; void load() }, {immediate: true})
watch(() => [route.query.character, route.query.state, route.hash], () => void focusRequestedState())
</script>

<template>
  <div class="page-wide">
    <header class="mb-5 flex flex-wrap items-center gap-3"><div class="mr-auto"><h1 class="grad-text text-2xl font-black">角色设定</h1><p class="mt-1 text-sm text-slate-400">小传、演绎、外观、关系与音色</p></div><AppearanceReviewButton :project="app.current || ''" :disabled="saving || loading" :rows="characters" :edited-rows="visualRows" :revision="revision" :stale-ids="staleVisualIds" @saved="visualSaved" @changed="load(true)" /></header>
    <p v-if="error" role="alert" class="mb-4 rounded-lg bg-rose-950/50 p-3 text-sm text-rose-200">{{ error }}</p>
    <p v-if="!app.current" class="glass p-8 text-center text-slate-400">请先选择项目</p>
    <p v-else-if="loading" class="glass p-8 text-center text-slate-400">正在读取角色设定…</p>
    <div v-else class="grid items-start gap-5 lg:grid-cols-[210px_minmax(0,1fr)]">
      <aside class="glass space-y-2 p-3 lg:sticky lg:top-4">
        <input v-model="search" class="input mb-2" aria-label="搜索角色" placeholder="搜索角色" />
        <button v-for="row in visibleCharacters" :key="row.id" class="block w-full rounded-xl border p-3 text-left" :class="row.id === selectedId ? 'border-sky-400/60 bg-sky-900/20' : 'border-white/10'" @click="selectedId = row.id">
          <b class="text-sm">{{ row.name }}</b><span class="mt-1 block text-xs text-slate-400">{{ row.reserved ? '画外说话人 · 仅音色' : row.role || '角色资料' }}</span>
        </button>
        <p v-if="characters.length === 1 && characters[0]?.reserved" class="p-2 text-xs leading-6 text-slate-400">暂无角色，请先从剧本提取人物或在素材页建立人物。</p>
      </aside>
      <main v-if="actor" class="min-w-0 space-y-5">
        <div class="flex flex-wrap items-center gap-3">
          <h2 class="text-xl font-bold text-slate-100">{{ actor.name }}</h2>
          <span v-if="dirty && !actor.reserved" class="text-xs text-amber-200">有未保存文本</span>
          <template v-if="!actor.reserved"><button class="btn ml-auto" :disabled="saving || !dirty" @click="save">{{ saving ? '保存中…' : '审核并保存角色' }}</button><button class="btn btn-ghost" :disabled="saving" @click="resetDraft">重新载入</button></template>
        </div>
        <p v-if="actor.reserved" class="glass p-4 text-sm text-slate-300">旁白只绑定声音，不建立人物资料。未绑定时不生成旁白配音。</p>
        <div v-else-if="draft" class="grid items-start gap-4 xl:grid-cols-2">
          <section class="glass space-y-3 p-4"><h3 class="font-bold text-sky-200">剧本小传</h3>
            <textarea v-model="draft.value.biography" class="textarea min-h-36" aria-label="剧本小传" placeholder="角色经历、欲望、冲突与变化…" :disabled="saving" />
            <label v-for="field in bioLabels" :key="field.key" class="block text-xs text-slate-400">{{ field.label }}<textarea v-model="draft.value[field.key]" class="textarea mt-1 min-h-16" rows="2" :disabled="saving" /></label>
          </section>
          <section class="glass space-y-3 p-4"><h3 class="font-bold text-sky-200">演绎方式</h3><p class="text-xs leading-6 text-slate-400">全剧角色基准。镜内目标、知情状态与已采用表演仍按分镜单独维护。</p>
            <label v-for="field in actingLabels" :key="field.key" class="block text-xs text-slate-400">{{ field.label }}<textarea v-model="draft.value.acting[field.key]" class="textarea mt-1 min-h-16" rows="2" :disabled="saving" /></label>
          </section>
          <section class="glass space-y-3 p-4"><h3 class="font-bold text-sky-200">外观描绘</h3>
            <p v-if="actor.visual_status && !actor.visual_status.ready" class="rounded bg-amber-950/30 p-2 text-xs text-amber-200">待确认：{{ actor.visual_status.field_labels.join('、') }}。保存后自动复检，并同步到素材生图。</p>
            <p class="text-xs leading-6 text-slate-400">外观与人物经历分别维护。保留原文数值和单位，未知项可以留空；自然五官与发型细节服从所选画风。异兽按自身结构填写。</p>
            <div v-for="field in appearanceFields" :key="field.key" class="space-y-1">
              <label class="block text-xs text-slate-400">{{ field.label }}<textarea v-model="draft.value.appearance[field.key]" class="textarea mt-1" rows="2" :placeholder="field.hint" :disabled="saving" @input="draft.value.appearance.sources[field.key] = 'authored'" /></label>
              <select v-model="draft.value.appearance.sources[field.key]" class="input text-xs" :aria-label="`${field.label}来源`" :disabled="saving">
                <option value="">历史档案（未标来源）</option><option value="source">原文明示</option><option value="authored">人工采用的设定</option><option value="proposal">待采用建议（不用于生图）</option><option value="unknown">未知（不用于生图）</option>
              </select>
              <div v-if="draft.value.appearance.proposals[field.key] != null && draft.value.appearance.proposals[field.key] !== ''" class="rounded bg-amber-950/30 p-2 text-xs text-amber-200">
                待采用建议：{{ draft.value.appearance.proposals[field.key] }}<button class="btn btn-sm ml-2" :disabled="saving" @click="adoptAppearanceProposal(draft.value.appearance, field.key)">采用此设定</button><p class="mt-1 text-slate-400">采用并保存后才用于正式生图。</p>
              </div>
            </div>
            <label class="block text-xs text-slate-400">身份外观锚点<textarea v-model="draft.value.identity_anchor" class="textarea mt-1" rows="2" :disabled="saving" /></label>
            <label class="block text-xs text-slate-400">人工补充描绘<textarea v-model="draft.value.sheet_prompt" class="textarea mt-1" rows="3" :disabled="saving" placeholder="已采用外观会自动进入生图；这里只填写额外细节" /></label>
          </section>
          <section class="glass space-y-3 p-4"><div class="flex items-center justify-between"><h3 class="font-bold text-sky-200">人物关系</h3><button class="btn btn-sm" :disabled="saving || characters.filter(row => !row.reserved).length < 2" @click="addRelation">添加关系</button></div>
            <article v-for="(relation, index) in draft.value.relations" :key="index" class="space-y-2 rounded-lg border border-white/10 p-3">
              <select v-model="relation.to_ref" class="input" aria-label="关系对象" :disabled="saving"><option v-for="row in characters.filter(row => !row.reserved && row.id !== selectedId)" :key="row.id" :value="`@character:${row.id}`">{{ row.name }}</option></select>
              <input v-model="relation.kind" class="input" aria-label="关系类型" placeholder="关系类型，例如师徒、对手" :disabled="saving" />
              <textarea v-model="relation.note" class="textarea" aria-label="关系描述" placeholder="关系中的矛盾、信任与变化" rows="2" :disabled="saving" />
              <button class="btn btn-sm btn-ghost" :disabled="saving" @click="draft.value.relations.splice(index, 1)">移除关系</button>
            </article>
            <p v-if="!draft.value.relations.length" class="text-xs text-slate-400">尚未登记人物关系。</p>
            <label class="block pt-3 text-xs text-slate-400">声音描绘<textarea v-model="draft.value.voice" class="textarea mt-1" rows="3" placeholder="音高、语速、质感与措辞节奏" :disabled="saving" /></label>
          </section>
          <section v-if="draft.value.states.length" id="states" class="glass space-y-3 p-4 xl:col-span-2"><div class="flex items-center gap-3"><h3 class="mr-auto font-bold text-sky-200">派生外观</h3><button class="btn btn-sm" :disabled="saving || !dirty" @click="save">审核并保存角色</button></div><p class="text-xs text-slate-400">每个状态只保留一个时刻的服装、持物或伤情。修改后点击审核保存，在差异弹窗确认即可；仅动作变化可勾选复用母图。</p>
            <article v-for="state in draft.value.states" :key="state.id" :id="stateElementId(state.id)" class="space-y-2 rounded border p-3" :class="route.query.state===state.id?'border-sky-400 bg-sky-950/20':'border-line'">
              <b class="text-sm">{{ state.label || state.id }}</b>
              <p v-if="!state.output_asset_ref && actor.states?.find(s=>s.id===state.id)?.visual_status?.ready===false" class="text-xs text-amber-200">上次保存校验：{{ [...(actor.states?.find(s=>s.id===state.id)?.visual_status?.field_labels || []), ...(actor.states?.find(s=>s.id===state.id)?.visual_status?.warnings || [])].join('；') }}</p>
              <label class="flex items-center gap-2 text-xs text-slate-300"><input type="checkbox" :checked="state.output_asset_ref === '@character:' + actor.id" :disabled="saving" @change="state.output_asset_ref = ($event.target as HTMLInputElement).checked ? '@character:' + actor.id : ''" />复用母图，动作或心理变化交给分镜表现</label>
              <label class="block text-xs text-slate-400">可见变化<textarea v-model="state.look_diff" class="textarea mt-1" rows="2" :disabled="saving" /></label>
              <label class="block text-xs text-slate-400">目标外观补充<textarea v-model="state.sheet_prompt" class="textarea mt-1" rows="2" :disabled="saving" /></label>
              <label class="block text-xs text-slate-400">对应分集或场景（每行一个）<textarea :value="(state.episodes || []).join('\n')" class="textarea mt-1" rows="2" :disabled="saving" @input="state.episodes = ($event.target as HTMLTextAreaElement).value.split('\n').map(x => x.trim()).filter(Boolean)" /></label>
            </article>
          </section>
        </div>
        <section id="voices" class="space-y-3"><h3 class="text-lg font-bold text-sky-200">音色绑定</h3><VoiceAssetsView :key="app.current || undefined" embedded :selected-character-id="actor.id" :voice-description="draft?.value.voice || ''" @binding-changed="load(true)" /></section>
        <details :open="route.hash === '#performance'" id="performance" v-if="!actor.reserved" class="glass p-4"><summary class="cursor-pointer font-bold text-sky-200">镜头表演 · {{ actor.name }}</summary><p class="my-3 text-xs leading-6 text-slate-400">查看该角色参与的镜头，生成候选并审核采用。角色资料保存不会抹掉已采用表演。</p><ActingView :key="app.current || undefined" embedded :selected-character-id="actor.id" :selected-character-name="actor.name" :profile-revision="revision" /></details>
      </main>
    </div>
  </div>
  <BatchDiffReview v-model:open="reviewOpen" :title="reviewTitle" :items="reviewItems" :busy="saving" :error="reviewError" @confirm="submitReview" />
</template>
