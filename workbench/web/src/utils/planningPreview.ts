interface VersionRef { id: string; history_id?: string }

export interface PlanningPreviewState<V, T> {
  selected: V | null
  displayed: { version: V; preview: T; baseline: T } | null
  loading: boolean
  error: string
}

// 版本、正文和比较基线作为一个快照提交；加载期间保留上次成功内容。
export function createPlanningPreview<V extends VersionRef, T>(
  fetch: (project: string, version?: string) => Promise<T>,
  publish: (state: PlanningPreviewState<V, T>) => void,
) {
  let project = '', sequence = 0
  let state: PlanningPreviewState<V, T> = { selected: null, displayed: null, loading: false, error: '' }
  function update(next: PlanningPreviewState<V, T>) { state = next; publish(state) }
  function reset(nextProject: string) {
    project = nextProject
    sequence++
    update({ selected: null, displayed: null, loading: false, error: '' })
  }
  async function select(version: V) {
    if (!project) return
    const request = ++sequence, requestedProject = project
    update({ ...state, selected: version, loading: true, error: '' })
    try {
      const [preview, baseline] = await Promise.all([
        fetch(requestedProject, version.id),
        fetch(requestedProject, version.history_id || undefined),
      ])
      if (request !== sequence) return
      update({ selected: version, displayed: { version, preview, baseline }, loading: false, error: '' })
    } catch (error) {
      if (request !== sequence) return
      update({ ...state, loading: false, error: error instanceof Error ? error.message : '读取规划版本失败' })
    }
  }
  function canAct(value = state) {
    return !value.loading && !value.error && !!value.displayed && value.selected?.id === value.displayed.version.id
  }
  return { reset, select, canAct }
}
