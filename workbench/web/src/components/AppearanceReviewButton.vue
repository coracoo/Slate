<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { fetchVisualReview, saveVisualReview, type CharacterProfile, type OtherVisualAsset } from '../utils/characterProfiles'
import { characterVisualReview, visualReviewDrafts, reviewText, stateSelectionId, selectedVisualPatches, selectedVisualConfirmations, reviewSelectionIds, pendingReviewItems } from '../utils/reviewDiff'
import { appearanceFields } from '../utils/characterAppearance'
import { visualReviewActions, identitySelectionReasons, pendingConfirmationIds } from '../utils/visualReviewActions'
import { buildUnits } from '../api'
import { trackJob } from '../stores/jobs'
import { toast } from '../stores/app'
import BatchDiffReview from './BatchDiffReview.vue'
import type { ReviewItem } from '../utils/reviewDiff'
import type { IdentityChange } from '../utils/characterProfiles'
import TextDiff from './TextDiff.vue'

const props=defineProps<{project:string;disabled?:boolean;rows?:CharacterProfile[];editedRows?:CharacterProfile[];revision?:string;staleIds?:string[];allAssets?:boolean;visibleRefs?:string[]}>()
const emit=defineEmits<{
  (event:'changed'):void
  (event:'saved',patches:Record<string,Record<string,unknown>>):void
}>()
const open=ref(false), busy=ref(false), error=ref(''), rows=ref<CharacterProfile[]>([])
const duplicatesOnly=ref(false)
const detailOwner=ref('')
const initialSelection=ref<string[]>()
const drafts=ref<ReturnType<typeof visualReviewDrafts>>({})
const others=ref<OtherVisualAsset[]>([]), otherDrafts=ref<Record<string,string>>({}), scenes=ref<Array<{ref:string;name:string}>>([])
const sourceEvidence=ref<Awaited<ReturnType<typeof fetchVisualReview>>['evidence']>()
const sourceRecords=ref<Record<string,Record<string,unknown>>>({})
const identities=ref<IdentityChange[]>([]), reuseChanges=ref<ReviewItem[]>([]), issues=ref<string[]>([])
const identityDrafts=ref<Record<string,{action:string;target:string;label:string;difference:string;episodes:string[]}>>({})
const reviewDialog=ref<InstanceType<typeof BatchDiffReview>>()
const identityReasons=computed(()=>identitySelectionReasons(identities.value,identityDrafts.value))
const pendingIdentities=computed(()=>identities.value.filter(item=>identityReasons.value[item.id]))
const otherStates=ref<Record<string,NonNullable<OtherVisualAsset['states']>>>({})
const isIdentity=(id:string)=>id.startsWith('identity-') || id.startsWith('existing-identity-')
function comparisonRecord(record?:Record<string,unknown>) {
  if(!record) return ''
  const fields=['id','name','gender','identity_anchor','appearance','biography','acting','relations','visual_description','description','geometry','layout_note','spatial_limit','action_slots','usage_boundary','states','basis']
  return reviewText(Object.fromEntries(fields.filter(key=>record[key]!=null).map(key=>[key,record[key]])))
}
function relatedEpisodes(id:string) {
  const reference=id.startsWith('@')?id:'@character:'+id
  return (sourceEvidence.value?.episodes || []).filter(e=>['cast_refs','scene_refs','key_asset_refs'].some(key=>Array.isArray(e[key]) && (e[key] as unknown[]).includes(reference)))
}
const title=computed(()=>duplicatesOnly.value?'重复素材对比与合并':props.allAssets?'批量补齐与审核素材设定':'批量补齐与审核外观派生')
const proposed=computed(()=>{
  const result=characterVisualReview(rows.value,drafts.value)
  for(const row of others.value) {
    const after=otherDrafts.value[row.id] ?? row.visual_description
    const groups=after!==row.visual_description?[{label:'视觉描绘',before:row.visual_description,after}]:[]
    if(groups.length) result.patches[row.id]={visual_description:after}
    if(reviewText(otherStates.value[row.id] || [])!==reviewText((row.states || []).map(({visual_status,...state})=>state))) {
      groups.push({label:'派生状态',before:reviewText(row.states),after:reviewText(otherStates.value[row.id])})
      result.patches[row.id]={...(result.patches[row.id] || {}),states:otherStates.value[row.id]}
    }
    result.items.push({id:row.id,title:(row.kind==='scene'?'场景':'道具')+' · '+row.name,groups,
      children:(row.states || []).map(s=>({id:stateSelectionId(row.id,s.id),title:'派生 · '+(s.label || s.id)})),
      note:row.in_use?'当前剧本在用素材':'未引用素材，补齐为可选操作，不影响当前剧本生成。'})
  }
  for(const item of identities.value) {
    result.items.push({...item,groups:[],title:'疑似重复 · '+String(item.record.name || item.record.id),defaultSelected:false,note:item.existing?'可直接勾选；选择处理方案后提交，合并时保留右侧目标，历史快照保留。':`同批 ${item.batch_size} 项关联同一份剧情，确定对应关系后一起采用。`})
    if(identityDrafts.value[item.id]?.action && !identityReasons.value[item.id]) result.patches[item.id]={...identityDrafts.value[item.id]}
  }
  for(const item of reuseChanges.value) {
    result.items.push({...item,id:'reuse:'+item.id,defaultSelected:false})
    result.patches['reuse:'+item.id]={_reuse:true}
  }
  return result
})
const items=computed(()=>{
  if(duplicatesOnly.value) return proposed.value.items.filter(item=>isIdentity(item.id))
  const statuses:Record<string,string|undefined>={}, edited:string[]=[]
  for(const row of [...rows.value,...others.value]) {
    statuses[row.id]=row.visual_status?.review_status
    const patch=proposed.value.patches[row.id]
    if(patch && Object.keys(patch).some(key=>key!=='states')) edited.push(row.id)
    for(const state of row.states || []) {
      const id=stateSelectionId(row.id,state.id)
      statuses[id]=state.visual_status?.review_status
      const {visual_status,...original}=state
      const next=Array.isArray(patch?.states)?patch.states.find((s:{id:string})=>s.id===state.id):undefined
      if(next && reviewText(next)!==reviewText(original)) edited.push(id)
    }
  }
  return pendingReviewItems(proposed.value.items.filter(item=>!isIdentity(item.id)),statuses,edited,detailOwner.value)
    .map(item=>({...item,disabledReason:props.staleIds?.includes(item.id)?'角色资料已有更新，请重新载入后合并当前编辑。':undefined}))
})
const missingIds=computed(()=>[
  ...rows.value.filter(row=>row.visual_status?.missing_fields.length).map(row=>row.id),
  ...others.value.filter(row=>row.visual_status.missing_fields.length).map(row=>row.id),
])
const originalStates=computed(()=>Object.fromEntries([
  ...rows.value.map(row=>[row.id,(row.states || []).map(({visual_status,...state})=>state)]),
  ...others.value.map(row=>[row.id,(row.states || []).map(({visual_status,...state})=>state)]),
]) as Record<string,Array<{id:string}>>)
function chosenPatches(ids:string[]) {return selectedVisualPatches(ids,proposed.value.patches,originalStates.value)}
function actions(ids:string[]) {
  const patches=chosenPatches(ids)
  const statuses:Record<string,boolean>={}
  for(const row of rows.value) {
    statuses[row.id]=row.visual_status?.ready!==false
    for(const state of row.states || []) statuses[stateSelectionId(row.id,state.id)]=!!state.output_asset_ref || state.visual_status?.ready!==false
  }
  for(const row of others.value) {
    statuses[row.id]=row.visual_status.ready
    for(const state of row.states || []) statuses[stateSelectionId(row.id,state.id)]=row.visual_status.ready
  }
  const confirmable=confirmationCandidates(ids,patches)
  return visualReviewActions([...new Set([...ids,...Object.keys(patches)])],patches,missingIds.value.filter(id=>ids.includes(id)),ids.filter(id=>statuses[id]===false),confirmable)
}
function setIdentityAction(ids:string[], action:'reuse'|'new') {
  for(const id of ids) if(identityDrafts.value[id]) identityDrafts.value[id]!.action=action
}
function confirmationCandidates(ids:string[],patches:Record<string,Record<string,unknown>>) {
  const statuses:Record<string,{ready?:boolean;review_status?:string}>={}, changed:string[]=[]
  for(const row of [...rows.value,...others.value]) {
    statuses[row.id]=row.visual_status || {}
    const patch=patches[row.id]
    if(patch && Object.keys(patch).some(key=>key!=='states')) changed.push(row.id)
    for(const state of row.states || []) {
      const id=stateSelectionId(row.id,state.id)
      statuses[id]=state.visual_status || {}
      const next=Array.isArray(patch?.states)?patch.states.find((s:{id:string})=>s.id===state.id):undefined
      const {visual_status,...original}=state
      if(next && reviewText(next)!==reviewText(original) || changed.includes(row.id)) changed.push(id)
    }
  }
  return pendingConfirmationIds(ids,statuses,changed)
}
async function primaryAction(ids:string[]) {
  const selectable=new Set(reviewSelectionIds(items.value))
  ids=ids.filter(id=>selectable.has(id))
  const plan=actions(ids)
  if(plan.kind==='save') await submit(ids)
  else if(plan.kind==='fill') await completeSelected(plan.fillIds)
}
let project='', revision=''
async function readData() {
  const data=await fetchVisualReview(project)
  if(!props.allAssets) {data.other_assets=[];data.identity_changes=[];data.reuse_changes=[]}
  return data
}
async function review(fresh=false, target?:{owner:string;state?:string},duplicateMode=false) {
  if(!props.project || busy.value) return
  project=props.project;busy.value=true;duplicatesOnly.value=duplicateMode;detailOwner.value=target?.owner || ''
  try {
    const data=await readData()
    if(project!==props.project) return
    rows.value=JSON.parse(JSON.stringify(data.characters.filter(c=>!props.visibleRefs || props.visibleRefs.includes('@character:'+c.id))))
    others.value=data.other_assets.filter(a=>!props.visibleRefs || props.visibleRefs.includes(a.id))
    otherDrafts.value=Object.fromEntries(others.value.map(a=>[a.id,a.visual_description]))
    otherStates.value=Object.fromEntries(others.value.map(a=>[a.id,(a.states || []).map(({visual_status,...state})=>JSON.parse(JSON.stringify(state)))]))
    scenes.value=data.scenes
    sourceEvidence.value='evidence' in data?data.evidence:undefined
    sourceRecords.value=('records' in data && data.records) || {}
    identities.value=data.identity_changes || [];reuseChanges.value=data.reuse_changes || [];issues.value=data.issues || []
    identityDrafts.value=Object.fromEntries(identities.value.map(i=>[i.id,{action:'',target:i.matches[0] || '',label:String(i.record.name || ''),difference:'',episodes:[]}]))
    drafts.value=visualReviewDrafts(rows.value,(!fresh && props.editedRows) || rows.value)
    initialSelection.value=target?[target.state?stateSelectionId(target.owner,target.state):target.owner]:duplicatesOnly.value?[]:
      items.value.flatMap(item=>[...(item.defaultSelected!==false?[item.id]:[]),...(item.children || []).map(child=>child.id)])
    revision=data.revision;error.value='';open.value=true
  } catch(e) {toast(e instanceof Error?e.message:'读取外观建议失败','err')}
  finally {busy.value=false}
}
async function submit(ids:string[]) {
  if(busy.value || project!==props.project) return
  const patches=chosenPatches(ids)
  const identityProblems=ids.filter(id=>isIdentity(id) && identityReasons.value[id])
  if(identityProblems.length) {error.value=identityProblems.map(id=>identityReasons.value[id]).join('\n');return}
  const confirmations=selectedVisualConfirmations(confirmationCandidates(ids,patches),items.value)
  const allPatches={...proposed.value.patches}
  const pendingDrafts=JSON.parse(JSON.stringify(drafts.value)) as typeof drafts.value
  const pendingOtherStates=JSON.parse(JSON.stringify(otherStates.value)) as typeof otherStates.value
  if(!Object.keys(patches).length && !Object.keys(confirmations).length) return
  busy.value=true;error.value=''
  try {
    const targets=[...new Set([...Object.keys(patches),...Object.keys(confirmations)])]
    const result=await saveVisualReview(project,targets.map(id=>isIdentity(id)?{id,...identityDrafts.value[id]}:id.startsWith('reuse:')?{id}:{id,patch:patches[id],confirm:confirmations[id]}),revision)
    if(project!==props.project) return
    emit('saved',Object.fromEntries(Object.entries(patches).filter(([id])=>!id.startsWith('@'))))
    emit('changed')
    // 保存后重新读取逐状态校验，仍有问题时留在当前弹窗继续修改。
    let fresh:Awaited<ReturnType<typeof readData>>
    try { fresh=await readData() }
    catch {open.value=false;toast('外观与派生已保存；校验结果读取失败，请刷新查看，无需重复提交','info');return}
    if(project!==props.project) return
    if(fresh.revision!==result.revision) {open.value=false;toast('本次修改已保存，资料随后有新更新，请重新打开审核查看','info');return}
    if(result.saved.some(id=>isIdentity(id) || id.startsWith('reuse:'))) {
      const oldDrafts=drafts.value, oldOtherDrafts=otherDrafts.value, oldOtherStates=otherStates.value
      busy.value=false
      await review(true,undefined,duplicatesOnly.value)
      for(const id of Object.keys(oldDrafts)) if(!result.saved.includes(id) && drafts.value[id] && allPatches[id]) drafts.value[id]=oldDrafts[id]!
      for(const id of Object.keys(oldOtherDrafts)) if(!result.saved.includes(id) && id in otherDrafts.value && allPatches[id]) otherDrafts.value[id]=oldOtherDrafts[id]!
      for(const id of Object.keys(oldOtherStates)) if(!result.saved.includes(id) && id in otherStates.value && allPatches[id]) otherStates.value[id]=oldOtherStates[id]!
      for(const id of result.saved) {
        if(drafts.value[id] && pendingDrafts[id]) {
          if(!ids.includes(id)) drafts.value[id]!.appearance=pendingDrafts[id]!.appearance
          drafts.value[id]!.states=drafts.value[id]!.states.map(state=>!ids.includes(stateSelectionId(id,state.id))?(pendingDrafts[id]!.states.find(s=>s.id===state.id) || state):state)
        }
        if(id in otherStates.value) {
          if(!ids.includes(id)) otherDrafts.value[id]=oldOtherDrafts[id]!
          otherStates.value[id]=otherStates.value[id]!.map(state=>!ids.includes(stateSelectionId(id,state.id))?(pendingOtherStates[id]?.find(s=>s.id===state.id) || state):state)
        }
      }
      toast('已采用所选设定与对应关系，关联引用已同步','ok')
      return
    }
    const problems=result.confirmations.filter(r=>!r.ready).map(r=>r.name+'：'+r.warnings.join('；'))
    const confirmed=result.confirmations.filter(r=>r.ready).length
      for(const row of fresh.characters) if(result.saved.includes(row.id)) {
        rows.value[rows.value.findIndex(c=>c.id===row.id)]=row
        drafts.value[row.id]=visualReviewDrafts([row])[row.id]!
        if(!ids.includes(row.id) && pendingDrafts[row.id]) drafts.value[row.id]!.appearance=pendingDrafts[row.id]!.appearance
        drafts.value[row.id]!.states=drafts.value[row.id]!.states.map(state=>!ids.includes(stateSelectionId(row.id,state.id))?(pendingDrafts[row.id]?.states.find(s=>s.id===state.id) || state):state)
      }
      for(const row of fresh.other_assets) if(result.saved.includes(row.id)) {
        others.value[others.value.findIndex(a=>a.id===row.id)]=row
        if(ids.includes(row.id)) otherDrafts.value[row.id]=row.visual_description
        otherStates.value[row.id]=(row.states || []).map(({visual_status,...state})=>!ids.includes(stateSelectionId(row.id,state.id))?(pendingOtherStates[row.id]?.find(s=>s.id===state.id) || state):state)
      }
      revision=fresh.revision
    if(problems.length) {
      error.value=`已确认 ${confirmed} 项；以下项目尚未通过：\n`+problems.join('\n')
      toast(`已确认 ${confirmed} 项，其余问题保留在当前弹窗`,'info')
    } else {
      if(Object.keys(proposed.value.patches).some(id=>!id.startsWith('reuse:') && !isIdentity(id))) error.value='所选修改已保存；其他未提交的编辑与建议仍留在弹窗中。'
      else open.value=false
      toast('已确认 '+confirmed+' 项设定，提示词已同步（未生成图片）'+(pendingIdentities.value.length?`；${pendingIdentities.value.length} 组疑似重复可在「对比合并重复素材」处理`:''),'ok')
    }
  } catch(e) {error.value=e instanceof Error?e.message:'提交失败，审核稿已保留';toast(error.value,'err')}
  finally {busy.value=false}
}
watch(()=>props.project,()=>{open.value=false})
async function completeSelected(ids:string[]) {
  if(busy.value || !ids.length || project!==props.project) return
  if(ids.some(id=>proposed.value.patches[id])) {error.value='当前选择有未提交修改或待采用建议，请先确认提交，再补齐剩余空缺。';return}
  const targetProject=project
  const preservedDrafts=JSON.parse(JSON.stringify(drafts.value)), preservedOthers={...otherDrafts.value}, preservedStates=JSON.parse(JSON.stringify(otherStates.value))
  busy.value=true;error.value=''
  try {
    const response=await buildUnits({project:targetProject,stage:'entity',asset_refs:ids.map(id=>id.startsWith('@')?id:'@character:'+id)})
    const job=await trackJob(response.id,'批量补齐素材设定')
    if(props.project!==targetProject) return
    emit('changed')
    busy.value=false
    await review(true)
    for(const id of Object.keys(preservedDrafts)) if(!ids.includes(id) && drafts.value[id]) drafts.value[id]=preservedDrafts[id]
    for(const id of Object.keys(preservedOthers)) if(!ids.includes(id) && id in otherDrafts.value) otherDrafts.value[id]=preservedOthers[id]!
    for(const id of Object.keys(preservedStates)) if(!ids.includes(id) && id in otherStates.value) otherStates.value[id]=preservedStates[id]
    if(!job.success) error.value=job.err || '部分补齐失败，成功内容已保留；再次补齐只处理剩余空缺。'
    toast(job.success?'补齐结束，请在当前弹窗核对设定与待采用建议':'补齐未全部完成，请查看任务日志与剩余缺项',job.success?'ok':'err',6000)
  } catch(e) {error.value=String(e);toast(error.value,'err')}
  finally {busy.value=false}
}
defineExpose({open:review,openTarget:(owner:string,state?:string)=>review(true,{owner,state})})
</script>
<template>
  <button class="btn" :disabled="disabled || busy || !props.project" @click="review()">{{busy?'处理中…':allAssets?'批量补齐与审核素材设定':'批量补齐与审核外观派生'}}</button>
  <button v-if="allAssets" class="btn btn-ghost" :disabled="disabled || busy || !props.project" @click="review(true,undefined,true)">对比合并重复素材</button>
  <BatchDiffReview ref="reviewDialog" v-model:open="open" :title="title" :items="items" :initial-selection="initialSelection" :busy="busy" :error="error" @confirm="primaryAction">
    <template #empty>当前范围没有待审核设定。已确认内容已保存，可从素材详情编辑。</template>
    <template #notice>
      <p v-if="pendingIdentities.length && !duplicatesOnly" class="text-sm text-amber-200">{{pendingIdentities.length}} 组疑似重复请从「对比合并重复素材」处理，不影响设定确认。</p>
      <details v-if="issues.length" class="text-sm text-amber-200"><summary>仍需核对的历史关联（{{issues.length}}）</summary><p v-for="issue in issues" :key="issue">{{issue}}</p></details>
    </template>
    <template #actions="{selectedIds}">
      <div v-if="duplicatesOnly && selectedIds.length" class="mb-3 flex flex-wrap items-center gap-2 text-sm"><span>所选 {{selectedIds.length}} 组：</span><button class="btn btn-sm" :disabled="busy" @click="setIdentityAction(selectedIds,'reuse')">合并到各自右侧目标</button><button class="btn btn-ghost btn-sm" :disabled="busy" @click="setIdentityAction(selectedIds,'new')">保留为独立素材</button><span class="text-amber-200">{{selectedIds.filter(id=>identityReasons[id]).length}} 组未指定方案</span></div>
      <p v-if="!duplicatesOnly" class="mb-3 text-sm text-slate-300">{{actions(selectedIds).confirmIds.length}} 项待确认 · {{actions(selectedIds).fillIds.length}} 项待补齐 · {{actions(selectedIds).attentionIds.length}} 项需核对。已确认且未修改的设定无需再次提交；确认设定不生成图片。</p>
      <p v-if="pendingIdentities.length" class="mb-3 text-sm text-amber-200">{{duplicatesOnly?'逐组对比，选择合并、保留独立或转为派生，再勾选提交。':'疑似重复暂不合并，不影响本次设定确认。'}}</p>
      <p v-if="actions(selectedIds).attentionIds.length" class="mb-3 text-sm text-amber-200">派生关联或前后动作冲突须在条目中修改后保存；补空缺不会改写这些已有内容。</p>
    </template>
    <template #submit="{selectedIds}">
      <button v-if="actions(selectedIds).saveIds.length && actions(selectedIds).fillIds.length" class="btn btn-ghost" :disabled="busy" @click="completeSelected(actions(selectedIds).fillIds)">补齐其余缺项（{{actions(selectedIds).fillIds.length}} 项）</button>
      <button class="btn" :disabled="busy || actions(selectedIds).kind==='none'" @click="primaryAction(selectedIds)">{{busy?'处理中…':duplicatesOnly?(actions(selectedIds).kind==='none'?'请选择处理方式并勾选':`应用所选方案（${actions(selectedIds).saveIds.length} 组）`):actions(selectedIds).label}}</button>
    </template>
    <template #editor="{item,disabled:locked}">
      <section v-if="identityDrafts[item.id]" class="mb-3 space-y-3 rounded border border-sky-500/40 p-3">
        <label class="block">处理方案<select v-model="identityDrafts[item.id]!.action" class="input mt-1" :disabled="locked"><option value="">请选择（可先勾选）</option><option value="reuse">{{identities.find(i=>i.id===item.id)?.existing?'合并到右侧素材，保留右侧设定':'复用右侧素材，采用本次设定'}}</option><option value="new">保留为独立素材</option><option value="derived">作为右侧素材的派生状态</option></select></label>
        <label class="block">右侧对比素材<select v-model="identityDrafts[item.id]!.target" class="input mt-1" :disabled="locked"><option v-for="target in identities.find(i=>i.id===item.id)?.matches" :key="target" :value="target">{{sourceRecords[target]?.name || target}} · {{target}}</option></select></label>
        <TextDiff :before="comparisonRecord(identities.find(i=>i.id===item.id)?.record)" :after="comparisonRecord(sourceRecords[identityDrafts[item.id]!.target])" before-label="左侧 · 待处理档案" after-label="右侧 · 合并后保留的目标" />
        <div v-if="identityDrafts[item.id]!.action==='derived'" class="space-y-2"><label class="block">派生名称<input v-model="identityDrafts[item.id]!.label" class="input" :disabled="locked" /></label><label class="block">可见差异<textarea v-model="identityDrafts[item.id]!.difference" class="textarea" :disabled="locked" /></label></div>
        <label v-if="identityDrafts[item.id]!.action==='derived'" class="block">对应分集或场景（每行一个）<textarea :value="identityDrafts[item.id]!.episodes.join('\n')" class="textarea mt-1" rows="2" :disabled="locked" @input="identityDrafts[item.id]!.episodes=($event.target as HTMLTextAreaElement).value.split('\n').map(x=>x.trim()).filter(Boolean)" /></label>
      </section>
      <details v-if="!isIdentity(item.id) && !item.id.startsWith('reuse:')" class="mb-3 rounded border border-white/15 p-3 text-sm">
        <summary class="cursor-pointer text-sky-200">查看现有设定与相关剧本依据</summary>
        <pre class="mt-2 max-h-64 overflow-auto whitespace-pre-wrap">{{reviewText(sourceRecords[item.id.startsWith('@')?item.id:'@character:'+item.id] || rows.find(r=>r.id===item.id))}}</pre>
        <details v-for="episode in relatedEpisodes(item.id)" :key="String(episode.id)" class="mt-2"><summary>{{episode.id}} · {{episode.title || '分集内容'}}</summary><pre class="max-h-72 overflow-auto whitespace-pre-wrap">{{reviewText(episode)}}</pre></details>
        <p v-if="allAssets && !relatedEpisodes(item.id).length" class="mt-2 text-amber-200">当前分集未显式引用此素材；不会假定存在剧情依据。</p>
      </details>
      <section v-if="drafts[item.id]" class="mb-4 space-y-2">
        <p v-if="rows.find(r=>r.id===item.id)?.visual_status?.review_status==='confirmed'" class="text-sm text-emerald-200">母素材设定已确认；仅修改后需要提交。</p>
        <h3 class="font-semibold text-sky-200">母图外观</h3>
        <div class="grid gap-3 md:grid-cols-2">
          <label v-for="field in appearanceFields.filter(f=>rows.find(r=>r.id===item.id)?.visual_status?.missing_fields.includes(f.key) || rows.find(r=>r.id===item.id)?.visual_status?.pending_fields.includes(f.key) || drafts[item.id]!.appearance[f.key])" :key="field.key" class="text-xs text-slate-300">{{field.label}}
            <textarea v-model="drafts[item.id]!.appearance[field.key]" class="textarea mt-1" rows="2" :placeholder="field.hint" :disabled="locked" @input="drafts[item.id]!.appearance.sources[field.key]='authored'" />
          </label>
        </div>
      </section>
      <label v-if="item.id in otherDrafts" class="mb-4 block text-sm text-slate-300">视觉描绘<textarea v-model="otherDrafts[item.id]" class="textarea mt-1" rows="4" :disabled="locked" placeholder="填写形状、材质、颜色或空间布局等可见细节" /></label>
      <section v-if="otherStates[item.id]?.length" class="mb-4 space-y-3"><article v-for="state in otherStates[item.id]" :key="state.id" class="rounded border border-white/15 p-3"><label class="block text-sm">派生名称<input v-model="state.label" class="input mt-1" :disabled="locked" /></label><label class="mt-2 block text-sm">可见差异<textarea v-model="state.look_diff" class="textarea mt-1" :disabled="locked" /></label><label class="mt-2 block text-sm">对应分集或场景（每行一个）<textarea :value="(state.episodes || []).join('\n')" class="textarea mt-1" rows="2" :disabled="locked" @input="state.episodes=($event.target as HTMLTextAreaElement).value.split('\n').map(x=>x.trim()).filter(Boolean)" /></label></article></section>
      <section v-if="drafts[item.id]?.states.length" class="mb-4 space-y-3">
        <p class="text-xs text-slate-400">全选包含全部派生；确认保留当前图片来源，未选项不受影响。</p>
        <article v-for="state in drafts[item.id]!.states" :key="state.id" :data-review-id="stateSelectionId(item.id,state.id)" class="space-y-2 rounded-lg border border-white/15 p-3">
          <h3 class="font-semibold text-sky-200">派生 · {{state.label || state.id}}</h3>
          <p v-if="rows.find(r=>r.id===item.id)?.states?.find(s=>s.id===state.id)?.visual_status?.review_status==='confirmed'" class="text-sm text-emerald-200">派生设定已确认；无需重复确认。</p>
          <p v-if="!state.output_asset_ref && rows.find(r=>r.id===item.id)?.states?.find(s=>s.id===state.id)?.visual_status?.warnings.length" class="text-xs text-amber-200">上次校验：{{rows.find(r=>r.id===item.id)?.states?.find(s=>s.id===state.id)?.visual_status?.warnings.join('；')}}</p>
          <label class="block text-sm">图片来源<select class="input mt-1" :value="state.output_asset_ref || ''" :disabled="locked" @change="state.output_asset_ref=($event.target as HTMLSelectElement).value"><option value="">独立派生图（按可见变化生成）</option><option :value="'@character:'+item.id">复用母图（动作交给分镜）</option><option v-if="state.output_asset_ref && state.output_asset_ref!=='@character:'+item.id" :value="state.output_asset_ref">{{state.output_asset_ref}}</option></select></label>
          <div class="grid gap-3 md:grid-cols-2">
            <label class="text-xs text-slate-300">可见变化<textarea v-model="state.look_diff" class="textarea mt-1" rows="3" :disabled="locked" /></label>
            <label class="text-xs text-slate-300">外观补充<textarea v-model="state.sheet_prompt" class="textarea mt-1" rows="3" :disabled="locked" /></label>
          </div>
          <label class="block text-xs text-slate-300">对应分集或场景（每行一个）<textarea :value="(state.episodes || []).join('\n')" class="textarea mt-1" rows="2" :disabled="locked" @input="state.episodes=($event.target as HTMLTextAreaElement).value.split('\n').map(x=>x.trim()).filter(Boolean)" /></label>
          <label v-if="scenes.length" class="block text-xs text-slate-300">选择现有场景（填入后可在上框移除旧关联）<select class="input mt-1" :disabled="locked" @change="state.episodes=[...new Set([...(state.episodes || []),($event.target as HTMLSelectElement).value])];($event.target as HTMLSelectElement).value=''">
            <option value="">请选择场景</option><option v-for="scene in scenes" :key="scene.ref" :value="scene.ref">{{scene.name}} · {{scene.ref}}</option>
          </select></label>
        </article>
      </section>
    </template>
  </BatchDiffReview>
</template>
