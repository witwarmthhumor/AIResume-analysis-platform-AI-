<script setup>
import { onMounted, ref } from 'vue'
import { get } from '../api.js'

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

onMounted(load)
</script>

<template>
  <section class="card">
    <h2>📊 管理面板</h2>
    <p v-if="error" class="msg error">{{ error }}</p>

    <template v-if="stats">
      <div class="stat-grid">
        <div class="stat-card"><strong>{{ stats.users }}</strong><span>用户</span></div>
        <div class="stat-card"><strong>{{ stats.resumes }}</strong><span>简历</span></div>
        <div class="stat-card"><strong>{{ stats.analyses }}</strong><span>分析</span></div>
        <div class="stat-card"><strong>{{ stats.interviews }}</strong><span>面试</span></div>
        <div class="stat-card full"><strong>{{ stats.tokens_today }}</strong><span>今日 Token 消耗</span></div>
      </div>
    </template>

    <h3 class="sec-title">👥 用户列表</h3>
    <table v-if="users.length" class="tbl">
      <thead><tr><th>ID</th><th>邮箱</th><th>角色</th><th>简历数</th><th>注册时间</th></tr></thead>
      <tbody>
        <tr v-for="u in users" :key="u.id">
          <td>{{ u.id }}</td><td>{{ u.email }}</td>
          <td><span class="badge" :class="u.role === 'admin' ? 'ok' : 'gray'">{{ u.role }}</span></td>
          <td>{{ u.resume_count }}</td><td>{{ new Date(u.created_at).toLocaleString() }}</td>
        </tr>
      </tbody>
    </table>

    <h3 class="sec-title">📈 近 7 日用量</h3>
    <table v-if="usage.length" class="tbl">
      <thead><tr><th>日期</th><th>调用次数</th><th>Token 消耗</th></tr></thead>
      <tbody>
        <tr v-for="d in usage" :key="d.date">
          <td>{{ d.date }}</td><td>{{ d.calls }}</td><td>{{ d.tokens }}</td>
        </tr>
      </tbody>
    </table>
  </section>
</template>

<style scoped>
.stat-grid {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 8px;
  margin: 12px 0;
}
.stat-card {
  background: #f0fdf4;
  border-radius: 10px;
  padding: 14px 8px;
  text-align: center;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.stat-card strong {
  font-size: 24px;
  color: #059669;
}
.stat-card span {
  font-size: 12px;
  color: var(--c-muted);
}
.stat-card.full {
  grid-column: 1 / -1;
  flex-direction: row;
  justify-content: center;
  gap: 12px;
}
.tbl {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
  margin: 8px 0 16px;
}
.tbl th, .tbl td {
  text-align: left;
  padding: 8px 6px;
  border-bottom: 1px solid var(--c-border);
}
.tbl th {
  color: var(--c-muted);
  font-weight: 600;
  font-size: 12px;
}
@media (max-width: 520px) {
  .stat-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}
</style>