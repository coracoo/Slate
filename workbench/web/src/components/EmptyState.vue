<script setup lang="ts">
// -*- coding: utf-8 -*-
/** 空状态块：未选项目 / 暂无数据等整区空态，glass 风格统一。 */
defineProps<{
  icon?: string
  title: string
  hint?: string
}>()
</script>

<template>
  <div class="glass p-10 text-center text-sm text-slate-500">
    <div v-if="icon" class="mb-2 text-xl">{{ icon }}</div>
    <p>{{ title }}</p>
    <p v-if="hint" class="mt-1 text-xs">{{ hint }}</p>
  </div>
</template>
