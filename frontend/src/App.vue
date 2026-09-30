<script setup>
import { computed, defineAsyncComponent, onMounted, ref } from 'vue'
import { get, post } from './api.js'
import ChatView from './components/ChatView.vue'
import LoginView from './components/LoginView.vue'
import Icon from './components/Icon.vue'
import HistoryView from './components/HistoryView.vue'
// 管理端三视图仅 admin 可达：异步分包，普通用户首屏不下载这部分代码
const AdminPanel = defineAsyncComponent(() => import('./components/AdminPanel.vue'))
const KbAdminView = defineAsyncComponent(() => import('./components/KbAdminView.vue'))
import AgentChatView from './components/agent/AgentChatView.vue'
import ResumeView from './components/ResumeView.vue'
import InterviewView from './components/InterviewView.vue'
import QuestionGenView from './components/QuestionGenView.vue'
import AudioView from './components/AudioView.vue'
import ProfileDialog from './components/ProfileDialog.vue'
import { avatarPreset } from './utils.js'

// —— 布局与视图 ——
const activeView = ref('resume') // resume / interview / audio / question-gen / chat / history / admin / kb-admin / agent
const collapsed = ref(false)
const viewRef = ref(null) // 动态组件实例引用，用于调用 ResumeView.refreshList
const viewEpoch = ref(0) // 登录/登出时 +1：重挂载全部视图，清空 KeepAlive 里的跨账号缓存
const pendingResumeId = ref(null) // ResumeView → InterviewView 的「拿这份简历去面试」交接
const pendingBankId = ref(null) // QuestionGenView → InterviewView 的「拿这套题去面试」交接

// —— 用户与登录 ——
const currentUser = ref(null)
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
  resume: ResumeView,
  interview: InterviewView,
  'question-gen': QuestionGenView,
  audio: AudioView,
  chat: ChatView,
  history: HistoryView,
  admin: AdminPanel,
  'kb-admin': KbAdminView,
  agent: AgentChatView,
}
const currentViewComponent = computed(() => viewComponents[activeView.value] || ResumeView)

const isAdmin = computed(() => currentUser.value?.role === 'admin')

// —— 侧边栏导航项：全角色统一（v4.2.1）——
// 四业务模块 + 使用日志（本人口径）+ 数据看板（管理员全站 / 普通用户本人，AdminPanel 内按角色分叉）；
// 在线对话 / 语料库管理 / AI 客服 / 首页落地页 / 个人中心导航按指示先去掉（代码保留）
const navItems = computed(() => {
  return [
    { key: 'resume', label: '简历评估', icon: 'file' },
    { key: 'audio', label: '录音分析', icon: 'mic' },
    { key: 'question-gen', label: '面试题生成', icon: 'pen' },
    { key: 'interview', label: '模拟面试', icon: 'chat' },
    { key: 'history', label: '使用日志', icon: 'list' },
    { key: 'admin', label: '数据看板', icon: 'chart' },
  ]
})

function selectView(item) {
  // 登录后才进系统（v4.2.1 独立登录首屏），导航点击不再需要 requireAuth 拦截分支
  showUserMenu.value = false
  pendingResumeId.value = null
  activeView.value = item.key
}

// 业务视图内部跳转（首页入口卡 / 简历评估「去模拟面试」/ 面试空态引导）
function gotoView(key, resumeId = null, bankId = null) {
  pendingResumeId.value = resumeId
  pendingBankId.value = bankId
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
    currentUser.value = null // 回到独立登录首屏（v4.2.1）
    showUserMenu.value = false
    pendingResumeId.value = null
    viewEpoch.value += 1 // 重挂载全部视图：KeepAlive 里缓存的上一账号状态必须清空
    viewRef.value?.refreshList?.()
  }
}

function onLoggedIn(user) {
  currentUser.value = user // 登录成功 → 直落简历评估（第一个业务模块）
  activeView.value = 'resume'
  viewEpoch.value += 1 // 换账号登录同样重挂载，避免读到上个账号的会话缓存
  viewRef.value?.refreshList?.()
}

onMounted(loadUser)
</script>

<template>
  <!-- v4.2.1 独立登录首屏：未登录只见登录页，登录成功才跳转进入系统 -->
  <LoginView v-if="!currentUser" @logged-in="onLoggedIn" />
  <div v-else class="shell">
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
                  <div v-if="currentUser.email" class="dp-row"><span class="dp-label">邮箱</span><span class="dp-value dp-ellipsis">{{ currentUser.email }}</span></div>
                  <div class="dp-row"><span class="dp-label">注册时间</span><span class="dp-value">{{ new Date(currentUser.created_at).toLocaleString() }}</span></div>
                </div>
                <button class="dropdown-item" @click="showProfile = true; showUserMenu = false"><Icon name="id" :size="14" /> 查看个人信息</button>
                <button class="dropdown-item" @click="showProfile = true; showUserMenu = false"><Icon name="key" :size="14" /> 修改密码</button>
                <button class="dropdown-item" @click="logout"><Icon name="logout" :size="14" /> 退出登录</button>
              </div>
              <!-- 点击外部关闭 -->
              <div v-if="showUserMenu" class="dropdown-backdrop" @click="showUserMenu = false"></div>
            </div>
          </template>
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
            <Icon class="nav-icon" :name="item.icon" :size="17" />
            <span class="nav-label">{{ item.label }}</span>
          </button>
        </nav>
        <button class="collapse-btn" @click="collapsed = !collapsed" :title="collapsed ? '展开侧边栏' : '折叠侧边栏'">
          <span class="collapse-icon">{{ collapsed ? '»' : '«' }}</span>
          <span class="nav-label">{{ collapsed ? '展开' : '折叠' }}</span>
        </button>
      </aside>

      <main class="content">
        <div class="page" :class="{ wide: ['admin', 'agent'].includes(activeView) }">
          <!-- 视图切换：不用 KeepAlive —— 实测 KeepAlive+动态组件在本项目下
               切换时 patch 崩溃（deactivate is not a function），主区停在旧视图；
               移除后恢复。代价是切视图丢组件内状态，各视图 onMounted 自行刷新。 -->
          <ResumeView
            v-if="activeView === 'resume'"
            :key="viewEpoch"
            ref="viewRef"
            @navigate="gotoView"
          />
          <InterviewView
            v-else-if="activeView === 'interview'"
            :key="viewEpoch"
            :pending-resume-id="pendingResumeId"
            :pending-bank-id="pendingBankId"
            @navigate="gotoView"
            @pending-consumed="pendingResumeId = null; pendingBankId = null"
          />
          <QuestionGenView
            v-else-if="activeView === 'question-gen'"
            :key="viewEpoch"
            @navigate="gotoView"
          />
          <AudioView v-else-if="activeView === 'audio'" :key="viewEpoch" />
          <AdminPanel
            v-else-if="activeView === 'admin'"
            :key="viewEpoch"
            ref="viewRef"
            :user="currentUser"
            @logout="logout"
          />
          <component :is="currentViewComponent" v-else :key="viewEpoch" ref="viewRef" @logout="logout" />
        </div>
      </main>
    </div>

    <!-- —— AI 客服悬浮窗：v4.2.1 按"其余先去掉"指示收起（组件保留） —— -->

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
  background: var(--c-ink);
  border-right: 1px solid var(--c-ink-border);
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
  color: var(--c-ink-muted);
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
  background: var(--c-ink-2);
  color: #fff;
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
  color: var(--c-ink-muted);
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
  white-space: nowrap;
  margin-top: 8px;
}
.collapse-btn:hover {
  background: rgb(255 255 255 / 8%);
  color: var(--c-ink-text);
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
