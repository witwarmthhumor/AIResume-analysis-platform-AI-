<script setup>
// 阶段0 首页：展示后端健康检查结果（服务状态 / 数据库 / 版本）
import { onMounted, ref } from 'vue'

const health = ref(null)
const loading = ref(true)

const label = {
  ok: '运行中',
  degraded: '异常',
  unreachable: '无法连接',
  connected: '已连接',
  disconnected: '未连接',
}

onMounted(async () => {
  try {
    const res = await fetch('/api/health') // 走 Vite 代理到后端，同源无跨域
    health.value = await res.json()
  } catch {
    health.value = { status: 'unreachable', database: 'disconnected', version: '-' }
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <main class="page">
    <h1>AI 简历分析 <span class="plus">+</span> 模拟面试</h1>
    <p class="subtitle">阶段 0 · 项目骨架 · 前后端已联通</p>

    <section class="card">
      <div class="row">
        <span class="name">后端服务</span>
        <span
          v-if="!loading"
          class="badge"
          :class="health.status === 'ok' ? 'ok' : 'bad'"
        >
          {{ label[health.status] }}
        </span>
        <span v-else class="badge">检测中…</span>
      </div>
      <div class="row">
        <span class="name">数据库</span>
        <span
          v-if="!loading"
          class="badge"
          :class="health.database === 'connected' ? 'ok' : 'bad'"
        >
          {{ label[health.database] }}
        </span>
        <span v-else class="badge">检测中…</span>
      </div>
      <div class="row">
        <span class="name">版本</span>
        <span class="value">{{ loading ? '…' : health.version }}</span>
      </div>
    </section>

    <p class="hint">本页通过 Vite 代理请求后端 <code>/api/health</code></p>
  </main>
</template>

<style scoped>
.page {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  font-family: system-ui, 'Microsoft YaHei', sans-serif;
  background: #f5f7fa;
}

h1 {
  font-size: 28px;
  color: #1f2937;
  margin: 0;
}

.plus {
  color: #10b981;
}

.subtitle {
  color: #6b7280;
  margin: 0 0 24px;
}

.card {
  background: #fff;
  border-radius: 12px;
  box-shadow: 0 1px 4px rgb(0 0 0 / 8%);
  padding: 24px 32px;
  width: 320px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 15px;
}

.name {
  color: #374151;
}

.value {
  color: #6b7280;
}

.badge {
  padding: 2px 12px;
  border-radius: 999px;
  font-size: 13px;
  background: #e5e7eb;
  color: #4b5563;
}

.badge.ok {
  background: #d1fae5;
  color: #047857;
}

.badge.bad {
  background: #fee2e2;
  color: #b91c1c;
}

.hint {
  margin-top: 24px;
  color: #9ca3af;
  font-size: 13px;
}

code {
  background: #e5e7eb;
  border-radius: 4px;
  padding: 1px 6px;
}
</style>
