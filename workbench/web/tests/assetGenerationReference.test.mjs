import test from 'node:test'
import assert from 'node:assert/strict'
import { generationReference, generationReferenceChoices } from '../src/utils/assetGenerationReference.ts'

const mother = {ref:'@character:hero',id:'hero',kind:'character',name:'主角',path:'mother.png',states:[{id:'armed',label:'持弩',path:'armed.png'},{id:'other',label:'其他',path:''}]}
const child = {ref:'@prop:bow',id:'bow',kind:'prop',name:'连弩',parent_ref:mother.ref,derived_from:mother.ref+'#armed'}

test('子素材仍归母素材，生成预览指向精确派生图', () => {
  const ref=generationReference(child,[mother,child])
  assert.equal(ref.path,'armed.png')
  assert.equal(ref.name,'主角 · 持弩')
  assert.equal(child.parent_ref,mother.ref)
  assert.equal(generationReference({...child,derived_from:''},[mother]).path,'mother.png')
})

test('未知或缺失的派生图不以母图伪装为参考', () => {
  assert.equal(generationReference({...child,derived_from:mother.ref+'#missing'},[mother]),undefined)
  assert.equal(generationReference({...child,derived_from:mother.ref+'#other'},[mother]).path,'')
  const choices=generationReferenceChoices([mother,child],child.ref)
  assert.equal(choices[mother.ref+'#armed'],'主角 · 持弩（派生图）')
  assert.equal(choices[child.ref],undefined)
})
