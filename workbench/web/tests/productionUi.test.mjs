import test from 'node:test'
import assert from 'node:assert/strict'
import { groupGalleryEntries, restoreSelection, boardSelection } from '../src/utils/productionUi.ts'

test('groupGalleryEntries keeps outputs isolated by storyboard, shot and panel', () => {
  const a = { id: 'a', board: '剧本_E1.json', shot_id: 'S1', panel_id: 'key' }
  const b = { id: 'b', board: '剧本_E1.json', shot_id: 'S2', panel_id: 'key' }
  const c = { id: 'c', board: '剧本_E1.json', shot_id: 'S1', panel_id: 'video' }
  const groups = groupGalleryEntries([a, b, c, { id: 'd' }])
  assert.deepEqual(groups.map((group) => group.key), [
    '剧本_E1.json::S1::key',
    '剧本_E1.json::S2::key',
    '剧本_E1.json::S1::video',
    '自由创作::未关联分镜::',
  ])
  assert.deepEqual(groups[0].entries.map((item) => item.id), ['a'])
  assert.equal(groups[0].label, '剧本_E1.json · S1 · key')
})

test('restoreSelection prefers cached value and never force-picks an option without memory', () => {
  // 无记忆且当前值不在选项中时返回 ''——不兜底硬选最后一项（见 productionUi.ts 注释：
  // 选项异步加载期间兜底会误触发一次“选择”，把自动回填当成用户操作）
  assert.equal(restoreSelection(['E1', 'E2'], '', ''), '')
  assert.equal(restoreSelection(['E1', 'E2'], 'E1', ''), 'E1')
  assert.equal(restoreSelection(['E1', 'E2'], 'E1', 'E2'), 'E2')
  assert.equal(restoreSelection(['E1', 'E2'], 'missing', 'stale'), '')
  assert.equal(restoreSelection([], 'E1', 'E1'), '')
})

test('分镜首项按集号排序，缓存刷新后保留，旧项目值不进入新项目',()=>{
 const options=['剧本_E10.json','剧本_E2.json','剧本_E1.json']
 assert.equal(boardSelection(options,'',''),'剧本_E1.json')
 assert.equal(boardSelection(options,'剧本_E10.json','剧本_E2.json'),'剧本_E2.json')
 assert.equal(boardSelection(options,'剧本_E2.json',''),'剧本_E2.json')
 assert.equal(boardSelection(['另一个.json'],'剧本_E2.json','过期.json'),'另一个.json')
 assert.equal(boardSelection([],'剧本_E2.json','剧本_E2.json'),'')
})
