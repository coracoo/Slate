<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { createChatGPTRun, fetchChatGPTRun, controlChatGPTRun } from '../api'
import { toChatGPTRunView, type ChatGPTRun } from '../utils/chatgptRun'
const props=withDefaults(defineProps<{project:string;jobIds:string[];disabled?:boolean;compact?:boolean}>(),{disabled:false,compact:false})
const emit=defineEmits<{completed:[];changed:[run:ChatGPTRun]}>()
const run=ref<ChatGPTRun|null>(null),token=ref(''),message=ref(''),busy=ref(false)
const active=computed(()=>!!run.value && !['done','cancelled','failed'].includes(run.value.status))
const view=computed(()=>run.value?toChatGPTRunView(run.value):null)
const key=()=>`previs.image-use.${props.project}`
let timer:ReturnType<typeof setInterval>|undefined
let refreshing=false
async function refresh(){
 if(!run.value || refreshing)return
 const project=props.project,id=run.value.run_id
 refreshing=true
 try{
  const current=(await fetchChatGPTRun(project,id)).run
  if(project!==props.project || run.value?.run_id!==id)return
  const done=current.status==='done' && run.value.status!=='done'
  run.value=current;emit('changed',current);if(done)emit('completed')
 }catch(e){message.value=String(e)}finally{refreshing=false}
}
async function start(ids?:string[]){
 if(active.value || busy.value)return false
 const selected=[...new Set(ids?.length?ids:props.jobIds)]
 if(!selected.length)return false
 busy.value=true;message.value=''
 try{
  const current=(await createChatGPTRun({project:props.project,job_ids:selected,options:{vision_validation:false,auto_import:true}})).run
  run.value=current;token.value=current.run_token || ''
  localStorage.setItem(key(),JSON.stringify({id:current.run_id,token:token.value}))
  emit('changed',current);return true
 }catch(e){message.value=String(e);return false}finally{busy.value=false}
}
async function control(action:'pause'|'resume'|'cancel'){
 if(!run.value || !token.value)return
 try{
  run.value=(await controlChatGPTRun(props.project,run.value.run_id,token.value,action)).run
  message.value=action==='resume'?'正在恢复；已有发送记录不会重新绘制。':'已请求停止，当前图片保存后生效。'
 }catch(e){message.value=String(e)}
}
watch(()=>props.project,async()=>{
 run.value=null;token.value='';message.value=''
 const project=props.project,storageKey=key()
 try{
  const raw=localStorage.getItem(storageKey) || sessionStorage.getItem(storageKey)
  const saved=JSON.parse(raw || 'null')
  if(saved){
   const restored=(await fetchChatGPTRun(project,saved.id)).run
   if(project!==props.project)return
   token.value=saved.token;run.value=restored
   localStorage.setItem(storageKey,JSON.stringify(saved))
   sessionStorage.removeItem(storageKey)
  }
 }catch{}
},{immediate:true})
onMounted(()=>{timer=setInterval(refresh,2000)})
onBeforeUnmount(()=>{if(timer)clearInterval(timer)})
defineExpose({start,refresh})
</script>
<template>
 <section class="my-3 rounded-xl border border-sky-400/20 bg-sky-400/5 p-3 text-xs">
  <div class="flex flex-wrap items-center gap-2">
   <strong class="text-sky-200">ChatGPT · 单张串行</strong>
   <a href="https://github.com/leeguooooo/image-use" target="_blank" rel="noopener" title="服务由 image-use / chrome-use 提供，工作台负责任务和导入。" class="text-slate-400">image-use ↗</a>
   <button class="btn btn-sm" :disabled="disabled || busy || active || !jobIds.length" @click="start()">{{busy?'启动中…':`依次生成并导入（${jobIds.length} 项）`}}</button>
   <button v-if="active" class="btn btn-sm" @click="control('pause')">本张结束后暂停</button>
   <button v-if="run?.status==='paused' || (active && run?.worker_active===false)" class="btn btn-sm" @click="control('resume')">恢复 / 对账</button>
   <button v-if="active" class="btn btn-sm" @click="control('cancel')">取消后续</button>
  </div>
  <p class="mt-2 text-slate-400">逐项上传参考图、生成一张、下载原图、按任务 ID 导入。异常停止，不自动重发。文件校验通过不代表画面内容已验收。</p>
  <p v-if="view" class="mt-2 text-sky-200">{{view.phaseLabel}} · {{view.progressText}} · {{run?.current_attempt?.job_id}}</p>
  <p v-if="run?.stop_requested" class="mt-2 text-amber-200">已请求 {{run.stop_requested==='cancel'?'取消后续':'本张后暂停'}}</p>
  <p v-if="run?.pause_reason" class="mt-2 text-amber-200">{{run.pause_reason}}</p>
  <p v-if="message" class="mt-2 text-amber-200">{{message}}</p>
 </section>
</template>
