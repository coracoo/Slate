<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { getJSON, postJSON, type Episode } from '../api'
import { toast } from '../stores/app'
import { onJobDone, trackJob } from '../stores/jobs'
import TextDiff from './TextDiff.vue'
import BatchDiffReview from './BatchDiffReview.vue'
import type { ReviewItem } from '../utils/reviewDiff'

type Repair = { id: string; status: string; before: string; proposed?: string; error?: string; automatic: boolean; created_at: string }
type Item = { episode: string; title: string; status: string; before: string; after: string; error: string; inactive_reason?: string; validation_rechecked?: boolean; failure_kind?: string; repairs?: Repair[] }
type Batch = { id: string; status: string; created_at: string; items: Item[] }
const props = defineProps<{ project: string; episodes: Episode[]; canGenerate: boolean; disabled?: boolean }>()
const emit = defineEmits<{ (event: 'changed'): void; (event: 'busy-change', value: boolean): void; (event: 'prepare'): void }>()
const selected = ref<string[]>([]), instructions = ref('')
const batches = ref<Batch[]>([]), batchId = ref(''), episodeId = ref(''), busy = ref(false)
const showHistory=ref(false)
const repairInstructions = ref(''), repairScope = ref('current')
const reviewOpen=ref(false), reviewItems=ref<ReviewItem[]>([]), reviewError=ref('')
let reviewProject='', reviewBatch='', reviewed:Record<string,{before:string;after:string}>={}
const current = computed(() => batches.value.find(b => b.id === batchId.value))
const row = computed(() => current.value?.items.find(i => i.episode === episodeId.value))
const running = computed(() => batches.value.some(b => ['queued','running'].includes(b.status)))
const repairable = (item:Item) => item.status==='failed' && !!item.after && (item.failure_kind==='validation' || item.error==='候选涉及未登记实体或规划冲突，请修改角色/场景设定后再生成')
const repairEpisodes = computed(() => repairScope.value==='batch'
  ? current.value?.items.filter(repairable).map(i=>i.episode) || []
  : row.value && repairable(row.value) ? [row.value.episode] : [])
const stateLabel: Record<string,string> = {queued:'排队中', running:'生成中', ready:'待审核', failed:'失败', partial:'部分完成', adopted:'已采用', stale:'历史候选 · 不可采用'}
let sequence = 0
async function load() {
  const project = props.project, seq = ++sequence
  if (!project) return
  try {
    const result = await getJSON<{batches:Batch[]}>(`/api/script/drafts?project=${encodeURIComponent(project)}&history=${showHistory.value?1:0}`)
    if (project !== props.project || seq !== sequence) return
    batches.value = result.batches
    if (!batches.value.some(b => b.id === batchId.value)) batchId.value = batches.value[0]?.id || ''
    if (!current.value?.items.some(i => i.episode === episodeId.value)) episodeId.value = current.value?.items[0]?.episode || ''
  } catch(e) { if (seq === sequence) toast(e instanceof Error ? e.message : '读取候选失败','err') }
}
function chooseBatch() { episodeId.value=current.value?.items[0]?.episode || '' }
function toggle(id:string) { selected.value=selected.value.includes(id)?selected.value.filter(e=>e!==id):[...selected.value,id] }
function setBusy(value:boolean) { busy.value=value;emit('busy-change',value) }
async function generate() {
  if (busy.value || props.disabled || !props.canGenerate || !selected.value.length || running.value) return
  const project = props.project
  setBusy(true)
  try {
    const result=await postJSON<{id:number;batch:string}>('/api/script/drafts/generate',{project,episodes:selected.value,instructions:instructions.value})
    if(project===props.project) {batchId.value=result.batch;await load()}
    await trackJob(result.id,'批量正文候选')
    if(project===props.project) await load()
  } catch(e) {toast(e instanceof Error?e.message:'生成候选失败','err')}
  finally {setBusy(false)}
}
function openReview() {
  if(busy.value || props.disabled || !current.value || ['queued','running'].includes(current.value.status)) return
  reviewProject=props.project;reviewBatch=batchId.value;reviewed={}
  reviewItems.value=current.value.items.filter(item=>item.status==='ready').map(item=>{
    reviewed[item.episode]={before:item.before,after:item.after}
    return {id:item.episode,title:`${item.episode} · ${item.title || '正文'}`,groups:[{label:'剧本正文',before:item.before,after:item.after}]}
  })
  reviewError.value='';reviewOpen.value=true
}
async function adopt(ids:string[]) {
  if(busy.value || props.disabled || !ids.length || reviewProject!==props.project) return
  const project=reviewProject
  setBusy(true)
  try {
    const result=await postJSON<{adopted:string[];warnings:string[]}>('/api/script/drafts/adopt',{project,batch:reviewBatch,episodes:ids,reviewed:Object.fromEntries(ids.map(id=>[id,reviewed[id]]))})
    reviewOpen.value=false
    toast(`已采用 ${result.adopted.length} 集，旧稿已存版本`,'ok')
    for(const warning of result.warnings) toast(warning,'info')
    if(project===props.project){await load();emit('changed')}
  } catch(e) {reviewError.value=e instanceof Error?e.message:'采用失败，候选已保留';toast(reviewError.value,'err')}
  finally{setBusy(false)}
}
async function repair() {
  if(busy.value || props.disabled || running.value || !current.value || !repairEpisodes.value.length) return
  const project=props.project, batch=batchId.value
  setBusy(true)
  try {
    const result=await postJSON<{id:number}>('/api/script/drafts/repair',{
      project,batch,episodes:repairEpisodes.value,instructions:repairInstructions.value})
    if(project===props.project) await load()
    await trackJob(result.id,'正文修正')
  } catch(e) {toast(e instanceof Error?e.message:'修正未完成，原稿已保留','err')}
  finally {
    if(project===props.project) await load()
    setBusy(false)
  }
}
watch([batchId,episodeId],()=>{repairInstructions.value='';repairScope.value='current'})
watch(showHistory,()=>void load())
watch(()=>props.project,()=>{reviewOpen.value=false;sequence++;batches.value=[];batchId.value='';episodeId.value='';selected.value=[];instructions.value='';void load()},{immediate:true})
watch(()=>props.episodes,()=>{selected.value=selected.value.filter(e=>props.episodes.some(r=>r.id===e))})
const unsubscribe=onJobDone(()=>void load())
onUnmounted(()=>{sequence++;unsubscribe()})
</script>

<template>
  <section class="space-y-4" aria-label="正文批量生成与审核">
    <div class="grid gap-4 lg:grid-cols-[minmax(220px,1fr)_2fr]">
      <div>
        <div class="mb-2 flex flex-wrap gap-3 text-sm"><b>生成范围</b><button class="text-sky-200" @click="selected=episodes.map(e=>e.id)">全选</button><button class="text-sky-200" @click="selected=episodes.filter(e=>!e.text?.trim()).map(e=>e.id)">只选缺稿</button><button class="text-sky-200" @click="selected=[]">清空</button></div>
        <div class="flex max-h-36 flex-wrap gap-2 overflow-auto"><label v-for="ep in episodes" :key="ep.id" class="rounded bg-white/5 px-2 py-1 text-sm"><input type="checkbox" :checked="selected.includes(ep.id)" @change="toggle(ep.id)" /> {{ep.id}}</label></div>
      </div>
      <div><label class="block text-sm text-slate-200">生成 / 修改要求<textarea v-model="instructions" class="textarea mt-2 w-full" rows="3" maxlength="8000" placeholder="空白时按分集规划扩写；已有正文会作为改写依据。"></textarea></label>
        <button class="btn mt-2" :disabled="busy || disabled || !canGenerate || !selected.length || running" @click="generate">{{busy || running ? '任务处理中…' : `批量生成正文候选（${selected.length} 集）`}}</button>
        <span class="ml-3 text-xs text-slate-300">文本缺口自动修正一次</span>
        <button v-if="!canGenerate" class="ml-3 text-sm text-sky-200 underline" @click="emit('prepare')">完成规划准备</button>
      </div>
    </div>
    <label class="flex items-center gap-2 text-sm text-slate-300"><input v-model="showHistory" type="checkbox" /> 查看历史候选</label>
    <p v-if="!batches.length" class="text-sm text-slate-400">当前没有待处理候选。已采用的正文在分集正文中，旧稿可通过剧本历史查看。</p>
    <div v-if="batches.length" class="border-t border-white/10 pt-4">
      <div class="mb-3 flex flex-wrap items-center gap-3"><select v-model="batchId" class="input flex-1" aria-label="正文候选批次" @change="chooseBatch"><option v-for="batch in batches" :key="batch.id" :value="batch.id">{{batch.created_at}} · {{stateLabel[batch.status]}} · {{batch.items.length}} 集</option></select><button class="btn" :disabled="busy || disabled || !current?.items.some(i=>i.status==='ready') || ['queued','running'].includes(current?.status || '')" @click="openReview">批量审核正文</button><button class="btn btn-ghost" @click="load">刷新</button></div>
      <div class="mb-3 flex flex-wrap gap-2"><button v-for="item in current?.items" :key="item.episode" class="rounded-lg border px-2 py-1 text-sm" :class="item.episode===episodeId?'border-sky-400 bg-sky-400/10':'border-white/10'" @click="episodeId=item.episode">{{item.episode}} · {{stateLabel[item.status]}}</button></div>
      <p v-if="row?.error" class="mb-3 text-sm text-red-200">{{row.error}}</p>
      <p v-if="row?.inactive_reason" class="mb-3 text-sm text-amber-200">{{row.inactive_reason}}，此候选仅供查看。</p>
      <div v-if="row && repairable(row)" class="mb-3 space-y-2 rounded-lg border border-amber-400/30 bg-amber-400/5 p-3">
        <label class="block text-sm text-slate-200">修正要求（可留空）<textarea v-model="repairInstructions" class="textarea mt-2 w-full" rows="2" maxlength="8000" placeholder="按现有设定修正署名和引用，保留剧情。涉及新人物或剧情变更时，请说明处理方式。"></textarea></label>
        <div class="flex flex-wrap items-center gap-3"><select v-model="repairScope" class="input" aria-label="正文修正范围"><option value="current">当前集</option><option value="batch">本批未通过稿</option></select><button class="btn" :disabled="busy || disabled || running || !repairEpisodes.length" @click="repair">修正并继续（{{repairEpisodes.length}} 集）</button><span class="text-xs text-slate-300">保留原稿，修正后待审核</span></div>
      </div>
      <p v-if="row?.validation_rechecked && row.status==='ready'" class="mb-3 text-sm text-sky-200">旧校验误报已修正，原稿保留，等待审核。</p>
      <details v-if="row?.repairs?.length" class="mb-3 rounded-lg border border-white/10 p-3"><summary class="cursor-pointer text-sm text-sky-200">修正记录（{{row.repairs.length}} 次）</summary><div class="max-h-96 space-y-4 overflow-auto pt-3"><section v-for="attempt in row.repairs" :key="attempt.id"><p class="mb-2 text-sm text-slate-200">{{attempt.automatic?'自动修正':'确认后修正'}} · {{attempt.created_at}} · {{attempt.status==='done'?'已通过':attempt.status==='running'?'处理中':'未通过'}}</p><p v-if="attempt.error" class="mb-2 text-sm text-red-200">{{attempt.error}}</p><TextDiff v-if="attempt.proposed" :before="attempt.before" :after="attempt.proposed" /></section></div></details>
      <TextDiff v-if="row" :before="row.before" :after="row.after" />
    </div>
  </section>
  <BatchDiffReview v-model:open="reviewOpen" title="批量审核剧本正文" :items="reviewItems" :busy="busy" :error="reviewError" submit-label="确认采用" @confirm="adopt" />
</template>
