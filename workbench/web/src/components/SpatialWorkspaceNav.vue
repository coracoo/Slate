<script setup lang="ts">
defineProps<{ active: 'plan' | 'white' | 'white3d' }>()

const views = [
  { key: 'plan', to: '/package', label: '2D 平面', purpose: '站位 · 轴线 · 路径' },
  { key: 'white', to: '/white', label: '动态白模', purpose: '动作 · 镜头节奏' },
  { key: 'white3d', to: '/white3d', label: '3D 机位', purpose: '遮挡 · 景别 · 视线' },
] as const
</script>

<template>
  <header class="mb-4">
    <div class="mb-2 flex flex-wrap items-baseline gap-2">
      <h1 class="text-lg font-bold text-slate-100">镜头空间验证</h1>
      <span class="rounded bg-white/5 px-2 py-0.5 text-xs text-slate-400">可选</span>
      <span class="text-xs text-slate-400">有空间疑问时，再用预演核对。</span>
    </div>
    <nav aria-label="空间验证视图" class="flex flex-wrap gap-2">
      <RouterLink v-for="view in views" :key="view.key" :to="view.to"
        :aria-current="active === view.key ? 'page' : undefined"
        class="rounded-lg border px-3 py-2 text-sm transition"
        :class="active === view.key ? 'border-sky-400/40 bg-sky-400/15 text-sky-200' : 'border-line-soft bg-white/5 text-slate-400 hover:text-slate-200'">
        <span class="font-bold">{{ view.label }}</span>
        <span class="ml-2 text-xs">{{ view.purpose }}</span>
      </RouterLink>
    </nav>
    <p class="mt-2 text-xs text-slate-500">按分镜与场景设定作示意；自动布局、陈设和机位需要人工核对，不能作为原片精确复原的证据。</p>
  </header>
</template>
