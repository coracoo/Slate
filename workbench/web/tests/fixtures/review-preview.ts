import { createApp, h, ref } from 'vue'
import BatchDiffReview from '../../src/components/BatchDiffReview.vue'
import '../../src/styles/main.css'

createApp({setup() {
  const open=ref(false), result=ref('尚未提交')
  const items=[
    {id:'a',title:'角色甲',groups:[{label:'脸型与五官',before:'外观尚未确认',after:'略窄长脸；眉峰不对称，左眼眼尾略低；鼻梁较直，下颌线略尖。'},
      {label:'发型与发质',before:'',after:'短卷发，额前有自然碎发。'}]},
    {id:'b',title:'角色乙',groups:[{label:'服装',before:'旧灰衣',after:'深蓝短袍，领口磨损，右肩一处补丁。'}]},
    {id:'c',title:'待补全角色',disabledReason:'尚无外观建议，请先补全角色设定',groups:[]},
  ]
  return ()=>h('main',{class:'p-6 text-slate-100'},[
    h('h1',{class:'mb-4 text-xl'},'批量审核界面检查'),
    h('button',{class:'btn',onClick:()=>{open.value=true}},'批量审核外观'),h('p',{class:'mt-4'},result.value),
    h(BatchDiffReview,{open:open.value,title:'批量审核角色外观',items,'onUpdate:open':(value:boolean)=>{open.value=value},onConfirm:(ids:string[])=>{result.value=`已确认：${ids.join('、')}`;open.value=false}}),
  ])
}}).mount('#app')
