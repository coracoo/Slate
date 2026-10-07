import test from 'node:test'
import assert from 'node:assert/strict'
import { textDiff } from '../src/utils/textDiff.ts'

test('差异保留标点、空行与重复句，可还原前后稿', () => {
  for (const [before,after] of [['甲。\n\n乙。甲。','甲。新句！\n甲。'],['','新增。'],['删除。',''],['一；二','二；一']]) {
    const diff=textDiff(before,after)
    assert.equal(diff.filter(p=>p.kind!=='add').map(p=>p.text).join(''),before)
    assert.equal(diff.filter(p=>p.kind!=='remove').map(p=>p.text).join(''),after)
  }
})
test('中间插入不把后续原文全部标为变更',()=>{
  const diff=textDiff('第一句。第二句。','第一句。新句。第二句。')
  assert.deepEqual(diff.filter(p=>p.kind==='add').map(p=>p.text),['新句。'])
  assert.equal(diff.filter(p=>p.kind==='remove').length,0)
})
