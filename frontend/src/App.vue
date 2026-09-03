<script setup>
import { computed, onMounted, ref } from 'vue'
import { get, post } from './api.js'
import UploadCard from './components/UploadCard.vue'
import ResumeList from './components/ResumeList.vue'
import AnalysisReport from './components/AnalysisReport.vue'
import InterviewChat from './components/InterviewChat.vue'
import Playground from './components/Playground.vue'
import LoginPanel from './components/LoginPanel.vue'
import HistoryView from './components/HistoryView.vue'
import AdminPanel from './components/AdminPanel.vue'

// —— 布局与视图 ——
const activeView = ref('home') // home / playground / history / admin
const collapsed = ref(false)

// —— 用户与登录 ——
const currentUser = ref(null)
const showLogin = ref(false)

// —— 简历业务：列表 + 当前查看的详情（提交②迁入 HomeView）——
const resumes = ref([])
const currentResume = ref(null)
const interviewResume = ref(null)

const STATUS = {
  success: { label: '解析成功', cls: 'ok' },
  unsupported: { label: '暂不支持', cls: 'warn' },
  failed: { label: '解析失败', cls: 'bad' },
  pending: { label: '解析中', cls: 'warn' },
}

// —— 侧边栏导航项：按登录态/角色计算 ——
const navItems = computed(() => {
  const items = [
    { key: 'home', label: '首页', icon: '🏠', requireAuth: false },
    { key: 'playground', label: 'Playground', icon: '🧪', requireAuth: false },
    { key: 'history', label: '我的历史', icon: '📋', requireAuth: true },
  ]
  if (currentUser.value?.role === 'admin') {
    items.push({ key: 'admin', label: '管理面板', icon: '⚙️', requireAuth: true, requireAdmin: true })
  }
  return items
})

function selectView(item) {
  // 提交③：未登录点需登录项改为弹登录模态；此处先直接切换
  activeView.value = item.key
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
    showLogin.value = false
    if (activeView.value === 'history' || activeView.value === 'admin') {
      activeView.value = 'home'
    }
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

onMounted(async () => {
  loadUser()
  refreshList()
})
</script>

<template>
  <div class="shell">
    <!-- —— 顶栏 —— -->
    <header class="topbar">
      <div class="topbar-inner">
        <div class="brand">
          <svg class="logo-svg" viewBox="0 0 32 32" fill="none" aria-hidden="true">
            <path d="M4 8a4 4 0 0 1 4-4h16a4 4 0 0 1 4 4v10a4 4 0 0 1-4 4H12l-6 5v-5a4 4 0 0 1-2-3.46V8z" fill="url(#logo-g)"/>
            <circle cx="11" cy="13" r="1.8" fill="#fff"/>
            <circle cx="16" cy="13" r="1.8" fill="#fff"/>
            <circle cx="21" cy="13" r="1.8" fill="#fff"/>
            <defs>
              <linearGradient id="logo-g" x1="4" y1="4" x2="28" y2="28">
                <stop stop-color="#10b981"/>
                <stop offset="1" stop-color="#059669"/>
              </linearGradient>
            </defs>
          </svg>
          <span class="brand-text">AI 简历分析 <span class="plus">+</span> 模拟面试</span>
        </div>
        <div class="topbar-right">
          <template v-if="currentUser">
            <span class="whoami">{{ currentUser.email }}</span>
            <button class="btn btn-ghost topbar-btn" @click="logout">退出</button>
          </template>
          <button v-else class="btn btn-ghost topbar-btn" @click="showLogin = true">登录 / 注册</button>
        </div>
      </div>
    </header>

    <!-- —— 主体：侧边栏 + 内容区 —— -->
    <div class="layout-row">
      <aside class="sidebar" :class="{ collapsed }">
        <nav class="side-nav">
          <button
            v-for="item in navItems"
            :key="item.key"
            class="nav-item"
            :class="{ active: activeView === item.key }"
            @click="selectView(item)"
            :title="collapsed ? item.label : ''"
          >
            <span class="nav-icon">{{ item.icon }}</span>
            <span class="nav-label">{{ item.label }}</span>
          </button>
        </nav>
        <button class="collapse-btn" @click="collapsed = !collapsed" :title="collapsed ? '展开侧边栏' : '折叠侧边栏'">
          <span class="collapse-icon">{{ collapsed ? '»' : '«' }}</span>
          <span class="nav-label">{{ collapsed ? '展开' : '折叠' }}</span>
        </button>
      </aside>

      <main class="content">
        <div class="page">
          <!-- —— 首页：简历全业务流（提交②迁入 HomeView）—— -->
          <template v-if="activeView === 'home'">
            <p class="tagline">
              <b>上传简历</b> · AI 深度分析 · <b>模拟实战面试</b> —— 求职路上的私人面试官
            </p>

            <LoginPanel v-if="showLogin && !currentUser" @logged-in="onLoggedIn" />

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

          <!-- —— Playground 独立视图 —— -->
          <Playground v-else-if="activeView === 'playground'" />

          <!-- —— 我的历史（需登录）—— -->
          <HistoryView v-else-if="activeView === 'history' && currentUser" />

          <!-- —— 管理面板（需管理员）—— -->
          <AdminPanel v-else-if="activeView === 'admin' && currentUser?.role === 'admin'" />
        </div>
      </main>
    </div>
  </div>
</template>

<style scoped>
.shell {
  height: 100vh;
  display: flex;
  flex-direction: column;
  font-family: var(--font, system-ui, 'Microsoft YaHei', sans-serif);
  color: var(--c-text);
  background:
    radial-gradient(1200px 400px at 80% -10%, rgb(16 185 129 / 10%), transparent 60%),
    radial-gradient(900px 300px at 10% 0%, rgb(59 130 246 / 5%), transparent 55%),
    #f6f9f7;
}

/* —— 顶栏（吸顶毛玻璃）—— */
.topbar {
  position: sticky;
  top: 0;
  z-index: 20;
  backdrop-filter: blur(12px);
  background: rgb(255 255 255 / 72%);
  border-bottom: 1px solid rgb(229 231 235 / 80%);
  flex-shrink: 0;
}
.topbar-inner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 20px;
  max-width: 1400px;
  margin: 0 auto;
}
.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 16px;
  font-weight: 700;
  white-space: nowrap;
}
.logo-svg {
  width: 30px;
  height: 30px;
  flex-shrink: 0;
  filter: drop-shadow(0 2px 6px rgb(16 185 129 / 30%));
}
.plus {
  color: var(--c-primary);
}
.topbar-right {
  display: flex;
  align-items: center;
  gap: 10px;
}
.whoami {
  color: var(--c-muted);
  font-size: 12.5px;
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.topbar-btn {
  padding: 6px 14px;
  font-size: 12.5px;
}

/* —— 主体行 —— */
.layout-row {
  display: flex;
  flex: 1;
  min-height: 0;
}

/* —— 侧边栏 —— */
.sidebar {
  width: 224px;
  flex-shrink: 0;
  background: rgb(255 255 255 / 60%);
  border-right: 1px solid rgb(229 231 235 / 70%);
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  padding: 16px 10px;
  transition: width 0.25s ease;
  overflow: hidden;
}
.sidebar.collapsed {
  width: 64px;
  padding: 16px 8px;
}
.side-nav {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 12px;
  border: 0;
  background: transparent;
  border-radius: 10px;
  font-size: 13.5px;
  font-family: inherit;
  color: var(--c-text-2);
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
  white-space: nowrap;
  text-align: left;
  width: 100%;
}
.nav-item:hover {
  background: rgb(16 185 129 / 8%);
  color: var(--c-primary-dark);
}
.nav-item.active {
  background: linear-gradient(135deg, rgb(16 185 129 / 14%), rgb(16 185 129 / 6%));
  color: var(--c-primary-dark);
  font-weight: 600;
  box-shadow: inset 2px 0 0 var(--c-primary);
}
.nav-icon {
  font-size: 15px;
  flex-shrink: 0;
  width: 20px;
  text-align: center;
}
.nav-label {
  overflow: hidden;
  text-overflow: ellipsis;
}
.sidebar.collapsed .nav-label {
  display: none;
}
.sidebar.collapsed .nav-item {
  justify-content: center;
  padding: 9px 0;
}

/* —— 折叠按钮 —— */
.collapse-btn {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 12px;
  border: 0;
  background: transparent;
  border-radius: 10px;
  font-size: 12.5px;
  font-family: inherit;
  color: var(--c-faint);
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
  white-space: nowrap;
  margin-top: 8px;
}
.collapse-btn:hover {
  background: rgb(0 0 0 / 4%);
  color: var(--c-muted);
}
.collapse-icon {
  font-size: 16px;
  font-weight: 700;
  width: 20px;
  text-align: center;
  flex-shrink: 0;
}
.sidebar.collapsed .collapse-btn {
  justify-content: center;
  padding: 9px 0;
}

/* —— 内容区 —— */
.content {
  flex: 1;
  overflow-y: auto;
  padding: 24px 28px;
}
.page {
  max-width: 960px;
  margin: 0 auto;
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

/* —— 简历详情卡（提交②迁入 HomeView）—— */
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

/* —— 窄屏兜底：≤900px 侧边栏自动收缩为图标条 —— */
@media (max-width: 900px) {
  .sidebar {
    width: 64px !important;
    padding: 16px 8px !important;
  }
  .sidebar .nav-label,
  .sidebar .collapse-btn {
    display: none !important;
  }
  .sidebar .nav-item {
    justify-content: center;
    padding: 9px 0;
  }
  .content {
    padding: 16px 14px;
  }
  .brand-text {
    display: none;
  }
}
</style>
