// -*- coding: utf-8 -*-
/** 制作线 UI 的纯函数：下拉选择记忆与按分镜归档画廊。 */

export interface GalleryEntryLike {
  board?: string
  shot_id?: string
  panel_id?: string
}

export interface GalleryGroup<T extends GalleryEntryLike = GalleryEntryLike> {
  key: string
  label: string
  board: string
  shot_id: string
  panel_id: string
  entries: T[]
}

/** 把创作产出按“分镜板 · 镜号 · 面板”分组，确保不同镜头不会混在同一栏。 */
export function groupGalleryEntries<T extends GalleryEntryLike>(items: readonly T[]): GalleryGroup<T>[] {
  const groups = new Map<string, GalleryGroup<T>>()
  for (const item of items) {
    const board = String(item.board || '自由创作').trim() || '自由创作'
    const shot = String(item.shot_id || '未关联分镜').trim() || '未关联分镜'
    const panel = String(item.panel_id || '').trim()
    const key = `${board}::${shot}::${panel}`
    let group = groups.get(key)
    if (!group) {
      const parts = [board, shot, panel].filter(Boolean)
      group = { key, label: parts.join(' · '), board, shot_id: shot, panel_id: panel, entries: [] }
      groups.set(key, group)
    }
    group.entries.push(item)
  }
  return [...groups.values()]
}

/**
 * 计算下拉框恢复值：优先浏览器缓存，其次保留外部传入的有效值，
 * 首次进入且没有值时返回空串（不兜底硬选最后一项）。
 */
export function restoreSelection(
  options: readonly string[],
  current: string | undefined,
  stored: string | undefined,
): string {
  const values = options.map((item) => String(item)).filter(Boolean)
  if (!values.length) return ''
  if (stored && values.includes(stored)) return stored
  if (current && values.includes(current)) return current
  // 无记忆且当前值不在选项中时不兜底硬选最后一项——选项异步加载期间
  // 兜底会误触发一次“选择”，把自动回填当成用户操作（曾把生图风格刷回“自动”）。
  return ''
}

/** 页面级分镜恢复：仅无有效选择时按集号取首项，缓存优先。 */
export function boardSelection(options: readonly string[], current: string, stored: string): string {
  return restoreSelection(options, current, stored) || [...options].sort((a,b)=>a.localeCompare(b, 'zh-CN', {numeric:true}))[0] || ''
}
