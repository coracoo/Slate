import test from 'node:test'
import assert from 'node:assert/strict'
import { createPlanningPreview } from '../src/utils/planningPreview.ts'

function deferred() {
  let resolve, reject
  const promise = new Promise((ok, fail) => { resolve = ok; reject = fail })
  return { promise, resolve, reject }
}
function fixture() {
  const calls = []
  let state
  const loader = createPlanningPreview((project, id) => {
    const request = deferred()
    calls.push({ project, id, ...request })
    return request.promise
  }, value => { state = value })
  loader.reset('项目甲')
  return { loader, calls, get state() { return state } }
}
const version = id => ({ id, label: `规划${id}`, history_id: `基线${id}` })
async function finish(f, id) {
  const pending = f.loader.select(version(id))
  f.calls.at(-2).resolve({ premise: id })
  f.calls.at(-1).resolve({ premise: `旧${id}` })
  await pending
}

test('切换期间保留旧正文与基线，两个请求齐备才原子替换', async () => {
  const f = fixture()
  await finish(f, 'A')
  const old = f.state.displayed
  const pending = f.loader.select(version('B'))
  assert.equal(f.state.displayed, old)
  assert.equal(f.state.loading, true)
  assert.equal(f.loader.canAct(), false)
  f.calls.at(-2).resolve({ premise: 'B' })
  await Promise.resolve()
  assert.equal(f.state.displayed, old)
  f.calls.at(-1).resolve({ premise: '旧B' })
  await pending
  assert.deepEqual(f.state.displayed, {
    version: version('B'), preview: { premise: 'B' }, baseline: { premise: '旧B' },
  })
  assert.equal(f.loader.canAct(), true)
})

test('快速切换后旧请求失败不能覆盖最终选择的成功结果', async () => {
  const f = fixture()
  const a = f.loader.select(version('A'))
  const b = f.loader.select(version('B'))
  f.calls[2].resolve({ premise: 'B' })
  f.calls[3].resolve({ premise: '旧B' })
  await b
  f.calls[0].reject(new Error('A读取失败'))
  f.calls[1].resolve({ premise: '旧A' })
  await a
  assert.equal(f.state.displayed.version.id, 'B')
  assert.equal(f.state.error, '')
  assert.equal(f.state.loading, false)
})

test('旧请求先成功也不能提前解除新版本的加载状态或更新正文', async () => {
  const f = fixture()
  await finish(f, 'A')
  const b = f.loader.select(version('B'))
  const c = f.loader.select(version('C'))
  f.calls[2].resolve({ premise: 'B' })
  f.calls[3].resolve({ premise: '旧B' })
  await b
  assert.equal(f.state.displayed.version.id, 'A')
  assert.equal(f.state.selected.id, 'C')
  assert.equal(f.state.loading, true)
  assert.equal(f.loader.canAct(), false)
  f.calls[4].resolve({ premise: 'C' })
  f.calls[5].resolve({ premise: '旧C' })
  await c
  assert.equal(f.state.displayed.version.id, 'C')
  assert.equal(f.loader.canAct(), true)
})

test('新版本基线失败时保留完整旧快照，禁止将旧内容作为新版本采用，重试可恢复', async () => {
  const f = fixture()
  await finish(f, 'A')
  const pending = f.loader.select(version('B'))
  f.calls.at(-2).resolve({ premise: 'B' })
  f.calls.at(-1).reject(new Error('基线读取失败'))
  await pending
  assert.equal(f.state.selected.id, 'B')
  assert.equal(f.state.displayed.version.id, 'A')
  assert.equal(f.state.error, '基线读取失败')
  assert.equal(f.loader.canAct(), false)
  await finish(f, 'B')
  assert.equal(f.state.displayed.version.id, 'B')
  assert.equal(f.state.error, '')
  assert.equal(f.loader.canAct(), true)
})

test('切项目立即移除旧内容，旧项目异步返回不能重新填充', async () => {
  const f = fixture()
  await finish(f, 'A')
  const pending = f.loader.select(version('B'))
  f.loader.reset('项目乙')
  assert.equal(f.state.displayed, null)
  assert.equal(f.state.selected, null)
  f.calls.at(-2).resolve({ premise: 'B' })
  f.calls.at(-1).resolve({ premise: '旧B' })
  await pending
  assert.equal(f.state.displayed, null)
  assert.equal(f.loader.canAct(), false)
  await finish(f, 'C')
  assert.equal(f.calls.at(-2).project, '项目乙')
  assert.equal(f.state.displayed.version.id, 'C')
})
