/** 角色外观编辑模型；只提交改动字段，来源与建议保留独立状态。 */
export const appearanceFields = [
  {key:'species', label:'物种与形态', hint:'人类、异兽或其他形态'},
  {key:'age', label:'年龄或年龄段', hint:'保留原文；不详时留空或写未知'},
  {key:'face', label:'脸型与五官', hint:'脸型、眼距、眉眼、鼻与下颌的个体差异'},
  {key:'skin', label:'肤色与皮肤特征', hint:'肤色、雀斑、细纹等已有设定'},
  {key:'hair', label:'发型与发质', hint:'发际线、发量、分缝、卷曲与自然碎发'},
  {key:'height', label:'身高', hint:'原文数值及单位，或已有的高矮描述'},
  {key:'body_type', label:'体型', hint:'骨架、肌肉与体态'},
  {key:'body_proportions', label:'身材比例', hint:'肩胯、躯干与腿长比例'},
  {key:'waist', label:'腰围（成年人适用）', hint:'仅填已有设定，保留原数值和单位'},
  {key:'hips', label:'臀围（成年人适用）', hint:'年龄不明或未成年不用于生图测量描述'},
  {key:'facial_hair', label:'胡须', hint:'须型、密度或明确无须；异兽按物种描述'},
  {key:'headwear', label:'头饰', hint:'头饰形制、材质与佩戴位置'},
  {key:'distinctive_features', label:'可辨识特征', hint:'不依赖服装的稳定识别点'},
  {key:'look', label:'外貌补充（兼容旧档案）', hint:'保留已有综合描述'},
  {key:'outfit', label:'服装', hint:'基准阶段服装，剧情状态可单独变化'},
] as const
type AppearanceValue = string | number | null
export type CharacterAppearance = Record<string, unknown> & {
  sources?: Record<string, string>
  proposals?: Record<string, AppearanceValue>
}
export type AppearanceDraft = Record<string, any> & {
  sources: Record<string, string>
  proposals: Record<string, AppearanceValue>
}

export function appearanceDraft(value: CharacterAppearance | null = {}): AppearanceDraft {
  value = value || {}
  return {
    ...Object.fromEntries(appearanceFields.map(field => [field.key, value[field.key] ?? ''])),
    sources: {...value.sources}, proposals: {...value.proposals},
  }
}

export function appearancePatch(value: AppearanceDraft, original: AppearanceDraft): CharacterAppearance {
  const patch: CharacterAppearance = {}, sources: Record<string, string> = {}
  for (const {key} of appearanceFields) {
    if (value[key] !== original[key]) {
      patch[key] = value[key]
      sources[key] = value.sources[key] !== original.sources[key] ? value.sources[key] || 'authored' : 'authored'
    } else if (value.sources[key] !== original.sources[key]) {
      sources[key] = value.sources[key] || 'unknown'
    }
  }
  if (Object.keys(sources).length) patch.sources = sources
  const proposals = Object.fromEntries(Object.entries(value.proposals).filter(([key, item]) => item !== original.proposals[key]))
  if (Object.keys(proposals).length) patch.proposals = proposals
  return patch
}

export function adoptAppearanceProposal(value: AppearanceDraft, key: string) {
  if (!appearanceFields.some(field => field.key === key) || value.proposals[key] == null) return
  value[key] = value.proposals[key]
  value.sources[key] = 'authored'
  value.proposals[key] = null
}

export function adoptPendingAppearanceProposals(value: AppearanceDraft) {
  let count = 0
  for (const {key} of appearanceFields) {
    const proposal = value.proposals[key]
    if (proposal == null || String(proposal).trim() === '') continue
    if (value[key] != null && String(value[key]).trim() && !['proposal', 'unknown'].includes(value.sources[key] || '')) continue
    adoptAppearanceProposal(value, key)
    count++
  }
  return count
}
