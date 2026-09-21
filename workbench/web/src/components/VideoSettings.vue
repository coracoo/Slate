<script setup lang="ts">
import { computed, watch } from 'vue'
import type { VideoCapability, VideoSettingsValue } from '../utils/videoSettings'
const props = defineProps<{capability?: VideoCapability; modelValue: VideoSettingsValue}>()
const emit = defineEmits<{ 'update:modelValue': [VideoSettingsValue] }>()
const names: Record<string,string> = {reference:'全能参考', first_frame:'首帧', first_last:'首尾帧', last_frame:'尾帧', text:'文生视频'}
const frame = computed(() => ['first_frame','first_last','last_frame'].includes(props.modelValue.mode || ''))
function update(patch: Partial<VideoSettingsValue>) { emit('update:modelValue', {...props.modelValue,...patch}) }
watch(() => [props.capability, props.modelValue.mode], () => {
  const cap = props.capability; if (!cap?.known) return
  const value = {...props.modelValue}
  if (!cap.modes.includes(value.mode || '')) value.mode = cap.default_mode
  if (!cap.resolutions.includes(value.resolution || '')) value.resolution = cap.resolutions[0]
  if (cap.frame_adaptive && ['first_frame','first_last','last_frame'].includes(value.mode || '')) value.ratio = 'adaptive'
  else if (!cap.ratios.includes(value.ratio || '')) value.ratio = cap.ratios[0]
  if (!cap.audio_output) delete value.generate_audio
  if (!cap.seed) delete value.seed
  if (JSON.stringify(value) !== JSON.stringify(props.modelValue)) emit('update:modelValue',value)
}, {immediate:true})
</script>

<template>
  <section class="space-y-3 rounded-xl border border-sky-400/20 bg-sky-950/10 p-3 text-xs">
    <b class="text-sky-200">视频模型参数</b>
    <p v-if="!capability?.known" class="text-amber-300">此型号尚无已验证的参数协议，请先选择受支持型号。</p>
    <template v-else>
      <label class="block">参考模式<select :value="modelValue.mode" @change="update({mode:($event.target as HTMLSelectElement).value})"><option v-for="mode in capability.modes" :key="mode" :value="mode">{{ names[mode] || mode }}</option></select></label>
      <div class="grid grid-cols-2 gap-2">
        <label>分辨率<select :value="modelValue.resolution" @change="update({resolution:($event.target as HTMLSelectElement).value})"><option v-for="r in capability.resolutions" :key="r" :value="r">{{ r === 'workflow' ? '内置工作流' : r }}</option></select></label>
        <label>画幅<select :value="modelValue.ratio" :disabled="frame && capability.frame_adaptive" @change="update({ratio:($event.target as HTMLSelectElement).value})"><option v-if="frame && capability.frame_adaptive" value="adaptive">跟随首尾帧</option><option v-for="r in capability.ratios" :key="r">{{ r }}</option></select></label>
      </div>
      <label v-if="capability.audio_output" class="block"><input type="checkbox" :checked="modelValue.generate_audio ?? true" @change="update({generate_audio:($event.target as HTMLInputElement).checked})" /> 生成声音</label>
      <label v-if="capability.seed" class="block">种子（空白为随机）<input type="number" :value="modelValue.seed" min="-1" max="2147483647" @change="update({seed:($event.target as HTMLInputElement).value ? Number(($event.target as HTMLInputElement).value) : undefined})" /></label>
      <p class="text-slate-400">{{ capability.min_duration }}–{{ capability.max_duration }} 秒 · {{ modelValue.mode === 'reference' ? `图片 ${capability.max_refs} / 音频 ${capability.max_audio} / 视频 ${capability.max_video}` : '仅传入当前模式的指定帧' }}</p>
      <p v-if="capability.transport === 'public_url'" class="text-amber-200">此接口需要公网素材 URL，本地路径不能直接提交。</p>
      <p v-if="frame" class="text-slate-400">仅使用下方明确指定的首尾帧；其余 S 画面保留在时间轴描述中。</p>
    </template>
  </section>
</template>

<style scoped>
select{display:block;width:100%;margin-top:5px;border:1px solid #ffffff20;border-radius:7px;padding:8px;background:#101a27;color:#dbe5f2}select:disabled{opacity:.6}
</style>
