<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { get, post } from './api.js'
import { router } from './router.js'
import LoginView from './components/LoginView.vue'
import Icon from './components/Icon.vue'
import ProfileDialog from './components/ProfileDialog.vue'
import { avatarPreset, fmtDateTime } from './utils.js'

// —— 视图切换（v4.3 P1-5c 起 hash 路由驱动）：深链/刷新/后退不再丢视图；
// 组件分包与映射关系全部移到 router.js，本文件只管布局、登录态与导航交互。——
const route = useRoute()
const activeView = computed(() => route.meta.key || 'resume')
const collapsed = ref(false)
const viewRef = ref(null) // 当前视图实例引用，用于调用 ResumeView.refreshList
const viewEpoch = ref(0) // 登录/登出时 +1：重挂载全部视图，清空跨账号缓存
// ResumeView/QuestionGenView → InterviewView 的「拿这份简历/这套题去面试」交接：
// 放进 query（/interview?resume=1&bank=2），刷新后交接参数不丢，消费后清 URL
const pendingResumeId = computed(() => (route.query.resume ? Number(route.query.resume) : null))
const pendingBankId = computed(() => (route.query.bank ? Number(route.query.bank) : null))

// —— 用户与登录 ——
const currentUser = ref(null)
const showUserMenu = ref(false)
const showProfile = ref(false) // v4.2 个人信息弹窗

// 头像：预设表情优先（v4.2），未设置回落邮箱首字母
const avatarPresetInfo = computed(() => avatarPreset(currentUser.value))
const avatarLetter = computed(() => {
  if (avatarPresetInfo.value) return avatarPresetInfo.value.emoji
  // 回落顺序：邮箱首字符 → 用户名首字符（v4.2.1 注册无邮箱字段，避免头像显示 ?）
  const ch = currentUser.value?.email?.trim()?.[0] || currentUser.value?.username?.trim()?.[0]
  return ch ? ch.toUpperCase() : '?'
})
const avatarColor = computed(() => avatarPresetInfo.value?.color || null)

const isAdmin = computed(() => currentUser.value?.role === 'admin')

// —— 侧边栏导航项：全角色统一（v4.2.1）——
// 五业务模块（含 AI 客服）+ 使用日志（本人口径）+ 数据看板（管理员全站 / 普通用户本人，
// AdminPanel 内按角色分叉）；在线对话 / 语料库管理不在导航露出但路由可达（代码保留，方案 §2.2）
const navItems = [
  { key: 'resume', label: '简历评估', icon: 'file' },
  { key: 'audio', label: '录音分析', icon: 'mic' },
  { key: 'question-gen', label: '面试题生成', icon: 'pen' },
  { key: 'interview', label: '模拟面试', icon: 'chat' },
  { key: 'agent', label: 'AI 客服', icon: 'spark' },
  { key: 'history', label: '使用日志', icon: 'list' },
  { key: 'admin', label: '数据看板', icon: 'chart' },
]

function selectView(item) {
  // 登录后才进系统（v4.2.1 独立登录首屏）；push 不带 query，跨模块切换天然丢弃交接参数
  showUserMenu.value = false
  router.push(`/${item.key}`)
}

// 业务视图内部跳转（简历评估「去模拟面试」/ 面试题生成「拿这套题去面试」/ 空态引导）
function gotoView(key, resumeId = null, bankId = null) {
  const query = {}
  if (resumeId != null) query.resume = String(resumeId)
  if (bankId != null) query.bank = String(bankId)
  router.push({ path: `/${key}`, query })
}

// InterviewView 消费完交接参数后清 URL（防止刷新页面时重复消费）
function onPendingConsumed() {
  const { resume, bank, ...rest } = route.query
  router.replace({ query: rest })
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
    viewEpoch.value += 1 // 重挂载全部视图：上一账号状态必须清空
    viewRef.value?.refreshList?.()
  }
}

function onLoggedIn(user) {
  currentUser.value = user
  viewEpoch.value += 1 // 换账号登录同样重挂载，避免读到上个账号的会话缓存
  // 落地路由：默认访问 / 会被 redirect 到 /resume（登录后直落简历评估）；
  // 未登录深链（如 /#/interview?resume=1）登录后原地进入，交接参数不丢
  viewRef.value?.refreshList?.()
}

onMounted(loadUser)
</script>

<template>
  <!-- v4.2.1 独立登录首屏：未登录只见登录页，登录成功才跳转进入系统 -->
  <LoginView v-if="!currentUser" @logged-in="onLoggedIn" />
  <div v-else class="shell">
    <!-- —— 顶栏 —— -->
    <header class="topbar">
      <div class="topbar-inner">
        <div class="brand">
          <svg class="logo-svg" viewBox="0 0 32 32" fill="none" aria-hidden="true">
            <path d="M4 8a4 4 0 0 1 4-4h16a4 4 0 0 1 4 4v10a4 4 0 0 1-4 4H12l-6 5v-5a4 4 0 0 1-2-3.46V8z" fill="url(#logo-g)" />
            <circle cx="11" cy="13" r="1.8" fill="#fff" />
            <circle cx="16" cy="13" r="1.8" fill="#fff" />
            <circle cx="21" cy="13" r="1.8" fill="#fff" />
            <defs>
              <linearGradient id="logo-g" x1="4" y1="4" x2="28" y2="28">
                <stop stop-color="#10b981" />
                <stop offset="1" stop-color="#059669" />
              </linearGradient>
            </defs>
          </svg>
          <span class="brand-text">AI 简历分析 <span class="plus">+</span> 模拟面试</span>
        </div>
        <div class="topbar-right">
          <template v-if="currentUser">
            <div class="user-menu-wrap">
              <button class="avatar-btn" :title="currentUser.username" @click="showUserMenu = !showUserMenu">
                <span class="avatar" :style="avatarColor ? { background: avatarColor } : {}">{{ avatarLetter }}</span>
              </button>
              <!-- 下拉菜单（v4.2：个人信息概览 + 三入口，对齐参考页面形态） -->
              <div v-if="showUserMenu" class="user-dropdown">
                <div class="dropdown-profile">
                  <div class="dp-row"><span class="dp-label">用户名</span><span class="dp-value">{{ currentUser.username }}</span></div>
                  <div class="dp-row"><span class="dp-label">手机号</span><span class="dp-value">{{ currentUser.phone || '-' }}</span></div>
                  <div v-if="currentUser.email" class="dp-row"><span class="dp-label">邮箱</span><span class="dp-value dp-ellipsis">{{ currentUser.email }}</span></div>
                  <div class="dp-row"><span class="dp-label">注册时间</span><span class="dp-value">{{ fmtDateTime(currentUser.created_at) }}</span></div>
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
            :title="collapsed ? item.label : ''"
            @click="selectView(item)"
          >
            <Icon class="nav-icon" :name="item.icon" :size="17" />
            <span class="nav-label">{{ item.label }}</span>
          </button>
        </nav>
        <button class="collapse-btn" :title="collapsed ? '展开侧边栏' : '折叠侧边栏'" @click="collapsed = !collapsed">
          <Icon :name="collapsed ? 'chevrons-right' : 'chevrons-left'" :size="15" />
          <span class="nav-label">{{ collapsed ? '展开' : '折叠' }}</span>
        </button>
      </aside>

      <main class="content">
        <div class="page" :class="{ wide: route.meta.wide }">
          <!-- 视图切换（v4.3 起 router-view 驱动）：不用 KeepAlive —— 实测 KeepAlive+动态组件
               在本项目下切换时 patch 崩溃（deactivate is not a function），移除后恢复；
               代价是切视图丢组件内状态，各视图 onMounted 自行刷新。 -->
          <router-view v-slot="{ Component }">
            <component
              :is="Component"
              :key="viewEpoch"
              ref="viewRef"
              :user="activeView === 'admin' ? currentUser : undefined"
              :pending-resume-id="activeView === 'interview' ? pendingResumeId : undefined"
              :pending-bank-id="activeView === 'interview' ? pendingBankId : undefined"
              @navigate="gotoView"
              @logout="logout"
              @pending-consumed="onPendingConsumed"
            />
          </router-view>
        </div>
      </main>
    </div>

    <!-- —— AI 客服悬浮窗：v4.2.1 按"其余先去掉"指示收起（组件保留） —— -->

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
  background: var(--c-bg);
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
  background: var(--c-primary-light);
  color: var(--c-primary-dark);
  font-size: var(--fs-md);
  font-weight: 600;
  transition: transform var(--dur-fast) var(--ease);
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
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-pop);
  padding: var(--sp-2);
  z-index: var(--z-pop);
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
  gap: var(--sp-3);
  font-size: var(--fs-xs);
  padding: var(--sp-1) 0;
}
.dp-label {
  color: var(--c-faint);
  flex-shrink: 0;
}
.dp-value {
  color: var(--c-text);
  font-weight: 500;
  text-align: right;
}
.dp-ellipsis {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 150px;
}
.dropdown-item {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  width: 100%;
  text-align: left;
  border: 0;
  background: transparent;
  padding: var(--sp-2);
  border-radius: var(--radius-sm);
  font-size: var(--fs-sm);
  font-family: inherit;
  color: var(--c-text-2);
  cursor: pointer;
  transition:
    background var(--dur-fast) var(--ease),
    color var(--dur-fast) var(--ease);
}
.dropdown-item:hover {
  background: var(--c-hover);
  color: var(--c-text);
}
.dropdown-backdrop {
  position: fixed;
  inset: 0;
  z-index: calc(var(--z-pop) - 5);
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
  padding: var(--sp-4) var(--sp-2);
  transition: width 0.25s var(--ease);
  overflow: hidden;
}
.sidebar.collapsed {
  width: 64px;
  padding: var(--sp-4) var(--sp-2);
}
.side-nav {
  display: flex;
  flex-direction: column;
  gap: var(--sp-1);
}
.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px var(--sp-3);
  border: 0;
  background: transparent;
  border-radius: var(--radius-md);
  font-size: var(--fs-sm);
  font-family: inherit;
  color: var(--c-ink-muted);
  cursor: pointer;
  transition:
    background var(--dur-fast) var(--ease),
    color var(--dur-fast) var(--ease);
  white-space: nowrap;
  text-align: left;
  width: 100%;
}
.nav-item:hover {
  background: rgb(255 255 255 / 6%);
  color: var(--c-ink-text);
}
.nav-item.active {
  background: var(--c-ink-2);
  color: #fff;
  font-weight: 600;
  box-shadow: inset 2px 0 0 var(--c-primary);
}
.nav-icon {
  flex-shrink: 0;
  width: 20px;
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
  padding: 8px var(--sp-3);
  border: 0;
  background: transparent;
  border-radius: var(--radius-md);
  font-size: var(--fs-xs);
  font-family: inherit;
  color: var(--c-ink-muted);
  cursor: pointer;
  transition:
    background var(--dur-fast) var(--ease),
    color var(--dur-fast) var(--ease);
  white-space: nowrap;
  margin-top: var(--sp-2);
}
.collapse-btn:hover {
  background: rgb(255 255 255 / 8%);
  color: var(--c-ink-text);
}
.sidebar.collapsed .collapse-btn {
  justify-content: center;
  padding: 8px 0;
}

/* —— 内容区 —— */
.content {
  flex: 1;
  overflow-y: auto;
  padding: var(--sp-5) var(--sp-6);
}
.page {
  max-width: 960px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: var(--sp-4);
}
.page.wide {
  max-width: 1100px;
}

/* —— 窄屏兜底：≤900px 侧边栏自动收缩为图标条 —— */
@media (max-width: 900px) {
  .sidebar {
    width: 64px !important;
    padding: var(--sp-4) var(--sp-2) !important;
  }
  .sidebar .nav-label,
  .sidebar .collapse-btn {
    display: none !important;
  }
  .sidebar .nav-item {
    justify-content: center;
    padding: 8px 0;
  }
  .content {
    padding: var(--sp-4) var(--sp-3);
  }
  .brand-text {
    display: none;
  }
}
</style>
