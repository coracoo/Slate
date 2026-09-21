import test from 'node:test'
import assert from 'node:assert/strict'
import { isAbnormal, hideAbnormal, visibleEntries, readHidden } from '../src/utils/galleryVisibility.ts'
import { visibleVendors } from '../src/utils/providerVisibility.ts'

const now = Date.parse('2026-09-20T12:00:00+08:00')
test('只清理失败异常和超过一天的生成，保留排队和有效产出', () => {
  const old = new Date(now - 86400001).toISOString()
  for (const status of ['error', 'failed', 'abnormal', 'cancelled', 'interrupted', 'timeout']) assert.equal(isAbnormal({status}, now), true)
  assert.equal(isAbnormal({status:'running', created_at:old}, now), true)
  assert.equal(isAbnormal({status:'generating', created_at:new Date(now-86400000).toISOString()}, now), false)
  for (const status of ['queued', 'awaiting_import', 'pending']) assert.equal(isAbnormal({status, created_at:old}, now), false)
  assert.equal(isAbnormal({status:'running', created_at:'invalid'}, now), false)
  assert.equal(isAbnormal({status:'done', outputs:['a.png']}, now), false)
  assert.equal(isAbnormal({status:'done', outputs:[]}, now), true)
})
test('隐藏可持久化，不修改源数据；重试或恢复产出自动重现', () => {
  const items = [{id:'a',status:'error',updated_at:'v1'}, {id:'b',status:'done',outputs:['b.png']}]
  const before = JSON.stringify(items)
  const hidden = hideAbnormal(items, {}, now)
  assert.deepEqual(visibleEntries(items, readHidden(JSON.stringify(hidden))).map(x=>x.id), ['b'])
  assert.equal(JSON.stringify(items), before)
  assert.equal(visibleEntries([{...items[0],status:'running'}], hidden).length, 1)
  assert.equal(visibleEntries([{...items[0],updated_at:'v2'}], hidden).length, 1)
  assert.equal(visibleEntries(items, {}).length, 2)
  assert.deepEqual(readHidden('bad'), {})
  assert.deepEqual(readHidden('[1,2]'), {})
})
test('隐藏厂商仅影响显示，完整配置和其他厂商模型保持不变', () => {
  const vendors=[{id:'doubao',models:{text:'kimi-k3'}},{id:'kimi',api_key:'secret'},{id:'glm'},{id:'deepseek'}]
  assert.deepEqual(visibleVendors(vendors).map(x=>x.id), ['doubao','deepseek'])
  assert.equal(vendors[1].api_key,'secret')
  assert.equal(vendors.length,4)
})
