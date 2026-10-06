<script setup lang="ts">
/** RH 调用入口：AI 应用通道（v2，个人 Key）——webappId → 拉取节点自动生成表单 → 提交/查询。标准模型 API 前端已下线（10-06 用户定版）。 */
import { computed, ref } from 'vue'
import { submitRunningHub, queryRunningHub, getJSON } from '../api'

defineProps<{ saved: boolean }>()

/* ── AI 应用通道 ── */
interface AppNode { nodeId: string | number; fieldName: string; fieldType?: string; description?: string | null; fieldValue?: string }
const webappId = ref('')
const appName = ref('')
const appDesc = ref('')
const appNodes = ref<AppNode[]>([])
const instanceType = ref('default')
const usePersonalQueue = ref(false)
const demoBusy = ref(false)
const demoMsg = ref('')
const lastApps = ref<{ id: string; name: string }[]>([])
try {
  const raw = localStorage.getItem('slate:rh:apps')
  if (raw) lastApps.value = JSON.parse(raw)
  webappId.value = localStorage.getItem('slate:rh:last-app') || ''
} catch { /* 忽略损坏记录 */ }

async function loadDemo() {
  const id = webappId.value.trim()
  if (!/^\d{6,32}$/.test(id)) { demoMsg.value = 'webappId 不合法：填应用详情页 URL 里的长数字'; return }
  demoBusy.value = true; demoMsg.value = ''
  try {
    const r = await getJSON<{ ok: boolean; webappName?: string; description?: string; nodes?: AppNode[]; err?: string }>(
      `/api/runninghub/aiapp-demo?webapp_id=${id}`)
    if (!r.ok) throw new Error(r.err || '拉取失败')
    appName.value = r.webappName || ''
    appDesc.value = (r.description || '').replace(/<[^>]+>/g, ' ').slice(0, 120)
    appNodes.value = (r.nodes || []).map(n => ({ ...n, fieldValue: n.fieldValue ?? '' }))
    if (appNodes.value.length) demoMsg.value = `发现 ${appNodes.value.length} 个输入节点`
    else demoMsg.value = '该应用未暴露输入参数（nodeInfoList 空，直接提交即用默认值）'
    const entry = { id, name: appName.value || id }
    lastApps.value = [entry, ...lastApps.value.filter(a => a.id !== id)].slice(0, 8)
    localStorage.setItem('slate:rh:apps', JSON.stringify(lastApps.value))
    localStorage.setItem('slate:rh:last-app', id)
  } catch (e) { demoMsg.value = e instanceof Error ? e.message : '拉取失败' } finally { demoBusy.value = false }
}

const nodeInfoList = computed<AppNode[]>(() => appNodes.value.map(n => ({ nodeId: n.nodeId, fieldName: n.fieldName, fieldValue: n.fieldValue })))

const busy = ref(false)
const message = ref('')
const result = ref('')
const files = ref<{ path: string; url: string }[]>([])
const task = ref('')
const legacy = ref(false)
try {
  task.value = localStorage.getItem('slate:rh:last-task') || ''
  legacy.value = localStorage.getItem('slate:rh:last-task-legacy') === 'true'
} catch { /* 浏览器禁用存储时仍可手填 */ }

async function submitApp() {
  busy.value = true; message.value = ''; files.value = []
  try {
    const body = { nodeInfoList: nodeInfoList.value, instanceType: instanceType.value, usePersonalQueue: String(usePersonalQueue.value).toLowerCase() }
    const data = await submitRunningHub(`/openapi/v2/run/ai-app/${webappId.value.trim()}`, body)
    if (!data.ok) throw new Error(data.err)
    result.value = JSON.stringify(data.result, null, 2)
    const nested = data.result.data as Record<string, unknown> | undefined
    task.value = String(data.result.taskId || nested?.taskId || '')
    legacy.value = false
    if (task.value) {
      try { localStorage.setItem('slate:rh:last-task', task.value); localStorage.setItem('slate:rh:last-task-legacy', 'false') } catch { /* 同上 */ }
      message.value = '已提交（按 RH 应用计费）；任务 ID 已保留，点击查询结果。'
    } else message.value = '接口已返回，结果见下方。'
  } catch (e) { message.value = `提交失败或结果未知：${String(e)}；先查 RH 任务记录再决定是否重发。` } finally { busy.value = false }
}

async function query() {
  busy.value = true
  try {
    const data = await queryRunningHub(task.value, true, legacy.value)
    if (!data.ok) throw new Error(data.err)
    result.value = JSON.stringify(data.result, null, 2)
    files.value = data.files || []
    message.value = files.value.length ? '任务完成，结果已下载到本地。' : `任务状态：${data.result.status || '未知'}`
  } catch (e) { message.value = `查询失败：${String(e)}；可再次查询同一任务。` } finally { busy.value = false }
}
</script>

<template>
  <div class="mt-3 space-y-2 border-t border-white/10 pt-3">
    <div class="flex gap-2">
      <input v-model="webappId" class="input flex-1 font-mono" placeholder="webappId：应用详情页 URL 里的长数字（如 2084320751339032577）" />
      <button class="btn btn-sm" :disabled="demoBusy" @click="loadDemo">{{ demoBusy ? '拉取中…' : '拉取节点' }}</button>
    </div>
    <div v-if="lastApps.length" class="flex flex-wrap gap-1">
      <button v-for="a in lastApps" :key="a.id" class="rounded-full bg-white/5 px-2 py-0.5 text-2xs text-slate-300 hover:bg-cyan-400/15 hover:text-cyan-100" @click="webappId = a.id; loadDemo()">{{ a.name }}</button>
    </div>
    <p v-if="appName" class="text-xs-plus text-slate-300"><b>{{ appName }}</b> <span class="text-2xs text-slate-500">{{ appDesc }}</span></p>
    <p v-if="demoMsg" class="text-2xs text-slate-400">{{ demoMsg }}</p>
    <div v-if="appNodes.length" class="space-y-1.5 rounded-lg bg-black/20 p-2">
      <div v-for="(n, i) in appNodes" :key="i" class="grid grid-cols-[auto_1fr] items-center gap-2">
        <span class="rounded bg-white/5 px-1.5 py-0.5 font-mono text-2xs text-slate-400" :title="`节点 ${n.nodeId} · ${n.fieldType || ''}`">{{ n.nodeId }}·{{ n.fieldName }}</span>
        <input v-if="(n.fieldType||'').toUpperCase()!=='IMAGE'" v-model="n.fieldValue" class="input !py-1 text-xs" :placeholder="n.description || String(n.fieldType || '值')" />
        <span v-else class="text-2xs text-slate-500">图片输入：留空用默认；批量链路自动上传填 URL</span>
      </div>
    </div>
    <div class="flex flex-wrap items-center gap-2">
      <label class="text-2xs text-slate-400">实例
        <select v-model="instanceType" class="input ml-1 !py-0.5 text-2xs">
          <option value="default">default（24G）</option>
          <option value="plus">plus（48G）</option>
          <option value="ultra">ultra（84G）</option>
        </select>
      </label>
      <label class="flex items-center gap-1 text-2xs text-slate-400"><input v-model="usePersonalQueue" type="checkbox" />个人独占队列</label>
      <button class="btn btn-sm" :disabled="busy || !saved || !/^\d{6,32}$/.test(webappId.trim())" @click="submitApp">提交一次（计费）</button>
      <span v-if="!saved" class="text-amber-300">先保存厂商配置</span>
    </div>

    <div class="flex gap-2">
      <input v-model="task" class="input flex-1 font-mono" placeholder="taskId：支持查询此前提交的任务" />
      <label class="flex shrink-0 items-center gap-1"><input v-model="legacy" type="checkbox" />旧任务</label>
      <button class="btn btn-ghost btn-sm" :disabled="busy || !task.trim()" @click="query">查询并下载结果</button>
    </div>
    <p v-if="message" class="break-words text-slate-300">{{ message }}</p>
    <a v-for="file in files" :key="file.path" :href="file.url" target="_blank" rel="noopener" class="block truncate text-emerald-300" :title="file.path">{{ file.path }}</a>
    <pre v-if="result" class="max-h-64 overflow-auto rounded bg-black/30 p-2 text-2xs">{{ result }}</pre>
  </div>
</template>
