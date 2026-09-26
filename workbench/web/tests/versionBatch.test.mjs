import test from 'node:test'
import assert from 'node:assert/strict'
import { createVersionBatcher } from '../src/utils/versionBatch.ts'

const tick = (ms = 0) => new Promise(r => setTimeout(r, ms))

test('同窗口内多路径合成一次批量请求，结果按路径分发', async () => {
  const calls = []
  const req = createVersionBatcher(async (paths) => {
    calls.push([...paths])
    return Object.fromEntries(paths.map(p => [p, [{ ts: p + '@1', rel: p, current: true }]]))
  }, 5)

  const [a, b, c, dup] = await Promise.all([req('x/a.png'), req('x/b.png'), req('x/a.png'), req('x/c.json')])
  assert.equal(calls.length, 1, '一个窗口内应只发一次')
  assert.deepEqual(calls[0], ['x/a.png', 'x/b.png', 'x/c.json'], '重复路径应去重')
  assert.equal(a[0].ts, 'x/a.png@1')
  assert.equal(c[0].ts, 'x/a.png@1', '同一路径的多个等待者都该拿到结果')
  assert.equal(b[0].rel, 'x/b.png')
  assert.equal(dup[0].ts, 'x/c.json@1')
})

test('窗口结束后新请求另起一批；空结果按无历史处理', async () => {
  let n = 0
  const req = createVersionBatcher(async (paths) => {
    n++
    return n === 1 ? Object.fromEntries(paths.map(p => [p, []])) : { later: [{ ts: 't', rel: 'later', current: true }] }
  }, 5)
  assert.deepEqual(await req('a.png'), [])
  await tick(20)
  const got = await req('later')
  assert.equal(n, 2, '错过窗口的请求应另发一批，不能挂在旧批次上')
  assert.equal(got[0].ts, 't')
})

test('批量端点失败时所有等待者一起拒绝，不静默当成"无历史"', async () => {
  const req = createVersionBatcher(async () => { throw new Error('502') }, 5)
  const p1 = req('a.png'), p2 = req('b.png')
  await assert.rejects(p1, /502/)
  await assert.rejects(p2, /502/)
})
