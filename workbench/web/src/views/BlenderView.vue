<script setup lang="ts">
// -*- coding: utf-8 -*-
/** Blender 安装与配置：状态自检 + 安装指引（Blender / Blender MCP）+ 工作台一键流程说明。 */
import { ref, onMounted } from 'vue'
import { fetchEnv, type EnvInfo } from '../api'
import { toast } from '../stores/app'
import { icons } from '../components/icons'

const GLOW = 'rgba(251,146,60,0.35)'

const env = ref<EnvInfo | null>(null)
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

onMounted(detect)

const FLOW = [
  { n: '①', t: '分镜 JSON', d: '在「① 拉片结构 / ⑤ 创作生成」产出 dialogue 契约分镜（显式 pos/look 站位）' },
  { n: '②', t: '生成构建脚本', d: '3D 白模页一键调用 blender_previs.py，生成 gen_<slug>.py' },
  { n: '③', t: 'MCP 构建场景', d: '脚本经 Blender MCP(127.0.0.1:9876) 在 Blender 里执行，自动存盘 .blend' },
  { n: '④', t: '产出与渲染', d: '.blend 可本机打开查看；「CLI 渲染」后台出 .mp4 供校对白模' }
]
</script>

<template>
  <div class="page">
    <header class="mb-6">
      <h1 class="grad-text text-2xl font-black">⑤ Blender 配置</h1>
      <p class="mt-1 text-xs text-slate-500">3D 白模链路依赖本机 Blender + Blender MCP；本页引导从零装到能用</p>
    </header>

    <!-- 顶部状态卡 -->
    <section class="glass mb-5 flex flex-wrap items-center gap-4 p-5" :style="{ '--glow': GLOW }">
      <template v-if="env">
        <div class="flex items-center gap-2.5">
          <span
            class="flex h-10 w-10 items-center justify-center rounded-xl text-white"
            :style="{ background: env.blender ? 'linear-gradient(130deg,#fb923c,#fbbf24)' : 'linear-gradient(130deg,#475569,#334155)' }"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path :d="icons.box3d" stroke-linecap="round" stroke-linejoin="round" />
            </svg>
          </span>
          <div>
            <div class="text-2xs tracking-wider text-slate-500">BLENDER</div>
            <div class="max-w-72 truncate text-sm font-bold" :class="env.blender ? 'text-slate-100' : 'text-rose-300'">
              {{ env.blender || '未安装' }}
            </div>
          </div>
        </div>
        <div class="flex items-center gap-2.5">
          <span class="h-2.5 w-2.5 rounded-full" :class="env.mcp ? 'bg-emerald-400 pulse-dot' : 'bg-rose-400'"></span>
          <div>
            <div class="text-2xs tracking-wider text-slate-500">BLENDER MCP</div>
            <div class="text-sm font-bold" :class="env.mcp ? 'text-emerald-300' : 'text-rose-300'">
              {{ env.mcp ? '在线' : '离线' }} · 127.0.0.1:9876
            </div>
          </div>
        </div>
      </template>
      <div v-else class="text-sm text-slate-500">{{ detecting ? '检测中…' : '状态不可用' }}</div>
      <div class="flex-1"></div>
      <button class="btn btn-ghost btn-sm" :disabled="detecting" @click="detect">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path :d="icons.refresh" stroke-linecap="round" stroke-linejoin="round" />
        </svg>
        {{ detecting ? '检测中…' : '重新检测' }}
      </button>
    </section>

    <!-- 步骤 1：安装 Blender -->
    <section class="glass glass-hover mb-5 p-5" :style="{ '--glow': GLOW }">
      <div class="flex items-start gap-4">
        <span
          class="flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-base font-black text-slate-950"
          style="background: linear-gradient(130deg, #fb923c, #fbbf24)"
        >1</span>
        <div class="min-w-0 flex-1">
          <h2 class="text-base font-bold text-slate-100">安装 Blender</h2>
          <p class="mt-1.5 text-xs leading-relaxed text-slate-400">
            官网
            <a class="mx-1 font-semibold text-orange-300 underline" href="https://www.blender.org/download/" target="_blank" rel="noopener">blender.org/download</a>
            下载 Windows 版（.msi 安装 或 .zip 解压即用）。本机示例路径
            <code class="rounded bg-orange-400/10 px-1.5 py-0.5 font-mono text-orange-200">J:\Blender 5.2\blender.exe</code>。
          </p>
          <p class="mt-1.5 text-xs leading-relaxed text-slate-500">
            工作台会自动探测常见安装路径（硬编码候选 + 全局通配），装好后点上方「重新检测」即可识别；
            若装在了非常规位置导致探测不到，「CLI 渲染」会提示未找到 blender.exe。
          </p>
        </div>
      </div>
    </section>

    <!-- 步骤 2：安装 Blender MCP -->
    <section class="glass glass-hover mb-5 p-5" :style="{ '--glow': GLOW }">
      <div class="flex items-start gap-4">
        <span
          class="flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-base font-black text-slate-950"
          style="background: linear-gradient(130deg, #fbbf24, #fde68a)"
        >2</span>
        <div class="min-w-0 flex-1">
          <h2 class="text-base font-bold text-slate-100">安装 Blender MCP 插件</h2>
          <p class="mt-1.5 text-xs leading-relaxed text-slate-400">
            开源项目
            <a class="mx-1 font-semibold text-orange-300 underline" href="https://github.com/ahujasid/blender-mcp" target="_blank" rel="noopener">github.com/ahujasid/blender-mcp</a>
            —— 由 Blender 侧插件（addon.py）+ 本机 MCP 服务器（<code class="rounded bg-orange-400/10 px-1 font-mono text-orange-200">uvx blender-mcp</code>）组成。
          </p>
          <ol class="mt-3 space-y-2.5">
            <li class="flex items-start gap-2.5">
              <span class="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-orange-400/15 text-2xs font-black text-orange-300">①</span>
              <p class="text-xs leading-relaxed text-slate-400">
                Blender → <b class="text-slate-200">Edit &gt; Preferences &gt; Add-ons</b> → 右上角下拉选
                <b class="text-slate-200">Install from Disk…</b>，选中仓库里的
                <code class="rounded bg-orange-400/10 px-1 font-mono text-orange-200">addon.py</code>，然后在列表里勾选启用 BlenderMCP。
              </p>
            </li>
            <li class="flex items-start gap-2.5">
              <span class="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-orange-400/15 text-2xs font-black text-orange-300">②</span>
              <p class="text-xs leading-relaxed text-slate-400">
                在 3D 视图按 <b class="text-slate-200">N</b> 打开侧栏 → <b class="text-slate-200">BlenderMCP</b> 面板 → 点
                <b class="text-slate-200">Connect to Claude</b> 启动插件内的 9876 端口监听。
                本工作台直接连这个端口，<b class="text-orange-300">不需要安装/运行 Claude</b>。
              </p>
            </li>
            <li class="flex items-start gap-2.5">
              <span class="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-orange-400/15 text-2xs font-black text-orange-300">③</span>
              <p class="text-xs leading-relaxed text-slate-400">
                回到本页点「重新检测」，Blender MCP 圆点变绿（在线）即就绪；3D 白模页的「✨ 生成 3D 场景」即可一键构建。
              </p>
            </li>
          </ol>
        </div>
      </div>
    </section>

    <!-- 步骤 3：工作台怎么用它 -->
    <section class="glass glass-hover p-5" :style="{ '--glow': GLOW }">
      <div class="flex items-start gap-4">
        <span
          class="flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-base font-black text-slate-950"
          style="background: linear-gradient(130deg, #f97316, #fb923c)"
        >3</span>
        <div class="min-w-0 flex-1">
          <h2 class="text-base font-bold text-slate-100">工作台怎么用它</h2>
          <p class="mt-1.5 text-xs leading-relaxed text-slate-400">
            前往
            <RouterLink to="/white3d" class="mx-1 font-semibold text-orange-300 underline">⑥ 3D 白模</RouterLink>
            页，整条链路一键完成：
          </p>
          <div class="mt-3 grid grid-cols-1 gap-2.5 sm:grid-cols-2 lg:grid-cols-4">
            <div
              v-for="f in FLOW"
              :key="f.n"
              class="rounded-xl border border-line-soft bg-black/25 p-3"
            >
              <div class="text-sm font-black text-orange-300">{{ f.n }} {{ f.t }}</div>
              <p class="mt-1 text-xs-plus leading-relaxed text-slate-500">{{ f.d }}</p>
            </div>
          </div>
        </div>
      </div>
    </section>
  </div>
</template>

