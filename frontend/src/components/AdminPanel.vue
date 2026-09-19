<script setup>
import { computed, nextTick, onMounted, ref } from 'vue'
import { get } from '../api.js'
import { fmtNum, pageNumbers } from '../utils.js'

const stats = ref(null)
const users = ref([])
const usage = ref([])
const error = ref('')

async function load() {
  error.value = ''
  try {
    const [s, u, us] = await Promise.all([
      get('/api/admin/stats'),
      get('/api/admin/users'),
      get('/api/admin/usage'),
    ])
    stats.value = s
    users.value = u
    usage.value = us
  } catch (e) {
    error.value = e.message || '加载失败'
  }
}

// —— 五统计卡片 ——
const cards = computed(() => {
  const s = stats.value
  if (!s) return []
  return [
    { label: '用户', value: s.users, color: '#10b981', icon: '👥' },
    { label: '简历', value: s.resumes, color: '#3b82f6', icon: '📄' },
    { label: '分析', value: s.analyses, color: '#06b6d4', icon: '📊' },
    { label: '面试', value: s.interviews, color: '#f59e0b', icon: '🎤' },
    { label: '今日Token', value: s.tokens_today, color: '#8b5cf6', icon: '⚡' },
  ]
})

// —— 分页：用户列表（每页 10 条） ——
const USER_PAGE_SIZE = 10
const userPage = ref(1)
const userTableRef = ref(null)
const userTotalPages = computed(() => Math.max(1, Math.ceil(users.value.length / USER_PAGE_SIZE)))
const pagedUsers = computed(() => {
  const start = (userPage.value - 1) * USER_PAGE_SIZE
  return users.value.slice(start, start + USER_PAGE_SIZE)
})
async function goUserPage(p) {
  if (p < 1 || p > userTotalPages.value) return
  userPage.value = p
  await nextTick()
  if (userTableRef.value) userTableRef.value.scrollTop = 0
}

// —— 分页：近 7 日用量（每页 5 条） ——
const USAGE_PAGE_SIZE = 5
const usagePage = ref(1)
const usageTableRef = ref(null)
const usageTotalPages = computed(() => Math.max(1, Math.ceil(usage.value.length / USAGE_PAGE_SIZE)))
const pagedUsage = computed(() => {
  const start = (usagePage.value - 1) * USAGE_PAGE_SIZE
  return usage.value.slice(start, start + USAGE_PAGE_SIZE)
})
async function goUsagePage(p) {
  if (p < 1 || p > usageTotalPages.value) return
  usagePage.value = p
  await nextTick()
  if (usageTableRef.value) usageTableRef.value.scrollTop = 0
}

// 页码数组（超过 7 页用省略号折叠）
// —— 柱状图（纯 CSS，全量 7 天） ——
const maxTokens = computed(() => Math.max(1, ...usage.value.map((d) => d.tokens || 0)))
function barHeight(tokens) {
  if (!tokens) return 0
  return Math.max(4, Math.round((tokens / maxTokens.value) * 100))
}
function shortDate(dateStr) {
  const parts = String(dateStr).split('-')
  return parts.length === 3 ? `${Number(parts[1])}/${parts[2]}` : dateStr
}
function isToday(dateStr) {
  const d = new Date(dateStr + 'T00:00:00')
  const now = new Date()
  return d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate()
}

// 注册时间格式化 YYYY/M/D HH:mm
function fmtDateTime(iso) {
  const d = new Date(iso)
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

onMounted(load)
</script>

<template>
  <section class="dashboard">
    <h2 class="page-title">📊 数据看板</h2>
    <p v-if="error" class="msg error">{{ error }}</p>

    <!-- —— 五统计卡片行 —— -->
    <div class="stat-row">
      <div
        v-for="card in cards"
        :key="card.label"
        class="stat-card"
        :style="{ '--card-color': card.color }"
      >
        <div class="stat-top">
          <span class="stat-dot"></span>
          <span class="stat-label">{{ card.label }}</span>
          <span class="stat-icon">{{ card.icon }}</span>
        </div>
        <div class="stat-num">{{ fmtNum(card.value) }}</div>
      </div>
    </div>

    <!-- —— 用户列表卡片 —— -->
    <div class="panel-card">
      <div class="panel-head">
        <h3>👥 用户列表</h3>
        <span class="count-badge">共 {{ users.length }} 人</span>
      </div>
      <div ref="userTableRef" class="table-scroll">
        <table class="tbl">
          <thead>
            <tr><th>ID</th><th>邮箱</th><th>角色</th><th>简历数</th><th>注册时间</th></tr>
          </thead>
          <tbody>
            <tr v-for="u in pagedUsers" :key="u.id">
              <td>{{ u.id }}</td>
              <td class="email">{{ u.email }}</td>
              <td>
                <span class="role-badge" :class="u.role === 'admin' ? 'admin' : 'user'">{{ u.role }}</span>
              </td>
              <td>{{ u.resume_count }}</td>
              <td class="muted">{{ fmtDateTime(u.created_at) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="pagination">
        <span class="page-info">共 {{ users.length }} 条 · 第 {{ userPage }}/{{ userTotalPages }} 页</span>
        <div class="page-btns">
          <button :disabled="userPage <= 1" @click="goUserPage(userPage - 1)">上一页</button>
          <template v-for="(p, i) in pageNumbers(userPage, userTotalPages)" :key="i">
            <span v-if="p === '…'" class="page-ellipsis">…</span>
            <button v-else :class="{ active: p === userPage }" @click="goUserPage(p)">{{ p }}</button>
          </template>
          <button :disabled="userPage >= userTotalPages" @click="goUserPage(userPage + 1)">下一页</button>
        </div>
      </div>
    </div>

    <!-- —— 近 7 日用量卡片 —— -->
    <div class="panel-card">
      <div class="panel-head">
        <h3>📈 近 7 日用量</h3>
      </div>
      <div class="usage-body">
        <!-- 左侧：表格 + 分页 -->
        <div class="usage-left">
          <div ref="usageTableRef" class="table-scroll">
            <table class="tbl">
              <thead>
                <tr><th>日期</th><th>调用次数</th><th>Token</th></tr>
              </thead>
              <tbody>
                <tr v-for="d in pagedUsage" :key="d.date" :class="{ today: isToday(d.date) }">
                  <td>{{ d.date }}</td>
                  <td>{{ d.calls }}</td>
                  <td class="token-cell">{{ fmtNum(d.tokens) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div class="pagination">
            <span class="page-info">共 {{ usage.length }} 条 · 第 {{ usagePage }}/{{ usageTotalPages }} 页</span>
            <div class="page-btns">
              <button :disabled="usagePage <= 1" @click="goUsagePage(usagePage - 1)">上一页</button>
              <template v-for="(p, i) in pageNumbers(usagePage, usageTotalPages)" :key="i">
                <span v-if="p === '…'" class="page-ellipsis">…</span>
                <button v-else :class="{ active: p === usagePage }" @click="goUsagePage(p)">{{ p }}</button>
              </template>
              <button :disabled="usagePage >= usageTotalPages" @click="goUsagePage(usagePage + 1)">下一页</button>
            </div>
          </div>
        </div>

        <!-- 右侧：Token 趋势柱状图（全量 7 天，不受分页影响） -->
        <div class="usage-right">
          <div class="chart">
            <div v-for="d in usage" :key="d.date" class="bar-col">
              <div class="bar-wrap">
                <div
                  class="bar"
                  :class="{ zero: !d.tokens }"
                  :style="{ height: barHeight(d.tokens) + '%' }"
                  :title="`${d.date}：${fmtNum(d.tokens)} tokens`"
                ></div>
              </div>
              <div class="bar-label">{{ shortDate(d.date) }}</div>
            </div>
          </div>
          <div class="chart-desc">Token 消耗趋势（近 7 日，全量显示）</div>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.dashboard {
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

/* —— 五统计卡片 —— */
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
  padding: 14px 14px 12px;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.04);
  transition: transform 0.2s ease, box-shadow 0.2s ease, border-color 0.2s ease;
  cursor: default;
}
.stat-card:hover {
  transform: translateY(-4px);
  box-shadow: 0 10px 28px rgba(0, 0, 0, 0.12);
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
  flex-shrink: 0;
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
  width: 30px;
  height: 30px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 15px;
  background: color-mix(in srgb, var(--card-color) 12%, transparent);
  flex-shrink: 0;
}
.stat-num {
  font-size: 26px;
  font-weight: 700;
  color: var(--card-color);
  margin-top: 8px;
  line-height: 1.2;
}

/* —— 内容卡片 —— */
.panel-card {
  flex: 1;
  min-height: 270px;
  background: #fff;
  border-radius: 18px;
  box-shadow: 0 2px 14px rgba(0, 0, 0, 0.06);
  padding: 16px 20px;
  display: flex;
  flex-direction: column;
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

/* —— 表格 —— */
.table-scroll {
  flex: 1;
  overflow-y: auto;
  border: 1px solid #f3f4f6;
  border-radius: 10px;
  min-height: 0;
}
.tbl {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.tbl thead th {
  position: sticky;
  top: 0;
  background: #fff;
  z-index: 1;
  text-align: left;
  padding: 9px 12px;
  font-size: 12px;
  font-weight: 600;
  color: #9ca3af;
  border-bottom: 2px solid #f3f4f6;
  white-space: nowrap;
}
.tbl tbody td {
  padding: 9px 12px;
  border-bottom: 1px solid #f3f4f6;
  white-space: nowrap;
}
.tbl tbody tr:last-child td {
  border-bottom: 0;
}
.tbl .email {
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
}
.tbl .muted {
  color: #9ca3af;
  font-size: 12px;
}
.role-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 6px;
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

/* 当日数据行高亮 */
.tbl tbody tr.today {
  background: #ecfdf5;
}
.tbl tbody tr.today td {
  color: #059669;
  font-weight: 600;
}
.token-cell {
  color: #059669;
  font-weight: 600;
}

/* —— 分页 —— */
.pagination {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 10px;
  flex-shrink: 0;
}
.page-info {
  font-size: 12px;
  color: #9ca3af;
}
.page-btns {
  display: flex;
  align-items: center;
  gap: 4px;
}
.page-btns button {
  min-width: 28px;
  padding: 4px 8px;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  background: #fff;
  color: #6b7280;
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
  transition: background 0.12s ease, color 0.12s ease, border-color 0.12s ease;
}
.page-btns button:hover:not(:disabled):not(.active) {
  background: #f9fafb;
  border-color: #d1d5db;
}
.page-btns button.active {
  background: #10b981;
  border-color: #10b981;
  color: #fff;
  font-weight: 700;
}
.page-btns button:disabled {
  background: #f3f4f6;
  color: #d1d5db;
  cursor: not-allowed;
}
.page-ellipsis {
  padding: 4px 2px;
  font-size: 12px;
  color: #d1d5db;
}

/* —— 用量双栏 —— */
.usage-body {
  flex: 1;
  display: flex;
  gap: 16px;
  min-height: 0;
}
.usage-left {
  flex: 0 0 42%;
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.usage-right {
  flex: 1;
  border-left: 1px dashed #e5e7eb;
  padding-left: 16px;
  display: flex;
  flex-direction: column;
  min-width: 0;
}

/* —— 柱状图 —— */
.chart {
  flex: 1;
  display: flex;
  align-items: flex-end;
  gap: 8px;
  min-height: 150px;
  padding: 8px 4px 0;
}
.bar-col {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  height: 100%;
  min-width: 0;
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
  max-width: 30px;
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
  white-space: nowrap;
}
.chart-desc {
  font-size: 11px;
  color: #9ca3af;
  text-align: center;
  margin-top: 8px;
}

/* —— 窄屏 —— */
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
  .usage-left {
    flex: none;
  }
  .usage-right {
    border-left: 0;
    border-top: 1px dashed #e5e7eb;
    padding-left: 0;
    padding-top: 12px;
  }
}
</style>
