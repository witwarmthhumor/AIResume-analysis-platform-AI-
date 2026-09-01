<script setup>
import { ref } from 'vue'

const emit = defineEmits(['logged-in', 'logged-out'])
const mode = ref('login')
const email = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)

async function submit() {
  error.value = ''
  loading.value = true
  try {
    const res = await fetch(`/api/auth/${mode.value}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      credentials: 'include', body: JSON.stringify({ email: email.value, password: password.value }),
    })
    const body = await res.json().catch(() => null)
    if (!res.ok) throw new Error(body?.detail || '操作失败')
    emit('logged-in', body.user)
  } catch (e) { error.value = e.message || '网络异常' } finally { loading.value = false }
}
</script>

<template>
  <section class="auth-card">
    <h2>{{ mode === 'login' ? '登录' : '注册账号' }}</h2>
    <input v-model="email" type="email" placeholder="邮箱" autocomplete="email" />
    <input v-model="password" type="password" placeholder="密码（至少 8 位）" autocomplete="current-password" />
    <button :disabled="loading || !email || password.length < 8" @click="submit">{{ loading ? '处理中…' : mode === 'login' ? '登录' : '注册并登录' }}</button>
    <p v-if="error" class="error">{{ error }}</p>
    <button class="link" @click="mode = mode === 'login' ? 'register' : 'login'; error = ''">{{ mode === 'login' ? '还没有账号？注册' : '已有账号？登录' }}</button>
  </section>
</template>

<style scoped>
.auth-card { display:flex; flex-direction:column; gap:10px; background:#fff; border-radius:12px; padding:20px 24px; box-shadow:0 1px 4px rgb(0 0 0 / 8%); }
h2 { margin:0 0 4px; font-size:18px; }
input { border:1px solid #d1d5db; border-radius:8px; padding:9px 10px; font:inherit; }
button { border:0; border-radius:8px; padding:9px 14px; background:#10b981; color:#fff; cursor:pointer; } button:disabled{opacity:.5;cursor:wait}.link{background:none;color:#047857;font-size:12px}.error{margin:0;color:#b91c1c;font-size:13px}
</style>
