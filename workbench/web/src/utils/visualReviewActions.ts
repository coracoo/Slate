/** 选择数量不等于可提交数量；补齐与保存各自使用确定的范围。 */
export function visualReviewActions(selected:string[], patches:Record<string,unknown>, missing:string[], attention:string[], confirmable:string[]=[]) {
  const ids=[...new Set(selected)]
  const saveIds=ids.filter(id=>Boolean(patches[id]))
  const fillIds=ids.filter(id=>!patches[id] && missing.includes(id))
  const attentionIds=ids.filter(id=>!patches[id] && !missing.includes(id) && attention.includes(id))
  const confirmIds=ids.filter(id=>confirmable.includes(id))
  const kind=saveIds.length || confirmIds.length?'save':fillIds.length?'fill':'none'
  const label=kind==='save'?(confirmIds.length?`确认所选设定（${confirmIds.length} 项）`:`确认保存修改（${saveIds.length} 项）`):kind==='fill'?`补齐所选缺项（${fillIds.length} 项）`:attentionIds.length?'请先核对派生问题':ids.length?'所选设定无需重复确认':'请选择要确认的设定'
  return {kind,label,saveIds,fillIds,attentionIds,confirmIds}
}

/** 已确认且未修改的设定不再进入确认请求；修改或未确认项按当前校验处理。 */
export function pendingConfirmationIds(selected:string[], statuses:Record<string,{ready?:boolean;review_status?:string}>, changed:string[]=[]) {
  return selected.filter(id=>changed.includes(id) || statuses[id]?.ready && statuses[id]?.review_status!=='confirmed')
}

/** 身份对应尚未决定时仍可编辑，但不能随设定全选进入采用范围。 */
export function identitySelectionReasons(changes:Array<{id:string;matches:string[];existing?:boolean;batch?:string}>, drafts:Record<string,{action?:string;target?:string;label?:string;difference?:string}>) {
  const reasons:Record<string,string>={}
  for(const item of changes) {
    const choice=drafts[item.id]
    if(!choice || !['new','reuse','derived'].includes(choice.action || '')) reasons[item.id]='先选择下方对应关系，再勾选采用；暂不处理不影响其它设定。'
    else if(choice.action!=='new' && !item.matches.includes(choice.target || '')) reasons[item.id]='请选择对应的已有素材。'
    else if(choice.action==='derived' && (!choice.label?.trim() || !choice.difference?.trim())) reasons[item.id]='派生状态须填写名称和可见差异。'
  }
  for(const batch of new Set(changes.filter(i=>!i.existing && i.batch).map(i=>i.batch))) {
    const members=changes.filter(i=>i.batch===batch), pending=members.filter(i=>reasons[i.id])
    if(pending.length) for(const item of members) reasons[item.id] ||= `这批关联剧情还有 ${pending.length} 项对应关系待确定；完整后一起采用。`
  }
  return reasons
}
