import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'

// 3s 发现链是自排 setTimeout：无幂等守卫时 authReady 与 LoginView 各起一条，
// 每次 SPA 内登录再多一条 → /api/jobs 轮询量按登录次数翻倍，且登出后无人清理。
function load({ guest = () => false } = {}) {
  const scheduled = []
  const api = fs.readFileSync(new URL('../src/api.ts', import.meta.url), 'utf8')
  const helpers = api.slice(api.indexOf('export function jobDone'), api.indexOf('export function fmtT'))
  const source = fs.readFileSync(new URL('../src/stores/jobs.ts', import.meta.url), 'utf8').replace(/^import .*$/mg, '')
  const js = ts.transpile(helpers + source, { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS })
  const context = {
    exports: {}, reactive: x => x, toast: () => {},
    fetchJob: async id => ({ id, status: 'running' }),
    fetchJobs: async () => ({ jobs: [] }),
    authReady: Promise.resolve(true), isGuest: guest,
    sessionStorage: { getItem: () => null, setItem: () => {} },
    window: { setTimeout: (fn, ms) => { scheduled.push(ms); return scheduled.length } },
    Date, Map, Set,
  }
  vm.runInNewContext(js, context)
  return { ...context.exports, scheduled }
}

const flush = async () => { for (let i = 0; i < 4; i++) await new Promise(r => setTimeout(r, 0)) }

test('任务轮询只有一条发现链：重复启动被幂等挡掉', async () => {
  const { startJobDiscovery, scheduled } = load()
  await flush()   // 让模块级 authReady.then 先起链
  startJobDiscovery(); startJobDiscovery(); startJobDiscovery()
  await flush()
  const chains = scheduled.filter(ms => ms === 3000).length
  assert.equal(chains, 1, `发现链应为 1 条，实际 ${chains} 条（每次调用各起一条）`)
})

test('未登录时不起链；转访客后允许重新登录再起一条', async () => {
  let guest = true
  const { startJobDiscovery, scheduled } = load({ guest: () => guest })
  await flush()
  startJobDiscovery()
  await flush()
  assert.equal(scheduled.filter(ms => ms === 3000).length, 0, '访客态不得打网络')
  guest = false
  startJobDiscovery()
  await flush()
  assert.equal(scheduled.filter(ms => ms === 3000).length, 1)
})
