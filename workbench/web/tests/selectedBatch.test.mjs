import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {runSelectedBatch} from '../src/utils/selectedBatch.ts'

test('固定选择，失败继续，成功项不重复发送', async()=>{
  const selected=['E1','E2','E1','E3'], called=[]
  const result=await runSelectedBatch(selected,async id=>{called.push(id);selected.push('E4');if(id==='E2') throw new Error('单集失败')})
  assert.deepEqual(called,['E1','E2','E3'])
  assert.deepEqual(result.filter(r=>!r.ok).map(r=>r.id),['E2'])
  const retried=[]
  await runSelectedBatch(result.filter(r=>!r.ok).map(r=>r.id),async id=>{retried.push(id)})
  assert.deepEqual(retried,['E2'])
})
test('空选择不发任何任务',async()=>{
  await assert.rejects(runSelectedBatch([],async()=>assert.fail('不可请求')),/选择/)
})
test('关键批量入口实际绑定事件，保留单项入口',()=>{
  const read=name=>readFileSync(new URL('../src/'+name,import.meta.url),'utf8')
  const shots=read('views/StudioShotsView.vue')
  for(const action of ['prompts','references']) assert.ok(shots.includes(`@click="runBatch('${action}')"`))
  assert.ok(shots.includes('@click="fillMissingPrompts"'))
  assert.ok(read('components/AppearanceReviewButton.vue').includes('@click="primaryAction(selectedIds)"'))
  assert.ok(read('components/StoryUnitsCard.vue').includes('@click="editEpisodeBatch"'))
})
