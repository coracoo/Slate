<script setup lang="ts">
// -*- coding: utf-8 -*-
/** ④ 深度：motion_depth 参数化运行，产物并排对比 + depth_verify 说明。 */
import { ref, computed, watch } from 'vue'
import { runGeneric, mediaUrl } from '../api'
import { app, projectVideos, materialVideos, toast, loadBasics } from '../stores/app'
import { trackJob } from '../stores/jobs'
import StyledSelect from '../components/StyledSelect.vue'
import DelBadge from '../components/DelBadge.vue'
import EmptyState from '../components/EmptyState.vue'
import { icons } from '../components/icons'

const video = ref('')
const enhance = ref<'none' | 'stretch' | 'clahe'>('none')
const color = ref<'none' | 'inferno'>('none')
const running = ref(false)
const doneOut = ref('')

/** 源视频：当前项目 拉片素材/ 唯一来源。 */
const materialList = computed(() => materialVideos())
const videoOptions = computed(() => materialList.value.map((v) => v.rel))
const videoLabels = computed(() => Object.fromEntries(materialList.value.map((v) => [v.rel, v.file])))
watch([videoOptions, video], () => {
  if (!video.value && videoOptions.value.length) video.value = videoOptions.value.at(-1) || '' // 默认选最后一个素材视频（含项目切换回填）
}, { immediate: true })

/** 历史深度产物：全项目树扫描（产物展示，非源选择）。 */
const videos = computed(() => projectVideos())

const depthProducts = computed(() =>
  videos.value.filter((v) => /深度图.*\.mp4$/i.test(v.file))
)

/** 预期产物路径：深度产物独立放 深度/ 目录（不与白模混放）。 */
function expectedOut(): string {
  const v = video.value
  const base = v.replace(/\.(mp4|mov|mkv)$/i, '')
  const dir = base.replace('/拉片素材/', '/深度/').replace('/成片/', '/深度/').replace('/白模/', '/深度/')
  return `${dir}_深度图.mp4`
}

async function run() {
  if (!app.current || !video.value) {
    toast('请先选择源视频', 'err')
    return
  }
  running.value = true
  doneOut.value = ''
  const args = [
    video.value,
    '--start', '0',
    '--end', '9999',
    '--fps', '12',
    '--enhance', enhance.value
  ]
  if (color.value !== 'none') args.push('--color', color.value)
  args.push('--out', expectedOut())
  try {
    const r = await runGeneric({ step: 'depth', args })
    if (!r.id) throw new Error(r.err || '任务未启动')
    toast(`深度任务 #${r.id} 已启动`, 'ok')
    const j = await trackJob(r.id, `深度图 ${enhance.value}/${color.value}`)
    if (j.success) {
      // 从任务日志解析真实产物路径（motion_depth 末行打印 "DEPTH -> <path>"），不依赖前端猜测
      const m = (j.out || '').match(/DEPTH ->\s*(\S+?\.mp4)/i)
      if (m) {
        doneOut.value = m[1].replace(/^[A-Za-z]:[\\/].*?projects[\\/]/, 'projects/').replace(/\\/g, '/')
      } else {
        doneOut.value = expectedOut()
      }
      toast('深度图生成完成', 'ok')
      await loadBasics() // 刷新项目树，让「历史深度产物」立即出现
    } else {
      toast('生成失败，详情见任务抽屉', 'err')
    }
  } catch (e) {
    toast(e instanceof Error ? e.message : '启动失败', 'err')
  } finally {
    running.value = false
  }
}

watch(() => app.current, () => {
  video.value = ''
  doneOut.value = ''
})
</script>

<template>
  <div class="page">
    <header class="mb-6">
      <h1 class="grad-text text-2xl font-black">④ 深度动作</h1>
      <p class="mt-1 text-xs text-slate-500">单目深度（近亮远暗）用于校准走位 / 动作 / 景别；默认忠实模型原始输出</p>
    </header>

    <!-- 参数条 -->
    <div class="glass mb-5 flex flex-wrap items-end gap-3 p-4">
      <label class="min-w-56 text-xs text-slate-400">
        源视频（项目 拉片素材/）
        <StyledSelect
          v-model="video"
          class="mt-1"
          :options="videoOptions"
          :labels="videoLabels"
          :storage-key="`wb.${app.current}.depth.video`"
          placeholder="— 选择素材视频 —"
        />
      </label>
      <p v-if="app.current && !materialList.length" class="mb-2 self-center rounded-lg bg-amber-400/10 px-3 py-1.5 text-xs-plus text-amber-300">
        素材为空：先到首页「从 Downloads 导入」
      </p>
      <label class="w-40 text-xs text-slate-400">
        对比度增强
        <StyledSelect
          v-model="enhance"
          class="mt-1"
          :options="['none', 'stretch', 'clahe']"
          :labels="{ none: 'none（忠实原始）', stretch: 'stretch（轻微拉伸）', clahe: 'clahe（自适应均衡）' }"
          storage-key="wb.depth.enhance"
        />
      </label>
      <label class="w-36 text-xs text-slate-400">
        色彩映射
        <StyledSelect
          v-model="color"
          class="mt-1"
          :options="['none', 'inferno']"
          :labels="{ none: 'none（灰度）', inferno: 'inferno（伪彩）' }"
          storage-key="wb.depth.color"
        />
      </label>
      <button class="btn" :disabled="running || !video" @click="run">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path :d="icons.wave" stroke-linecap="round" stroke-linejoin="round"/></svg>
        {{ running ? '生成中…' : '生成深度图' }}
      </button>
      <div class="flex-1"></div>
      <p class="max-w-xs text-2xs leading-relaxed text-slate-500">
        单目深度在转场叠化、剧烈运镜处可能失真；验证用 depth_verify.py 看亮度分布与前后景分离度。
      </p>
    </div>

    <EmptyState v-if="!app.current" title="请先在左侧选择项目" />

    <!-- 并排对比 -->
    <div v-else-if="video" class="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <div class="glass overflow-hidden">
        <div class="border-b border-line-soft px-4 py-2 text-xs font-bold text-slate-300">原片</div>
        <video :src="mediaUrl(video)" controls class="aspect-video w-full bg-black" preload="metadata"></video>
      </div>
      <div class="glass overflow-hidden">
        <div class="border-b border-line-soft px-4 py-2 text-xs font-bold text-slate-300">
          深度图
          <span v-if="doneOut" class="ml-2 rounded-full bg-violet-400/15 px-2 py-0.5 text-2xs text-violet-300">新生成 ✓</span>
        </div>
        <video
          v-if="doneOut || depthProducts.find((d) => d.path === expectedOut())"
          :src="mediaUrl(doneOut || expectedOut())"
          controls class="aspect-video w-full bg-black" preload="metadata"
        ></video>
        <div v-else class="flex aspect-video flex-col items-center justify-center bg-black/40 text-xs text-slate-500">
          <p>该视频还没有深度产物</p>
          <p class="mt-1 text-2xs">设置参数后点「生成深度图」</p>
        </div>
      </div>
    </div>

    <!-- 历史产物 -->
    <section v-if="depthProducts.length" class="mt-6">
      <h3 class="mb-2 text-xs font-bold text-slate-500">已有深度产物（项目树 深度图*.mp4）</h3>
      <div class="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <div v-for="d in depthProducts" :key="d.path" class="glass glass-hover group relative overflow-hidden" :style="{ '--glow': 'rgba(167,139,250,0.35)' }">
          <DelBadge :path="d.path.replace(`projects/${app.current}/`, '')" :label="d.file"
            @deleted="(p) => { if (doneOut === 'projects/' + app.current + '/' + p) doneOut = '' }" />
          <video :src="mediaUrl(d.path)" controls class="aspect-video w-full bg-black" preload="metadata"></video>
          <div class="flex items-center justify-between p-2.5">
            <span class="truncate text-xs-plus text-slate-300" :title="d.path">{{ d.file }}</span>
            <a class="btn btn-ghost btn-sm shrink-0" :href="mediaUrl(d.path)" :download="d.file">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path :d="icons.download" stroke-linecap="round" stroke-linejoin="round"/></svg>
            </a>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>
