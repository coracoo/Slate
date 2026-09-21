<script setup lang="ts">
import { onMounted, ref } from 'vue'
const state = ref<Record<string, any>>({})
const message = ref('')
async function refresh() {
  try {
    const response = await fetch('/api/env/chrome-use')
    const data = await response.json()
    if (!response.ok) throw new Error(data.err || '检查失败')
    state.value = data.status
  } catch (e) { message.value = String(e) }
}
async function copy() {
  try { await navigator.clipboard.writeText(state.value.install_command); message.value = '已复制。在本机 PowerShell 执行后，点重新检查。' }
  catch { message.value = '请选中下方命令复制。' }
}
onMounted(refresh)
</script>
<template>
  <section class="glass mb-5 p-5">
    <div class="flex flex-wrap items-center gap-3">
      <h3 class="font-bold text-sky-200">ChatGPT · chrome-use</h3>
      <span :class="state.installed ? 'text-emerald-300' : 'text-amber-300'">{{ state.installed ? '本机程序已安装' : '尚未安装' }}</span>
      <button class="btn btn-sm" @click="refresh">重新检查</button>
      <a class="text-xs text-sky-300" href="https://github.com/leeguooooo/image-use" target="_blank" rel="noopener" title="网页生图服务由开源 image-use 提供；工作台负责排队、校验和导入。">服务由 image-use 提供 ↗</a>
    </div>
    <p class="my-3 text-xs leading-relaxed text-slate-400">一次只发送一个生图任务，带上该任务的参考图；下载原图后自动匹配并导入，再执行下一张。无需 Previs MCP、公网隧道或旧 Runner 扩展。</p>
    <div class="flex flex-wrap gap-2">
      <button class="btn btn-sm" :disabled="!state.install_command" @click="copy">复制一键安装命令</button>
      <a class="btn btn-sm" :href="state.extension_url" target="_blank" rel="noopener">安装 Chrome 扩展</a>
      <a class="btn btn-sm" :href="state.extension_download" target="_blank" rel="noopener">下载上游扩展包</a>
      <a class="btn btn-sm" href="https://chatgpt.com/" target="_blank" rel="noopener">打开 ChatGPT 登录</a>
    </div>
    <pre class="my-3 overflow-auto rounded-lg bg-black/20 p-3 text-xs text-sky-200 select-all">{{ state.install_command }}</pre>
    <p class="text-xs text-slate-500">{{ state.note }} 安装后启用 Chrome 扩展。日志位于项目「创作/chatgpt_runs」目录。</p>
    <p v-if="state.verification?.note" class="mt-2 text-xs text-amber-200">实测状态：{{state.verification.note}}</p>
    <p v-if="message" class="mt-2 text-xs text-amber-200">{{ message }}</p>
  </section>
</template>
