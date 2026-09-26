import test from 'node:test'
import assert from 'node:assert/strict'
import { sceneLabel, speakerName } from '../src/utils/assetNames.ts'

// ③ 网页表格与 Excel 导出的"名字"口径（09-25 用户定版：两侧都用名字，不是 id / room·field）。
// 后端同源实现在 workbench/tools/export_storyboard_xlsx.py，两侧规则分叉就会被各自的测试咬住。
const BOARD_ACTORS = { hero: { name: '板内名·主角' } }
const ARCHIVE = [{ id: 'hero', name: '主角' }, { id: 'lu', name: '刘备' }]

test('说话人名字：板内 actors 优先，其次 ② 人物档案，最后才退回 id', () => {
  assert.equal(speakerName('hero', BOARD_ACTORS, ARCHIVE), '板内名·主角')
  assert.equal(speakerName('lu', BOARD_ACTORS, ARCHIVE), '刘备')
  assert.equal(speakerName('ghost', BOARD_ACTORS, ARCHIVE), 'ghost', '解不到名要照实留 id，不能静默丢这行台词')
  assert.equal(speakerName('hero', undefined, undefined), 'hero', '没有名字表时也不能崩')
  assert.equal(speakerName(undefined, BOARD_ACTORS, ARCHIVE), '')
})

test('场景列：scene_ref 出场景名，没关联才回落室内/外景', () => {
  const names = { loc_tent: '中军帐' }
  assert.equal(sceneLabel({ scene_ref: '@scene:loc_tent', scene: 'room' }, names), '中军帐',
    '有关联时 scene 字段（预设地形）不该盖掉场景名')
  assert.equal(sceneLabel({ scene_ref: 'loc_tent' }, names), '中军帐', '裸 ref（无 @scene: 前缀）同样要认')
  assert.equal(sceneLabel({ scene: 'room' }, names), '室内')
  assert.equal(sceneLabel({ scene: 'field' }, names), '外景')
  assert.equal(sceneLabel({ scene_ref: '@scene:gone' }, names), 'gone', '档案里查不到就照实显示 ref')
  assert.equal(sceneLabel({}, names), '')
})
