import test from 'node:test'
import assert from 'node:assert/strict'
import { shotRangeRows } from '../src/utils/shotRange.ts'

// 真实分镜形态：拉片/解构产出的描述式 id（02_蜘蛛侠、03_海街日记、01_买瓜）
const DESC_IDS = ['S01 楼顶登场·戴面具', 'S02 跃出楼顶·自由落体', 'S03 砸上玻璃幕墙', 'S04 垂直俯冲主观POV']
const PLAIN_IDS = ['S1', 'S2', 'S3', 'S4']
const GAPPED_IDS = ['S1', 'S2', 'S5', 'S8']

test('描述式 id 的分镜按行号取区间（旧实现把 id 原文拼进串里，直接被校验拒）', () => {
  assert.deepEqual(shotRangeRows('2-3', DESC_IDS), [1, 2])
  assert.deepEqual(shotRangeRows('S2-S3', DESC_IDS), [1, 2])
  assert.deepEqual(shotRangeRows('4-4', DESC_IDS), [3])
})

test('常规 S 号分镜按 id 命中，顺序与页面一致', () => {
  assert.deepEqual(shotRangeRows('S2-S4', PLAIN_IDS), [1, 2, 3])
  assert.deepEqual(shotRangeRows('S4-S2', PLAIN_IDS), [1, 2, 3])
})

test('断号分镜：id 优先、位置兜底，缺号行不会被拉进来', () => {
  assert.deepEqual(shotRangeRows('S5-S8', GAPPED_IDS), [2, 3])
  assert.deepEqual(shotRangeRows('3-4', GAPPED_IDS), [2, 3])
})

test('默认全量串 S1-S999 不会越界选中，也不因越界丢弃真实镜头', () => {
  assert.deepEqual(shotRangeRows('S1-S999', PLAIN_IDS), [0, 1, 2, 3])
  assert.deepEqual(shotRangeRows('S1-S999', DESC_IDS), [0, 1, 2, 3])
})

test('非法区间文本返回空集，交给调用方按未选中处理', () => {
  for (const bad of ['', 'S1', '1', 'S1-', 'abc', 'S1-S2-S3', 'Sx-Sy']) {
    assert.deepEqual(shotRangeRows(bad, PLAIN_IDS), [])
  }
})
