// -*- coding: utf-8 -*-
/** 统一 API 访问层：类型定义 + fetch 封装 + 后台任务轮询。 */
import type { ChatGPTRun } from './utils/chatgptRun'
import { visibleVendors } from './utils/providerVisibility'

export interface Project {
  name: string
  workspace_root?: string
  dirs: Record<string, string[]>
  type?: '拆片' | '制作'
}

export interface SourceVideo {
  name: string
  mb: number
}

export interface JobInfo {
  id?: number
  step?: string
  status: 'running' | 'done' | 'error' | 'failed' | string
  ok?: boolean
  returncode?: number
  out: string
  err: string
  elapsed?: number
  cmd?: string[]
  started_at?: number
  finished_at?: number
}

/** 历史任务清单条目（/api/jobs，轻字段；完整日志再取 /api/job?id=）。 */
export interface JobSummary {
  id: number
  step?: string
  status?: string
  ok?: boolean
  started_at?: number
  finished_at?: number
  elapsed?: number
  err?: string
}

export const fetchJobs = () => getJSON<{ jobs: JobSummary[] }>('/api/jobs')
/** 清空已结束的历史任务（running 保留）。 */
export const clearFinishedJobs = () => postJSON<{ ok: boolean; cleared: number }>('/api/jobs/clear', {})

export interface AnalysisMeta {
  name: string
  created_at?: string
  status?: string
  shot_count?: number
  engine?: string
  source?: string
  note?: string
  first_t?: number
  last_t?: number
}

export interface DialogueLine {
  speaker: string
  text: string
  t_in: number
  t_out: number
  source?: string
  /** 前端辅助字段：可增删列表的稳定 key；保存前剔除，不落盘。 */
  _uid?: string
}

export interface AnalysisShot {
  id: string
  t_in: number
  t_out: number
  duration?: number
  shot_size?: string
  camera_move?: string
  angle?: string
  lighting?: string
  action?: string
  story?: string
  dialogue?: DialogueLine[]
  prompt_cn?: string
  transition?: string
  keyframes?: string[]
}

export interface Analysis {
  name?: string
  version?: string
  source?: string
  created_at?: string
  engine?: string
  shots?: AnalysisShot[]
}

export interface FramesInfo {
  source?: string
  fps?: number
  frames?: { t: number; file: string }[]
}

export interface ScriptData {
  speakers: Record<string, { name: string; color: string }>
  lines: DialogueLine[]
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

const FETCH_TIMEOUT = 30000

async function fetchWithTimeout(url: string, init?: RequestInit, timeoutMs = FETCH_TIMEOUT): Promise<Response> {
  const ctl = new AbortController()
  // 外部 signal（如模态强制关闭）联动中断
  const outer = init?.signal
  if (outer) {
    if (outer.aborted) ctl.abort()
    else outer.addEventListener('abort', () => ctl.abort(), { once: true })
  }
  const timer = setTimeout(() => ctl.abort(), timeoutMs)
  try {
    return await fetch(url, { ...init, signal: ctl.signal })
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') {
      if (outer?.aborted) throw new ApiError(0, '已取消')
      throw new ApiError(0, `请求超时（>${Math.round(timeoutMs / 1000)}s）：${url.split('?')[0]}`)
    }
    throw e
  } finally {
    clearTimeout(timer)
  }
}

export async function getJSON<T>(url: string): Promise<T> {
  const r = await fetchWithTimeout(url)
  if (!r.ok) {
    const data = (await r.json().catch(() => ({}))) as { error?: string; err?: string; need_setup?: boolean }
    if (r.status === 401 && !url.startsWith('/api/auth') && location.pathname !== '/login') {
      location.href = data.need_setup ? '/login?mode=setup' : '/login'
    }
    throw new ApiError(r.status, data.error || data.err || `${r.status} ${r.statusText}`)
  }
  return (await r.json()) as T
}

// ---- 账号体系（N85）----
export const fetchAuthStatus = () => getJSON<{ ok: boolean; configured: boolean; authed: boolean }>('/api/auth/status')
export const authSetup = (password: string) => postJSON<{ ok: boolean }>('/api/auth/setup', { password })
export const authLogin = (password: string) => postJSON<{ ok: boolean }>('/api/auth/login', { password })
export const authLogout = () => postJSON<{ ok: boolean }>('/api/auth/logout', {})
export const authChange = (old: string, next: string) => postJSON<{ ok: boolean }>('/api/auth/change', { old, new: next })

// ---- GitHub 更新检查 / 拉取 / 回退（N86）----
export interface UpdateCheck { ok: boolean; supported: boolean; branch?: string; behind?: number; ahead?: number; commits?: string[]; err?: string }
export const checkUpdate = (force = false) => getJSON<UpdateCheck>(`/api/update/check${force ? '?force=1' : ''}`)
export const applyUpdate = () => postJSON<{ ok: boolean; updated: boolean; note?: string; backup?: string }>('/api/update/apply', {})
export const rollbackUpdate = () => postJSON<{ ok: boolean; rolled_back_to: string; note?: string }>('/api/update/rollback', {})
export const restartServer = () => postJSON<{ ok: boolean; note?: string }>('/api/update/restart', {})

export async function postJSON<T>(url: string, body: unknown): Promise<T> {
  const r = await fetchWithTimeout(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  }, 120000)
  const data = (await r.json().catch(() => ({}))) as T & { error?: string; err?: string }
  if (!r.ok) {
    if (r.status === 401 && !url.startsWith('/api/auth') && location.pathname !== '/login') location.href = '/login'
    throw new ApiError(r.status, data.error || data.err || `${r.status} ${r.statusText}`)
  }
  return data
}

/** 项目树：兼容数组 [{name,dirs}] 与对象映射 {name:dirs} 两种返回。 */
export async function fetchProjects(): Promise<Project[]> {
  const raw = await getJSON<Project[] | Record<string, Record<string, string[]>>>('/api/projects')
  if (Array.isArray(raw)) return raw
  return Object.entries(raw).map(([name, dirs]) => ({ name, dirs }))
}

export const fetchSources = () => getJSON<SourceVideo[]>('/api/sources')

export const fetchJob = (id: number | string) => getJSON<JobInfo>(`/api/job?id=${id}`)

export const fetchAnalysisList = (project: string) =>
  getJSON<AnalysisMeta[]>(`/api/analysis?project=${encodeURIComponent(project)}`)

export const fetchAnalysis = (project: string, name: string) =>
  getJSON<Analysis>(`/api/analysis/get?project=${encodeURIComponent(project)}&name=${encodeURIComponent(name)}`)

export const saveAnalysis = (project: string, name: string, analysis: Analysis) =>
  postJSON<{ ok: boolean }>('/api/analysis/save', { project, name, analysis })

export const deleteAnalysis = (project: string, name: string) =>
  postJSON<{ ok: boolean }>('/api/analysis/delete', { project, name })

export const runAnalysis = (body: {
  project: string
  video: string
  name?: string
  note?: string
  no_ai?: boolean
  vendor_id?: string
  min_dur?: number
  thresh?: number
  workers?: number
}) => postJSON<{ ok: boolean; id: number; name?: string }>('/api/analysis/run', body)

/** 导出分镜脚本 xlsx（同步返回项目内相对路径）。 */
export const exportAnalysisXlsx = (project: string, name: string) =>
  postJSON<{ ok: boolean; file: string }>('/api/analysis/export', { project, name })

/** 把台词页合并的台词脚本并入已有版本 dialogue（无 AI，秒级）。 */
export const mergeAnalysisLines = (project: string, name: string) =>
  postJSON<{ ok: boolean; merged?: string }>('/api/analysis/merge', { project, name })

/** 对已有版本跑 AI 填充（不重新切点抽帧）；only_empty=true 只填空镜。 */
export const runAnalysisAi = (body: { project: string; name: string; only_empty?: boolean }) =>
  postJSON<{ ok: boolean; id: number }>('/api/analysis/ai', body)

/** 单镜/多镜重新识别（重新抽帧+AI 识别指定镜头）。 */
export const reshotShots = (body: { project: string; name: string; shots: string[] }) =>
  postJSON<{ ok: boolean; id: number }>('/api/analysis/reshot', body)

export const runFramesExtract = (body: { project: string; video: string }) =>
  postJSON<{ ok: boolean; id: number }>('/api/frames/extract', body)


export interface AssetStateItem {
  id: string; label: string; look_diff?: string; camp?: string
  episodes?: string[]; path?: string
}
export interface AssetRegistryItem {
  ref: string; kind: "character" | "scene" | "prop" | "style"; id: string; name: string
  path?: string; usage?: string; aliases?: string[]; prompt?: string
  asset_revision?: number
  parent_ref?: string; relation?: string; derived_from?: string; related_refs?: string[]
  children_refs?: string[]; source_episode_ids?: string[]
  /** 资产级画风覆盖（image skill id）；空 = 跟随项目生图风格。 */
  style?: string
  /** 资产级画风自由文本（最高优先级，直接作为画风层）；空 = 用 style skill / 项目默认。 */
  style_prompt?: string
  states?: AssetStateItem[]
}
export interface AssetPromptLayers {
  subject: string; style: string; style_source: "asset_text" | "asset_skill" | "project" | "none"
  negative: string; constraint: string; final: string
}
export const fetchAssetPromptLayers = (project: string, kind: string, id: string) =>
  getJSON<{ ok: boolean; layers: AssetPromptLayers }>(`/api/asset/prompt_layers?project=${encodeURIComponent(project)}&kind=${encodeURIComponent(kind)}&id=${encodeURIComponent(id)}`)
export interface AssetRelationUpdate {
  ref: string; parent_ref?: string | null; relation?: string | null
  derived_from?: string | null; related_refs?: string[]
}
export const fetchAssets = (project: string, kind?: string) =>
  getJSON<{ ok: boolean; assets: AssetRegistryItem[] }>("/api/assets?project=" + encodeURIComponent(project) + (kind ? "&kind=" + encodeURIComponent(kind) : ""))
export const saveAssetRelations = (body: { project: string; updates: AssetRelationUpdate[]; expected_revisions?: Record<string, string> }) =>
  postJSON<{ ok: boolean; updated?: string[]; assets?: AssetRegistryItem[]; issues?: Array<Record<string, unknown>> }>('/api/assets/relations', body)
export interface AssetCreateRequest {
  project: string; kind: "character" | "scene" | "prop"; id?: string; name: string
  parent_ref?: string | null; relation?: string | null; prompt?: string
  role?: string; prop_kind?: string; episode?: string
  related_refs?: string[]
}
export const createAsset = (body: AssetCreateRequest) =>
  postJSON<{ ok: boolean; asset?: AssetRegistryItem; assets?: AssetRegistryItem[]; issues?: Array<Record<string, unknown>> }>('/api/assets/create', body)
export const editAsset = (body: { project: string; ref: string; patch: Record<string, unknown>; expected_revision?: string | number }) =>
  postJSON<{ ok: boolean; asset?: AssetRegistryItem; assets?: AssetRegistryItem[]; affected?: { shots?: string[]; reasons?: Record<string, string> } }>('/api/assets/edit', body)

export const fetchFrames = (project: string) =>
  getJSON<FramesInfo>(`/api/frames?project=${encodeURIComponent(project)}`)

export const runLinesMerge = (project: string) =>
  postJSON<{ ok: boolean; id: number }>('/api/lines/merge', { project })

export const fetchLines = (project: string) =>
  getJSON<ScriptData>(`/api/lines?project=${encodeURIComponent(project)}`)

export const saveLines = (project: string, script: ScriptData) =>
  postJSON<{ ok: boolean }>('/api/lines/save', { project, script })

export const runWhiterange = (body: {
  project: string
  json: string
  shots: string
  engine?: string
}) => postJSON<{ ok: boolean; id: number }>('/api/whiterange/run', body)

export const whiteFromAnalysis = (body: { project: string; analysis?: string; style?: string }) =>
  postJSON<{ ok: boolean; id: number; job: true }>('/api/white/from_analysis', body)

/* 预演包导出：逐镜干净预演帧+表演提示词+manifest（图生视频参考输入）；异步 job，out 末行为产物目录 */
export const exportPreviz = (body: { project: string; json: string; frame?: 'mid' | 'start' }) =>
  postJSON<{ ok: boolean; id?: number; err?: string }>('/api/previz/export', body)

/* ---------- 白模分镜（引擎契约 storyboard） ---------- */
export interface WhiteLine { at: number; dur?: number; speaker?: string | null; line: string }
export interface WhiteShot {
  id: string
  dur: number
  cam?: string
  fov?: number
  move?: string
  action?: string
  prompt?: string
  speaker?: string
  line?: string
  lines?: WhiteLine[]
  host?: string
  target?: string
  pos?: number[]
  look?: number[]
}
export interface WhiteBoard {
  project?: string
  title?: string
  w?: number
  h?: number
  fps?: number
  script_rev?: number
  actors?: Record<string, { name?: string; shirt?: number[] }>
  shots: WhiteShot[]
  panels?: StoryboardPanel[]
  grids?: { id: string; layout: string; panel_ids: string[] }[]
}
export const fetchWhiteBoard = (project: string, name: string) =>
  getJSON<WhiteBoard>(`/api/white/board?project=${encodeURIComponent(project)}&name=${encodeURIComponent(name)}`)

/** @deprecated 兼容入口；当前页面不使用，不作为新功能接入点。 */
export const fetchCreationDiagrams = (project: string, name: string) =>
  getJSON<{ paths: string[] }>(
    '/api/creation/diagrams?project=' + encodeURIComponent(project) + '&name=' + encodeURIComponent(name)
  )

/** 在线查看分镜脚本 xlsx（解析为 sheet 行数组）。 */
export const fetchAnalysisXlsxView = (project: string, name: string) =>
  getJSON<{ ok: boolean; sheets: { title: string; rows: string[][] }[] }>(
    `/api/analysis/xlsx?project=${encodeURIComponent(project)}&name=${encodeURIComponent(name)}`
  )

/* ---------- 环境 / 厂商（厂商 + 能力槽模型） ---------- */

export interface EnvInfo {
  python?: string
  ffmpeg: string | null
  packages: Record<string, string>
  blender: string | null
  mcp: boolean

}

export type ModelSlot = 'text' | 'vision' | 'image' | 'image_edit' | 'video' | 'music' | 'speech'

export const MODEL_SLOTS: { key: ModelSlot; label: string; color: string }[] = [
  { key: 'text', label: '文本', color: '#22d3ee' },
  { key: 'vision', label: '视觉', color: '#a78bfa' },
  { key: 'image', label: '生图', color: '#e879f9' },
  { key: 'image_edit', label: '改图', color: '#f472b6' },
  { key: 'video', label: '生视频', color: '#fb7185' },
  { key: 'music', label: '音乐', color: '#fbbf24' },
  { key: 'speech', label: '语音', color: '#34d399' }
]

export interface Vendor {
  video_capabilities?: import('./utils/videoSettings').VideoCapability
  id: string
  label: string
  base_url: string
  api_key: string
  enabled: boolean
  note?: string
  documentation_url?: string
  models: Record<ModelSlot, string>
  endpoints?: Record<string, string> | null
  /** 厂商扩展配置（如 doubao 语音 ASR 的 asr_api_key/asr_ak/asr_sk/tos_bucket/tos_region）。密钥类服务端返回掩码值。 */
  extra?: Record<string, string>
  /** 单次请求允许传入的参考图数量；缺省由适配器按模型推断。 */
  reference_limit?: number | null
}

export interface EnvConfig {
  vendors: Vendor[]
}

export interface TestResult {
  ok: boolean
  latency_ms?: number
  note?: string
  err?: string
}

export const fetchEnv = () => getJSON<EnvInfo>('/api/env')

export const fetchEnvConfig = async (options: { includeHidden?: boolean } = {}) => {
  const config = await getJSON<EnvConfig>('/api/env/config')
  return options.includeHidden ? config : { ...config, vendors: visibleVendors(config.vendors || []) }
}
export interface ComfyWorkflowInfo {
  path: string
  name: string
  size?: number
  refs?: number[]
  has_negative?: boolean
  has_save_image?: boolean
  error?: string
}

/** workbench/workflows 下可供 ComfyUI 生图选择的 API 格式工作流。 */
export const fetchComfyWorkflows = () =>
  getJSON<{ ok: boolean; workflows: ComfyWorkflowInfo[]; err?: string }>('/api/comfy/workflows')

export const saveEnvConfig = (vendors: Vendor[]) =>
  postJSON<{ ok: boolean }>('/api/env/config', { vendors })

/** 厂商草稿：测试/拉取模型时用卡片当前未落盘的值；api_key 空串 = 后端回落已落盘 key。 */
export interface VendorDraft {
  base_url?: string
  api_key?: string
  models?: Record<ModelSlot, string>
}

export const testProvider = (body: { id: string; kind?: ModelSlot; draft?: VendorDraft }) =>
  postJSON<TestResult>('/api/env/test', body)

/** 拉取厂商可用模型列表（OpenAI 兼容 /models）；draft 非空直接用草稿的 base_url/key 拉取。 */
export const fetchEnvModels = (body: { id?: string; draft?: { base_url?: string; api_key?: string } }) =>
  postJSON<{ ok: boolean; models?: string[]; err?: string }>('/api/env/models', body)

/** 整理项目目录：apply=false 返回 dry-run 计划（字符串列表），apply=true 执行。 */
export const normalizeProject = (project: string, apply: boolean) =>
  postJSON<{ ok: boolean; apply: boolean; plan?: string[]; err?: string }>(
    '/api/project/normalize',
    { project, apply }
  )

/* ---------- 镜头语言讲解 ---------- */

export interface ExplainDoc {
  md: string
}

export const runExplain = (body: { project: string; name: string }) =>
  postJSON<{ ok: boolean; id: number }>('/api/explain/run', body)

export const fetchExplain = (project: string, name: string) =>
  getJSON<ExplainDoc>(
    `/api/explain/get?project=${encodeURIComponent(project)}&name=${encodeURIComponent(name)}`
  )

/** 讲解前置态：拉片数据 / 关键帧在盘 / vision 厂商 / 已有讲解文档。 */
export interface ExplainPreflight {
  ok: boolean
  shot_count?: number
  kf_total?: number
  kf_disk?: number
  vision_vendor?: string
  has_doc?: boolean
  doc_mtime?: string
}

export const fetchExplainPreflight = (project: string, name: string) =>
  getJSON<ExplainPreflight>(
    `/api/explain/preflight?project=${encodeURIComponent(project)}&name=${encodeURIComponent(name)}`
  )

/* ---------- 模拟创作 ---------- */

export interface CreateItem {
  id: string
  character_id?: string
  character_name?: string
  voice_id?: string
  model_slot?: string
  material_path?: string
  source_video?: string
  registration_error?: string
  type: 'image' | 'video' | 'music' | 'speech'
  /** 图片模式：生图或使用参考图改图。 */
  image_mode?: 'generate' | 'edit'
  prompt: string
  refs: (string | { path: string; ref_token?: string; purpose?: string; reference_role?: string; target_time_seconds?: number; usage?: string; source_item_id?: string; panel_id?: string })[]
  board?: string
  shot_id?: string
  prompt_user?: string
  prompt_assembled?: string
  prompt_stage?: 'script' | 'storyboard_image' | 'video' | 'asset_image' | string
  prompt_system?: string
  prompt_revision?: string
  asset_revisions?: Record<string, number>
  prompt_json?: Record<string, unknown> | null
  asset_refs?: string[]
  negative?: string
  vendor_id?: string
  provider_id?: string
  status: 'running' | 'queued' | 'generating' | 'awaiting_import' | 'done' | 'error' | 'failed' | 'abnormal' | 'cancelled' | 'canceled' | 'interrupted' | 'timeout' | 'pending'
  delivery?: 'chatgpt_queue'
  task_type?: 'reference_image' | 'storyboard_image' | 'keyframe_image'
  output_spec?: { asset_id?: string; filename?: string; version?: number }
  created_at?: string
  started_at?: string
  updated_at?: string
  outputs: string[]
  note?: string
  source_hash?: string
  acting_mode?: string
  actor_performance_used?: boolean
  actor_warnings?: string[]
  panel_id?: string
  panel_draft?: PanelDraft
  compiled_request?: CompiledImageRequest
  asset_context?: {
    characters?: Record<string, unknown>[]
    scene?: Record<string, unknown> | null
    props?: Record<string, unknown>[]
  }
}

export const runCreate = (body: {
  video_options?: import('./utils/videoSettings').VideoSettingsValue
  project: string
  type: 'image' | 'video' | 'music' | 'speech'
  mode?: 'generate' | 'edit'
  model_slot?: string
  character_id?: string
  voice_id?: string
  prompt: string
  refs: (string | { path: string; ref_token?: string; purpose?: string; reference_role?: string; target_time_seconds?: number; usage?: string; source_item_id?: string; panel_id?: string })[]
  board?: string
  shot_id?: string
  prompt_user?: string
  prompt_assembled?: string
  prompt_stage?: 'storyboard_image' | 'video' | string
  prompt_system?: string
  prompt_revision?: string
  asset_revisions?: Record<string, number>
  prompt_json?: Record<string, unknown> | null
  asset_refs?: string[]
  negative?: string
  vendor_id?: string
  note?: string
  source_hash?: string
  acting_mode?: string
  actor_performance_used?: boolean
  actor_warnings?: string[]
  panel_id?: string
  panel_draft?: PanelDraft
}) => postJSON<{ ok: boolean; id: number }>('/api/create/run', body)

export interface StoryboardPanel {
  id: string
  shot_ids: string[]
  beat: string
  time_role: string
  composition: { aspect_ratio: string; shot_size?: string; angle?: string }
  visible_refs: string[]
  lighting?: string
  source: { kind: string; shot_id?: string }
}

export interface PanelDraft {
  panel_id: string
  visual_description: string
  local_negative: string[]
  used_refs: string[]
}

export interface CompiledImageRequest {
  panel_id: string
  shot_ids: string[]
  prompt: string
  negative_prompt: string
  aspect_ratio: string
  image_refs: { path: string; purpose: string; reference_role?: string; target_time_seconds?: number; usage?: string; version_id: string }[]
  policy_version: string
  panel_spec_hash: string
  draft_hash: string
}

export const generatePanelDraft = (body: { project: string; board: string; panel_id: string }) =>
  postJSON<{ ok: boolean; id: number }>('/api/storyboard/panel/draft/generate', body)

export const fetchPanelDraft = (project: string, board: string, panelId: string) =>
  getJSON<PanelDraft>(`/api/storyboard/panel/draft?project=${encodeURIComponent(project)}&board=${encodeURIComponent(board)}&panel_id=${encodeURIComponent(panelId)}`)

export const saveStoryboardGrid = (body: { project: string; board: string; grid: { id: string; layout: string; panel_ids: string[] } }) =>
  postJSON<{ ok: boolean; grids: { id: string; layout: string; panel_ids: string[] }[] }>('/api/storyboard/grid/save', body)

export const saveStoryboardPanel = (body: { project: string; board: string; panel: StoryboardPanel }) =>
  postJSON<{ ok: boolean; panel: StoryboardPanel }>('/api/storyboard/panel/save', body)

export const previewStoryboardPanel = (body: { project: string; board: string; panel_id: string; draft: PanelDraft; refs: { path: string; purpose: string }[] }) =>
  postJSON<{ ok: boolean; compiled_request: CompiledImageRequest }>('/api/storyboard/panel/preview', body)

export const assembleCreateShot = (body: { project: string; board: string; shot_id: string; prompt_user?: string; type?: "image" | "video"; vendor_id?: string }) =>
  postJSON<{ ok: boolean; board: string; shot_id: string; prompt_assembled: string; prompt_json?: Record<string, unknown> | null; asset_refs?: string[]; negative: string; refs: { path: string; purpose: string; reference_role?: string; target_time_seconds?: number; usage?: string }[]; asset_context?: CreateItem['asset_context']; prompt_stage?: string; system_prompt?: string; source_hash: string; prompt_revision?: string; asset_revisions?: Record<string, number> }>(
    '/api/create/assemble', body
  )

/** 调用文本模型重写单镜提示词并保存进分镜；不启动生图/生视频。 */
export const regenerateCreateShotPrompt = (body: { project: string; board: string; shot_id: string; type: 'image' | 'video' }) =>
  postJSON<{ ok: boolean; id: number }>('/api/create/prompt/regenerate', body)

export const runCreateBatch = (body: { project: string; board: string; type: 'image' | 'video'; mode?: 'generate' | 'edit'; vendor_id: string; shot_ids?: string[] }) =>
  postJSON<{ ok: boolean; id: number }>('/api/create/batch', body)

export interface ChatGPTJobSummary {
  id: string
  board: string
  shot_id: string
  task_type: 'reference_image' | 'storyboard_image' | 'keyframe_image' | 'asset_image'
  status: 'queued' | 'generating' | 'awaiting_import' | 'done' | 'error'
  filename: string
  source_mode?: '创作' | '资产提炼' | string
  asset_ref?: string
  asset_kind?: string
}

export const queueChatGPTShots = (body: { project: string; board: string; shot_ids: string[]; type?: 'image'; task_type?: ChatGPTJobSummary['task_type'] }) =>
  postJSON<{ ok: boolean; jobs: CreateItem[] }>('/api/create/chatgpt/queue', body)

export const queueChatGPTAssets = (body: { project: string; asset_refs: Array<string | { ref: string; state_id?: string }> }) =>
  postJSON<{ ok: boolean; jobs: CreateItem[] }>('/api/asset/chatgpt/queue', body)

export const fetchChatGPTJobs = (project: string, opts: { board?: string; status?: string; limit?: number } = {}) => {
  const q = new URLSearchParams({ project })
  if (opts.board) q.set('board', opts.board)
  if (opts.status) q.set('status', opts.status)
  if (opts.limit) q.set('limit', String(opts.limit))
  return getJSON<{ ok: boolean; jobs: ChatGPTJobSummary[] }>(`/api/create/chatgpt/jobs?${q.toString()}`)
}

export const fetchChatGPTJob = (project: string, id: string) =>
  getJSON<{ ok: boolean; job: CreateItem }>(`/api/create/chatgpt/job?project=${encodeURIComponent(project)}&id=${encodeURIComponent(id)}`)

export const createChatGPTRun = (body: {
  project: string
  job_ids: string[]
  options?: { vision_validation?: boolean; auto_import?: boolean; max_retries?: number }
}) => postJSON<{ ok: boolean; run: ChatGPTRun }>('/api/create/chatgpt/run/create', body)

export const fetchChatGPTRun = (project: string, runId: string) =>
  getJSON<{ ok: boolean; run: ChatGPTRun }>(
    `/api/create/chatgpt/run?project=${encodeURIComponent(project)}&run_id=${encodeURIComponent(runId)}`
  )

export async function controlChatGPTRun(project: string, runId: string, token: string, action: 'pause' | 'resume' | 'cancel', reason = '') {
  const response = await fetchWithTimeout('/api/create/chatgpt/run/control', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Previs-Run-Token': token },
    body: JSON.stringify({ project, run_id: runId, action, reason })
  }, 120000)
  const data = (await response.json().catch(() => ({}))) as { ok?: boolean; run?: ChatGPTRun; err?: string }
  if (!response.ok || data.ok === false) throw new ApiError(response.status, data.err || `运行控制失败（${response.status}）`)
  return data as { ok: true; run: ChatGPTRun }
}

export const importChatGPTPackage = (project: string, file: File, signal?: AbortSignal) => {
  const form = new FormData()
  form.append('project', project)
  form.append('package', file)
  // 上传体积可能较大：5 分钟超时，支持外部 signal 强制取消
  return fetchWithTimeout('/api/create/chatgpt/import', { method: 'POST', body: form, signal }, 300000).then(async r => {
    const data = await r.json()
    if (!r.ok || data?.ok === false) throw new Error(data?.err || `导入失败（${r.status}）`)
    return data as { ok: boolean; imported: number; items: string[] }
  })
}

/** 将多张独立原图按已确认的任务映射直接导入，无需用户制作 ZIP/manifest。 */
export const importChatGPTImages = (project: string, rows: { file: File; job: ChatGPTJobSummary }[], signal?: AbortSignal) => {
  const form = new FormData()
  form.append('project', project)
  const manifest = {
    schema_version: '1.0', generator: 'chatgpt', project,
    assets: rows.map(({ job }) => {
      const version = Number(job.filename.match(/_v(\d+)\.[^.]+$/i)?.[1] || 0)
      if (!version) throw new Error(`任务缺少版本号：${job.filename}`)
      return { job_id: job.id, task_type: job.task_type, version, file: `images/${job.filename}` }
    })
  }
  form.append('manifest', new Blob([JSON.stringify(manifest)], { type: 'application/json' }), 'manifest.json')
  rows.forEach(({ file, job }) => form.append('image', file, `images/${job.filename}`))
  return fetchWithTimeout('/api/create/chatgpt/import', { method: 'POST', body: form, signal }, 300000).then(async r => {
    const data = await r.json()
    if (!r.ok || data?.ok === false) throw new Error(data?.err || `导入失败（${r.status}）`)
    return data as { ok: boolean; imported: number; items: string[] }
  })
}

export interface CreateCoverage {
  ok: boolean
  board?: string
  shots: { shot_id: string; status: 'done' | 'running' | 'queued' | 'generating' | 'awaiting_import' | 'error' | 'pending'; item_ids: string[] }[]
  counts: { total: number; done: number; running: number; queued?: number; generating?: number; awaiting_import?: number; error: number; pending: number }
}

export const fetchCreateCoverage = (project: string, board: string, type: 'image' | 'video' = 'image') =>
  getJSON<CreateCoverage>(`/api/create/coverage?project=${encodeURIComponent(project)}&board=${encodeURIComponent(board)}&type=${type}`)

export const fetchCreate = (project: string) =>
  getJSON<{ items: CreateItem[] }>(`/api/create?project=${encodeURIComponent(project)}`)

export const saveCreateItem = (project: string, item: CreateItem) =>
  postJSON<{ ok: boolean }>('/api/create/save', { project, item })

export const deleteCreateItem = (project: string, id: string) =>
  postJSON<{ ok: boolean }>('/api/create/delete', { project, id })

export interface GenericRunBody {
  step: string
  args: string[]
}

/** 通用脚本任务（深度图等走 /api/run）。 */
export interface RunResult {
  ok: boolean
  id?: number
  gen_script?: string
  source?: string
  err?: string
}

/** 台词人物归属（vision AI，按镜头批量标注 speaker）。 */
export const runLinesAttribute = (body: { project: string; analysis?: string }) =>
  postJSON<RunResult>('/api/lines/attribute', body)

export const runGeneric = (body: GenericRunBody) =>
  postJSON<RunResult>('/api/run', body)

/* ---------- 创作线：剧本 → 分集 → 人物/场景/道具 → 分镜 → 组装 ---------- */
export interface Episode {
  id: string
  title?: string
  text?: string
  hook?: string
  cliff?: string
  summary?: string
  duration_min?: number
  char_start?: number
  char_end?: number
  cast_refs?: string[]
  scene_refs?: string[]
  key_asset_refs?: string[]
}
export interface CharacterItem { id: string; name?: string; role?: string; basis?: string; dialogue_count?: number; parent_ref?: string; relation?: string; derived_from?: string; related_refs?: string[] }
export interface SceneItem { id: string; name?: string; time?: string; light?: string; interior?: boolean; geometry?: string[]; parent_ref?: string; relation?: string; derived_from?: string; related_refs?: string[] }
export interface PropItem { id: string; name?: string; kind?: string; owner?: string; shot_hint?: string | null; parent_ref?: string; relation?: string; derived_from?: string; related_refs?: string[] }
export interface ScriptBundle {
  script: string
  script_mode?: string | null
  script_rev?: number
  episodes: Episode[] | null
  characters: { characters: CharacterItem[] } | null
  scenes: { scenes: SceneItem[] } | null
  props: { props: PropItem[] } | null
  style?: { anchor?: string; image?: string; script?: string; storyboard?: string; acting?: string }
}
export const fetchScriptData = (project: string) =>
  getJSON<ScriptBundle>(`/api/script/data?project=${encodeURIComponent(project)}`)
export const importScript = (body: { project: string; text: string }) =>
  postJSON<{ ok: boolean; chars: number }>('/api/script/import', body)
export const scriptEpisodes = (project: string) =>
  postJSON<RunResult>('/api/script/episodes', { project })
export const scriptOverview = (project: string, episode?: string) =>
  postJSON<RunResult>('/api/script/overview', { project, episode })
export const scriptExtract = (project: string, episode?: string) =>
  postJSON<RunResult>('/api/script/extract', { project, episode })
/** 创作构想 → 大纲(=分集)；episode 给定则扩写该集为分场剧本。 */
export const scriptExpand = (body: { project: string; idea?: string; eps?: number; episode?: string }) =>
  postJSON<RunResult>('/api/script/expand', body)
/** 删除分集清单中的一集；全局资产与已生成产物由后端保留。 */
export const deleteScriptEpisode = (project: string, episode: string) =>
  postJSON<{ ok: boolean; episode?: string; remaining_episodes?: string[]; assets_unlinked?: number }>('/api/script/episode/delete', { project, episode })
/** 资产设定图生图：人物三视图/场景/道具（kind: character|scene|prop|all）。 */
export const genAssetImage = (body: { project: string; kind: string; id?: string; vendor_id?: string; force?: boolean; states?: 'include' | 'only' | 'skip'; state_id?: string }) =>
  postJSON<RunResult>('/api/asset/image', body)
/** 剧情战略图（2D 俯视走位/相机/运镜交互 HTML）。 */
export const buildStrategy = (project: string, storyboard: string) =>
  postJSON<RunResult>('/api/strategy/build', { project, storyboard })

export const scriptStoryboard = (project: string, episode?: string) =>
  postJSON<RunResult>('/api/script/storyboard', { project, episode })
export const creationAssemble = (project: string, storyboard: string) =>
  postJSON<RunResult>('/api/creation/package', { project, storyboard })
export interface ActingCompileResult {
  ok: boolean; shot_id: string; mode: string; mode_used?: string; media_type: string
  prompt: string; text?: string; baseline_prompt?: string; prompt_json?: Record<string, unknown> | null; asset_refs?: string[]; voice_notes?: string[]
  warnings?: string[]; source_hash: string; performance_used: boolean; supports_audio?: boolean
}
export const compileActingPrompt = (body: { project: string; storyboard: string; shot_id: string; mode?: string; media_type?: string; supports_audio?: boolean }) =>
  postJSON<ActingCompileResult>('/api/acting/compile', body)
export const runActing = (body: { project: string; storyboard: string; shot_id: string; vendor_id: string; context?: string; mode?: "style" | "stateful" }) =>
  postJSON<RunResult>('/api/acting/run', body)
export const evaluateActing = (body: { project: string; storyboard: string; shot_ids?: string[]; modes?: string[]; repeats?: number; experiment_id?: string; model?: string; vendor_id?: string }) =>
  postJSON<RunResult>('/api/acting/evaluate', body)

/** 演员评分（A=裸分镜 B=+导演风格 C=+风格+角色卡记忆，LLM 裁判逐镜对比） */
export interface ActingEvalScore { dims?: Record<string, number>; total?: number | null; reason?: string }
export interface ActingEvalShot { best?: string; reason?: string; A?: ActingEvalScore; B?: ActingEvalScore; C?: ActingEvalScore }
export interface ActingEval {
  experiment_id: string; created_at: string; board: string
  shots: string[]; modes: string[]
  summary?: { wins?: Record<string, number>; avg_total?: Record<string, number | null>; model?: string; note?: string }
  scores: Record<string, ActingEvalShot>
}
export const fetchActingEvals = (project: string, storyboard: string) =>
  getJSON<{ ok: boolean; evals: ActingEval[] }>('/api/acting/evals?project=' + encodeURIComponent(project) + '&storyboard=' + encodeURIComponent(storyboard))

export interface ActingShot {
  id: string; dur?: number; speaker?: string; action?: string; prompt?: string
  /** 演员层只允许生成的主角；空数组表示本镜没有可生成主角。 */
  actor_ids?: string[]; actor_names?: string[]
  performance_status: 'ready' | 'pending' | 'locked' | 'invalid' | string
  performance_locked: boolean; performance?: Record<string, unknown> | null
}
export interface ActingCandidate {
  run_id: string; candidate_kind: 'context' | 'performance' | string; status: string
  board: string; source_revision?: string; source_hash?: string; shot_ids?: string[]
  created_at?: string; path?: string
}
export interface ActingContextResponse {
  ok: boolean; board: string; revision: string
  context: Record<string, unknown>
  actors: Record<string, Record<string, unknown>> | Array<Record<string, unknown>>
  shots: ActingShot[]; candidates: ActingCandidate[]
}
export const fetchActingContext = (project: string, storyboard: string) =>
  getJSON<ActingContextResponse>('/api/acting/context?project=' + encodeURIComponent(project) + '&storyboard=' + encodeURIComponent(storyboard))
export const fetchActingCandidates = (project: string, storyboard: string) =>
  getJSON<{ ok: boolean; revision: string; candidates: ActingCandidate[] }>('/api/acting/candidates?project=' + encodeURIComponent(project) + '&storyboard=' + encodeURIComponent(storyboard))
export const saveActingContext = (body: { project: string; storyboard: string; revision?: string; context: Record<string, unknown> }) =>
  postJSON<{ ok: boolean; revision: string; context: Record<string, unknown> }>('/api/acting/context', body)
export const prepareActing = (body: { project: string; storyboard: string; shot_ids?: string[]; context?: Record<string, unknown>; vendor_id?: string }) =>
  postJSON<{ ok: boolean; id: number; job: true }>('/api/acting/prepare', body)
export const applyActingCandidate = (body: { project: string; storyboard: string; run_id: string; revision?: string }) =>
  postJSON<{ ok: boolean; run_id: string; revision: string; changed_shots: string[]; locked_shots: string[]; stale_shots: string[]; idempotent?: boolean }>('/api/acting/apply', body)
export const setActingLock = (body: { project: string; storyboard: string; shot_ids: string[]; locked: boolean; revision?: string }) =>
  postJSON<{ ok: boolean; revision: string; shot_ids: string[]; locked: boolean }>('/api/acting/lock', body)


/* ---------- 产出版本管理（最新原位 + .versions 历史） ---------- */
export interface FileVersion { ts: string; rel: string; current: boolean }
export const fetchVersions = (p: string) =>
  getJSON<{ versions: FileVersion[] }>(`/api/versions?p=${encodeURIComponent(p)}`)
export const restoreVersion = (p: string, ts: string) =>
  postJSON<{ ok: boolean }>('/api/versions/restore', { p, ts })

/* ---------- Skill 中心（影视创作垂直 skill，SKILL.md 格式） ---------- */
export interface SkillItem {
  id: string
  name: string
  category: string
  target: string
  enabled: boolean
  builtin: boolean
  description: string
  path: string
}
export const fetchSkillText = (id: string) =>
  getJSON<{ id: string; text: string }>(`/api/skills/get?id=${encodeURIComponent(id)}`)
export const saveSkill = (id: string, text: string, kind?: 'system' | 'style') =>
  postJSON<{ ok: boolean }>('/api/skills/save', { id, text, kind })

/** 读项目内文本/JSON 文件（走统一 getJSON，替代视图裸 fetch）。 */
export const fetchProjectFile = <T = unknown>(project: string, p: string) =>
  getJSON<T>(`/api/file?project=${encodeURIComponent(project)}&p=${encodeURIComponent(p)}`)

/* ---------- 分镜汇总表格：行内编辑回写 + xlsx 导出 ---------- */
export const saveStoryboardShots = (project: string, name: string, shots: unknown[]) =>
  postJSON<{ ok: boolean; shots: number }>('/api/storyboard/save', { project, name, shots })
export const exportStoryboardXlsx = (project: string, name: string) =>
  postJSON<{ ok: boolean; file: string }>('/api/storyboard/xlsx', { project, name })
/** 删除分镜文件（及同名单镜 xlsx）；.versions 历史快照保留。 */
export const deleteStoryboard = (project: string, name: string) =>
  postJSON<{ ok: boolean; deleted?: string[]; err?: string }>('/api/storyboard/delete', { project, name })

/** 只重建当前分镜提示词与资产修订索引，不调用生图/生视频厂商。 */
export const rebuildProductionPrompts = (body: { project: string; episode?: string; shot_ids?: string[] }) =>
  postJSON<{ ok: boolean; boards?: string[]; updated_prompts?: number; media_calls?: number; shots?: Array<Record<string, unknown>>; errors?: Array<Record<string, unknown>> }>(
    '/api/production/prompts', body
  )

/** 重建某一集的全部分镜提示词；不修改分集正文或大纲。 */
export const rebuildProductionEpisode = (body: { project: string; episode: string }) =>
  postJSON<{ ok: boolean; boards?: string[]; updated_prompts?: number; media_calls?: number; shots?: Array<Record<string, unknown>> }>(
    '/api/production/episode', body
  )

/** 计算资产修改影响的镜头范围。 */
export const affectedProductionShots = (body: { project: string; changed_refs: string[] }) =>
  postJSON<{ ok: boolean; shots?: string[]; reasons?: Record<string, string> }>('/api/production/affected', body)

/* ---------- 拉片知识库（自动归纳卡片 + 用户经验卡片）/ 看门狗 ---------- */
export interface KnowledgeSkill {
  id: string
  skill: string
  trigger: string[]
  prescription: string
  example: string
  count: number
}
export interface UserCard {
  id: string
  skill: string
  trigger: string[]
  prescription: string
  example: string
  count: number
  source: 'user'
}
export const fetchKnowledge = () =>
  getJSON<{ skills: KnowledgeSkill[]; cards: UserCard[] }>('/api/knowledge/list')
export const saveCard = (body: { id?: string; skill: string; trigger: string; prescription: string; example?: string }) =>
  postJSON<{ ok: boolean; id?: string }>('/api/knowledge/card', body)
export const deleteCard = (id: string) =>
  postJSON<{ ok: boolean }>('/api/knowledge/card/delete', { id })
export const previewKnowledge = (text: string) =>
  getJSON<{ hits: KnowledgeSkill[] }>(`/api/knowledge/preview?text=${encodeURIComponent(text)}`)
export const buildKnowledge = () => postJSON<RunResult>('/api/knowledge/build', {})
export interface WatchdogInfo {
  interval_min: number
  log: string[]
  active: { id: number; step: string; status: string; attempts: number }[]
}
/** @deprecated 兼容入口；当前页面不使用，不作为新功能接入点。 */
export const fetchWatchdog = () => getJSON<WatchdogInfo>('/api/watchdog')

/** 新建项目：上传本地视频字节流到 projects/<项目>/拉片素材/（无视频后端不收，故必选）。 */
export async function importVideo(
  project: string,
  name: string,
  data: Blob
): Promise<{ ok: boolean; path: string }> {
  const r = await fetch(
    `/api/import?project=${encodeURIComponent(project)}&name=${encodeURIComponent(name)}`,
    { method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: data }
  )
  const j = (await r.json().catch(() => ({}))) as { ok?: boolean; path?: string; err?: string }
  if (!r.ok || j.ok === false) throw new ApiError(r.status, j.err || `${r.status} ${r.statusText}`)
  return { ok: true, path: j.path || '' }
}

/** 从 Downloads 导入源视频（src 为绝对路径或 Downloads 文件名，后端复制到 项目/拉片素材/）。 */
/** 新建项目：type=拆片(配视频) | 制作(纯剧本创作)。 */
export const newProject = (project: string, type: '拆片' | '制作') =>
  postJSON<{ ok: boolean; type: string }>('/api/project/new', { project, type })

export const importSrc = (project: string, src: string) =>
  postJSON<{ ok: boolean; path: string }>('/api/import_src', { project, src })

/** 本机打开 .blend（调起系统默认程序）。 */
export const openBlend = (path: string) =>
  postJSON<{ ok: boolean; opened?: string }>('/api/openblend', { path })

/** 读文本文件（srt/txt/md 预览、脚本预览）。与 getJSON 共用 fetchWithTimeout 通道（超时+取消语义一致）。 */
export async function fetchText(p: string): Promise<string> {
  const r = await fetchWithTimeout(`/api/file?p=${encodeURIComponent(p)}`)
  if (!r.ok) throw new ApiError(r.status, `${r.status}`)
  return r.text()
}

/** 删除项目内产物（产物区白名单：拉片/分镜/白模/深度/逐帧/成片/三维探索/白模3D/创作/素材/演员/推演/台词；拉片素材不可删）。 */
export const deleteFile = (project: string, path: string) =>
  postJSON<{ ok: boolean; deleted?: string; kind?: string }>('/api/file/delete', { project, path })

/** 项目内媒体（视频/图片，支持 Range）。 */
export const mediaUrl = (p: string) => `/media?p=${encodeURIComponent(p)}`
/** Downloads 源视频（支持 Range）。 */
/** @deprecated 兼容入口；当前页面不使用，不作为新功能接入点。 */
export const srcUrl = (p: string) => `/src?p=${encodeURIComponent(p)}`

/** 任务是否已结束（兼容 done/failed/error 及 ok/returncode 字段）。 */
export function jobDone(j: JobInfo): boolean {
  return !['running', 'queued', 'pending'].includes(j.status)
}

/** 任务是否算成功（兼容 done/failed/error 及 ok/returncode 字段）。 */
export function jobOk(j: JobInfo): boolean {
  if (j.ok === false) return false
  if (j.returncode !== undefined && j.returncode !== 0) return false
  return ['done', 'completed', 'success', 'succeeded'].includes(j.status)
}

export function fmtT(t: number): string {
  const m = Math.floor(t / 60)
  const s = t - m * 60
  return `${String(m).padStart(2, '0')}:${s.toFixed(1).padStart(4, '0')}`
}

export function fmtClock(sec: number): string {
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}












