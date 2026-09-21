import { getJSON, postJSON, type CreateItem } from '../api'
import { durableRequest, type RequestBody } from './durableRequest'
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
  video_binding?: MediaBinding; video_stale?: boolean; generation_options?: GenerationOptions; timeline?: {shot_id: string; start: number; end: number}[]
  judge?: {ok: boolean; warnings: string[]}
}
export interface StudioState {
  board: {shots: ProductionShot[]; video_units?: VideoUnit[]}; revision: string
  capabilities: Record<string, VideoCapability>
  asset_previews?: {path: string; purpose: string; shot_id: string}[]
}
export type ProductionItem = CreateItem & {scope?: string; unit_id?: string; actual_duration?: number; archive_error?: string}
export const studioData = (project: string, board: string) => getJSON<StudioState>(`/api/studio/data?project=${encodeURIComponent(project)}&board=${encodeURIComponent(board)}`)
export const studioPost = (path: string, body: unknown) => postJSON<{ok: boolean; id?: number; item_id?: string; reused?: boolean}>(`/api/studio/${path}`, body)
export const fetchStudioSettings = () => getJSON<{ok: boolean; default_video_duration: number}>('/api/studio/settings')
export const saveStudioSettings = (body: {default_video_duration: number}) => postJSON<{ok: boolean; default_video_duration: number}>('/api/studio/settings', body)
export const submitStudioJob = (body: RequestBody, recover = false) => navigator.locks.request('slate-production:' + body.project, () => durableRequest(body, b => studioPost('job', b), recover))
