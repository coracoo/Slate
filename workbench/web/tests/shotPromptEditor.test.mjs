import test from 'node:test'
import assert from 'node:assert/strict'
import { defaultShotPrompt, retimeUnit } from '../src/utils/shotPromptEditor.ts'

test('S 时间更新联动 V 总长和标签，保留手写正文', () => {
  const shots = [{id:'S1',dur:4,prompt_video:'【S1镜（0.0—2.0s）：用户内容】'}, {id:'S2',dur:3,prompt_video:'【S2镜（2.0—5.0s）：动作不变】'}]
  const u={shot_ids:['S1','S2'],duration:5}
  retimeUnit(u,shots)
  assert.equal(u.duration,7)
  assert.equal(shots[1].prompt_video,'【S2镜（4.0—7.0s）：动作不变】')
  assert.deepEqual(u.timeline.map(x=>[x.start,x.end]),[[0,4],[4,7]])
})
test('从分镜结构填默认值，标准格式人工内容保持原样', () => {
  const shot={id:'S1',dur:2,shot_size:'全景',angle:'平视',lens:'35mm',camera_move:'横移',content:'集市',action:'拍手',sound:'人声',lighting:'晨光',lines:[{speaker:'小师妹',line:'都要'}]}
  const text=defaultShotPrompt(shot,'prompt_video',0)
  for(const part of ['0.0—2.0s','全景','平视','35mm','横移','集市','拍手','人声','晨光','都要']) assert.ok(text.includes(part))
  assert.equal(defaultShotPrompt({...shot,prompt_video:text},'prompt_video',0),text)
})
