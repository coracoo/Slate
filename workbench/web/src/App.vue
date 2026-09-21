<script setup lang="ts">
// -*- coding: utf-8 -*-
import { computed, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import ParticleBg from './components/ParticleBg.vue'
import JobDrawer from './components/JobDrawer.vue'
import { icons } from './components/icons'
import { loadBasics, app, toasts, selectProject, currentProject } from './stores/app'

const route = useRoute()
const theme = computed(() => ({
  c1: (route.meta.c1 as string) || '#22d3ee',
  c2: (route.meta.c2 as string) || '#818cf8'
}))

const navItems = [
  { key: 'home', to: '/', label: '首页', icon: 'home', group: '' },
  { key: 'studio', to: '/studio', label: '① 剧本生成', icon: 'chat', group: '制作' },
  { key: 'studioShots', to: '/studio/shots', label: '② 分镜生成', icon: 'clapper', group: '制作' },
  { key: 'studioAsset', to: '/studio/asset', label: '③ 素材生成', icon: 'box3d', group: '制作' },
  { key: 'voices', to: '/studio/asset/voices', label: '④ 音色绑定', icon: 'wave', group: '制作' },
  { key: 'acting', to: '/acting', label: '⑤ 演员表现', icon: 'user', group: '制作' },
  { key: 'package', to: '/package', label: '⑥ 平面推演', icon: 'box3d', group: '制作' },
  { key: 'create', to: '/create', label: '⑦ 创作生成', icon: 'wand', group: '制作' },
  { key: 'free', to: '/create/free', label: '⑧ 自由创作', icon: 'wand', group: '制作' },
  { key: 'lapian', to: '/lapian', label: '① 拉片结构', icon: 'clapper', group: '拉片' },
  { key: 'lines', to: '/lines', label: '② 台词分析', icon: 'chat', group: '拉片' },
  { key: 'frames', to: '/frames', label: '③ 逐帧拉片', icon: 'frames', group: '拉片' },
  { key: 'depth', to: '/depth', label: '④ 深度动作', icon: 'wave', group: '拉片' },
  { key: 'explain', to: '/explain', label: '⑤ 镜头讲解', icon: 'book', group: '拉片' },
  { key: 'white', to: '/white', label: '① 辅助·白模', icon: 'cube', group: '系统' },
  { key: 'white3d', to: '/white3d', label: '② 辅助·Blender', icon: 'box3d', group: '系统' },
  { key: 'skills', to: '/skills', label: '③ Skill 配置', icon: 'book', group: '系统' },
  { key: 'env', to: '/env', label: '④ 环境检查', icon: 'server', group: '系统' },
  { key: 'blender', to: '/blender', label: '⑤ Blender 配置', icon: 'wrench', group: '系统' }
]

watch(
  theme,
  (t) => {
    const el = document.documentElement
    el.style.setProperty('--accent', t.c1)
    el.style.setProperty('--c1', t.c1)
    el.style.setProperty('--c2', t.c2)
  },
  { immediate: true }
)

const toastColor = { ok: '#34d399', err: '#f87171', info: '#38bdf8' } as const

onMounted(loadBasics)
</script>

<template>
  <ParticleBg />
  <div class="relative z-10 flex h-full">
    <!-- 侧边栏 -->
    <aside
      class="flex w-56 shrink-0 flex-col border-r border-line-soft bg-black/30 backdrop-blur-xl"
    >
      <div class="px-5 pb-4 pt-6">
        <div class="flex items-center gap-2.5">
          <div
            class="flex h-9 w-9 items-center justify-center rounded-xl text-white shadow-lg"
            :style="{ background: `linear-gradient(130deg, ${theme.c1}, ${theme.c2})` }"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path :d="icons.film" stroke-linecap="round" stroke-linejoin="round"/></svg>
          </div>
          <div>
            <div class="text-sm font-extrabold tracking-wide text-slate-100">场记 Slate</div>
            <div class="text-2xs tracking-[0.2em] text-slate-500">SLATE · PREVIS WORKBENCH</div>
          </div>
        </div>
      </div>

      <nav class="flex-1 space-y-1 px-3">
        <template v-for="r in navItems" :key="r.key">
          <div v-if="r.group && r.group !== (navItems[navItems.indexOf(r)-1]?.group || '')"
            class="mt-3 mb-1 px-3 text-2xs font-black tracking-[0.25em] text-slate-500">{{ r.group }}</div>
          <RouterLink
          :to="r.to"
          v-slot="{ isActive, navigate }"
          custom
        >
          <button
            class="group flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left text-[13px] transition-all duration-200"
            :class="isActive
              ? 'font-bold text-white shadow-lg'
              : 'text-slate-400 hover:bg-white/5 hover:text-slate-200'"
            :style="isActive ? { background: `linear-gradient(110deg, color-mix(in srgb, ${theme.c1} 26%, transparent), color-mix(in srgb, ${theme.c2} 14%, transparent))`, boxShadow: `inset 2px 0 0 ${theme.c1}` } : {}"
            @click="navigate"
          >
            <svg
              width="16" height="16" viewBox="0 0 24 24" fill="none"
              stroke="currentColor" stroke-width="2"
              :style="isActive ? { color: theme.c1 } : {}"
              class="transition-transform duration-200 group-hover:scale-110"
            ><path :d="icons[r.icon]" stroke-linecap="round" stroke-linejoin="round"/></svg>
            {{ r.label }}
          </button>
        </RouterLink>
        </template>
      </nav>

      <!-- 当前项目 -->
      <div class="border-t border-line-soft p-3">
        <label class="mb-1 block text-2xs text-slate-500">当前项目</label>
        <select class="select" :value="app.current" @change="selectProject(($event.target as HTMLSelectElement).value)">
          <option value="" disabled>— 选择项目 —</option>
          <option v-for="p in app.projects" :key="p.name" :value="p.name">{{ p.name }}</option>
        </select>
        <div v-if="currentProject" class="mt-2 truncate text-2xs text-slate-500" :title="currentProject.name">
          projects/{{ currentProject.name }}/
        </div>
      </div>
    </aside>

    <!-- 主区 -->
    <main class="flex-1 overflow-y-auto">
      <RouterView v-slot="{ Component }">
        <!-- 页面可能包含 Teleport/多根节点（创作页的 OverlayViewer），不包 Transition，
             避免 Vue 在多根组件切换时停留在离场状态。 -->
        <component :is="Component" :key="route.path" />
      </RouterView>
    </main>
  </div>

  <JobDrawer />

  <!-- Toast -->
  <Teleport to="body">
    <div aria-live="polite" class="pointer-events-none fixed left-1/2 top-5 z-[10000] flex max-h-[85vh] w-[min(90vw,720px)] -translate-x-1/2 flex-col items-center gap-2 overflow-y-auto">
      <TransitionGroup name="fade-slide">
        <div
          v-for="t in toasts"
          :key="t.id"
          :role="t.kind === 'err' ? 'alert' : 'status'"
          class="glass pointer-events-auto flex max-w-full items-center gap-2 whitespace-pre-wrap break-words px-4 py-2 text-sm font-semibold text-slate-100"
          :style="{ boxShadow: `0 8px 30px -8px ${toastColor[t.kind]}`, borderColor: `${toastColor[t.kind]}55` }"
        >
          <span class="h-2 w-2 rounded-full" :style="{ background: toastColor[t.kind] }" />
          {{ t.text }}
        </div>
      </TransitionGroup>
    </div>
  </Teleport>
</template>

