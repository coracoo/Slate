import { getJSON, postJSON, type SkillItem } from '../api'
import { createProjectStyleWriter } from './projectStyle.ts'

type Options = {skills:SkillItem[];style:Record<string,string>}
const inflight = new Map<string,Promise<Options>>()

export const saveProjectStyle = createProjectStyleWriter(
  async project => (await getJSON<Options>(`/api/skills/style?project=${encodeURIComponent(project)}`)).style || {},
  (project,_style,patch) => postJSON('/api/skills/style',{project,patch}),
)

// 同页多个选择器共用正在进行的读取；请求结束即清理，后续编辑即时可见。
export function fetchStyleOptions(project:string) {
  const current = inflight.get(project)
  if (current) return current
  const request = getJSON<Options>(`/api/skills/style?project=${encodeURIComponent(project)}`)
    .finally(() => { if (inflight.get(project) === request) inflight.delete(project) })
  inflight.set(project, request)
  return request
}
