<script setup lang="ts">
// -*- coding: utf-8 -*-
/**
 * ① 第一步「全剧最小单元」卡片：剧本/一句话 → 生成最小单元 → 体检 → 锚定。
 * 锚定之后，第二步逐集扩写才吃这套设定（后端 expand 注入），② 素材生成改为投影。
 * 产物分落各既有 json 包：剧本/大纲.json 加厚、剧本/埋线.json、素材/{人物,场景,道具}.json 设定层。
 */
import { ref, computed, watch } from 'vue'
import {
  fetchUnits, buildUnits, anchorUnits, editUnits,
  type UnitsBundle, type UnitForeshadow, type UnitEpisode,
} from '../api'
import { toast } from '../stores/app'
import { trackJob } from '../stores/jobs'

const props = defineProps<{ project: string }>()
const emit = defineEmits<{ (e: 'changed'): void }>()

const bundle = ref<UnitsBundle | null>(null)
const open = ref(false)
const busy = ref('')
const tab = ref<'arcs' | 'episodes' | 'threads' | 'assets' | 'index'>('arcs')
const arcSize = ref(6)
const threadsDraft = ref<UnitForeshadow[]>([])
const assetDraft = ref<Record<string, string>>({})

const report = computed(() => bundle.value?.report)
const errs = computed(() => report.value?.errors || [])
const warns = computed(() => report.value?.warnings || [])
const arcs = computed(() => bundle.value?.outline?.arcs || [])
const rules = computed(() => bundle.value?.outline?.rules || [])
const taboos = computed(() => bundle.value?.outline?.taboos || [])
const episodes = computed(() => bundle.value?.episodes || [])
const anchored = computed(() => !!bundle.value?.anchored)
/** 反查索引行：素材 → 首次出场/关键集/所属段/牵连伏笔/受影响分镜（全部派生，不写回档案） */
const indexRows = computed(() => Object.values(bundle.value?.index || {})
  .sort((a, b) => (a.ref || '').localeCompare(b.ref || '')))

/* ---------- 素材设定：横向选实体（一屏能看完谁有设定谁没有），点开才出字段表 ---------- */
const assetKind = ref<'人物' | '场景' | '道具'>('人物')
const assetPick = ref('')
type AssetField = { key: string; label: string; wide?: boolean }
const CHAR_FIELDS: AssetField[] = [
  { key: 'bio_arc', label: '弧光：从什么变成什么' },
  { key: 'bio_language', label: '语言风格' },
  { key: 'bio_crack', label: '说话的破绽' },
  { key: 'bio_pressure', label: '被逼急时怎么做' },
  { key: 'bio_address', label: '称呼规则' },
]
const SCENE_FIELDS: AssetField[] = [
  { key: 'spatial_limit', label: '空间对行动的限制' },
  { key: 'action_slots', label: '可复用动作位置（分号分隔）' },
]
const PROP_FIELDS: AssetField[] = [{ key: 'usage_boundary', label: '使用边界：何时生效、何时不生效、代价' }]

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
  return String(raw ?? '')
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

async function load() {
  if (!props.project) return
  try {
    bundle.value = await fetchUnits(props.project)
    threadsDraft.value = JSON.parse(JSON.stringify(bundle.value.foreshadows || []))
    assetDraft.value = {}
  } catch (e) {
    toast(e instanceof Error ? `读取最小单元失败：${e.message}` : '读取最小单元失败', 'err', 6000)
  }
}
watch(() => props.project, load, { immediate: true })
watch(bundle, b => { if (b) threadsDraft.value = JSON.parse(JSON.stringify(b.foreshadows || [])) })

async function run(label: string, fn: () => Promise<unknown>) {
  if (busy.value) return
  busy.value = label
  try {
    await fn()
    toast(`${label}完成`, 'ok', 4000)
    await load()
    emit('changed')   // 父页同步刷新分集列表（分集加厚条目与正文状态都变了）
  } catch (e) {
    toast(e instanceof Error ? `${label}失败：${e.message}` : `${label}失败`, 'err', 7000)
  } finally {
    busy.value = ''
  }
}

const doBuild = (thenAnchor = false, stage: 'all' | 'entity' = 'all') => run(
  stage === 'entity' ? '只续跑设定层' : (thenAnchor ? '生成并锚定最小单元' : '生成最小单元'), async () => {
    const r = await buildUnits({ project: props.project, arc_size: arcSize.value, anchor: thenAnchor, stage })
    const j = await trackJob(r.id, '生成最小单元')
    if (!j.success) throw new Error(j.err || '生成失败，明细见任务日志')
  })

const doAnchor = () => run('锚定', async () => {
  const r = await anchorUnits(props.project)
  if (!r.ok) throw new Error(`体检有 ${r.errors?.length || 0} 项阻断，先修：${r.errors?.[0]?.message || ''}`)
})

async function saveThreads() {
  await run('保存埋线表', async () => {
    const r = await editUnits({ project: props.project, kind: 'threads', foreshadows: threadsDraft.value })
    if (!r.ok) throw new Error(r.err || '保存失败')
    const blocked = (r.report?.errors || []).filter(e => e.path.startsWith('foreshadows'))
    if (blocked.length) toast(`已保存，但体检仍有 ${blocked.length} 条伏笔阻断项`, 'info', 7000)
  })
}

async function saveAsset(refStr: string, field: string, lock: boolean) {
  const value = assetDraft.value[`${refStr}#${field}`]
  if (value === undefined) return
  await run('保存设定', async () => {
    const [kind, id] = refStr.replace('@', '').split(':')
    const zone = kind === 'character' ? '人物' : kind === 'scene' ? '场景' : '道具'
    const r = await editUnits({ project: props.project, kind: 'asset', zone, id,
      fields: { [field]: value }, lock: lock ? [field] : [] })
    if (!r.ok) throw new Error(r.err || '保存失败')
    if (r.rejected?.length) toast(`这些字段不认：${r.rejected.join('、')}`, 'info', 6000)
  })
}
</script>

<template>
  <section class="glass mb-5 p-4">
    <button class="flex w-full items-center gap-2 text-left" @click="open = !open">
      <span class="flex h-6 w-6 items-center justify-center rounded-full bg-cyan-400/15 text-xs font-black text-cyan-300">单</span>
      <h3 class="text-sm font-bold text-slate-200">全剧最小单元（第一步）</h3>
      <span v-if="anchored" class="rounded bg-emerald-400/15 px-2 py-0.5 text-2xs text-emerald-300">
        已锚定 v{{ bundle?.anchor_rev }}
      </span>
      <span v-else class="rounded bg-slate-400/15 px-2 py-0.5 text-2xs text-slate-400">未锚定</span>
      <span v-if="errs.length" class="rounded bg-rose-400/15 px-2 py-0.5 text-2xs text-rose-300">
        体检阻断 {{ errs.length }}
      </span>
      <span v-else-if="warns.length" class="rounded bg-amber-400/15 px-2 py-0.5 text-2xs text-amber-300">
        待补 {{ warns.length }}
      </span>
      <span class="text-xs-plus text-slate-500">
        先定结构与设定，再逐集写正文——分集加厚/埋线配对/人物传记/道具边界/场景限制
      </span>
      <span class="ml-auto text-xs text-slate-500">{{ open ? '▲ 收起' : '▼ 展开' }}</span>
    </button>

    <div v-if="open" class="mt-3 border-t border-line-soft pt-3">
      <div class="flex flex-wrap items-center gap-3">
        <button class="btn btn-sm" :disabled="!!busy" @click="doBuild(false)">
          {{ busy === '生成最小单元' ? '生成中…（看任务日志）' : '生成最小单元' }}
        </button>
        <button class="btn btn-sm" :disabled="!!busy" @click="doBuild(true)">生成并锚定</button>
        <button class="btn btn-sm" :disabled="!!busy || !bundle?.episodes?.length" @click="doBuild(false, 'entity')"
          title="不重跑骨架，只按缺项分批补人物传记/道具边界/场景限制">只补设定层（缺项）</button>
        <button class="btn btn-sm" :disabled="!!busy || !bundle?.episodes?.length" @click="doAnchor">锚定</button>
        <label class="text-xs text-slate-400">每段集数
          <input v-model.number="arcSize" type="number" min="1" max="20" class="input mt-1 w-16" />
        </label>
        <span class="text-2xs text-slate-500">
          生成=两次 LLM（剧情骨架+设定层），按段并批防截断；已导入成品剧本也走这条路（从正文反推单元）
        </span>
      </div>

      <div v-if="errs.length || warns.length" class="mt-3 rounded bg-slate-500/5 p-2 text-2xs">
        <div v-for="e in errs.slice(0, 12)" :key="e.path + e.code" class="text-rose-300">
          ✗ {{ e.path }}：{{ e.message }}
        </div>
        <div v-for="w in warns.slice(0, 6)" :key="w.path + w.code" class="text-amber-300">
          ! {{ w.path }}：{{ w.message }}
        </div>
      </div>

      <div class="mt-3 flex gap-2 text-xs">
        <button v-for="t in ([['arcs', `分段与规则 ${arcs.length}`], ['episodes', `分集加厚 ${episodes.length}`],
          ['threads', `埋线 ${threadsDraft.length}`], ['assets', `素材设定 ${(bundle?.bios?.length || 0) + (bundle?.scene_limits?.length || 0) + (bundle?.prop_boundaries?.length || 0)}`],
          ['index', `反查索引 ${indexRows.length}`]] as const)"
          :key="t[0]" class="rounded px-2 py-1"
          :class="tab === t[0] ? 'bg-cyan-400/15 text-cyan-200' : 'text-slate-400 hover:text-slate-200'"
          @click="tab = t[0] as typeof tab">{{ t[1] }}</button>
      </div>

      <!-- 分段与规则 -->
      <div v-if="tab === 'arcs'" class="mt-3 space-y-3 text-xs">
        <p v-if="!arcs.length" class="text-slate-500">还没有分段：先「生成最小单元」，分段是逐集扩写的批次单位与阶段目标。</p>
        <table v-else class="w-full text-2xs">
          <tbody>
            <tr v-for="a in arcs" :key="a.id" class="border-t border-line-soft align-top">
              <td class="py-1 pr-2 font-bold text-slate-300">{{ a.id }}</td>
              <td class="pr-2 text-slate-400">{{ a.ep_from }}~{{ a.ep_to }}</td>
              <td class="pr-2 text-slate-200">{{ a.goal }}</td>
              <td class="text-slate-500">释放：{{ (a.release || []).join('；') }}</td>
            </tr>
          </tbody>
        </table>
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
        <p v-if="!episodes.length" class="text-slate-500">还没有加厚分集条目。</p>
        <table v-else class="w-full">
          <thead class="text-slate-500">
            <tr><th class="py-1 text-left">集</th><th class="text-left">段/阶段目标</th><th class="text-left">节拍</th>
              <th class="text-left">待埋</th><th class="text-left">待收</th><th class="text-left">人物·场景·道具</th><th class="text-left">正文</th></tr>
          </thead>
          <tbody>
            <tr v-for="e in episodes" :key="e.id" class="border-t border-line-soft align-top">
              <td class="py-1 pr-2 font-bold text-slate-200">{{ e.id }}</td>
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
                  <input v-model="f.plant" class="input w-full" />
                </td>
                <td class="pr-2"><input v-model="f.set_in" class="input w-14" /></td>
                <td class="pr-2"><input v-model="f.pay_in" class="input w-14" /></td>
                <td class="pr-2 text-slate-400">{{ (f.refs || []).map(refName).join('、') || '—' }}</td>
                <td><input v-model="f.status" class="input w-24" placeholder="open|paid|needs_review" /></td>
              </tr>
            </tbody>
          </table>
          <button class="btn btn-sm mt-2" :disabled="!!busy" @click="saveThreads">保存埋线表（并重新体检）</button>
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
          还没有设定层：这一步产人物传记（弧光/语言风格/破绽/被逼急/称呼）、道具使用边界、场景空间限制——都不含外貌，外貌与生图字段仍在 ② 素材派生。
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
          <table v-if="assetCurrent" class="mt-2 w-full">
            <thead class="text-slate-500">
              <tr><th class="py-1 text-left">字段</th><th class="text-left">值</th><th class="w-28"></th></tr>
            </thead>
            <tbody>
              <tr v-for="f in assetFields" :key="f.key" class="border-t border-line-soft align-top">
                <td class="w-44 py-1 pr-2 text-slate-400">{{ f.label }}</td>
                <td class="pr-2">
                  <input :value="assetCurrent.fields[f.key]" class="input w-full"
                    @input="assetDraft[`${assetCurrent.ref}#${f.key}`] = ($event.target as HTMLInputElement).value" />
                </td>
                <td class="text-right">
                  <button class="btn btn-sm" :disabled="!!busy" @click="saveAsset(assetCurrent.ref, f.key, true)">存并锁定</button>
                </td>
              </tr>
            </tbody>
          </table>
          <p v-if="assetCurrent" class="mt-1 text-slate-500">
            {{ assetCurrent.ref }} · 已填 {{ filledCount(assetCurrent) }}/{{ assetFields.length }}
            ——「存并锁定」会把字段写进 locked_fields，之后任何自动生成都不许覆盖它
          </p>
        </template>
      </div>

      <!-- 反查索引：改这条设定会牵连哪几集／哪几张分镜（派生，不写回素材档案） -->
      <div v-else class="mt-3 overflow-x-auto text-2xs">
        <p v-if="!indexRows.length" class="text-slate-500">还没有引用面数据：先生成最小单元（分集的 cast/scene/key_asset 引用是它的来源）。</p>
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

      <div class="mt-3 text-2xs text-slate-500">
        命令行同功能：<code>python workbench/tools/story_units.py check|anchor|index|gaps|block {{ project }}</code>
        · 撤锚：`story_units.py unanchor`（之后扩写与 ② 提炼退回改造前行为）
      </div>
    </div>
  </section>
</template>
