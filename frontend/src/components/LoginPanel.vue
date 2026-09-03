<script setup>
import { ref } from 'vue'
import { post } from '../api.js'

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
    const body = await post(`/api/auth/${mode.value}`, {
      email: email.value, password: password.value,
    })
    emit('logged-in', body.user)
  } catch (e) { error.value = e.message || '网络异常' } finally { loading.value = false }
}
</script>

<template>
  <section class="card" style="max-width:420px">
    <h2 style="margin-bottom:8px">{{ mode === 'login' ? '登录' : '注册账号' }}</h2>
    <label class="label" for="login-email">邮箱</label>
    <input id="login-email" v-model="email" type="email" autocomplete="email" placeholder="your@email.com" />
    <label class="label" for="login-pass">密码（至少 8 位）</label>
    <input id="login-pass" v-model="password" type="password" autocomplete="current-password" />
    <button class="btn btn-primary" :disabled="loading || !email || password.length < 8" @click="submit" style="width:100%;margin-top:10px;padding:11px">
      {{ loading ? '处理中…' : mode === 'login' ? '登 录' : '注册并登录' }}
    </button>
    <p v-if="error" class="msg error" style="margin:0">{{ error }}</p>
    <p class="switch">
      <span v-if="mode === 'login'">还没有账号？<b class="link" @click="mode='register'; error=''">注册</b></span>
      <span v-else>已有账号？<b class="link" @click="mode='login'; error=''">登录</b></span>
    </p>
  </section>
</template>

<style scoped>
.label {
  display: block;
  font-size: 12.5px;
  color: var(--c-muted);
  margin: 12px 0 5px;
}
input {
  width: 100%;
  box-sizing: border-box;
  border: 1.5px solid var(--c-border);
  border-radius: 10px;
  padding: 10px 12px;
  font-size: 13.5px;
  font-family: inherit;
  outline: none;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
input:focus {
  border-color: #6ee7b7;
  box-shadow: 0 0 0 3px rgb(16 185 129 / 12%);
}
.switch {
  text-align: center;
  font-size: 12px;
  color: var(--c-faint);
  margin: 10px 0 0;
}
.link {
  color: var(--c-primary-dark);
  cursor: pointer;
}
.link:hover {
  text-decoration: underline;
}
</style>