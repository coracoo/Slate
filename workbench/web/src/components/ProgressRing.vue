<script setup lang="ts">
// -*- coding: utf-8 -*-
/** SVG 进度环：stroke-dasharray 动画，用于模块完成度。 */
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{ value: number; size?: number; color?: string; label?: string }>(),
  { size: 56, color: '#22d3ee', label: '' }
)

const r = computed(() => props.size / 2 - 5)
const circ = computed(() => 2 * Math.PI * r.value)
const dash = computed(() => circ.value * Math.max(0, Math.min(1, props.value)))
</script>

<template>
  <div class="relative inline-flex items-center justify-center" :style="{ width: size + 'px', height: size + 'px' }">
    <svg :width="size" :height="size" class="-rotate-90">
      <circle
        :cx="size / 2" :cy="size / 2" :r="r" fill="none"
        stroke="rgba(148,163,184,0.15)" :stroke-width="5"
      />
      <circle
        :cx="size / 2" :cy="size / 2" :r="r" fill="none"
        :stroke="color" :stroke-width="5" stroke-linecap="round"
        :stroke-dasharray="`${dash} ${circ}`"
        :style="{
          transition: 'stroke-dasharray 0.8s cubic-bezier(0.22, 1, 0.36, 1)',
          filter: `drop-shadow(0 0 4px ${color})`
        }"
      />
    </svg>
    <span class="absolute text-2xs font-bold text-slate-200">{{ Math.round(value * 100) }}%</span>
    <span v-if="label" class="absolute -bottom-4 whitespace-nowrap text-2xs text-slate-400">{{ label }}</span>
  </div>
</template>
