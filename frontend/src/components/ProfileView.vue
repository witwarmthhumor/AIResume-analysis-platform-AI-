<script setup>
/* ProfileView —— 用户端「个人中心」（v3.4，必须登录，App 导航已拦截未登录）
   四区块：个人信息卡(含退出) / 本人五卡(第5卡=累计Token) / 近7日用量柱图 / 精简使用日志。
   数据全部是"本人"口径：/api/me/*；日志复用 /api/usage/logs（后端本就按当前用户过滤）。 */
import { computed, onMounted, ref } from 'vue'
import { get } from '../api.js'
import { fmtDateTime, fmtNum, shortDate } from '../utils.js'

const emit = defineEmits(['logout'])

const me = ref(null)
const stats = ref(null)
const usage = ref([])
const logs = ref([])
const logTotal = ref(0)
const error = ref('')

// 日志分页（精简，无筛选）
const page = ref(1)
const pageSize = 10
const logTotalPages = computed(() => Math.max(1, Math.ceil(logTotal.value / pageSize)))

const actionMap = {
  parse: { label: '简历解析', color: '#6B7280', bg: '#F3F4F6' },
  analysis: { label: 'AI分析', color: '#1D4ED8', bg: '#DBEAFE' },
  interview_message: { label: '模拟面试', color: '#92400E', bg: '#FEF3C7' },
  playground: { label: '知识库问答', color: '#065F46', bg: '#D1FAE5' },
  agent: { label: 'AI客服', color: '#047857', bg: '#D1FAE5' },
  agent_create: { label: '新建客服对话', color: '#7C3AED', bg: '#EDE9FE' },
  chat_create: { label: '新建对话', color: '#7C3AED', bg: '#EDE9FE' },
}

const cards = computed(() => {
  const s = stats.value
  if (!s) return []
  return [
    { label: '我的简历', value: s.resumes, color: '#10b981', icon: '📄' },
    { label: '我的分析', value: s.analyses, color: '#3b82f6', icon: '📊' },
    { label: '我的面试', value: s.interviews, color: '#f59e0b', icon: '🎤' },
    { label: '今日 Token', value: s.tokens_today, color: '#06b6d4', icon: '⚡' },
    { label: '累计 Token', value: s.tokens_total, color: '#8b5cf6', icon: '🏆' },
  ]
})

const maxTokens = computed(() => Math.max(1, ...usage.value.map((d) => d.tokens || 0)))
function barHeight(t) {
  return t ? Math.max(4, Math.round((t / maxTokens.value) * 100)) : 0
}
// shortDate / fmtNum / fmtDateTime 来自 utils.js（与 AdminPanel/HistoryView 共享）
const avatarLetter = computed(() => me.value?.email?.trim()?.[0]?.toUpperCase() || '?')

async function loadAll() {
  error.value = ''
  try {
    const [m, s, u] = await Promise.all([get('/api/auth/me'), get('/api/me/stats'), get('/api/me/usage')])
    me.value = m
    stats.value = s
    usage.value = u
    loadLogs()
  } catch (e) {
    error.value = e.message || '加载失败'
  }
}

async function loadLogs() {
  try {
    const res = await get(`/api/usage/logs?page=${page.value}&page_size=${pageSize}`)
    logs.value = res.items
    logTotal.value = res.total
  } catch {
    /* 日志失败不阻塞其余区块 */
  }
}
function goPage(p) {
  if (p < 1 || p > logTotalPages.value) return
  page.value = p
  loadLogs()
}

function logout() {
  // 只 emit：POST /api/auth/logout 由 App.logout 统一发一次（此前会连发两次）
  emit('logout')
}

onMounted(loadAll)
</script>

<template>
  <section class="profile">
    <h2 class="page-title">👤 个人中心</h2>
    <p v-if="error" class="msg error">{{ error }}</p>

    <!-- ① 个人信息卡 -->
    <div class="info-card">
      <div class="info-left">
        <span class="info-avatar">{{ avatarLetter }}</span>
        <div class="info-meta">
          <div class="info-email">{{ me?.email || '加载中…' }}</div>
          <div class="info-tags">
            <span class="role-badge" :class="me?.role === 'admin' ? 'admin' : 'user'">
              {{ me?.role === 'admin' ? '管理员' : '普通用户' }}
            </span>
            <span class="info-since" v-if="me?.created_at">注册于 {{ fmtDateTime(me.created_at) }}</span>
          </div>
        </div>
      </div>
      <button class="logout-btn" @click="logout">退出登录</button>
    </div>

    <!-- ② 本人五卡（第5卡=累计Token） -->
    <div class="stat-row">
      <div v-for="c in cards" :key="c.label" class="stat-card" :style="{ '--card-color': c.color }">
        <div class="stat-top">
          <span class="stat-dot"></span>
          <span class="stat-label">{{ c.label }}</span>
          <span class="stat-icon">{{ c.icon }}</span>
        </div>
        <div class="stat-num">{{ fmtNum(c.value) }}</div>
      </div>
    </div>

    <!-- ③ 近 7 日用量 -->
    <div class="panel-card">
      <div class="panel-head"><h3>📈 我的近 7 日 Token 用量</h3></div>
      <div class="usage-body">
        <div class="chart">
          <div v-for="d in usage" :key="d.date" class="bar-col">
            <div class="bar-wrap">
              <div
                class="bar"
                :class="{ zero: !d.tokens }"
                :style="{ height: barHeight(d.tokens) + '%' }"
                :title="`${d.date}：${fmtNum(d.tokens)} tokens / ${d.calls} 次`"
              ></div>
            </div>
            <div class="bar-label">{{ shortDate(d.date) }}</div>
          </div>
        </div>
        <div class="usage-table">
          <table class="tbl">
            <thead><tr><th>日期</th><th>次数</th><th class="r">Token</th></tr></thead>
            <tbody>
              <tr v-for="d in usage" :key="d.date">
                <td>{{ d.date }}</td>
                <td>{{ d.calls }}</td>
                <td class="r token">{{ fmtNum(d.tokens) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- ④ 精简使用日志 -->
    <div class="panel-card">
      <div class="panel-head"><h3>📋 我的使用日志</h3><span class="count-badge">共 {{ logTotal }} 条</span></div>
      <div class="table-scroll">
        <table class="tbl">
          <thead><tr><th>时间</th><th>动作</th><th>模型</th><th class="r">Token</th></tr></thead>
          <tbody>
            <tr v-for="l in logs" :key="l.id">
              <td class="muted">{{ fmtDateTime(l.created_at) }}</td>
              <td>
                <span
                  class="action-badge"
                  :style="{ background: actionMap[l.action_type]?.bg || '#F3F4F6', color: actionMap[l.action_type]?.color || '#6B7280' }"
                >{{ actionMap[l.action_type]?.label || l.action_type }}</span>
              </td>
              <td>{{ l.model_name || '-' }}</td>
              <td class="r token">{{ fmtNum(l.tokens_total) }}</td>
            </tr>
            <tr v-if="!logs.length"><td colspan="4" class="muted center">暂无使用记录</td></tr>
          </tbody>
        </table>
      </div>
      <div class="pagination">
        <span class="page-info">第 {{ page }}/{{ logTotalPages }} 页</span>
        <div class="page-btns">
          <button :disabled="page <= 1" @click="goPage(page - 1)">上一页</button>
          <button :disabled="page >= logTotalPages" @click="goPage(page + 1)">下一页</button>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.profile {
  display: flex;
  flex-direction: column;
  gap: 14px;
  width: 100%;
}
.page-title {
  font-size: 18px;
  font-weight: 700;
  margin: 0;
}

/* 个人信息卡 */
.info-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #fff;
  border: 1px solid #f3f4f6;
  border-radius: 16px;
  padding: 16px 20px;
  box-shadow: 0 2px 10px rgb(0 0 0 / 4%);
}
.info-left {
  display: flex;
  align-items: center;
  gap: 14px;
}
.info-avatar {
  width: 50px;
  height: 50px;
  border-radius: 50%;
  background: linear-gradient(135deg, #10b981, #059669);
  color: #fff;
  font-size: 22px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 3px 10px rgb(16 185 129 / 30%);
}
.info-email {
  font-size: 15px;
  font-weight: 700;
}
.info-tags {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 5px;
}
.role-badge {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
}
.role-badge.admin {
  background: #d1fae5;
  color: #059669;
}
.role-badge.user {
  background: #f3f4f6;
  color: #6b7280;
}
.info-since {
  font-size: 12px;
  color: #9ca3af;
}
.logout-btn {
  border: 1px solid #fca5a5;
  background: #fff;
  color: #dc2626;
  border-radius: 10px;
  padding: 8px 18px;
  font-size: 13px;
  font-family: inherit;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.12s ease;
}
.logout-btn:hover {
  background: #fef2f2;
}

/* 五卡 */
.stat-row {
  display: flex;
  gap: 12px;
}
.stat-card {
  flex: 1;
  min-width: 0;
  background: #fff;
  border: 1px solid #f3f4f6;
  border-radius: 14px;
  padding: 14px;
  box-shadow: 0 2px 10px rgb(0 0 0 / 4%);
  transition: transform 0.2s ease, box-shadow 0.2s ease, border-color 0.2s ease;
}
.stat-card:hover {
  transform: translateY(-4px);
  box-shadow: 0 10px 28px rgb(0 0 0 / 12%);
  border-color: var(--card-color);
}
.stat-top {
  display: flex;
  align-items: center;
  gap: 6px;
}
.stat-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--card-color);
}
.stat-label {
  font-size: 12px;
  color: #9ca3af;
  flex: 1;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.stat-icon {
  width: 28px;
  height: 28px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  background: color-mix(in srgb, var(--card-color) 12%, transparent);
}
.stat-num {
  font-size: 24px;
  font-weight: 700;
  color: var(--card-color);
  margin-top: 8px;
}

/* 面板卡 */
.panel-card {
  background: #fff;
  border-radius: 18px;
  box-shadow: 0 2px 14px rgb(0 0 0 / 6%);
  padding: 16px 20px;
}
.panel-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}
.panel-head h3 {
  font-size: 15px;
  font-weight: 700;
  margin: 0;
}
.count-badge {
  background: #f3f4f6;
  color: #6b7280;
  font-size: 11px;
  font-weight: 600;
  padding: 2px 10px;
  border-radius: 999px;
}
.usage-body {
  display: flex;
  gap: 18px;
}
.chart {
  flex: 1;
  display: flex;
  align-items: flex-end;
  gap: 8px;
  height: 170px;
  padding: 8px 4px 0;
}
.bar-col {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  height: 100%;
}
.bar-wrap {
  flex: 1;
  width: 100%;
  display: flex;
  align-items: flex-end;
  justify-content: center;
}
.bar {
  width: 60%;
  max-width: 28px;
  background: linear-gradient(180deg, #10b981, #059669);
  border-radius: 4px 4px 0 0;
  min-height: 3px;
  transition: height 0.3s ease;
}
.bar.zero {
  background: #e5e7eb;
}
.bar-label {
  font-size: 9px;
  color: #9ca3af;
  margin-top: 6px;
}
.usage-table {
  flex: 0 0 40%;
  border-left: 1px dashed #e5e7eb;
  padding-left: 16px;
}

/* 表格 */
.table-scroll {
  border: 1px solid #f3f4f6;
  border-radius: 10px;
  max-height: 320px;
  overflow-y: auto;
}
.tbl {
  width: 100%;
  border-collapse: collapse;
  font-size: 12.5px;
}
.tbl thead th {
  position: sticky;
  top: 0;
  background: #fff;
  text-align: left;
  padding: 9px 12px;
  font-size: 12px;
  color: #9ca3af;
  border-bottom: 2px solid #f3f4f6;
  white-space: nowrap;
}
.tbl tbody td {
  padding: 9px 12px;
  border-bottom: 1px solid #f3f4f6;
  white-space: nowrap;
}
.tbl .r {
  text-align: right;
}
.tbl .center {
  text-align: center;
}
.tbl .muted {
  color: #9ca3af;
}
.tbl .token {
  color: #059669;
  font-weight: 600;
}
.action-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 11px;
  font-weight: 600;
}

/* 分页 */
.pagination {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 10px;
}
.page-info {
  font-size: 12px;
  color: #9ca3af;
}
.page-btns {
  display: flex;
  gap: 4px;
}
.page-btns button {
  padding: 4px 12px;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  background: #fff;
  color: #6b7280;
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
}
.page-btns button:hover:not(:disabled) {
  background: #f9fafb;
}
.page-btns button:disabled {
  background: #f3f4f6;
  color: #d1d5db;
  cursor: not-allowed;
}

@media (max-width: 900px) {
  .stat-row {
    flex-wrap: wrap;
  }
  .stat-card {
    flex: 1 1 30%;
  }
  .usage-body {
    flex-direction: column;
  }
  .usage-table {
    flex: none;
    border-left: 0;
    border-top: 1px dashed #e5e7eb;
    padding-left: 0;
    padding-top: 12px;
  }
}
</style>
