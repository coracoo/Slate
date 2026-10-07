import test from 'node:test'
import assert from 'node:assert/strict'
import {visualReviewActions, identitySelectionReasons, pendingConfirmationIds} from '../src/utils/visualReviewActions.ts'

test('再次打开已确认设定无需重复确认，真正修改的项仍可提交',()=>{
  const statuses={base:{ready:true,review_status:'confirmed'},state:{ready:true,review_status:'confirmed'},fresh:{ready:true,review_status:'unconfirmed'},invalid:{ready:false}}
  assert.deepEqual(pendingConfirmationIds(['base','state','fresh','invalid'],statuses),['fresh'])
  assert.deepEqual(pendingConfirmationIds(['base','state'],statuses,['state']),['state'])
  assert.equal(visualReviewActions(['base','state'],{},[],[],pendingConfirmationIds(['base','state'],statuses)).kind,'none')
})

test('只选空缺时主操作补齐，不空提交',()=>{
  const plan=visualReviewActions(['a','@prop:p'],{},['a','@prop:p'],[])
  assert.equal(plan.kind,'fill')
  assert.deepEqual(plan.fillIds,['a','@prop:p'])
  assert.equal(plan.label,'补齐所选缺项（2 项）')
})
test('混选修改、空缺、派生问题按实际范围分开',()=>{
  const plan=visualReviewActions(['a','b','c','d'],{a:{appearance:{face:'窄脸'}}},['a','b'],['c'])
  assert.equal(plan.kind,'save')
  assert.deepEqual(plan.saveIds,['a'])
  assert.deepEqual(plan.fillIds,['b'])
  assert.deepEqual(plan.attentionIds,['c'])
  assert.equal(plan.label,'确认保存修改（1 项）')
})
test('无改动和单纯关联异常均不能发送空保存或无效补齐',()=>{
  for(const selected of [[],['valid'],['derived']]) {
    const plan=visualReviewActions(selected,{},[],['derived'])
    assert.equal(plan.kind,'none')
    assert.deepEqual(plan.fillIds,[])
    assert.deepEqual(plan.saveIds,[])
  }
})

test('完整且无修改的母图与派生可批量确认',()=>{
  const plan=visualReviewActions(['a','state-a'],{},[],[],['a','state-a'])
  assert.equal(plan.kind,'save')
  assert.deepEqual(plan.confirmIds,['a','state-a'])
  assert.equal(plan.label,'确认所选设定（2 项）')
})

test('疑似重复素材须明确对应关系，未判定不能随全选进入提交',()=>{
  const changes=[{id:'old',matches:['@scene:now'],existing:true}]
  assert.ok(identitySelectionReasons(changes,{}).old)
  assert.deepEqual(identitySelectionReasons(changes,{old:{action:'new'}}),{})
  assert.ok(identitySelectionReasons(changes,{old:{action:'reuse',target:''}}).old)
  assert.deepEqual(identitySelectionReasons(changes,{old:{action:'reuse',target:'@scene:now'}}),{})
  assert.ok(identitySelectionReasons(changes,{old:{action:'derived',target:'@scene:now',label:'夜间'}}).old)
  assert.deepEqual(identitySelectionReasons(changes,{old:{action:'derived',target:'@scene:now',label:'夜间',difference:'蓝色夜灯'}}),{})
})

test('新候选关联剧情按批采用，不完整批次保持待核对且不拦正常设定',()=>{
  const changes=[{id:'i1',matches:['@scene:a'],batch:'b'},{id:'i2',matches:['@scene:b'],batch:'b'}]
  const partial=identitySelectionReasons(changes,{i1:{action:'new'}})
  assert.ok(partial.i1)
  assert.ok(partial.i2)
  assert.deepEqual(identitySelectionReasons(changes,{i1:{action:'new'},i2:{action:'new'}}),{})
})
