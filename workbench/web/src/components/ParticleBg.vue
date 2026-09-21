<script setup lang="ts">
// -*- coding: utf-8 -*-
/** 轻量 Canvas 粒子/光斑动态背景，~60 粒子；prefers-reduced-motion 时静态渲染一帧。 */
import { onMounted, onBeforeUnmount, ref } from 'vue'
import { app } from '../stores/app'

const canvas = ref<HTMLCanvasElement>()
let raf = 0
let ctx: CanvasRenderingContext2D | null = null
let W = 0
let H = 0

interface P {
  x: number
  y: number
  r: number
  vx: number
  vy: number
  hue: number
  alpha: number
}

const HUES = [190, 300, 45, 265, 150]
let parts: P[] = []

function resize() {
  const c = canvas.value
  if (!c) return
  const oldW = W
  const oldH = H
  W = c.width = window.innerWidth
  H = c.height = window.innerHeight
  // 重排既有粒子：等比映射到新画布，窗口拉大后右/下区域也有粒子覆盖
  for (const p of parts) {
    p.x = oldW ? (p.x / oldW) * W : Math.random() * W
    p.y = oldH ? (p.y / oldH) * H : Math.random() * H
  }
  // 静态模式（prefers-reduced-motion）没有 rAF 循环，resize 后需手动重绘一帧
  if (app.reducedMotion) draw(true)
}

function init() {
  parts = Array.from({ length: 60 }, () => ({
    x: Math.random() * W,
    y: Math.random() * H,
    r: 0.8 + Math.random() * 2.4,
    vx: (Math.random() - 0.5) * 0.25,
    vy: (Math.random() - 0.5) * 0.25,
    hue: HUES[Math.floor(Math.random() * HUES.length)],
    alpha: 0.15 + Math.random() * 0.45
  }))
}

function draw(staticOnly = false) {
  if (!ctx) return
  ctx.clearRect(0, 0, W, H)
  // 大光斑
  for (let i = 0; i < 3; i++) {
    const g = ctx.createRadialGradient(
      W * (0.2 + i * 0.3),
      H * (0.25 + (i % 2) * 0.5),
      0,
      W * (0.2 + i * 0.3),
      H * (0.25 + (i % 2) * 0.5),
      Math.max(W, H) * 0.35
    )
    g.addColorStop(0, `hsla(${HUES[i * 2]}, 80%, 55%, 0.06)`)
    g.addColorStop(1, 'transparent')
    ctx.fillStyle = g
    ctx.fillRect(0, 0, W, H)
  }
  for (const p of parts) {
    ctx.beginPath()
    ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2)
    ctx.fillStyle = `hsla(${p.hue}, 85%, 65%, ${p.alpha})`
    ctx.fill()
    if (!staticOnly) {
      p.x += p.vx
      p.y += p.vy
      if (p.x < -10) p.x = W + 10
      if (p.x > W + 10) p.x = -10
      if (p.y < -10) p.y = H + 10
      if (p.y > H + 10) p.y = -10
    }
  }
}

function loop() {
  draw()
  raf = requestAnimationFrame(loop)
}

onMounted(() => {
  const c = canvas.value
  if (!c) return
  ctx = c.getContext('2d')
  resize()
  init()
  window.addEventListener('resize', resize)
  if (app.reducedMotion) draw(true)
  else loop()
})

onBeforeUnmount(() => {
  cancelAnimationFrame(raf)
  window.removeEventListener('resize', resize)
})
</script>

<template>
  <canvas
    ref="canvas"
    class="pointer-events-none fixed inset-0 z-0"
    aria-hidden="true"
  />
</template>
