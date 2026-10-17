<script setup>
import Icon from './Icon.vue'
import { computed, onMounted, ref } from 'vue'
import { get } from '../api.js'
import { fmtDateTime, pageNumbers as pageNumbersUtil } from '../utils.js'
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

// 动作类型中文映射 + 配色（纯展示配置，一律走设计令牌；原紫色 chat_create 按规范中性化）
const actionMap = {
  parse: { label: '简历解析', color: 'var(--c-muted)', bg: 'var(--c-bg-soft)' },
  analysis: { label: 'AI分析', color: 'var(--c-info)', bg: 'var(--c-info-bg)' },
  interview_message: { label: '模拟面试', color: 'var(--c-warn)', bg: 'var(--c-warn-bg)' },
  kb_upload: { label: '知识库上传', color: 'var(--c-info)', bg: 'var(--c-info-bg)' },
  playground: { label: '知识库问答', color: 'var(--c-ok)', bg: 'var(--c-ok-bg)' },
  chat_create: { label: '新建对话', color: 'var(--c-text-2)', bg: 'var(--c-bg-soft)' },
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
    // 失败只提示、保留旧数据：一次网络抖动不应把整张表换成错误占位
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

// 页码折叠算法收敛到 utils.pageNumbers（与 AdminPanel 同一份实现）
const pageNumbers = computed(() => pageNumbersUtil(page.value, totalPages.value))

onMounted(loadLogs)
</script>

<template>
  <section class="usage-log-page">
    <!-- —— 筛选条件卡片 —— -->
    <div class="filter-card">
      <div class="page-head filter-header">
        <div>
          <h3>
            筛选条件
            <span v-if="activeFilters" class="filter-badge">{{ activeFilters }}</span>
          </h3>
        </div>
        <div class="ph-actions">
          <button class="btn btn-ghost" @click="reset"><Icon name="refresh" :size="14" />重置</button>
          <button class="btn btn-primary" @click="search"><Icon name="search" :size="14" />搜索</button>
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
        <select v-model="actionType" class="input filter-select">
          <option value="">全部动作</option>
          <option v-for="(v, k) in actionMap" :key="k" :value="k">{{ v.label }}</option>
        </select>
        <input v-model="modelName" class="input filter-input" placeholder="模型，如 qwen-plus" />
        <input v-model="ipAddress" class="input filter-input" placeholder="IP，如 127.0.0.1" />
      </div>
    </div>

    <!-- —— 使用日志卡片 —— -->
    <div class="log-card">
      <h3>使用日志</h3>
      <!-- 错误横幅不顶掉表格：失败时保留旧数据，重试后恢复 -->
      <p v-if="error" class="msg error">{{ error }}</p>
      <div v-if="loading && !logs.length" class="loading-row"><span class="spinner"></span>加载中…</div>

      <template v-else-if="!loading || logs.length">
        <!-- 空态 -->
        <div v-if="logs.length === 0" class="empty-state">
          <div class="es-icon"><Icon name="inbox" :size="24" /></div>
          <p class="es-text">暂无数据 · 未找到使用记录</p>
        </div>

        <template v-else>
          <div class="tbl-wrap">
            <table class="tbl log-tbl">
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
                  <td class="time">{{ fmtDateTime(log.created_at) }}</td>
                  <td>
                    <span
                      class="badge"
                      :style="{
                        background: actionMap[log.action_type]?.bg || 'var(--c-bg-soft)',
                        color: actionMap[log.action_type]?.color || 'var(--c-muted)',
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
              <select v-model="pageSize" class="input page-size-select" @change="onPageSizeChange">
                <option :value="10">10</option>
                <option :value="20">20</option>
                <option :value="50">50</option>
              </select>
              条 · 共 {{ total }} 条
            </div>
            <div class="pager">
              <button :disabled="page <= 1" @click="goPage(page - 1)">上一页</button>
              <template v-for="(p, i) in pageNumbers" :key="i">
                <span v-if="p === '…'" class="page-ellipsis">…</span>
                <button v-else :class="{ on: p === page }" @click="goPage(p)">{{ p }}</button>
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
  gap: var(--sp-3);
}

/* —— 卡片通用 —— */
.filter-card,
.log-card {
  background: #fff;
  border: 1px solid var(--c-border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow);
  padding: var(--sp-4) var(--sp-5);
}
/* 布局骨架复用全局 .page-head，这里只留卡片内的下间距 */
.filter-header {
  margin-bottom: var(--sp-3);
}
.filter-header h3 {
  font-size: var(--fs-lg);
  font-weight: 600;
  margin: 0;
  display: flex;
  align-items: center;
  gap: var(--sp-2);
}
.filter-badge {
  background: var(--c-primary);
  color: #fff;
  font-size: var(--fs-xs);
  font-weight: 600;
  border-radius: var(--radius-full);
  padding: 2px var(--sp-2);
}

/* —— 筛选行 —— */
.filter-row {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
  margin-bottom: var(--sp-3);
}
.filter-row:last-child {
  margin-bottom: 0;
}
.row-label {
  font-size: var(--fs-xs);
  color: var(--c-muted);
  white-space: nowrap;
}
/* 边框/聚焦态走全局 .input，这里只放开行内布局宽度 */
.filter-select,
.filter-input {
  width: auto;
}
.filter-input {
  min-width: 160px;
}

.quick-btns {
  display: flex;
  border: 1px solid var(--c-border);
  border-radius: var(--radius-sm);
  overflow: hidden;
}
.quick-btns button {
  padding: 6px var(--sp-3);
  border: 0;
  border-left: 1px solid var(--c-border);
  background: #fff;
  color: var(--c-muted);
  font-size: var(--fs-sm);
  font-family: inherit;
  cursor: pointer;
  transition: background var(--dur-fast) var(--ease), color var(--dur-fast) var(--ease);
}
.quick-btns button:first-child {
  border-left: 0;
}
.quick-btns button:hover {
  background: var(--c-hover);
}
.quick-btns button.active {
  background: var(--c-primary);
  color: #fff;
  font-weight: 600;
}

/* —— 日志卡片 —— */
.log-card h3 {
  font-size: var(--fs-lg);
  font-weight: 600;
  margin: 0 0 var(--sp-3);
}
/* 表格基础走全局 .tbl/.tbl-wrap，这里只留列级补充 */
.log-tbl td {
  white-space: nowrap;
}
.log-tbl .right {
  text-align: right;
}
.log-tbl .time {
  color: var(--c-muted);
  font-size: var(--fs-xs);
}
.log-tbl .model {
  color: var(--c-text-2);
}
.log-tbl .token {
  color: var(--c-primary-dark);
  font-weight: 600;
}
.log-tbl .ip {
  color: var(--c-muted);
  font-size: var(--fs-xs);
}

/* —— 分页（按钮走全局 .pager，这里只留布局与每页条数选择器） —— */
.pagination {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: var(--sp-3);
  flex-wrap: wrap;
  gap: var(--sp-2);
}
.page-size {
  font-size: var(--fs-xs);
  color: var(--c-muted);
  display: flex;
  align-items: center;
  gap: var(--sp-1);
}
.page-size select {
  width: auto;
  padding: var(--sp-1) var(--sp-2);
  font-size: var(--fs-xs);
}
.page-ellipsis {
  padding: var(--sp-1) 2px;
  font-size: var(--fs-xs);
  color: var(--c-faint);
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
