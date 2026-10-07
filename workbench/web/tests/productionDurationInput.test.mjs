import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'
import {retimeUnit} from '../src/utils/shotPromptEditor.ts'

const page=fs.readFileSync(new URL('../src/views/ProductionStudioView.vue',import.meta.url),'utf8')
const source=page.slice(page.indexOf('function editDuration'),page.indexOf('async function split'))
const js=ts.transpile(source+'\nexports.editDuration=editDuration;',{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS})
function fixture(scope) {
  const cell=value=>({value}),first={id:'S1',dur:4},second={id:'S2',dur:6},unit={id:'V1',shot_ids:['S1','S2'],duration:10}
  const context={exports:{},scope:cell(scope),shot:cell(first),unit:cell(unit),units:cell([unit]),allShots:cell([first,second]),duration:cell(scope==='S'?4:10),dirty:cell(false),retimeUnit,
    syncDuration:()=>{context.duration.value=context.scope.value==='S'?context.shot.value.video_duration || context.shot.value.dur:context.unit.value.duration},Number}
  vm.runInNewContext(js,context)
  return context
}
test('单镜输入只显示单镜时长，同时更新所属V合计',()=>{
  const f=fixture('S');f.exports.editDuration({target:{valueAsNumber:3}})
  assert.equal(f.shot.value.dur,3)
  assert.equal(f.shot.value.video_duration,3)
  assert.equal(f.unit.value.duration,9)
  assert.equal(f.duration.value,3)
  assert.equal(f.dirty.value,true)
})
test('整V覆盖只改变V总时长，无效输入不改任何时长',()=>{
  const f=fixture('V');f.exports.editDuration({target:{valueAsNumber:12}})
  assert.equal(f.unit.value.duration,12)
  assert.equal(f.shot.value.dur,4)
  for(const value of [NaN,0,-1]) f.exports.editDuration({target:{valueAsNumber:value}})
  assert.equal(f.unit.value.duration,12)
  assert.equal(f.duration.value,12)
})
