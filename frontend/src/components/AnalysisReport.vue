<script setup>
// AI 分析报告卡：挂载时先查历史报告（刷新可恢复），没有则提供「开始分析」按钮。
// 状态机 idle(未分析) → loading(10~30s 同步等待) → done(有报告) | error(友好提示)。
// v3.5：同一简历的历次分析（不同 prompt_version）可并排对比差异。
// v3.8：done 状态提供「导出 PDF」——走浏览器打印对话框，打印样式在 assets/main.css 的 @media print。
import Icon from './Icon.vue'
import { computed, onMounted, ref } from 'vue'

import { get, post } from '../api.js'
import { fmtDateTime } from '../utils.js'

const props = defineProps({
  resume: { type: Object, required: true },
})

const state = ref('idle') // idle | loading | done | error
const analysis = ref(null) // AnalysisOut（含 report 六块 + token/耗时元信息）
const cached = ref(false)
const errorMsg = ref('')

/**
 * 导出 PDF：调起浏览器打印对话框，用户在「目标打印机」里选「另存为 PDF」。
 * 之所以不做后端渲染（weasyprint 等）：报告是纯文本 + 手写 SVG，打印 CSS 足够还原，
 * 省掉一个重型依赖与一条服务端链路。打印时导航与按钮由 .no-print 隐藏。
 */
function exportPdf() {
  window.print()
}

// 报告字段 → 展示元信息。kind 决定渲染成列表/标签/编号；icon 为 Icon 组件 name
const SECTION_META = {
  strengths: { title: '优势', kind: 'list', icon: 'trophy' },
  weaknesses: { title: '短板', kind: 'list', icon: 'alert' },
  keyword_gaps: { title: '关键词缺口', kind: 'chips', icon: 'list' },
  suggestions: { title: '改进建议', kind: 'list', icon: 'pen' },
  predicted_questions: { title: '预测面试题', kind: 'numbered', icon: 'flag' },
}

function fmtDuration(ms) {
  if (ms == null) return '-'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

async function loadExisting() {
  try {
    analysis.value = await get(`/api/resumes/${props.resume.id}/analysis`)
    cached.value = false // 历史报告不算本次重复调用
    state.value = 'done'
  } catch (e) {
    if (e.code === 'not_found') {
      state.value = 'idle'
    } else {
      errorMsg.value = e.message || '后端未启动或网络异常，请确认服务已启动'
      state.value = 'error'
    }
  }
}

async function analyze() {
  state.value = 'loading'
  errorMsg.value = ''
  try {
    const body = await post(`/api/resumes/${props.resume.id}/analyze`)
    cached.value = body.cached === true
    analysis.value = body.analysis
    state.value = 'done'
    await loadVersions() // 新出一版后刷新对比列表
  } catch (e) {
    errorMsg.value = e.message || '后端未启动或网络异常，请确认服务已启动'
    state.value = 'error'
  }
}

/* —— v3.5 版本对比 ——
   同一简历在提示词改版（PROMPT_VERSION 递增）后会留下多份报告，
   这里只做展示层对比：把两个版本的同一板块并排，标出各自独有的条目。 */

const versions = ref([])
const compareId = ref('')
const versionsError = ref('')

const otherVersions = computed(() =>
  versions.value.filter((item) => item.id !== analysis.value?.id)
)

const compareVersion = computed(
  () => versions.value.find((item) => String(item.id) === String(compareId.value)) || null
)

// 与对比版逐条比对：only=true 表示这条只在本版出现
function markOnly(items, otherItems) {
  const others = new Set((otherItems || []).map((text) => String(text).trim()))
  return (items || []).map((text) => ({
    text,
    only: !others.has(String(text).trim()),
  }))
}

const compareDiff = computed(() => {
  if (!compareVersion.value) return null
  const current = analysis.value?.report || {}
  const other = compareVersion.value.report || {}
  const keys = Object.keys(SECTION_META)
  const result = {}
  for (const key of keys) {
    result[key] = {
      current: markOnly(current[key], other[key]),
      other: markOnly(other[key], current[key]),
    }
  }
  return result
})

async function loadVersions() {
  try {
    const data = await get(`/api/resumes/${props.resume.id}/analyses`)
    versions.value = data?.items || []
  } catch (e) {
    versionsError.value = e.message || '历史版本加载失败'
  }
}

onMounted(async () => {
  await loadExisting()
  if (state.value === 'done') await loadVersions()
})
</script>

<template>
  <section class="card report">
    <div class="head">
      <h2>AI 简历分析</h2>
      <span v-if="cached" class="badge ok">已有报告 · 未重复调用</span>
      <button
        v-if="state === 'done'"
        class="btn btn-ghost btn-sm no-print export-btn"
        title="用浏览器打印对话框导出为 PDF"
        @click="exportPdf"
      >
        <Icon name="printer" :size="14" /> 导出 PDF
      </button>
    </div>

    <template v-if="state === 'idle'">
      <p class="hint">
        分析将把简历内容发送给第三方大模型服务，生成岗位匹配、优劣势、关键词缺口、改进建议与预测面试题。
      </p>
      <button class="btn btn-primary" @click="analyze"><Icon name="spark" :size="14" /> 生成 AI 分析</button>
    </template>

    <div v-else-if="state === 'loading'" class="loading-row">
      <span class="spinner" aria-hidden="true"></span>
      <p class="loading-text">AI 分析中，通常需要 10~30 秒…</p>
    </div>

    <div v-else-if="state === 'error'" class="err">
      <p class="msg error">{{ errorMsg }}</p>
      <button class="btn btn-ghost" @click="analyze">重试</button>
    </div>

    <template v-else>
      <!-- 版本对比开关：只有存在其他版本时才出现 -->
      <div v-if="otherVersions.length" class="compare-bar">
        <label class="compare-pick">
          <span>版本对比</span>
          <select v-model="compareId" class="input">
            <option value="">关闭</option>
            <option v-for="item in otherVersions" :key="item.id" :value="String(item.id)">
              v{{ item.prompt_version }} · {{ item.model_name }} ·
              {{ fmtDateTime(item.created_at) }}
            </option>
          </select>
        </label>
        <span class="legend"><i class="dot only"></i>仅本版有</span>
      </div>
      <p v-else-if="versionsError" class="hint">{{ versionsError }}</p>

      <!-- —— 对比模式：两版并排 —— -->
      <template v-if="compareDiff">
        <p class="target">
          目标岗位：<strong>{{ analysis.report.target_position || '—' }}</strong>
          <span class="arrow">→</span>
          <strong>{{ compareVersion.report.target_position || '—' }}</strong>
        </p>

        <div class="diff-cols">
          <div class="diff-col">
            <p class="diff-head">当前 · v{{ analysis.prompt_version }}</p>
            <p class="para">{{ analysis.report.position_match || '暂无' }}</p>
          </div>
          <div class="diff-col">
            <p class="diff-head">对比 · v{{ compareVersion.prompt_version }}</p>
            <p class="para">{{ compareVersion.report.position_match || '暂无' }}</p>
          </div>
        </div>

        <template v-for="(meta, key) in SECTION_META" :key="key">
          <h3 class="sec-title"><Icon :name="meta.icon" :size="14" /> {{ meta.title }}</h3>
          <div class="diff-cols">
            <div class="diff-col">
              <ul class="list">
                <li
                  v-for="(item, i) in compareDiff[key].current"
                  :key="i"
                  :class="{ only: item.only }"
                >
                  {{ item.text }}
                </li>
              </ul>
              <p v-if="!compareDiff[key].current.length" class="none">暂无</p>
            </div>
            <div class="diff-col">
              <ul class="list">
                <li
                  v-for="(item, i) in compareDiff[key].other"
                  :key="i"
                  :class="{ only: item.only }"
                >
                  {{ item.text }}
                </li>
              </ul>
              <p v-if="!compareDiff[key].other.length" class="none">暂无</p>
            </div>
          </div>
        </template>
      </template>

      <!-- —— 单版模式（默认） —— -->
      <template v-else>
        <p class="target">
          目标岗位：<strong>{{ analysis.report.target_position || '—' }}</strong>
        </p>
        <p class="para">{{ analysis.report.position_match || '暂无' }}</p>

        <template v-for="(meta, key) in SECTION_META" :key="key">
          <h3 class="sec-title"><Icon :name="meta.icon" :size="14" /> {{ meta.title }}</h3>
          <ul v-if="meta.kind === 'list'" class="list">
            <li v-for="(item, i) in analysis.report[key] || []" :key="i">{{ item }}</li>
          </ul>
          <div v-else-if="meta.kind === 'chips'" class="chips">
            <span v-for="(item, i) in analysis.report[key] || []" :key="i" class="chip">
              {{ item }}
            </span>
          </div>
          <ol v-else class="numbered">
            <li v-for="(item, i) in analysis.report[key] || []" :key="i">{{ item }}</li>
          </ol>
          <p v-if="!(analysis.report[key] || []).length" class="none">暂无</p>
        </template>
      </template>

      <div class="pills">
        <span class="pill">{{ analysis.model_name }}</span>
        <span class="pill">提示词 v{{ analysis.prompt_version }}</span>
        <span class="pill">输入 {{ analysis.tokens_prompt ?? '-' }} tok</span>
        <span class="pill">输出 {{ analysis.tokens_completion ?? '-' }} tok</span>
        <span class="pill">耗时 {{ fmtDuration(analysis.duration_ms) }}</span>
        <span class="pill">{{ fmtDateTime(analysis.created_at) }}</span>
      </div>
    </template>
  </section>
</template>

<style scoped>
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--sp-1);
}
/* 导出按钮靠右：head 是 space-between，徽标与按钮之间用 auto 外边距把它顶到最右 */
.export-btn {
  margin-left: auto;
}
.hint {
  margin: 0 0 var(--sp-3);
  color: var(--c-muted);
  font-size: var(--fs-sm);
  line-height: 1.6;
}
/* 加载态壳与转圈样式由全局 .loading-row + .spinner 提供 */
.loading-text {
  margin: 0;
}
.err {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  flex-wrap: wrap;
}
.target {
  margin: var(--sp-1) 0 var(--sp-3);
  font-size: var(--fs-md);
  color: var(--c-text);
}
.arrow {
  margin: 0 var(--sp-2);
  color: var(--c-muted);
}
.para {
  margin: 0 0 var(--sp-1);
  color: var(--c-text-2);
  font-size: var(--fs-md);
  line-height: 1.6;
}
.list,
.numbered {
  margin: 0;
  padding-left: var(--sp-5);
  color: var(--c-text-2);
  font-size: var(--fs-md);
  line-height: 1.6;
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-2);
}
.none {
  margin: 0;
  color: var(--c-faint);
  font-size: var(--fs-sm);
}
.pills {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-2);
  margin-top: var(--sp-4);
  padding-top: var(--sp-3);
  border-top: 1px dashed var(--c-border);
}

/* —— v3.5 版本对比 —— */
.compare-bar {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  flex-wrap: wrap;
  margin: var(--sp-1) 0 var(--sp-3);
  padding: var(--sp-2) var(--sp-3);
  border: 1px solid var(--c-border);
  border-radius: var(--radius-md);
  background: var(--c-bg-soft);
}
.compare-pick {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  font-size: var(--fs-sm);
  color: var(--c-muted);
}
.compare-pick select {
  width: auto;
  max-width: 320px;
}
.legend {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  margin-left: auto;
  font-size: var(--fs-xs);
  color: var(--c-muted);
}
.dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 2px;
}
.dot.only {
  background: var(--c-primary);
}
.diff-cols {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--sp-4);
  margin-bottom: var(--sp-1);
}
.diff-col {
  min-width: 0;
  padding: var(--sp-3);
  border: 1px solid var(--c-border);
  border-radius: var(--radius-md);
  background: #fff;
}
.diff-head {
  margin: 0 0 var(--sp-2);
  font-size: var(--fs-xs);
  color: var(--c-muted);
}
.diff-col .list {
  padding-left: var(--sp-4);
}
.diff-col .list > li.only {
  margin-left: calc(var(--sp-2) * -1);
  padding-left: var(--sp-2);
  border-left: 3px solid var(--c-primary);
  background: var(--wash-primary);
  border-radius: var(--radius-sm);
  list-style: none;
}

@media (max-width: 900px) {
  .diff-cols {
    grid-template-columns: 1fr;
    gap: var(--sp-3);
  }
}
</style>
