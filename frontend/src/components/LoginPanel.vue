<script setup>
/* LoginPanel —— 登录/注册双模式表单（模态内使用）。
   v4.2.1 起登录标识=用户名（手机号兼容；v6 审计 C6-6 口径补注：存量邮箱账号仍可用邮箱登录，后端三态识别）。
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
// 注册：用户名过前端正则 + 手机号合法 + 密码 ≥8 且两次一致（v4.2.1 起无邮箱字段；
// 后端登录仍兼容存量账号的邮箱标识，见 username_service/is_valid_username 同口径校验）
const registerReady = () =>
  USERNAME_RE.test(regUsername.value.trim().toLowerCase()) &&
  PHONE_RE.test(regPhone.value.trim()) &&
  password.value.length >= 8 &&
  password.value === regConfirm.value
</script>

<template>
  <section class="card login-card">
    <h2>{{ mode === 'login' ? '登录' : '注册账号' }}</h2>
    <!-- 包一层 form：密码框回车即可提交 -->
    <form @submit.prevent="submit">
      <template v-if="mode === 'login'">
        <label class="label" for="login-id">用户名 / 手机号</label>
        <input id="login-id" v-model="identifier" class="input" autocomplete="username" />
      </template>
      <template v-else>
        <label class="label" for="reg-username">用户名</label>
        <input id="reg-username" v-model="regUsername" class="input" autocomplete="username" placeholder="3~64 位小写字母、数字或下划线" />
        <label class="label" for="reg-phone">手机号</label>
        <input id="reg-phone" v-model="regPhone" class="input" type="tel" autocomplete="tel" placeholder="11 位手机号，可用于登录" />
      </template>
      <label class="label" for="login-pass">密码{{ mode === 'register' ? '（至少 8 位）' : '' }}</label>
      <input
        id="login-pass"
        v-model="password"
        class="input"
        type="password"
        :autocomplete="mode === 'login' ? 'current-password' : 'new-password'"
      />
      <template v-if="mode === 'register'">
        <label class="label" for="reg-confirm">确认密码</label>
        <input
          id="reg-confirm"
          v-model="regConfirm"
          class="input"
          type="password"
          autocomplete="new-password"
          placeholder="请再次输入密码"
        />
        <p v-if="regConfirm && password !== regConfirm" class="msg warn mt-2">两次输入的密码不一致</p>
      </template>
      <button
        class="btn btn-primary w-full submit-btn"
        type="submit"
        :disabled="loading || !(mode === 'login' ? loginReady() : registerReady())"
      >
        {{ loading ? '处理中…' : mode === 'login' ? '登 录' : '注册并登录' }}
      </button>
    </form>
    <p v-if="error" class="msg error">{{ error }}</p>
    <p class="switch">
      <span v-if="mode === 'login'">还没有账号？<b class="link" @click="mode='register'; error=''">注册</b></span>
      <span v-else>已有账号？<b class="link" @click="mode='login'; error=''">登录</b></span>
    </p>
  </section>
</template>

<style scoped>
.login-card {
  max-width: 420px;
}
.label {
  display: block;
  font-size: var(--fs-xs);
  color: var(--c-muted);
  margin: var(--sp-3) 0 var(--sp-1);
}
.submit-btn {
  margin-top: var(--sp-3);
  padding: 10px;
}
.switch {
  text-align: center;
  font-size: var(--fs-xs);
  color: var(--c-faint);
  margin: var(--sp-2) 0 0;
}
.link {
  color: var(--c-primary-dark);
  cursor: pointer;
}
.link:hover {
  text-decoration: underline;
}
</style>
