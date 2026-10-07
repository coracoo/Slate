<script setup lang="ts">
// -*- coding: utf-8 -*-
/**
 * ① 第一步「全剧最小单元」卡片：剧本/一句话 → 生成最小单元 → 体检 → 锚定。
 * 锚定之后，第二步逐集扩写才吃这套设定（后端 expand 注入），② 素材生成改为投影。
 * 产物分落各既有 json 包：剧本/大纲.json 加厚、剧本/埋线.json、素材/{人物,场景,道具}.json 设定层。
 */
import { ref, computed, watch, nextTick } from 'vue'
import {
  fetchUnits, anchorUnits, editUnits,
  type UnitsBundle, type UnitForeshadow, type UnitEpisode, type UnitArc,
} from '../api'
import { toast } from '../stores/app'
import BatchDiffReview from './BatchDiffReview.vue'
import { changedGroups, reviewText, type ReviewItem } from '../utils/reviewDiff'

const props = defineProps<{ project: string; disabled?: boolean; revision?: number; canLock?: boolean; managedConfirmation?: boolean }>()
const emit = defineEmits<{ (e: 'changed'): void; (e: 'busy-change', value: boolean): void }>()

const bundle = ref<UnitsBundle | null>(null)
const open = ref(true)   // 默认展开：体检结论（阻断/警告）是用户需要第一时间看到的
const busy = ref('')
const reviewOpen=ref(false), reviewTitle=ref(''), reviewItems=ref<ReviewItem[]>([]), reviewError=ref('')
let submitReviewed: ((ids:string[])=>Promise<unknown>) | null=null, reviewProject=''
function review(title:string, items:ReviewItem[], submit:(ids:string[])=>Promise<unknown>) {
  batchEpisodeMode.value=false
  reviewTitle.value=title;reviewItems.value=items;submitReviewed=submit;reviewProject=props.project
  reviewError.value='';reviewOpen.value=true
}
async function confirmReview(ids:string[]) {
  if(!submitReviewed || reviewProject!==props.project) return
  reviewError.value=''
  const ok=await submitReviewed(ids)
  if(ok===false) {if(!reviewError.value) reviewError.value='保存失败，审核稿已保留。请处理顶部错误后重试。'}
  else reviewOpen.value=false
}
const tab = ref<'arcs' | 'episodes' | 'threads' | 'assets' | 'index'>('arcs')
const cardRoot = ref<HTMLElement | null>(null)
async function showTab(value: typeof tab.value) {
  open.value = true
  tab.value = value
  await nextTick()
  cardRoot.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}
async function showIssue(path: string, refStr?: string) {
  await showTab(path.includes('人物') || path.includes('场景') || path.includes('道具') ? 'assets' : path.startsWith('foreshadows') || path.startsWith('hooks') ? 'threads' : path.startsWith('episodes') ? 'episodes' : 'arcs')
  if (path.includes('场景')) assetKind.value = '场景'
  if (path.includes('道具')) assetKind.value = '道具'
  if (refStr) { await nextTick(); assetPick.value = refStr }
}
defineExpose({ showTab, showIssue, ready: () => activeLoad ?? Promise.resolve(), confirm: () => doAnchor(), hasUnsaved: computed(() => unsaved.value) })
const threadsDraft = ref<UnitForeshadow[]>([])
const assetDraft = ref<Record<string, string>>({})
const arcsDraft = ref<UnitArc[]>([])
const premiseDraft = ref('')
const episodeDraft = ref<UnitEpisode | null>(null)
const beatsDraft = ref('')
const batchEpisodeMode=ref(false), batchEpisodeDrafts=ref<Record<string,Record<string,unknown>>>({})
const episodeLabels={title:'标题',summary:'本集概要',hook:'开场钩子',cliff:'集尾悬念',arc_id:'所属分段',beats:'节奏节点'}
const batchEpisodeItems=computed(()=>Object.entries(batchEpisodeDrafts.value).map(([id,fields])=>({id,title:id,groups:changedGroups((episodes.value.find(e=>e.id===id) || {}) as unknown as Record<string,unknown>,fields,episodeLabels)})))
function editEpisodeBatch() {
  if(editingDisabled.value || !bundle.value) return
  if(unsaved.value) {toast('请先保存或取消当前单项编辑，再打开批量编辑','info');return}
  const project=props.project
  let revision=bundle.value.planning_revision
  batchEpisodeDrafts.value=Object.fromEntries(episodes.value.map(ep=>[ep.id,Object.fromEntries(Object.keys(episodeLabels).map(key=>[key,(ep as unknown as Record<string,unknown>)[key] ?? (key==='beats'?[]:'')]))]))
  review('批量编辑分集规划',[],async ids=>{
    const items=ids.filter(id=>batchEpisodeItems.value.find(i=>i.id===id)?.groups.length).map(id=>({id,fields:JSON.parse(JSON.stringify(batchEpisodeDrafts.value[id]))}))
    if(!items.length) {reviewError.value='所选分集没有修改';return false}
    const remaining=Object.fromEntries(Object.entries(batchEpisodeDrafts.value).filter(([id])=>!ids.includes(id)))
    const ok=await run('批量保存分集规划',()=>editUnits({project,kind:'episodes',items,revision}))
    if(ok && project===props.project && Object.keys(remaining).length) {
      batchEpisodeDrafts.value=remaining
      revision=bundle.value?.planning_revision || ''
      reviewError.value='所选修改已保存；未选项目仍留在弹窗中。'
      return false
    }
    return ok
  })
  batchEpisodeMode.value=true
}

const report = computed(() => bundle.value?.report)
const errs = computed(() => report.value?.errors || [])
const warns = computed(() => report.value?.warnings || [])
const arcs = computed(() => bundle.value?.outline?.arcs || [])
const rules = computed(() => bundle.value?.outline?.rules || [])
const taboos = computed(() => bundle.value?.outline?.taboos || [])
const episodes = computed(() => bundle.value?.episodes || [])
const anchored = computed(() => !!bundle.value?.anchored)
const editingDisabled = computed(() => !!busy.value || props.disabled || anchored.value)
const assetFieldDisabled = (key: string) => !!busy.value || props.disabled || (anchored.value && key !== 'visual_description')
const unsaved = computed(() => JSON.stringify(arcsDraft.value) !== JSON.stringify(arcs.value)
  || premiseDraft.value !== (bundle.value?.outline.premise || '')
  || JSON.stringify(threadsDraft.value) !== JSON.stringify(bundle.value?.foreshadows || [])
  || Object.keys(assetDraft.value).length > 0 || !!episodeDraft.value)
/** 反查索引行：素材 → 首次出场/关键集/所属段/牵连伏笔/受影响分镜（全部派生，不写回档案） */
const indexRows = computed(() => Object.values(bundle.value?.index || {})
  .sort((a, b) => (a.ref || '').localeCompare(b.ref || '', 'zh-CN', { numeric: true })))   // 自然序：S2 排在 S10 前

/* ---------- 素材设定：横向选实体（一屏能看完谁有设定谁没有），点开才出字段表 ---------- */
const assetKind = ref<'人物' | '场景' | '道具'>('人物')
const assetPick = ref('')
type AssetField = { key: string; label: string; wide?: boolean }
const CHAR_FIELDS: AssetField[] = [
  { key: 'bio_arc', label: '弧光：从什么变成什么' },
  { key: 'biography', label: '剧本小传' },
  { key: 'bio_language', label: '语言风格' },
  { key: 'bio_crack', label: '说话的破绽' },
  { key: 'bio_pressure', label: '被逼急时怎么做' },
  { key: 'bio_address', label: '称呼规则' },
]
const SCENE_FIELDS: AssetField[] = [
  { key: 'visual_description', label: '外观描绘：布局、材质、色彩与光线', wide: true },
  { key: 'spatial_limit', label: '空间对行动的限制' },
  { key: 'action_slots', label: '可复用动作位置（分号分隔）' },
]
const PROP_FIELDS: AssetField[] = [
  { key: 'visual_description', label: '外观描绘：形状、材质、颜色与辨识细节', wide: true },
  { key: 'usage_boundary', label: '使用边界：何时生效、何时不生效、代价' },
]

type AssetRow = { ref: string; name: string; fields: Record<string, string>; note?: string }

function fieldText(kind: '人物' | '场景' | '道具', refStr: string, key: string): string {
  const draftKey = `${refStr}#${key}`
  if (assetDraft.value[draftKey] !== undefined) return assetDraft.value[draftKey]
  const b = bundle.value
  if (!b) return ''
  if (kind === '人物') {
    const row = (b.bios || []).find(r => r.ref === refStr) as Record<string, unknown> | undefined
    return String(row?.[key] ?? '')
  }
  const row = (kind === '场景' ? b.scene_limits : b.prop_boundaries).find(r => r.ref === refStr) as Record<string, unknown> | undefined
  const raw = row?.[key]
  if (Array.isArray(raw)) return raw.join('；')
  const text = String(raw ?? '').trim()
  if (text === '[]') return ''   // LLM 把空数组二次序列化成字符串 '[]' 的脏数据
  return text
}

const assetRows = computed<AssetRow[]>(() => {
  const b = bundle.value
  if (!b) return []
  if (assetKind.value === '人物') {
    return (b.bios || []).map(r => ({
      ref: r.ref, name: r.name,
      note: (r.states || []).length ? `状态 ${r.states!.length}` : '',
      fields: Object.fromEntries(CHAR_FIELDS.map(f => [f.key, fieldText('人物', r.ref, f.key)])),
    }))
  }
  const list = assetKind.value === '场景' ? (b.scene_limits || []) : (b.prop_boundaries || [])
  const fields = assetKind.value === '场景' ? SCENE_FIELDS : PROP_FIELDS
  return list.map(r => ({
    ref: r.ref, name: r.name,
    fields: Object.fromEntries(fields.map(f => [f.key, fieldText(assetKind.value, r.ref, f.key)])),
  }))
})

const assetFields = computed<AssetField[]>(() =>
  assetKind.value === '人物' ? CHAR_FIELDS : assetKind.value === '场景' ? SCENE_FIELDS : PROP_FIELDS)

const assetCurrent = computed<AssetRow | null>(() =>
  assetRows.value.find(r => r.ref === assetPick.value) || assetRows.value[0] || null)

/** 已填 complete 与否只统计"有没有值"，用来在横条上标色，不判内容对错 */
const filledCount = (row: AssetRow) => Object.values(row.fields).filter(v => String(v || '').trim()).length

watch(assetKind, () => { assetPick.value = '' })
watch(bundle, () => { if (!assetPick.value) assetPick.value = assetRows.value[0]?.ref || '' })

/** 集号 → 分段名，供分集表显示"这段要完成什么" */
const arcOf = (ep: UnitEpisode) => arcs.value.find(a => a.id === ep.arc_id)

function refName(refStr: string): string {
  const hit = [...(bundle.value?.bios || []), ...(bundle.value?.scene_limits || []),
    ...(bundle.value?.prop_boundaries || [])].find(r => r.ref === refStr)
  return hit?.name || refStr
}

let loadSeq = 0
let activeLoad: Promise<void> | null = null
function load(preserveDrafts = false) {
  activeLoad = loadBundle(preserveDrafts)
  return activeLoad
}
async function loadBundle(preserveDrafts = false) {
  const request = ++loadSeq
  if (!props.project) return
  try {
    const next = await fetchUnits(props.project)
    if (request !== loadSeq) return
    const keep = preserveDrafts && unsaved.value
    bundle.value = next
    if (!keep) {
      arcsDraft.value = JSON.parse(JSON.stringify(next.outline.arcs || []))
      premiseDraft.value = next.outline.premise || ''
      episodeDraft.value = null
      threadsDraft.value = JSON.parse(JSON.stringify(next.foreshadows || []))
      assetDraft.value = {}
    }
  } catch (e) {
    toast(e instanceof Error ? `读取最小单元失败：${e.message}` : '读取最小单元失败', 'err', 6000)
  }
}
watch(() => props.project, () => {
  reviewOpen.value=false
  bundle.value = null
  threadsDraft.value = []
  assetDraft.value = {}
  assetPick.value = ''
  void load()
}, { immediate: true })
watch(() => props.revision, () => { void load(true) })

async function run(label: string, fn: () => Promise<unknown>) {
  if (busy.value || props.disabled) return
  busy.value = label
  emit('busy-change', true)
  try {
    await fn()
    toast(`${label}完成`, 'ok', 4000)
    await load()
    emit('changed')   // 父页同步刷新分集列表（分集加厚条目与正文状态都变了）
    return true
  } catch (e) {
    toast(e instanceof Error ? `${label}失败：${e.message}` : `${label}失败`, 'err', 7000)
    return false
  } finally {
    busy.value = ''
    emit('busy-change', false)
  }
}

const doAnchor = () => run('确认规划', async () => {
  const r = await anchorUnits(props.project)
  if (!r.ok) throw new Error(`体检有 ${r.errors?.length || 0} 项阻断，先修：${r.errors?.[0]?.message || ''}`)
})
const doUnlock = () => run('解除设定锁定', () => editUnits({ project: props.project, kind: 'unlock' }))


function saveOutline() {
  const project=props.project, fields={premise:premiseDraft.value,arcs:JSON.parse(JSON.stringify(arcsDraft.value))}
  review('审核主线与分段',[{id:'outline',title:'主线与分段',groups:changedGroups({premise:bundle.value?.outline.premise,arcs:bundle.value?.outline.arcs},fields,{premise:'故事主线',arcs:'剧情分段'})}],()=>run('保存主线与分段',()=>editUnits({project,kind:'outline',fields})))
}
function beginEpisodeEdit(ep: UnitEpisode) {
  episodeDraft.value = { ...ep }
  beatsDraft.value = (ep.beats || []).join('\n')
}
function saveEpisode() {
  const ep = episodeDraft.value
  if (!ep) return
  const project=props.project, id=ep.id, fields={
    title: ep.title || '', summary: ep.summary || '', hook: ep.hook || '', cliff: ep.cliff || '',
    arc_id: ep.arc_id || '', beats: beatsDraft.value.split(/\n+/).map(s => s.trim()).filter(Boolean),
  }
  const previous=episodes.value.find(row=>row.id===id)
  review('审核分集卡',[{id,title:id,groups:changedGroups(Object.fromEntries(Object.keys(fields).map(key=>[key,(previous as unknown as Record<string,unknown> | undefined)?.[key]])),fields,{title:'标题',summary:'概要',hook:'开场钩子',cliff:'集尾悬念',arc_id:'所属分段',beats:'节奏节点'})}],()=>run('保存分集卡',()=>editUnits({project,kind:'episode',id,fields})))
}

async function saveThreads() {
  const project=props.project, foreshadows=JSON.parse(JSON.stringify(threadsDraft.value))
  review('审核伏笔与回收',[{id:'threads',title:'埋线表',groups:[{label:'伏笔与回收',before:reviewText(bundle.value?.foreshadows),after:reviewText(foreshadows)}]}],()=>run('保存埋线表', async () => {
    const r = await editUnits({ project, kind: 'threads', foreshadows })
    if (!r.ok) throw new Error(r.err || '保存失败')
    const blocked = (r.report?.errors || []).filter(e => e.path.startsWith('foreshadows'))
    if (blocked.length) toast(`已保存，但体检仍有 ${blocked.length} 条伏笔阻断项`, 'info', 7000)
  }))
}

async function saveAsset(refStr: string, field: string, lock: boolean) {
  const value = assetDraft.value[`${refStr}#${field}`]
  if (value === undefined) return
  const project=props.project
  const before=[...(bundle.value?.bios || []),...(bundle.value?.scene_limits || []),...(bundle.value?.prop_boundaries || [])].find(row=>row.ref===refStr) as unknown as Record<string,unknown> | undefined
  review('审核素材设定',[{id:refStr,title:refName(refStr),groups:[{label:[...CHAR_FIELDS,...SCENE_FIELDS,...PROP_FIELDS].find(f=>f.key===field)?.label || field,before:reviewText(before?.[field]),after:value}]}],()=>run('保存设定', async () => {
    const [kind, id] = refStr.replace('@', '').split(':')
    const zone = kind === 'character' ? '人物' : kind === 'scene' ? '场景' : '道具'
    const r = await editUnits({ project, kind: 'asset', zone, id,
      fields: { [field]: value }, lock: lock ? [field] : [] })
    if (!r.ok) throw new Error(r.err || '保存失败')
    if (r.rejected?.length) toast(`这些字段不认：${r.rejected.join('、')}`, 'info', 6000)
  }))
}
</script>

<template>
  <section ref="cardRoot" class="glass mb-5 p-4">
    <button class="flex w-full items-center gap-2 text-left" @click="open = !open">
      <span class="flex h-6 w-6 items-center justify-center rounded-full bg-cyan-400/15 text-xs font-black text-cyan-300">单</span>
      <h3 class="text-sm font-bold text-slate-200">全剧设定与连续性</h3>
      <span v-if="anchored" class="rounded bg-emerald-400/15 px-2 py-0.5 text-2xs text-emerald-300">
        已确认 v{{ bundle?.anchor_rev }}
      </span>
      <span v-else class="rounded bg-amber-400/15 px-2 py-0.5 text-2xs text-amber-300">待确认</span>
      <span v-if="errs.length" class="rounded bg-rose-400/15 px-2 py-0.5 text-2xs text-rose-300">
        待处理 {{ errs.length }}
      </span>
      <span v-else-if="warns.length" class="rounded bg-amber-400/15 px-2 py-0.5 text-2xs text-amber-300">
        待补 {{ warns.length }}
      </span>
      <span class="text-xs-plus text-slate-400">
        分段、伏笔、人物与场景设定
      </span>
      <span class="ml-auto text-xs text-slate-500">{{ open ? '▲ 收起' : '▼ 展开' }}</span>
    </button>

    <div v-if="open" class="mt-3 border-t border-line-soft pt-3">
      <button v-if="managedConfirmation && anchored" class="mb-2 text-sm text-sky-200 underline" :disabled="!!busy || props.disabled" @click="doUnlock">修改已确认规划</button>
      <div v-if="!managedConfirmation" class="flex flex-wrap items-center gap-3">
        <button v-if="!anchored" class="btn btn-sm" :disabled="!!busy || props.disabled || props.canLock === false || unsaved || !bundle?.workflow?.can_anchor" @click="doAnchor">确认规划</button>
        <button v-else class="btn btn-sm btn-ghost" :disabled="!!busy || props.disabled" @click="doUnlock">解除设定锁定</button>
        <span class="text-xs text-slate-400">人工核对内容后锁定；检查只验证结构与引用。</span>
      </div>
      <p v-if="unsaved" class="mt-2 text-sm text-amber-300">正在编辑的内容尚未保存。</p>
      <p v-else-if="!managedConfirmation && props.canLock === false" class="mt-2 text-xs text-amber-300">制作规则或构想有未保存修改。</p>

      <details v-if="errs.length || warns.length" class="mt-3 rounded bg-slate-500/5 p-2 text-xs">
        <summary class="cursor-pointer text-slate-300">检查详情 · {{ errs.length }} 项待处理，{{ warns.length }} 项建议</summary>
        <div v-for="e in errs" :key="e.path + e.code" class="text-rose-300">
          ✗ {{ e.path }}：{{ e.message }}
        </div>
        <div v-for="w in warns.slice(0, 6)" :key="w.path + w.code" class="text-amber-300">
          ! {{ w.path }}：{{ w.message }}
        </div>
      </details>

      <div class="mt-3 flex gap-2 text-xs">
        <button v-for="t in ([['arcs', `分段与规则 ${arcs.length}`], ['episodes', `分集规划 ${episodes.length}`],
          ['threads', `埋线 ${threadsDraft.length}`], ['assets', `素材设定 ${(bundle?.bios?.length || 0) + (bundle?.scene_limits?.length || 0) + (bundle?.prop_boundaries?.length || 0)}`],
          ['index', `引用关系 ${indexRows.length}`]] as const)"
          :key="t[0]" class="rounded px-2 py-1"
          :class="tab === t[0] ? 'bg-cyan-400/15 text-cyan-200' : 'text-slate-400 hover:text-slate-200'"
          @click="tab = t[0] as typeof tab">{{ t[1] }}</button>
      </div>

      <!-- 分段与规则 -->
      <div v-if="tab === 'arcs'" class="mt-3 space-y-3 text-xs">
        <label v-if="arcs.length" class="block text-slate-300">全剧主线
          <textarea v-model="premiseDraft" rows="3" class="textarea mt-1 w-full" :disabled="editingDisabled"></textarea>
        </label>
        <p v-if="!arcs.length" class="text-slate-500">还没有分段：先「保存规则并生成规划」，分段是逐集扩写的批次单位与阶段目标。</p>
        <table v-else class="w-full text-2xs">
          <thead class="text-left text-slate-400"><tr><th class="py-2">分段</th><th>起始集 — 结束集</th><th>阶段目标</th><th>信息释放</th></tr></thead>
          <tbody>
            <tr v-for="a in arcsDraft" :key="a.id" class="border-t border-line-soft align-top">
              <td class="py-1 pr-2 font-bold text-slate-300">{{ a.id }}</td>
              <td class="pr-2 text-slate-400"><input v-model="a.ep_from" class="input w-16" :disabled="editingDisabled" aria-label="分段起始集" /> ~ <input v-model="a.ep_to" class="input w-16" :disabled="editingDisabled" aria-label="分段结束集" /></td>
              <td class="pr-2 text-slate-200"><textarea v-model="a.goal" rows="2" class="textarea w-full" :disabled="editingDisabled" aria-label="分段目标"></textarea></td>
              <td class="text-slate-500">释放：{{ (a.release || []).join('；') }}</td>
            </tr>
          </tbody>
        </table>
        <button v-if="arcs.length" class="btn btn-sm" :disabled="editingDisabled" @click="saveOutline">保存主线与分段</button>
        <div v-if="rules.length">
          <h4 class="font-bold text-slate-300">全剧规则（能力边界）</h4>
          <p v-for="r in rules" :key="r.id" class="text-2xs text-slate-400">
            <b class="text-slate-300">{{ r.id }}</b> {{ r.text }}
            <span v-if="r.check_hint" class="text-slate-500">（违例：{{ r.check_hint }}）</span>
          </p>
        </div>
        <div v-if="taboos.length">
          <h4 class="font-bold text-slate-300">创作禁区</h4>
          <p v-for="t in taboos" :key="t.id" class="text-2xs text-slate-400">
            <b class="text-rose-300">{{ t.id }}</b> {{ t.rule }}
            <span class="text-slate-500">机检词：{{ (t.detect || []).join('、') || '（无，无法自动检查）' }}</span>
          </p>
        </div>
      </div>

      <!-- 分集加厚 -->
      <div v-else-if="tab === 'episodes'" class="mt-3 overflow-x-auto text-2xs">
        <button v-if="episodes.length" class="btn mb-3" :disabled="editingDisabled" @click="editEpisodeBatch">批量编辑分集规划</button>
        <p v-if="!episodes.length" class="text-slate-500">还没有加厚分集条目。</p>
        <table v-else class="w-full">
          <thead class="text-slate-500">
            <tr><th class="py-1 text-left">集</th><th class="text-left">段/阶段目标</th><th class="text-left">节拍</th>
              <th class="text-left">待埋</th><th class="text-left">待收</th><th class="text-left">人物·场景·道具</th><th class="text-left">正文</th></tr>
          </thead>
          <tbody>
            <tr v-for="e in episodes" :key="e.id" class="border-t border-line-soft align-top">
              <td class="py-1 pr-2 font-bold text-slate-200">{{ e.id }}<button class="ml-2 text-cyan-300" :disabled="editingDisabled" @click="beginEpisodeEdit(e)">编辑</button></td>
              <td class="pr-2 text-slate-400">{{ e.arc_id }}<span class="text-slate-500"> {{ arcOf(e)?.goal || '' }}</span></td>
              <td class="pr-2 text-slate-300">{{ (e.beats || []).join(' → ') || '—' }}</td>
              <td class="pr-2 text-cyan-300">{{ (e.fs_plant || []).join('、') || '—' }}</td>
              <td class="pr-2 text-emerald-300">{{ (e.fs_pay || []).join('、') || '—' }}</td>
              <td class="pr-2 text-slate-400">
                {{ (e.cast_refs || []).length }}·{{ (e.scene_refs || []).length }}·{{ (e.key_asset_refs || []).length }}
                <div v-if="e.state_derive?.length" class="text-slate-500">
                  {{ e.state_derive.map(d => `${refName(d.ref)}→${d.label || d.state_id}`).join('；') }}
                </div>
              </td>
              <td :class="e.has_text ? 'text-emerald-300' : 'text-slate-500'">
                {{ e.has_text ? `${e.text_len} 字` : '未写' }}
              </td>
            </tr>
          </tbody>
        </table>
        <div v-if="episodeDraft" class="mt-4 space-y-3 rounded-xl border border-line-soft p-3">
          <h4 class="font-bold text-slate-200">{{ episodeDraft.id }} · 分集卡</h4>
          <div class="grid gap-3 sm:grid-cols-2">
            <label>标题<input v-model="episodeDraft.title" class="input mt-1 w-full" :disabled="editingDisabled" /></label>
            <label>所属分段<select v-model="episodeDraft.arc_id" class="input mt-1 w-full" :disabled="editingDisabled"><option value="">请选择</option><option v-for="a in arcs" :key="a.id" :value="a.id">{{ a.id }} · {{ a.goal }}</option></select></label>
            <label class="sm:col-span-2">本集概要<textarea v-model="episodeDraft.summary" rows="3" class="textarea mt-1 w-full" :disabled="editingDisabled"></textarea></label>
            <label>开场钩子<textarea v-model="episodeDraft.hook" rows="2" class="textarea mt-1 w-full" :disabled="editingDisabled"></textarea></label>
            <label>结尾落点<textarea v-model="episodeDraft.cliff" rows="2" class="textarea mt-1 w-full" :disabled="editingDisabled"></textarea></label>
            <label class="sm:col-span-2">剧情节拍（每行一条）<textarea v-model="beatsDraft" rows="4" class="textarea mt-1 w-full" :disabled="editingDisabled"></textarea></label>
          </div>
          <button class="btn btn-sm" :disabled="editingDisabled" @click="saveEpisode">保存分集卡</button>
          <button class="btn btn-sm btn-ghost ml-2" @click="episodeDraft = null">取消编辑</button>
        </div>
      </div>

      <!-- 埋线表（可编辑） -->
      <div v-else-if="tab === 'threads'" class="mt-3 text-2xs">
        <p v-if="!threadsDraft.length" class="text-slate-500">
          还没有伏笔表。每条线必须成对：埋在 E几 / 收在 E几，收点不得早于埋点——单向登记的线一定会被遗忘。
        </p>
        <template v-else>
          <table class="w-full">
            <thead class="text-slate-500">
              <tr><th class="py-1 text-left">id</th><th class="text-left">埋什么</th><th class="text-left">埋在</th>
                <th class="text-left">收在</th><th class="text-left">依赖素材</th><th class="text-left">状态</th></tr>
            </thead>
            <tbody>
              <tr v-for="f in threadsDraft" :key="f.id" class="border-t border-line-soft align-top">
                <td class="py-1 pr-2 font-bold text-slate-300">{{ f.id }}</td>
                <td class="pr-2">
                  <input v-model="f.plant" class="input w-full" :disabled="editingDisabled" />
                </td>
                <td class="pr-2"><input v-model="f.set_in" class="input w-14" :disabled="editingDisabled" /></td>
                <td class="pr-2"><input v-model="f.pay_in" class="input w-14" :disabled="editingDisabled" /></td>
                <td class="pr-2 text-slate-400">{{ (f.refs || []).map(refName).join('、') || '—' }}</td>
                <td><input v-model="f.status" class="input w-24" placeholder="open|paid|needs_review" :disabled="editingDisabled" /></td>
              </tr>
            </tbody>
          </table>
          <button class="btn btn-sm mt-2" :disabled="editingDisabled" @click="saveThreads">保存埋线表</button>
        </template>
        <div v-if="bundle?.hooks?.length" class="mt-4">
          <h4 class="text-xs font-bold text-slate-300">钩子表（只读）</h4>
          <p v-for="h in bundle.hooks" :key="h.id" class="text-slate-400">
            <b class="text-slate-300">{{ h.ep }}</b> {{ h.beat }}
            <span class="text-slate-500">— {{ h.question }}</span>
          </p>
        </div>
      </div>

      <!-- 素材设定层：横向选实体（不铺开占页面），点开才出该实体的字段表 -->
      <div v-else-if="tab === 'assets'" class="mt-3 text-2xs">
        <p v-if="!assetRows.length" class="text-slate-500">
          暂无素材设定。生成规划后，在角色设定维护人物，在视觉素材维护场景与道具。
        </p>
        <template v-else>
          <div class="mb-2 flex gap-2 text-xs">
            <button v-for="k in (['人物', '场景', '道具'] as const)" :key="k" class="rounded px-2 py-1"
              :class="assetKind === k ? 'bg-cyan-400/15 text-cyan-200' : 'text-slate-400 hover:text-slate-200'"
              @click="assetKind = k">
              {{ k }} {{ k === '人物' ? (bundle?.bios?.length || 0) : k === '场景' ? (bundle?.scene_limits?.length || 0) : (bundle?.prop_boundaries?.length || 0) }}
            </button>
          </div>
          <div class="flex max-h-32 flex-wrap gap-1 overflow-y-auto rounded border border-line-soft p-2">
            <button v-for="row in assetRows" :key="row.ref" class="rounded px-2 py-0.5 leading-5"
              :class="assetCurrent?.ref === row.ref ? 'bg-cyan-400/20 text-cyan-100'
                : (filledCount(row) ? 'bg-emerald-400/10 text-emerald-200' : 'bg-slate-500/10 text-slate-400')"
              :title="row.ref" @click="assetPick = row.ref">
              {{ row.name }}<span v-if="row.note" class="ml-1 text-slate-500">{{ row.note }}</span>
            </button>
          </div>
          <RouterLink v-if="assetKind === '人物' && assetCurrent" class="mt-2 inline-block text-sm text-cyan-300 underline" :to="{path: '/studio/characters', query: {character: assetCurrent.ref.split(':').pop()}}">编辑角色设定</RouterLink>
          <table v-if="assetCurrent" class="mt-2 w-full">
            <thead class="text-slate-500">
              <tr><th class="py-1 text-left">字段</th><th class="text-left">值</th><th class="w-28"></th></tr>
            </thead>
            <tbody>
              <tr v-for="f in assetFields" :key="f.key" class="border-t border-line-soft align-top">
                <td class="w-44 py-1 pr-2 text-slate-400">{{ f.label }}</td>
                <td class="pr-2">
                  <p v-if="assetKind === '人物'" class="whitespace-pre-wrap py-1 text-slate-200">{{ assetCurrent.fields[f.key] || '—' }}</p>
                  <textarea v-else-if="f.wide" :value="assetCurrent.fields[f.key]" class="textarea w-full" rows="4" :disabled="assetFieldDisabled(f.key)"
                    @input="assetDraft[`${assetCurrent.ref}#${f.key}`] = ($event.target as HTMLTextAreaElement).value"></textarea>
                  <input v-else :value="assetCurrent.fields[f.key]" class="input w-full" :disabled="assetFieldDisabled(f.key)"
                    @input="assetDraft[`${assetCurrent.ref}#${f.key}`] = ($event.target as HTMLInputElement).value" />
                </td>
                <td class="text-right">
                  <button v-if="assetKind !== '人物'" class="btn btn-sm" :disabled="assetFieldDisabled(f.key)" @click="saveAsset(assetCurrent.ref, f.key, true)">保存并保护</button>
                </td>
              </tr>
            </tbody>
          </table>
          <p v-if="assetCurrent" class="mt-1 text-slate-500">
            {{ assetCurrent.ref }} · 已填 {{ filledCount(assetCurrent) }}/{{ assetFields.length }}
            · 字段保护仅防止自动覆盖，全剧锁定在上方确认。
          </p>
        </template>
      </div>

      <!-- 反查索引：改这条设定会牵连哪几集／哪几张分镜（派生，不写回素材档案） -->
      <div v-else class="mt-3 overflow-x-auto text-2xs">
        <p v-if="!indexRows.length" class="text-slate-500">还没有引用面数据：生成规划后显示素材与分集的引用关系。</p>
        <table v-else class="w-full">
          <thead class="text-slate-500">
            <tr><th class="py-1 text-left">素材</th><th class="text-left">首次出场</th><th class="text-left">关键集次</th>
              <th class="text-left">所属段</th><th class="text-left">牵连伏笔</th><th class="text-left">受影响分镜</th></tr>
          </thead>
          <tbody>
            <tr v-for="row in indexRows" :key="row.ref" class="border-t border-line-soft align-top">
              <td class="py-1 pr-2 text-slate-200">{{ refName(row.ref) }}<div class="text-slate-500">{{ row.ref }}</div></td>
              <td class="pr-2 text-cyan-300">{{ row.first_ep || '—' }}</td>
              <td class="pr-2 text-slate-300">{{ (row.key_eps || []).join('、') || '—' }}</td>
              <td class="pr-2 text-slate-400">{{ (row.arcs || []).join('、') || '—' }}</td>
              <td class="pr-2 text-amber-300">{{ (row.foreshadows || []).join('、') || '—' }}</td>
              <td class="text-slate-400">{{ (row.used_by_boards || []).join('、') || '—' }}</td>
            </tr>
          </tbody>
        </table>
        <p v-if="indexRows.length" class="mt-2 text-slate-500">
          这一列就是"改一条设定要重跑哪里"的答案——它由分集引用与埋线表反查得出，不落在素材档案里（两处真相迟早分叉）。
        </p>
      </div>

    </div>
  </section>
  <BatchDiffReview v-model:open="reviewOpen" :title="reviewTitle" :items="batchEpisodeMode?batchEpisodeItems:reviewItems" :busy="!!busy" :error="reviewError" submit-label="确认保存" @confirm="confirmReview">
    <template v-if="batchEpisodeMode" #editor="{item,disabled:locked}">
      <div class="grid gap-3 md:grid-cols-2">
        <label v-for="(label,key) in episodeLabels" :key="key" class="text-sm">{{label}}
          <textarea v-if="key==='beats'" class="textarea mt-1" rows="3" :disabled="locked" :value="(batchEpisodeDrafts[item.id]!.beats as string[]).join('\n')" @input="batchEpisodeDrafts[item.id]!.beats=($event.target as HTMLTextAreaElement).value.split('\n').filter(x=>x.trim())" />
          <textarea v-else :value="String(batchEpisodeDrafts[item.id]![key] || '')" @input="batchEpisodeDrafts[item.id]![key]=($event.target as HTMLTextAreaElement).value" class="textarea mt-1" rows="3" :disabled="locked" />
        </label>
      </div>
    </template>
  </BatchDiffReview>
</template>
