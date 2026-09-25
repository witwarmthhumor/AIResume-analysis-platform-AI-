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

// —— 布局与视图 ——
const activeView = ref('home') // home / chat / history / admin
const collapsed = ref(false)
const viewRef = ref(null) // 动态组件实例引用，用于调用 HomeView.refreshList
const viewEpoch = ref(0) // 登录/登出时 +1：重挂载全部视图，清空 KeepAlive 里的跨账号缓存

// —— 用户与登录 ——
const currentUser = ref(null)
const showLogin = ref(false)
const showUserMenu = ref(false)

// 头像首字母（邮箱首字符大写）
const avatarLetter = computed(() => {
  const ch = currentUser.value?.email?.trim()?.[0]
  return ch ? ch.toUpperCase() : '?'
})

// —— 视图组件映射：home/chat/history/admin/kb-admin/agent/profile 七视图（双端按角色可见性由 navItems 控制）——
const viewComponents = {
  home: HomeView,
  chat: ChatView,
  history: HistoryView,
  admin: AdminPanel,
  'kb-admin': KbAdminView,
  agent: AgentChatView,
  profile: ProfileView,
}
const currentViewComponent = computed(() => viewComponents[activeView.value] || HomeView)

const isAdmin = computed(() => currentUser.value?.role === 'admin')

// —— 侧边栏导航项：双端分离（v3.4）——
// 管理端保持现状五项；用户端仅 首页 / AI客服(整页) / 个人中心（必须登录）。
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
    { key: 'agent', label: 'AI客服', icon: '🤖', requireAuth: false },
    { key: 'profile', label: '个人中心', icon: '👤', requireAuth: true },
  ]
})

function selectView(item) {
  // 未登录点需登录项 → 弹登录模态，不切换视图
  if (item.requireAuth && !currentUser.value) {
    showLogin.value = true
    return
  }
  showUserMenu.value = false
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
    showUserMenu.value = false
    if (['history', 'admin', 'kb-admin', 'profile', 'chat', 'agent'].includes(activeView.value)) {
      activeView.value = 'home'
    }
    viewEpoch.value += 1 // 重挂载全部视图：KeepAlive 里缓存的上一账号状态必须清空
    viewRef.value?.refreshList?.()
  }
}

function onLoggedIn(user) {
  currentUser.value = user
  showLogin.value = false
  viewEpoch.value += 1 // 换账号登录同样重挂载，避免读到上个账号的会话缓存
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
              <button class="avatar-btn" @click="showUserMenu = !showUserMenu" :title="currentUser.email">
                <span class="avatar">{{ avatarLetter }}</span>
              </button>
              <!-- 下拉菜单 -->
              <div v-if="showUserMenu" class="user-dropdown">
                <div class="dropdown-email">{{ currentUser.email }}</div>
                <div v-if="currentUser.role === 'admin'" class="dropdown-role">
                  <span class="badge ok">管理员</span>
                </div>
                <button class="dropdown-item" @click="logout">退出登录</button>
              </div>
              <!-- 点击外部关闭 -->
              <div v-if="showUserMenu" class="dropdown-backdrop" @click="showUserMenu = false"></div>
            </div>
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
        <div class="page" :class="{ wide: ['admin', 'agent', 'profile'].includes(activeView) }">
          <!-- 视图切换：不用 KeepAlive —— 实测 KeepAlive+动态组件在本项目下
               切换时 patch 崩溃（deactivate is not a function），主区停在旧视图；
               移除后恢复。代价是切视图丢组件内状态，各视图 onMounted 自行刷新。 -->
          <component :is="currentViewComponent" :key="viewEpoch" ref="viewRef" @logout="logout" />
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
.dropdown-email {
  font-size: 12.5px;
  color: var(--c-text);
  font-weight: 600;
  padding: 6px 8px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.dropdown-role {
  padding: 2px 8px 8px;
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
