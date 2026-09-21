<script setup lang="ts">
// -*- coding: utf-8 -*-
/** ① 剧本分集：文本输入框 → LLM 分集（多集剧本），支持逐集查看明细（钩子/落点/梗概/原文） */
import { ref, computed, watch } from 'vue'
import {
  fetchScriptData, importScript, scriptEpisodes, scriptOverview, scriptExpand, deleteScriptEpisode,
  type ScriptBundle, type Episode
} from '../api'
import { app, toast } from '../stores/app'
import { trackJob } from '../stores/jobs'
import { pendingEpisodeIds } from '../utils/scriptEpisodes'
import EmptyState from '../components/EmptyState.vue'
import StyleSelect from '../components/StyleSelect.vue'

const data = ref<ScriptBundle | null>(null)
const loading = ref(false)
const scriptText = ref('')
const busy = ref(false)
const detail = ref<Episode | null>(null)
const mode = ref<'import' | 'idea'>('import')
const idea = ref('')
const epsN = ref(6)
const expanding = ref('')
const epDeleting = ref('')

/** 删除分集：只删分集清单并解除资产来源标签；资产、图片和已生成产物保留。 */
async function deleteEpisode(e: Episode) {
  if (!app.current || epDeleting.value) return
  const label = `${e.id}${e.title ? `「${e.title}」` : ''}`
  if (!window.confirm(`确定删除分集 ${label}？\n\n只删除分集清单并解除该集资产来源标签，全球资产、图片和已生成产物会保留。`)) return
  epDeleting.value = e.id
  try {
    const result = await deleteScriptEpisode(app.current, e.id)
    const unlinked = result.assets_unlinked || 0
    if (detail.value?.id === e.id) detail.value = null
    await load()
    toast(`已删除分集 ${result.episode || label}${unlinked ? `，解除 ${unlinked} 条资产来源关联` : ''}`, 'ok', 5000)
  } catch (err) {
    toast(err instanceof Error ? err.message : '删除分集失败', 'err', 6000)
  } finally {
    epDeleting.value = ''
  }
}

const episodes = computed(() => data.value?.episodes || [])
const scriptMode = computed(() => (data.value as any)?.script_mode || (episodes.value.some((e) => e.text) ? 'generated' : 'imported'))
const scriptReadonly = computed(() => scriptMode.value === 'generated')

async function load() {
  if (!app.current) return
  loading.value = true
  try {
    data.value = await fetchScriptData(app.current)
    scriptText.value = data.value.script || ''
  } catch (e) {
    toast(e instanceof Error ? e.message : '加载失败', 'err')
  } finally { loading.value = false }
}
watch(() => app.current, load, { immediate: true })

async function run(label: string, fn: () => Promise<{ id?: number; err?: string }>) {
  busy.value = true
  try {
    const r = await fn()
    if (!r.id) { toast(`${label} 完成`, 'ok', 4000); await load(); return }  // 同步接口（importScript）无任务 id，抛错即失败
    const j = await trackJob(r.id, label)
    if (j.success) { toast(`${label} 完成`, 'ok', 4000); await load() }
    else throw new Error(j.err || `${label} 失败`)
  } catch (e) {
    toast(e instanceof Error ? e.message : `${label} 失败`, 'err', 6000)
  } finally { busy.value = false }
}
const save = () => run('导入剧本', async () => { await importScript({ project: app.current!, text: scriptText.value }); return { id: 0 } })
const doEps = () => run('LLM 分集', () => scriptEpisodes(app.current!))
const doOverview = (episode?: string) => run(episode ? `更新 ${episode} 概要` : '更新分集概要', () => scriptOverview(app.current!, episode))

function epSlice(e: Episode): string {
  if (e.text) return e.text
  return (data.value?.script || '').slice(e.char_start ?? 0, e.char_end ?? undefined)
}
/** 从0生成：构想 → 大纲（写 分集.json）；episode 给定则扩写该集原文 */
async function expandOne(episode: string): Promise<void> {
  if (!app.current) throw new Error('请先选择项目')
  const r = await scriptExpand({ project: app.current, idea: idea.value || undefined, eps: epsN.value, episode })
  if (!r.id) throw new Error(r.err || '任务未启动')
  const j = await trackJob(r.id, `扩写 ${episode}`)
  if (!j.success) throw new Error(j.err || `${episode} 生成失败`)
}

async function doExpand(episode?: string) {
  if (!app.current || busy.value || expanding.value) return
  if (episode) expanding.value = episode
  else busy.value = true
  try {
    if (episode) {
      await expandOne(episode)
      toast(`${episode} 扩写完成`, 'ok', 4000)
    } else {
      const r = await scriptExpand({ project: app.current, idea: idea.value || undefined, eps: epsN.value })
      if (!r.id) throw new Error(r.err || '任务未启动')
      const j = await trackJob(r.id, '生成大纲')
      if (!j.success) throw new Error(j.err || '生成大纲失败')
      toast('大纲完成', 'ok', 4000)
    }
    await load()
  } catch (e) {
    toast(e instanceof Error ? e.message : '生成失败', 'err', 6000)
  } finally {
    expanding.value = ''; busy.value = false
  }
}

const pendingEpisodes = computed(() => pendingEpisodeIds(episodes.value))
const allExpandProgress = ref({ done: 0, total: 0 })
const allExpandLabel = computed(() => {
  if (expanding.value === '全部') {
    return `全部生成中 ${allExpandProgress.value.done}/${allExpandProgress.value.total}…`
  }
  return pendingEpisodes.value.length
    ? `一键生成全部剧本（${pendingEpisodes.value.length} 集）`
    : '全部剧本已生成'
})

/** 一键生成全部未扩写分集；逐集排队避免多个进程覆盖同一份分集.json。 */
async function doExpandAll() {
  if (!app.current || busy.value || expanding.value) return
  const pending = pendingEpisodeIds(episodes.value)
  if (!pending.length) {
    toast('所有分集已有正文，无需重复生成', 'info', 4500)
    return
  }
  busy.value = true
  expanding.value = '全部'
  allExpandProgress.value = { done: 0, total: pending.length }
  const failures: string[] = []
  let done = 0
  try {
    for (const episode of pending) {
      try {
        await expandOne(episode)
      } catch (e) {
        const message = e instanceof Error ? e.message : '生成失败'
        failures.push(`${episode}：${message}`)
        // 缺少构想属于全局前置条件，继续请求只会重复失败。
        if (message.includes('缺少创作构想')) break
      } finally {
        done += 1
        allExpandProgress.value = { done, total: pending.length }
      }
    }
    await load()
    const success = done - failures.length
    if (failures.length) {
      toast(`全部生成完成 ${success} / 失败 ${failures.length}：${failures.slice(0, 2).join('；')}`, 'err', 7000)
    } else {
      toast(`全部剧本生成完成：${success} 集`, 'ok', 5000)
    }
  } finally {
    expanding.value = ''
    busy.value = false
  }
}
</script>

<template>
  <div class="page">
    <header class="mb-6">
      <h1 class="grad-text text-2xl font-black">① 剧本生成</h1>
      <p class="mt-1 text-xs text-slate-500">制作流水线第一步：粘贴本地剧本 → LLM 按叙事弧分集（多集剧本）→ 点开任一集查看明细
        <span v-if="data?.script_rev" class="ml-2 rounded-full bg-white/5 px-2 py-0.5 text-2xs text-slate-400"
          title="剧本修订号：分集清单每次变更（分集/扩写/删集/概要）自动 +1；分镜与资产提炼会记录生成时的版本">剧本 v{{ data.script_rev }}</span>
      </p>
    </header>

    <EmptyState v-if="!app.current" title="请先在左侧选择项目" hint="建议使用「制作」类型项目" />

    <template v-else>
      <!-- 文本输入：双模式（导入已有剧本 / 一段话从0生成） -->
      <section class="glass mb-5 p-4">
        <div class="mb-3 flex flex-wrap items-center gap-2">
          <span class="flex h-6 w-6 items-center justify-center rounded-full bg-pink-400/15 text-xs font-black text-pink-300">1</span>
          <h3 class="text-sm font-bold text-slate-200">剧本来源</h3>
          <button class="rounded-lg px-3 py-1 text-xs font-bold transition"
            :class="mode === 'import' ? 'chip-active' : 'chip'"
            @click="mode = 'import'">导入成品剧本（拆分集）</button>
          <button class="rounded-lg px-3 py-1 text-xs font-bold transition"
            :class="mode === 'idea' ? 'chip-active' : 'chip'"
            @click="mode = 'idea'">一段话从 0 生成（扩写）</button>
          <span class="flex-1"></span>
          <span v-if="mode === 'import'" class="text-xs-plus text-slate-500">{{ scriptText.length }} 字（也放 projects/&lt;项目&gt;/剧本/剧本.txt）</span>
        </div>

        <div v-if="mode === 'import'">
          <div v-if="scriptReadonly" class="mb-2 rounded-lg bg-violet-400/10 px-3 py-1.5 text-xs-plus text-violet-200">
            本项目剧本由「从 0 生成」逐集扩写而来——以下是全部集正文聚合（只读）。修改请回「一段话从 0 生成」页对单集重新扩写；在下方直接保存会覆盖聚合结果为 imported 模式。
          </div>
          <div class="mb-2 flex justify-end gap-2">
            <button class="btn btn-ghost" :disabled="busy || scriptReadonly || scriptText === (data?.script || '')" @click="save">
              {{ scriptReadonly ? 'generated 项目不可保存' : '保存剧本' }}
            </button>
            <button class="btn" :disabled="busy || scriptText.length < 80" @click="doEps">
              {{ busy ? '分集中…' : 'LLM 分集' }}
            </button>
          </div>
          <textarea v-model="scriptText" rows="8" class="textarea w-full font-mono text-xs leading-relaxed"
            :readonly="scriptReadonly"
            placeholder="粘贴剧本文本（≥80 字）：场景标题、动作描述、人物台词……"></textarea>
        </div>

        <div v-else>
          <div class="mb-2 flex flex-wrap items-end gap-3">
            <label class="w-24 text-xs text-slate-400">目标集数
              <input v-model.number="epsN" type="number" min="1" max="30" class="input mt-1" />
            </label>
            <button class="btn" :disabled="busy || idea.trim().length < 5" @click="doExpand()">
              {{ busy ? '大纲生成中…' : '生成剧集大纲' }}
            </button>
            <span class="text-xs-plus text-slate-500">一段话构想 → 全季大纲（钩子/落点/主线）→ 点各集「扩写」成完整分场剧本</span>
            <StyleSelect target="script" label="拆剧本手法" />
          </div>
          <textarea v-model="idea" rows="5" class="textarea w-full text-xs leading-relaxed"
            placeholder="一段话创作构想，例如：一个外卖员捡到一部只能拨打给十年前自己的手机，他试图阻止一场事故，却发现每次改动都在制造更大的麻烦。悬疑基调，短剧节奏。"></textarea>
        </div>
      </section>

      <!-- 分集列表（多集剧本） -->
      <section class="glass p-4">
        <div class="mb-3 flex flex-wrap items-center gap-3">
          <span class="flex h-6 w-6 items-center justify-center rounded-full bg-pink-400/15 text-xs font-black text-pink-300">2</span>
          <h3 class="text-sm font-bold text-slate-200">分集（{{ episodes.length }}）</h3>
          <span class="flex-1 text-xs-plus text-slate-500">点卡片查看该集明细：钩子/落点/梗概/原文；一键生成会跳过已有正文的分集</span>
          <button class="btn btn-sm" :disabled="busy || !!expanding || !pendingEpisodes.length" @click="doExpandAll">
            {{ allExpandLabel }}
          </button>
        </div>
        <div v-if="expanding === '全部'" class="mb-3 rounded-lg border border-pink-400/20 bg-pink-400/5 px-3 py-2 text-xs-plus text-pink-200">
          正在逐集写入剧本文件，请保持当前页面打开（{{ allExpandProgress.done }} / {{ allExpandProgress.total }}）
        </div>
        <div v-if="!episodes.length" class="py-10 text-center text-sm text-slate-500">尚未分集——保存剧本后点「LLM 分集」</div>
        <div class="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          <div v-for="e in episodes" :key="e.id" class="glass glass-hover p-3">
            <button class="block w-full text-left" @click="detail = e">
              <div class="flex items-center gap-2">
                <span class="rounded bg-pink-400/15 px-2 py-0.5 text-xs font-black text-pink-300">{{ e.id }}</span>
                <b class="text-sm text-slate-100">{{ e.title }}</b>
                <span class="ml-auto text-2xs text-slate-500">{{ e.duration_min ?? '?' }}min</span>
              </div>
              <div class="mt-1.5 line-clamp-2 text-xs-plus text-slate-400">{{ e.summary }}</div>
            </button>
            <div class="mt-2 flex items-center gap-2 border-t border-line-soft pt-2">
              <button class="btn btn-ghost btn-sm" :disabled="busy || !!expanding" @click="doExpand(e.id)">
                {{ expanding === e.id ? '扩写中…' : e.text ? '重新扩写本集' : '扩写本集剧本' }}
              </button>
              <button class="btn btn-ghost btn-sm" :disabled="busy || !!expanding || !e.text" title="只更新影评式概要和本集资产索引，不改剧本文本" @click.stop="doOverview(e.id)">更新概要</button>
              <span v-if="e.text" class="text-2xs text-emerald-300">已扩写 {{ e.text.length }} 字</span>
              <button
                class="btn btn-danger btn-sm ml-auto"
                :disabled="busy || !!expanding || epDeleting === e.id"
                title="删除该分集清单并解除其资产来源标签；资产、图片和已生成产物保留"
                @click="deleteEpisode(e)"
              >{{ epDeleting === e.id ? '删除中…' : '删除' }}</button>
            </div>
          </div>
        </div>
      </section>

      <!-- 集明细抽屉 -->
      <Teleport to="body">
        <div v-if="detail" class="overlay-end" @click.self="detail = null">
          <div class="drawer-panel">
            <div class="mb-4 flex items-center gap-3">
              <span class="rounded bg-pink-400/15 px-2 py-0.5 text-sm font-black text-pink-300">{{ detail.id }}</span>
              <b class="text-lg text-slate-100">{{ detail.title }}</b>
              <span class="text-xs text-slate-500">{{ detail.duration_min ?? '?' }}min · 原文 {{ epSlice(detail).length }} 字</span>
              <button class="btn btn-ghost ml-auto" @click="detail = null">关闭</button>
            </div>
            <div class="space-y-3 text-sm">
              <div class="rounded-lg bg-white/5 p-3"><b class="text-sky-300">钩子</b><p class="mt-1 text-slate-300">{{ detail.hook }}</p></div>
              <div class="rounded-lg bg-white/5 p-3"><b class="text-amber-300">落点</b><p class="mt-1 text-slate-300">{{ detail.cliff }}</p></div>
              <div class="rounded-lg bg-white/5 p-3"><b class="text-emerald-300">梗概</b><p class="mt-1 text-slate-300">{{ detail.summary }}</p></div>
              <div v-if="detail.cast_refs?.length || detail.scene_refs?.length || detail.key_asset_refs?.length" class="rounded-lg bg-cyan-400/5 p-3">
                <b class="text-cyan-300">制作索引</b>
                <div v-if="detail.cast_refs?.length" class="mt-2 text-xs-plus text-slate-300">人物：{{ detail.cast_refs.join('、') }}</div>
                <div v-if="detail.scene_refs?.length" class="mt-1 text-xs-plus text-slate-300">场景：{{ detail.scene_refs.join('、') }}</div>
                <div v-if="detail.key_asset_refs?.length" class="mt-1 text-xs-plus text-slate-300">关键资产：{{ detail.key_asset_refs.join('、') }}</div>
              </div>
              <div class="rounded-lg bg-white/5 p-3"><b class="text-slate-300">本集原文</b>
                <pre class="mt-2 whitespace-pre-wrap font-mono text-xs leading-relaxed text-slate-400">{{ epSlice(detail) }}</pre>
              </div>
            </div>
            <p class="mt-4 text-xs-plus text-slate-500">下一步：② 分镜生成 → ③ 素材生成</p>
          </div>
        </div>
      </Teleport>
    </template>
  </div>
</template>

