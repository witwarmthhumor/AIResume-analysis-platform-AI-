<script setup>
import { onMounted, ref } from 'vue'
import { get, post } from './api.js'
import UploadCard from './components/UploadCard.vue'
import ResumeList from './components/ResumeList.vue'
import AnalysisReport from './components/AnalysisReport.vue'
import InterviewChat from './components/InterviewChat.vue'
import LoginPanel from './components/LoginPanel.vue'
import HistoryView from './components/HistoryView.vue'
import AdminPanel from './components/AdminPanel.vue'

// —— 健康检查（页脚胶囊状态条）——
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
const showAdmin = ref(false)

const STATUS = {
  success: { label: '解析成功', cls: 'ok' },
  unsupported: { label: '暂不支持', cls: 'warn' },
  failed: { label: '解析失败', cls: 'bad' },
  pending: { label: '解析中', cls: 'warn' },
}

async function loadUser() {
  try {
    currentUser.value = await get('/api/auth/me')
  } catch {
    /* 未登录属于正常态 */
  }
}

async function logout() {
  try {
    await post('/api/auth/logout')
  } finally {
    currentUser.value = null
    showHistory.value = false
    showAdmin.value = false
    await refreshList()
  }
}

function onLoggedIn(user) {
  currentUser.value = user
  showLogin.value = false
  refreshList()
}

async function refreshList() {
  try {
    resumes.value = await get('/api/resumes')
  } catch {
    /* 后端没起时列表留空，页脚健康条会给出提示 */
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

onMounted(async () => {
  loadUser()
  refreshList()
  try {
    health.value = await get('/api/health') // 走 Vite 代理到后端，同源无跨域
  } catch {
    health.value = { status: 'unreachable', database: 'disconnected', version: '-' }
  }
})
</script>

<template>
  <div class="shell">
    <nav class="nav">
      <div class="nav-inner">
        <div class="brand">
          <span class="logo">◉</span>
          <span>AI 简历分析 <span class="plus">+</span> 模拟面试</span>
        </div>
        <div class="nav-btns">
          <template v-if="currentUser">
            <span class="whoami">{{ currentUser.email }}</span>
            <button v-if="currentUser.role === 'admin'" class="btn btn-ghost" @click="showAdmin = !showAdmin; showHistory = false">
              {{ showAdmin ? '收起面板' : '管理面板' }}
            </button>
            <button class="btn btn-ghost" @click="showHistory = !showHistory; showAdmin = false">
              {{ showHistory ? '收起历史' : '我的历史' }}
            </button>
            <button class="btn btn-ghost" @click="logout">退出</button>
          </template>
          <button v-else class="btn btn-ghost" @click="showLogin = !showLogin">
            登录 / 注册
          </button>
        </div>
      </div>
    </nav>

    <main class="page">
      <p class="tagline">
        <b>上传简历</b> · AI 深度分析 · <b>模拟实战面试</b> —— 求职路上的私人面试官
      </p>

      <LoginPanel v-if="showLogin && !currentUser" @logged-in="onLoggedIn" />
      <AdminPanel v-if="showAdmin && currentUser?.role === 'admin'" />
      <HistoryView v-if="showHistory && currentUser" />
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

      <footer class="health">
        <span class="hpill">
          <span class="pulse" :class="{ off: health?.status !== 'ok' }"></span>
          后端 {{ healthLabel[health?.status] || '检测中…' }}
        </span>
        <span class="hpill">
          <span class="pulse" :class="{ off: health?.database !== 'connected' }"></span>
          数据库 {{ healthLabel[health?.database] || '检测中…' }}
        </span>
        <span class="hpill ver">v{{ health?.version || '…' }}</span>
      </footer>
    </main>
  </div>
</template>

<style scoped>
.shell {
  min-height: 100vh;
  font-family: var(--font, system-ui, 'Microsoft YaHei', sans-serif);
  color: var(--c-text);
  background:
    radial-gradient(1200px 400px at 80% -10%, rgb(16 185 129 / 10%), transparent 60%),
    radial-gradient(900px 300px at 10% 0%, rgb(59 130 246 / 5%), transparent 55%),
    #f6f9f7;
}

/* —— 吸顶毛玻璃导航 —— */
.nav {
  position: sticky;
  top: 0;
  z-index: 10;
  backdrop-filter: blur(12px);
  background: rgb(255 255 255 / 72%);
  border-bottom: 1px solid rgb(229 231 235 / 80%);
}
.nav-inner {
  max-width: 760px;
  margin: 0 auto;
  padding: 11px 20px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 16.5px;
  font-weight: 700;
  white-space: nowrap;
}
.logo {
  width: 30px;
  height: 30px;
  border-radius: 9px;
  background: linear-gradient(135deg, var(--c-primary), var(--c-primary-dark));
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-size: 14px;
  box-shadow: 0 2px 8px rgb(16 185 129 / 35%);
}
.plus {
  color: var(--c-primary);
}
.nav-btns {
  display: flex;
  align-items: center;
  gap: 8px;
}
.whoami {
  color: var(--c-muted);
  font-size: 12px;
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.nav-btns .btn {
  padding: 6px 12px;
  font-size: 12.5px;
}

/* —— 主内容列 —— */
.page {
  max-width: 760px;
  margin: 0 auto;
  padding: 22px 20px 44px;
  display: flex;
  flex-direction: column;
  gap: 18px;
}
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

/* —— 页脚胶囊状态条 —— */
.health {
  display: flex;
  justify-content: center;
  gap: 10px;
  padding-top: 4px;
}
.hpill {
  display: flex;
  align-items: center;
  gap: 7px;
  background: #fff;
  border: 1px solid var(--c-border);
  border-radius: 999px;
  padding: 6px 14px;
  font-size: 12px;
  color: var(--c-muted);
  box-shadow: var(--shadow);
}
.pulse {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--c-primary);
  animation: pulse-dot 1.6s infinite;
}
.pulse.off {
  background: #ef4444;
  animation: none;
}
.ver {
  font-family: ui-monospace, Consolas, monospace;
}
</style>
