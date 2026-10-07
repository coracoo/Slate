import test from 'node:test'
import assert from 'node:assert/strict'
import { appearanceDraft, appearancePatch, adoptAppearanceProposal, adoptPendingAppearanceProposals } from '../src/utils/characterAppearance.ts'

test('只保存改动外观，保留来源与精确数字文本', () => {
  const original = appearanceDraft({height:'172.50 cm', face:'左眉略高', sources:{height:'source'}, custom:'旧扩展'})
  const edited = structuredClone(original)
  edited.face = '右眉略高'
  assert.equal(original.height, '172.50 cm')
  assert.deepEqual(appearancePatch(edited, original), {face:'右眉略高', sources:{face:'authored'}})
})

test('整组采用只补待确认项，保留已采用的面部与人工服装', () => {
  const value = appearanceDraft({face:'已确认窄脸', outfit:'人工灰衣', sources:{outfit:'authored'},
    proposals:{face:'另一张圆脸', outfit:'另一套红衣', hair:'短卷发'}})
  assert.equal(adoptPendingAppearanceProposals(value), 1)
  assert.equal(value.face, '已确认窄脸')
  assert.equal(value.outfit, '人工灰衣')
  assert.equal(value.hair, '短卷发')
  assert.equal(value.sources.hair, 'authored')
})

test('采用建议显式变成人工设定，其他待采用建议保留', () => {
  const value = appearanceDraft({proposals:{headwear:'木簪', hair:'短卷发'}})
  adoptAppearanceProposal(value, 'headwear')
  assert.equal(value.headwear, '木簪')
  assert.equal(value.sources.headwear, 'authored')
  assert.equal(value.proposals.headwear, null)
  assert.equal(value.proposals.hair, '短卷发')
})
