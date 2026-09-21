<script setup lang="ts">
import { ref } from 'vue'
import { trackJob } from '../stores/jobs'
import { toast } from '../stores/app'
const emit = defineEmits<{ installed: [] }>()
const selected = ref(['base'])
const busy = ref(false)
const log = ref('')
const choices = [
  { id: 'base', title: '基础依赖（锁定版本）' },
  { id: 'depth', title: '深度 / 剪影（PyTorch、Transformers、rembg）' },
  { id: 'chatgpt', title: 'ChatGPT 本机程序（image-use / chrome-use）' },
]
async function install() {
  busy.value = true
  log.value = ''
  try {
    const response = await fetch('/api/env/install', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ groups: selected.value }) })
    const data = await response.json()
    if (!response.ok) throw new Error(data.err || '安装提交失败')
    toast('安装任务已提交，可在任务面板查看实时日志', 'ok')
    const result = await trackJob(data.id, '批量安装环境')
    log.value = result.out || result.err || '任务已结束'
    emit('installed')
  } catch (error) { toast(String(error), 'err'); log.value = String(error) }
  finally { busy.value = false }
}
</script>
<template>
  <section class="glass mb-5 p-5">
    <h3 class="font-bold text-sky-200">批量安装环境</h3>
    <p class="my-3 text-xs leading-relaxed text-slate-400">安装到项目 .venv（Python 3.12）。安装期间请勿提交生成任务；基础依赖安装后重启前台服务。深度依赖下载较大，GPU 专用版本需按设备配置。Chrome 扩展授权与登录需手动完成。</p>
    <div class="flex flex-wrap gap-4">
      <label v-for="choice in choices" :key="choice.id" class="flex items-center gap-2 text-sm">
        <input v-model="selected" type="checkbox" :value="choice.id" :disabled="busy">{{ choice.title }}
      </label>
    </div>
    <button class="btn mt-4" :disabled="busy || !selected.length" @click="install">{{ busy ? '安装中…' : '后台安装所选环境' }}</button>
    <pre v-if="log" class="mt-3 max-h-60 overflow-auto whitespace-pre-wrap text-xs">{{ log }}</pre>
  </section>
</template>
