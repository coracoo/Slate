// -*- coding: utf-8 -*-
import { createRouter, createWebHistory } from 'vue-router'

export const routes = [
  {
    path: '/login',
    name: 'login',
    component: () => import('./views/LoginView.vue'),
    meta: { title: '登录', c1: '#22d3ee', c2: '#818cf8', icon: 'home' }
  },
  {
    path: '/',
    name: 'home',
    component: () => import('./views/HomeView.vue'),
    meta: { title: '首页', c1: '#22d3ee', c2: '#818cf8', icon: 'home' }
  },
  {
    path: '/lapian',
    name: 'lapian',
    component: () => import('./views/LapianView.vue'),
    meta: { title: '① 拉片结构', c1: '#e879f9', c2: '#f472b6', icon: 'clapper' }
  },
  {
    path: '/lines',
    name: 'lines',
    component: () => import('./views/LinesView.vue'),
    meta: { title: '② 台词分析', c1: '#fbbf24', c2: '#fb923c', icon: 'chat' }
  },
  {
    path: '/frames',
    name: 'frames',
    component: () => import('./views/FramesView.vue'),
    meta: { title: '③ 逐帧拉片', c1: '#34d399', c2: '#22d3ee', icon: 'frames' }
  },
  {
    path: '/depth',
    name: 'depth',
    component: () => import('./views/DepthView.vue'),
    meta: { title: '④ 深度动作', c1: '#a78bfa', c2: '#818cf8', icon: 'wave' }
  },
  {
    path: '/acting',
    name: 'acting',
    component: () => import('./views/ActingView.vue'),
    meta: { title: '⑤ 演员表现', c1: '#f59e0b', c2: '#ec4899', icon: 'user' }
  },
  {
    path: '/white',
    name: 'white',
    component: () => import('./views/WhiteView.vue'),
    meta: { title: '① 辅助·白模', c1: '#38bdf8', c2: '#34d399', icon: 'cube' }
  },
  {
    path: '/white3d',
    name: 'white3d',
    component: () => import('./views/White3dView.vue'),
    meta: { title: '② 辅助·Blender', c1: '#22d3ee', c2: '#818cf8', icon: 'box3d' }
  },
  {
    path: '/studio',
    name: 'studio',
    component: () => import('./views/StudioView.vue'),
    meta: { title: '① 剧本生成', c1: '#ec4899', c2: '#f472b6', icon: 'chat' }
  },
  {
    path: '/studio/shots',
    name: 'studioShots',
    component: () => import('./views/StudioShotsView.vue'),
    meta: { title: '③ 分镜生成', c1: '#f472b6', c2: '#e879f9', icon: 'clapper' }
  },
  {
    path: '/studio/asset',
    name: 'studioAsset',
    component: () => import('./views/StudioAssetView.vue'),
    meta: { title: '② 素材生成', c1: '#a78bfa', c2: '#ec4899', icon: 'box3d' }
  },
  {
    path: '/studio/asset/voices', name: 'voiceAssets', component: () => import('./views/VoiceAssetsView.vue'),
    meta: { title: '角色音色', c1: '#a78bfa', c2: '#ec4899', icon: 'box3d' }
  },
  {
    path: '/create/free', name: 'freeCreate', component: () => import('./views/CreateView.vue'),
    meta: { title: '自由创作与画廊', c1: '#a78bfa', c2: '#ec4899', icon: 'clapper' }
  },
  {
    path: '/blender',
    name: 'blender',
    component: () => import('./views/BlenderView.vue'),
    meta: { title: '⑤ Blender 配置', c1: '#fb923c', c2: '#fbbf24', icon: 'wrench' }
  },
  {
    path: '/explain',
    name: 'explain',
    component: () => import('./views/ExplainView.vue'),
    meta: { title: '⑤ 镜头讲解', c1: '#a78bfa', c2: '#e879f9', icon: 'book' }
  },
  {
    path: '/skills',
    name: 'skills',
    component: () => import('./views/SkillsView.vue'),
    meta: { title: '③ Skill 配置', c1: '#8b5cf6', c2: '#ec4899', icon: 'book' }
  },
  {
    path: '/env',
    name: 'env',
    component: () => import('./views/EnvView.vue'),
    meta: { title: '④ 环境检查', c1: '#f59e0b', c2: '#fbbf24', icon: 'server' }
  },
  {
    path: '/billing',
    name: 'billing',
    component: () => import('./views/BillingView.vue'),
    meta: { title: '用量计费', c1: '#34d399', c2: '#22d3ee', icon: 'chart' }
  },
  {
    path: '/package',
    name: 'package',
    component: () => import('./views/PackageView.vue'),
    meta: { title: '⑥ 平面推演', c1: '#22d3ee', c2: '#6ee7b7', icon: 'box3d' }
  },
  {
    path: '/create',
    name: 'create',
    component: () => import('./views/ProductionStudioView.vue'),
    meta: { title: '⑦ 创作生成', c1: '#ec4899', c2: '#f472b6', icon: 'wand' }
  }
]

export const router = createRouter({
  history: createWebHistory(),
  routes
})

router.afterEach((to) => {
  document.title = `${(to.meta.title as string) || ''} · AI 短片分析工作台`
})

