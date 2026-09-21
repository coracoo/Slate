// 未确认请求先持久化，再发送；刷新或更换页面只能接管原请求。
export type RequestBody = Record<string, unknown>
export interface PendingRequest {nonce: string; body: RequestBody; created_at: string}
const key = (project: string) => 'slate:production-request:' + project
export function pendingRequest(project: string, storage: Storage = localStorage): PendingRequest | null {
  const raw = storage.getItem(key(project))
  if (!raw) return null
  const value = JSON.parse(raw) as PendingRequest
  if (!value.nonce || !value.body) throw new Error('未确认请求记录损坏，请保留记录并检查服务日志')
  return value
}
export async function durableRequest<T>(body: RequestBody, send: (body: RequestBody) => Promise<T>, recover = false, storage: Storage = localStorage): Promise<T> {
  const project = String(body.project || '')
  const previous = pendingRequest(project, storage)
  if (recover && !previous) throw new Error('当前项目没有未确认请求')
  if (previous && !recover && JSON.stringify(previous.body) !== JSON.stringify(body)) throw new Error('存在未确认的提交，请先点击“接管未确认请求”，避免重复生成')
  const pending = previous || {nonce: crypto.randomUUID(), body, created_at: new Date().toISOString()}
  // 存储不可用时抛错并停止；不能退回只在内存记录。
  storage.setItem(key(project), JSON.stringify(pending))
  try {
    const result = await send({...pending.body, nonce: pending.nonce})
    storage.removeItem(key(project))
    return result
  } catch (error) {
    const status = (error as {status?: number}).status || 0
    // 明确的参数拒绝发生在创建任务前；网络错误和 5xx 继续保留原请求。
    if ([400, 401, 403, 404, 409, 422].includes(status)) storage.removeItem(key(project))
    throw error
  }
}
