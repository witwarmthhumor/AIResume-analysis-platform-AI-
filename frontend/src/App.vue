<script setup>
import { computed, defineAsyncComponent, onMounted, ref } from 'vue'
import { get, post } from './api.js'
import HomeView from './components/HomeView.vue'
import ChatView from './components/ChatView.vue'
import LoginPanel from './components/LoginPanel.vue'
import HistoryView from './components/HistoryView.vue'
// 管理端三视图仅 admin 可达：异步分包，普通用户首屏不下载这部分代码
const AdminPanel = defineAsyncComponent(() => import('./components/AdminPanel.vue'))
const KbAdminView = defineAsyncComponent(() => import('./components/KbAdminView.vue'))
import AgentChatView from './components/agent/AgentChatView.vue'
import AgentWidget from './components/agent/AgentWidget.vue'
import ProfileView from './components/ProfileView.vue'
import ResumeView from './components/ResumeView.vue'
import InterviewView from './components/InterviewView.vue'
import ProfileDialog from './components/ProfileDialog.vue'
import { avatarPreset } from './utils.js'

// —— 布局与视图 ——
const activeView = ref('home') // home / resume / interview / chat / history / admin / kb-admin / agent / profile
const collapsed = ref(false)
const viewRef = ref(null) // 动态组件实例引用，用于调用 ResumeView.refreshList
const viewEpoch = ref(0) // 登录/登出时 +1：重挂载全部视图，清空 KeepAlive 里的跨账号缓存
const pendingView = ref(null) // 未登录点击需登录项时记下目标视图，登录成功后送回去（方案「保留原路径」的内存态实现）
const pendingResumeId = ref(null) // ResumeView → InterviewView 的「拿这份简历去面试」交接

// —— 用户与登录 ——
const currentUser = ref(null)
const showLogin = ref(false)
const showUserMenu = ref(false)
const showProfile = ref(false) // v4.2 个人信息弹窗

// 头像：预设表情优先（v4.2），未设置回落邮箱首字母
const avatarPresetInfo = computed(() => avatarPreset(currentUser.value))
const avatarLetter = computed(() => {
  if (avatarPresetInfo.value) return avatarPresetInfo.value.emoji
  const ch = currentUser.value?.email?.trim()?.[0]
  return ch ? ch.toUpperCase() : '?'
})
const avatarColor = computed(() => avatarPresetInfo.value?.color || null)

// —— 视图组件映射（v4.1 A4：首页落地化，简历评估/模拟面试独立成视图）——
// chat/history/agent 仍保留在映射里：管理端在线对话/使用日志、admin 悬浮客服继续使用，
// 用户端导航已收起（方案 §2.2，代码不删）。
const viewComponents = {
  home: HomeView,
  resume: ResumeView,
  interview: InterviewView,
  chat: ChatView,
  history: HistoryView,
  admin: AdminPanel,
  'kb-admin': KbAdminView,
  agent: AgentChatView,
  profile: ProfileView,
}
const currentViewComponent = computed(() => viewComponents[activeView.value] || HomeView)

const isAdmin = computed(() => currentUser.value?.role === 'admin')

// —— 侧边栏导航项：双端分离 ——
// 管理端口径冻结（方案 §2.2）；用户端收敛为 首页 + 两大业务模块 + 个人中心，
// 业务模块强制登录（requireAuth），AI客服 / 知识库 / Playground 不再露出
const navItems = computed(() => {
  if (isAdmin.value) {
    return [
      { key: 'home', label: '首页', icon: '🏠', requireAuth: false },
      { key: 'chat', label: '在线对话', icon: '💬', requireAuth: false },
      { key: 'history', label: '使用日志', icon: '📋', requireAuth: true },
      { key: 'admin', label: '数据看板', icon: '📊', requireAuth: true },
      { key: 'kb-admin', label: '语料库管理', icon: '📚', requireAuth: true },
    ]
  }
  return [
    { key: 'home', label: '首页', icon: '🏠', requireAuth: false },
    { key: 'resume', label: '简历评估', icon: '📄', requireAuth: true },
    { key: 'interview', label: '模拟面试', icon: '🎤', requireAuth: true },
    { key: 'profile', label: '个人中心', icon: '👤', requireAuth: true },
  ]
})

function selectView(item) {
  // 未登录点需登录项 → 记下目标视图并弹登录模态，登录成功后送回去
  if (item.requireAuth && !currentUser.value) {
    pendingView.value = item.key
    showLogin.value = true
    return
  }
  showUserMenu.value = false
  pendingResumeId.value = null
  activeView.value = item.key
}

// 业务视图内部跳转（首页入口卡 / 简历评估「去模拟面试」/ 面试空态引导）
function gotoView(key, resumeId = null) {
  pendingResumeId.value = resumeId
  activeView.value = key
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
    showUserMenu.value = false
    if (activeView.value !== 'home') activeView.value = 'home'
    pendingView.value = null
    pendingResumeId.value = null
    viewEpoch.value += 1 // 重挂载全部视图：KeepAlive 里缓存的上一账号状态必须清空
    viewRef.value?.refreshList?.()
  }
}

function onLoggedIn(user) {
  currentUser.value = user
  showLogin.value = false
  viewEpoch.value += 1 // 换账号登录同样重挂载，避免读到上个账号的会话缓存
  // 「保留原路径」（内存态）：登录前想去的视图在登录后自动送达
  if (pendingView.value) {
    activeView.value = pendingView.value
    pendingView.value = null
  }
  viewRef.value?.refreshList?.()
}

onMounted(loadUser)
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
            <div class="user-menu-wrap">
              <button class="avatar-btn" @click="showUserMenu = !showUserMenu" :title="currentUser.username">
                <span class="avatar" :style="avatarColor ? { background: avatarColor } : {}">{{ avatarLetter }}</span>
              </button>
              <!-- 下拉菜单（v4.2：个人信息概览 + 三入口，对齐参考页面形态） -->
              <div v-if="showUserMenu" class="user-dropdown">
                <div class="dropdown-profile">
                  <div class="dp-row"><span class="dp-label">用户名</span><span class="dp-value">{{ currentUser.username }}</span></div>
                  <div class="dp-row"><span class="dp-label">手机号</span><span class="dp-value">{{ currentUser.phone || '-' }}</span></div>
                  <div class="dp-row"><span class="dp-label">邮箱</span><span class="dp-value dp-ellipsis">{{ currentUser.email }}</span></div>
                  <div class="dp-row"><span class="dp-label">注册时间</span><span class="dp-value">{{ new Date(currentUser.created_at).toLocaleString() }}</span></div>
                </div>
                <button class="dropdown-item" @click="showProfile = true; showUserMenu = false">🪪 查看个人信息</button>
                <button class="dropdown-item" @click="showProfile = true; showUserMenu = false">🔑 修改密码</button>
                <button class="dropdown-item" @click="logout">退出登录</button>
              </div>
              <!-- 点击外部关闭 -->
              <div v-if="showUserMenu" class="dropdown-backdrop" @click="showUserMenu = false"></div>
            </div>
          </template>
          <button v-else class="btn btn-ghost topbar-btn" @click="pendingView = activeView; showLogin = true">登录 / 注册</button>
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
        <div class="page" :class="{ wide: ['admin', 'agent', 'profile'].includes(activeView) }">
          <!-- 视图切换：不用 KeepAlive —— 实测 KeepAlive+动态组件在本项目下
               切换时 patch 崩溃（deactivate is not a function），主区停在旧视图；
               移除后恢复。代价是切视图丢组件内状态，各视图 onMounted 自行刷新。 -->
          <HomeView v-if="activeView === 'home'" @navigate="gotoView" />
          <ResumeView
            v-else-if="activeView === 'resume'"
            :key="viewEpoch"
            ref="viewRef"
            @navigate="gotoView"
          />
          <InterviewView
            v-else-if="activeView === 'interview'"
            :key="viewEpoch"
            :pending-resume-id="pendingResumeId"
            @navigate="gotoView"
            @pending-consumed="pendingResumeId = null"
          />
          <component :is="currentViewComponent" v-else :key="viewEpoch" ref="viewRef" @logout="logout" />
        </div>
      </main>
    </div>

    <!-- —— 管理端右下角悬浮 AI 客服（仅 admin；用户端无悬浮，用整页 AI客服）—— -->
    <AgentWidget v-if="isAdmin" />

    <!-- —— 登录模态（居中遮罩）—— -->
    <div v-if="showLogin && !currentUser" class="modal-overlay" @click.self="showLogin = false">
      <div class="modal-box">
        <button class="modal-close" @click="showLogin = false" aria-label="关闭登录">✕</button>
        <LoginPanel @logged-in="onLoggedIn" />
      </div>
    </div>

    <!-- —— 个人信息弹窗（v4.2）—— -->
    <ProfileDialog
      v-if="showProfile && currentUser"
      :user="currentUser"
      @saved="currentUser = $event"
      @logout="logout"
      @close="showProfile = false"
    />
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
.topbar-btn {
  padding: 6px 14px;
  font-size: 12.5px;
}

/* —— 头像与下拉菜单 —— */
.user-menu-wrap {
  position: relative;
}
.avatar-btn {
  border: 0;
  background: transparent;
  padding: 0;
  cursor: pointer;
  border-radius: 50%;
}
.avatar {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: 50%;
  background: linear-gradient(135deg, var(--c-primary), var(--c-primary-dark));
  color: #fff;
  font-size: 14px;
  font-weight: 700;
  box-shadow: 0 2px 8px rgb(16 185 129 / 30%);
  transition: transform 0.15s ease;
}
.avatar-btn:hover .avatar {
  transform: scale(1.06);
}
.user-dropdown {
  position: absolute;
  right: 0;
  top: calc(100% + 8px);
  min-width: 200px;
  background: #fff;
  border: 1px solid var(--c-border);
  border-radius: 12px;
  box-shadow: 0 8px 28px rgba(0, 0, 0, 0.12);
  padding: 10px;
  z-index: 30;
  animation: fade-in-up 0.18s ease both;
}
.dropdown-profile {
  padding: 4px 8px 10px;
  border-bottom: 1px dashed var(--c-border);
  margin-bottom: 6px;
}
.dp-row {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  font-size: 12px;
  padding: 3px 0;
}
.dp-label {
  color: var(--c-faint);
  flex-shrink: 0;
}
.dp-value {
  color: var(--c-text);
  font-weight: 600;
  text-align: right;
}
.dp-ellipsis {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 150px;
}
.dropdown-item {
  display: block;
  width: 100%;
  text-align: left;
  border: 0;
  background: transparent;
  padding: 8px;
  border-radius: 8px;
  font-size: 13px;
  font-family: inherit;
  color: var(--c-text-2);
  cursor: pointer;
  transition: background 0.12s ease, color 0.12s ease;
}
.dropdown-item:hover {
  background: rgb(16 185 129 / 8%);
  color: var(--c-primary-dark);
}
.dropdown-backdrop {
  position: fixed;
  inset: 0;
  z-index: 25;
  background: transparent;
}

/* —— 登录模态 —— */
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 50;
  padding: 20px;
  animation: modal-fade 0.2s ease both;
}
.modal-box {
  position: relative;
  width: 100%;
  max-width: 420px;
  animation: modal-pop 0.22s ease both;
}
.modal-close {
  position: absolute;
  top: -12px;
  right: -12px;
  width: 32px;
  height: 32px;
  border-radius: 50%;
  border: 0;
  background: #fff;
  color: var(--c-muted);
  font-size: 14px;
  cursor: pointer;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.15);
  z-index: 1;
  transition: color 0.12s ease, transform 0.12s ease;
}
.modal-close:hover {
  color: var(--c-text);
  transform: scale(1.08);
}
@keyframes modal-fade {
  from { opacity: 0; }
  to { opacity: 1; }
}
@keyframes modal-pop {
  from { opacity: 0; transform: translateY(12px) scale(0.97); }
  to { opacity: 1; transform: translateY(0) scale(1); }
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
.page.wide {
  max-width: 1100px;
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
