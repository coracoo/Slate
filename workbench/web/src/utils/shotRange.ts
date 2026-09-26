/** 镜头区间文本 -> 命中的行下标。
 *
 *  解析优先级与后端 render_shot_range.expand_range 保持一致：先按 id 精确命中，再按 `S<号>` 命中，
 *  两者都落空才按数组位置兜底。拉片产出的分镜常用描述式 id（"S01 楼顶登场·戴面具"），
 *  那种分镜只有位置能对上；按 id 命中的分镜（S1..Sn）位置与号同序，两种写法结果一致。 */
export function shotRangeRows(spec: string, ids: string[]): number[] {
  const m = (spec || '').replace(/\s/g, '').match(/^S?(\d+)-S?(\d+)$/i)
  if (!m) return []
  const byId = new Map<string, number>()
  ids.forEach((id, i) => { if (!byId.has(id)) byId.set(id, i) })
  let a = Number(m[1])
  let b = Number(m[2])
  if (a > b) [a, b] = [b, a]
  const rows = new Set<number>()
  for (let n = a; n <= b; n++) {
    const hit = byId.get(String(n)) ?? byId.get(`S${n}`)
    if (hit !== undefined) rows.add(hit)
    else if (n >= 1 && n <= ids.length) rows.add(n - 1)
    // 号与位置都对不上 = 该镜不存在（后端同样按镜数截断），不高亮
  }
  return [...rows].sort((x, y) => x - y)
}
