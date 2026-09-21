// -*- coding: utf-8 -*-
/** 轻量 markdown → HTML：先整体转义再做白名单替换，杜绝注入；支持标题/段落/加粗/斜体/行内代码/列表/分隔线/链接。 */

function esc(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
}

/** 行内语法：加粗 / 斜体 / 行内代码 / 链接（仅放行 http(s)）。 */
export function inlineMd(s: string): string {
  let t = esc(s)
  t = t.replace(/`([^`]+)`/g, '<code>$1</code>')
  t = t.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  t = t.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1<em>$2</em>')
  t = t.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_m, text: string, url: string) =>
    /^https?:\/\//i.test(url) ? `<a href="${url}" target="_blank" rel="noopener">${text}</a>` : text
  )
  return t
}

/** 整段 markdown → HTML。 */
export function renderMarkdown(md: string): string {
  const lines = md.replace(/\r\n/g, '\n').split('\n')
  const out: string[] = []
  let list: string[] = []
  let ordered = false
  const flush = () => {
    if (list.length) {
      out.push(ordered ? `<ol>${list.join('')}</ol>` : `<ul>${list.join('')}</ul>`)
      list = []
    }
  }
  for (const raw of lines) {
    const t = raw.trim()
    if (!t) {
      flush()
      continue
    }
    const h = t.match(/^(#{1,4})\s+(.*)$/)
    if (h) {
      flush()
      const lv = h[1].length
      out.push(`<h${lv}>${inlineMd(h[2])}</h${lv}>`)
      continue
    }
    if (/^(-{3,}|\*{3,})$/.test(t)) {
      flush()
      out.push('<hr>')
      continue
    }
    const ul = t.match(/^[-*]\s+(.*)$/)
    if (ul) {
      if (ordered) flush()
      ordered = false
      list.push(`<li>${inlineMd(ul[1])}</li>`)
      continue
    }
    const ol = t.match(/^\d+[.、)]\s*(.*)$/)
    if (ol) {
      if (!ordered && list.length) flush()
      ordered = true
      list.push(`<li>${inlineMd(ol[1])}</li>`)
      continue
    }
    flush()
    out.push(`<p>${inlineMd(t)}</p>`)
  }
  flush()
  return out.join('\n')
}

/** 按 ## 小节切分（讲解文档每镜一节 → 各渲染成一张玻璃卡片）。 */
export function splitSections(md: string): { title: string; body: string }[] {
  const parts = md.replace(/\r\n/g, '\n').split(/^##\s+/m)
  const out: { title: string; body: string }[] = []
  const intro = parts.shift()
  if (intro && intro.trim()) out.push({ title: '', body: intro.trim() })
  for (const p of parts) {
    const nl = p.indexOf('\n')
    out.push({ title: nl < 0 ? p.trim() : p.slice(0, nl).trim(), body: nl < 0 ? '' : p.slice(nl + 1).trim() })
  }
  return out
}
