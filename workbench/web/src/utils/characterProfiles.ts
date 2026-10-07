import { getJSON, postJSON } from '../api'
import type { CharacterAppearance } from './characterAppearance'
import type { VisualAssetStatus } from '../api'
import type { ReviewItem } from './reviewDiff'

export interface IdentityChange extends ReviewItem {
  kind:string; record:Record<string,unknown>; matches:string[]; batch?:string; batch_size:number; existing?:boolean
}

export interface OtherVisualAsset {
  id:string; name:string; kind:'scene'|'prop'; visual_description:string; in_use:boolean; visual_status:VisualAssetStatus
  states?:Array<{id:string;label?:string;look_diff?:string;episodes?:string[];visual_status?:VisualAssetStatus}>
}
export interface VisualReviewData {
  ok:boolean; characters:CharacterProfile[]; character_revision:string; revision:string
  other_assets:OtherVisualAsset[]; scenes:Array<{ref:string;name:string}>
  evidence?:{outline:Record<string,unknown>;episodes:Array<Record<string,unknown>>;foreshadows:Array<Record<string,unknown>>}
  records?:Record<string,Record<string,unknown>>
  identity_changes?:IdentityChange[]; reuse_changes?:ReviewItem[]; issues?:string[]
}
export const fetchVisualReview = (project:string) => getJSON<VisualReviewData>(`/api/assets/visual-review?project=${encodeURIComponent(project)}`)
export const saveVisualReview = (project:string,items:Array<{id:string;patch?:Record<string,unknown>;confirm?:{base:boolean;states:string[]};action?:string;target?:string;label?:string;difference?:string;episodes?:string[]}>,revision:string) =>
  postJSON<{ok:boolean;saved:string[];revision:string;validations:Record<string,{ready:boolean;field_labels:string[];warnings:string[]}>;confirmations:Array<{id:string;state_id:string|null;name:string;ready:boolean;warnings:string[]}>}>('/api/assets/visual-review',{project,items,revision})

export interface CharacterProfile {
  id: string
  name: string
  role?: string
  reserved?: boolean
  biography?: string
  bio_language?: string
  bio_crack?: string
  bio_pressure?: string
  bio_address?: string
  bio_arc?: string
  identity_anchor?: string
  sheet_prompt?: string
  voice?: string
  appearance?: CharacterAppearance
  visual_status?: VisualAssetStatus
  acting?: Record<string, unknown>
  relations?: Array<Record<string, unknown>>
  states?: Array<{id:string; label?:string; look_diff?:string; sheet_prompt?:string; episodes?:string[]; output_asset_ref?:string; visual_status?:VisualAssetStatus}>
  voice_binding?: { voice_asset_id: string; revision?: number }
}

export const fetchCharacters = (project: string) =>
  getJSON<{ok: boolean; characters: CharacterProfile[]; revision: string}>(`/api/characters?project=${encodeURIComponent(project)}`)

export const saveCharacter = (project: string, character_id: string, patch: Record<string, unknown>, expected_revision: string) =>
  postJSON<{ok: boolean; character: CharacterProfile; revision: string; visual_validation?: VisualAssetStatus}>('/api/characters/save', {project, character_id, patch, expected_revision})

export const saveCharacters = (project:string, items:Array<{character_id:string;patch:Record<string,unknown>}>, expected_revision:string) =>
  postJSON<{ok:boolean;saved:string[];revision:string;visual_validations:Record<string,VisualAssetStatus>}>('/api/characters/save',{project,items,expected_revision})
