/** 多个预览重叠时仅在最后一个关闭后恢复原来的滚动样式。 */
let count = 0
let previous = ''
export function lockBodyScroll() {
  if (count++ === 0) { previous = document.body.style.overflow; document.body.style.overflow = 'hidden' }
  let released = false
  return () => {
    if (released) return
    released = true
    if (--count === 0) document.body.style.overflow = previous
  }
}
