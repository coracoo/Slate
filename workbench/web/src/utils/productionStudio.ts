import { getJSON, postJSON, type CreateItem } from '../api'
import { clearPendingRequest, durableRequest, pendingRequest, type RequestBody } from './durableRequest'
import type { VideoCapability, VideoSettingsValue } from './videoSettings'

export interface GenerationOptions {ref_mode?: string; tail_mode?: string; tail_item?: string; vision_vendor?: string; include_voices?: boolean; video_options?: VideoSettingsValue; first_shot_id?:string; last_shot_id?:string; image_urls?:Record<string,string>; audio_urls?:string; video_urls?:string}

export interface MediaBinding { item_id: string; path: string; sha256: string; source_hash: string }
export interface ProductionShot {
  id: string; dur: number; scene_ref?: string; action?: string; actor_refs?: string[]; prop_refs?: string[]
  prompt?: string; prompt_image?: string; prompt_video?: string; prompt_grid?: string; negative?: string[] | string
  keyframe?: MediaBinding; video_binding?: MediaBinding; video_duration?: number; generation_options?: GenerationOptions
  content?: string; shot_size?: string; angle?: string; lens?: string; camera_move?: string; sound?: string; lighting?: string; light?: string
  lines?: {speaker?: string; line?: string; text?: string}[]
}
export interface VideoUnit {
  id: string; label?: string; title: string; shot_ids: string[]; scene_ref?: string; duration: number
  prompt_video?: string; prompt_grid?: string; negative?: string; source_hash?: string; stale?: boolean
  video_binding?: MediaBinding; grid_binding?: MediaBinding; video_stale?: boolean; generation_options?: GenerationOptions; timeline?: {shot_id: string; start: number; end: number}[]
  judge?: {ok: boolean; warnings: string[]}
}
export interface StudioState {
  board: {shots: ProductionShot[]; video_units?: VideoUnit[]}; revision: string
  capabilities: Record<string, VideoCapability>
  asset_previews?: {path: string; purpose: string; shot_id: string}[]
  /** 制作规格（E05）：V 总时长超过单集目标时长时后端给出的提示。 */
  brief_notice?: string
}
/** 局部修补（重做片段）候选的锚点溯源信息：redo.t0–t1 为拼回窗口，anchors 为原片首尾锚点帧。 */
export interface RedoInfo {t0: number; t1: number; anchors?: {head: string; tail: string}; source_output?: {item_id: string; output_index: number; path?: string}; defer?: boolean; merged?: boolean; version?: string; version_name?: string; from_item?: string}
export type ProductionItem = CreateItem & {scope?: string; unit_id?: string; actual_duration?: number; archive_error?: string; action?: string; note?: string; grid?: boolean; redo?: RedoInfo}
export const studioData = (project: string, board: string) => getJSON<StudioState>(`/api/studio/data?project=${encodeURIComponent(project)}&board=${encodeURIComponent(board)}`)
export const studioPost = (path: string, body: unknown) => postJSON<{ok: boolean; id?: number; item_id?: string; reused?: boolean}>(`/api/studio/${path}`, body)
export const fetchStudioSettings = () => getJSON<{ok: boolean; default_video_duration: number}>('/api/studio/settings')
export const saveStudioSettings = (body: {default_video_duration: number}) => postJSON<{ok: boolean; default_video_duration: number}>('/api/studio/settings', body)
// 未确认请求自动对账：按 nonce 查后端是否已落地——落地即静默确认清除；未落地则幂等补交
// （nonce 相同，服务端去重，绝不重复生成）。服务不可达时不动记录，人工「接管」按钮保留兜底。
export async function reconcilePending(project: string) {
  const previous = pendingRequest(project)
  if (!previous) return
  try {
    const r = await getJSON<{found: boolean}>(`/api/production/confirm?project=${encodeURIComponent(project)}&nonce=${encodeURIComponent(previous.nonce)}`)
    if (r.found) { clearPendingRequest(project); return }
  } catch { return }
  const resend = (url: string) => postJSON<{ok: boolean}>(url, { ...previous.body, nonce: previous.nonce })
  const result = await (previous.body.action === 'redo_segment'
    ? resend('/api/production/redo_segment')
    : resend('/api/studio/job')).catch((e: {status?: number}) => {
      if ([400, 401, 403, 404, 409, 422].includes(e.status || 0)) clearPendingRequest(project)   // 参数类拒绝：坏请求直接出清
      return null
    })
  if (result) clearPendingRequest(project)
}
export const submitStudioJob = async (body: RequestBody, recover = false) => {
  await reconcilePending(String(body.project || ''))
  return navigator.locks.request('slate-production:' + body.project, () => durableRequest(body, b => studioPost('job', b), recover))
}
// 局部修补与生成走同一持久化通道（稳定 nonce + 自动对账），只是落在独立路由上。
export const submitRedoJob = async (body: RequestBody, recover = false) => {
  await reconcilePending(String(body.project || ''))
  return navigator.locks.request('slate-production:' + body.project, () => durableRequest(body, b => postJSON<{ok: boolean; id?: number; item_id?: string; reused?: boolean}>('/api/production/redo_segment', b), recover))
}
