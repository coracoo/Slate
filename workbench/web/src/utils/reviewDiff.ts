import { appearanceFields, appearanceDraft, appearancePatch, adoptPendingAppearanceProposals } from './characterAppearance.ts'
import type { CharacterAppearance } from './characterAppearance.ts'
import type { CharacterProfile } from './characterProfiles.ts'

export interface ReviewGroup { label: string; before: string; after: string }
export interface ReviewItem { id: string; title: string; groups: ReviewGroup[]; note?: string; disabledReason?: string; selectionBlockedReason?:string; defaultSelected?: boolean; children?:Array<{id:string;title:string}> }

/** 待审核列表只保留未确认或有编辑的项；详情入口仍可打开已确认档案。 */
export function pendingReviewItems(items:ReviewItem[], statuses:Record<string,string|undefined>, edited:string[]=[], detailOwner='') {
  return items.flatMap(item=>{
    if(item.id===detailOwner) return [item]
    const children=(item.children || []).filter(child=>statuses[child.id]!=='confirmed' || edited.includes(child.id))
    const basePending=statuses[item.id]!=='confirmed' || edited.includes(item.id)
    return basePending || children.length?[{...item,children,defaultSelected:basePending}]:[]
  })
}

export const stateSelectionId=(owner:string,state:string)=>'state:'+JSON.stringify([owner,state])
export function reviewSelectionIds(items:ReviewItem[]) {
  return items.filter(item=>!item.disabledReason && !item.selectionBlockedReason).flatMap(item=>[item.id,...(item.children || []).map(child=>child.id)])
}

export function selectedVisualPatches(selected:string[], patches:Record<string,Record<string,unknown>>, originals:Record<string,Array<{id:string}>>) {
  const chosen=new Set(selected), result:Record<string,Record<string,unknown>>={}
  for(const [owner,patch] of Object.entries(patches)) {
    const {states,...fields}=patch
    const value:Record<string,unknown>=chosen.has(owner)?{...fields}:{}
    if(Array.isArray(states) && originals[owner]) {
      const next=originals[owner].map(old=>chosen.has(stateSelectionId(owner,old.id))?(states.find(s=>s.id===old.id) || old):old)
      if(reviewText(next)!==reviewText(originals[owner])) value.states=structuredClone(next)
    }
    if(Object.keys(value).length) result[owner]=value
  }
  return result
}

export function selectedVisualConfirmations(selected:string[], items:ReviewItem[]) {
  const chosen=new Set(selected), result:Record<string,{base:boolean;states:string[]}>={}
  for(const item of items) {
    if(item.disabledReason || item.selectionBlockedReason || /^(?:existing-identity-|identity-|reuse:)/.test(item.id)) continue
    const states=(item.children || []).filter(s=>chosen.has(s.id)).map(s=>JSON.parse(s.id.slice(6))[1] as string)
    if(chosen.has(item.id) || states.length) result[item.id]={base:chosen.has(item.id),states}
  }
  return result
}

export function reviewText(value: unknown): string {
  if (value == null) return ''
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2)
}

export function changedGroups(before: Record<string, unknown>, after: Record<string, unknown>, labels: Record<string, string> = {}): ReviewGroup[] {
  return [...new Set([...Object.keys(before), ...Object.keys(after)])]
    .filter(key => reviewText(before[key]) !== reviewText(after[key]))
    .map(key => ({label: labels[key] || key, before: reviewText(before[key]), after: reviewText(after[key])}))
}

export function mergeReviewedRows<T extends {id:string}>(before:T[], after:T[], selected:string[]):T[] {
  const chosen=new Set(selected), edited=new Map(after.map(row=>[row.id,row]))
  return before.map(row=>structuredClone(chosen.has(row.id) ? edited.get(row.id) || row : row))
}

export function appearanceReview(rows: Array<{id: string; name: string; reserved?: boolean; appearance?: CharacterAppearance}>) {
  const items: ReviewItem[] = [], patches: Record<string, {appearance: CharacterAppearance}> = {}
  for (const row of rows) {
    if (row.reserved) continue
    const before = appearanceDraft(row.appearance), after = structuredClone(before)
    if (!adoptPendingAppearanceProposals(after)) continue
    const groups = appearanceFields.filter(field => before[field.key] !== after[field.key] || before.sources[field.key] !== after.sources[field.key])
      .map(field => ({label: field.label, before: reviewText(before[field.key]), after: reviewText(after[field.key])}))
    patches[row.id] = {appearance: appearancePatch(after, before)}
    items.push({id: row.id, title: row.name, groups})
  }
  return {items, patches}
}

export function visualReviewDrafts(rows: CharacterProfile[], editedRows: CharacterProfile[] = rows) {
  return Object.fromEntries(rows.filter(row=>!row.reserved).map(row=>{
    const edited=editedRows.find(item=>item.id===row.id) || row
    const appearance=appearanceDraft(edited.appearance)
    adoptPendingAppearanceProposals(appearance)
    for(const key of row.visual_status?.pending_fields || []) {
      if(appearanceFields.some(field=>field.key===key) && String(appearance[key] ?? '').trim() && appearance.sources[key]==='proposal') appearance.sources[key]='authored'
    }
    const states=(edited.states || []).map(({visual_status, ...state})=>JSON.parse(JSON.stringify(state)) as typeof state)
    return [row.id, {appearance, states}]
  }))
}

export function characterVisualReview(rows: CharacterProfile[], drafts: ReturnType<typeof visualReviewDrafts>) {
  const items:ReviewItem[]=[], patches:Record<string,Record<string,unknown>>={}
  for(const row of rows) {
    const draft=drafts[row.id]
    if(row.reserved || !draft) continue
    const before=appearanceDraft(row.appearance), after=draft.appearance
    const patch:Record<string,unknown>={}, groups:ReviewGroup[]=[]
    const appearance=appearancePatch(after,before)
    if(Object.keys(appearance).length) {
      patch.appearance=appearance
      groups.push(...appearanceFields.filter(f=>before[f.key]!==after[f.key] || before.sources[f.key]!==after.sources[f.key])
        .map(f=>({label:'母图 · '+f.label+(before[f.key]===after[f.key]?' · 确认采用现有建议':''),before:reviewText(before[f.key]),after:reviewText(after[f.key])})))
    }
    for(const state of draft.states) {
      const old=row.states?.find(s=>s.id===state.id)
      if(!old) continue
      const labels={look_diff:'可见变化',sheet_prompt:'外观补充',episodes:'对应分集或场景',output_asset_ref:'图片来源'}
      for(const [key,label] of Object.entries(labels)) {
        const field=key as keyof typeof labels
        const previous=old[field] || (field==='episodes'?[]:''), current=state[field] || (field==='episodes'?[]:'')
        if(reviewText(previous)===reviewText(current)) continue
        groups.push({label:`派生 · ${state.label || state.id} · ${label}`,
          before:field==='output_asset_ref'?(previous?'复用母图':'独立派生图'):reviewText(previous),
          after:field==='output_asset_ref'?(current?'复用母图':'独立派生图'):reviewText(current)})
        patch.states=draft.states
      }
    }
    if(Object.keys(patch).length) patches[row.id]=patch
    items.push({id:row.id,title:row.name,groups,
      children:draft.states.map(s=>({id:stateSelectionId(row.id,s.id),title:'派生 · '+(s.label || s.id)})),
      note:row.visual_status?.ready===false?'需补齐或确认：'+row.visual_status.field_labels.join('、'):undefined})
  }
  return {items,patches}
}
