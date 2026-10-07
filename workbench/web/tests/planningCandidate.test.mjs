import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'

const component=fs.readFileSync(new URL('../src/components/PlanningCandidate.vue',import.meta.url),'utf8')
const source=component.split('<script setup lang="ts">')[1].split('</script>')[0].replace(/^import .*$/mg,'')
const js=ts.transpile(source+'\nexports.state={load,review,adopt,resume,candidate,open,items,reviewError,error};',{
  target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,
})
function fixture(status='ready') {
  const props={project:'甲',revision:0},reads=[],writes=[],events=[]
  const candidate={id:'候选一',status,label:'改写规划',created_at:'今天',can_adopt:status==='ready',can_resume:status==='failed'}
  const context={exports:{},defineProps:()=>props,defineEmits:()=>((...event)=>events.push(event)),
    ref:value=>({value}),watch:()=>{},onUnmounted:()=>{},defineExpose:()=>{},onJobDone:()=>()=>{},toast:()=>{},
    fetchPlanningCandidate:async (project,id)=>{reads.push({project,id});return {candidate,revision:'已审阅基线',groups:[{label:'分集',before:'旧规划',after:'候选规划'}]}},
    editUnits:async body=>{writes.push(body);return {ok:true}},buildUnits:async()=>({id:1}),trackJob:async()=>({success:true}),Error,
  }
  vm.runInNewContext(js,context)
  return {props,reads,writes,events,context,state:context.exports.state}
}

test('生成中和失败候选不能打开采用审核或发送采用请求', async () => {
  for(const status of ['generating','failed']) {
    const f=fixture(status);await f.state.load();await f.state.review();await f.state.adopt(['候选一'])
    assert.equal(f.state.open.value,false)
    assert.equal(f.reads.length,1)
    assert.equal(f.writes.length,0)
  }
})

test('采用携带已审阅基线与候选ID，切项目后旧审核不能写入', async () => {
  const f=fixture();await f.state.load();await f.state.review()
  assert.equal(f.state.open.value,true)
  assert.equal(f.state.items.value[0].groups[0].after,'候选规划')
  f.props.project='乙';await f.state.adopt(['候选一'])
  assert.equal(f.writes.length,0)
  f.props.project='甲';await f.state.adopt(['候选一'])
  assert.equal(f.writes[0].project,'甲')
  assert.equal(f.writes[0].kind,'adopt-plan')
  assert.equal(f.writes[0].id,'候选一')
  assert.equal(f.writes[0].revision,'已审阅基线')
})

test('采用冲突时保留审核差异并显示错误，不通知已更新', async () => {
  const f=fixture();await f.state.load();await f.state.review()
  f.context.editUnits=async()=>{throw new Error('规划已有更新')}
  await f.state.adopt(['候选一'])
  assert.equal(f.state.open.value,true)
  assert.equal(f.state.reviewError.value,'规划已有更新')
  assert.equal(f.events.some(event=>event[0]==='changed'),false)
})

test('缺失差异或基线的响应不能打开采用弹窗', async () => {
  const f=fixture();await f.state.load()
  f.context.fetchPlanningCandidate=async()=>({candidate:f.state.candidate.value,revision:''})
  await f.state.review();await f.state.adopt(['候选一'])
  assert.equal(f.state.open.value,false)
  assert.match(f.state.error.value,/基线缺失/)
  assert.equal(f.writes.length,0)
})
