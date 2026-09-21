// -*- coding: utf-8 -*-
/** 全局资产 @引用输入辅助：只处理稳定 ref，不展开资产长设定。 */

export interface AssetMentionLike {
  ref: string
  kind?: string
  id?: string
  name?: string
  aliases?: string[]
  parent_ref?: string
  parent_name?: string
}

export type AssetMentionMode = 'ref' | 'name'

export interface AssetMentionContext {
  mode: AssetMentionMode
  query: string
  start: number
  end: number
}

const REF_RE = /@(character|scene|prop):[A-Za-z0-9][A-Za-z0-9_-]*/g
const TOKEN_RE = /[A-Za-z0-9_\-\u4e00-\u9fff]/

/** 提取文本中已经写入的规范资产引用，并按出现顺序去重。 */
export function extractAssetRefs(text: string): string[] {
  const refs: string[] = []
  const seen = new Set<string>()
  for (const match of String(text || '').matchAll(REF_RE)) {
    const ref = match[0]
    if (!seen.has(ref)) {
      seen.add(ref)
      refs.push(ref)
    }
  }
  return refs
}

/**
 * 返回光标前正在输入的 @引用或素材名称片段。
 * start/end 用于把片段替换成规范的 @kind:id。
 */
export function assetMentionContext(text: string, cursor = text.length): AssetMentionContext | null {
  const value = String(text || '')
  const end = Math.max(0, Math.min(Number.isFinite(cursor) ? cursor : value.length, value.length))
  const before = value.slice(0, end)

  const at = before.match(/@([A-Za-z0-9_\-:\u4e00-\u9fff]*)$/)
  if (at) {
    return { mode: 'ref', query: at[1] || '', start: end - at[0].length, end }
  }

  const tokenStart = (() => {
    let index = before.length
    while (index > 0 && TOKEN_RE.test(before[index - 1])) index -= 1
    return index
  })()
  const token = before.slice(tokenStart)
  if (!token) return null
  return { mode: 'name', query: token, start: tokenStart, end }
}

function normalized(value: unknown): string {
  return String(value || '').trim().toLocaleLowerCase()
}

/** 按名称、别名、id 或 ref 检索全局资产，并给精确/前缀命中更高排序。 */
export function filterAssetMentionCandidates<T extends AssetMentionLike>(
  rows: readonly T[], query: string, currentRef = '', limit = 12
): T[] {
  const needle = normalized(query).replace(/^@/, '')
  const current = normalized(currentRef)
  const scored: Array<{ row: T; score: number }> = []
  for (const row of rows) {
    if (!row?.ref || normalized(row.ref) === current) continue
    const fields = [row.ref, row.ref.replace(/^@/, ''), row.id, row.name, row.parent_name, ...(row.aliases || [])]
      .map(normalized)
      .filter(Boolean)
    if (!needle) {
      scored.push({ row, score: 10 })
      continue
    }
    const exact = fields.some(field => field === needle)
    const prefix = fields.some(field => field.startsWith(needle))
    const contains = fields.some(field => field.includes(needle))
    if (!contains) continue
    scored.push({ row, score: exact ? 0 : prefix ? 1 : 2 })
  }
  return scored
    .sort((a, b) => a.score - b.score || String(a.row.name || a.row.id || a.row.ref).localeCompare(String(b.row.name || b.row.id || b.row.ref), 'zh-CN'))
    .slice(0, Math.max(1, limit))
    .map(item => item.row)
}

