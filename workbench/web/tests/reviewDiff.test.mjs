import test from 'node:test'
import assert from 'node:assert/strict'
import { changedGroups, appearanceReview, mergeReviewedRows, visualReviewDrafts, characterVisualReview, reviewSelectionIds, stateSelectionId, selectedVisualPatches, selectedVisualConfirmations, pendingReviewItems } from '../src/utils/reviewDiff.ts'

test('确认后的母素材和派生退出待审核列表，保留未确认项与详情编辑入口',()=>{
  const items=[{id:'a',title:'甲',groups:[],children:[{id:'s',title:'派生'}]},{id:'b',title:'乙',groups:[]}]
  assert.deepEqual(pendingReviewItems(items,{a:'confirmed',s:'confirmed',b:'unconfirmed'}).map(i=>i.id),['b'])
  assert.deepEqual(pendingReviewItems(items,{a:'confirmed',s:'unconfirmed',b:'confirmed'})[0].children.map(i=>i.id),['s'])
  assert.deepEqual(pendingReviewItems(items,{a:'confirmed',s:'confirmed',b:'confirmed'},[],'a').map(i=>i.id),['a'])
  assert.deepEqual(pendingReviewItems(items,{a:'confirmed',s:'confirmed',b:'confirmed'},['b']).map(i=>i.id),['b'])
})

test('确认范围明确区分母图和派生，未修改也能确认且不扩大选择',()=>{
  const items=[{id:'a',title:'甲',groups:[],children:[{id:stateSelectionId('a','s1'),title:'受伤'},{id:stateSelectionId('a','s2'),title:'醒悟'}]}]
  assert.deepEqual(selectedVisualConfirmations([stateSelectionId('a','s1')],items),{a:{base:false,states:['s1']}})
  assert.deepEqual(selectedVisualConfirmations(reviewSelectionIds(items),items),{a:{base:true,states:['s1','s2']}})
  assert.deepEqual(selectedVisualConfirmations([],items),{})
})

test('全选包含人物、场景、道具的派生；部分选择只提交选中状态且保留母图复用决定',()=>{
  for(const id of ['hero','@scene:room','@prop:flag']) {
    const original=[{id:'day',look_diff:'原日间'},{id:'night',look_diff:'原夜间',output_asset_ref:'@character:hero'}]
    const edits=[{...original[0],look_diff:'新日间'},{...original[1],look_diff:'新夜间'}]
    const children=original.map(s=>({id:stateSelectionId(id,s.id),title:s.id}))
    const items=[{id,title:id,groups:[],children}]
    const patches={[id]:{visual_description:'新母图',states:edits}}
    const all=reviewSelectionIds(items)
    assert.deepEqual(all,[id,...children.map(c=>c.id)])
    assert.deepEqual(selectedVisualPatches(all,patches,{[id]:original})[id],patches[id])
    const partial=selectedVisualPatches([children[0].id],patches,{[id]:original})[id]
    assert.equal(partial.visual_description,undefined)
    assert.deepEqual(partial.states,[edits[0],original[1]])
    assert.deepEqual(selectedVisualPatches([id],patches,{[id]:original})[id],{visual_description:'新母图'})
    assert.deepEqual(original[0],{id:'day',look_diff:'原日间'})
  }
})

test('全选跳过不可选项及其派生，无修改的派生不制造提交',()=>{
  assert.deepEqual(reviewSelectionIds([{id:'a',title:'甲',groups:[],disabledReason:'已过期',children:[{id:'child',title:'派生'}]}]),[])
  assert.deepEqual(selectedVisualPatches([stateSelectionId('a','s')],{a:{states:[{id:'s',look_diff:'原'}]}},{a:[{id:'s',look_diff:'原'}]}),{})
})

test('全选设定及派生时，未决定对应关系的疑似重复素材不混入提交',()=>{
  const items=[{id:'hero',title:'主角',groups:[],children:[{id:stateSelectionId('hero','hurt'),title:'受伤'}]},
    {id:'existing-identity-scene:old',title:'旧场景',groups:[],selectionBlockedReason:'待核对对应关系'},
    {id:'existing-identity-prop:old',title:'旧道具',groups:[],defaultSelected:false}]
  assert.deepEqual(reviewSelectionIds(items),['hero',stateSelectionId('hero','hurt'),'existing-identity-prop:old'])
  assert.deepEqual(selectedVisualConfirmations(reviewSelectionIds(items),items),{hero:{base:true,states:['hurt']}})
  items[1].selectionBlockedReason=undefined
  assert.ok(reviewSelectionIds(items).includes('existing-identity-scene:old'))
})

test('建议存于现有字段时，无需改字也可确认采用来源',()=>{
  const rows=[{id:'a',name:'甲',appearance:{face:'方脸',sources:{face:'proposal'}},visual_status:{ready:false,pending_fields:['face'],missing_fields:[],field_labels:['脸型与五官']}}]
  const review=characterVisualReview(rows,visualReviewDrafts(rows))
  assert.equal(review.patches.a.appearance.sources.face,'authored')
  assert.match(review.items[0].groups[0].label,/确认采用现有建议/)
  assert.equal(rows[0].appearance.sources.face,'proposal')
})

test('审核只显示变更字段，保留数值和空值差异', () => {
  const groups = changedGroups({name:'甲', duration:2, prompt:''}, {name:'甲', duration:3.5, prompt:'新增'}, {duration:'时长',prompt:'提示词'})
  assert.deepEqual(groups.map(g => g.label), ['时长','提示词'])
  assert.equal(groups[0].before, '2')
  assert.equal(groups[0].after, '3.5')
})

test('外观批量审核只采用待确认建议，不污染原档案或覆盖已确认五官', () => {
  const rows = [{id:'a',name:'甲',appearance:{face:'已确认窄脸',proposals:{face:'圆脸',hair:'短卷发'}}},
    {id:'b',name:'乙',appearance:{proposals:{outfit:'灰衣'}}}, {id:'narrator',name:'旁白',reserved:true}]
  const original = structuredClone(rows)
  const result = appearanceReview(rows)
  assert.deepEqual(rows, original)
  assert.equal(result.items.length, 2)
  assert.equal(result.patches.a.appearance.hair, '短卷发')
  assert.equal(result.patches.a.appearance.face, undefined)
  assert.match(result.items[0].groups[0].label, /发型/)
})

test('部分确认只提交选中分镜，未选修改继续保留在编辑器', () => {
  const before=[{id:'S1',prompt:'原一'},{id:'S2',prompt:'原二'}], edited=[{id:'S1',prompt:'新一'},{id:'S2',prompt:'新二'}]
  assert.deepEqual(mergeReviewedRows(before,edited,['S2']), [{id:'S1',prompt:'原一'},{id:'S2',prompt:'新二'}])
  assert.equal(edited[0].prompt,'新一')
})

test('没有母图建议也能在同一批次修改派生，不把校验结果写入档案', () => {
  const rows=[{id:'a',name:'甲',states:[{id:'a_s1',label:'持弩',look_diff:'持弩随后脱手',visual_status:{ready:false,warnings:['前后动作']}}]}]
  const drafts=visualReviewDrafts(rows)
  assert.equal(characterVisualReview(rows,drafts).items.length,1)
  assert.deepEqual(characterVisualReview(rows,drafts).patches,{})
  drafts.a.states[0].look_diff='手持连弩'
  const review=characterVisualReview(rows,drafts)
  assert.equal(review.patches.a.states[0].look_diff,'手持连弩')
  assert.equal(review.patches.a.states[0].visual_status,undefined)
  assert.equal(review.patches.a.appearance,undefined)
  assert.match(review.items[0].groups[0].label,/派生 · 持弩 · 可见变化/)
  assert.equal(rows[0].states[0].look_diff,'持弩随后脱手')
})

test('母图建议与派生复用合成一个角色提交，未改角色不重写', () => {
  const rows=[{id:'a',name:'甲',appearance:{proposals:{hair:'短发'}},states:[{id:'s1',label:'醒悟',look_diff:'决定反抗'}]},
    {id:'b',name:'乙',states:[{id:'s2',look_diff:'红衣'}]}]
  const drafts=visualReviewDrafts(rows)
  drafts.a.states[0].output_asset_ref='@character:a'
  const review=characterVisualReview(rows,drafts)
  assert.deepEqual(Object.keys(review.patches),['a'])
  assert.equal(review.patches.a.appearance.hair,'短发')
  assert.equal(review.patches.a.states[0].output_asset_ref,'@character:a')
  assert.ok(review.items[0].groups.some(g=>g.after==='复用母图'))
  assert.equal(rows[0].states[0].output_asset_ref,undefined)
})

test('批量审核保留角色页的派生草稿和其它状态，匹配键变动进入差异', () => {
  const rows=[{id:'a',name:'甲',states:[{id:'s1',look_diff:'旧',episodes:['旧场景']},{id:'s2',look_diff:'不变',locked_fields:['look_diff']}]}]
  const edited=structuredClone(rows)
  edited[0].states[0].look_diff='人工修改'
  edited[0].states[0].episodes=['E2','@scene:room']
  const result=characterVisualReview(rows,visualReviewDrafts(rows,edited))
  assert.equal(result.patches.a.states.length,2)
  assert.deepEqual(result.patches.a.states[1],rows[0].states[1])
  assert.ok(result.items[0].groups.some(g=>g.label.includes('对应分集或场景')))
})

test('无建议无派生且外观为 null 的缺项角色仍进入审核，可直接补填', () => {
  const rows=[{id:'a',name:'缺项角色',appearance:null,visual_status:{ready:false,field_labels:['脸型与五官'],missing_fields:['face'],pending_fields:[]}}]
  const drafts=visualReviewDrafts(rows)
  let review=characterVisualReview(rows,drafts)
  assert.equal(review.items.length,1)
  assert.match(review.items[0].note,/脸型与五官/)
  assert.deepEqual(review.patches,{})
  drafts.a.appearance.face='瘦长脸'
  drafts.a.appearance.sources.face='authored'
  review=characterVisualReview(rows,drafts)
  assert.equal(review.patches.a.appearance.face,'瘦长脸')
  assert.equal(rows[0].appearance,null)
})
