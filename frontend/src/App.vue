<script setup>
import { onMounted, ref } from 'vue'
import UploadCard from './components/UploadCard.vue'
import ResumeList from './components/ResumeList.vue'
import AnalysisReport from './components/AnalysisReport.vue'
import InterviewChat from './components/InterviewChat.vue'
import LoginPanel from './components/LoginPanel.vue'
import HistoryView from './components/HistoryView.vue'

// —— 健康检查（缩成页脚状态条）——
const health = ref(null)
const healthLabel = {
  ok: '运行中',
  degraded: '异常',
  unreachable: '无法连接',
  connected: '已连接',
  disconnected: '未连接',
}

// —— 简历业务：列表 + 当前查看的详情 ——
const resumes = ref([])
const currentResume = ref(null)
const interviewResume = ref(null)
const currentUser = ref(null)
const showLogin = ref(false)
const showHistory = ref(false)

const STATUS = {
  success: { label: '解析成功', cls: 'ok' },
  unsupported: { label: '暂不支持', cls: 'warn' },
  failed: { label: '解析失败', cls: 'bad' },
  pending: { label: '解析中', cls: 'warn' },
}

async function loadUser() {
  const res = await fetch('/api/auth/me', { credentials: 'include' })
  if (res.ok) currentUser.value = await res.json()
}

async function logout() {
  await fetch('/api/auth/logout', { method: 'POST', credentials: 'include' })
  currentUser.value = null
  showHistory.value = false
  await refreshList()
}

function onLoggedIn(user) {
  currentUser.value = user
  showLogin.value = false
  refreshList()
}

async function refreshList() {
  try {
    const res = await fetch('/api/resumes')
    if (res.ok) resumes.value = await res.json()
  } catch {
    /* 后端没起时列表留空，页脚健康条会给出提示 */
  }
}

async function onUploaded(resume) {
  currentResume.value = resume
  await refreshList()
}

function startInterview() {
  if (currentResume.value?.parse_status === 'success') interviewResume.value = currentResume.value
}

async function onSelect(id) {
  interviewResume.value = null
  currentResume.value = null
  try {
    const res = await fetch(`/api/resumes/${id}`)
    if (res.ok) currentResume.value = await res.json()
  } catch {
    /* 网络异常时详情区留空，重试即可 */
  }
}

onMounted(async () => {
  loadUser()
  refreshList()
  try {
    const res = await fetch('/api/health') // 走 Vite 代理到后端，同源无跨域
    health.value = await res.json()
  } catch {
    health.value = { status: 'unreachable', database: 'disconnected', version: '-' }
  }
})
</script>

<template>
  <main class="page">
    <header>
      <h1>AI 简历分析 <span class="plus">+</span> 模拟面试</h1>
      <p class="subtitle">阶段 3 · 文字模拟面试</p>
    </header>

    <div class="toolbar">
      <span v-if="currentUser">已登录：{{ currentUser.email }}</span>
      <button v-if="currentUser" class="small-btn" @click="showHistory = !showHistory">{{ showHistory ? '收起历史' : '我的历史' }}</button>
      <button v-if="currentUser" class="small-btn" @click="logout">退出</button>
      <button v-else class="small-btn" @click="showLogin = !showLogin">登录 / 注册</button>
    </div>
    <LoginPanel v-if="showLogin && !currentUser" @logged-in="onLoggedIn" />
    <HistoryView v-if="showHistory && currentUser" />
    <UploadCard @uploaded="onUploaded" />
    <ResumeList :resumes="resumes" :current-id="currentResume?.id" @select="onSelect" />

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
      <button v-if="currentResume.parse_status === 'success'" class="interview-button" @click="startInterview">
        开始模拟面试
      </button>
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

    <footer class="health">
      <span :class="health?.status === 'ok' ? 'ok-text' : 'bad-text'">
        后端 {{ healthLabel[health?.status] || '检测中…' }}
      </span>
      <span :class="health?.database === 'connected' ? 'ok-text' : 'bad-text'">
        数据库 {{ healthLabel[health?.database] || '检测中…' }}
      </span>
      <span class="ver">v{{ health?.version || '…' }}</span>
    </footer>
  </main>
</template>

<style scoped>
.page {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
  font-family: system-ui, 'Microsoft YaHei', sans-serif;
  background: #f5f7fa;
  padding: 32px 16px 40px;
}

.toolbar {
  width: 100%;
  max-width: 680px;
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  color: #6b7280;
  font-size: 12px;
}

.small-btn {
  border: 0;
  border-radius: 7px;
  padding: 6px 10px;
  background: #ecfdf5;
  color: #047857;
  cursor: pointer;
  font-size: 12px;
}

header h1 {
  font-size: 26px;
  color: #1f2937;
  margin: 0;
}

.plus {
  color: #10b981;
}

.subtitle {
  color: #6b7280;
  margin: 6px 0 8px;
  text-align: center;
}

.card {
  background: #fff;
  border-radius: 12px;
  box-shadow: 0 1px 4px rgb(0 0 0 / 8%);
  padding: 20px 24px;
  width: 100%;
  max-width: 680px;
  box-sizing: border-box;
}

.detail-head {
  display: flex;
  align-items: center;
  gap: 10px;
}

.detail-name {
  margin: 0;
  font-size: 17px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.badge {
  padding: 2px 12px;
  border-radius: 999px;
  font-size: 12px;
  white-space: nowrap;
  background: #e5e7eb;
  color: #4b5563;
}

.badge.ok {
  background: #d1fae5;
  color: #047857;
}

.badge.warn {
  background: #fef3c7;
  color: #92400e;
}

.badge.bad {
  background: #fee2e2;
  color: #b91c1c;
}

.meta {
  margin: 8px 0 12px;
  color: #6b7280;
  font-size: 13px;
}

.msg {
  border-radius: 8px;
  padding: 8px 12px;
  font-size: 13px;
}

.msg.warn {
  background: #fef3c7;
  color: #92400e;
}

.raw-text {
  background: #f8fafc;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 14px;
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 420px;
  overflow: auto;
  margin: 0;
}

.health {
  display: flex;
  gap: 18px;
  justify-content: center;
  font-size: 12px;
  color: #6b7280;
  padding-top: 8px;
}

.ok-text {
  color: #047857;
}

.bad-text {
  color: #b91c1c;
}
</style>
