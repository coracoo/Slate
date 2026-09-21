// -*- coding: utf-8 -*-
/** ChatGPT 浏览器运行的前端纯视图模型。完成口径只认服务端 imported。 */

export interface ChatGPTRunAttempt {
  attempt_id: string
  job_id: string
  phase: string
  missing_refs?: string[]
  result?: Record<string, unknown> | null
}

export interface ChatGPTRun {
  run_id: string
  project?: string
  status: string
  pause_reason?: string
  job_ids: string[]
  batches: string[][]
  cursor: number
  revision: number
  counts: { total: number; imported: number; remaining: number }
  current_attempt?: ChatGPTRunAttempt | null
  updated_at?: string
  options?: { vision_validation?: boolean; auto_import?: boolean }
  worker_active?: boolean
  stop_requested?: string
  run_token?: string
}

export interface ChatGPTRunView {
  completed: number
  total: number
  remaining: number
  percent: number
  progressText: string
  batchText: string
  phaseLabel: string
  actionableReason: string
  tone: 'idle' | 'running' | 'success' | 'warning' | 'danger'
}

type JobLike = { id: string; status: string }

export function freezeQueuedJobIds(jobs: JobLike[], selectedIds: string[], limit = 20): string[] {
  const queued = new Set(jobs.filter((job) => job.status === 'queued').map((job) => job.id))
  const frozen: string[] = []
  for (const id of selectedIds) {
    if (queued.has(id) && !frozen.includes(id)) frozen.push(id)
  }
  if (frozen.length > limit) throw new Error(`一次最多执行 ${limit} 个任务`)
  return frozen
}

const PHASE_LABELS: Record<string, string> = {
  ready: '等待领取',
  waiting_dependencies: '等待父素材',
  uploading: '上传参考图',
  preparing: '素材准备轮',
  generating: '正在绘制',
  staged: '结果已暂存',
  validating: '视觉复核',
  importing: '正在导入',
  imported: '单项已导入',
  paused: '已暂停',
  needs_review: '待人工复核',
  done: '全部完成',
  failed: '运行失败',
  cancelled: '已取消',
}

export function toChatGPTRunView(run: ChatGPTRun): ChatGPTRunView {
  const total = Number(run.counts?.total ?? run.job_ids.length)
  const completed = Number(run.counts?.imported ?? run.cursor ?? 0)
  const remaining = Number(run.counts?.remaining ?? Math.max(0, total - completed))
  const percent = total ? Math.max(0, Math.min(100, Math.round(completed / total * 100))) : 0
  const batches = run.batches?.length || Math.max(1, Math.ceil(total / 10))
  const currentBatch = total === 0 ? 0 : Math.min(batches, Math.floor(Math.min(completed, Math.max(0, total - 1)) / 10) + 1)
  const status = String(run.status || 'ready')
  const tone: ChatGPTRunView['tone'] = status === 'done' ? 'success'
    : ['needs_review', 'waiting_dependencies', 'paused'].includes(status) ? 'warning'
      : ['failed', 'cancelled'].includes(status) ? 'danger'
        : ['ready'].includes(status) ? 'idle' : 'running'
  let actionableReason = String(run.pause_reason || '')
  if (!actionableReason && status === 'waiting_dependencies') {
    actionableReason = `缺少参考图：${run.current_attempt?.missing_refs?.join('、') || '请先生成父素材'}`
  }
  if (!actionableReason && status === 'needs_review') actionableReason = '结果需要人工复核后才能导入'
  return {
    completed,
    total,
    remaining,
    percent,
    progressText: `${completed}/${total} 已导入`,
    batchText: total ? `第 ${currentBatch}/${batches} 批` : '尚无任务',
    phaseLabel: PHASE_LABELS[status] || status,
    actionableReason,
    tone,
  }
}
