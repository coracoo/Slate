import test from 'node:test'
import assert from 'node:assert/strict'
import { conflictNotice, isRevisionConflict, overwriteAllowed } from '../src/utils/boardRevision.ts'

// ③ 汇总表格是整组回写：一旦失去基线就退化成"最后写入者赢"，会把 ⑦/⑤ 期间的改动静默盖掉。
// 这里锁住判型与放行条件——409 才叫冲突，覆盖必须同时有显式确认和可用新基线。
const conflict = Object.assign(new Error('revision 冲突: expected=abc, current=def'), { status: 409 })

test('只有 409 算乐观锁冲突，400/500/普通异常都不算', () => {
  assert.equal(isRevisionConflict(conflict), true)
  assert.equal(isRevisionConflict(Object.assign(new Error('时长超出 1~60s'), { status: 400 })), false)
  assert.equal(isRevisionConflict(new Error('网络断了')), false)
  assert.equal(isRevisionConflict(undefined), false)
  assert.equal(isRevisionConflict(null), false)
})

test('冲突提示要让人能决策：说清谁的改动没落地、留下哪两条路，且不透服务端串', () => {
  const text = conflictNotice(conflict)
  assert.match(text, /⑦|⑤/, '要点名是谁可能改过这份分镜')
  assert.match(text, /重新载入|覆盖/, '要给两条出路')
  assert.ok(!text.includes('expected='), '服务端内部串不该原样给用户看')
  assert.equal(conflictNotice(new Error('时长超出 1~60s: 99')), '时长超出 1~60s: 99', '非冲突要原样回显根因')
})

test('覆盖放行：确认勾选 + 拿到新基线，缺一个都不放行', () => {
  assert.equal(overwriteAllowed(true, 'rev-new'), true)
  assert.equal(overwriteAllowed(false, 'rev-new'), false, '没确认不得覆盖别人的改动')
  assert.equal(overwriteAllowed(true, ''), false, '拿不到基线不得发无基线写')
  assert.equal(overwriteAllowed(true, null), false)
})
