/** 版本清单请求合并器：同一时间窗内的多个路径合成一次批量请求。
 *
 *  ② 素材页每张资产图都挂一个版本徽标，切一次项目会发出 248 个 /api/versions 请求
 *  （实测中位数 203ms/条、墙钟 7.6s，服务端每条都要扫一次 .versions 目录）。
 *  这里把同窗口内的路径去重后交给 sender 一次取回，再按路径分发给各自等待者。 */
export function createVersionBatcher<V>(
  sender: (paths: string[]) => Promise<Record<string, V[]>>,
  delay = 40
): (path: string) => Promise<V[]> {
  let waiting = new Map<string, Array<{ resolve: (v: V[]) => void; reject: (e: unknown) => void }>>()
  let timer: ReturnType<typeof setTimeout> | null = null

  return (path: string) => new Promise<V[]>((resolve, reject) => {
    const key = String(path || '')
    waiting.set(key, [...(waiting.get(key) || []), { resolve, reject }])
    if (timer) return
    timer = setTimeout(() => {
      timer = null
      const batch = waiting
      waiting = new Map()
      sender([...batch.keys()]).then((res) => {
        for (const [p, subs] of batch) {
          const list = (res && res[p]) || []
          subs.forEach(s => s.resolve(list))
        }
      }).catch((e) => {
        for (const subs of batch.values()) subs.forEach(s => s.reject(e))
      })
    }, delay)
  })
}
