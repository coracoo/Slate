export type DiffPart = { kind: 'same' | 'remove' | 'add'; text: string }

/** 保留原始分隔符，保证差异视图可还原两份原稿。 */
export function textDiff(before: string, after: string): DiffPart[] {
  const parts = (text: string) => text.match(/[^\n。！？；]*[\n。！？；]|[^\n。！？；]+$/g) || []
  const a = parts(before), b = parts(after)
  if (a.length * b.length > 640000) {
    return before === after ? [{kind: 'same', text: before}] : [{kind: 'remove', text: before}, {kind: 'add', text: after}]
  }
  const table = Array.from({length: a.length + 1}, () => new Uint32Array(b.length + 1))
  for (let i = a.length - 1; i >= 0; i--) for (let j = b.length - 1; j >= 0; j--)
    table[i]![j] = a[i] === b[j] ? table[i + 1]![j + 1]! + 1 : Math.max(table[i + 1]![j]!, table[i]![j + 1]!)
  const result: DiffPart[] = []
  let i = 0, j = 0
  while (i < a.length || j < b.length) {
    if (i < a.length && j < b.length && a[i] === b[j]) { result.push({kind: 'same', text: a[i++]!}); j++ }
    else if (i < a.length && (j === b.length || table[i + 1]![j]! >= table[i]![j + 1]!)) result.push({kind: 'remove', text: a[i++]!})
    else result.push({kind: 'add', text: b[j++]!})
  }
  return result
}
