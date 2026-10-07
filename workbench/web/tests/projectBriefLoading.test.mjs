import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'

// 抽取真实页面的加载与保存函数，用延迟接口核对状态转移，不启动页面。
const source=fs.readFileSync(new URL('../src/views/StudioView.vue',import.meta.url),'utf8')
const helpers=source.slice(source.indexOf('function fillBriefForm'),source.indexOf('/** 可空数字字段'))
  +source.slice(source.indexOf('async function saveBriefForm'),source.indexOf('async function refreshWorkflow'))
  +source.slice(source.indexOf('let loadSeq'),source.indexOf('watch(() => app.current'))
const js=ts.transpile(helpers+'\nexports.load=load;exports.saveBriefForm=saveBriefForm;',{
  target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,
})
function fixture(fetchBrief) {
  const cell=value=>({value}), writes=[], notices=[]
  const context={exports:{},app:{current:'乙'},data:cell(null),detail:cell(null),loading:cell(false),
    briefReady:cell(true),briefLoadError:cell(''),briefSaving:cell(false),briefBaseline:cell('旧基线'),
    briefForm:cell({episode_minutes:9,total_episodes:99,aspect_ratio:'9:16',genre_tone:'甲规则',dialogue_density:'高',max_characters:20,max_scenes:15}),
    scriptText:cell(''),idea:cell(''),mode:cell('idea'),workspaceTab:cell('setup'),workflowRevision:cell(0),
    localStorage:{getItem:()=>null},fetchBrief,fetchScriptData:async()=>({script:'',episodes:[],prompt_flow:{}}),
    saveBrief:async (project,patch)=>{writes.push({project,patch});return {brief:patch}},
    refreshWorkflow:async()=>{},toast:(...args)=>notices.push(args),
    nullIfEmpty:value=>value===''?null:Number(value),Error,JSON,Number,
  }
  vm.runInNewContext(js,context)
  return {...context,writes,notices}
}

test('新项目规则读取失败时清空旧项目值，并阻止写入默认值', async () => {
  const f=fixture(async()=>{throw new Error('暂时不可用')})
  await f.exports.load()
  assert.equal(f.briefForm.value.total_episodes,'')
  assert.equal(f.briefForm.value.genre_tone,'')
  assert.equal(f.briefReady.value,false)
  assert.equal(f.briefLoadError.value,'暂时不可用')
  assert.equal(await f.exports.saveBriefForm(),false)
  assert.equal(f.writes.length,0)
})

test('读取重试成功后只允许保存当前项目规则', async () => {
  let failed=true
  const f=fixture(async project=>{if(failed) throw new Error('暂时不可用');return {brief:{total_episodes:8,genre_tone:project+'规则'}}})
  await f.exports.load();failed=false;await f.exports.load()
  assert.equal(f.briefReady.value,true)
  assert.equal(f.briefLoadError.value,'')
  assert.equal(await f.exports.saveBriefForm(),true)
  assert.equal(f.writes[0].project,'乙')
  assert.equal(f.writes[0].patch.total_episodes,8)
  assert.equal(f.writes[0].patch.genre_tone,'乙规则')
})
