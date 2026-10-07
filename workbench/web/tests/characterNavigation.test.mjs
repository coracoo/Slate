import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import {characterSectionTarget,characterStateElementId} from '../src/utils/characterNavigation.ts'

test('派生、音色、表演栏目重定向都能定位，未知锚点不访问页面元素',()=>{
  for(const section of ['states','voices','performance']) assert.equal(characterSectionTarget(undefined,'#'+section),section)
  assert.equal(characterSectionTarget(undefined,'#unknown'),'')
  assert.equal(characterSectionTarget('', '#voices'),'voices')
})

test('明确派生状态优先于栏目锚点，并与实际元素ID一致',()=>{
  const id='受伤/夜间'
  assert.equal(characterSectionTarget(id,'#voices'),characterStateElementId(id))
  const page=fs.readFileSync(new URL('../src/views/CharacterView.vue',import.meta.url),'utf8')
  assert.match(page,/characterSectionTarget\(route.query.state,route.hash\)/)
  assert.match(page,/:id="stateElementId\(state.id\)"/)
  assert.match(page,/watch\(\(\) => \[route.query.character, route.query.state, route.hash\]/)
})
