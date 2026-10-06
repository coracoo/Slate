<script setup lang="ts">
// -*- coding: utf-8 -*-
/** 环境：本机运行环境检测（python/ffmpeg/blender/MCP/依赖包） + AI 厂商配置（厂商卡片 + 五能力槽编辑/拉取模型/测试/增删/整体保存）。 */
import { ref, computed, onMounted, watch, onBeforeUnmount } from 'vue'
import {
  fetchEnv, queryVendorBalance, fetchEnvConfig, saveEnvConfig, testProvider, fetchEnvModels, fetchComfyWorkflows,
  MODEL_SLOTS, type EnvInfo, type Vendor, type ModelSlot, type TestResult
} from '../api'
import { toast } from '../stores/app'
import StyledSelect from '../components/StyledSelect.vue'
import EnvironmentInstaller from '../components/EnvironmentInstaller.vue'
import ChromeUseEnvironment from '../components/ChromeUseEnvironment.vue'
import MediaGatewayEnvironment from '../components/MediaGatewayEnvironment.vue'
import RunningHubTools from '../components/RunningHubTools.vue'
import { icons } from '../components/icons'
import { visibleVendors } from '../utils/providerVisibility'

const GLOW = 'rgba(245,158,11,0.35)'

const emptyModels = (): Record<ModelSlot, string> => ({ text: '', vision: '', image: '', image_edit: '', video: '', music: '', speech: '' })

/** 豆包 Agent Plan 语音：独立 Key 可选；端点与 Resource-Id 固定在后端。 */
const SPEECH_EXTRA_KEYS = [
  { key: 'speech_api_key', label: '语音 Plan API Key（留空共用上方 Key）', secret: true, ph: '留空使用豆包 API Key' }
] as const
const extraDrafts = ref<Record<string, Record<string, string>>>({})
const hasSpeechExtra = (v: Vendor) => v.id === 'doubao'

/* ---------- 单价配置（用量计费；随「保存全部」走 saveEnvConfig 落盘） ---------- */
const pricingOpen = ref<Record<string, boolean>>({})
/** 可配价的能力槽与字段：text/vision=每百万 token 输入/输出；image/image_edit=每张；video=每秒；speech/music=每次 */
const PRICING_SLOTS: { key: ModelSlot; label: string; fields: { f: string; label: string }[] }[] = [
  { key: 'text', label: '文本', fields: [{ f: 'input', label: '输入/百万token' }, { f: 'output', label: '输出/百万token' }] },
  { key: 'vision', label: '视觉', fields: [{ f: 'input', label: '输入/百万token' }, { f: 'output', label: '输出/百万token' }] },
  { key: 'image', label: '生图', fields: [{ f: 'per_image', label: '每张' }] },
  { key: 'image_edit', label: '改图', fields: [{ f: 'per_image', label: '每张' }] },
  { key: 'video', label: '视频', fields: [{ f: 'per_second', label: '每秒' }] },
  { key: 'speech', label: '语音', fields: [{ f: 'per_call', label: '每次' }] },
  { key: 'music', label: '音乐', fields: [{ f: 'per_call', label: '每次' }] }
]
/** 只给已填模型的能力槽渲染价格输入。 */
const pricingSlotsOf = (v: Vendor) => PRICING_SLOTS.filter((s) => (v.models?.[s.key] || '').trim() !== '')
/** 读价：优先当前模型名键，回落 "*" 通配键。 */
function pricingVal(v: Vendor, kind: ModelSlot, field: string): string {
  const table = (v.pricing as Record<string, Record<string, Record<string, number>>> | undefined)?.[kind]
  if (!table) return ''
  const model = (v.models?.[kind] || '').trim()
  const val = (model && table[model]?.[field]) ?? table['*']?.[field]
  return val === undefined || val === null ? '' : String(val)
}
/** 写价：以当前槽位模型名为键；清空即删除该字段（服务端空 pricing 不清旧值）。 */
function setPricingVal(v: Vendor, kind: ModelSlot, field: string, raw: string) {
  const model = (v.models?.[kind] || '').trim() || '*'
  const pricing = (v.pricing ??= {}) as unknown as Record<string, Record<string, Record<string, number>>>
  const table = (pricing[kind] ??= {})
  const rate = (table[model] ??= {})
  const n = parseFloat(raw)
  if (raw.trim() === '' || Number.isNaN(n)) {
    delete rate[field]
    if (!Object.keys(rate).length) delete table[model]
  } else {
    rate[field] = n
  }
}

/* ---------- 本机环境 ---------- */
const env = ref<EnvInfo | null>(null)
const modal = ref('')
const balanceBusy = ref('')
const balanceText = ref<Record<string, string>>({})
async function checkBalance(id: string) {
  if (balanceBusy.value) return
  balanceBusy.value = id
  try {
    const r = await queryVendorBalance(id)
    balanceText.value[id] = r.text || '无数据'
  } catch (e) {
    balanceText.value[id] = e instanceof Error ? e.message : '查询失败'
  } finally { balanceBusy.value = '' }
}
const detecting = ref(false)

async function detect() {
  detecting.value = true
  try {
    env.value = await fetchEnv()
  } catch {
    toast('环境检测失败（后端可能未就绪）', 'err')
  } finally {
    detecting.value = false
  }
}

const pkgList = computed(() => Object.entries(env.value?.packages || {}))

/* ---------- 厂商 ---------- */
const vendors = ref<Vendor[]>([])
const displayedVendors = computed(() => visibleVendors(vendors.value))
function applyDefaultOrder() {
  // 默认顺序：启用在前 + id 首字母。用户拖拽过的顺序存 localStorage（一次性记录），刷新不再重排。
  if (localStorage.getItem('wb.env.vendorOrder')) return
  const ids = vendors.value.map(v => v.id).join(',')
  vendors.value.sort((a, b) =>
    Number(b.enabled ?? false) - Number(a.enabled ?? false) || a.id.localeCompare(b.id))
  if (vendors.value.map(v => v.id).join(',') !== ids) scheduleAutoSave()
}
let dragVendor = ''
function onDragStart(id: string) { dragVendor = id }
function onDropOn(id: string) {
  if (!dragVendor || dragVendor === id) return
  const from = vendors.value.findIndex(v => v.id === dragVendor)
  const to = vendors.value.findIndex(v => v.id === id)
  if (from < 0 || to < 0) return
  const [moved] = vendors.value.splice(from, 1)
  vendors.value.splice(to, 0, moved)
  localStorage.setItem('wb.env.vendorOrder', vendors.value.map(v => v.id).join(','))
}
const loadingConfig = ref(true)   // 初始即加载态：骨架先占位，数据到达原位填充
const expanded = ref<Set<string>>(new Set())
const keyDrafts = ref<Record<string, string>>({})
const testing = ref<Record<string, boolean>>({})
const testResults = ref<Record<string, { ok: boolean; text: string }>>({})
const saving = ref(false)

/** ComfyUI 生图 API 工作流清单；视频 H3 使用内置工作流，不从这里选择。 */
const comfyWorkflows = ref<{ path: string; name: string; refs?: number[]; has_negative?: boolean; has_save_image?: boolean; error?: string }[]>([])
const loadingComfyWorkflows = ref(false)
const comfyWorkflowMsg = ref('')

async function loadComfyWorkflows() {
  loadingComfyWorkflows.value = true
  try {
    const r = await fetchComfyWorkflows()
    comfyWorkflows.value = r.workflows || []
    comfyWorkflowMsg.value = r.err || ''
  } catch (e) {
    comfyWorkflowMsg.value = e instanceof Error ? e.message : '工作流目录读取失败'
  } finally {
    loadingComfyWorkflows.value = false
  }
}
/** 新增厂商弹窗 */
const addVisible = ref(false)
const addForm = ref({ id: '', label: '', base_url: '' })

/** 每卡原始快照（id → 规范化 JSON），用于「未保存」徽标；新增卡无快照 = 未保存。 */
const pristineMap = ref<Record<string, string>>({})
const pristineListSig = ref('')

const dirty = computed(() => JSON.stringify(canonical(vendors.value)) !== pristineListSig.value)
const autoSaveNote = ref('')
let autoSaveTimer: ReturnType<typeof setTimeout> | undefined
function scheduleAutoSave() {
  if (autoSaveTimer) clearTimeout(autoSaveTimer)
  if (loadingConfig.value || !dirty.value) return
  autoSaveNote.value = '待自动保存…'
  autoSaveTimer = setTimeout(autoSave, 800)
}
async function autoSave() {
  if (loadingConfig.value || !dirty.value) return
  if (saving.value) { scheduleAutoSave(); return }
  const signature = JSON.stringify(canonical(vendors.value))
  const payload = JSON.parse(signature) as Vendor[]
  saving.value = true
  autoSaveNote.value = '保存中…'
  let succeeded = false
  try {
    await saveEnvConfig(payload)
    pristineListSig.value = signature
    pristineMap.value = Object.fromEntries(payload.map(v => [v.id, JSON.stringify(v)]))
    autoSaveNote.value = '已自动保存'
    succeeded = true
  } catch (e) { autoSaveNote.value = `保存失败：${e instanceof Error ? e.message : '请重试'}` }
  finally { saving.value = false }
  if (succeeded && dirty.value) scheduleAutoSave()
}
watch(() => JSON.stringify(canonical(vendors.value)), scheduleAutoSave)
onBeforeUnmount(() => { if (autoSaveTimer) clearTimeout(autoSaveTimer); void autoSave() })

/** 比较/提交用规范化：api_key 以草稿为准（空 = 保留旧值）；models 五键补全；
 *  extra 密钥类取草稿（空 = 保留，绝不回传掩码），非密钥类取卡片当前值（可清空）。 */
function canonical(list: Vendor[]): unknown[] {
  return list.map((v) => ({
    ...v,
    api_key: keyDrafts.value[v.id] ?? '',
    base_url: v.base_url ?? '',
    note: v.note ?? '',
    models: { ...emptyModels(), ...(v.models || {}) },
    extra: hasSpeechExtra(v)
      ? { ...(v.extra || {}), speech_api_key: extraDrafts.value[v.id]?.speech_api_key ?? '' }
      : v.extra
  }))
}

function cardCanonical(v: Vendor): unknown {
  return canonical([v])[0]
}

/** 单卡是否有未保存改动。 */
function cardDirty(v: Vendor): boolean {
  return pristineMap.value[v.id] !== JSON.stringify(cardCanonical(v))
}

/** 卡片当前草稿：测试/拉取用未落盘值；api_key 没动过传空串，后端回落已落盘 key。 */
function draftOf(v: Vendor) {
  return {
    base_url: v.base_url ?? '',
    api_key: keyDrafts.value[v.id] ?? '',
    models: { ...emptyModels(), ...(v.models || {}) }, endpoints: v.endpoints || {}, extra: v.extra || {}
  }
}

async function loadConfig() {
  loadingConfig.value = true
  try {
    const r = await fetchEnvConfig({ includeHidden: true })
    vendors.value = (r.vendors || []).map((v) => ({
      ...v,
      models: { ...emptyModels(), ...(v.models || {}) },
      extra: { ...(v.extra || {}) }
    }))
    applyDefaultOrder()   // 快照前应用默认顺序：启用在前+字母序；用户拖拽过则尊重存储顺序
    keyDrafts.value = {}
    extraDrafts.value = {}
    // 快照须在 keyDrafts 清空后取（草稿为空串 ≡ 保留旧 key）
    pristineListSig.value = JSON.stringify(canonical(vendors.value))
    pristineMap.value = Object.fromEntries(vendors.value.map((v) => [v.id, JSON.stringify(cardCanonical(v))]))
  } catch {
    toast('厂商配置加载失败（后端可能未就绪）', 'err')
  } finally {
    loadingConfig.value = false
  }
}

function toggleExpand(id: string) {
  // 手风琴：同一时刻只有一张展开（点新卡自动收起其它），再点自己收拢
  expanded.value = new Set(expanded.value.has(id) ? [] : [id])
}

function openAdd() {
  addForm.value = { id: '', label: '', base_url: '' }
  addVisible.value = true
}

function submitAdd() {
  const id = addForm.value.id.trim()
  if (!id) {
    toast('请填写厂商 id', 'err')
    return
  }
  if (vendors.value.some((v) => v.id === id)) {
    toast('厂商 id 已存在', 'err')
    return
  }
  vendors.value.unshift({
    id,
    label: addForm.value.label.trim() || id,
    base_url: addForm.value.base_url.trim(),
    api_key: '',
    enabled: true,
    models: emptyModels(),
    extra: id === 'local-comfyui' ? { workflow_path: '', image_edit_workflow_path: '' } : undefined
  })
  keyDrafts.value[id] = ''
  addVisible.value = false
  expanded.value = new Set([id])
  toast('已新增厂商（保存后生效）', 'ok')
}

function removeVendor(v: Vendor) {
  if (!confirm(`确定删除厂商「${v.label || v.id}」？（保存后生效）`)) return
  vendors.value = vendors.value.filter((x) => x.id !== v.id)
  delete keyDrafts.value[v.id]
  delete extraDrafts.value[v.id]
}

async function submitAll(fromCard: boolean) {
  saving.value = true
  try {
    await saveEnvConfig(canonical(vendors.value) as Vendor[])
    toast(fromCard ? '已保存（后端为全量提交，其他卡的改动一并落盘）' : '厂商配置已保存', 'ok')
    await loadConfig()
  } catch (e) {
    toast(e instanceof Error ? e.message : '保存失败', 'err')
  } finally {
    saving.value = false
  }
}

const saveAll = () => submitAll(false)
/** 卡片「保存」：语义是保存此厂商，实现走全量提交（接口无单卡保存）。 */
const saveCard = () => submitAll(true)

/** 测试用 kind：卡片当前已填模型中 vision→text→image→video→music 第一个非空槽。 */
function primaryKind(v: Vendor): ModelSlot | undefined {
  const order: ModelSlot[] = ['vision', 'text', 'image', 'image_edit', 'video', 'music', 'speech']
  return order.find((k) => (v.models?.[k] || '').trim() !== '')
}

async function test(v: Vendor) {
  testing.value[v.id] = true
  testResults.value[v.id] = { ok: false, text: '测试中（用卡片当前草稿值）…' }
  const t0 = Date.now()
  try {
    const r: TestResult = await testProvider({ id: v.id, kind: primaryKind(v), draft: draftOf(v) })
    if (r.ok) {
      const ms = r.latency_ms ?? Date.now() - t0
      testResults.value[v.id] = { ok: true, text: r.note ? `检查通过 · ${r.note}` : `连接成功 · ${ms}ms` }
    } else {
      testResults.value[v.id] = { ok: false, text: r.err || r.note || '连接失败' }
    }
  } catch (e) {
    testResults.value[v.id] = { ok: false, text: e instanceof Error ? e.message : '测试请求失败' }
  } finally {
    testing.value[v.id] = false
  }
}

/* ---------- 拉取模型（每能力槽一个按钮，结果全卡共享 datalist） ---------- */
const modelsMap = ref<Record<string, string[]>>({})
const fetchingModels = ref<Record<string, boolean>>({})
const modelMsgs = ref<Record<string, { ok: boolean; text: string }>>({})

async function fetchModels(v: Vendor, slot: ModelSlot) {
  fetchingModels.value[v.id] = true
  modelMsgs.value[v.id] = { ok: true, text: `正在用草稿值拉取「${MODEL_SLOTS.find((s) => s.key === slot)?.label}」模型列表…` }
  try {
    const d = draftOf(v)
    const r = await fetchEnvModels({ id: v.id, kind: slot, draft: { base_url: d.base_url, api_key: d.api_key, extra: d.extra } })
    if (r.ok && r.models?.length) {
      modelsMap.value[v.id] = r.models
      modelMsgs.value[v.id] = { ok: true, text: `已拉取 ${r.models.length} 个模型` }
      toast(`已拉取 ${r.models.length} 个模型（未保存）`, 'ok')
    } else {
      modelMsgs.value[v.id] = { ok: false, text: r.err || '未获取到模型列表，可手动填写' }
    }
  } catch (e) {
    modelMsgs.value[v.id] = { ok: false, text: e instanceof Error ? e.message : '拉取失败，可手动填写' }
  } finally {
    fetchingModels.value[v.id] = false
  }
}

onMounted(() => {
  detect()
  loadConfig()
  loadComfyWorkflows()
})
</script>

<template>
  <div class="page">
    <header class="mb-6 flex flex-wrap items-center gap-3">
      <div>
        <h1 class="grad-text text-2xl font-black">④ 环境检查</h1>
        <p class="mt-1 text-sm text-slate-400">检查本机依赖，配置生成模型与公网素材出口。</p>
      </div>
      <div class="ml-auto flex flex-wrap gap-2">
        <button class="btn btn-sm" title="安装/补全本机依赖（Python 基础锁文件、深度推理、image-use）" @click="modal = 'installer'">补全缺失环境</button>
        <button class="btn btn-ghost btn-sm" title="云端厂商读取本地音视频时的限时公网链接（signed URL）配置" @click="modal = 'gateway'">填写公网素材出口</button>
        <button class="btn btn-ghost btn-sm" title="ChatGPT 网页生图通道的浏览器自动化组件安装" @click="modal = 'chromeuse'">部署 chrome-use</button>
      </div>
    </header>

    <!-- 三个功能各独立弹窗：互不占版面，打开才渲染 -->
    <div v-if="modal" class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" @click.self="modal = ''">
      <div class="glass max-h-[85vh] w-full max-w-2xl overflow-y-auto p-5">
        <div class="mb-3 flex items-center justify-between">
          <h2 class="text-base font-bold text-slate-100">{{ modal === 'installer' ? '补全缺失环境' : modal === 'gateway' ? '公网素材出口' : '部署 chrome-use' }}</h2>
          <button class="text-slate-400 hover:text-slate-200" @click="modal = ''">✕</button>
        </div>
        <EnvironmentInstaller v-if="modal === 'installer'" @installed="detect" />
        <MediaGatewayEnvironment v-if="modal === 'gateway'" />
        <ChromeUseEnvironment v-if="modal === 'chromeuse'" />
      </div>
    </div>

    <!-- 上半：本机环境 -->
    <section class="glass mb-5 p-5" :style="{ '--glow': GLOW }">
      <div class="mb-4 flex items-center gap-2">
        <h3 class="text-xs font-bold text-slate-500">本机环境</h3>
        <div class="flex-1"></div>
        <button class="btn btn-ghost btn-sm" :disabled="detecting" @click="detect">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path :d="icons.refresh" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
          {{ detecting ? '检测中…' : '重新检测' }}
        </button>
      </div>

      <div v-if="!env" aria-busy="true">
        <!-- 占位骨架：与检测完成后同结构同高（四宫格 + 一行依赖 chips），数据到达原位填充不跳动 -->
        <div class="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <div v-for="n in 4" :key="n" class="min-h-[72px] animate-pulse rounded-xl border border-line-soft bg-black/25 p-3">
            <div class="h-2 w-14 rounded bg-slate-700/60"></div>
            <div class="mt-2 h-4 w-24 rounded bg-slate-700/40"></div>
          </div>
        </div>
        <div class="mt-4 flex flex-wrap items-center gap-1.5">
          <div class="h-3 w-16 animate-pulse rounded bg-slate-700/50"></div>
          <div v-for="n in 7" :key="n" class="h-5 w-20 animate-pulse rounded-full bg-slate-700/35"></div>
        </div>
      </div>

      <template v-else>
        <div class="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <!-- Python -->
          <div class="min-h-[72px] rounded-xl border border-line-soft bg-black/25 p-3">
            <div class="mb-1 text-2xs tracking-wider text-slate-500">PYTHON</div>
            <div class="flex items-center gap-2">
              <span class="h-2 w-2 rounded-full" :class="env.python ? 'bg-emerald-400' : 'bg-rose-400'"></span>
              <span class="text-sm font-bold text-slate-100">{{ env.python || '未找到' }}</span>
            </div>
          </div>
          <!-- ffmpeg -->
          <div class="min-h-[72px] rounded-xl border border-line-soft bg-black/25 p-3">
            <div class="mb-1 text-2xs tracking-wider text-slate-500">FFMPEG</div>
            <div class="flex items-center gap-2">
              <span class="h-2 w-2 rounded-full" :class="env.ffmpeg ? 'bg-emerald-400' : 'bg-rose-400'"></span>
              <span class="truncate text-xs text-slate-300" :title="env.ffmpeg || ''">{{ env.ffmpeg || '未找到' }}</span>
            </div>
          </div>
          <!-- Blender -->
          <div class="min-h-[72px] rounded-xl border border-line-soft bg-black/25 p-3">
            <div class="mb-1 text-2xs tracking-wider text-slate-500">BLENDER</div>
            <div class="flex items-center gap-2">
              <span class="h-2 w-2 rounded-full" :class="env.blender ? 'bg-emerald-400' : 'bg-rose-400'"></span>
              <span class="truncate text-xs text-slate-300" :title="env.blender || ''">{{ env.blender || '未找到' }}</span>
            </div>
          </div>
          <!-- Blender MCP -->
          <div class="min-h-[72px] rounded-xl border border-line-soft bg-black/25 p-3">
            <div class="mb-1 text-2xs tracking-wider text-slate-500">BLENDER MCP</div>
            <div class="flex items-center gap-2">
              <span class="h-2 w-2 rounded-full" :class="env.mcp ? 'bg-emerald-400 pulse-dot' : 'bg-rose-400'"></span>
              <span class="text-sm font-bold" :class="env.mcp ? 'text-emerald-300' : 'text-rose-300'">
                {{ env.mcp ? '在线' : '离线' }}
              </span>
              <span class="text-2xs text-slate-500">127.0.0.1:9876</span>
            </div>
          </div>
        </div>

        <!-- 依赖包：单行 chips（紧凑，不再用表） -->
        <div class="mt-4 flex flex-wrap items-center gap-1.5">
          <span class="text-2xs font-bold text-slate-500">PYTHON 依赖</span>
          <span
            v-for="[name, ver] in pkgList" :key="name"
            class="rounded-full px-2 py-0.5 font-mono text-2xs"
            :class="ver !== '缺失' ? 'bg-emerald-400/10 text-emerald-300' : 'bg-rose-400/10 text-rose-300'"
            :title="ver"
          >{{ name }} {{ ver !== '缺失' ? '' : '✗' }}</span>
          <span v-if="!pkgList.length" class="text-2xs text-slate-500">暂无依赖信息</span>
        </div>
      </template>
    </section>

    <!-- 下半：AI 厂商 -->
    <section class="glass p-5" :style="{ '--glow': GLOW }">
      <div class="mb-4 flex flex-wrap items-center gap-2">
        <h3 class="text-xs font-bold text-slate-500">AI 厂商</h3>
        <span v-if="dirty" class="pop-in rounded-full bg-amber-400/15 px-2 py-0.5 text-2xs font-bold text-amber-300">
          ● 有未保存修改
        </span>
        <div class="flex-1"></div>
        <button class="btn btn-ghost btn-sm" @click="openAdd">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
            <path :d="icons.plus" stroke-linecap="round" />
          </svg>
          新增厂商
        </button>
        <button class="btn" :disabled="!dirty || saving" @click="saveAll">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path :d="icons.save" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
          {{ saving ? '保存中…' : '保存全部' }}
        </button>
      </div>

      <!-- 加载骨架：与真实卡片区同网格同卡高，数据到达后原位填充不跳动 -->
      <div v-if="loadingConfig" class="grid grid-cols-1 items-start gap-3 lg:grid-cols-2" aria-busy="true">
        <div v-for="n in 6" :key="n" class="glass p-3">
          <div class="flex items-center gap-2.5">
            <div class="h-8 w-8 shrink-0 animate-pulse rounded-lg bg-slate-700/50"></div>
            <div class="flex-1">
              <div class="h-3.5 w-28 animate-pulse rounded bg-slate-700/50"></div>
              <div class="mt-1.5 h-2.5 w-44 animate-pulse rounded bg-slate-700/35"></div>
            </div>
            <div class="h-4 w-14 animate-pulse rounded-full bg-slate-700/35"></div>
          </div>
        </div>
      </div>

      <div v-else-if="!vendors.length" class="p-8 text-center">
        <p class="text-sm text-slate-300">还没有配置任何厂商</p>
        <p class="mt-2 text-xs text-slate-500">修改后自动保存；未启用也可测试和拉取模型，启用只控制生成时是否可选。{{ autoSaveNote }}</p>
      </div>

      <div v-else class="grid grid-cols-1 items-start gap-3 lg:grid-cols-2">
        <article
          v-for="v in displayedVendors"
          :key="v.id"
          class="glass glass-hover relative p-3"
          :class="expanded.has(v.id) ? 'z-20 ring-1 ring-amber-400/50' : ''"
          :style="{ '--glow': GLOW }"
          draggable="true"
          @dragstart="onDragStart(v.id)"
          @dragover.prevent
          @drop="onDropOn(v.id)"
        >
          <!-- 卡片头 -->
          <div class="flex cursor-pointer items-center gap-2.5" @click="toggleExpand(v.id)">
            <span
              class="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-slate-950"
              style="background: linear-gradient(130deg, #f59e0b, #fbbf24)"
            >
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path :d="icons.server" stroke-linecap="round" stroke-linejoin="round" />
              </svg>
            </span>
            <div class="min-w-0 flex-1">
              <div class="truncate text-sm font-bold text-slate-100">{{ v.label || v.id }}</div>
              <div class="truncate text-2xs text-slate-500">{{ v.base_url || '未设置 base_url' }}</div>
              <div v-if="v.note" class="mt-0.5 line-clamp-2 text-2xs leading-relaxed text-amber-200/70">{{ v.note }}</div>
            </div>
            <!-- 已配置能力槽小圆点 -->
            <div class="hidden shrink-0 gap-1 sm:flex" :title="'已配置能力槽'">
              <span
                v-for="s in MODEL_SLOTS"
                :key="s.key"
                class="h-2 w-2 rounded-full"
                :style="{ background: v.models?.[s.key] ? s.color : 'rgba(148,163,184,0.2)' }"
              ></span>
            </div>
            <span class="shrink-0 rounded-full px-2 py-0.5 text-2xs font-bold" :class="v.enabled?'bg-emerald-500/20 text-emerald-300':'bg-slate-500/15 text-slate-400'">{{ v.enabled?'● 已启用':'已停用' }}</span>
            <span
              v-if="cardDirty(v)"
              class="pop-in shrink-0 rounded-full bg-amber-400/20 px-2 py-0.5 text-2xs font-bold text-amber-300"
              title="此卡有未落盘的改动"
            >未保存</span>
            <svg
              width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#94a3b8" stroke-width="2"
              class="shrink-0 transition-transform duration-200"
              :class="{ 'rotate-90': expanded.has(v.id) }"
            >
              <path :d="icons.chevronR" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
          </div>

          <!-- 展开编辑：浮层不占位（撑高卡片会把 grid 行高与同行邻卡拉出大片空白），向下覆盖后续内容 -->
          <div v-if="expanded.has(v.id)" class="absolute inset-x-0 top-full z-10 mt-1 space-y-2 rounded-xl border border-line bg-[#0b1220] p-3 shadow-2xl shadow-black/50">
            <div class="grid grid-cols-2 gap-2">
              <label class="text-2xs text-slate-500">
                厂商 ID
                <input :value="v.id" class="input mt-0.5 font-mono opacity-60" disabled />
              </label>
              <label class="text-2xs text-slate-500">
                名称
                <input v-model="v.label" class="input mt-0.5" placeholder="自定义厂商名称" />
              </label>
              <label class="col-span-2 text-2xs text-slate-500">
                Base URL
                <input v-model="v.base_url" class="input mt-0.5 font-mono" placeholder="https://api.example.com" />
              </label>
              <div v-if="v.id === 'local-comfyui'" class="col-span-2 rounded-xl border border-cyan-400/15 bg-cyan-400/5 p-2.5 text-2xs">
                <div class="flex items-center gap-2 text-cyan-200">
                  <span class="font-bold">ComfyUI 工作流</span>
                  <span class="text-2xs text-slate-500">只影响“生图参考图”</span>
                  <button class="btn btn-ghost ml-auto !px-2 !py-1 text-2xs" :disabled="loadingComfyWorkflows" @click.stop="loadComfyWorkflows">
                    {{ loadingComfyWorkflows ? '刷新中…' : '刷新列表' }}
                  </button>
                </div>
                <label class="mt-2 block text-2xs text-slate-400">文生图工作流（按模型匹配）
                  <select v-model="v.extra!.workflow_path" class="input mt-1 w-full font-mono text-2xs">
                    <option value="">自动匹配模型：Z-Image / Qwen Image 2.1</option>
                    <option v-for="wf in comfyWorkflows" :key="'gen-'+wf.path" :value="wf.path">
                      {{ wf.path }} · {{ wf.refs?.length || 0 }} 个参考位{{ wf.error ? ' · 文件有误' : '' }}
                    </option>
                  </select>
                  <input v-model="v.extra!.workflow_path" class="input mt-1 font-mono text-2xs"
                    placeholder="也可手动填 workbench/workflows 下的相对路径" />
                </label>
                <label class="mt-2 block text-2xs text-slate-400">参考图工作流（按模型匹配）
                  <select v-model="v.extra!.image_edit_workflow_path" class="input mt-1 w-full font-mono text-2xs">
                    <option value="">自动匹配模型：Qwen Image 2.1 / Qwen Edit</option>
                    <option v-for="wf in comfyWorkflows" :key="'edit-'+wf.path" :value="wf.path">
                      {{ wf.path }} · {{ wf.refs?.length || 0 }} 个参考位{{ wf.error ? ' · 文件有误' : '' }}
                    </option>
                  </select>
                  <input v-model="v.extra!.image_edit_workflow_path" class="input mt-1 font-mono text-2xs"
                    placeholder="Qwen 改图 API JSON 相对路径（需 LoadImage.image={{ref1}}）" />
                </label>
                <p class="mt-1.5 leading-relaxed text-slate-400">
                  操作：在 ComfyUI 加载工作流 → 菜单“Save (API Format)”导出 JSON → 放入
                  <span class="font-mono text-cyan-300">workbench/workflows</span> → 点“刷新列表”并选择。
                  工作流中需要把参考图节点的 <span class="font-mono text-cyan-300">LoadImage.image</span> 填成
                  <span class="font-mono text-cyan-300">&#123;&#123;ref1&#125;&#125;</span>、<span class="font-mono text-cyan-300">&#123;&#123;ref2&#125;&#125;</span>…；负面词输入位用
                  <span class="font-mono text-cyan-300">&#123;&#123;negative&#125;&#125;</span>。
                </p>
                <p class="mt-1 leading-relaxed text-amber-300/80">
                  视频槽走内置 MiniMax H3 工作流，最多 3 张参考图，不读取这里的生图工作流；需要 SD1.5/SDXL 视频时，必须另外导出对应的视频 API 工作流并接入专用适配器。
                </p>
                <p v-if="comfyWorkflowMsg" class="mt-1 text-rose-300">{{ comfyWorkflowMsg }}</p>
                <p v-if="!loadingComfyWorkflows && !comfyWorkflows.length" class="mt-1 text-amber-300/80">当前目录还没有 API 工作流 JSON；系统先识别所选模型，再按参考图选择兼容工作流；未知模型需提供专用 API 工作流。自定义工作流仅用于覆盖默认链路。</p>
              </div>
              <label v-if="v.id !== 'local-comfyui' && v.id !== 'chatgpt-queue'" class="col-span-2 text-2xs text-slate-500">
                API Key
                <input
                  v-model="keyDrafts[v.id]"
                  type="password"
                  class="input mt-0.5 font-mono"
                  :placeholder="v.api_key ? `已保存：${v.api_key}（留空保留）` : 'sk-…'"
                />
              </label>
            </div>

            <p v-if="v.id === 'chatgpt-queue'" class="text-xs text-sky-300"><a href="https://github.com/leeguooooo/image-use" target="_blank" rel="noopener" title="网页生图由 image-use 提供；工作台负责排队与导入。">服务由 image-use 提供 ↗</a> · 单张串行。</p>
              <label v-if="v.models.video && v.extra" class="block text-xs text-slate-400">视频兼容型号（部署别名可选）
                <input v-model="v.extra.video_profile" class="input mt-1" placeholder="留空自动识别；仅填本厂商已接入的标准型号" />
                <span class="text-2xs">部署 ID / 派生版须由服务商确认兼容；不更改计费地址。未知协议需要单独适配。</span>
              </label>
              <label v-if="v.id === 'minimax'" class="block text-xs text-slate-400">语音 voice_id
              <input class="input mt-1" :value="v.extra?.voice_id || ''" placeholder="从 MiniMax 音色列表选取 voice_id" @input="(v.extra ??= {}).voice_id = ($event.target as HTMLInputElement).value" />
            </label>
            <a v-if="v.documentation_url" :href="v.documentation_url" target="_blank" rel="noopener" class="text-xs text-sky-300">官方接口文档 ↗</a>
            <!-- 豆包 Agent Plan 语音配置 -->
            <div v-if="hasSpeechExtra(v)" class="rounded-xl border border-amber-400/15 bg-amber-400/5 p-2.5">
              <div class="mb-1.5 text-2xs font-bold text-amber-300">豆包 Agent Plan 语音</div>
              <p class="text-2xs leading-relaxed text-slate-400">
                TTS：/api/v3/plan/tts/unidirectional · Resource-Id seed-tts-2.0<br />
                ASR：/api/v3/plan/sauc/bigmodel_nostream · Resource-Id volc.seedasr.sauc.duration
              </p>
              <label v-for="f in SPEECH_EXTRA_KEYS" :key="f.key" class="mt-2 block text-2xs text-slate-500">
                {{ f.label }}
                <input :value="extraDrafts[v.id]?.[f.key] ?? ''" type="password" class="input mt-0.5 font-mono"
                  :placeholder="v.extra?.[f.key] ? `已保存（留空保留）` : f.ph"
                  @input="(extraDrafts[v.id] ??= {})[f.key] = ($event.target as HTMLInputElement).value" />
              </label>
              <p class="mt-1.5 text-2xs text-slate-500">语音模型固定使用 Plan Resource-Id，不通过 Auto 或模型列表切换。</p>
            </div>

            <!-- RH 专属区（标准模型 API 前端已下线，10-06 定版）：AI 应用通道 + 工作流槽位 + LLM -->
            <template v-if="v.id === 'runninghub'">
              <div class="mt-2 rounded-xl border border-cyan-400/25 bg-cyan-400/5 p-3">
                <div class="mb-1 flex flex-wrap items-center gap-2">
                  <b class="text-xs-plus font-bold text-cyan-200">AI 应用通道</b>
                  <span class="rounded bg-cyan-400/20 px-1.5 py-0.5 text-2xs font-bold text-cyan-300">RH 唯一生成通道 · v2 工作流</span>
                  <span class="text-2xs text-slate-500">webappId → 拉取节点自动生成表单；提交按 RH 应用计费</span>
                </div>
                <RunningHubTools :saved="!cardDirty(v)" />
              </div>
              <div class="mt-2 space-y-1.5">
                <div class="text-2xs text-slate-500">工作流槽位（③/⑦ 生成消费；填 <code class="text-cyan-200">workflow:&lt;webappId&gt;</code>，留空 = 未配置）</div>
                <div class="flex items-center gap-1.5">
                  <span class="w-14 shrink-0 rounded-md px-1.5 py-1 text-center text-2xs font-bold text-pink-300 bg-pink-400/10">生图</span>
                  <input v-model="v.models.image" class="input flex-1 font-mono text-xs" placeholder="workflow:应用页URL长数字" />
                </div>
                <div class="flex items-center gap-1.5">
                  <span class="w-14 shrink-0 rounded-md px-1.5 py-1 text-center text-2xs font-bold text-violet-300 bg-violet-400/10">改图</span>
                  <input v-model="v.models.image_edit" class="input flex-1 font-mono text-xs" placeholder="workflow:应用页URL长数字" />
                </div>
                <div class="flex items-center gap-1.5">
                  <span class="w-14 shrink-0 rounded-md px-1.5 py-1 text-center text-2xs font-bold text-cyan-300 bg-cyan-400/10">生视频</span>
                  <input v-model="v.models.video" class="input flex-1 font-mono text-xs" placeholder="workflow:应用页URL长数字" />
                </div>
              </div>
              <label class="mt-2 block text-xs text-slate-400">文本/视觉 LLM Base URL
                <input v-model="v.extra!.llm_base_url" class="input mt-1 font-mono" placeholder="https://llm.runninghub.ai/v1" />
              </label>
              <details class="mt-2 text-xs text-slate-400">
                <summary class="cursor-pointer">工作流节点映射（api_parameters）</summary>
                <label class="mt-2 block">按槽位填 JSON（节点 nodeId/fieldName 从上方「AI 应用通道」拉取后填入）
                  <textarea v-model="v.extra!.api_parameters" class="textarea mt-1 min-h-24 w-full font-mono" placeholder='{"image":{"prompt_node":{"nodeId":"39","fieldName":"value"},"image_nodes":[{"nodeId":"52","fieldName":"image"}]},"video":{"instance_type":"plus"}}' />
                </label>
                <div class="mt-2 rounded-lg border border-cyan-400/15 bg-cyan-400/5 p-2.5 text-2xs leading-relaxed text-slate-400">
                  <b class="text-cyan-200">组装规则</b>——生成链把提示词/参考图自动填进节点：
                  <code class="text-cyan-200">prompt_node</code>（提示词注入）、<code class="text-cyan-200">image_nodes</code>（参考图节点列表，本地文件自动上传）、
                  <code class="text-cyan-200">extra_fields</code>（开关/数值/COMBO 等任意节点参数）、
                  <code class="text-cyan-200">instance_type</code>（default 24G / plus 48G / ultra 84G 显存）。
                </div>
              </details>
            </template>
            <!-- 六能力槽：生图与改图分开，避免模型/工作流串用（RH 已下线标准 API，走上方工作流槽位） -->
            <div v-if="v.id !== 'runninghub'">
              <div class="mb-1 text-2xs text-slate-500">能力槽（生图与改图分开填写；留空 = 未配置）</div>
              <div class="space-y-1.5">
                <div v-for="s in MODEL_SLOTS" :key="s.key" class="flex items-center gap-1.5">
                  <span
                    class="w-14 shrink-0 rounded-md px-1.5 py-1 text-center text-2xs font-bold"
                    :style="{ color: s.color, background: `${s.color}1a` }"
                  >{{ s.label }}</span>
                  <StyledSelect
                    v-model="v.models[s.key]"
                    class="flex-1"
                    :class="{ 'opacity-50': !v.models[s.key] }"
                    :options="modelsMap[v.id] || []"
                    placeholder="未配置"
                  />
                  <button
                    class="btn btn-ghost shrink-0 !px-2 !py-1.5 text-2xs"
                    :disabled="fetchingModels[v.id]"
                    title="用卡片当前草稿值拉取该厂商可用模型列表"
                    @click="fetchModels(v, s.key)"
                  >
                    <svg v-if="fetchingModels[v.id]" class="animate-spin" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                      <path d="M12 3a9 9 0 1 0 9 9" stroke-linecap="round" />
                    </svg>
                    <svg v-else width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                      <path :d="icons.download" stroke-linecap="round" stroke-linejoin="round" />
                    </svg>
                    拉取
                  </button>
                </div>
              </div>
              <div
                v-if="modelMsgs[v.id]"
                class="mt-1.5 rounded-lg px-2.5 py-1.5 text-xs-plus font-semibold"
                :class="modelMsgs[v.id].ok ? 'bg-emerald-400/10 text-emerald-300' : 'bg-rose-400/10 text-rose-300'"
              >
                {{ modelMsgs[v.id].text }}
              </div>
              <p class="mt-1 text-2xs text-slate-500">下拉选择或手动填写模型后，需点卡片「保存」或顶部「保存全部」才会落盘</p>
              <p v-if="v.id==='doubao'" class="mt-2 rounded-md bg-amber-500/10 p-2 text-xs text-amber-200">视频模型必须属于当前 Agent Plan 套餐。旧 Seedance 1.5 Pro 配置已被接口拒绝，请按控制台填写可用模型 ID；套餐模型列表接口不可用时可直接手填，不会自动切到按量计费接口。</p>
            </div>

            <!-- 单价配置（用量计费）：按已配置模型的能力槽渲染价格输入 -->
            <div class="rounded-xl border border-line-soft bg-black/20 p-2.5">
              <button class="flex w-full items-center gap-1.5 text-2xs font-bold text-slate-400" @click="pricingOpen[v.id] = !pricingOpen[v.id]">
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
                  class="transition-transform duration-200" :class="{ 'rotate-90': pricingOpen[v.id] }">
                  <path :d="icons.chevronR" stroke-linecap="round" stroke-linejoin="round" />
                </svg>
                单价配置（用量计费）
                <span class="ml-auto font-normal text-slate-600">{{ pricingSlotsOf(v).length ? '留空 = 不计费仍记调用' : '先填模型再配价' }}</span>
              </button>
              <div v-if="pricingOpen[v.id]" class="mt-2 space-y-1.5">
                <label class="block text-2xs text-slate-500">币种
                  <input :value="v.pricing?.currency || 'CNY'" class="input mt-0.5 w-24"
                    @input="(v.pricing ??= {}).currency = ($event.target as HTMLInputElement).value || 'CNY'" />
                </label>
                <div v-for="s in pricingSlotsOf(v)" :key="s.key" class="flex items-start gap-1.5">
                  <span class="mt-1.5 w-14 shrink-0 rounded-md bg-white/5 px-1.5 py-1 text-center text-2xs font-bold text-slate-300">{{ s.label }}</span>
                  <label v-for="f in s.fields" :key="f.f" class="flex-1 text-2xs text-slate-500">
                    {{ f.label }}
                    <input :value="pricingVal(v, s.key, f.f)" type="number" step="any" min="0" class="input mt-0.5" placeholder="不填不计费"
                      @input="setPricingVal(v, s.key, f.f, ($event.target as HTMLInputElement).value)" />
                  </label>
                </div>
                <p class="text-2xs leading-relaxed text-slate-600">价格键 = 当前槽位模型名（模型改名后需重填；记账时找不到模型键会回落 "*" 通配键）。文本/视觉填每百万 token 单价。</p>
              </div>
            </div>

            <div class="flex items-center gap-2 pt-1">
              <label class="flex cursor-pointer items-center gap-1.5 text-xs text-slate-300">
                <input v-model="v.enabled" type="checkbox" class="accent-amber-400" />
                启用
              </label>
              <div class="flex-1"></div>
              <button
                class="btn btn-sm"
                :disabled="saving"
                title="保存此厂商（后端为全量提交，其他卡的改动会一并落盘）"
                @click="saveCard"
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path :d="icons.save" stroke-linecap="round" stroke-linejoin="round" />
                </svg>
                {{ saving ? '保存中…' : '保存' }}
              </button>
              <button class="btn btn-ghost btn-sm" :disabled="testing[v.id]" @click="test(v)">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path :d="icons.bolt" stroke-linecap="round" stroke-linejoin="round" />
                </svg>
                {{ testing[v.id] ? '测试中…' : '测试连接' }}
              </button>
              <button v-if="v.id !== 'local-comfyui'" class="btn btn-ghost btn-sm" :disabled="balanceBusy === v.id" title="查询该厂商账户余额（仅官方开放余额 API 的厂商返回数值，其余给控制台指引）" @click="checkBalance(v.id)">
                {{ balanceBusy === v.id ? '查询中…' : '查余额' }}
              </button>
              <button class="btn btn-danger btn-sm" @click="removeVendor(v)">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path :d="icons.trash" stroke-linecap="round" stroke-linejoin="round" />
                </svg>
                删除
              </button>
            </div>

            <div
              v-if="testResults[v.id]"
              class="rounded-lg px-2.5 py-1.5 text-xs-plus font-semibold"
              :class="testResults[v.id].ok ? 'bg-emerald-400/10 text-emerald-300' : 'bg-rose-400/10 text-rose-300'"
            >
              {{ testResults[v.id].text }}
            </div>
            <div
              v-if="balanceText[v.id]"
              class="mt-1 rounded-lg px-2.5 py-1.5 text-xs-plus font-semibold"
              :class="balanceText[v.id].includes('未开放') || balanceText[v.id].includes('失败') ? 'bg-slate-400/10 text-slate-300' : 'bg-sky-400/10 text-sky-200'"
            >
              余额：{{ balanceText[v.id] }}
            </div>
          </div>
        </article>
      </div>
    </section>

    <!-- 新增厂商弹窗 -->
    <Teleport to="body">
      <div
        v-if="addVisible"
        class="overlay p-6"
        @click.self="addVisible = false"
      >
        <div class="glass w-full max-w-md p-5" :style="{ '--glow': GLOW }">
          <h3 class="mb-3 text-base font-bold text-slate-100">新增厂商</h3>
          <label class="mb-1 block text-2xs text-slate-500">厂商 ID（唯一，保存后不可改）</label>
          <input v-model="addForm.id" class="input font-mono" placeholder="如：my-vendor" />
          <label class="mb-1 mt-3 block text-2xs text-slate-500">名称</label>
          <input v-model="addForm.label" class="input" placeholder="如：我的厂商" />
          <label class="mb-1 mt-3 block text-2xs text-slate-500">Base URL</label>
          <input v-model="addForm.base_url" class="input font-mono" placeholder="https://api.example.com" />
          <div class="mt-5 flex justify-end gap-2">
            <button class="btn btn-ghost" @click="addVisible = false">取消</button>
            <button class="btn" :disabled="!addForm.id.trim()" @click="submitAdd">新增</button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>
