<script setup lang="ts">
// -*- coding: utf-8 -*-
/** 剧本工作区：设定与规格 → 分集 → 单集正文。 */
import { ref, computed, watch, nextTick } from 'vue'
import {
  fetchScriptData, importScript, scriptEpisodes, scriptOverview, deleteScriptEpisode,
  fetchBrief, saveBrief, buildUnits, saveEpisodeText, fetchEpisodeHistory,
  ApiError, type ScriptBundle, type Episode, type ProductionBrief, type EpisodeEditResult
} from '../api'
import { app, toast } from '../stores/app'
import { trackJob } from '../stores/jobs'
import { pendingEpisodeIds } from '../utils/scriptEpisodes'
import EmptyState from '../components/EmptyState.vue'
import StoryUnitsCard from '../components/StoryUnitsCard.vue'
import ScriptHistory from '../components/ScriptHistory.vue'
import StyleSelect from '../components/StyleSelect.vue'
import StyledSelect from '../components/StyledSelect.vue'
import ScriptDrafts from '../components/ScriptDrafts.vue'
import PlanningCandidate from '../components/PlanningCandidate.vue'
import BatchDiffReview from '../components/BatchDiffReview.vue'
import type { ReviewItem } from '../utils/reviewDiff'

const data = ref<ScriptBundle | null>(null)
const loading = ref(false)
const scriptText = ref('')
const busy = ref(false)
const unitsBusy = ref(false)
const workflowRevision = ref(0)
const workflow = computed(() => data.value?.prompt_flow?.script_workflow)
const canExpand = computed(() => workflow.value?.can_expand ?? true)
const targetCountInput = ref<HTMLInputElement | null>(null)
const unitsCard = ref<InstanceType<typeof StoryUnitsCard> | null>(null)
const detail = ref<Episode | null>(null)
const mode = ref<'import' | 'idea'>('import')
const idea = ref('')
const revisionMode = ref<'extend' | 'rewrite'>('extend')
const revisionInstructions = ref('')
const workspaceTab = ref<'setup' | 'episodes' | 'drafts'>('episodes')
const visitedTabs = ref(new Set(['episodes']))
watch(workspaceTab, tab => visitedTabs.value.add(tab), {flush:'sync'})
const planningAction = ref<'plan' | 'extend' | 'rewrite' | 'split'>('plan')
const draftBusy = ref(false)
const candidateBusy = ref(false),candidateCard=ref<InstanceType<typeof PlanningCandidate>|null>(null)
const settingFieldLabel = (field: string) => ({biography:'人物小传', bio_language:'语言风格', bio_crack:'表达弱点', bio_pressure:'压力反应', bio_address:'称呼规则', bio_arc:'人物弧光', visual_description:'外观描绘', spatial_limit:'空间限制', action_slots:'动作位置', usage_boundary:'使用边界', 'appearance.face':'脸部设计', 'appearance.hair':'发型', 'appearance.body_type':'体型', 'appearance.outfit':'服装', 'appearance.species':'物种', 'appearance.distinctive_features':'辨识特征', 'appearance.look':'外观'} as Record<string,string>)[field] || field
const nextAction = computed(() => ideaDirty.value ? 'build' : workflow.value?.next_action || (canExpand.value ? 'expand' : 'build'))
const nextLabel = computed(() => ({build:'生成全剧规划', complete_settings:'补齐缺项并检查', repair:'查看并修改待处理项', anchor:'确认规划，进入正文', expand:rulesDirty.value ? '保存规则并核对规划' : '生成 / 改写正文'}[nextAction.value]))
async function showPreparation(path?: string, refStr?: string) {
  workspaceTab.value = 'setup'
  await nextTick()
  await unitsCard.value?.ready()
  if (path) await unitsCard.value?.showIssue(path, refStr)
}
async function advanceWorkflow() {
  if (!briefReady.value || mutationBusy.value || draftBusy.value || edit.value) return
  if (nextAction.value === 'build') return doExpand()
  if (nextAction.value === 'complete_settings') return completeSettings()
  if (nextAction.value === 'repair') {
    if (completionBlockers.value.some(e => e.code === 'SPEC_EP_COUNT')) return focusTargetCount()
    return showPreparation(workflow.value?.blockers?.[0]?.path)
  }
  if (rulesDirty.value) {
    const wasReady = nextAction.value === 'expand'
    if (!await saveBriefForm()) return
    await nextTick()
    if (wasReady && !canExpand.value) return showPreparation()
  }
  if (workflow.value?.can_anchor) {
    await showPreparation()
    if (unitsCard.value?.hasUnsaved) { toast('请先保存正在编辑的规划内容', 'info'); return }
    await unitsCard.value?.confirm()
    await load()
  }
  if (canExpand.value) workspaceTab.value = 'drafts'
  else await showPreparation()
}
async function executePlanning() {
  if (planningAction.value === 'split') return doEps()
  if (planningAction.value === 'plan') return doExpand()
  revisionMode.value = planningAction.value
  return reviseEpisodes()
}
const epDeleting = ref('')
const mutationBusy = computed(() => loading.value || busy.value || unitsBusy.value || candidateBusy.value || !!epDeleting.value || briefSaving.value)
const edit = ref<{ project: string; episode: string; text: string; base: string; revision: string } | null>(null)
const editHistory = ref<Array<{ id: string; label: string; current: boolean; text: string }>>([])
const historyPick = ref('')
const lastEditImpact = ref<EpisodeEditResult['affected'] | null>(null)
const draftKey = (project: string, episode: string) => `wb.${project}.script.draft.${episode}`
watch(edit, value => {
  if (value) localStorage.setItem(draftKey(value.project, value.episode), JSON.stringify(value))
}, { deep: true })
const progressRows = computed(() => data.value?.production_progress?.episodes || [])
const selectedProgress = computed(() => progressRows.value.find(row => row.id === detail.value?.id))
let editSeq = 0
async function beginEdit() {
  if (!app.current || !detail.value || mutationBusy.value) return
  const project = app.current, episode = detail.value.id, request = ++editSeq
  const current = { project, episode, text: selectedText.value, base: selectedText.value, revision: data.value?.script_revision || '' }
  try {
    const saved = JSON.parse(localStorage.getItem(draftKey(project, episode)) || 'null')
    edit.value = saved?.project === project && saved?.episode === episode ? saved : current
    if (edit.value?.revision !== current.revision) toast('本机草稿基于旧版本，已保留。请先对照最新正文再合并保存。', 'info', 6500)
  } catch { edit.value = current }
  historyPick.value = ''
  editHistory.value = []
  try {
    const r = await fetchEpisodeHistory(project, episode)
    if (request === editSeq && edit.value?.project === project && edit.value?.episode === episode) editHistory.value = r.versions
  } catch (e) { if (request === editSeq) toast(e instanceof Error ? e.message : '读取版本失败', 'err') }
}
function useHistory() {
  const version = editHistory.value.find(v => v.id === historyPick.value)
  if (version && edit.value) edit.value.text = version.text
}
function useLatestBaseline() {
  if (!edit.value) return
  edit.value.base = selectedText.value
  edit.value.revision = data.value?.script_revision || ''
}
function closeEdit() {
  edit.value = null
  editSeq++
}
const episodeReviewOpen=ref(false), episodeReviewItems=ref<ReviewItem[]>([]), episodeReviewError=ref('')
let reviewedEpisode:NonNullable<typeof edit.value> | null=null
function openEpisodeReview() {
  if(!edit.value || mutationBusy.value) return
  reviewedEpisode={...edit.value}
  episodeReviewItems.value=[{id:edit.value.episode,title:edit.value.episode,groups:[{label:'剧本正文',before:edit.value.base,after:edit.value.text}]}]
  episodeReviewError.value='';episodeReviewOpen.value=true
}
async function saveEditedEpisode() {
  if (!reviewedEpisode || mutationBusy.value || reviewedEpisode.project!==app.current) return
  const draft = { ...reviewedEpisode }
  busy.value = true
  try {
    const r = await saveEpisodeText({ project: draft.project, episode: draft.episode, text: draft.text, revision: draft.revision })
    episodeReviewOpen.value=false
    localStorage.removeItem(draftKey(draft.project, draft.episode))
    if (app.current === draft.project) {
      closeEdit()
      await load()
      lastEditImpact.value = r.affected
    }
    toast(r.changed ? `${draft.episode} 正文已保存，${r.affected.boards.length} 份分镜待复核` : '正文没有变化', 'ok', 5000)
    if (r.warnings.length) toast(r.warnings.join('；'), 'info', 6500)
  } catch (e) {
    if (e instanceof ApiError && e.status === 409 && app.current === draft.project) await load()
    toast(e instanceof Error ? e.message : '保存正文失败，编辑稿已保留', 'err', 7000)
    episodeReviewError.value=e instanceof Error?e.message:'保存正文失败，编辑稿已保留'
  }
  finally { busy.value = false }
}

/* ---------- 制作规格（E05）：剧本/brief.json 的编辑表单；保存即被大纲/扩写/生图/成片链路消费 ---------- */
const briefSaving = ref(false)
const briefReady = ref(false)
const briefLoadError = ref('')
const briefBaseline = ref('')
const briefForm = ref({
  episode_minutes: 3 as number,
  total_episodes: '' as string | number,
  aspect_ratio: '16:9',
  genre_tone: '',
  dialogue_density: '中',
  max_characters: '' as string | number,
  max_scenes: '' as string | number,
})

/** 切项目时允许传空对象，所有缺省值统一在此重置；读取成功另由 briefReady 控制。 */
function fillBriefForm(b: Partial<ProductionBrief>) {
  briefForm.value = {
    episode_minutes: b.episode_minutes ?? 3,
    total_episodes: b.total_episodes ?? '',
    aspect_ratio: b.aspect_ratio || '16:9',
    genre_tone: b.genre_tone || '',
    dialogue_density: b.dialogue_density || '中',
    max_characters: b.max_characters ?? '',
    max_scenes: b.max_scenes ?? '',
  }
  briefBaseline.value = JSON.stringify(briefForm.value)
}

/** 可空数字字段：空串 = 恢复默认（后端按 None 删键）。 */
const nullIfEmpty = (v: string | number) => (v === '' || v === null || v === undefined ? null : Number(v))

async function saveBriefForm() {
  if (!app.current || briefSaving.value) return
  if (!briefReady.value) { toast('制作规则尚未读取成功，请先重新读取再保存', 'err'); return false }
  const project = app.current
  briefSaving.value = true
  try {
    const r = await saveBrief(project, {
      episode_minutes: Number(briefForm.value.episode_minutes),
      total_episodes: nullIfEmpty(briefForm.value.total_episodes),
      aspect_ratio: briefForm.value.aspect_ratio,
      genre_tone: briefForm.value.genre_tone,
      dialogue_density: briefForm.value.dialogue_density,
      max_characters: nullIfEmpty(briefForm.value.max_characters),
      max_scenes: nullIfEmpty(briefForm.value.max_scenes),
    })
    if (app.current !== project) return false
    fillBriefForm(r.brief)
    await refreshWorkflow()
    toast('制作规则已保存', 'ok', 3000)
    return true
  } catch (e) {
    toast(e instanceof Error ? e.message : '保存制作规格失败', 'err', 6000)
    return false
  } finally {
    briefSaving.value = false
  }
}

async function refreshWorkflow() {
  const project = app.current
  if (!project) return
  try {
    const next = await fetchScriptData(project)
    if (app.current !== project || !data.value) return
    data.value.prompt_flow = next.prompt_flow
    workflowRevision.value++
  } catch (e) {
    toast(e instanceof Error ? e.message : '读取流程状态失败', 'err', 6000)
    throw e
  }
}

const rulesDirty = computed(() => JSON.stringify(briefForm.value) !== briefBaseline.value)
const ideaDirty = computed(() => mode.value === 'idea' && idea.value.trim() !== (data.value?.idea || '').trim())

/** 删除分集：只删分集清单并解除资产来源标签；资产、图片和已生成产物保留。 */
async function deleteEpisode(e: Episode) {
  if (!app.current || mutationBusy.value || edit.value) return
  const label = `${e.id}${e.title ? `「${e.title}」` : ''}`
  if (!window.confirm(`确定删除分集 ${label}？\n\n只删除分集清单并解除该集来源标签，资产、图片和已生成产物保留。`)) return
  epDeleting.value = e.id
  try {
    const result = await deleteScriptEpisode(app.current, e.id)
    const unlinked = result.assets_unlinked || 0
    if (detail.value?.id === e.id) detail.value = null
    await load()
    toast(`已删除分集 ${result.episode || label}${unlinked ? `，解除 ${unlinked} 条资产来源关联` : ''}`, 'ok', 5000)
  } catch (err) {
    toast(err instanceof Error ? err.message : '删除分集失败', 'err', 6000)
  } finally {
    epDeleting.value = ''
  }
}

const episodes = computed(() => data.value?.episodes || [])
const completionBlockers = computed(() => {
  const errors = (workflow.value?.completion_blockers || []).filter(e => e.code !== 'SPEC_EP_COUNT')
  const target = Number(briefForm.value.total_episodes)
  if (episodes.value.length && target > 0 && target !== episodes.value.length) {
    errors.unshift({ code: 'SPEC_EP_COUNT', path: '制作规则.目标集数',
      message: `全剧目标 ${target} 集，已有 ${episodes.value.length} 集。请先确认集数；补全不会新增或删除分集。` })
  }
  return errors
})
async function completeSettings() {
  const project = app.current
  if (!project || !briefReady.value) return
  if (rulesDirty.value && !await saveBriefForm()) return
  if (project !== app.current) return
  unitsBusy.value = true
  try {
    const r = await buildUnits({ project, stage: 'entity' })
    if (r.id) { const j = await trackJob(r.id, '自动补全设定层'); if (!j.success) throw new Error(j.err || '补全失败') }
    if (project === app.current) {
      await load()
      workspaceTab.value = 'setup'
      const remaining = workflow.value?.settings_missing?.length || 0
      toast(remaining ? `本次补全已结束，仍有 ${remaining} 个实体需要处理，请查看缺项列表` : '设定缺项已补齐，可在全剧规划中核对', remaining ? 'info' : 'ok', 6000)
    }
  } catch (e) { toast(e instanceof Error ? e.message : '补全失败', 'err', 6000); if (project === app.current) await load() } finally { unitsBusy.value = false }
}
async function focusTargetCount() {
  await nextTick()
  targetCountInput.value?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  targetCountInput.value?.focus({ preventScroll: true })
}
const scriptMode = computed(() => (data.value as any)?.script_mode || (episodes.value.some((e) => e.text) ? 'generated' : 'imported'))
const scriptReadonly = computed(() => scriptMode.value === 'generated')
const selectedText = computed(() => detail.value ? epSlice(detail.value) : '')
const paragraphs = computed(() => selectedText.value.split(/\n+/).filter(line => line.trim()))
function selectEpisode(episode: Episode) {
  if (edit.value && edit.value.episode !== episode.id) closeEdit()
  lastEditImpact.value = null
  detail.value = episode
  if (app.current) localStorage.setItem(`wb.${app.current}.script.episode`, episode.id)
}

let loadSeq = 0
async function load() {
  const project = app.current, seq = ++loadSeq
  data.value = null
  detail.value = null
  briefReady.value = false
  briefLoadError.value = ''
  fillBriefForm({})
  if (!project) { loading.value = false; return }
  loading.value = true
  try {
    const next = await fetchScriptData(project)
    // 丢弃已切换项目的旧响应。
    if (seq !== loadSeq) return
    data.value = next
    scriptText.value = next.script || ''
    idea.value = next.idea || ''
    mode.value = next.script_mode === 'generated' || next.idea || !next.script ? 'idea' : 'import'
    const selectedId = localStorage.getItem(`wb.${project}.script.episode`)
    detail.value = next.episodes?.find(e => e.id === selectedId) || next.episodes?.[0] || null
    workspaceTab.value = next.episodes?.length && next.prompt_flow?.script_workflow?.can_expand !== false ? 'episodes' : 'setup'
    workflowRevision.value++
    try {
      const b = await fetchBrief(project)
      if (seq !== loadSeq) return
      fillBriefForm(b.brief)
      briefReady.value = true
    } catch (e) {
      if (seq === loadSeq) briefLoadError.value = e instanceof Error ? e.message : '制作规则读取失败'
    }
  } catch (e) {
    if (seq === loadSeq) toast(e instanceof Error ? e.message : '加载失败', 'err')
  } finally { if (seq === loadSeq) loading.value = false }
}
watch(() => app.current, () => { episodeReviewOpen.value=false; closeEdit(); lastEditImpact.value = null;
  revisionInstructions.value = ''; void load() }, { immediate: true })

async function run(label: string, fn: () => Promise<{ id?: number; err?: string }>) {
  if (mutationBusy.value || edit.value) return
  busy.value = true
  try {
    const r = await fn()
    if (!r.id) { toast(`${label} 完成`, 'ok', 4000); await load(); return }  // 同步接口（importScript）无任务 id，抛错即失败
    const j = await trackJob(r.id, label)
    if (j.success) { toast(`${label} 完成`, 'ok', 4000); await load() }
    else throw new Error(j.err || `${label} 失败`)
  } catch (e) {
    toast(e instanceof Error ? e.message : `${label} 失败`, 'err', 6000)
  } finally { busy.value = false }
}
const doEps = () => run('生成分集', async () => {
  const project = app.current!
  const text = scriptText.value
  const dirty = text !== data.value?.script
  if (!await saveBriefForm()) throw new Error('请先保存制作规格')
  if (app.current !== project) throw new Error('项目已切换，未启动分集任务')
  if (dirty) await importScript({ project, text })
  if (app.current !== project) throw new Error('项目已切换，原稿已保存，未启动分集任务')
  return scriptEpisodes(project)
})
const doOverview = (episode?: string) => run(episode ? `更新 ${episode} 概要` : '更新分集概要', () => scriptOverview(app.current!, episode))

function epSlice(e: Episode): string {
  if (e.text) return e.text
  if (scriptMode.value === 'generated') return ''
  return (data.value?.script || '').slice(e.char_start ?? 0, e.char_end ?? undefined)
}
async function doExpand() {
  if (!app.current || mutationBusy.value || draftBusy.value || edit.value) return
  const project = app.current
  busy.value = true
  try {
    if (!await saveBriefForm() || project !== app.current) return
    if (completionBlockers.value.length) throw new Error(completionBlockers.value.map(e => e.message).join('；'))
    if (mode.value === 'import' && !scriptReadonly.value && scriptText.value !== data.value?.script) await importScript({project,text:scriptText.value})
    if (project !== app.current) return
    const result = await buildUnits({project,idea:mode.value==='idea'?idea.value || undefined:undefined,
      eps:Number(briefForm.value.total_episodes) || (episodes.value.length?0:6),stage:episodes.value.length?'complete':'all'})
    const job = await trackJob(result.id,'全剧规划')
    if (!job.success) throw new Error(job.err || '规划失败')
    if (project===app.current) {await load();workspaceTab.value='setup'}
  } catch(e) {toast(e instanceof Error?e.message:'规划失败','err',6000)}
  finally {busy.value=false}
}

async function reviseEpisodes() {
  if (!app.current || mutationBusy.value || edit.value) return
  const project = app.current, source = mode.value === 'idea' ? 'idea' : 'script'
  const target = Number(briefForm.value.total_episodes)
  if (!Number.isInteger(target) || target < 1 || target > 200) {
    toast('请填写 1–200 的全剧目标集数', 'err', 5000)
    await focusTargetCount()
    return
  }
  if (revisionMode.value === 'extend' && target <= episodes.value.length) {
    toast('扩写的目标集数须大于已有集数；调整或缩集请选择改写', 'err', 5000)
    return
  }
  if (revisionMode.value === 'rewrite' && !revisionInstructions.value.trim()) {
    toast('请填写修改要求', 'err', 5000)
    return
  }
  busy.value = true
  try {
    if (!await saveBriefForm() || project !== app.current) return
    if (source === 'script' && scriptText.value !== data.value?.script) await importScript({project,text:scriptText.value})
    if (project !== app.current) return
    const result = await buildUnits({ project, stage: 'replan', eps: target, planning_source: source,
      idea: source === 'idea' ? idea.value : undefined, revision_mode: revisionMode.value,
      revision_instructions: revisionInstructions.value.trim() })
    const job = await trackJob(result.id, revisionMode.value === 'extend' ? '扩写分集' : '改写分集')
    if (!job.success) throw new Error(job.err || '修订失败，当前内容保留')
    toast('规划工作候选已生成，请审核采用', 'ok', 6000)
    if (project === app.current) {
      await load()
      workspaceTab.value = 'setup'
      await candidateCard.value?.reviewCurrent()
    }
  } catch (e) { toast(e instanceof Error ? e.message : '修订失败', 'err', 7000) }
  finally {
    busy.value = false
    if (project === app.current) await refreshWorkflow()
  }
}

const pendingEpisodes = computed(() => pendingEpisodeIds(episodes.value))
</script>


<template>
  <div class="page">
    <header class="mb-4 flex items-center gap-3"><h1 class="grad-text text-2xl font-black">剧本工作区</h1></header>
    <EmptyState v-if="!app.current" title="请先选择项目" />
    <div v-else class="authoring-layout">
      <aside class="glass space-y-4 p-4 authoring-settings">
        <h2 class="font-bold text-slate-100">故事与制作规则</h2>
        <p v-if="briefLoadError" role="alert" class="text-sm text-rose-200">制作规则读取失败：{{briefLoadError}}<button class="ml-2 underline" :disabled="loading" @click="load">重新读取</button></p>
        <label class="block text-sm">来源<select v-model="mode" class="input mt-1"><option value="idea">故事构想</option><option value="import">剧本原文</option></select></label>
        <textarea v-if="mode==='idea'" v-model="idea" class="textarea w-full" rows="6" aria-label="故事构想" placeholder="人物、目标、冲突、结局方向"></textarea>
        <textarea v-else v-model="scriptText" class="textarea w-full" rows="8" :readonly="scriptReadonly" aria-label="剧本原文" placeholder="粘贴剧本原文"></textarea>
        <p v-if="mode==='import' && scriptReadonly" class="text-xs text-slate-400">此处展示分集正文汇总，在右侧编辑各集。</p>
        <div class="grid grid-cols-2 gap-3">
          <label class="text-xs text-slate-300">目标集数<input ref="targetCountInput" v-model="briefForm.total_episodes" type="number" min="1" max="200" class="input mt-1" placeholder="6" /></label>
          <label class="text-xs text-slate-300">单集分钟<input v-model.number="briefForm.episode_minutes" type="number" min="0.5" max="10" step="0.5" class="input mt-1" /></label>
          <label class="text-xs text-slate-300">画幅<StyledSelect v-model="briefForm.aspect_ratio" :options="['16:9','9:16','1:1','4:3']" class="mt-1" /></label>
          <label class="text-xs text-slate-300">对白密度<StyledSelect v-model="briefForm.dialogue_density" :options="['低','中','高']" class="mt-1" /></label>
        </div>
        <details><summary class="cursor-pointer text-sm text-sky-200">编剧方法与资源约束</summary><div class="mt-3 space-y-3">
          <StyleSelect target="script_structure" label="叙事结构" @changed="refreshWorkflow" /><StyleSelect target="script_pacing" label="剧情节奏" @changed="refreshWorkflow" /><StyleSelect target="script_continuity" label="连续性" @changed="refreshWorkflow" />
          <label class="block text-xs">基调<input v-model="briefForm.genre_tone" class="input mt-1" maxlength="200" /></label>
          <label class="block text-xs">主要人物上限<input v-model="briefForm.max_characters" type="number" min="1" class="input mt-1" placeholder="不限" /></label>
          <label class="block text-xs">主要场景上限<input v-model="briefForm.max_scenes" type="number" min="1" class="input mt-1" placeholder="不限" /></label>
        </div></details>
        <label class="block text-sm">规划操作<select v-model="planningAction" class="input mt-1"><option value="plan">{{episodes.length?'补全规划缺项':'生成全剧规划'}}</option><option v-if="episodes.length" value="extend">扩集 · 保留已有框架</option><option v-if="episodes.length" value="rewrite">改写 · 调整已有框架</option><option v-if="mode==='import' && !scriptReadonly" value="split">按原稿拆分集</option></select></label>
        <textarea v-if="planningAction==='extend'||planningAction==='rewrite'" v-model="revisionInstructions" class="textarea w-full" rows="3" maxlength="4000" placeholder="需要调整的剧情、节奏、关系；需要保留的事件"></textarea>
        <button v-if="planningAction!=='plan'" class="btn w-full" :disabled="!briefReady || mutationBusy || draftBusy || !!edit" @click="executePlanning">{{busy?'生成中…':planningAction==='extend'?'保存规则并扩集':planningAction==='rewrite'?'保存规则并改写规划':'按原稿拆分集'}}</button>
        <button v-if="rulesDirty" class="text-xs text-sky-200" :disabled="!briefReady || mutationBusy || draftBusy" @click="saveBriefForm">仅保存规则</button>
        <p class="text-xs text-slate-400">已有 {{episodes.length}} 集 · {{pendingEpisodes.length}} 集缺正文</p>
      </aside>
      <main class="min-w-0 space-y-4">
        <PlanningCandidate ref="candidateCard" :project="app.current" :revision="workflowRevision" :disabled="mutationBusy || draftBusy || !!edit || rulesDirty || ideaDirty" @busy-change="candidateBusy=$event" @changed="load" />
        <section class="glass p-4 space-y-3" aria-label="剧本下一步">
          <div class="flex flex-wrap items-center gap-3"><h2 class="mr-auto font-bold text-slate-100">{{ canExpand ? '规划已确认' : '规划准备' }}</h2><button class="btn" :disabled="!briefReady || mutationBusy || draftBusy || !!edit" @click="advanceWorkflow">{{mutationBusy || draftBusy ? '处理中…' : nextLabel}}</button></div>
          <p v-if="workflow?.reason && !canExpand" class="text-sm text-slate-300">{{workflow.reason}}</p>
          <div v-if="workflow?.settings_missing?.length" class="flex flex-wrap items-center gap-3">
            <span class="text-sm text-amber-200">{{workflow.settings_missing.length}} 个素材缺少设定</span>
            <button v-if="nextAction!=='complete_settings'" class="btn btn-sm" :disabled="!briefReady || !workflow.can_complete || mutationBusy || draftBusy || !!edit || ideaDirty" @click="completeSettings">批量补齐素材设定</button>
          </div>
          <details v-if="workflow?.settings_missing?.length" class="text-sm"><summary class="cursor-pointer text-amber-200">查看 {{workflow.settings_missing.length}} 个缺项与编辑入口</summary><div class="mt-2 max-h-64 overflow-auto space-y-2"><div v-for="item in workflow.settings_missing" :key="item.ref" class="flex items-start gap-3 border-t border-white/10 py-2"><span class="flex-1">{{item.kind}} · {{item.name || item.id}}<small class="block text-slate-400">{{item.missing.map(f=>settingFieldLabel(f)).join('、')}}</small></span><RouterLink v-if="item.kind==='人物'" class="text-sky-200 underline" :to="{path:'/studio/characters',query:{character:item.id}}">编辑</RouterLink><button v-else class="text-sky-200 underline" @click="showPreparation(item.kind,item.ref)">编辑</button></div></div></details>
          <div v-for="issue in (workflow?.blockers || []).filter(e=>!['CHARACTER_SETTINGS_INCOMPLETE','ASSET_SETTINGS_INCOMPLETE'].includes(e.code))" :key="issue.path+issue.code" class="flex gap-3 text-sm text-amber-200"><span class="flex-1">{{issue.message}}</span><button class="shrink-0 underline" @click="issue.code==='SPEC_EP_COUNT'?focusTargetCount():showPreparation(issue.path)">去修改</button></div>
        </section>
        <nav class="flex flex-wrap gap-2" aria-label="剧本内容"><button v-for="tab in [{id:'episodes',label:'分集正文'},{id:'drafts',label:'生成与审核'},{id:'setup',label:'全剧规划'}]" :key="tab.id" class="btn" :class="workspaceTab===tab.id?'':'btn-ghost'" @click="workspaceTab=tab.id as typeof workspaceTab">{{tab.label}}</button><ScriptHistory :project="app.current" /></nav>
        <section v-if="visitedTabs.has('setup')" v-show="workspaceTab==='setup'" class="space-y-4">
          <StoryUnitsCard ref="unitsCard" managed-confirmation :project="app.current" :revision="workflowRevision" :can-lock="briefReady && !rulesDirty && !ideaDirty" :disabled="!briefReady || mutationBusy || draftBusy || !!edit || rulesDirty || ideaDirty" @busy-change="unitsBusy=$event" @changed="load" />
        </section>
        <section v-if="visitedTabs.has('drafts')" v-show="workspaceTab==='drafts'" class="glass p-4"><ScriptDrafts :project="app.current" :episodes="episodes" :can-generate="briefReady && canExpand && !rulesDirty && !ideaDirty" :disabled="!briefReady || mutationBusy || !!edit || rulesDirty || ideaDirty" @prepare="showPreparation()" @busy-change="draftBusy=$event" @changed="load" /></section>
        <section v-if="workspaceTab==='episodes'" class="script-workspace">
          <aside class="glass p-3"><h2 class="mb-3 text-sm font-bold">分集 · {{episodes.length}}</h2><div class="script-episode-list">
            <button v-for="ep in episodes" :key="ep.id" class="script-episode" :class="detail?.id===ep.id?'selected':''" @click="selectEpisode(ep)"><b class="text-sky-200">{{ep.id}}</b> · {{ep.title || '未命名'}}<small class="mt-1 block text-slate-300">{{ep.text?.trim()?`${ep.text.length} 字`:'待生成'}} · {{ep.duration_min || '?'}} 分钟</small></button>
          </div></aside>
          <article v-if="detail" class="glass min-w-0 p-5">
            <header class="mb-4 flex flex-wrap items-center gap-2"><h2 class="mr-auto text-lg font-bold">{{detail.id}} · {{detail.title}}</h2><ScriptHistory :project="app.current" :episode="detail.id" /><button v-if="!edit" class="btn btn-sm" :disabled="mutationBusy || draftBusy" @click="beginEdit">编辑</button><button class="btn btn-ghost btn-sm" @click="workspaceTab='drafts'">生成 / 改写</button><details class="relative"><summary class="cursor-pointer text-sm text-slate-300">更多</summary><div class="absolute right-0 z-10 mt-2 grid w-36 gap-2 rounded border border-white/20 bg-slate-900 p-2"><button :disabled="mutationBusy || draftBusy || !!edit || rulesDirty || ideaDirty" @click="doOverview(detail.id)">更新概要</button><button class="text-red-300" :disabled="mutationBusy || draftBusy || !!edit || rulesDirty || ideaDirty" @click="deleteEpisode(detail)">删除分集</button></div></details></header>
            <p v-if="detail.planning_review_required" class="mb-3 text-sm text-amber-200">本集规划已修改，当前正文保留，可在生成与审核中改写。</p><p v-if="selectedProgress?.storyboard_review" class="mb-3 text-sm text-amber-200">本集正文有修改，关联分镜待复核。</p>
            <details class="mb-4"><summary class="cursor-pointer text-sm text-sky-200">本集规划与引用</summary><div class="mt-3 space-y-2 text-sm leading-7 text-slate-300"><p>{{detail.summary}}</p><p>开场：{{detail.hook || '未填写'}}</p><p>落点：{{detail.cliff || '未填写'}}</p><p>{{[...(detail.cast_refs||[]),...(detail.scene_refs||[])].join('、')}}</p></div></details>
            <section v-if="edit" class="space-y-3"><div class="flex flex-wrap gap-2"><button class="btn" :disabled="mutationBusy || draftBusy || !edit.text.trim()" @click="openEpisodeReview">审核并保存正文</button><button class="btn btn-ghost" @click="closeEdit">返回阅读</button><select v-if="editHistory.length" v-model="historyPick" class="input flex-1" @change="useHistory"><option value="">读取历史稿</option><option v-for="version in editHistory" :key="version.id" :value="version.id">{{version.label}}</option></select></div>
              <p v-if="edit.revision!==data?.script_revision" class="text-sm text-amber-200">已保存稿有更新，请合并后<button class="underline" @click="useLatestBaseline">更新保存基线</button>。</p>
              <textarea v-model="edit.text" class="textarea w-full text-base leading-8" rows="18" aria-label="单集正文编辑器"></textarea>
            </section>
            <div v-else class="screenplay-reader"><p v-if="!paragraphs.length" class="py-12 text-center text-slate-400">尚无正文，在「生成与审核」选择本集生成。</p><p v-for="(line,index) in paragraphs" :key="index" :class="line.trim().startsWith('【')?'screenplay-heading':''">{{line}}</p></div>
          </article>
          <EmptyState v-else title="先生成或导入分集规划" />
        </section>
      </main>
    </div>
  </div>
  <BatchDiffReview v-model:open="episodeReviewOpen" title="审核剧本正文" :items="episodeReviewItems" :busy="busy" :error="episodeReviewError" submit-label="确认保存" @confirm="saveEditedEpisode" />
</template>

<style scoped>
.authoring-layout { display:grid;grid-template-columns:minmax(260px,320px) minmax(0,1fr);gap:1rem;align-items:start }
.authoring-settings { position:sticky;top:0;max-height:calc(100vh - 7rem);overflow:auto }
.script-workspace { display: grid; grid-template-columns: minmax(145px, 190px) minmax(0, 1fr); gap: 1rem; align-items: start; }
.script-episode-list { display: grid; gap: .5rem; max-height: 70vh; overflow-y: auto; }
.script-episode { width: 100%; text-align: left; padding: .8rem; border: 1px solid transparent; border-radius: .75rem; background: rgb(255 255 255 / .03); }
.script-episode:hover { background: rgb(255 255 255 / .07); }
.script-episode.selected { border-color: rgb(56 189 248 / .5); background: rgb(56 189 248 / .12); }
.screenplay-reader { max-width: 78ch; margin: 0 auto; color: #e2e8f0; font-size: 1rem; line-height: 1.95; overflow-wrap: anywhere; }
.screenplay-reader p { white-space: pre-wrap; margin-bottom: .85rem; }
.screenplay-reader .screenplay-heading { color: #bae6fd; font-weight: 700; margin-top: 1.5rem; padding-bottom: .45rem; border-bottom: 1px solid rgb(255 255 255 / .08); }
@media (max-width: 1150px) {.authoring-layout{grid-template-columns:1fr}.authoring-settings{position:static;max-height:none}}
@media (max-width: 900px) { .script-workspace { grid-template-columns: 1fr; } .script-episode-list { grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); max-height: 35vh; } }
</style>
