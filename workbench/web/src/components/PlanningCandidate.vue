<script setup lang="ts">
import { onUnmounted, ref, watch } from 'vue'
import { buildUnits, editUnits, fetchPlanningCandidate, type PlanningCandidate } from '../api'
import { trackJob, onJobDone } from '../stores/jobs'
import { toast } from '../stores/app'
import BatchDiffReview from './BatchDiffReview.vue'
import type { ReviewItem } from '../utils/reviewDiff'

const props=defineProps<{project:string;revision:number;disabled?:boolean}>()
const emit=defineEmits<{(event:'changed'):void;(event:'busy-change',value:boolean):void}>()
const candidate=ref<PlanningCandidate|null>(null),loading=ref(false),busy=ref(false),error=ref('')
const open=ref(false),items=ref<ReviewItem[]>([]),reviewError=ref('')
let sequence=0,reviewSequence=0,reviewed:{project:string;id:string;revision:string}|null=null
function setBusy(value:boolean) {busy.value=value;emit('busy-change',value)}
async function load() {
  const project=props.project,request=++sequence
  if(!project) {candidate.value=null;loading.value=false;return}
  loading.value=true;error.value=''
  try {
    const result=await fetchPlanningCandidate(project)
    if(request===sequence && project===props.project) candidate.value=result.candidate
  } catch(e) {if(request===sequence) error.value=e instanceof Error?e.message:'读取工作候选失败'}
  finally {if(request===sequence) loading.value=false}
}
async function review() {
  const project=props.project,id=candidate.value?.id,request=++reviewSequence
  if(!project || !id || !candidate.value?.can_adopt || busy.value) return
  setBusy(true);reviewError.value=''
  try {
    const result=await fetchPlanningCandidate(project,id)
    if(request!==reviewSequence || project!==props.project) return
    if(!result.candidate?.can_adopt || result.candidate.status!=='ready') throw new Error('工作候选尚未完成或已变化，请重新读取')
    if(!result.revision || !Array.isArray(result.groups)) throw new Error('候选差异或当前规划基线缺失，请重新读取')
    reviewed={project,id,revision:result.revision}
    items.value=[{id,title:result.candidate.label || '规划工作候选',groups:result.groups}]
    open.value=true
  } catch(e) {if(request===reviewSequence) error.value=e instanceof Error?e.message:'读取候选差异失败'}
  finally {setBusy(false)}
}
async function adopt(ids:string[]) {
  const target=reviewed
  if(!target || !ids.includes(target.id) || target.project!==props.project || busy.value || props.disabled) return
  setBusy(true);reviewError.value=''
  try {
    const result=await editUnits({project:target.project,kind:'adopt-plan',id:target.id,revision:target.revision})
    if(!result.ok) throw new Error(result.err || '采用失败')
    if(target.project!==props.project) return
    open.value=false;emit('changed');await load()
    toast('已采用规划工作候选，请核对规划后确认','ok')
  } catch(e) {if(target.project===props.project) reviewError.value=e instanceof Error?e.message:'采用失败，工作候选已保留'}
  finally {setBusy(false)}
}
async function resume() {
  const project=props.project,id=candidate.value?.id
  if(!project || !id || !candidate.value?.can_resume || busy.value || props.disabled) return
  setBusy(true);error.value=''
  try {
    const result=await buildUnits({project,stage:'replan',planning_version:id})
    const job=await trackJob(result.id,'继续生成规划工作候选')
    if(!job.success) throw new Error(job.err || '候选生成失败')
    if(project===props.project) {emit('changed');await load()}
  } catch(e) {if(project===props.project) error.value=e instanceof Error?e.message:'继续生成失败'}
  finally {setBusy(false)}
  if(project===props.project && candidate.value?.can_adopt) await review()
}
async function reviewCurrent() {await load();if(candidate.value?.can_adopt) await review()}
watch(()=>props.project,()=>{reviewSequence++;open.value=false;reviewed=null;candidate.value=null;void load()},{immediate:true})
watch(()=>props.revision,()=>void load())
const unsubscribe=onJobDone(()=>void load())
onUnmounted(()=>{sequence++;reviewSequence++;unsubscribe()})
defineExpose({reviewCurrent})
</script>

<template>
  <section v-if="candidate || error" class="glass space-y-3 p-4" aria-label="规划工作候选">
    <div class="flex flex-wrap items-center gap-3"><h2 class="mr-auto font-bold text-sky-200">规划工作候选</h2><button class="btn btn-ghost btn-sm" :disabled="loading || busy" @click="load">重新读取</button></div>
    <p v-if="error" role="alert" class="text-sm text-rose-200">{{error}}</p>
    <template v-if="candidate">
      <p class="text-sm text-slate-300">{{candidate.label}} · {{candidate.created_at}} · {{({generating:'生成中',ready:'待审核采用',failed:'生成失败，可继续',applied:'已采用'} as Record<string,string>)[candidate.status]}}</p>
      <p v-if="candidate.error" class="text-sm text-amber-200">{{candidate.error}}</p>
      <p v-if="candidate.status==='ready' && !candidate.can_adopt" class="text-sm text-amber-200">当前规划已变化，此候选不能采用，请按最新规划重新生成。</p>
      <p v-if="candidate.status!=='applied'" class="text-sm text-slate-300">审核采用后更新当前规划，已有分集正文保留。</p>
      <div class="flex flex-wrap gap-3"><button v-if="candidate.can_adopt" class="btn" :disabled="disabled || busy || loading" @click="review">查看差异并采用</button><button v-if="candidate.can_resume" class="btn btn-ghost" :disabled="disabled || busy || loading" @click="resume">继续生成</button></div>
    </template>
  </section>
  <BatchDiffReview v-model:open="open" title="审核规划工作候选" :items="items" :busy="busy" :error="reviewError" submit-label="确认采用" @confirm="adopt" />
</template>
