export interface VideoSettingsValue { mode?: string; resolution?: string; ratio?: string; duration?: number; seed?: number; generate_audio?: boolean }
export interface VideoCapability {
  known:boolean; model:string; modes:string[]; default_mode:string; resolutions:string[]; ratios:string[]
  min_duration:number; max_duration:number; max_refs:number; max_audio:number; max_video:number
  frame_adaptive:boolean; first_frame:boolean; audio_refs:boolean; transport:string; audio_output:boolean; seed:boolean
}
