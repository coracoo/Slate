<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { toast } from '../stores/app'
// 公网图床为主区（独立保存+测试）；签名 URL 出口折叠为高级备选（图床未配/上传失败时的兜底）
const hostType = ref(''), busy = ref(false), testing = ref(false)
const webdavStatic = ref(''), webdavUrl = ref(''), webdavUser = ref(''), webdavPass = ref(''), webdavDir = ref('/images')
const imgurId = ref(''), imgurProxy = ref(''), imgbbKey = ref('')
const cfAccount = ref(''), cfToken = ref('')
const base = ref(''), ttl = ref(86400)
onMounted(async () => {
  try {
    const r = await fetch('/api/media-gateway'); if (!r.ok) throw new Error('读取公网素材出口失败')
    const cfg = await r.json(); base.value = cfg.base_url; ttl.value = cfg.ttl_seconds
    const h = cfg.image_host || {}
    hostType.value = h.type || ''
    webdavStatic.value = h.base_url || ''; webdavUrl.value = h.webdav_url || ''
    webdavUser.value = h.username || ''; webdavPass.value = h.password || ''; webdavDir.value = h.remote_dir || '/images'
    imgurId.value = h.client_id || ''; imgurProxy.value = h.proxy || ''
    imgbbKey.value = h.api_key || ''
    cfAccount.value = h.account_id || ''; cfToken.value = h.api_token || ''
  } catch(e) {toast(String(e),'err')}
})
function hostBody() {
  const trim = (v: string) => v.trim()
  if (hostType.value === 'imgbb') return { type:'imgbb', api_key:trim(imgbbKey.value) }
  if (hostType.value === 'cloudflare') return { type:'cloudflare', account_id:trim(cfAccount.value), api_token:trim(cfToken.value) }
  if (hostType.value === 'imgur') { const h: Record<string, unknown> = { type:'imgur', client_id:trim(imgurId.value) }; if (trim(imgurProxy.value)) h.proxy = trim(imgurProxy.value); return h }
  if (hostType.value === 'webdav') return { type:'webdav', base_url:trim(webdavStatic.value), webdav_url:trim(webdavUrl.value), username:webdavUser.value, password:webdavPass.value, remote_dir:trim(webdavDir.value) || '/images' }
  return null
}
async function saveHost() {
  busy.value = true
  try {
    const body: Record<string, unknown> = {base_url:base.value, ttl_seconds:ttl.value}
    const host = hostBody(); if (host) body.image_host = host; else body.image_host = null
    const r = await fetch('/api/media-gateway',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
    const cfg = await r.json(); if (!r.ok) throw new Error(cfg.err || '保存失败')
    toast('图床配置已保存','ok')
  } catch(e) {toast(e instanceof Error ? e.message : '保存失败','err',6000)} finally {busy.value=false}
}
async function testHost() {
  testing.value = true
  try {
    const host = hostBody()
    if (!host) throw new Error('请先选择图床类型并填写')
    const r = await fetch('/api/media-gateway/test-host',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({host})})
    const res = await r.json()
    if (!res.ok) throw new Error(res.err || '测试失败')
    toast('图床连通 OK 直链：' + (res.url || '').slice(0, 70), 'ok', 7000)
  } catch(e) {toast(e instanceof Error ? e.message : '测试失败','err',7000)} finally {testing.value=false}
}
</script>
<template>
  <section class="glass space-y-3 p-5">
    <h3 class="font-bold text-amber-200">公网素材出口 · 图床</h3>
    <p class="text-xs text-slate-400">参考图（宫格图/关键帧/资产图）生视频时自动上传图床取直链，交给 Agnes 等只认公网 URL 的厂商。图床失败时回落下方签名 URL（若配置）。</p>

    <div class="flex flex-wrap gap-2">
      <button class="btn btn-sm" :class="hostType==='imgbb'?'':'btn-ghost'" title="国内直连可达，免费注册拿 key" @click="hostType='imgbb'">imgbb（国内推荐）</button>
      <button class="btn btn-sm" :class="hostType==='cloudflare'?'':'btn-ghost'" title="Cloudflare Images：account_id + API Token，经 CF CDN 全球可达" @click="hostType='cloudflare'">Cloudflare</button>
      <button class="btn btn-sm" :class="hostType==='imgur'?'':'btn-ghost'" title="标准 Imgur API v3，开源通用；国内需代理" @click="hostType='imgur'">Imgur（需代理）</button>
      <button class="btn btn-sm" :class="hostType==='webdav'?'':'btn-ghost'" title="自建 WebDAV 图床（PicGo 同款参数）" @click="hostType='webdav'">WebDAV（自建）</button>
      <button v-if="hostType" class="btn btn-ghost btn-sm" @click="hostType=''">停用</button>
    </div>

    <div v-if="hostType==='imgbb'" class="space-y-2">
      <label class="block text-sm">imgbb API Key<input v-model="imgbbKey" class="input mt-1 w-full font-mono" placeholder="api.imgbb.com 免费注册获取" /></label>
      <p class="text-2xs text-slate-500">注册：https://api.imgbb.com — 免费额度足够，国内直连。</p>
    </div>
    <div v-if="hostType==='cloudflare'" class="grid gap-2">
      <label class="block text-sm">Account ID<input v-model="cfAccount" class="input mt-1 w-full font-mono" placeholder="Cloudflare Dashboard 右侧 Account ID" /></label>
      <label class="block text-sm">API Token（Images:Edit 权限）<input v-model="cfToken" type="password" class="input mt-1 w-full font-mono" /></label>
      <p class="text-2xs text-slate-500">需账户开通 Images 并启用公共交付；直链形如 imagedelivery.net。</p>
    </div>
    <div v-if="hostType==='imgur'" class="grid gap-2">
      <label class="block text-sm">Client-ID<input v-model="imgurId" class="input mt-1 w-full font-mono" placeholder="api.imgur.com/oauth2/addclient 注册应用获取" /></label>
      <label class="block text-sm">代理（国内必填，Imgur 被墙）<input v-model="imgurProxy" class="input mt-1 w-full font-mono" placeholder="http://127.0.0.1:7890" /></label>
    </div>
    <div v-if="hostType==='webdav'" class="grid gap-2">
      <label class="block text-sm">静态站地址（直链前缀）<input v-model="webdavStatic" class="input mt-1 w-full font-mono" placeholder="https://example.com:8443" /></label>
      <label class="block text-sm">WebDAV 上传地址<input v-model="webdavUrl" class="input mt-1 w-full font-mono" placeholder="http://host:port" /></label>
      <div class="grid grid-cols-2 gap-2">
        <label class="block text-sm">用户名<input v-model="webdavUser" class="input mt-1 w-full" /></label>
        <label class="block text-sm">密码<input v-model="webdavPass" type="password" class="input mt-1 w-full" /></label>
      </div>
      <label class="block text-sm">上传目录<input v-model="webdavDir" class="input mt-1 w-full font-mono" placeholder="/images（按 年/月 自动归档）" /></label>
    </div>

    <div class="flex flex-wrap gap-2">
      <button class="btn" :disabled="busy" @click="saveHost">{{ busy ? '保存中…' : '保存图床配置' }}</button>
      <button class="btn btn-ghost" :disabled="testing || !hostType" @click="testHost">{{ testing ? '测试中…' : '测试图床（上传探测图）' }}</button>
    </div>

    <details class="rounded-xl border border-line-soft bg-black/20 p-3">
      <summary class="cursor-pointer text-sm text-slate-300">高级 · 签名 URL 出口（图床的兜底备选）</summary>
      <div class="mt-2 space-y-2">
        <p class="text-xs-plus leading-relaxed text-slate-400">
          这是什么：把本机工作台的 <code class="text-cyan-200">/api/public-reference</code> 接口暴露到公网的一条转发路径。
          参考图不落任何第三方图床，而是生成一个<b>带签名、限时</b>（默认 24 小时）的直链交给云端厂商拉取——图更隐私，但要求任务执行期间你的电脑可被公网访问。
        </p>
        <p class="text-xs-plus leading-relaxed text-slate-400">
          怎么配置（三步）：<br>
          ① 你需要一个能把公网流量转发到本机 8775 端口的通道——常用做法：云服务器上 nginx 反代 / frp / SSH 端口转发，或路由器端口映射（家宽公网 IP）；<br>
          ② 在下方填那条通道的<b>公网基础地址</b>（域名或 IP + 端口），并确保该地址下的路径
          <code class="text-cyan-200">/api/public-reference</code> 会原样转发到本机 8775 同名路径——<b>必须保留查询参数</b>（签名在参数里），只开放这一个路径即可；<br>
          ③ 保存后，生视频时参考图会自动生成形如
          <code class="text-cyan-200">https://&lt;你的地址&gt;/api/public-reference?project=…&amp;expires=…&amp;signature=…</code>
          的限时链接；图床不可用时自动回落到这里。
        </p>
        <label class="block text-sm">公网基础地址（域名/IP + 端口，可带路径前缀）<input v-model="base" class="input mt-1 w-full font-mono" placeholder="https://media.example.com:8443/previs" /></label>
        <label class="block text-sm">链接有效期（秒，1 小时 – 7 天）<input v-model.number="ttl" class="input ml-2" type="number" min="3600" max="604800" /></label>
        <p class="text-xs text-amber-200">保存≠连通：转发通道没配好的话链接公网拉取会失败。配置后可在「测试图床」失败时的任务日志里看到具体拉取错误。</p>
        <button class="btn btn-sm" :disabled="busy" @click="saveHost">保存（含高级配置）</button>
      </div>
    </details>
  </section>
</template>
