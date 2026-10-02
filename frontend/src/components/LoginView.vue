<script setup>
/* LoginView —— 独立登录首屏（v4.2.1，对齐参考页面交互：先登录才跳转进入系统）。
   未登录时 App 只渲染本页（无侧边栏/顶栏）；登录成功 emit logged-in，App 切入主界面。
   登录表单复用 LoginPanel（登录/注册双模式 + 用户名/邮箱/手机号三标识）。 */
import LoginPanel from './LoginPanel.vue'

const emit = defineEmits(['logged-in'])
</script>

<template>
  <div class="login-screen">
    <div class="login-card-wrap">
      <div class="brand-block">
        <svg class="logo-svg" viewBox="0 0 32 32" fill="none" aria-hidden="true">
          <path d="M4 8a4 4 0 0 1 4-4h16a4 4 0 0 1 4 4v10a4 4 0 0 1-4 4H12l-6 5v-5a4 4 0 0 1-2-3.46V8z" fill="url(#logo-lg)" />
          <circle cx="11" cy="13" r="1.8" fill="#fff" />
          <circle cx="16" cy="13" r="1.8" fill="#fff" />
          <circle cx="21" cy="13" r="1.8" fill="#fff" />
          <defs>
            <linearGradient id="logo-lg" x1="4" y1="4" x2="28" y2="28">
              <stop stop-color="#10b981" />
              <stop offset="1" stop-color="#059669" />
            </linearGradient>
          </defs>
        </svg>
        <h1 class="brand-title">AI 简历分析 <span class="plus">+</span> 模拟面试</h1>
        <p class="brand-sub">求职路上的私人面试官</p>
      </div>
      <LoginPanel @logged-in="(u) => emit('logged-in', u)" />
    </div>
  </div>
</template>

<style scoped>
.login-screen {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  font-family: var(--font, system-ui, 'Microsoft YaHei', sans-serif);
  color: var(--c-text);
  background:
    radial-gradient(1200px 420px at 80% -10%, rgb(16 185 129 / 12%), transparent 60%),
    radial-gradient(900px 320px at 10% 0%, rgb(59 130 246 / 6%), transparent 55%),
    #f6f9f7;
}
.login-card-wrap {
  width: 100%;
  max-width: 440px;
}
.brand-block {
  text-align: center;
  margin-bottom: 20px;
}
.logo-svg {
  width: 44px;
  height: 44px;
  filter: drop-shadow(0 2px 8px rgb(16 185 129 / 30%));
}
.brand-title {
  margin: 10px 0 4px;
  font-size: 20px;
  font-weight: 700;
}
.plus {
  color: var(--c-primary);
}
.brand-sub {
  margin: 0 0 16px;
  font-size: 13px;
  color: var(--c-muted);
  letter-spacing: 1px;
}

/* —— 响应式（v4.2.1）—— */
@media (max-width: 480px) {
  .brand-title {
    font-size: 17px;
  }
}

</style>
