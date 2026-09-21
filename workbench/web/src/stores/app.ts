// -*- coding: utf-8 -*-
/** 全局响应式状态：当前项目、项目树、源视频、Toast。无 pinia，模块级 reactive 即可。 */
import { reactive, computed } from 'vue'
import { fetchProjects, fetchSources, type Project, type SourceVideo } from '../api'

export const app = reactive({
  projects: [] as Project[],
  sources: [] as SourceVideo[],
  loading: false,
  current: localStorage.getItem('wb.project') || '',
  reducedMotion: window.matchMedia('(prefers-reduced-motion: reduce)').matches
})

export const currentProject = computed(() =>
  app.projects.find((p) => p.name === app.current)
)

export function selectProject(name: string) {
  app.current = name
  localStorage.setItem('wb.project', name)
}

export function projectFiles(sub: string, re?: RegExp): string[] {
  const p = currentProject.value
  if (!p) return []
  const list = p.dirs[sub] || []
  const visible = list.filter((f) => {
    const name = String(f || '')
    const parts = name.split('/')
    return !name.startsWith('[帧序列]')
      && !name.startsWith('.')
      && !parts.some((part) => part.startsWith('.'))
      && !parts.some((part) => part === '_versions.json' || part.startsWith('_versions.'))
  })
  return re ? visible.filter((f) => re.test(f)) : visible
}

/** 项目内第一个可用视频（素材 → 成片 → 白模 → 根目录）。 */
export function firstProjectVideo(): string {
  for (const sub of ['拉片素材', '成片', '白模', '根目录']) {
    const hit = projectFiles(sub, /\.(mp4|mov|mkv)$/i)[0]
    if (hit) return `projects/${app.current}/${sub === '根目录' ? '' : sub + '/'}${hit}`
  }
  return ''
}

/** 项目内全部视频（含子目录相对路径）。 */
export function projectVideos(): { sub: string; file: string; path: string }[] {
  const out: { sub: string; file: string; path: string }[] = []
  for (const sub of ['拉片素材', '成片', '白模', '深度', '白模3D', '根目录']) {
    for (const f of projectFiles(sub, /\.(mp4|mov|mkv)$/i)) {
      out.push({ sub, file: f, path: `projects/${app.current}/${sub === '根目录' ? '' : sub + '/'}${f}` })
    }
  }
  return out
}

/** 工作区相对路径 → 服务端所在机器的绝对路径。 */
export function absDiskPath(rel: string): string {
  const root = currentProject.value?.workspace_root
  if (!root) return rel
  const separator = root.includes('\\') || /^[A-Za-z]:/.test(root) ? '\\' : '/'
  return root.replace(/[\\/]+$/, '') + separator + rel.replace(/[\\/]/g, separator)
}

export interface MaterialVideo {
  file: string
  rel: string
  abs: string
}

/** 当前项目 拉片素材/ 下的视频清单——功能页「选源视频」的唯一数据来源。 */
export function materialVideos(): MaterialVideo[] {
  return projectFiles('拉片素材', /\.(mp4|mov|mkv)$/i).map((f) => {
    const rel = `projects/${app.current}/拉片素材/${f}`
    return { file: f, rel, abs: absDiskPath(rel) }
  })
}

export async function loadBasics() {
  app.loading = true
  try {
    const [ps, ss] = await Promise.all([fetchProjects(), fetchSources()])
    app.projects = ps
    app.sources = ss
    if (!ps.find((p) => p.name === app.current)) {
      app.current = ps[0]?.name || ''
      if (app.current) localStorage.setItem('wb.project', app.current)
    }
  } finally {
    app.loading = false
  }
}

/* ---------- Toast ---------- */
export interface Toast {
  id: number
  text: string
  kind: 'ok' | 'err' | 'info'
}
let toastSeq = 0
export const toasts = reactive<Toast[]>([])

export function toast(text: string, kind: Toast['kind'] = 'info', ms = 2600) {
  const id = ++toastSeq
  toasts.push({ id, text, kind })
  setTimeout(() => {
    const i = toasts.findIndex((t) => t.id === id)
    if (i >= 0) toasts.splice(i, 1)
  }, ms)
}
