import type { AssetRegistryItem } from '../api'

/** 展示层级与生图来源独立，派生引用保留精确状态。 */
export function generationReference(row: AssetRegistryItem, assets: AssetRegistryItem[]) {
  const source = row.derived_from || row.parent_ref || ''
  const [base, stateId] = source.split('#')
  const mother = assets.find(asset => asset.ref === base)
  if (!mother) return undefined
  if (!stateId) return mother
  const state = mother.states?.find(item => item.id === stateId)
  return state ? { ...mother, ref: source, name: `${mother.name} · ${state.label}`, path: state.path || '' } : undefined
}

export function generationReferenceChoices(assets: AssetRegistryItem[], selfRef = '') {
  const choices: Record<string, string> = { '': '跟随展示归属的母图' }
  for (const asset of assets) {
    if (asset.kind === 'style' || asset.usage === 'plan' || asset.ref === selfRef) continue
    choices[asset.ref] = asset.name
    for (const state of asset.states || []) choices[`${asset.ref}#${state.id}`] = `${asset.name} · ${state.label}（派生图）`
  }
  return choices
}
