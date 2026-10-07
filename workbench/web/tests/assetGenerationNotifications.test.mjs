import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'

const page=fs.readFileSync(new URL('../src/views/StudioAssetView.vue',import.meta.url),'utf8')
const source=page.slice(page.indexOf('async function doGen'),page.indexOf('async function genChildren'))
const js=ts.transpile(source+'\nexports.doGen=doGen;',{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS})
test('全局已通知后处理未完成，素材页只刷新而不追加成功提示',async()=>{
  const notices=[];let refreshes=0
  const context={exports:{},genning:{value:''},isChatGPTQueue:{value:false},app:{current:'甲'},selectedVendor:{value:{id:'本地'}},
    genAssetImage:async()=>({id:1}),trackJob:async()=>({success:true,out:'[后处理未完成] 布局不明确'}),
    toast:(...args)=>notices.push(args),load:async()=>{refreshes++},Error,
  }
  vm.runInNewContext(js,context)
  await context.exports.doGen('character','甲')
  assert.equal(notices.length,0)
  assert.equal(refreshes,1)
  assert.equal(context.genning.value,'')
})
