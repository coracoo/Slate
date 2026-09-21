<script setup lang="ts">
// -*- coding: utf-8 -*-
/** ② 台词：左 OCR 原文 / 中 合并时间轴 / 右 ASR 原文；行内编辑 + 导出 srt/txt。 */
import { ref, computed, watch } from 'vue'
import { fetchLines, saveLines, runLinesMerge, runLinesAttribute, runGeneric, fetchText, fetchProjectFile, fetchEnvConfig, deleteFile, fmtT, type ScriptData, type DialogueLine } from '../api'
import { app, projectFiles, materialVideos, loadBasics, toast } from '../stores/app'
import { trackJob } from '../stores/jobs'
import StyledSelect from '../components/StyledSelect.vue'
import DelBadge from '../components/DelBadge.vue'
import EmptyState from '../components/EmptyState.vue'
import { icons } from '../components/icons'

const PALETTE = ['#f87171', '#fbbf24', '#34d399', '#38bdf8', '#a78bfa', '#e879f9', '#fb923c', '#22d3ee']

const script = ref<ScriptData | null>(null)
/** 显式脏标记：编辑入口置 true（替代全量 JSON.stringify 对比），加载/保存成功置 false。 */
const dirty = ref(false)
function markDirty() {
  dirty.value = true
}

/* 可增删行稳定 key：加载时补 _uid，保存前剔除（后端不落该字段）。 */
let uidSeq = 0
const nextUid = () => `u${Date.now().toString(36)}_${++uidSeq}`
function ensureLineUids(lines: DialogueLine[]) {
  for (const l of lines) if (!l._uid) l._uid = nextUid()
}
/** 保存载荷：剔除前端辅助字段 _uid，保持落盘干净。 */
function scriptPayload(): ScriptData {
  const s = script.value!
  return {
    speakers: s.speakers,
    lines: s.lines.map((l) => { const c = { ...l }; delete c._uid; return c })
  }
}
const merging = ref(false)
const saving = ref(false)

/* ---------- 原文文件（拉片/ 与 拉片素材/ 都扫：提取产物就近写在素材视频旁） ---------- */
interface SrcFile { path: string; file: string }
// OCR 列只放字幕类产物；ASR 系列（_ASR.srt / _ASR_修正.srt / _ASR_台词.txt）全部归 ASR 列
const OCR_RE = /(_字幕\.srt|_台词\.txt)$/i
const ASR_RE = /_ASR(_修正)?\.srt$|_ASR_台词\.txt$/i
const isAsr = (f: string) => /ASR/i.test(f)
const scanSubs = (re: RegExp): SrcFile[] =>
  (['拉片', '拉片素材'] as const).flatMap((sub) =>
    projectFiles(sub, re).map((f) => ({ path: `projects/${app.current}/${sub}/${f}`, file: f }))
  )
const ocrFiles = computed<SrcFile[]>(() => scanSubs(OCR_RE).filter((f) => !isAsr(f.file)))
const asrFiles = computed<SrcFile[]>(() => scanSubs(ASR_RE))

/* ---------- 台词解析入口：OCR 字幕提取 / ASR 语音转写（走 /api/run 后台任务） ---------- */
const srcVideo = ref('')
const asrEngine = ref<'local' | 'cloud'>('local')
const asrModel = ref('large-v3-turbo')
const asrVendor = ref('')
const asrCloudModel = ref('')
const extracting = ref('')
const srcOptions = computed(() => materialVideos().map((v) => v.rel))
const srcLabels = computed(() => Object.fromEntries(materialVideos().map((v) => [v.rel, v.file])))
watch([srcOptions, srcVideo], () => {
  if (!srcVideo.value && srcOptions.value.length) srcVideo.value = srcOptions.value.at(-1) || '' // 默认选最后一个素材视频（含项目切换回填）
}, { immediate: true })

/* 云端 ASR：可用厂商（已启用 + 有 base_url + 已配 key）与各家的 ASR 模型建议 */
const CLOUD_MODEL_HINTS: Record<string, string> = {
  qwen: 'paraformer-v2',
  doubao: 'doubao-seed-asr-2.0', // 走 openspeech 任务制（非方舟）；需在环境页 doubao 卡片配「语音 ASR」区
  'openai-compat': 'whisper-1',
  minimax: '',
  gemini: '',
}
const cloudVendors = ref<{ id: string; label: string }[]>([])
fetchEnvConfig().then((cfg) => {
  cloudVendors.value = (cfg.vendors || [])
    .filter((v) => v.enabled && v.base_url && v.api_key)
    .map((v) => ({ id: v.id, label: v.label }))
}).catch(() => {})
watch(asrVendor, (v) => {
  const hint = CLOUD_MODEL_HINTS[v]
  if (hint !== undefined) asrCloudModel.value = hint
})

async function extract(kind: 'subtitles' | 'speech') {
  if (!app.current || !srcVideo.value) {
    toast('请先选择素材视频', 'err')
    return
  }
  if (kind === 'speech' && asrEngine.value === 'cloud' && !asrVendor.value) {
    toast('云端引擎需先选择厂商（环境页启用并配置 key）', 'err')
    return
  }
  extracting.value = kind
  try {
    const args =
      kind === 'speech'
        ? asrEngine.value === 'cloud'
          ? [srcVideo.value, '--engine', 'cloud', '--vendor', asrVendor.value, '--model', asrCloudModel.value, '--lang', 'zh']
          : [srcVideo.value, '--model', asrModel.value, '--lang', 'zh']
        : [srcVideo.value]
    const r = await runGeneric({ step: kind, args })
    if (!r.id) throw new Error(r.err || '任务未启动')
    toast(`提取任务 #${r.id} 已启动`, 'ok')
    const label = asrEngine.value === 'cloud' ? `${asrVendor.value}/${asrCloudModel.value}` : asrModel.value
    const j = await trackJob(r.id, kind === 'speech' ? `ASR 转写 (${label})` : 'OCR 字幕提取')
    if (j.success) {
      toast('提取完成，可点「合并台词」', 'ok')
      await loadBasics() // 刷新项目树，让新 srt 出现在左右栏
    } else {
      toast('提取失败，详情见任务抽屉', 'err')
    }
  } catch (e) {
    toast(e instanceof Error ? e.message : '启动失败', 'err')
  } finally {
    extracting.value = ''
  }
}

/* ---------- ④ ASR AI 纠错：文本厂商批量订正同音错字，时间轴不变 ---------- */
const fixing = ref(false)

async function fixAsr() {
  if (!app.current || !srcVideo.value) {
    toast('请先选择素材视频', 'err')
    return
  }
  const srt = srcVideo.value.replace(/\.[^.]+$/, '') + '_ASR.srt'
  if (!asrFiles.value.some((f) => f.path === srt)) {
    toast('该视频还没有 ASR 产物，先跑 ② 提取', 'err')
    return
  }
  fixing.value = true
  try {
    const r = await runGeneric({ step: 'fixasr', args: [srt] })
    if (!r.id) throw new Error(r.err || '任务未启动')
    toast(`纠错任务 #${r.id} 已启动`, 'ok')
    const j = await trackJob(r.id, 'ASR AI 纠错')
    if (j.success) {
      toast('纠错完成，产物 *_ASR_修正.srt；确认后重新合并即生效', 'ok')
      await loadBasics()
    } else {
      toast('纠错失败，详情见任务抽屉', 'err')
    }
  } catch (e) {
    toast(e instanceof Error ? e.message : '启动失败', 'err')
  } finally {
    fixing.value = false
  }
}

const previewText = ref('')
const previewTitle = ref('')
async function preview(f: SrcFile) {
  previewTitle.value = f.file
  previewText.value = '加载中…'
  try {
    previewText.value = await fetchText(f.path)
  } catch {
    previewText.value = '读取失败'
  }
}

/* ---------- AI 归属 sidecar（合并的第三路输入，左栏展示） ---------- */
interface Sidecar {
  speakers: Record<string, { name: string; color: string }>
  lines: { t_in: number; t_out: number; text: string; speaker: string; source: string }[]
}
const sidecar = ref<Sidecar | null>(null)

async function loadSidecar() {
  if (!app.current) {
    sidecar.value = null
    return
  }
  try {
    sidecar.value = await fetchProjectFile<Sidecar>(app.current, 'AI归属_台词角色.json')
  } catch {
    sidecar.value = null
  }
}

function previewSidecar() {
  previewTitle.value = 'AI归属_台词角色.json'
  previewText.value = sidecar.value ? JSON.stringify(sidecar.value, null, 1).slice(0, 6000) : ''
}

/** 删除 AI 归属 sidecar（可重新跑 ④ 生成）。 */
async function delSidecar() {
  if (!app.current) return
  if (!confirm('确定删除 AI归属_台词角色.json？可重新跑「④ AI 人物归属」生成')) return
  try {
    await deleteFile(app.current, 'AI归属_台词角色.json')
    toast('已删除归属 sidecar', 'ok')
    sidecar.value = null
    await loadBasics()
  } catch (e) {
    toast(e instanceof Error ? e.message : '删除失败', 'err')
  }
}

/* ---------- 加载 / 合并 / 保存 ---------- */
let loadSeq = 0
async function load() {
  const seq = ++loadSeq
  const project = app.current
  if (!project) return
  loadSidecar()
  try {
    const result = await fetchLines(project)
    if (seq !== loadSeq || project !== app.current) return
    ensureLineUids(result.lines)
    script.value = result
    dirty.value = false
  } catch (e) {
    if (seq !== loadSeq || project !== app.current) return
    if (e instanceof Error && 'status' in e && (e as { status: number }).status === 404) {
      // 旧后端 404 = 尚无台词脚本；新契约 200 空表，此处仅兜底
      script.value = { speakers: {}, lines: [] }
      dirty.value = false
    } else {
      toast('台词加载失败（后端可能未就绪）', 'err')
    }
  }
}

async function merge() {
  if (!app.current) return
  merging.value = true
  try {
    const r = await runLinesMerge(app.current)
    toast(`合并任务 #${r.id} 已启动`, 'ok')
    const j = await trackJob(r.id, '台词合并（ASR 为主，OCR 补漏）')
    if (j.success) {
      toast('合并完成', 'ok')
      await load()
    } else {
      toast('合并失败，详情见任务抽屉', 'err')
    }
  } catch (e) {
    toast(e instanceof Error ? e.message : '启动失败', 'err')
  } finally {
    merging.value = false
  }
}

async function save() {
  if (!app.current || !script.value) return
  saving.value = true
  try {
    await saveLines(app.current, scriptPayload())
    dirty.value = false
    toast('台词已保存', 'ok')
  } catch (e) {
    toast(e instanceof Error ? e.message : '保存失败', 'err')
  } finally {
    saving.value = false
  }
}

/* ---------- AI 人物归属（vision，产物为合并的第三路输入 sidecar） ---------- */
const attributing = ref(false)

async function attribute() {
  if (!app.current) return
  if (!ocrFiles.value.length && !asrFiles.value.length) {
    toast('请先跑 ① OCR / ② ASR 提取', 'err')
    return
  }
  attributing.value = true
  try {
    const r = await runLinesAttribute({ project: app.current })
    if (!r.id) throw new Error(r.err || '任务未启动')
    toast(`人物归属任务 #${r.id} 已启动`, 'ok')
    const j = await trackJob(r.id, 'AI 台词人物归属')
    if (j.success) {
      toast('归属完成，自动合并中…', 'ok')
      await merge() // 归属是合并的输入：链式触发合并，结果直接落到时间轴
    } else {
      toast('人物归属失败，详情见任务抽屉', 'err')
    }
  } catch (e) {
    toast(e instanceof Error ? e.message : '启动失败', 'err')
  } finally {
    attributing.value = false
  }
}

/* ---------- 说话人颜色 ---------- */
function speakerColor(id: string): string {
  if (!script.value) return PALETTE[0]
  const s = script.value.speakers[id]
  if (s?.color) return s.color
  // 未登记说话人：按 hash 分配一个稳定颜色
  let h = 0
  for (const c of id) h = (h * 31 + c.charCodeAt(0)) % 997
  return PALETTE[h % PALETTE.length]
}

function speakerName(id: string): string {
  return script.value?.speakers[id]?.name || id
}

function setSpeakerColor(id: string, color: string) {
  if (!script.value) return
  if (!script.value.speakers[id]) script.value.speakers[id] = { name: speakerName(id), color }
  else script.value.speakers[id].color = color
  markDirty()
}

/* ---------- 行校验：时间、角色、台词 三者不可缺 ---------- */
function lineIssues(l: { t_in?: number | null; t_out?: number | null; speaker?: string | null; text?: string | null }): string[] {
  const issues: string[] = []
  const badTime =
    l.t_in === null || l.t_in === undefined || l.t_out === null || l.t_out === undefined ||
    Number.isNaN(l.t_in) || Number.isNaN(l.t_out) || !(l.t_out > l.t_in) || !(l.t_in >= 0)
  if (badTime) issues.push('缺时间')
  if (!l.speaker || !String(l.speaker).trim()) issues.push('缺角色')
  if (!l.text || !String(l.text).trim()) issues.push('缺台词')
  return issues
}
const incompleteCount = computed(
  () => (script.value?.lines || []).filter((l) => lineIssues(l).length).length
)

/* ---------- 行编辑 ---------- */
function addLine() {
  if (!script.value) return
  const last = script.value.lines[script.value.lines.length - 1]
  script.value.lines.push({
    t_in: last ? last.t_out : 0,
    t_out: last ? last.t_out + 2 : 2,
    speaker: Object.keys(script.value.speakers)[0] || '角色A',
    text: '',
    source: 'manual',
    _uid: nextUid()
  })
  markDirty()
}
function removeLine(i: number) {
  script.value?.lines.splice(i, 1)
  markDirty()
}

/* ---------- 导出 ---------- */
function exportTxt() {
  if (!script.value) return
  const text = script.value.lines.map((l) => `${fmtT(l.t_in)} → ${fmtT(l.t_out)}  ${speakerName(l.speaker)}：${l.text}`).join('\n')
  download(`${app.current}_台词.txt`, text)
}

function exportSrt() {
  if (!script.value) return
  const srt = script.value.lines
    .map((l, i) => {
      return `${i + 1}\n${srtTime(l.t_in)} --> ${srtTime(l.t_out)}\n${speakerName(l.speaker)}：${l.text}\n`
    })
    .join('\n')
  download(`${app.current}_台词.srt`, srt)
}

function srtTime(t: number): string {
  const h = Math.floor(t / 3600)
  const m = Math.floor((t % 3600) / 60)
  const s = Math.floor(t % 60)
  const ms = Math.round((t - Math.floor(t)) * 1000)
  const p = (n: number, w: number) => String(n).padStart(w, '0')
  return `${p(h, 2)}:${p(m, 2)}:${p(s, 2)},${p(ms, 3)}`
}

function download(name: string, text: string) {
  const blob = new Blob([text], { type: 'text/plain;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = name
  a.click()
  URL.revokeObjectURL(a.href)
  toast(`已导出 ${name}`, 'ok')
}

watch(() => app.current, load, { immediate: true })
</script>

<template>
  <div class="page">
    <header class="mb-6 flex flex-wrap items-end gap-3">
      <div class="mr-auto">
        <h1 class="grad-text text-2xl font-black">② 台词分析</h1>
        <p class="mt-1 text-xs text-slate-500">ASR 语音为主时间轴，OCR 字幕仅补 ASR 漏听的台词；双击行可就地编辑</p>
      </div>
      <button class="btn" :disabled="merging || !app.current" @click="merge">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path :d="icons.bolt" stroke-linecap="round" stroke-linejoin="round"/></svg>
        {{ merging ? '合并中…' : '合并台词' }}
      </button>
      <button class="btn btn-ghost btn-sm" :disabled="!script" @click="exportSrt">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path :d="icons.download" stroke-linecap="round" stroke-linejoin="round"/></svg>
        导出 srt
      </button>
      <button class="btn btn-ghost btn-sm" :disabled="!script" @click="exportTxt">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path :d="icons.download" stroke-linecap="round" stroke-linejoin="round"/></svg>
        导出 txt
      </button>
      <button class="btn btn-sm" :disabled="!dirty || saving" @click="save">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path :d="icons.save" stroke-linecap="round" stroke-linejoin="round"/></svg>
        保存{{ saving ? '中…' : '' }}
      </button>
      <span v-if="dirty" class="rounded-full bg-amber-400/15 px-2 py-0.5 text-2xs font-bold text-amber-300">● 未保存</span>
    </header>

    <EmptyState v-if="!app.current" title="请先在左侧选择项目" />

    <template v-else>
      <!-- 台词解析入口：OCR / ASR 提取 -->
      <div class="glass mb-4 flex flex-wrap items-end gap-3 p-3">
        <label class="min-w-56 text-xs text-slate-400">
          源视频（项目 拉片素材/）
          <StyledSelect
            v-model="srcVideo"
            class="mt-1"
            :options="srcOptions"
            :labels="srcLabels"
            :storage-key="`wb.${app.current}.lines.video`"
            placeholder="— 选择素材视频 —"
          />
        </label>
        <label class="w-28 text-xs text-slate-400">
          ASR 引擎
          <StyledSelect
            v-model="asrEngine"
            class="mt-1"
            :options="['local', 'cloud']"
            :labels="{ local: '本地 whisper', cloud: '云端 API' }"
            storage-key="wb.lines.asr.engine"
          />
        </label>
        <template v-if="asrEngine === 'local'">
          <label class="w-32 text-xs text-slate-400">
            本地模型
            <StyledSelect
              v-model="asrModel"
              class="mt-1"
              :options="['tiny', 'base', 'small', 'medium', 'large-v3-turbo']"
              :labels="{ tiny: 'tiny（最快）', base: 'base', small: 'small', medium: 'medium', 'large-v3-turbo': 'large-v3-turbo（最准）' }"
              storage-key="wb.lines.asr.model"
            />
          </label>
        </template>
        <template v-else>
          <label class="w-36 text-xs text-slate-400">
            云端厂商
            <StyledSelect
              v-model="asrVendor"
              class="mt-1"
              :options="cloudVendors.map((v) => v.id)"
              :labels="Object.fromEntries(cloudVendors.map((v) => [v.id, v.label]))"
              storage-key="wb.lines.asr.vendor"
              placeholder="— 选厂商 —"
            />
          </label>
          <label class="w-48 text-xs text-slate-400">
            云端 ASR 模型
            <input v-model="asrCloudModel" class="input mt-1 text-xs" placeholder="选择厂商后填写其语音识别模型" />
          </label>
        </template>
        <button
          class="btn w-44 justify-center"
          style="--c1: #fbbf24; --c2: #fb923c"
          :disabled="!!extracting || !srcVideo"
          title="可选：画面字幕 OCR。ASR 够准时无需跑；只在 ASR 漏听的时段作为补充进合并结果"
          @click="extract('subtitles')"
        >
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path :d="icons.chat" stroke-linecap="round" stroke-linejoin="round"/></svg>
          {{ extracting === 'subtitles' ? '提取中…' : '① OCR 字幕（可选）' }}
        </button>
        <button
          class="btn w-44 justify-center"
          style="--c1: #22d3ee; --c2: #38bdf8"
          :disabled="!!extracting || !srcVideo"
          @click="extract('speech')"
        >
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path :d="icons.bolt" stroke-linecap="round" stroke-linejoin="round"/></svg>
          {{ extracting === 'speech' ? '转写中…' : '② 提取 ASR 语音' }}
        </button>
        <button
          class="btn w-40 justify-center"
          style="--c1: #34d399; --c2: #22d3ee"
          :disabled="fixing || !srcVideo"
          title="用已配置的文本模型批量订正 ASR 同音错字（文言/成语/人名），时间轴不变；产物 *_ASR_修正.srt，归属与合并自动优先采用"
          @click="fixAsr"
        >
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path :d="icons.wand" stroke-linecap="round" stroke-linejoin="round"/></svg>
          {{ fixing ? '纠错中…' : '③ ASR AI 纠错' }}
        </button>
        <button
          class="btn w-40 justify-center"
          style="--c1: #a78bfa; --c2: #e879f9"
          :disabled="attributing || (!ocrFiles.length && !asrFiles.length)"
          title="vision 按镜头关键帧判断每句谁说的，基于 OCR + ASR（有修正版用修正版）产出归属 sidecar；完成后自动合并"
          @click="attribute"
        >
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path :d="icons.wand" stroke-linecap="round" stroke-linejoin="round"/></svg>
          {{ attributing ? '归属中…' : '④ AI 人物归属' }}
        </button>
        <p class="flex-1 self-center text-right text-2xs leading-relaxed text-slate-500">
          推荐 ② ASR（主）→ ③ 纠错 → ④ 归属 → 合并；① OCR 可选，只补 ASR 漏听的台词<br />合并以 ASR 时间轴为准：OCR 字幕窗口已被 ASR 覆盖即丢弃（不相似时挂 alt 备查）
        </p>
      </div>

      <div class="grid grid-cols-1 gap-4 lg:grid-cols-[300px_1fr]">
      <!-- 左列：三路输入（OCR / ASR / AI 归属） -->
      <div class="space-y-4">
      <!-- OCR 原文 -->
      <section class="glass p-3">
        <h3 class="mb-2 flex items-center gap-1.5 text-xs font-bold text-slate-400">
          <span class="h-2 w-2 rounded-full bg-amber-400"></span>① OCR 字幕原文
        </h3>
        <div v-if="!ocrFiles.length" class="py-4 text-center text-xs-plus text-slate-500">
          暂无 *_字幕.srt / *_台词.txt<br />ASR 够准时可跳过 OCR（仅补漏用）
        </div>
        <div v-else class="space-y-1.5">
          <button
            v-for="f in ocrFiles" :key="f.path"
            class="glass glass-hover group relative block w-full p-2 text-left"
            :style="{ '--glow': 'rgba(251,191,36,0.3)' }"
            @click="preview(f)"
          >
            <DelBadge :path="f.path.replace(`projects/${app.current}/`, '')" :label="f.file"
              @deleted="load(); loadSidecar()" />
            <div class="flex items-center gap-1.5 text-xs-plus font-semibold text-slate-200">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#fbbf24" stroke-width="2"><path :d="icons.chat" stroke-linecap="round" stroke-linejoin="round"/></svg>
              <span class="truncate">{{ f.file }}</span>
            </div>
            <div class="mt-0.5 truncate pl-4 text-2xs text-slate-500">{{ f.path }}</div>
          </button>
        </div>
      </section>

      <!-- ASR 原文 -->
      <section class="glass p-3">
        <h3 class="mb-2 flex items-center gap-1.5 text-xs font-bold text-slate-400">
          <span class="h-2 w-2 rounded-full bg-cyan-400"></span>② ASR 语音原文
        </h3>
        <div v-if="!asrFiles.length" class="py-4 text-center text-xs-plus text-slate-500">
          暂无 *_ASR.srt<br />先跑 ② ASR 提取
        </div>
        <div v-else class="space-y-1.5">
          <button
            v-for="f in asrFiles" :key="f.path"
            class="glass glass-hover group relative block w-full p-2 text-left"
            :style="{ '--glow': 'rgba(34,211,238,0.3)' }"
            @click="preview(f)"
          >
            <DelBadge :path="f.path.replace(`projects/${app.current}/`, '')" :label="f.file"
              @deleted="load(); loadSidecar()" />
            <div class="flex items-center gap-1.5 text-xs-plus font-semibold text-slate-200">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#22d3ee" stroke-width="2"><path :d="icons.chat" stroke-linecap="round" stroke-linejoin="round"/></svg>
              <span class="truncate">{{ f.file }}</span>
            </div>
            <div class="mt-0.5 truncate pl-4 text-2xs text-slate-500">{{ f.path }}</div>
          </button>
        </div>
      </section>

      <!-- AI 人物归属（sidecar） -->
      <section class="glass p-3">
        <h3 class="mb-2 flex items-center gap-1.5 text-xs font-bold text-slate-400">
          <span class="h-2 w-2 rounded-full bg-fuchsia-400"></span>④ AI 人物归属
        </h3>
        <div v-if="!sidecar" class="py-4 text-center text-xs-plus text-slate-500">
          暂无归属结果<br />先跑 ④ AI 人物归属
        </div>
        <template v-else>
          <div class="flex flex-wrap gap-1.5">
            <span
              v-for="(spk, name) in sidecar.speakers"
              :key="name"
              class="rounded-full border px-2 py-0.5 text-2xs font-semibold"
              :style="{ color: spk.color, borderColor: spk.color + '55', background: spk.color + '14' }"
            >{{ spk.name }}</span>
          </div>
          <div class="mt-2 flex items-center justify-between text-2xs text-slate-500">
            <span>{{ sidecar.lines.length }} 条已归属</span>
            <span class="flex items-center gap-2">
              <button class="text-cyan-300/70 transition hover:text-cyan-200" @click="previewSidecar">查看 JSON</button>
              <button
                class="btn-danger rounded-md p-0.5 transition"
                title="删除 AI归属_台词角色.json（可重新跑 ④ 生成）"
                @click="delSidecar"
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path :d="icons.trash" stroke-linecap="round" stroke-linejoin="round"/></svg>
              </button>
            </span>
          </div>
        </template>
      </section>

      <!-- 文本预览（OCR/ASR/sidecar 共用） -->
      <div v-if="previewText" class="glass p-3">
        <div class="mb-1 flex items-center justify-between text-2xs text-slate-500">
          <span class="truncate">{{ previewTitle }}</span>
          <button class="text-slate-500 hover:text-slate-300" @click="previewText = ''">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path :d="icons.close" stroke-linecap="round"/></svg>
          </button>
        </div>
        <pre class="log-tail max-h-48 overflow-y-auto whitespace-pre-wrap text-slate-400" style="color:#94a3b8">{{ previewText.slice(0, 6000) }}</pre>
      </div>
      </div>

      <!-- 右：合并时间轴（宽列；flex 填充剩余视口高度，内部滚动） -->
      <section class="glass flex max-h-[calc(100vh-7rem)] flex-col p-3">
        <div class="mb-2 flex items-center justify-between gap-2">
          <h3 class="text-xs font-bold text-slate-400">
            合并结果时间轴
            <span v-if="script" class="ml-1 font-normal text-slate-500">
              {{ script.lines.length }} 行
              <span v-if="incompleteCount" class="font-bold text-rose-400">· {{ incompleteCount }} 行不完整</span>
            </span>
          </h3>
          <button class="btn btn-ghost btn-sm" :disabled="!script" @click="addLine">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path :d="icons.plus" stroke-linecap="round"/></svg>
            加一行
          </button>
        </div>

        <div v-if="!script" class="py-10 text-center text-xs-plus leading-relaxed text-slate-500">加载中…</div>
        <div v-else-if="!script.lines.length" class="py-10 text-center text-xs-plus leading-relaxed text-slate-500">
          还没有合并台词<br />点右上角「合并台词」：ASR 为主时间轴，OCR 自动补漏
        </div>

        <div v-else class="min-h-0 flex-1 space-y-1.5 overflow-y-auto pr-1">
          <div
            v-for="(l, i) in script.lines"
            :key="l._uid || i"
            class="group rounded-lg border bg-black/25 p-2 transition"
            :class="lineIssues(l).length ? 'border-rose-500/40' : 'border-line-soft hover:border-amber-400/20'"
          >
            <div class="flex items-center gap-1.5 text-2xs tabular-nums text-slate-500">
              <input v-model.number="l.t_in" type="number" step="0.1" min="0" class="input w-14 px-1 py-0.5 text-2xs" title="入点(秒)" @input="markDirty" />
              <span>→</span>
              <input v-model.number="l.t_out" type="number" step="0.1" min="0" class="input w-14 px-1 py-0.5 text-2xs" title="出点(秒)" @input="markDirty" />
              <span v-for="iss in lineIssues(l)" :key="iss" class="pop-in rounded-full bg-rose-500/15 px-1.5 py-px text-2xs font-bold text-rose-400">{{ iss }}</span>
              <span class="flex-1"></span>
              <span class="rounded bg-white/5 px-1">{{ l.source || 'merge' }}</span>
              <button class="opacity-0 transition group-hover:opacity-100" @click="removeLine(i)">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#f87171" stroke-width="2"><path :d="icons.trash" stroke-linecap="round" stroke-linejoin="round"/></svg>
              </button>
            </div>
            <div class="mt-1 flex items-start gap-2">
              <div class="flex shrink-0 flex-col items-center gap-0.5 pt-1">
                <input
                  type="color"
                  class="h-4 w-6 cursor-pointer rounded border-0 bg-transparent p-0"
                  :value="speakerColor(l.speaker)"
                  title="说话人颜色"
                  @input="setSpeakerColor(l.speaker, ($event.target as HTMLInputElement).value)"
                />
              </div>
              <input
                class="input w-20 shrink-0 px-1.5 py-0.5 text-xs-plus font-bold"
                :class="{ 'border-rose-500/50!': !l.speaker }"
                :style="{ color: speakerColor(l.speaker), borderColor: speakerColor(l.speaker) + '44' }"
                v-model="l.speaker"
                title="说话人（角色，不可为空）"
                @input="markDirty"
              />
              <input
                v-model="l.text"
                class="input flex-1 px-1.5 py-0.5 text-xs"
                :class="{ 'border-rose-500/50!': !l.text }"
                placeholder="台词文本（不可为空）"
                @input="markDirty"
              />
            </div>
          </div>
        </div>
      </section>
      </div>
    </template>
  </div>
</template>
