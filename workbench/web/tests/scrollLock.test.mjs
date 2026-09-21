import test from 'node:test'
import assert from 'node:assert/strict'
import { lockBodyScroll } from '../src/utils/scrollLock.ts'

test('重叠预览卸载只在最后一次释放恢复原滚动值，重复释放无副作用', () => {
  globalThis.document = {body:{style:{overflow:'auto'}}}
  const first=lockBodyScroll(), second=lockBodyScroll()
  assert.equal(document.body.style.overflow,'hidden')
  first(); first()
  assert.equal(document.body.style.overflow,'hidden')
  second()
  assert.equal(document.body.style.overflow,'auto')
  delete globalThis.document
})
