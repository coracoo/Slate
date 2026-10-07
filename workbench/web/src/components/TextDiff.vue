<script setup lang="ts">
import { computed } from 'vue'
import { textDiff } from '../utils/textDiff'
const props = withDefaults(defineProps<{ before: string; after: string; beforeLabel?: string; afterLabel?: string }>(), { beforeLabel: '原稿', afterLabel: '候选稿' })
const diff = computed(() => textDiff(props.before, props.after))
</script>

<template>
  <div class="text-diff" aria-label="修改前后差异">
    <section><h4>{{beforeLabel}} <span>红色为删除</span></h4><pre><template v-for="(part, i) in diff" :key="i"><span v-if="part.kind !== 'add'" :class="part.kind">{{ part.text }}</span></template></pre></section>
    <section><h4>{{afterLabel}} <span>绿色为新增</span></h4><pre><template v-for="(part, i) in diff" :key="i"><span v-if="part.kind !== 'remove'" :class="part.kind">{{ part.text }}</span></template></pre></section>
  </div>
</template>

<style scoped>
.text-diff { display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:1rem }
h4 { color:#e2e8f0;font-weight:600;margin-bottom:.5rem } h4 span { font-size:.75rem;color:#94a3b8;font-weight:400 }
pre { white-space:pre-wrap;overflow-wrap:anywhere;max-height:65vh;overflow:auto;font-family:inherit;font-size:.9rem;line-height:1.85;padding:.75rem;background:#02061755;border-radius:.5rem }
.remove { background:#f8717125;color:#fecaca;text-decoration:line-through }.add { background:#34d39925;color:#a7f3d0 }
@media(max-width:850px){.text-diff{grid-template-columns:1fr}}
</style>
