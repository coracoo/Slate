<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { toast } from '../stores/app'
const base = ref(''), ttl = ref(86400), busy = ref(false)
onMounted(async () => {
  try {
    const r = await fetch('/api/media-gateway'); if (!r.ok) throw new Error('读取公网素材出口失败')
    const cfg = await r.json(); base.value = cfg.base_url; ttl.value = cfg.ttl_seconds
  } catch(e) {toast(String(e),'err')}
})
async function save() {
  busy.value = true
  try {
    const r = await fetch('/api/media-gateway',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({base_url:base.value,ttl_seconds:ttl.value})})
    const cfg = await r.json(); if (!r.ok) throw new Error(cfg.err || '保存失败')
    base.value = cfg.base_url; toast('公网素材出口配置已保存','ok')
  } catch(e) {toast(String(e),'err')} finally {busy.value=false}
}
</script>
<template>
  <section class="glass space-y-3 p-5">
    <h3 class="font-bold text-amber-200">公网素材出口</h3>
    <label class="block text-sm">公网基础地址（域名 + 端口 + 可选路径前缀）<input v-model="base" class="input mt-2 w-full" placeholder="https://media.example.com:443/previs" /></label>
    <label class="block text-sm">链接有效期（秒，默认 24 小时）<input v-model.number="ttl" class="input ml-2" type="number" min="3600" max="604800" /></label>
    <p class="text-xs text-slate-400">自动生成：{{ base || '公网基础地址' }}/api/public-reference?…（临时签名）。配置后，本地参考音视频和项目素材通过此地址供云端模型读取。</p>
    <p class="text-xs text-amber-200">请将此前缀下的 /api/public-reference 转发到工作台同名路径；保留查询参数和 Range 请求头，仅开放这个素材读取接口。保存配置不代表外网已连通。工作台须在任务读取期间保持运行。</p>
    <button class="btn" :disabled="busy" @click="save">{{ busy ? '保存中…' : '保存公网出口' }}</button>
  </section>
</template>
