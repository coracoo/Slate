<script setup lang="ts">
// -*- coding: utf-8 -*-
/** Skill 中心：影视创作垂直 skill 库（拆剧本/导演风格/生图风格），SKILL.md 格式，编辑即下次运行生效 */
import { ref, computed, onMounted } from 'vue'
import { getJSON, postJSON, fetchSkillText, saveSkill, fetchKnowledge, saveCard, deleteCard, buildKnowledge, type KnowledgeSkill, type UserCard, type SkillItem } from '../api'
interface AnyItem extends SkillItem { overridden?: boolean; text?: string }
import { toast } from '../stores/app'
import { trackJob } from '../stores/jobs'
import StyledSelect from '../components/StyledSelect.vue'

const skills = ref<AnyItem[]>([])
const autoCards = ref<KnowledgeSkill[]>([])
const userCards = ref<UserCard[]>([])
const cardEditing = ref<Partial<UserCard> | null>(null)
const cardForm = ref({ skill: '', trigger: '', prescription: '', example: '' })
const loading = ref(false)
const cat = ref('全部')
const detail = ref<AnyItem | null>(null)
const editText = ref('')
const saving = ref(false)
const createVisible = ref(false)
const newName = ref('')
const newCat = ref('导演风格')
const newTarget = ref('storyboard')
const newDesc = ref('')
const newText = ref('')

const CATS = ['全部', '导演风格', '生图风格', '拆剧本', '系统提示词', '经验卡片']
const list = computed(() => skills.value.filter((s) => cat.value === '全部' || s.category === cat.value))

async function load() {
  loading.value = true
  try {
    skills.value = (await getJSON<{ skills: SkillItem[] }>('/api/skills')).skills || []
    const k = await fetchKnowledge()
    autoCards.value = k.skills || []
    userCards.value = k.cards || []
  }
  catch (e) { toast(e instanceof Error ? e.message : '加载失败', 'err') }
  finally { loading.value = false }
}
onMounted(load)

async function openDetail(s: AnyItem) {
  detail.value = s
  try {
    const r = await fetchSkillText(s.id)
    editText.value = r.text || ''
  } catch (e) { editText.value = ''; toast(e instanceof Error ? e.message : '提示词读取失败', 'err') }
}
async function resetSys() {
  if (!detail.value) return
  if (!confirm('重置「' + detail.value.name + '」为内置系统提示词？自定义将被删除。')) return
  try {
    await postJSON('/api/skills/reset', { id: detail.value.id })
    toast('已重置为内置', 'ok')
    detail.value = null
    await load()
  } catch (e) { toast(e instanceof Error ? e.message : '重置失败', 'err') }
}
async function save() {
  if (!detail.value) return
  saving.value = true
  try {
    await saveSkill(detail.value.id, editText.value, detail.value.category === '系统提示词' ? 'system' : 'style')
    toast('已保存——下次运行自动生效', 'ok')
    await load()
  } catch (e) { toast(e instanceof Error ? e.message : '保存失败', 'err') }
  finally { saving.value = false }
}
async function toggle(s: SkillItem) {
  try {
    await postJSON('/api/skills/toggle', { id: s.id, enabled: !s.enabled })
    await load()
  } catch (e) { toast(e instanceof Error ? e.message : '切换失败', 'err') }
}
async function remove(s: SkillItem) {
  if (!confirm(`删除自定义 skill「${s.name}」？`)) return
  try {
    const r = await postJSON<{ ok: boolean }>('/api/skills/delete', { id: s.id })
    if (!r.ok) { toast('内置 skill 不可删除（可停用）', 'err'); return }
    toast('已删除', 'ok'); detail.value = null; await load()
  } catch (e) { toast(e instanceof Error ? e.message : '删除失败', 'err') }
}
async function saveCardForm() {
  const f = cardForm.value
  if (!f.skill.trim() || !f.prescription.trim()) { toast('手法名与处方必填', 'err'); return }
  try {
    await saveCard({ id: cardEditing.value?.id, skill: f.skill, trigger: f.trigger, prescription: f.prescription, example: f.example })
    toast(cardEditing.value?.id ? '卡片已更新（创作时自动垫上下文）' : '卡片已沉淀', 'ok')
    cardEditing.value = null
    await load()
  } catch (e) { toast(e instanceof Error ? e.message : '卡片保存失败', 'err') }
}
async function removeCard(c: UserCard) {
  if (!confirm(`删除经验卡片「${c.skill}」？`)) return
  try {
    await deleteCard(c.id)
    await load()
  } catch (e) { toast(e instanceof Error ? e.message : '删除失败', 'err') }
}
/** 卡片→Skill：处方升级为可注入的风格 skill（拆剧本类） */
async function cardToSkill(c: UserCard | KnowledgeSkill) {
  const body = `创作手法指令（来自拉片经验卡片「${c.skill}」）：
- 触发场面：${(c.trigger || []).join('、')}
- 镜头处方：${c.prescription}
- 参考片例：${c.example || '（补充你的片例）'}

命中该场面的分镜/扩写必须按处方执行。`
  try {
    await postJSON('/api/skills/create', { category: 'script', name: c.skill, target: 'script', description: c.prescription.slice(0, 40), text: body })
    toast('已转为拆剧本 Skill（可在上方编辑正文）', 'ok')
    await load()
  } catch (e) { toast(e instanceof Error ? e.message : '转换失败', 'err') }
}
const kbRebuilding = ref(false)
async function rebuildKb() {
  try {
    const r = await buildKnowledge()
    if (!r.id) throw new Error(r.err || '任务未启动')
    kbRebuilding.value = true
    toast('知识库重建中（自动卡片；用户卡片不受影响）', 'info')
    const j = await trackJob(r.id, '拉片知识库重建')
    if (j.success) { toast('知识库重建完成', 'ok'); await load() }
    else toast('知识库重建失败，详情见任务抽屉', 'err')
  } catch (e) { toast(e instanceof Error ? e.message : '重建失败', 'err') }
  finally { kbRebuilding.value = false }
}
async function create() {
  if (!newName.value.trim() || !newText.value.trim()) { toast('名称与正文必填', 'err'); return }
  try {
    await postJSON('/api/skills/create', {
      category: newCat.value === '导演风格' ? 'directing' : newCat.value === '生图风格' ? 'image-style' : 'script',
      name: newName.value.trim(), target: newTarget.value,
      description: newDesc.value.trim(), text: newText.value
    })
    toast('已创建（自定义，可直接拷入网上 SKILL.md 内容）', 'ok')
    createVisible.value = false
    newName.value = ''; newDesc.value = ''; newText.value = ''
    await load()
  } catch (e) { toast(e instanceof Error ? e.message : '创建失败', 'err') }
}
</script>

<template>
  <div class="page">
    <header class="mb-6">
      <h1 class="grad-text text-2xl font-black">③ Skill 配置</h1>
      <p class="mt-1 text-xs text-slate-500">
        影视创作垂直 skill 库（SKILL.md 开放格式）：拆剧本 / 导演风格 / 生图风格。
        项目在 ①②③ 页选定风格后，管线自动注入对应 LLM 调用；编辑正文即下次运行生效。网上社区 skill 可直接拷入新建。
      </p>
    </header>

    <div class="mb-4 flex flex-wrap items-center gap-2">
      <button v-for="c in CATS" :key="c" class="rounded-lg px-3 py-1.5 text-xs font-bold transition"
        :class="cat === c ? 'chip-active' : 'chip'"
        @click="cat = c">{{ c }}</button>
      <span class="flex-1"></span>
      <button class="btn" @click="createVisible = true">＋ 新建 Skill</button>
    </div>

    <div v-if="loading" class="glass p-10 text-center text-sm text-slate-500">加载…</div>
    <div class="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
      <div v-for="s in list" :key="s.id" class="glass glass-hover cursor-pointer p-3 text-left" role="button" tabindex="0"
        @click="openDetail(s)" @keydown.enter="openDetail(s)" @keydown.space.prevent="openDetail(s)">
        <div class="flex flex-wrap items-center gap-2">
          <b class="text-sm text-slate-100">{{ s.name }}</b>
          <span class="rounded bg-white/10 px-1.5 text-2xs text-slate-400">{{ s.category }}</span>
          <span class="rounded px-1.5 text-2xs"
            :class="s.target === 'storyboard' ? 'bg-sky-400/15 text-sky-300' : s.target === 'image' ? 'bg-fuchsia-400/15 text-fuchsia-300' : 'bg-amber-400/15 text-amber-300'">
            {{ s.target === 'storyboard' ? '分镜' : s.target === 'image' ? '生图' : '剧本' }}
          </span>
          <span v-if="s.category === '系统提示词'" class="rounded bg-cyan-400/15 px-1.5 text-2xs text-cyan-300">系统提示词</span>
          <span v-if="(s as AnyItem).overridden" class="rounded bg-amber-400/15 px-1.5 text-2xs text-amber-300">已覆盖</span>
          <span v-if="!s.builtin && s.category !== '系统提示词'" class="rounded bg-violet-400/15 px-1.5 text-2xs text-violet-300">自定义</span>
          <button type="button" class="ml-auto text-2xs" :class="s.enabled ? 'text-emerald-300' : 'text-slate-500'"
            @click.stop="toggle(s)">{{ s.enabled ? '● 启用' : '○ 停用' }}</button>
        </div>
        <div class="mt-1.5 text-xs-plus text-slate-400">{{ s.description }}</div>
      </div>
    </div>

    <!-- 经验卡片（拉片沉淀层） -->
    <section v-if="cat === '全部' || cat === '经验卡片'" class="glass mt-5 p-4">
      <div class="mb-3 flex flex-wrap items-center gap-3">
        <h3 class="text-sm font-bold text-slate-200">经验卡片 <span class="text-xs-plus font-normal text-slate-500">拉片沉淀 · 创作时自动垫上下文</span></h3>
        <span class="flex-1"></span>
        <button class="btn btn-ghost" :disabled="kbRebuilding" @click="rebuildKb">{{ kbRebuilding ? '重建中…' : '从拉片重建' }}</button>
        <button class="btn" @click="cardEditing = {}; cardForm = { skill: '', trigger: '', prescription: '', example: '' }">＋ 沉淀我的经验</button>
      </div>
      <p class="mb-3 text-xs-plus text-slate-500">
        经典分镜用法与组合（如正反打、紧张快切）天然跨场景复用——命中触发词的分镜/扩写会自动注入对应处方。
        自动卡片来自本工作区拉片归纳；<b class="text-violet-300">用户卡片加权优先</b>，也可一键转成拆剧本 Skill。
      </p>
      <div class="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
        <div v-for="c in userCards" :key="c.id" class="rounded-lg border border-violet-400/30 bg-violet-400/5 p-2.5">
          <div class="flex flex-wrap items-center gap-1.5">
            <b class="text-sm text-violet-200">{{ c.skill }}</b>
            <span class="rounded bg-violet-400/20 px-1.5 text-2xs text-violet-300">我的经验</span>
          </div>
          <div class="mt-1 text-xs text-emerald-300/90">{{ c.prescription }}</div>
          <div class="mt-0.5 text-xs-plus text-slate-500">触发：{{ (c.trigger || []).join('、') }}</div>
          <div class="mt-1.5 flex gap-1.5">
            <button class="btn btn-ghost btn-sm" @click="cardEditing = c; cardForm = { skill: c.skill, trigger: (c.trigger || []).join(','), prescription: c.prescription, example: c.example }">编辑</button>
            <button class="btn btn-ghost btn-sm" @click="cardToSkill(c)">转为 Skill</button>
            <button class="btn btn-danger btn-sm" @click="removeCard(c)">删除</button>
          </div>
        </div>
        <div v-for="c in autoCards" :key="c.id" class="rounded-lg bg-white/5 p-2.5">
          <div class="flex flex-wrap items-center gap-1.5">
            <b class="text-sm text-slate-100">{{ c.skill }}</b>
            <span class="rounded bg-white/10 px-1.5 text-2xs text-slate-400">拉片归纳 ×{{ c.count }}</span>
          </div>
          <div class="mt-1 text-xs text-emerald-300/80">{{ c.prescription }}</div>
          <div class="mt-0.5 line-clamp-1 text-xs-plus text-slate-500">片例：{{ c.example }}</div>
          <button class="btn btn-ghost btn-sm mt-1.5" @click="cardToSkill(c)">转为 Skill</button>
        </div>
      </div>
      <!-- 卡片表单 -->
      <Teleport to="body">
        <div v-if="cardEditing" class="overlay" @click.self="cardEditing = null">
          <div class="glass w-full max-w-lg p-5">
            <h3 class="mb-3 text-sm font-bold text-slate-200">{{ cardEditing.id ? '编辑经验卡片' : '沉淀经验卡片' }}</h3>
            <div class="grid grid-cols-2 gap-2">
              <label class="text-xs text-slate-400">手法名 *
                <input v-model="cardForm.skill" class="input mt-1" placeholder="如：我的三镜对峙节奏" />
              </label>
              <label class="text-xs text-slate-400">触发词（逗号分隔）
                <input v-model="cardForm.trigger" class="input mt-1" placeholder="对峙,谈判,叫阵" />
              </label>
            </div>
            <label class="mt-2 block text-xs text-slate-400">镜头处方 *（命中后自动垫入创作上下文的具体打法）
              <textarea v-model="cardForm.prescription" rows="3" class="textarea mt-1 text-xs"
                placeholder="wide 定场交代空间 → 正反打×3 逐步推近 → 情绪爆点切手持特写"></textarea>
            </label>
            <label class="mt-2 block text-xs text-slate-400">参考片例
              <input v-model="cardForm.example" class="input mt-1" placeholder="丞相骂王朗 S1-S4" />
            </label>
            <div class="mt-3 flex justify-end gap-2">
              <button class="btn btn-ghost" @click="cardEditing = null">取消</button>
              <button class="btn" @click="saveCardForm">保存</button>
            </div>
          </div>
        </div>
      </Teleport>
    </section>

    <!-- 编辑抽屉 -->
    <Teleport to="body">
      <div v-if="detail" class="overlay-end" @click.self="detail = null">
        <div class="drawer-panel-wide flex flex-col">
          <div class="mb-3 flex items-center gap-3">
            <b class="text-lg text-slate-100">{{ detail.name }}</b>
            <span class="text-xs text-slate-500">{{ detail.id }} · {{ detail.category }} → {{ detail.target }}</span>
            <button class="btn btn-ghost ml-auto" @click="detail = null">关闭</button>
            <button v-if="detail.category === '系统提示词' && detail.overridden" class="btn btn-ghost text-amber-300" @click="resetSys">重置内置</button>
            <button v-if="!detail.builtin && detail.category !== '系统提示词'" class="btn btn-danger" @click="remove(detail)">删除</button>
            <button class="btn" :disabled="saving" @click="save">{{ saving ? '保存中…' : '保存（自动生效）' }}</button>
          </div>
          <p class="mb-2 text-xs-plus text-slate-500">
            <template v-if="detail.category === '系统提示词'">
              该步骤的完整系统提示词（LLM 每次调用都用）。保存后整体替换内置并即时生效；
              支持 <code v-pre>{{knowledge}}</code> 占位（运行时注入拉片知识库检索）。「重置内置」删除覆盖恢复原版。
            </template>
            <template v-else>正文即注入给 LLM 的风格/手法指令。规则：指令具体可执行（写机位/光线/节奏/构图），禁止空泛形容词。</template>
          </p>
          <textarea v-model="editText" class="textarea flex-1 w-full font-mono text-xs leading-relaxed" spellcheck="false"></textarea>
        </div>
      </div>
    </Teleport>

    <!-- 新建 -->
    <Teleport to="body">
      <div v-if="createVisible" class="overlay" @click.self="createVisible = false">
        <div class="glass w-full max-w-xl p-5">
          <h3 class="mb-3 text-sm font-bold text-slate-200">新建 Skill（可粘贴网上社区 SKILL.md 正文）</h3>
          <div class="mb-2 grid grid-cols-2 gap-2">
            <label class="text-xs text-slate-400">名称
              <input v-model="newName" class="input mt-1" placeholder="如：诺兰式时间结构" />
            </label>
            <label class="text-xs text-slate-400">类别
              <StyledSelect v-model="newCat" class="mt-1" :options="['导演风格', '生图风格', '拆剧本']" storage-key="wb.skills.new.cat" />
            </label>
            <label class="text-xs text-slate-400">注入目标
              <StyledSelect v-model="newTarget" class="mt-1" :options="['storyboard', 'image', 'script', 'acting']" storage-key="wb.skills.new.target" />
            </label>
            <label class="text-xs text-slate-400">一句话说明
              <input v-model="newDesc" class="input mt-1" />
            </label>
          </div>
          <textarea v-model="newText" rows="8" class="textarea w-full font-mono text-xs leading-relaxed"
            placeholder="正文：注入给 LLM 的指令（具体可执行，禁空泛）"></textarea>
          <div class="mt-3 flex justify-end gap-2">
            <button class="btn btn-ghost" @click="createVisible = false">取消</button>
            <button class="btn" @click="create">创建</button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>
