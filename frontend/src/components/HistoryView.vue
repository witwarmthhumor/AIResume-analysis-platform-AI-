<script setup>
import { onMounted, ref } from 'vue'

const history = ref(null)
const error = ref('')

async function load() {
  try {
    const res = await fetch('/api/history', { credentials: 'include' })
    const body = await res.json().catch(() => null)
    if (!res.ok) throw new Error(body?.detail || '请先登录')
    history.value = body
  } catch (e) {
    error.value = e.message || '加载失败'
  }
}

onMounted(load)
</script>

<template>
  <section class="card">
    <h2>我的历史记录</h2>
    <p v-if="error" class="msg error">{{ error }}</p>
    <template v-else-if="history">
      <h3 class="sec">📄 简历 <span class="count">{{ history.resumes.length }}</span></h3>
      <ul class="hlist">
        <li v-for="item in history.resumes" :key="item.id">
          <span class="name">{{ item.filename }}</span>
          <span class="badge" :class="item.parse_status === 'success' ? 'ok' : item.parse_status === 'unsupported' ? 'warn' : 'bad'">
            {{ item.parse_status === 'success' ? '成功' : item.parse_status === 'unsupported' ? '扫描件' : '失败' }}
          </span>
        </li>
      </ul>
      <h3 class="sec">✨ 分析 <span class="count">{{ history.analyses.length }}</span></h3>
      <ul class="hlist">
        <li v-for="item in history.analyses" :key="item.id">
          <span class="name">简历 #{{ item.resume_id }}</span>
          <span class="pill">{{ item.model_name }}</span>
        </li>
      </ul>
      <h3 class="sec">🎙️ 面试 <span class="count">{{ history.interviews.length }}</span></h3>
      <ul class="hlist">
        <li v-for="item in history.interviews" :key="item.id">
          <span class="name">简历 #{{ item.resume_id }}</span>
          <span class="pill">{{ item.status }} · {{ item.turn_count }} 轮</span>
        </li>
      </ul>
    </template>
  </section>
</template>

<style scoped>
.sec {
  font-size: 13.5px;
  margin: 16px 0 8px;
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--c-text);
}
.sec:first-of-type {
  margin-top: 4px;
}
.count {
  background: var(--c-primary-light);
  color: var(--c-primary-dark);
  border-radius: 999px;
  padding: 1px 8px;
  font-size: 11px;
}
.hlist {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.hlist li {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-radius: var(--radius-sm);
  border: 1px solid transparent;
  transition: background 0.15s ease;
}
.hlist li:hover {
  background: #f6fdf9;
  border-color: #d1fae5;
}
.hlist .name {
  flex: 1;
  font-size: 13px;
  color: var(--c-text-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
