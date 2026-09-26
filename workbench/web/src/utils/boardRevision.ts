// -*- coding: utf-8 -*-
/** ③ 汇总表格整组回写的乐观锁判定。
 *  ③ 保存的是整张 shots，因此必须由服务端比对 revision；这里的职责只有两件事：
 *  认出「409 = 别处（⑦ 创作生成 / ⑤ 演员表现）在你编辑期间改过这份分镜」，
 *  以及把这句话翻译成人能决策的提示——绝不静默覆盖，也绝不自作主张重放。 */
import type { ApiError } from '../api'

/** 用 status 判型而不是 instanceof：api.ts 在 node 测试里不便整模块加载。 */
export function isRevisionConflict(e: unknown): boolean {
  return (e as Partial<ApiError> | null)?.status === 409
}

export function conflictNotice(e: unknown): string {
  if (!isRevisionConflict(e)) return e instanceof Error ? e.message : String(e)
  return '这份分镜在你编辑期间被别处改过（⑦ 创作生成 / ⑤ 演员表现）。'
    + '你的表格内容仍留在页面上、没有写下去：可以先「重新载入最新版」对照，'
    + '确认要以你这份为准再点「仍用我的版本覆盖」（旧版会自动进 .versions）。'
}

/** 冲突后要不要放行覆盖：只有操作者显式确认、且能拿到新基线时才允许重放。 */
export function overwriteAllowed(confirmed: boolean, revision: string): boolean {
  return confirmed && Boolean(revision)
}
