<script setup lang="ts">
// -*- coding: utf-8 -*-
/** 用量计费：月份切换 + 总览卡片（调用数/成功率/预估费用）+ 厂商×能力表格 + 最近记录表。
 *  数据来自 workbench/billing/ledger.jsonl（append-only 账本，见 tools/billing.py）。 */
import { ref, computed, onMounted } from 'vue'
import { fetchBillingSummary, fetchBillingRecords, type BillingSummary, type BillingRecord } from '../api'
import { toast } from '../stores/app'

const GLOW = 'rgba(52,211,153,0.35)'

const month = ref('')            // '' = 全部月份；否则 YYYY-MM
const summary = ref<BillingSummary | null>(null)
const records = ref<BillingRecord[]>([])
const loading = ref(false)

/* 月份切换选项：当前月往前 12 个月 + 全部 */
const monthOptions = computed(() => {
  const out: { value: string; label: string }[] = [{ value: '', label: '全部月份' }]
  const d = new Date()
  for (let i = 0; i < 12; i++) {
    const v = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
    out.push({ value: v, label: v })
    d.setMonth(d.getMonth() - 1)
  }
  return out
})

const KIND_LABEL: Record<string, string> = {
  text: '文本', vision: '视觉', image: '生图', image_edit: '改图',
  video: '视频', music: '音乐', speech: '语音', asr: 'ASR', voice: '音色'
}
const kindLabel = (k: string) => KIND_LABEL[k] || k || '—'

/** 币种分桶 → “¥1.23 / $0.45” 文本；空桶显示“—”。 */
function costText(cost: Record<string, number> | undefined): string {
  const entries = Object.entries(cost || {}).filter(([, v]) => v)
  if (!entries.length) return '—'
  return entries.map(([cur, v]) => `${cur === 'CNY' ? '¥' : cur + ' '}${v.toFixed(4).replace(/0+$/, '').replace(/\.$/, '')}`).join(' / ')
}

const successRate = computed(() => {
  const t = summary.value?.total
  if (!t || !t.calls) return '—'
  return ((t.ok / t.calls) * 100).toFixed(1) + '%'
})

function fmtUnits(r: BillingRecord): string {
  if (r.usage) return `${r.usage.prompt_tokens ?? 0}+${r.usage.completion_tokens ?? 0} tok`
  const u = r.units || {}
  if (u.images) return `${u.images} 图`
  if (u.seconds) return `${u.seconds}s`
  if (u.chars) return `${u.chars} 字`
  return ''
}

async function load() {
  loading.value = true
  try {
    const [s, r] = await Promise.all([
      fetchBillingSummary(month.value || undefined),
      fetchBillingRecords(100, month.value || undefined)
    ])
    summary.value = s
    records.value = r.records || []
  } catch (e) {
    toast(e instanceof Error ? e.message : '计费数据加载失败', 'err')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <header class="mb-6 flex flex-wrap items-end gap-3">
      <div>
        <h1 class="grad-text text-2xl font-black">⑥ 用量计费</h1>
        <p class="mt-1 text-xs text-slate-500">模型调用账本（append-only）：成功按单价表计费，无价格配置仍记录调用；失败记 0 元</p>
      </div>
      <div class="ml-auto flex items-center gap-2">
        <select v-model="month" class="select w-36" @change="load">
          <option v-for="m in monthOptions" :key="m.value" :value="m.value">{{ m.label }}</option>
        </select>
        <button class="btn btn-ghost" :disabled="loading" @click="load">{{ loading ? '刷新中…' : '刷新' }}</button>
      </div>
    </header>

    <!-- 总览卡片 -->
    <section class="mb-5 grid grid-cols-1 gap-3 sm:grid-cols-3">
      <div class="glass p-4" :style="{ '--glow': GLOW }">
        <div class="text-2xs text-slate-500">调用次数</div>
        <div class="mt-1 text-2xl font-black text-slate-100">{{ summary?.total.calls ?? '—' }}</div>
        <div class="mt-0.5 text-2xs text-slate-500">成功 {{ summary?.total.ok ?? 0 }} · 失败 {{ summary?.total.fail ?? 0 }}</div>
      </div>
      <div class="glass p-4" :style="{ '--glow': GLOW }">
        <div class="text-2xs text-slate-500">成功率</div>
        <div class="mt-1 text-2xl font-black text-slate-100">{{ successRate }}</div>
        <div class="mt-0.5 text-2xs text-slate-500">按调用次数计（含免费通道外的直连补账）</div>
      </div>
      <div class="glass p-4" :style="{ '--glow': GLOW }">
        <div class="text-2xs text-slate-500">预估费用</div>
        <div class="mt-1 text-2xl font-black text-emerald-300">{{ costText(summary?.total.cost) }}</div>
        <div class="mt-0.5 text-2xs text-slate-500">按厂商单价表估算；未配置单价的调用不计入</div>
      </div>
    </section>

    <!-- 按厂商 × 能力 -->
    <section class="glass mb-5 p-4" :style="{ '--glow': GLOW }">
      <h2 class="mb-2 text-sm font-bold text-slate-200">按厂商 × 能力</h2>
      <table v-if="summary?.groups.length" class="w-full text-xs">
        <thead>
          <tr class="text-left text-2xs text-slate-500">
            <th class="pb-1.5">厂商</th><th class="pb-1.5">能力</th>
            <th class="pb-1.5 text-right">调用</th><th class="pb-1.5 text-right">成功</th>
            <th class="pb-1.5 text-right">失败</th><th class="pb-1.5 text-right">费用</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="g in summary.groups" :key="g.vendor + '/' + g.kind" class="border-t border-line-soft">
            <td class="py-1.5 font-mono text-slate-300">{{ g.vendor }}</td>
            <td class="py-1.5 text-slate-300">{{ kindLabel(g.kind) }}</td>
            <td class="py-1.5 text-right text-slate-300">{{ g.calls }}</td>
            <td class="py-1.5 text-right text-emerald-300">{{ g.ok }}</td>
            <td class="py-1.5 text-right" :class="g.fail ? 'text-rose-300' : 'text-slate-500'">{{ g.fail }}</td>
            <td class="py-1.5 text-right font-semibold text-slate-200">{{ costText(g.cost) }}</td>
          </tr>
        </tbody>
      </table>
      <p v-else class="text-xs text-slate-500">该月份暂无调用记录</p>
    </section>

    <!-- 最近记录 -->
    <section class="glass p-4" :style="{ '--glow': GLOW }">
      <h2 class="mb-2 text-sm font-bold text-slate-200">最近 {{ records.length }} 条记录</h2>
      <div class="overflow-x-auto">
        <table v-if="records.length" class="w-full whitespace-nowrap text-xs">
          <thead>
            <tr class="text-left text-2xs text-slate-500">
              <th class="pb-1.5">时间</th><th class="pb-1.5">厂商</th><th class="pb-1.5">能力</th>
              <th class="pb-1.5">模型</th><th class="pb-1.5">操作</th><th class="pb-1.5">成败</th>
              <th class="pb-1.5 text-right">费用</th><th class="pb-1.5">用量</th><th class="pb-1.5">来源</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(r, i) in records" :key="r.ts + i" class="border-t border-line-soft">
              <td class="py-1.5 font-mono text-slate-400">{{ r.ts }}</td>
              <td class="py-1.5 font-mono text-slate-300">{{ r.vendor }}</td>
              <td class="py-1.5 text-slate-300">{{ kindLabel(r.kind) }}</td>
              <td class="max-w-40 truncate py-1.5 font-mono text-slate-400" :title="r.model">{{ r.model || '—' }}</td>
              <td class="py-1.5 font-mono text-slate-400">{{ r.op }}</td>
              <td class="py-1.5">
                <span v-if="r.ok" class="rounded-full bg-emerald-500/15 px-1.5 py-0.5 text-2xs font-bold text-emerald-300">成功</span>
                <span v-else class="rounded-full bg-rose-500/15 px-1.5 py-0.5 text-2xs font-bold text-rose-300" :title="r.error">失败</span>
              </td>
              <td class="py-1.5 text-right font-semibold" :class="r.cost ? 'text-slate-200' : 'text-slate-500'">
                {{ r.cost ? `${r.currency === 'CNY' ? '¥' : (r.currency || '') + ' '}${r.cost}` : (r.ok ? '—' : '0') }}
              </td>
              <td class="py-1.5 text-slate-400">{{ fmtUnits(r) }}</td>
              <td class="py-1.5 text-slate-500">{{ r.project || r.source || '—' }}</td>
            </tr>
          </tbody>
        </table>
        <p v-else class="text-xs text-slate-500">暂无记录；调用任意云端厂商后自动入账</p>
      </div>
    </section>
  </div>
</template>
