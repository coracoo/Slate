<script setup lang="ts">
// -*- coding: utf-8 -*-
/** 项目级素材族：母素材大图 + 子素材缩略图；层级关系由后台 JSON 管理。 */
import { ref, computed, watch, onMounted, onBeforeUnmount, reactive, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import {
  fetchScriptData, scriptExtract, genAssetImage, fetchEnvConfig, fetchAssets, fetchAssetPromptLayers, createAsset, editAsset, saveAssetRelations, rebuildProductionPrompts, queueChatGPTAssets, fetchChatGPTJobs, importChatGPTPackage, importChatGPTImages, mediaUrl, getJSON,
  type ScriptBundle, type CharacterItem, type SceneItem, type PropItem, type Vendor, type AssetRegistryItem, type AssetStateItem, type ChatGPTJobSummary, type SkillItem, type AssetPromptLayers
} from '../api'
import { app, toast } from '../stores/app'
import { trackJob, onJobDone } from '../stores/jobs'
import StyledSelect from '../components/StyledSelect.vue'
import StyleSelect from '../components/StyleSelect.vue'
import Versions from '../components/Versions.vue'
import AssetCard from '../components/AssetCard.vue'
import OverlayViewer from '../components/OverlayViewer.vue'
import ChatGPTRunPanel from '../components/ChatGPTRunPanel.vue'
import EmptyState from '../components/EmptyState.vue'
import { assetMentionContext, extractAssetRefs, filterAssetMentionCandidates, type AssetMentionContext } from '../utils/assetMentions'

type CreateKind = 'character' | 'scene' | 'prop'
type CreateForm = {
  kind: CreateKind
  id: string
  name: string
  prompt: string
  related_refs: string
  role: string
  prop_kind: string
}

const router = useRouter()
const data = ref<ScriptBundle | null>(null)
const assets = ref<AssetRegistryItem[]>([])
const vendors = ref<Vendor[]>([])
const vendorId = ref('')
const episode = ref('')
const busy = ref(false)
const genning = ref('')
const imageRevision = ref(0)
const lightboxImage = ref('')
const lightboxVisible = ref(false)
const createVisible = ref(false)
const createMode = ref<'mother' | 'child'>('mother')
const createParentRef = ref('')
const createSaving = ref(false)
const childGenVisible = ref(false)
const childGenAsset = ref<AssetRegistryItem | null>(null)
const detailAsset = ref<AssetRegistryItem | null>(null)
const detailEditing = ref(false)
const detailSaving = ref(false)
const detailName = ref('')
const detailPrompt = ref('')
/** 资产级画风覆盖：'' = 跟随项目生图风格；imageSkills = 可选的生图 skill 清单。 */
const detailStyle = ref('')
/** 资产级画风自由文本（画风层最高优先级）；空 = 用上方 skill / 项目默认。 */
const detailStylePrompt = ref('')
/** 三层提示词预览（外观/画风/负面/硬约束/最终合成），后端组装口径为准。 */
const promptLayers = ref<AssetPromptLayers | null>(null)
const imageSkills = ref<SkillItem[]>([])
const detailStyleLabels = computed<Record<string, string>>(() => ({
  '': '项目默认（跟随生图风格）',
  ...Object.fromEntries(imageSkills.value.map((s) => [s.id, s.name + ' — ' + s.description.slice(0, 18)]))
}))
const detailPromptArea = ref<HTMLTextAreaElement | null>(null)
const detailMentionOpen = ref(false)
const detailMentionContext = ref<AssetMentionContext | null>(null)
const detailMentionIndex = ref(0)
const detailPromptRefs = ref<string[]>([])
const affectedAssetRef = ref('')
const affectedShots = ref<string[]>([])
const rebuildingAffected = ref(false)
const draggingAsset = ref<AssetRegistryItem | null>(null)
const dragTargetRef = ref('')
const importInput = ref<HTMLInputElement | null>(null)
const chatgptRunPanel = ref<InstanceType<typeof ChatGPTRunPanel> | null>(null)
const pendingChatGPTJobIds = ref<string[]>([])
const importReview = ref<{ file: File; url: string; jobId: string }[]>([])
const importJobs = ref<ChatGPTJobSummary[]>([])
const importStartJobId = ref('')
const importing = ref(false)
const importJobLabels = computed<Record<string, string>>(() => Object.fromEntries(
  importJobs.value.map(job => [job.id, `${job.asset_ref || job.shot_id || job.id} · ${job.filename}`])
))
const importReady = computed(() => importReview.value.length > 0 &&
  importReview.value.every(row => !!row.jobId) &&
  new Set(importReview.value.map(row => row.jobId)).size === importReview.value.length)
const createForm = reactive<CreateForm>({
  kind: 'character', id: '', name: '', prompt: '', related_refs: '', role: '主角', prop_kind: '关联素材'
})

const episodes = computed(() => data.value?.episodes || [])
const epLabels = computed<Record<string, string>>(() =>
  ({ '': '全部', ...Object.fromEntries(episodes.value.map((e) => [e.id, e.id + ' ' + (e.title || '')])) }))
const imageVendors = computed(() => vendors.value.filter(v => v.enabled && !!v.models?.image))
const vendorLabels = computed<Record<string, string>>(() => Object.fromEntries(
  imageVendors.value.map(v => [v.id, (v.label || v.id) + ' · ' + v.models.image])))
const selectedVendor = computed(() => imageVendors.value.find(v => v.id === vendorId.value))
const isChatGPTQueue = computed(() => vendorId.value === 'chatgpt-queue')
const vendorOptions = computed(() => imageVendors.value.map(v => v.id))
const vendorOptionLabels = computed<Record<string, string>>(() => ({
  'chatgpt-queue': 'ChatGPT · chrome-use',
  ...vendorLabels.value
}))
const styleHint = computed(() => {
  const st = ((data.value?.style as any) || {}) as { image?: string }
  const cur = st.image || '自动'
  return `生图风格即全项目统一画风（skill 中英双语指令+负面词），当前：${cur}；未选（自动）时不注入画风指令、模型自由发挥。单个资产可在详情弹窗里覆盖画风（蜘蛛女侠式混搭）`
})
const assetByRef = computed(() => new Map(assets.value.map(a => [a.ref, a])))
const detailMentionAssets = computed(() => assets.value
  .filter(asset => asset.kind !== 'style')
  .map(asset => ({
    ...asset,
    parent_name: asset.parent_ref ? assetByRef.value.get(asset.parent_ref)?.name : ''
  })))
const detailMentionCandidates = computed(() => filterAssetMentionCandidates(
  detailMentionAssets.value,
  detailMentionContext.value?.query || '',
  detailAsset.value?.ref || '',
  12
))
const detailLinkedAssets = computed(() => detailPromptRefs.value
  .map(ref => assetByRef.value.get(ref))
  .filter((asset): asset is AssetRegistryItem => !!asset))
const detailRelationAssets = computed(() => {
  const refs = [...(detailAsset.value?.related_refs || []), ...detailPromptRefs.value]
  const seen = new Set<string>()
  return refs
    .map(ref => assetByRef.value.get(ref))
    .filter((asset): asset is AssetRegistryItem => {
      if (!asset || seen.has(asset.ref)) return false
      seen.add(asset.ref)
      return true
    })
})
const queueableAssets = computed(() => assets.value.filter(a => a.kind !== 'style' && fromEpisode(a)))

function assetRef(kind: string, id: string) { return '@' + kind + ':' + id }
function registryFor(kind: string, id: string) { return assetByRef.value.get(assetRef(kind, id)) }
function fromEpisode(item: any) {
  if (!episode.value) return true
  const ids = item?.source_episode_ids || item?.source_episodes || []
  return Array.isArray(ids) && ids.map(String).includes(String(episode.value))
}
function isCollective(item: { id?: string; name?: string; is_collective?: boolean }) {
  if (item.is_collective) return true
  const name = String(item.name || '')
  const id = String(item.id || '').toLowerCase().replace(/[^a-z0-9]/g, '')
  return /五人组|六人组|小组|团队|集体|师生|全班|班级|同学们|学生们|老师们|教师们|人群|群众|众人|路人|群演|大军|士兵们|守卫们|男学生|女学生/.test(name) ||
    ['wurenxiaozu', 'shisheng', 'quantongban', 'nanxuesheng', 'nvxuesheng', 'xuesheng'].includes(id)
}
const characters = computed(() => (data.value?.characters?.characters || [])
  .filter((x) => fromEpisode(x) && !isCollective(x) && !registryFor('character', x.id)?.parent_ref))
const scenes = computed(() => (data.value?.scenes?.scenes || []).filter((x) =>
  !registryFor('scene', x.id)?.parent_ref &&
  (fromEpisode(x) || childrenOf(assetRef('scene', x.id)).length > 0)))
const props = computed(() => (data.value?.props?.props || [])
  .filter((x) => fromEpisode(x))
  .filter((x) => !registryFor('prop', x.id)?.parent_ref))
const mothers = computed(() => assets.value.filter(a =>
  a.kind !== 'style' && !a.parent_ref && !a.derived_from && fromEpisode(a)))
const childCount = computed(() => assets.value.filter(a => a.kind !== 'style' && a.parent_ref).length)
function childrenOf(ref: string) {
  return assets.value.filter(a => a.kind !== 'style' && a.parent_ref === ref && fromEpisode(a))
}
function relationLabel(row: AssetRegistryItem) {
  if (row.relation === 'component_of') return '组成部件'
  if (row.relation === 'located_in') return '场景内'
  if (row.relation === 'variant_of') return '变体'
  if (row.relation === 'derived_from') return '派生状态'
  return row.relation || '子素材'
}
function imagePath(row?: AssetRegistryItem) {
  if (!row?.path || !app.current) return ''
  return 'projects/' + app.current + '/' + row.path
}
function imageUrl(row?: AssetRegistryItem) {
  const path = imagePath(row)
  return path ? mediaUrl(path) + '&v=' + imageRevision.value : ''
}
function showAsset(row?: AssetRegistryItem) {
  const url = imageUrl(row)
  if (!url) return
  lightboxImage.value = url
  lightboxVisible.value = true
}
function statesOf(kind: string, id: string): AssetStateItem[] {
  return registryFor(kind, id)?.states || []
}
function stateImageUrl(st: AssetStateItem) {
  return st.path && app.current ? mediaUrl('projects/' + app.current + '/' + st.path) + '&v=' + imageRevision.value : ''
}
function showStateImage(st: AssetStateItem) {
  const url = stateImageUrl(st)
  if (!url) return
  lightboxImage.value = url
  lightboxVisible.value = true
}
function openAssetDetails(row?: AssetRegistryItem) {
  if (!row) return
  detailAsset.value = row
  detailEditing.value = false
  detailName.value = row.name || ''
  detailPrompt.value = row.prompt || ''
  detailStyle.value = row.style || ''
  detailStylePrompt.value = row.style_prompt || ''
  detailPromptRefs.value = extractAssetRefs(detailPrompt.value)
    .filter(ref => assetByRef.value.has(ref))
  detailMentionContext.value = null
  detailMentionOpen.value = false
  detailMentionIndex.value = 0
  refreshPromptLayers(row)
}
async function refreshPromptLayers(row?: AssetRegistryItem | null) {
  const target = row || detailAsset.value
  if (!target || !app.current || target.kind === 'style') { promptLayers.value = null; return }
  try {
    const res = await fetchAssetPromptLayers(app.current, target.kind, target.id)
    promptLayers.value = res.ok ? res.layers : null
  } catch { promptLayers.value = null }
}
function closeAssetDetails() { if (!detailSaving.value) { detailAsset.value = null; detailEditing.value = false } }
function updateDetailPromptRefs() {
  detailPromptRefs.value = extractAssetRefs(detailPrompt.value)
    .filter(ref => assetByRef.value.has(ref))
}
function updateDetailMentionContext() {
  const area = detailPromptArea.value
  const rawContext = assetMentionContext(detailPrompt.value, area?.selectionStart ?? detailPrompt.value.length)
  let context = rawContext
  // 中文短句通常没有空格分词；从末尾向前尝试素材名称，允许“背景是二年A班教室”
  // 直接命中“二年A班教室”，而不要求用户先手动输入 @。
  if (context?.mode === 'name') {
    for (let offset = 0; offset < context.query.length; offset += 1) {
      const suffix = context.query.slice(offset)
      if (suffix.length < 2) continue
      if (!filterAssetMentionCandidates(detailMentionAssets.value, suffix, detailAsset.value?.ref || '', 1).length) continue
      context = { ...context, query: suffix, start: context.start + offset }
      break
    }
  }
  detailMentionContext.value = context
  detailMentionIndex.value = 0
  if (!context) {
    detailMentionOpen.value = false
    return
  }
  const hasCandidates = detailMentionCandidates.value.length > 0
  // @ 后立即显示全局资产；普通名称只在确实命中素材时显示，避免
  // 日常中文输入被下拉框打断。
  detailMentionOpen.value = hasCandidates
}
function onDetailPromptInput() {
  updateDetailPromptRefs()
  updateDetailMentionContext()
}
function onDetailPromptKeydown(event: KeyboardEvent) {
  if (!detailMentionOpen.value || !detailMentionCandidates.value.length) {
    if (event.key === 'Escape') detailMentionOpen.value = false
    return
  }
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    detailMentionIndex.value = (detailMentionIndex.value + 1) % detailMentionCandidates.value.length
  } else if (event.key === 'ArrowUp') {
    event.preventDefault()
    detailMentionIndex.value = (detailMentionIndex.value - 1 + detailMentionCandidates.value.length) % detailMentionCandidates.value.length
  } else if (event.key === 'Enter' || event.key === 'Tab') {
    event.preventDefault()
    insertDetailAssetRef(detailMentionCandidates.value[detailMentionIndex.value])
  } else if (event.key === 'Escape') {
    event.preventDefault()
    detailMentionOpen.value = false
  }
}
async function insertDetailAssetRef(asset?: AssetRegistryItem) {
  const context = detailMentionContext.value
  if (!asset || !context) return
  const value = detailPrompt.value
  const token = asset.ref + ' '
  detailPrompt.value = value.slice(0, context.start) + token + value.slice(context.end)
  updateDetailPromptRefs()
  detailMentionOpen.value = false
  detailMentionContext.value = null
  await nextTick()
  const area = detailPromptArea.value
  if (area) {
    area.focus()
    const cursor = context.start + token.length
    area.setSelectionRange(cursor, cursor)
  }
}
function removeDetailAssetRef(ref: string) {
  const escaped = ref.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  detailPrompt.value = detailPrompt.value.replace(new RegExp(`${escaped}\\s*`, 'g'), '')
  updateDetailPromptRefs()
  detailMentionOpen.value = false
}
function assetKindLabel(kind?: string) {
  return kind === 'character' ? '人物' : kind === 'scene' ? '场景' : kind === 'prop' ? '道具' : kind || '素材'
}
async function saveAssetEdit() {
  const row = detailAsset.value
  if (!row || !app.current || !detailName.value.trim()) return
  detailSaving.value = true
  try {
    updateDetailPromptRefs()
    const result = await editAsset({ project: app.current, ref: row.ref, patch: {
      name: detailName.value.trim(), prompt: detailPrompt.value, related_refs: detailPromptRefs.value,
      style: detailStyle.value, style_prompt: detailStylePrompt.value.trim()
    }, expected_revision: row.asset_revision })
    if (!result.ok) throw new Error((result as any).err || '保存设定失败')
    const count = result.affected?.shots?.length || 0
    affectedAssetRef.value = row.ref
    affectedShots.value = result.affected?.shots || []
    await load()
    detailAsset.value = assets.value.find((item) => item.ref === row.ref) || null
    detailEditing.value = false
    refreshPromptLayers()
    toast(count ? `资产已更新，${count} 个镜头标记为待重建` : '资产设定已更新', 'ok', 5000)
  } catch (e) { toast(e instanceof Error ? e.message : '保存设定失败', 'err', 6000) }
  finally { detailSaving.value = false }
}
async function rebuildAffectedPromptShots() {
  if (!app.current || !affectedShots.value.length || rebuildingAffected.value) return
  rebuildingAffected.value = true
  try {
    const result = await rebuildProductionPrompts({ project: app.current, shot_ids: affectedShots.value })
    toast(`已重建 ${result.updated_prompts || 0} 个受影响镜头提示词，未调用媒体模型`, 'ok', 5000)
    affectedShots.value = []
  } catch (e) { toast(e instanceof Error ? e.message : '受影响镜头重建失败', 'err', 6000) }
  finally { rebuildingAffected.value = false }
}
function assetFilePath(row?: AssetRegistryItem | null) {
  return row?.path && app.current ? `projects/${app.current}/${row.path}` : ''
}
function relationForMove(row: AssetRegistryItem, parentRef: string) {
  if (!parentRef) return null
  const parent = assetByRef.value.get(parentRef)
  if (!parent) return null
  if (parent.kind === 'scene') return 'located_in'
  return row.kind === 'prop' ? 'component_of' : 'contains'
}
async function moveAssetTo(row: AssetRegistryItem, target: string) {
  if (!app.current) return
  if (target === row.ref) { toast('不能把素材挂到自身', 'err'); return }
  const targetAsset = target ? assetByRef.value.get(target) : undefined
  if (targetAsset?.parent_ref) {
    toast('资产关系最多两层，不能挂到子素材下', 'err')
    return
  }
  const result = await saveAssetRelations({
    project: app.current,
    updates: [{ ref: row.ref, parent_ref: target || null, relation: relationForMove(row, target) }]
  })
  assets.value = result.assets || assets.value
  imageRevision.value = Date.now()
  toast(target ? `已移动到「${assetByRef.value.get(target)?.name || target}」下方` : '已设为母素材', 'ok')
}
function startAssetDrag(row?: AssetRegistryItem, event?: DragEvent) {
  // 母素材卡片与子素材卡片是嵌套的 draggable 元素；必须阻断事件冒泡，
  // 否则拖拽子素材时外层母卡片会再次收到 dragstart，拖拽源就会被覆盖。
  event?.stopPropagation()
  if (!row || row.kind === 'style') return
  draggingAsset.value = row
  dragTargetRef.value = ''
  if (event?.dataTransfer) {
    event.dataTransfer.effectAllowed = 'move'
    event.dataTransfer.setData('text/plain', row.ref)
  }
}
function dragOverAsset(row?: AssetRegistryItem, event?: DragEvent) {
  event?.stopPropagation()
  const moving = draggingAsset.value
  // 只允许投放到母素材。子素材仍可作为拖拽源，但不能继续形成第三级。
  if (!moving || !row || row.ref === moving.ref || row.parent_ref) {
    dragTargetRef.value = ''
    return
  }
  dragTargetRef.value = row.ref
}
async function dropAsset(row?: AssetRegistryItem, event?: DragEvent) {
  event?.stopPropagation()
  const moving = draggingAsset.value
  const target = row?.ref || ''
  draggingAsset.value = null
  dragTargetRef.value = ''
  if (!moving || target === moving.ref) return
  if (row?.parent_ref) {
    toast('资产关系最多两层，不能挂到子素材下', 'err')
    return
  }
  try { await moveAssetTo(moving, target) }
  catch (e) { toast(e instanceof Error ? e.message : '拖拽移动失败，未保存任何变更', 'err', 6000) }
}
function endAssetDrag(event?: DragEvent) {
  event?.stopPropagation()
  draggingAsset.value = null
  dragTargetRef.value = ''
}
function resetCreate() {
  createForm.id = ''
  createForm.name = ''
  createForm.prompt = ''
  createForm.related_refs = ''
  createForm.role = createMode.value === 'child' ? '关联角色' : '主角'
  createForm.prop_kind = createMode.value === 'child' ? '关联素材' : '叙事'
}
function openCreate(mode: 'mother' | 'child', parentRef = '') {
  createMode.value = mode
  createParentRef.value = parentRef
  const parent = assetByRef.value.get(parentRef)
  createForm.kind = mode === 'child' ? (parent?.kind === 'scene' ? 'scene' : 'prop') : 'character'
  resetCreate()
  createVisible.value = true
}
function closeCreate() {
  if (!createSaving.value) createVisible.value = false
}
function parentName() {
  return createParentRef.value ? (assetByRef.value.get(createParentRef.value)?.name || createParentRef.value) : ''
}
async function submitCreate() {
  if (!app.current || !createForm.name.trim()) {
    toast('请填写素材名称', 'err')
    return
  }
  createSaving.value = true
  try {
    const result = await createAsset({
      project: app.current, kind: createForm.kind, id: createForm.id.trim() || undefined,
      name: createForm.name.trim(), prompt: createForm.prompt.trim(),
      role: createForm.kind === 'character' ? createForm.role : undefined,
      prop_kind: createForm.kind === 'prop' ? createForm.prop_kind : undefined,
      parent_ref: createParentRef.value || null,
      relation: createParentRef.value ? (createParentRef.value.startsWith('@scene:') ? 'located_in' : 'component_of') : null,
      related_refs: createForm.related_refs.split(/[，,\s]+/).map((value) => value.trim()).filter(Boolean),
      episode: episode.value || undefined
    })
    if (!result.ok) throw new Error((result as any).err || '新增素材失败')
    await load()
    createVisible.value = false
    toast(createMode.value === 'mother' ? '母素材已新增' : '子素材已新增，可继续生成图片', 'ok')
  } catch (e) {
    toast(e instanceof Error ? e.message : '新增素材失败', 'err', 6000)
  } finally {
    createSaving.value = false
  }
}
let loadSeq = 0
async function load() {
  const project = app.current
  if (!project) { data.value = null; assets.value = []; return }
  const seq = ++loadSeq
  try {
    const [script, registry] = await Promise.all([fetchScriptData(project), fetchAssets(project)])
    if (seq !== loadSeq || project !== app.current) return   // 项目切换/连续刷新竞态：丢弃过期响应
    data.value = script
    assets.value = registry.assets || []
    imageRevision.value = Date.now()
    await refreshChatGPTRunJobs()
  } catch {
    if (seq !== loadSeq || project !== app.current) return
    data.value = null
    assets.value = []
  }
}
async function refreshChatGPTRunJobs() {
  if (!app.current) { pendingChatGPTJobIds.value = []; return }
  try {
    const allowed = new Set(queueableAssets.value.map(asset => asset.ref))
    const jobs = (await fetchChatGPTJobs(app.current, { status: 'queued', limit: 200 })).jobs
    pendingChatGPTJobIds.value = jobs
      .filter(job => job.task_type === 'asset_image' && (!allowed.size || !job.asset_ref || allowed.has(job.asset_ref)))
      .map(job => job.id)
  } catch {
    pendingChatGPTJobIds.value = []
  }
}
async function loadVendors() {
  try {
    vendors.value = (await fetchEnvConfig()).vendors || []
    if (!vendorOptions.value.includes(vendorId.value))
      vendorId.value = imageVendors.value[0]?.id || 'chatgpt-queue'
  } catch {
    vendors.value = []
    vendorId.value = ''
  }
}
watch(() => app.current, () => {
  // 资产页进入项目时从全局视图开始；用户切换分集后仍保留当前筛选。
  episode.value = ''
  void load()
}, { immediate: true })
// 生图/提炼任务完成即刷新资产数据（含刷新页面后恢复跟踪的任务），不依赖手动刷新。
const offJobDone = onJobDone((j) => {
  if (j.label.startsWith('生图') || j.label === '资产提炼') void load()
})
onMounted(() => {
  void loadVendors()
  // 资产级画风覆盖的候选清单：image target 的启用 skill
  getJSON<{ skills: SkillItem[] }>('/api/skills')
    .then((d) => { imageSkills.value = (d.skills || []).filter((s) => s.target === 'image' && s.enabled) })
    .catch(() => { imageSkills.value = [] })
})

async function doExtract() {
  if (!app.current) return
  busy.value = true
  try {
    const r = await scriptExtract(app.current, episode.value || undefined)
    if (!r.id) throw new Error(r.err || '任务未启动')
    const j = await trackJob(r.id, '资产提炼')
    if (!j.success) throw new Error(j.err || '提炼失败')
    toast('资产提炼完成', 'ok')
  } catch (e) {
    toast(e instanceof Error ? e.message : '提炼失败', 'err', 6000)
  } finally {
    // 提炼任务失败也可能已写回部分三件套，统一刷新展示。
    busy.value = false
    await load()
  }
}
async function doGen(kind: string, id?: string, force = false, states?: 'include' | 'only' | 'skip', stateId?: string) {
  if (isChatGPTQueue.value) {
    const ref = `@${kind}:${id || ''}`
    await queueChatGPTAssetRefs([stateId ? { ref, state_id: stateId } : ref])
    return
  }
  if (!app.current || !selectedVendor.value) return
  genning.value = kind + (id || '') + (stateId ? '#' + stateId : '')
  try {
    const r = await genAssetImage({ project: app.current, kind, id, vendor_id: selectedVendor.value.id, force, states, state_id: stateId })
    if (!r.id) throw new Error(r.err || '任务未启动')
    const j = await trackJob(r.id, '生图 ' + kind + (id ? ' ' + id : ''))
    if (!j.success) throw new Error(j.err || '生图失败')
    toast('图片已生成；子素材会继承母素材参考图', 'ok')
  } catch (e) {
    toast(e instanceof Error ? e.message : '生图失败', 'err', 6000)
  } finally {
    // 成功/部分失败都刷新注册表与缩略图：部分失败时已产出的图也要立即可见，
    // load 内部会 bump imageRevision 使图片 URL 变化、浏览器重新拉取（不刷新页面）。
    genning.value = ''
    await load()
  }
}

async function queueChatGPTAssetRefs(refs: Array<string | { ref: string; state_id?: string }>) {
  if (!app.current || !refs.length) return
  genning.value = 'chatgpt'
  try {
    const valid = refs.filter((value) => typeof value === 'string' ? !value.endsWith(':') : Boolean(value.ref))
    const r = await queueChatGPTAssets({ project: app.current, asset_refs: valid })
    const ids = (r.jobs || []).map(job => job.id).slice(0, 20)
    pendingChatGPTJobIds.value = ids
    const started = ids.length ? await chatgptRunPanel.value?.start(ids) : false
    toast(started
      ? `已加入并启动 ChatGPT 资产队列（${ids.length} 项）`
      : `已加入 ChatGPT 资产队列（${r.jobs?.length || 0} 项），请在执行面板启动`, 'ok')
    await load()
  } catch (e) {
    toast(e instanceof Error ? e.message : '加入 ChatGPT 资产队列失败', 'err', 6000)
  } finally {
    genning.value = ''
  }
}

async function queueAllChatGPTAssets() {
  await queueChatGPTAssetRefs(queueableAssets.value.map(a => a.ref))
}

async function onChatGPTRunCompleted() {
  await load()
  toast('ChatGPT 本批图片已全部校验并导入', 'ok')
}

async function onChatGPTImport(event: Event) {
  const selected = Array.from((event.target as HTMLInputElement).files || [])
  ;(event.target as HTMLInputElement).value = ''
  if (!selected.length || !app.current) return
  try {
    if (selected.length === 1 && selected[0].name.toLowerCase().endsWith('.zip')) {
      const result = await importChatGPTPackage(app.current, selected[0])
      toast(`已导入 ${result.imported} 项 ChatGPT 资产`, 'ok')
      await load()
      return
    }
    if (selected.some(file => !/\.(png|jpe?g|webp)$/i.test(file.name))) throw new Error('请只选独立图片，或单独选择一个 ZIP 包')
    const jobs = (await fetchChatGPTJobs(app.current, { status: 'queued', limit: 100 })).jobs
      .filter(job => job.task_type === 'asset_image')
    if (selected.length > jobs.length) throw new Error(`只剩 ${jobs.length} 个待导入资产任务，当前选了 ${selected.length} 张图片`)
    closeImportReview()
    importJobs.value = jobs
    importStartJobId.value = jobs[0]?.id || ''
    const sorted = selected.sort((a, b) => a.lastModified - b.lastModified || a.name.localeCompare(b.name, undefined, { numeric: true }))
    const used = new Set<string>()
    const exact = sorted.map(file => {
      const hit = jobs.find(job => job.filename.toLowerCase() === file.name.toLowerCase() && !used.has(job.id))
      if (hit) used.add(hit.id)
      return hit?.id || ''
    })
    const remaining = jobs.filter(job => !used.has(job.id))
    importReview.value = sorted.map((file, i) => ({
      file, url: URL.createObjectURL(file), jobId: exact[i] || remaining.shift()?.id || ''
    }))
  } catch (e) {
    toast(e instanceof Error ? e.message : '导入 ChatGPT 生成包失败', 'err', 6000)
  }
}
function closeImportReview() {
  importReview.value.forEach(row => URL.revokeObjectURL(row.url))
  importReview.value = []
  importJobs.value = []
  importStartJobId.value = ''
}
function remapImportFromStart() {
  const start = importJobs.value.findIndex(job => job.id === importStartJobId.value)
  if (start < 0) return
  importReview.value.forEach((row, index) => {
    row.jobId = importJobs.value[start + index]?.id || ''
  })
}
const importAbort = ref<AbortController | null>(null)
async function confirmChatGPTImport() {
  if (!app.current || !importReady.value || importing.value) return
  importing.value = true
  importAbort.value = new AbortController()
  try {
    const rows = importReview.value.map(row => ({
      file: row.file, job: importJobs.value.find(job => job.id === row.jobId)!
    }))
    const result = await importChatGPTImages(app.current, rows, importAbort.value.signal)
    closeImportReview()
    toast(`已自动匹配并导入 ${result.imported} 张资产原图`, 'ok')
    await load()
  } catch (e) {
    if (!(e instanceof Error && e.message === '已取消'))
      toast(e instanceof Error ? e.message : '批量导入失败', 'err', 6000)
  } finally {
    importing.value = false
    importAbort.value = null
  }
}
/** 导入中也可强制关闭：中断请求（服务端导入按任务幂等，重进不重复）。 */
function forceCloseImport() {
  importAbort.value?.abort()
  importing.value = false
  closeImportReview()
}
// Esc 关闭导入核对模态（导入中=中断并关闭）
function onImportEsc(e: KeyboardEvent) {
  if (e.key === 'Escape' && importReview.value.length)
    importing.value ? forceCloseImport() : closeImportReview()
}
watch(() => importReview.value.length, n => {
  if (n > 0) window.addEventListener('keydown', onImportEsc)
  else window.removeEventListener('keydown', onImportEsc)
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onImportEsc)
  offJobDone()
})
function openChildGen(row: AssetRegistryItem) {
  childGenAsset.value = row
  childGenVisible.value = true
}
function closeChildGen() {
  if (!genning.value) childGenVisible.value = false
}
async function submitChildGen() {
  const row = childGenAsset.value
  if (!row) return
  childGenVisible.value = false
  // 子素材生成只产本图：states 派生状态走母素材卡上的独立入口
  await doGen(row.kind, row.id, true, 'skip')
}
function parentAsset(row?: AssetRegistryItem | null) {
  return row?.parent_ref ? assetByRef.value.get(row.parent_ref) : undefined
}
</script>

<template>
  <div class="page">
    <header class="mb-6">
      <div class="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 class="grad-text text-2xl font-black">③ 素材生成</h1><RouterLink to="/studio/asset/voices" class="mt-2 inline-block text-sm text-sky-300">音色绑定 · 云端音色库 · AI 音色创作 →</RouterLink>
          <p class="mt-1 text-xs text-slate-500">母素材统一身份，子素材保存服饰、配饰和身体组件的差异；子图生成自动继承母图参考。拖动资产卡到另一张卡下方即可移动，拖到资产区空白处可提升为母素材。</p>
        </div>
        <button class="btn btn-ghost" title="新增独立角色、场景或道具母素材" @click="openCreate('mother')">＋新增母素材</button>
      </div>
    </header>
    <EmptyState v-if="!app.current" title="请先在左侧选择项目" />
    <template v-else>
      <div class="mb-3 rounded-lg bg-sky-400/10 px-3 py-2 text-xs-plus text-sky-200">
        推荐顺序：① 剧本生成 → ③ 本页提炼（人物/场景/道具，收尾自动出场景平面图初稿）→ ② 分镜生成（自动关联 scene_ref，无需手动绑定）→ ⑤ 演员表现 → ⑥ 平面推演 → ⑦ 创作生成。
      </div>
      <div class="glass mb-5 flex flex-wrap items-end gap-3 p-4">
        <div class="flex items-end gap-2">
          <label class="text-xs text-slate-400">提炼/查看分集
            <StyledSelect v-model="episode" class="mt-1 w-48" :options="['', ...episodes.map(e => e.id)]" :labels="epLabels" placeholder="全部" />
          </label>
        </div>
        <button class="btn" :disabled="busy" @click="doExtract" title="按当前分集提炼；已有同 ID 资产自动复用">{{ busy ? '提炼中…' : '提炼三件套' }}</button>
        <StyleSelect target="image" label="生图风格" :hint="styleHint" @changed="load" />
        <label class="text-xs text-slate-400">设定图模型
          <StyledSelect v-model="vendorId" class="mt-1 w-60" :options="vendorOptions" :labels="vendorOptionLabels" :storage-key="`wb.${app.current}.assets.vendor`" placeholder="选择生图模型" />
        </label>
        <button class="btn btn-ghost" @click="router.push('/acting')" title="编辑角色卡、连续性记忆和表演候选">编辑演员卡/记忆</button>
        <!-- 自然宽度按钮组：文字不截断；容器 flex-wrap + items-end，换行对齐一致 -->
        <button v-if="!isChatGPTQueue" class="btn btn-ghost" :disabled="busy || !!genning || !selectedVendor" @click="doGen('all')" :title="selectedVendor ? '补缺模式：只生成尚未存在的素材图，已有图片会跳过，不创建新版本' : '先选择生图模型'">
          {{ genning === 'all' ? '补缺生图中…' : '补缺生成全部素材图' }}
        </button>
        <button v-else class="btn border-cyan-400/30 text-cyan-200" :disabled="busy || !!genning || !queueableAssets.length" :title="'加入队列后由 image-use 逐项生成并导入，最多选择20项'" @click="queueAllChatGPTAssets">{{ genning === 'chatgpt' ? '启动执行中…' : `加入并执行（${Math.min(queueableAssets.length, 20)} 项）` }}</button>
        <button v-if="!isChatGPTQueue" class="btn btn-ghost text-amber-200" :disabled="busy || !!genning || !selectedVendor" @click="doGen('all', undefined, true)" :title="selectedVendor ? '强制重生成全项目素材图，会为被覆盖的图片创建版本快照' : '先选择生图模型'">
          {{ genning === 'all' ? '全部生图中…' : '全部重生成' }}
        </button>
        <template v-else>
          <input ref="importInput" type="file" multiple accept=".zip,image/png,image/jpeg,image/webp" class="hidden" @change="onChatGPTImport" />
          <button class="btn btn-ghost text-violet-200" title="批量导入 ChatGPT 原图 / ZIP" @click="importInput?.click()">导入原图/ZIP</button>
        </template>
        <span v-if="isChatGPTQueue" class="mb-1 inline-flex h-4 w-4 cursor-help items-center justify-center rounded-full border border-cyan-400/50 text-2xs text-cyan-300" title="可一次选择多张独立原图，系统按文件名或队列顺序预匹配">?</span>
        <span class="ml-auto text-xs-plus text-slate-500">{{ episode ? `当前 ${episode}` : '全局全部素材' }} · 母素材 {{ mothers.length }} · 子素材 {{ childCount }}</span>
        <span v-if="!imageVendors.length && !isChatGPTQueue" class="text-xs-plus text-amber-300">环境页暂无启用的生图模型</span>
      </div>

      <ChatGPTRunPanel
        v-if="isChatGPTQueue"
        ref="chatgptRunPanel"
        class="mb-5"
        :project="app.current"
        :job-ids="pendingChatGPTJobIds"
        :disabled="busy || !!genning"
        @completed="onChatGPTRunCompleted"
      />

      <div class="grid gap-4 lg:grid-cols-3">
        <section class="glass p-4" @dragover.prevent="dragOverAsset()" @drop.prevent="dropAsset()">
          <h3 class="mb-3 text-sm font-bold text-slate-200">人物母素材（{{ characters.length }}）</h3>
          <div v-if="!characters.length" class="text-xs-plus text-slate-500">尚未提炼</div>
          <AssetCard v-for="c in characters as CharacterItem[]" :key="c.id" kind="character" :id="c.id" :name="c.name"
            :asset="registryFor('character', c.id)" :project="app.current" :image-url-of="imageUrl" :relation-label="relationLabel"
            :children="childrenOf(assetRef('character', c.id))" :genning="genning"
            :gen-disabled="!!genning || (!selectedVendor && !isChatGPTQueue)" :drag-target="dragTargetRef"
            @details="openAssetDetails" @show="showAsset" @gen="doGen('character', c.id, true, 'skip')"
            @create-child="openCreate('child', assetRef('character', c.id))" @child-gen="openChildGen"
            @drag-start="startAssetDrag" @drag-over="dragOverAsset" @drop="dropAsset" @drag-end="endAssetDrag" @restored="load">
            <template #badge><span class="ml-2 rounded bg-amber-400/15 px-1.5 text-2xs text-amber-300">{{ c.role || '角色' }}</span></template>
            <template #meta>{{ c.id }} · {{ c.basis || '项目母素材' }}</template>
            <template #states>
              <div v-if="statesOf('character', c.id).length" class="mt-4 border-t border-line pt-3">
                <div class="mb-2 flex items-center justify-between"><span class="text-xs-plus font-semibold text-slate-300">派生状态（{{ statesOf('character', c.id).length }}）</span><span class="text-2xs text-slate-500">生成时参考本角色母图</span></div>
                <div class="grid gap-2 sm:grid-cols-2">
                  <div v-for="st in statesOf('character', c.id)" :key="st.id" class="rounded-lg bg-black/15 p-2">
                    <button v-if="stateImageUrl(st)" class="block h-24 w-full overflow-hidden rounded-lg border border-line bg-black/20" @click="showStateImage(st)"><img :src="stateImageUrl(st)" class="h-full w-full object-contain" :alt="st.label" /></button>
                    <div v-else class="flex h-24 items-center justify-center rounded-lg border border-dashed border-line text-2xs text-slate-500">状态图未生成</div>
                    <div class="mt-1 truncate text-xs text-slate-200" :title="st.look_diff">{{ st.label }}</div>
                    <div class="text-2xs text-slate-500">派生状态{{ st.camp && st.camp !== '不明' ? ' · ' + st.camp : '' }}</div>
                    <div class="mt-1 flex gap-1"><button class="btn btn-ghost btn-sm flex-1" :disabled="!!genning || (!selectedVendor && !isChatGPTQueue)" :title="'以本角色母图为参考' + (st.path ? '重新生成该状态图（旧版本自动保存）' : '生成该状态图') + '；母图不受影响'" @click="doGen('character', c.id, true, 'only', st.id)">{{ genning === 'character' + c.id + '#' + st.id ? '生成中…' : '生成派生图' }}</button><Versions :path="'projects/' + app.current + '/素材/人物/' + c.id + '__' + st.id + '.png'" kind="image" @restored="load" /></div>
                  </div>
                </div>
              </div>
            </template>
          </AssetCard>
        </section>

        <section class="glass p-4" @dragover.prevent="dragOverAsset()" @drop.prevent="dropAsset()">
          <h3 class="mb-3 text-sm font-bold text-slate-200">场景母素材（{{ scenes.length }}）</h3>
          <div v-if="!scenes.length" class="text-xs-plus text-slate-500">尚未提炼</div>
          <AssetCard v-for="s in scenes as SceneItem[]" :key="s.id" kind="scene" :id="s.id" :name="s.name"
            :asset="registryFor('scene', s.id)" :project="app.current" :image-url-of="imageUrl" :relation-label="relationLabel"
            :children="childrenOf(assetRef('scene', s.id))" :genning="genning"
            :gen-disabled="!!genning || (!selectedVendor && !isChatGPTQueue)" :drag-target="dragTargetRef"
            @details="openAssetDetails" @show="showAsset" @gen="doGen('scene', s.id, true)"
            @create-child="openCreate('child', assetRef('scene', s.id))" @child-gen="openChildGen"
            @drag-start="startAssetDrag" @drag-over="dragOverAsset" @drop="dropAsset" @drag-end="endAssetDrag" @restored="load">
            <template #meta>{{ s.id }} · {{ s.time }} · {{ s.light }}</template>
          </AssetCard>
        </section>

        <section class="glass p-4" @dragover.prevent="dragOverAsset()" @drop.prevent="dropAsset()">
          <h3 class="mb-3 text-sm font-bold text-slate-200">独立道具母素材（{{ props.length }}）</h3>
          <div v-if="!props.length" class="text-xs-plus text-slate-500">尚未提炼</div>
          <AssetCard v-for="p in props as PropItem[]" :key="p.id" kind="prop" :id="p.id" :name="p.name"
            :asset="registryFor('prop', p.id)" :project="app.current" :image-url-of="imageUrl" :relation-label="relationLabel"
            :children="childrenOf(assetRef('prop', p.id))" :genning="genning"
            :gen-disabled="!!genning || (!selectedVendor && !isChatGPTQueue)" :drag-target="dragTargetRef"
            @details="openAssetDetails" @show="showAsset" @gen="doGen('prop', p.id, true)"
            @create-child="openCreate('child', assetRef('prop', p.id))" @child-gen="openChildGen"
            @drag-start="startAssetDrag" @drag-over="dragOverAsset" @drop="dropAsset" @drag-end="endAssetDrag" @restored="load">
            <template #meta>{{ p.id }} · {{ p.kind || '叙事' }}</template>
          </AssetCard>
        </section>
      </div>
    </template>

    <div v-if="importReview.length" class="overlay p-4" @click.self="importing ? forceCloseImport() : closeImportReview()">
      <div class="glass modal-h-lg flex w-full max-w-4xl flex-col p-5">
        <div class="mb-3 flex items-center justify-between gap-3"><h2 class="text-lg font-bold text-slate-100">核对 ChatGPT 原图与资产任务</h2><button class="btn btn-ghost btn-sm" @click="importing ? forceCloseImport() : closeImportReview()">{{ importing ? '中断并关闭' : '关闭' }}</button></div>
        <p class="mb-3 text-xs text-slate-400">已按目标文件名优先匹配，其余按下载时间与待导入队列顺序配对。请核对缩略图；任务对应错误时可在该行改选。图片本身不会添加角标。</p>
        <div class="mb-3 flex items-center gap-2"><span class="shrink-0 text-xs text-slate-400">本批从</span><StyledSelect v-model="importStartJobId" :options="importJobs.map(job => job.id)" :labels="importJobLabels" placeholder="选择第一张对应的任务" /><button class="btn btn-ghost btn-sm shrink-0" @click="remapImportFromStart">顺序配对</button></div>
        <div class="min-h-0 flex-1 space-y-2 overflow-y-auto pr-1">
          <div v-for="(row, index) in importReview" :key="row.url" class="grid items-center gap-3 rounded-lg border border-line bg-white/5 p-2 sm:grid-cols-[4rem_1fr_1.5fr]">
            <img :src="row.url" :alt="`待导入图片 ${index + 1}`" class="h-16 w-16 rounded bg-black object-contain" />
            <div class="min-w-0"><div class="text-xs text-slate-300">第 {{ index + 1 }} 张</div><div class="truncate text-xs-plus text-slate-500" :title="row.file.name">{{ row.file.name }}</div></div>
            <StyledSelect v-model="row.jobId" :options="importJobs.map(job => job.id)" :labels="importJobLabels" placeholder="选择目标资产任务" />
          </div>
        </div>
        <div class="mt-4 flex items-center justify-between gap-3"><span class="text-xs" :class="importReady ? 'text-emerald-300' : 'text-amber-300'">{{ importReady ? `已匹配 ${importReview.length} 张独立原图` : '请为每张图片选择不同的任务' }}</span><button class="btn" :disabled="!importReady || importing" @click="confirmChatGPTImport">{{ importing ? '导入中…' : '确认批量导入' }}</button></div>
      </div>
    </div>

    <div v-if="createVisible" class="overlay p-4" @click.self="closeCreate">
      <form class="glass w-full max-w-xl p-5" @submit.prevent="submitCreate">
        <div class="mb-4 flex items-center justify-between"><h2 class="text-lg font-bold text-slate-100">{{ createMode === 'mother' ? '新增母素材' : '新增子素材' }}</h2><button type="button" class="btn btn-ghost btn-sm" @click="closeCreate">关闭</button></div>
        <p v-if="createMode === 'child'" class="mb-3 rounded bg-violet-400/10 px-3 py-2 text-xs text-violet-200">母素材：{{ parentName() }}。子图生成时会自动继承母图作为参考。</p>
        <div class="grid gap-3 sm:grid-cols-2">
          <label class="text-xs text-slate-400">素材类型
            <StyledSelect v-model="createForm.kind" class="mt-1" :options="['character', 'scene', 'prop']" :labels="{ character: '人物', scene: '场景', prop: '道具/关联素材' }" />
          </label>
          <label class="text-xs text-slate-400">稳定 ID（可留空自动生成）
            <input v-model="createForm.id" class="input mt-1 w-full" placeholder="例如 baixiaozhuxu_collar" />
          </label>
        </div>
        <label class="mt-3 block text-xs text-slate-400">素材名称
          <input v-model="createForm.name" required class="input mt-1 w-full" placeholder="例如：拟态项圈 / 银白蜘蛛步足 / 夜行服饰" />
        </label>
        <label v-if="createForm.kind === 'character'" class="mt-3 block text-xs text-slate-400">角色类型
          <StyledSelect v-model="createForm.role" class="mt-1" :options="['主角', '配角', '关联角色']" />
        </label>
        <label v-if="createForm.kind === 'prop'" class="mt-3 block text-xs text-slate-400">素材子类型
          <StyledSelect v-model="createForm.prop_kind" class="mt-1" :options="['关联素材', '服饰', '配饰', '组件', '叙事']" />
        </label>
        <label class="mt-3 block text-xs text-slate-400">生图提示词
          <textarea v-model="createForm.prompt" class="textarea mt-1 min-h-28 w-full" :placeholder="createMode === 'child' ? '只写子素材差异；生图会继承母素材图' : '描述母素材的稳定外观与用途'" />
        </label>
        <label class="mt-3 block text-xs text-slate-400">关联素材引用（可选）
          <input v-model="createForm.related_refs" class="input mt-1 w-full" placeholder="例如 @prop:yinbai_jiezhi_buzu @prop:heitai_xiangquan" />
        </label>
        <div class="mt-5 flex justify-end gap-2"><button type="button" class="btn btn-ghost" @click="closeCreate">取消</button><button class="btn" :disabled="createSaving">{{ createSaving ? '保存中…' : '保存素材' }}</button></div>
      </form>
    </div>
    <div v-if="childGenVisible && childGenAsset" class="overlay p-4" @click.self="closeChildGen">
      <div class="glass w-full max-w-2xl p-5">
        <div class="mb-4 flex items-center justify-between">
          <h2 class="text-lg font-bold text-slate-100">生成子素材图</h2>
          <button class="btn btn-ghost btn-sm" @click="closeChildGen">关闭</button>
        </div>
        <p class="mb-4 text-xs text-slate-400">子图只描述子素材差异，系统会把母图作为身份、结构、材质和画风参考传入模型。</p>
        <div class="grid gap-4 sm:grid-cols-2">
          <div>
            <div class="mb-2 text-xs text-slate-400">母素材参考</div>
            <button v-if="imageUrl(parentAsset(childGenAsset))" class="block h-48 w-full overflow-hidden rounded-xl border border-line bg-black/20" @click="showAsset(parentAsset(childGenAsset))">
              <img :src="imageUrl(parentAsset(childGenAsset))" class="h-full w-full object-contain" :alt="parentAsset(childGenAsset)?.name || '母素材'" />
            </button>
            <div v-else class="flex h-48 items-center justify-center rounded-xl border border-dashed border-line text-xs text-slate-500">母图尚未生成，仍可先保存子素材提示词</div>
            <div class="mt-2 text-xs text-slate-300">{{ parentAsset(childGenAsset)?.name || childGenAsset.parent_ref }}</div>
          </div>
          <div>
            <div class="mb-2 text-xs text-slate-400">当前子素材</div>
            <button v-if="imageUrl(childGenAsset)" class="block h-48 w-full overflow-hidden rounded-xl border border-line bg-black/20" @click="showAsset(childGenAsset)">
              <img :src="imageUrl(childGenAsset)" class="h-full w-full object-contain" :alt="childGenAsset.name" />
            </button>
            <div v-else class="flex h-48 items-center justify-center rounded-xl border border-dashed border-line text-xs text-slate-500">子图尚未生成</div>
            <div class="mt-2 text-xs text-slate-300">{{ childGenAsset.name }} <span class="text-slate-500">· {{ relationLabel(childGenAsset) }}</span></div>
          </div>
        </div>
        <div class="mt-5 flex justify-end gap-2">
          <button class="btn btn-ghost" @click="closeChildGen">取消</button>
          <button class="btn" :disabled="!!genning || (!selectedVendor && !isChatGPTQueue)" @click="submitChildGen">{{ genning ? '生成中…' : '继承母图并生成子图' }}</button>
        </div>
      </div>
    </div>
    <div v-if="detailAsset" class="overlay p-4" @click.self="closeAssetDetails">
      <section class="glass modal-h-lg w-full max-w-3xl overflow-y-auto p-5">
        <div class="mb-4 flex items-center justify-between gap-3"><div><h2 class="text-lg font-bold text-slate-100">{{ detailEditing ? '编辑资产设定' : detailAsset.name }}</h2><p class="mt-1 text-2xs text-slate-500">{{ detailAsset.ref }} · {{ detailAsset.parent_ref ? '子素材' : '母素材' }} · v{{ detailAsset.asset_revision || 1 }}</p></div><div class="flex gap-2"><button v-if="!detailEditing" class="btn btn-ghost btn-sm" @click="detailEditing = true">编辑设定</button><button class="btn btn-ghost btn-sm" @click="closeAssetDetails">关闭</button></div></div>
        <button v-if="imageUrl(detailAsset)" class="mb-4 block max-h-[45vh] w-full overflow-hidden rounded-xl border border-line bg-black/20" title="点击查看大图" @click="showAsset(detailAsset)"><img :src="imageUrl(detailAsset)" class="max-h-[45vh] w-full object-contain" :alt="detailAsset.name" /></button>
        <div v-else class="mb-4 flex h-32 items-center justify-center rounded-xl border border-dashed border-line text-xs text-slate-500">尚未生成图片</div>
        <div class="mb-4 flex flex-wrap items-center gap-2"><span class="rounded bg-white/5 px-2 py-1 text-2xs text-slate-400">{{ detailAsset.usage || detailAsset.kind }}</span><span v-if="detailAsset.relation" class="rounded bg-cyan-400/10 px-2 py-1 text-2xs text-cyan-200">{{ relationLabel(detailAsset) }}</span><span v-if="detailAsset.style" class="rounded bg-violet-400/10 px-2 py-1 text-2xs text-violet-200" title="该资产生图使用自己的画风，不跟随项目生图风格">画风：{{ detailStyleLabels[detailAsset.style] || detailAsset.style }}</span><Versions v-if="assetFilePath(detailAsset)" :path="assetFilePath(detailAsset)" kind="image" @restored="load" /></div>
        <div v-if="affectedAssetRef === detailAsset.ref && affectedShots.length" class="mb-4 rounded-lg border border-amber-400/20 bg-amber-400/5 p-3"><div class="text-xs-plus text-amber-200">本次修改影响 {{ affectedShots.length }} 个镜头：{{ affectedShots.join('、') }}</div><button class="btn btn-ghost btn-sm mt-2" :disabled="rebuildingAffected" @click="rebuildAffectedPromptShots">{{ rebuildingAffected ? '重建中…' : '只重建这些镜头提示词' }}</button></div>
        <div v-if="detailEditing" class="space-y-3">
          <label class="block text-xs text-slate-400">素材名称<input v-model="detailName" class="input mt-1 w-full" /></label>
          <label class="block text-xs text-slate-400">画风覆盖
            <span class="ml-2 text-2xs text-slate-500">默认跟随项目生图风格；给单个资产指定可实现一部剧多画风混搭</span>
            <StyledSelect v-model="detailStyle" class="mt-1" :options="['', ...imageSkills.map(s => s.id)]" :labels="detailStyleLabels" placeholder="项目默认" />
          </label>
          <label class="block text-xs text-slate-400">画风提示词（自由文本，最高优先级）
            <span class="ml-2 text-2xs text-slate-500">留空 = 用上方 skill 或项目默认；填写后该资产完全按此画风生成</span>
            <textarea v-model="detailStylePrompt" class="textarea mt-1 min-h-16 w-full" placeholder="例：日式动漫赛璐璐插画风格，平涂上色，柔和阴影…" />
          </label>
          <label class="block text-xs text-slate-400">外观提示词（只写外观/场景/物件事实，不写画风）
            <span class="ml-2 text-2xs text-slate-500">输入 @ 或素材名称检索全局资产，选中后写入规范引用</span>
            <div class="relative mt-1">
              <textarea
                ref="detailPromptArea"
                v-model="detailPrompt"
                class="textarea min-h-36 w-full"
                placeholder="只写本素材稳定设定；子素材只写与母素材的差异。场景可输入 @ 调用人物、道具或其它场景"
                @input="onDetailPromptInput"
                @click="updateDetailMentionContext"
                @keyup="updateDetailMentionContext"
                @keydown="onDetailPromptKeydown"
              />
              <div v-if="detailMentionOpen && detailMentionCandidates.length" class="absolute inset-x-0 top-full z-20 mt-1 max-h-64 overflow-y-auto rounded-xl border border-cyan-300/20 bg-slate-950/95 p-1 shadow-2xl backdrop-blur">
                <button
                  v-for="(candidate, index) in detailMentionCandidates"
                  :key="candidate.ref"
                  type="button"
                  class="flex w-full items-center gap-2 rounded-lg px-2 py-2 text-left transition"
                  :class="detailMentionIndex === index ? 'bg-cyan-400/15 text-cyan-100' : 'text-slate-300 hover:bg-white/10'"
                  @mousedown.prevent="insertDetailAssetRef(candidate)"
                >
                  <span v-if="imageUrl(candidate)" class="h-8 w-8 shrink-0 overflow-hidden rounded border border-line bg-black/20"><img :src="imageUrl(candidate)" class="h-full w-full object-cover" :alt="candidate.name" /></span>
                  <span v-else class="flex h-8 w-8 shrink-0 items-center justify-center rounded border border-dashed border-line text-2xs text-slate-500">—</span>
                  <span class="min-w-0 flex-1"><span class="block truncate text-xs">{{ candidate.name || candidate.id }}</span><span class="block truncate text-2xs text-slate-500">{{ assetKindLabel(candidate.kind) }} · {{ candidate.ref }}<template v-if="candidate.parent_name"> · {{ candidate.parent_name }}</template></span></span>
                </button>
              </div>
            </div>
          </label>
          <div v-if="detailLinkedAssets.length" class="rounded-lg border border-cyan-400/15 bg-cyan-400/5 p-2">
            <div class="mb-1 text-2xs text-cyan-200">已关联全局资产（保存后写入依赖关系）</div>
            <div class="flex flex-wrap gap-1.5">
              <span v-for="linked in detailLinkedAssets" :key="linked.ref" class="inline-flex items-center gap-1 rounded-full bg-white/10 px-2 py-1 text-2xs text-slate-300">
                <span>{{ linked.name }}</span><button type="button" class="btn-danger rounded px-1 transition" :aria-label="'移除 ' + linked.name" @click="removeDetailAssetRef(linked.ref)">×</button>
              </span>
            </div>
          </div>
          <div class="flex justify-end gap-2"><button class="btn btn-ghost" :disabled="detailSaving" @click="detailEditing = false">取消</button><button class="btn" :disabled="detailSaving" @click="saveAssetEdit">{{ detailSaving ? '保存中…' : '保存设定并标记受影响镜头' }}</button></div>
        </div>
        <div v-else class="space-y-3">
          <div><h3 class="mb-1 text-xs font-semibold text-slate-300">外观提示词</h3><pre class="max-h-40 overflow-auto whitespace-pre-wrap rounded-lg bg-black/25 p-3 text-xs-plus leading-relaxed text-slate-300">{{ promptLayers?.subject || detailAsset.prompt || '暂无已保存提示词' }}</pre></div>
          <div><h3 class="mb-1 text-xs font-semibold text-slate-300">画风提示词 <span class="ml-1 rounded bg-white/10 px-1.5 py-0.5 text-2xs font-normal text-slate-400">{{ ({ asset_text: '本资产自由文本', asset_skill: '本资产指定 skill', project: '项目生图风格', none: '未配置' } as Record<string, string>)[promptLayers?.style_source || 'none'] }}</span></h3><pre class="max-h-32 overflow-auto whitespace-pre-wrap rounded-lg bg-black/25 p-3 text-xs-plus leading-relaxed text-violet-200">{{ promptLayers?.style || '（未配置画风，按模型默认）' }}</pre></div>
          <div v-if="promptLayers?.constraint"><h3 class="mb-1 text-xs font-semibold text-slate-300">类别硬约束（生成时强制，不可被覆盖）</h3><pre class="whitespace-pre-wrap rounded-lg bg-black/25 p-3 text-xs-plus leading-relaxed text-amber-200">{{ promptLayers.constraint }}</pre></div>
          <div><h3 class="mb-1 text-xs font-semibold text-slate-300">负面提示词（全局统一，只读）</h3><pre class="max-h-24 overflow-auto whitespace-pre-wrap rounded-lg bg-black/25 p-3 text-xs-plus leading-relaxed text-rose-200/80">{{ promptLayers?.negative || '—' }}</pre></div>
          <details v-if="promptLayers?.final" class="rounded-lg border border-line"><summary class="cursor-pointer px-3 py-2 text-xs-plus text-slate-400">最终合成提示词（模型实际收到的完整文本）</summary><pre class="max-h-64 overflow-auto whitespace-pre-wrap border-t border-line p-3 text-xs-plus leading-relaxed text-slate-400">{{ promptLayers.final }}</pre></details>
        </div>
        <div v-if="!detailEditing && detailRelationAssets.length" class="mt-4 rounded-lg border border-cyan-400/15 bg-cyan-400/5 p-3"><h3 class="mb-2 text-xs-plus font-semibold text-cyan-200">已关联全局资产</h3><div class="flex flex-wrap gap-1.5"><button v-for="linked in detailRelationAssets" :key="linked.ref" type="button" class="rounded-full bg-white/10 px-2 py-1 text-2xs text-slate-300 hover:bg-cyan-400/15 hover:text-cyan-100" @click="openAssetDetails(linked)">{{ assetKindLabel(linked.kind) }} · {{ linked.name }}</button></div></div>
        <div v-if="detailAsset.aliases?.length" class="mt-4"><h3 class="mb-2 text-xs font-semibold text-slate-300">别名</h3><div class="flex flex-wrap gap-1.5"><span v-for="alias in detailAsset.aliases" :key="alias" class="rounded bg-white/5 px-2 py-1 text-2xs text-slate-400">{{ alias }}</span></div></div>
      </section>
    </div>
    <OverlayViewer :images="lightboxImage ? [lightboxImage] : []" :index="0" :visible="lightboxVisible" @close="lightboxVisible = false" />
  </div>
</template>
