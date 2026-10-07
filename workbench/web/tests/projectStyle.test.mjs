import test from 'node:test'
import assert from 'node:assert/strict'
import {createProjectStyleWriter} from '../src/utils/projectStyle.ts'

function deferred() {
  let resolve, reject
  const promise = new Promise((ok, fail) => { resolve=ok; reject=fail })
  return {promise, resolve, reject}
}

test('同项目多个维度串行读取最新风格，保存不会丢掉前一项', async () => {
  let stored={script_structure:'旧结构', image:'原画风'}
  const gate=deferred(), reads=[], writes=[]
  const save=createProjectStyleWriter(async project=>{reads.push(project);return {...stored}},async (project,style,patch)=>{
    writes.push({project,style:{...style},patch})
    if(writes.length===1) await gate.promise
    stored={...style}
  })
  const first=save('甲',style=>{style.script_structure='新结构'})
  const second=save('甲',style=>{style.script_pacing='快节奏'})
  await Promise.resolve();await Promise.resolve()
  assert.deepEqual(reads,['甲'])
  gate.resolve();await Promise.all([first,second])
  assert.deepEqual(stored,{script_structure:'新结构',script_pacing:'快节奏',image:'原画风'})
  assert.deepEqual(writes.map(write=>write.patch),[{script_structure:'新结构'},{script_pacing:'快节奏'}])
})

test('切项目后在途保存仍写原项目，不同项目的保存互不等待', async () => {
  const gate=deferred(), writes=[]
  const save=createProjectStyleWriter(async project=>{if(project==='甲') await gate.promise;return {owner:project}},async (project,style)=>writes.push({project,style}))
  const pending=save('甲',style=>{style.image='甲画风'})
  await save('乙',style=>{style.image='乙画风'})
  assert.deepEqual(writes,[{project:'乙',style:{owner:'乙',image:'乙画风'}}])
  gate.resolve();await pending
  assert.deepEqual(writes[1],{project:'甲',style:{owner:'甲',image:'甲画风'}})
})

test('前次保存失败不会阻塞后续选择或复用失败的风格快照', async () => {
  const writes=[]
  const save=createProjectStyleWriter(async ()=>({image:'现有画风'}),async (_project,style)=>{
    writes.push({...style})
    if(writes.length===1) throw new Error('保存失败')
  })
  const first=save('甲',style=>{style.script_structure='失败的修改'})
  const second=save('甲',style=>{style.script_pacing='有效修改'})
  await assert.rejects(first,/保存失败/)
  await second
  assert.deepEqual(writes[1],{image:'现有画风',script_pacing:'有效修改'})
})
