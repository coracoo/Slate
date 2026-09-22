// -*- coding: utf-8 -*-
/** 全局后台任务跟踪：所有 job 集中登记，JobDrawer 统一展示；轮询 1.5s。 */
import { reactive } from 'vue'
import { fetchJob, fetchJobs, jobDone, jobOk, type JobInfo } from '../api'
import { toast } from './app'

export interface TrackedJob extends JobInfo {
  trackId: number
  label: string
  startedAt: number
  liveElapsed: number
  success?: boolean
}

let seq = 0
const timers = new Map<number, number>()
const tracked = new Map<number, Promise<TrackedJob>>()
/** 每任务轮询失败计数与退避间隔（成功即重置）。 */
const pollState = new Map<number, { fails: number; delay: number; base: string }>()

export const jobs = reactive<TrackedJob[]>([])

export const runningCount = () => jobs.filter((j) => j.status === 'running').length

/** 任务完成（成功/失败/丢失/中断）监听器：页面据此刷新自身数据，不依赖整页刷新。 */
type JobDoneListener = (j: TrackedJob) => void
const doneListeners = new Set<JobDoneListener>()
export function onJobDone(cb: JobDoneListener): () => void {
  doneListeners.add(cb)
  return () => doneListeners.delete(cb)
}
function fireJobDone(j: TrackedJob) {
  for (const cb of [...doneListeners]) {
    try { cb(j) } catch { /* 监听器异常不影响其他页面 */ }
  }
}

/** 页面刷新前正在跟踪的任务：写入 sessionStorage，刷新后恢复跟踪并在完成时通知页面。 */
const INFLIGHT_KEY = 'wb.jobs.inflight'
function inflightMap(): Record<string, string> {
  try { return JSON.parse(sessionStorage.getItem(INFLIGHT_KEY) || '{}') } catch { return {} }
}
function inflightAdd(id: number, label: string) {
  try {
    const m = inflightMap()
    m[String(id)] = label
    sessionStorage.setItem(INFLIGHT_KEY, JSON.stringify(m))
  } catch { /* 存储不可用时静默降级 */ }
}
function inflightRemove(id: number) {
  try {
    const m = inflightMap()
    delete m[String(id)]
    sessionStorage.setItem(INFLIGHT_KEY, JSON.stringify(m))
  } catch { /* 同上 */ }
}
/** 刷新后恢复：对仍在跟踪列表里的任务重新挂轮询（已完成/丢失会立即走对应分支并通知）。 */
function restoreInflight() {
  for (const [id, label] of Object.entries(inflightMap())) {
    const jobId = Number(id)
    if (!jobId || jobs.some((x) => x.id === jobId)) continue
    void trackJob(jobId, label)
  }
}

function upsert(trackId: number, patch: Partial<TrackedJob>) {
  const j = jobs.find((x) => x.trackId === trackId)
  if (j) Object.assign(j, patch)
}

/** 登记一个已开始轮询的任务；返回 Promise<JobInfo>（结束时 resolve，含 success 标记）。 */
export function trackJob(id: number, label: string): Promise<TrackedJob> {
  const existing = tracked.get(id)
  if (existing) return existing
  const trackId = ++seq
  const tj = reactive<TrackedJob>({
    trackId,
    id,
    label,
    status: 'running',
    out: '',
    err: '',
    startedAt: Date.now(),
    liveElapsed: 0
  })
  jobs.unshift(tj)
  // 只裁剪已结束记录，不能让并行任务失去最终状态。
  if (jobs.length > 30) {
    let removable = -1
    for (let i = jobs.length - 1; i >= 0; i--) {
      if (jobDone(jobs[i]!)) { removable = i; break }
    }
    if (removable >= 0) jobs.splice(removable, 1)
  }
  inflightAdd(id, label)

  const promise = new Promise<TrackedJob>((resolve) => {
    /** 统一收尾：清 inflight 记录并通知全局监听器。 */
    const finish = () => {
      inflightRemove(id)
      const j = finalJob()
      j.success = jobOk(j)
      toast(`${label}｜${j.success ? '已完成' : '失败'}${!j.success ? '：' + (j.err || '请查看任务日志') : ''}`,
        j.success ? 'ok' : 'err', j.success ? 6000 : 12000)
      fireJobDone(j)
      resolve(j)
    }
    /** 任务被 30 条上限挤出列表时 resolve 兜底对象，而非 undefined。 */
    const finalJob = (): TrackedJob =>
      jobs.find((x) => x.trackId === trackId) ||
      ({
        trackId, id, label, status: 'error', ok: false,
        out: tj.out, err: tj.err || '任务记录已被挤出列表',
        startedAt: tj.startedAt, liveElapsed: (Date.now() - tj.startedAt) / 1000,
        success: false
      } as TrackedJob)
    const tick = async () => {
      // 活耗时显示（后端 elapsed 字段可能缺省，前端自己算）
      upsert(trackId, { liveElapsed: (Date.now() - tj.startedAt) / 1000 })
      let j: JobInfo
      try {
        j = await fetchJob(id)
        pollState.delete(trackId)
      } catch (e) {
        if ((e as { status?: number })?.status === 401) {
          upsert(trackId, { status: 'error', ok: false, err: '需要登录；重新登录后请刷新页面恢复跟踪' })
          timers.delete(trackId)
          finish()
          return
        }
        const st = pollState.get(trackId) || { fails: 0, delay: 1500, base: tj.out }
        st.fails += 1
        st.delay = Math.min(st.delay * 2, 30000)
        pollState.set(trackId, st)
        // 原地更新同一行，不逐次追加
        upsert(trackId, { out: (st.base ? st.base + '\n' : '') + `…轮询失败 ×${st.fails}，重试中` })
        timers.set(trackId, window.setTimeout(tick, st.delay))
        return
      }
      // 服务重启后任务记录丢失：后端返回 {err:"无此任务", lost:true}（无 status 字段）
      if (!j.status) {
        upsert(trackId, { status: 'error', ok: false, err: '任务记录丢失（服务可能已重启），请到对应页面确认产物是否已生成' })
        timers.delete(trackId)
        finish()
        return
      }
      // 重启后恢复的历史任务：running 被标记为 interrupted（进程已随服务被杀）
      if (j.status === 'interrupted') {
        upsert(trackId, { status: 'error', ok: false, err: (j.err || '') + '（服务重启导致中断，请重新运行）', out: j.out || tj.out })
        timers.delete(trackId)
        finish()
        return
      }
      upsert(trackId, {
        status: j.status,
        ok: j.ok,
        returncode: j.returncode,
        out: j.out || tj.out,
        err: j.err || '',
        elapsed: j.elapsed
      })
      if (!jobDone(j)) {
        timers.set(trackId, window.setTimeout(tick, 1500))
        return
      }
      upsert(trackId, { success: jobOk({ ...tj, ...j }) })
      timers.delete(trackId)
      finish()
    }
    timers.set(trackId, window.setTimeout(tick, 300))
  })
  tracked.set(id, promise)
  return promise
}

// 模块加载即恢复刷新前未完成的任务跟踪。
restoreInflight()

// 页面未显式 trackJob 的入口、其他页面启动的任务也统一接管。
// 首次载入不重放历史结果；本次打开后的快速结束任务同样通知。
const openedAt = Date.now() / 1000
async function discoverJobs() {
  try {
    for (const job of (await fetchJobs()).jobs) {
      if (!tracked.has(job.id) && (job.status === 'running' || job.status === 'queued' || (job.finished_at || 0) >= openedAt)) {
        void trackJob(job.id, job.step || `任务 #${job.id}`)
      }
    }
  } catch { /* 短暂离线不判任务失败，恢复后继续接管。 */ }
  window.setTimeout(discoverJobs, 3000)
}
void discoverJobs()

/** 日志尾部若干行。 */
export function logTail(j: TrackedJob, n = 10): string {
  const s = (j.out || '').trim()
  if (!s) return j.status === 'running' ? '启动中…' : ''
  return s.split('\n').slice(-n).join('\n')
}
