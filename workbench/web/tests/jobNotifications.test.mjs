import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'

test('成功、失败、中断、丢失均通知一次，排队不提前完成', async () => {
  const notices = [], timers = [], responses = new Map()
  const api = fs.readFileSync(new URL('../src/api.ts', import.meta.url), 'utf8')
  const helpers = api.slice(api.indexOf('export function jobDone'), api.indexOf('export function fmtT'))
  const source = fs.readFileSync(new URL('../src/stores/jobs.ts', import.meta.url), 'utf8').replace(/^import .*$/mg, '')
  const js = ts.transpile(helpers + source, {target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS})
  const context = {exports:{}, reactive:x=>x, toast:(...x)=>notices.push(x),
    fetchJob:async id=>responses.get(id), fetchJobs:async()=>({jobs:[]}),
    authReady:Promise.resolve(true), isGuest:()=>false,
    sessionStorage:{getItem:()=>null,setItem:()=>{}},
    window:{setTimeout:(fn,ms)=>{if(ms!==3000)timers.push(fn);return timers.length}}, Date, Map, Set}
  vm.runInNewContext(js,context)
  const {trackJob}=context.exports
  for (const [id,result] of [[1,{status:'done',ok:true}],[2,{status:'failed',err:'失败原因'}],[3,{status:'interrupted'}],[4,{lost:true}]]) {
    responses.set(id,result)
    const p=trackJob(id,'任务'+id)
    assert.equal(trackJob(id,'重复'),p)
    await timers.shift()(); await p
  }
  assert.equal(notices.length,4)
  assert.deepEqual(notices.map(x=>x[1]),['ok','err','err','err'])
  responses.set(5,{status:'queued'})
  const p=trackJob(5,'排队任务');await timers.shift()()
  assert.equal(notices.length,4)
  responses.set(5,{status:'done',ok:true});await timers.shift()();await p
  assert.equal(notices.length,5)
})
