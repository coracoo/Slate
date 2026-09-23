<script setup lang="ts">
// -*- coding: utf-8 -*-
/** 分镜汇总表格（只读版）：复用分镜提示词页的 14 列网格样式，
 *  供版本预览等只读场景展示 storyboard JSON（镜号/场景/景别/时长/内容/动作/声音/机位/运镜/光影/器械/镜头/台词/提示词）。 */
defineProps<{ shots: any[] }>()

function linesOf(s: any): string {
  return (s.lines || []).map((l: any) => `【${l.speaker}】${l.line}`).join(' / ')
}
/** 摄像机位（视角）可读描述：高度差+角度词+水平距离（与分镜提示词页 viewOf 同口径） */
function viewOf(s: any): string {
  const ang = s.angle || '平视'
  if (ang === '鸟瞰') return '顶拍·俯瞰'
  let h = ''
  const pos = s.pos || [], look = s.look || []
  if (pos.length === 3 && look.length === 3) {
    const d = look[1] - pos[1]
    if (d > 0.8) h = '仰拍'
    else if (d < -1.2) h = '高机位下压'
    else h = '眼平'
  }
  const dist = pos.length === 3 && look.length === 3
    ? Math.hypot(look[0] - pos[0], look[2] - pos[2]).toFixed(1) : '?'
  return `${h || ang}·${dist}m`
}
</script>

<template>
  <div class="rounded-lg border border-line">
    <table class="w-full border-collapse text-left text-xs-plus">
      <thead class="sticky top-0 z-10 bg-slate-900/95 backdrop-blur">
        <tr class="text-slate-300">
          <th class="border-b border-line px-2 py-1.5">镜号</th>
          <th class="border-b border-line px-2 py-1.5">场景</th>
          <th class="border-b border-line px-2 py-1.5 w-16">时长s</th>
          <th class="border-b border-line px-2 py-1.5">机位(视角)</th>
          <th class="border-b border-line px-2 py-1.5">器械</th>
          <th class="border-b border-line px-2 py-1.5">镜头</th>
          <th class="border-b border-line px-2 py-1.5">运镜</th>
          <th class="border-b border-line px-2 py-1.5 min-w-40">内容</th>
          <th class="border-b border-line px-2 py-1.5 min-w-36">动作</th>
          <th class="border-b border-line px-2 py-1.5 min-w-28">声音</th>
          <th class="border-b border-line px-2 py-1.5 min-w-28">光影</th>
          <th class="border-b border-line px-2 py-1.5 min-w-40">台词</th>
          <th class="border-b border-line px-2 py-1.5 min-w-48">提示词</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="s in shots" :key="s.id" class="align-top hover:bg-white/5">
          <td class="whitespace-nowrap border-b border-line-soft px-2 py-1 font-black text-sky-300">{{ s.id }}</td>
          <td class="border-b border-line-soft px-2 py-1">
            <span class="rounded px-1 text-2xs font-bold" :class="s.scene === 'field' ? 'bg-amber-400/15 text-amber-300' : s.scene === 'room' ? 'bg-white/10 text-slate-400' : 'bg-violet-400/15 text-violet-300'">{{ s.scene === 'field' ? '外景' : s.scene === 'room' ? '室内' : (s.scene || '—') }}</span>
          </td>
          <td class="border-b border-line-soft px-2 py-1 text-center tabular-nums text-slate-200">{{ s.dur ?? 4 }}</td>
          <td class="whitespace-pre-line border-b border-line-soft px-2 py-1 leading-relaxed text-slate-300">{{ s.content }}</td>
          <td class="whitespace-pre-line border-b border-line-soft px-2 py-1 leading-relaxed text-slate-200">{{ s.action }}</td>
          <td class="whitespace-pre-line border-b border-line-soft px-2 py-1 leading-relaxed text-slate-400">{{ s.sound }}</td>
          <td class="whitespace-nowrap border-b border-line-soft px-2 py-1 text-slate-400" :title="`${JSON.stringify(s.pos)} → ${JSON.stringify(s.look)}`">{{ viewOf(s) }}</td>
          <td class="whitespace-nowrap border-b border-line-soft px-2 py-1 text-slate-400">{{ s.camera_move }}</td>
          <td class="whitespace-pre-line border-b border-line-soft px-2 py-1 leading-relaxed text-slate-400">{{ s.lighting }}</td>
          <td class="whitespace-nowrap border-b border-line-soft px-2 py-1 text-slate-300">{{ s.rig }}</td>
          <td class="whitespace-nowrap border-b border-line-soft px-2 py-1 text-slate-300">{{ s.lens }}</td>
          <td class="max-w-56 whitespace-pre-line border-b border-line-soft px-2 py-1 text-slate-400">{{ linesOf(s) || '—' }}</td>
          <td class="whitespace-pre-line border-b border-line-soft px-2 py-1 leading-relaxed text-slate-300">{{ s.prompt }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
