/** 仅隐藏前端入口；环境页保存时必须保留完整配置。 */
export function visibleVendors<T extends { id: string }>(vendors: T[]): T[] {
  return vendors.filter(v => !['kimi', 'glm'].includes(v.id))
}
