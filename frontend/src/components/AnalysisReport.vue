<script setup>
// AI 分析报告卡：挂载时先查历史报告（刷新可恢复），没有则提供「开始分析」按钮。
// 状态机 idle(未分析) → loading(10~30s 同步等待) → done(有报告) | error(友好提示)。
// v3.5：同一简历的历次分析（不同 prompt_version）可并排对比差异。
import { computed, onMounted, ref } from 'vue'

import { get, post } from '../api.js'

const props = defineProps({
  resume: { type: Object, required: true },
})

const state = ref('idle') // idle | loading | done | error
const analysis = ref(null) // AnalysisOut（含 report 六块 + token/耗时元信息）
const cached = ref(false)
const errorMsg = ref('')

// 报告字段 → 展示元信息。kind 决定渲染成列表/标签/编号
const SECTION_META = {
  strengths: { title: '优势', kind: 'list', icon: '💪' },
  weaknesses: { title: '短板', kind: 'list', icon: '⚠️' },
  keyword_gaps: { title: '关键词缺口', kind: 'chips', icon: '🔑' },
  suggestions: { title: '改进建议', kind: 'list', icon: '🛠️' },
  predicted_questions: { title: '预测面试题', kind: 'numbered', icon: '🎯' },
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
    </div>

    <template v-if="state === 'idle'">
      <p class="hint">
        分析将把简历内容发送给第三方大模型服务，生成岗位匹配、优劣势、关键词缺口、改进建议与预测面试题。
      </p>
      <button class="btn btn-primary" @click="analyze">✨ 生成 AI 分析</button>
    </template>

    <div v-else-if="state === 'loading'" class="loading">
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
          <select v-model="compareId">
            <option value="">关闭</option>
            <option v-for="item in otherVersions" :key="item.id" :value="String(item.id)">
              v{{ item.prompt_version }} · {{ item.model_name }} ·
              {{ new Date(item.created_at).toLocaleString() }}
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
          <h3 class="sec-title">{{ meta.icon }} {{ meta.title }}</h3>
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
          <h3 class="sec-title">{{ meta.icon }} {{ meta.title }}</h3>
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
        <span class="pill">🤖 {{ analysis.model_name }}</span>
        <span class="pill">提示词 v{{ analysis.prompt_version }}</span>
        <span class="pill">输入 {{ analysis.tokens_prompt ?? '-' }} tok</span>
        <span class="pill">输出 {{ analysis.tokens_completion ?? '-' }} tok</span>
        <span class="pill">耗时 {{ fmtDuration(analysis.duration_ms) }}</span>
        <span class="pill">{{ new Date(analysis.created_at).toLocaleString() }}</span>
      </div>
    </template>
  </section>
</template>

<style scoped>
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}
.hint {
  margin: 0 0 14px;
  color: var(--c-muted);
  font-size: 13px;
  line-height: 1.7;
}
.loading {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 6px 0;
}
.spinner {
  width: 18px;
  height: 18px;
  border: 2px solid #d1d5db;
  border-top-color: var(--c-primary);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  flex-shrink: 0;
}
.loading-text {
  margin: 0;
  color: var(--c-muted);
  font-size: 13px;
}
.err {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.target {
  margin: 4px 0 10px;
  font-size: 14.5px;
  color: var(--c-text);
}
.arrow {
  margin: 0 6px;
  color: var(--c-muted);
}
.para {
  margin: 0 0 4px;
  color: var(--c-text-2);
  font-size: 13.5px;
  line-height: 1.8;
}
.list,
.numbered {
  margin: 0;
  padding-left: 20px;
  color: var(--c-text-2);
  font-size: 13.5px;
  line-height: 1.9;
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.none {
  margin: 0;
  color: var(--c-faint);
  font-size: 13px;
}
.pills {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px dashed var(--c-border);
}

/* —— v3.5 版本对比 —— */
.compare-bar {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
  margin: 2px 0 14px;
  padding: 10px 12px;
  border: 1px solid var(--c-border);
  border-radius: 10px;
  background: #f8fafc;
}
.compare-pick {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12.5px;
  color: var(--c-muted);
}
.compare-pick select {
  max-width: 320px;
  padding: 6px 8px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  font-size: 12.5px;
  color: var(--c-text-2);
  background: #fff;
}
.legend {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;
  font-size: 12px;
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
  gap: 16px;
  margin-bottom: 6px;
}
.diff-col {
  min-width: 0;
  padding: 10px 12px;
  border: 1px solid var(--c-border);
  border-radius: 10px;
  background: #fff;
}
.diff-head {
  margin: 0 0 8px;
  font-size: 12px;
  color: var(--c-muted);
  letter-spacing: 0.2px;
}
.diff-col .list {
  padding-left: 18px;
}
.diff-col .list > li.only {
  margin-left: -8px;
  padding-left: 8px;
  border-left: 3px solid var(--c-primary);
  background: rgba(16, 185, 129, 0.07);
  border-radius: 3px;
  list-style: none;
}

@media (max-width: 900px) {
  .diff-cols {
    grid-template-columns: 1fr;
    gap: 10px;
  }
}
</style>
