import test from 'node:test'
import assert from 'node:assert/strict'
import { durableRequest, pendingRequest } from '../src/utils/durableRequest.ts'

const memory = () => { const m = new Map(); return {getItem: k => m.get(k) || null, setItem: (k,v) => m.set(k,v), removeItem: k => m.delete(k)} }
test('响应丢失并刷新后复用原 nonce，原请求内容不受页面新选择影响', async () => {
  const storage = memory(), body = {project:'test', target:'S1'}
  let first
  await assert.rejects(durableRequest(body, async b => { first=b; throw new Error('network') }, false, storage))
  assert.equal(pendingRequest('test', storage).nonce, first.nonce)
  await assert.rejects(durableRequest({...body,target:'S2'}, async()=>{}, false, storage), /未确认/)
  await durableRequest({...body,target:'S2'}, async b => {assert.deepEqual(b,first)}, true, storage)
  assert.equal(pendingRequest('test',storage), null)
})
test('明确参数拒绝可以修正，存储失败绝不发送', async () => {
  const body = {project:'test'}, storage=memory()
  await assert.rejects(durableRequest(body, async()=> {throw {status:400}}, false, storage))
  assert.equal(pendingRequest('test',storage),null)
  let sent=false
  await assert.rejects(durableRequest(body,async()=>{sent=true},false,{...storage,setItem(){throw Error('disk')}}))
  assert.equal(sent,false)
})
