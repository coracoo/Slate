import test from 'node:test'
import assert from 'node:assert/strict'
import { pendingEpisodeIds } from '../src/utils/scriptEpisodes.ts'

test('pendingEpisodeIds returns only episodes without screenplay text', () => {
  assert.deepEqual(pendingEpisodeIds([
    { id: 'E1', text: '已有正文' },
    { id: 'E2', text: '' },
    { id: 'E3' },
    { id: 'E4', text: '  ' },
  ]), ['E2', 'E3', 'E4'])
})
