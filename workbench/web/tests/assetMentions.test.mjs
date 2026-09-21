import test from 'node:test'
import assert from 'node:assert/strict'
import { assetMentionContext, extractAssetRefs, filterAssetMentionCandidates } from '../src/utils/assetMentions.ts'

const assets = [
  { ref: '@character:hero', id: 'hero', name: '白咲蛛绪', aliases: ['蛛绪'] },
  { ref: '@scene:room', id: 'room', name: '二年A班教室' },
  { ref: '@prop:desk', id: 'desk', name: '佐藤陆的课桌' },
]

test('assetMentionContext recognizes @ references and plain asset names', () => {
  const mention = '场景中 @白咲'
  assert.deepEqual(assetMentionContext(mention, mention.length), {
    mode: 'ref', query: '白咲', start: 4, end: mention.length,
  })
  assert.deepEqual(assetMentionContext('背景是二年A班教室', 9), {
    mode: 'name', query: '背景是二年A班教室', start: 0, end: 9,
  })
})

test('assetMentionContext returns the text range to replace', () => {
  const text = '场景 @character:'
  const ctx = assetMentionContext(text)
  assert.equal(ctx?.start, 3)
  assert.equal(ctx?.end, text.length)
  assert.equal(ctx?.query, 'character:')
})

test('filterAssetMentionCandidates searches names, ids and aliases', () => {
  assert.deepEqual(filterAssetMentionCandidates(assets, '蛛绪').map(row => row.ref), ['@character:hero'])
  assert.deepEqual(filterAssetMentionCandidates(assets, 'desk').map(row => row.ref), ['@prop:desk'])
  assert.deepEqual(filterAssetMentionCandidates(assets, '', '@scene:room').map(row => row.ref), [
    '@character:hero', '@prop:desk',
  ])
})

test('extractAssetRefs keeps canonical refs in order and removes duplicates', () => {
  assert.deepEqual(extractAssetRefs('@scene:room 中 @character:hero，重复 @scene:room'), [
    '@scene:room', '@character:hero',
  ])
})
