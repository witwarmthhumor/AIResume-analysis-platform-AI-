<script setup>
import { computed, onMounted, ref } from 'vue'
import { get } from '../api.js'
import DateRangePicker from './DateRangePicker.vue'

// —— 数据 ——
const logs = ref([])
const total = ref(0)
const loading = ref(false)
const error = ref('')

// —— 分页（后端分页） ——
const page = ref(1)
const pageSize = ref(10)
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)))

// —— 筛选条件 ——
const dateRange = ref({ start: '', end: '', startTime: '00:00', endTime: '23:59' })
const actionType = ref('')
const modelName = ref('')
const ipAddress = ref('')
const activeQuick = ref('') // 当前激活的快捷按钮：1/7/30/''

const activeFilters = computed(() => {
  let n = 0
  if (dateRange.value.start) n++
  if (actionType.value) n++
  if (modelName.value.trim()) n++
  if (ipAddress.value.trim()) n++
  return n
})

// 动作类型中文映射 + 配色
const actionMap = {
  parse: { label: '简历解析', color: '#6B7280', bg: '#F3F4F6' },
  analysis: { label: 'AI分析', color: '#1D4ED8', bg: '#DBEAFE' },
  interview_message: { label: '模拟面试', color: '#92400E', bg: '#FEF3C7' },
  kb_upload: { label: '知识库上传', color: '#0E7490', bg: '#CFFAFE' },
  playground: { label: '知识库问答', color: '#065F46', bg: '#D1FAE5' },
  chat_create: { label: '新建对话', color: '#7C3AED', bg: '#EDE9FE' },
}

async function loadLogs() {
  loading.value = true
  error.value = ''
  try {
    const params = new URLSearchParams()
    params.set('page', page.value)
    params.set('page_size', pageSize.value)
    if (actionType.value) params.set('action_type', actionType.value)
    if (modelName.value.trim()) params.set('model_name', modelName.value.trim())
    if (ipAddress.value.trim()) params.set('ip_address', ipAddress.value.trim())
    if (dateRange.value.start) {
      params.set('start_date', dateRange.value.start)
      params.set('start_time', dateRange.value.startTime || '00:00')
    }
    if (dateRange.value.end) {
      params.set('end_date', dateRange.value.end)
      params.set('end_time', dateRange.value.endTime || '23:59')
    }
    const res = await get('/api/usage/logs?' + params.toString())
    logs.value = res.items
    total.value = res.total
  } catch (e) {
    error.value = e.message || '加载失败'
  } finally {
    loading.value = false
  }
}

function search() {
  activeQuick.value = ''
  page.value = 1
  loadLogs()
}

function reset() {
  dateRange.value = { start: '', end: '', startTime: '00:00', endTime: '23:59' }
  actionType.value = ''
  modelName.value = ''
  ipAddress.value = ''
  activeQuick.value = ''
  page.value = 1
  loadLogs()
}

function pad(n) {
  return String(n).padStart(2, '0')
}
function fmtDate(d) {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

// 快捷日期：1=今日，7=近7天，30=近30天
function setQuickRange(days) {
  const end = new Date()
  const start = new Date()
  if (days > 1) start.setDate(start.getDate() - (days - 1))
  dateRange.value = {
    start: fmtDate(start),
    end: fmtDate(end),
    startTime: '00:00',
    endTime: '23:59',
  }
  activeQuick.value = days
  page.value = 1
  loadLogs()
}

function goPage(p) {
  if (p < 1 || p > totalPages.value) return
  page.value = p
  loadLogs()
}

function onPageSizeChange() {
  page.value = 1
  loadLogs()
}

// 页码（超过 7 页用省略号折叠）
const pageNumbers = computed(() => {
  const tp = totalPages.value
  const cur = page.value
  if (tp <= 7) return Array.from({ length: tp }, (_, i) => i + 1)
  const arr = [1]
  if (cur > 3) arr.push('…')
  for (let i = Math.max(2, cur - 1); i <= Math.min(tp - 1, cur + 1); i++) arr.push(i)
  if (cur < tp - 2) arr.push('…')
  arr.push(tp)
  return arr
})

function formatTime(iso) {
  if (!iso) return '-'
  const d = new Date(iso)
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

onMounted(loadLogs)
</script>

<template>
  <section class="usage-log-page">
    <!-- —— 筛选条件卡片 —— -->
    <div class="filter-card">
      <div class="filter-header">
        <h3>
          筛选条件
          <span v-if="activeFilters" class="filter-badge">{{ activeFilters }}</span>
        </h3>
        <div class="filter-actions">
          <button class="btn-ghost" @click="reset">↩ 重置</button>
          <button class="btn-primary" @click="search">🔍 搜索</button>
        </div>
      </div>

      <div class="filter-row">
        <label class="row-label">日期范围</label>
        <DateRangePicker v-model="dateRange" />
        <div class="quick-btns">
          <button :class="{ active: activeQuick === 1 }" @click="setQuickRange(1)">今日</button>
          <button :class="{ active: activeQuick === 7 }" @click="setQuickRange(7)">7天</button>
          <button :class="{ active: activeQuick === 30 }" @click="setQuickRange(30)">30天</button>
        </div>
      </div>

      <div class="filter-row">
        <select v-model="actionType" class="filter-select">
          <option value="">全部动作</option>
          <option v-for="(v, k) in actionMap" :key="k" :value="k">{{ v.label }}</option>
        </select>
        <input v-model="modelName" class="filter-input" placeholder="模型，如 qwen-plus" />
        <input v-model="ipAddress" class="filter-input" placeholder="IP，如 127.0.0.1" />
      </div>
    </div>

    <!-- —— 使用日志卡片 —— -->
    <div class="log-card">
      <h3>📋 使用日志</h3>
      <p v-if="error" class="msg error">{{ error }}</p>
      <div v-else-if="loading" class="state-loading">加载中…</div>

      <template v-else>
        <!-- 空态 -->
        <div v-if="logs.length === 0" class="empty-state">
          <div class="empty-icon">📭</div>
          <div class="empty-title">暂无数据</div>
          <div class="empty-desc">未找到使用记录</div>
        </div>

        <template v-else>
          <div class="table-wrap">
            <table class="log-tbl">
              <thead>
                <tr>
                  <th>时间</th>
                  <th>动作</th>
                  <th>模型</th>
                  <th class="right">Token</th>
                  <th>IP</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="log in logs" :key="log.id">
                  <td class="time">{{ formatTime(log.created_at) }}</td>
                  <td>
                    <span
                      class="action-badge"
                      :style="{
                        background: actionMap[log.action_type]?.bg || '#F3F4F6',
                        color: actionMap[log.action_type]?.color || '#6B7280',
                      }"
                    >{{ actionMap[log.action_type]?.label || log.action_type }}</span>
                  </td>
                  <td class="model">{{ log.model_name || '-' }}</td>
                  <td class="right token">{{ Number(log.tokens_total || 0).toLocaleString() }}</td>
                  <td class="ip">{{ log.ip_address || '-' }}</td>
                </tr>
              </tbody>
            </table>
          </div>

          <!-- 分页栏 -->
          <div class="pagination">
            <div class="page-size">
              每页
              <select v-model="pageSize" @change="onPageSizeChange">
                <option :value="10">10</option>
                <option :value="20">20</option>
                <option :value="50">50</option>
              </select>
              条 · 共 {{ total }} 条
            </div>
            <div class="page-btns">
              <button :disabled="page <= 1" @click="goPage(page - 1)">上一页</button>
              <template v-for="(p, i) in pageNumbers" :key="i">
                <span v-if="p === '…'" class="page-ellipsis">…</span>
                <button v-else :class="{ active: p === page }" @click="goPage(p)">{{ p }}</button>
              </template>
              <button :disabled="page >= totalPages" @click="goPage(page + 1)">下一页</button>
            </div>
          </div>
        </template>
      </template>
    </div>
  </section>
</template>

<style scoped>
.usage-log-page {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

/* —— 卡片通用 —— */
.filter-card,
.log-card {
  background: #fff;
  border-radius: 18px;
  box-shadow: 0 2px 14px rgba(0, 0, 0, 0.06);
  padding: 16px 20px;
}
.filter-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
}
.filter-header h3 {
  font-size: 15px;
  font-weight: 700;
  margin: 0;
  display: flex;
  align-items: center;
  gap: 8px;
}
.filter-badge {
  background: #10b981;
  color: #fff;
  font-size: 11px;
  font-weight: 700;
  border-radius: 999px;
  padding: 1px 7px;
}
.filter-actions {
  display: flex;
  gap: 8px;
}
.btn-ghost,
.btn-primary {
  padding: 6px 14px;
  border-radius: 8px;
  font-size: 12.5px;
  font-family: inherit;
  cursor: pointer;
  transition: background 0.12s ease;
}
.btn-ghost {
  border: 1px solid #e5e7eb;
  background: #fff;
  color: #6b7280;
}
.btn-ghost:hover {
  background: #f9fafb;
}
.btn-primary {
  border: 1px solid #10b981;
  background: #10b981;
  color: #fff;
  font-weight: 600;
}
.btn-primary:hover {
  background: #059669;
}

/* —— 筛选行 —— */
.filter-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}
.filter-row:last-child {
  margin-bottom: 0;
}
.row-label {
  font-size: 12.5px;
  color: #6b7280;
  white-space: nowrap;
}
.filter-select,
.filter-input {
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 6px 10px;
  font-size: 12.5px;
  font-family: inherit;
  outline: none;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.filter-select:focus,
.filter-input:focus {
  border-color: #10b981;
  box-shadow: 0 0 0 3px rgb(16 185 129 / 10%);
}
.filter-input {
  min-width: 160px;
}

.quick-btns {
  display: flex;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  overflow: hidden;
}
.quick-btns button {
  padding: 6px 14px;
  border: 0;
  border-left: 1px solid #e5e7eb;
  background: #fff;
  color: #6b7280;
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
  transition: background 0.12s ease, color 0.12s ease;
}
.quick-btns button:first-child {
  border-left: 0;
}
.quick-btns button:hover {
  background: #f9fafb;
}
.quick-btns button.active {
  background: #10b981;
  color: #fff;
  font-weight: 600;
}

/* —— 日志卡片 —— */
.log-card h3 {
  font-size: 15px;
  font-weight: 700;
  margin: 0 0 12px;
}
.state-loading {
  color: #9ca3af;
  font-size: 13px;
  padding: 32px 0;
  text-align: center;
}
.empty-state {
  border: 2px dashed #e5e7eb;
  border-radius: 14px;
  padding: 40px 20px;
  text-align: center;
}
.empty-icon {
  font-size: 32px;
  margin-bottom: 8px;
}
.empty-title {
  font-size: 15px;
  font-weight: 700;
  color: #374151;
}
.empty-desc {
  font-size: 12px;
  color: #9ca3af;
  margin-top: 4px;
}

.table-wrap {
  border: 1px solid #f3f4f6;
  border-radius: 10px;
  overflow-x: auto;
}
.log-tbl {
  width: 100%;
  border-collapse: collapse;
  font-size: 12.5px;
}
.log-tbl thead th {
  background: #f9fafb;
  text-align: left;
  padding: 9px 12px;
  font-size: 12px;
  font-weight: 600;
  color: #9ca3af;
  border-bottom: 2px solid #e5e7eb;
  white-space: nowrap;
}
.log-tbl tbody td {
  padding: 9px 12px;
  border-bottom: 1px solid #f3f4f6;
  white-space: nowrap;
}
.log-tbl tbody tr:last-child td {
  border-bottom: 0;
}
.log-tbl .right {
  text-align: right;
}
.log-tbl .time {
  color: #6b7280;
  font-size: 12px;
}
.log-tbl .model {
  color: #374151;
}
.log-tbl .token {
  color: #059669;
  font-weight: 600;
}
.log-tbl .ip {
  color: #6b7280;
  font-size: 12px;
}
.action-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 11px;
  font-weight: 600;
}

/* —— 分页 —— */
.pagination {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 12px;
  flex-wrap: wrap;
  gap: 8px;
}
.page-size {
  font-size: 12px;
  color: #6b7280;
  display: flex;
  align-items: center;
  gap: 4px;
}
.page-size select {
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  padding: 3px 6px;
  font-size: 12px;
  font-family: inherit;
  outline: none;
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

@media (max-width: 900px) {
  .filter-row {
    flex-direction: column;
    align-items: stretch;
  }
  .quick-btns {
    align-self: flex-start;
  }
}
</style>
