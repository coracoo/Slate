import type { ProductionShot, VideoUnit } from './productionStudio'

/** 默认文案直接读取分镜字段；已有标准格式的人工文本不重写。 */
export function defaultShotPrompt(s: ProductionShot, field: 'prompt_image' | 'prompt_video' | 'prompt_grid', start: number) {
  const text = s[field] || (field === 'prompt_image' ? s.prompt : '') || ''
  if (text.trimStart().startsWith('【')) return text
  const lines = s.lines?.map(l => `${l.speaker || ''}：“${l.line || l.text || ''}”`).join('；')
  const parts = [s.shot_size, s.angle, s.lens, s.camera_move, text || s.content, s.action, s.sound, lines, s.lighting || s.light]
  return `【${s.id}镜（${start.toFixed(1)}—${(start + s.dur).toFixed(1)}s）：${parts.filter(Boolean).join('；')}】`
}

export function defaultUnitPrompt(u: VideoUnit, all: ProductionShot[], field: 'prompt_video' | 'prompt_grid') {
  const text = u[field] || ''
  if (/【S[^）]*镜（/.test(text)) return text
  const sections = u.shot_ids.map(id => all.find(s => s.id === id)?.[field] || '').filter(Boolean)
  return sections.join('\n') + (text ? '\n整段补充：' + text : '')
}

/** 修改 S 后重算 V 总时长及时间标签，正文保持不变。 */
export function retimeUnit(u: VideoUnit, all: ProductionShot[]) {
  let at = 0
  u.timeline = u.shot_ids.map(id => {
    const s = all.find(s => s.id === id)!
    const start = at; at = Math.round((at + s.dur) * 1000) / 1000
    for (const field of ['prompt_image', 'prompt_video', 'prompt_grid'] as const) {
      const value = s[field] || ''
      const prefix = `【${s.id}镜（`
      if (value.startsWith(prefix)) s[field] = value.replace(/^【[^）]*）：/, `${prefix}${start.toFixed(1)}—${at.toFixed(1)}s）：`)
    }
    const escaped = id.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    for (const field of ['prompt_video', 'prompt_grid'] as const) {
      u[field] = (u[field] || '').replace(new RegExp(`【${escaped}镜（[^）]*）：`, 'g'), `【${id}镜（${start.toFixed(1)}—${at.toFixed(1)}s）：`)
    }
    return {shot_id: id, start, end: at}
  })
  u.duration = at; u.stale = true
}
