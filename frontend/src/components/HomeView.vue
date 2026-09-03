<script setup>
import { onMounted, ref } from 'vue'
import { get } from '../api.js'
import UploadCard from './UploadCard.vue'
import ResumeList from './ResumeList.vue'
import AnalysisReport from './AnalysisReport.vue'
import InterviewChat from './InterviewChat.vue'

// —— 简历业务：列表 + 当前查看的详情 + 面试 ——
const resumes = ref([])
const currentResume = ref(null)
const interviewResume = ref(null)

const STATUS = {
  success: { label: '解析成功', cls: 'ok' },
  unsupported: { label: '暂不支持', cls: 'warn' },
  failed: { label: '解析失败', cls: 'bad' },
  pending: { label: '解析中', cls: 'warn' },
}

async function refreshList() {
  try {
    resumes.value = await get('/api/resumes')
  } catch {
    /* 后端没起时列表留空，各组件内嵌错误提示 */
  }
}

async function onUploaded(resume) {
  currentResume.value = resume
  await refreshList()
}

async function onDeleted(id) {
  if (currentResume.value?.id === id) currentResume.value = null
  await refreshList()
}

function startInterview() {
  if (currentResume.value?.parse_status === 'success') interviewResume.value = currentResume.value
}

async function onSelect(id) {
  interviewResume.value = null
  currentResume.value = null
  try {
    currentResume.value = await get(`/api/resumes/${id}`)
  } catch {
    /* 网络异常时详情区留空，重试即可 */
  }
}

onMounted(refreshList)

// 暴露给 App.vue：登录/退出成功后主动刷新列表
defineExpose({ refreshList })
</script>

<template>
  <p class="tagline">
    <b>上传简历</b> · AI 深度分析 · <b>模拟实战面试</b> —— 求职路上的私人面试官
  </p>

  <UploadCard @uploaded="onUploaded" />
  <ResumeList
    :resumes="resumes"
    :current-id="currentResume?.id"
    @select="onSelect"
    @deleted="onDeleted"
  />

  <section v-if="currentResume" class="card detail">
    <div class="detail-head">
      <h2 class="detail-name">{{ currentResume.filename }}</h2>
      <span class="badge" :class="STATUS[currentResume.parse_status]?.cls">
        {{ STATUS[currentResume.parse_status]?.label ?? currentResume.parse_status }}
      </span>
    </div>
    <p class="meta">
      {{ currentResume.page_count ?? '-' }} 页 ·
      {{ currentResume.file_size ? Math.round(currentResume.file_size / 1024) : '-' }} KB ·
      {{ new Date(currentResume.created_at).toLocaleString() }}
    </p>
    <p v-if="currentResume.parse_status !== 'success'" class="msg warn">
      {{ currentResume.parse_error }}
    </p>
    <pre v-else class="raw-text">{{ currentResume.raw_text }}</pre>
    <div v-if="currentResume.parse_status === 'success'" class="detail-actions">
      <button class="btn btn-primary" @click="startInterview">🤖 开始模拟面试</button>
    </div>
  </section>

  <InterviewChat
    v-if="interviewResume"
    :key="interviewResume.id"
    :resume="interviewResume"
    @close="interviewResume = null"
  />

  <AnalysisReport
    v-if="currentResume?.parse_status === 'success'"
    :key="currentResume.id"
    :resume="currentResume"
  />
</template>

<style scoped>
.tagline {
  text-align: center;
  font-size: 13px;
  color: var(--c-muted);
  letter-spacing: 0.5px;
  margin: 2px 0 0;
}
.tagline b {
  color: var(--c-primary-dark);
  font-weight: 600;
}

/* —— 简历详情卡 —— */
.detail-head {
  display: flex;
  align-items: center;
  gap: 10px;
}
.detail-name {
  margin: 0;
  font-size: 16px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.meta {
  margin: 8px 0 12px;
  color: var(--c-muted);
  font-size: 12.5px;
}
.raw-text {
  background: var(--c-bg-soft);
  border: 1px solid var(--c-border);
  border-radius: 10px;
  padding: 14px;
  font-size: 12.5px;
  line-height: 1.8;
  color: #475569;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 420px;
  overflow: auto;
  margin: 0;
}
.detail-actions {
  display: flex;
  gap: 10px;
  margin-top: 14px;
}
</style>
