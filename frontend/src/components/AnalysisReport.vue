<script setup>
// AI 分析报告卡：挂载时先查历史报告（刷新可恢复），没有则提供「开始分析」按钮。
// 状态机 idle(未分析) → loading(10~30s 同步等待) → done(有报告) | error(友好提示)。
import { onMounted, ref } from 'vue'
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
  } catch (e) {
    errorMsg.value = e.message || '后端未启动或网络异常，请确认服务已启动'
    state.value = 'error'
  }
}

onMounted(loadExisting)
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
</style>
