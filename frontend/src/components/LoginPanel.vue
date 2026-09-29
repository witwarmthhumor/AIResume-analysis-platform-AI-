<script setup>
/* LoginPanel —— 登录/注册双模式表单（模态内使用）。
   v4.1 A4 认证改造：登录用「用户名或邮箱」标识（发送 username 字段，后端含 @ 自动按邮箱查）；
   注册必填用户名（3~64 位小写字母/数字/下划线）。成功后 emit logged-in 交由 App 写入全局用户态。 */
import { ref } from 'vue'
import { post } from '../api.js'

const emit = defineEmits(['logged-in'])
const mode = ref('login')
const identifier = ref('') // 登录标识：用户名 / 邮箱 / 手机号
const regUsername = ref('') // 注册用户名
const regPhone = ref('') // 注册手机号（v4.2）
const regConfirm = ref('') // 确认密码（v4.2.1，对齐参考注册页）
const password = ref('')
const error = ref('')
const loading = ref(false)

const USERNAME_RE = /^[a-z0-9_]{3,64}$/
const PHONE_RE = /^1[3-9]\d{9}$/

async function submit() {
  error.value = ''
  loading.value = true
  try {
    if (mode.value === 'login') {
      const body = await post('/api/auth/login', {
        username: identifier.value.trim(),
        password: password.value,
      })
      emit('logged-in', body.user)
    } else {
      const body = await post('/api/auth/register', {
        username: regUsername.value.trim().toLowerCase(),
        phone: regPhone.value.trim(),
        password: password.value,
      })
      emit('logged-in', body.user)
    }
  } catch (e) { error.value = e.message || '网络异常' } finally { loading.value = false }
}

// 登录：标识非空 + 密码非空即可提交（内置 admin 的 6 位口令也要能登录）
const loginReady = () => identifier.value.trim() && password.value
// 注册：用户名过前端正则 + 邮箱含 @ + 手机号合法 + 密码 ≥8 且两次一致（与后端校验同口径）
const registerReady = () =>
  USERNAME_RE.test(regUsername.value.trim().toLowerCase()) &&
  PHONE_RE.test(regPhone.value.trim()) &&
  password.value.length >= 8 &&
  password.value === regConfirm.value
</script>

<template>
  <section class="card" style="max-width:420px">
    <h2 style="margin-bottom:8px">{{ mode === 'login' ? '登录' : '注册账号' }}</h2>
    <!-- 包一层 form：密码框回车即可提交 -->
    <form @submit.prevent="submit">
      <template v-if="mode === 'login'">
        <label class="label" for="login-id">用户名 / 手机号</label>
        <input id="login-id" v-model="identifier" autocomplete="username" placeholder="admin 或 138…" />
      </template>
      <template v-else>
        <label class="label" for="reg-username">用户名</label>
        <input id="reg-username" v-model="regUsername" autocomplete="username" placeholder="3~64 位小写字母、数字或下划线" />
        <label class="label" for="reg-phone">手机号</label>
        <input id="reg-phone" v-model="regPhone" type="tel" autocomplete="tel" placeholder="11 位手机号，可用于登录" />
      </template>
      <label class="label" for="login-pass">密码{{ mode === 'register' ? '（至少 8 位）' : '' }}</label>
      <input
        id="login-pass"
        v-model="password"
        type="password"
        :autocomplete="mode === 'login' ? 'current-password' : 'new-password'"
      />
      <template v-if="mode === 'register'">
        <label class="label" for="reg-confirm">确认密码</label>
        <input
          id="reg-confirm"
          v-model="regConfirm"
          type="password"
          autocomplete="new-password"
          placeholder="请再次输入密码"
        />
        <p v-if="regConfirm && password !== regConfirm" class="msg warn" style="margin:6px 0 0">两次输入的密码不一致</p>
      </template>
      <button
        class="btn btn-primary"
        type="submit"
        :disabled="loading || !(mode === 'login' ? loginReady() : registerReady())"
        style="width:100%;margin-top:10px;padding:11px"
      >
        {{ loading ? '处理中…' : mode === 'login' ? '登 录' : '注册并登录' }}
      </button>
    </form>
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
