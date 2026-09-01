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
  } catch (e) { error.value = e.message || '加载失败' }
}

onMounted(load)
</script>

<template>
  <section class="card">
    <h2>我的历史记录</h2>
    <p v-if="error" class="error">{{ error }}</p>
    <template v-else-if="history">
      <h3>简历（{{ history.resumes.length }}）</h3>
      <ul><li v-for="item in history.resumes" :key="item.id">{{ item.filename }} <small>{{ item.parse_status }}</small></li></ul>
      <h3>分析（{{ history.analyses.length }}）</h3>
      <ul><li v-for="item in history.analyses" :key="item.id">简历 #{{ item.resume_id }} · {{ item.model_name }}</li></ul>
      <h3>面试（{{ history.interviews.length }}）</h3>
      <ul><li v-for="item in history.interviews" :key="item.id">简历 #{{ item.resume_id }} · {{ item.status }} · {{ item.turn_count }} 轮</li></ul>
    </template>
  </section>
</template>

<style scoped>
.card{background:#fff;border-radius:12px;box-shadow:0 1px 4px rgb(0 0 0 / 8%);padding:20px 24px}h2{margin:0 0 12px;font-size:18px}h3{margin:14px 0 5px;font-size:14px}ul{margin:0;padding-left:20px;color:#4b5563;font-size:13px;line-height:1.8}small{color:#9ca3af}.error{color:#b91c1c;font-size:13px}
</style>
