<script setup>
// AI 分析报告卡：挂载时先查历史报告（刷新可恢复），没有则提供「开始分析」按钮。
// 状态机 idle(未分析) → loading(10~30s 同步等待) → done(有报告) | error(友好提示)。
import { onMounted, ref } from 'vue'

const props = defineProps({
  resume: { type: Object, required: true },
})

const state = ref('idle') // idle | loading | done | error
const analysis = ref(null) // AnalysisOut（含 report 六块 + token/耗时元信息）
const cached = ref(false)
const errorMsg = ref('')

// 报告字段 → 展示元信息。kind 决定渲染成列表/标签/编号
const SECTION_META = {
  strengths: { title: '优势', kind: 'list' },
  weaknesses: { title: '短板', kind: 'list' },
  keyword_gaps: { title: '关键词缺口', kind: 'chips' },
  suggestions: { title: '改进建议', kind: 'list' },
  predicted_questions: { title: '预测面试题', kind: 'numbered' },
}

function fmtDuration(ms) {
  if (ms == null) return '-'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function errorText(res) {
  if (res.status === 429) return res.body?.detail || '今日分析次数已用完，次日 0 点恢复'
  if (res.status === 502) return res.body?.detail || 'AI 服务暂时不可用，请稍后重试'
  return res.body?.detail || '分析失败，请稍后重试'
}

async function loadExisting() {
  try {
    const res = await fetch(`/api/resumes/${props.resume.id}/analysis`)
    if (res.ok) {
      analysis.value = await res.json()
      cached.value = false // 历史报告不算本次重复调用
      state.value = 'done'
    } else if (res.status === 404) {
      state.value = 'idle'
    } else {
      errorMsg.value = errorText(res)
      state.value = 'error'
    }
  } catch {
    errorMsg.value = '后端未启动或网络异常，请确认服务已启动'
    state.value = 'error'
  }
}

async function analyze() {
  state.value = 'loading'
  errorMsg.value = ''
  try {
    const res = await fetch(`/api/resumes/${props.resume.id}/analyze`, { method: 'POST' })
    const body = await res.json().catch(() => null)
    if (!res.ok) {
      errorMsg.value = body?.detail || errorText({ status: res.status, body })
      state.value = 'error'
      return
    }
    cached.value = body.cached === true
    analysis.value = body.analysis
    state.value = 'done'
  } catch {
    errorMsg.value = '后端未启动或网络异常，请确认服务已启动'
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
      <button @click="analyze">开始 AI 分析</button>
    </template>

    <div v-else-if="state === 'loading'" class="loading">
      <span class="spinner" aria-hidden="true"></span>
      <p class="loading-text">AI 分析中，通常需要 10~30 秒…</p>
    </div>

    <div v-else-if="state === 'error'" class="err">
      <p class="msg error">{{ errorMsg }}</p>
      <button @click="analyze">重试</button>
    </div>

    <template v-else>
      <p class="target">
        目标岗位：<strong>{{ analysis.report.target_position || '—' }}</strong>
      </p>
      <p class="para">{{ analysis.report.position_match || '暂无' }}</p>

      <template v-for="(meta, key) in SECTION_META" :key="key">
        <h3 class="sec">{{ meta.title }}</h3>
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

      <div class="meta">
        <span>模型 {{ analysis.model_name }}</span>
        <span>提示词 v{{ analysis.prompt_version }}</span>
        <span>输入 {{ analysis.tokens_prompt ?? '-' }} tok</span>
        <span>输出 {{ analysis.tokens_completion ?? '-' }} tok</span>
        <span>耗时 {{ fmtDuration(analysis.duration_ms) }}</span>
        <span>{{ new Date(analysis.created_at).toLocaleString() }}</span>
      </div>
    </template>
  </section>
</template>

<style scoped>
.card {
  background: #fff;
  border-radius: 12px;
  box-shadow: 0 1px 4px rgb(0 0 0 / 6%);
  padding: 20px 24px;
}
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}
.head h2 {
  margin: 0;
  font-size: 17px;
}
.badge {
  padding: 2px 10px;
  border-radius: 999px;
  font-size: 12px;
  white-space: nowrap;
}
.badge.ok {
  background: #d1fae5;
  color: #047857;
}
.hint {
  margin: 0 0 14px;
  color: #6b7280;
  font-size: 13px;
}
button {
  border: none;
  border-radius: 8px;
  background: #10b981;
  color: #fff;
  font-size: 14px;
  padding: 9px 18px;
  cursor: pointer;
}
button:hover {
  background: #059669;
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
  border-top-color: #10b981;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  flex-shrink: 0;
}
@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
.loading-text {
  margin: 0;
  color: #6b7280;
  font-size: 13px;
}
.err {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.msg {
  border-radius: 8px;
  padding: 8px 12px;
  font-size: 13px;
}
.msg.error {
  background: #fee2e2;
  color: #b91c1c;
}
.target {
  margin: 4px 0 10px;
  font-size: 15px;
  color: #374151;
}
.para {
  margin: 0 0 4px;
  color: #4b5563;
  font-size: 14px;
  line-height: 1.7;
}
.sec {
  margin: 16px 0 6px;
  font-size: 14px;
  color: #111827;
}
.list {
  margin: 0;
  padding-left: 20px;
  color: #4b5563;
  font-size: 14px;
  line-height: 1.7;
}
.numbered {
  margin: 0;
  padding-left: 20px;
  color: #4b5563;
  font-size: 14px;
  line-height: 1.7;
}
.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.chip {
  background: #f0fdf4;
  border: 1px solid #bbf7d0;
  color: #047857;
  border-radius: 999px;
  padding: 2px 10px;
  font-size: 12px;
}
.none {
  margin: 0;
  color: #9ca3af;
  font-size: 13px;
}
.meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 14px;
  margin-top: 18px;
  padding-top: 12px;
  border-top: 1px dashed #e5e7eb;
  color: #9ca3af;
  font-size: 12px;
}
</style>
