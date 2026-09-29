<script setup>
/* ProfileDialog —— 个人信息弹窗（v4.2，对齐参考页面形态）。
   头像选择（8 预设）+ 用户名（只读）+ 身份证（可编辑）+ 手机号（空可设一次，非空只读）
   + 邮箱/注册时间（只读）+ 内嵌修改密码（成功即全端下线 → 回登录页）。
   保存调 PUT /api/me/profile；成功后 emit('saved', user) 让 App 更新全局用户态。 */
import { computed, ref } from 'vue'
import { post, put } from '../api.js'
import { fmtDateTime, AVATAR_PRESETS, avatarPreset } from '../utils.js'

const props = defineProps({ user: { type: Object, required: true } })
const emit = defineEmits(['saved', 'logout', 'close'])

const form = ref({
  avatar_key: props.user.avatar_key || '',
  id_card: props.user.id_card || '',
  phone: props.user.phone || '', // 只读回显；空账号可设置一次
})
const saving = ref(false)
const msg = ref('')
const msgOk = ref(false)

const canSetPhone = computed(() => !props.user.phone)
const currentPreset = computed(() => avatarPreset({ avatar_key: form.value.avatar_key }))

function pickAvatar(key) {
  form.value.avatar_key = key
}

async function save() {
  saving.value = true
  msg.value = ''
  try {
    const body = await put('/api/me/profile', {
      avatar_key: form.value.avatar_key || null,
      id_card: form.value.id_card || null,
      phone: canSetPhone.value ? form.value.phone || null : null,
    })
    msgOk.value = true
    msg.value = '个人信息已保存'
    emit('saved', body)
  } catch (e) {
    msgOk.value = false
    msg.value = e.message || '保存失败，请重试'
  } finally {
    saving.value = false
  }
}

// —— 内嵌修改密码：成功即全端下线，回登录页 ——
const pwd = ref({ old: '', neu: '', confirm: '' })
const pwdBusy = ref(false)
const pwdMsg = ref('')
const pwdOk = ref(false)

async function changePassword() {
  if (pwdBusy.value) return
  pwdBusy.value = true
  pwdMsg.value = ''
  try {
    await post('/api/auth/change-password', {
      old_password: pwd.value.old,
      new_password: pwd.value.neu,
    })
    pwdOk.value = true
    pwdMsg.value = '密码已更新，所有登录状态已失效，即将返回登录页…'
    setTimeout(() => emit('logout'), 1500)
  } catch (e) {
    pwdOk.value = false
    pwdMsg.value = e.message || '修改失败，请重试'
  } finally {
    pwdBusy.value = false
  }
}
</script>

<template>
  <div class="modal-overlay" @click.self="emit('close')">
    <div class="profile-box">
      <button class="modal-close" @click="emit('close')" aria-label="关闭">✕</button>
      <h2 class="box-title">🪪 个人信息</h2>

      <!-- 头像 -->
      <div class="field-label">当前头像</div>
      <div class="avatar-row">
        <span
          class="avatar-big"
          :style="currentPreset ? { background: currentPreset.color } : {}"
        >{{ currentPreset?.emoji || props.user.username?.[0]?.toUpperCase() || '?' }}</span>
      </div>
      <div class="field-label">选择头像</div>
      <div class="avatar-grid">
        <button
          v-for="p in AVATAR_PRESETS"
          :key="p.key"
          class="avatar-pick"
          :class="{ picked: form.avatar_key === p.key }"
          :style="{ background: p.color }"
          @click="pickAvatar(p.key)"
        >{{ p.emoji }}</button>
      </div>

      <!-- 字段 -->
      <div class="field-label">用户名（不可修改）</div>
      <input :value="props.user.username" readonly class="readonly" />
      <div class="field-label">身份证号</div>
      <input v-model="form.id_card" placeholder="15 或 18 位；留空不填" />
      <div class="field-label">手机号{{ canSetPhone ? '（仅可设置一次）' : '（不可修改）' }}</div>
      <input
        v-model="form.phone"
        :readonly="!canSetPhone"
        :class="{ readonly: !canSetPhone }"
        placeholder="11 位手机号"
      />
      <div class="field-label" v-if="props.user.email">邮箱（不可修改）</div>
      <input v-if="props.user.email" :value="props.user.email" readonly class="readonly" />
      <div class="field-label">注册时间</div>
      <input :value="fmtDateTime(props.user.created_at)" readonly class="readonly" />

      <button class="btn btn-primary save-btn" :disabled="saving" @click="save">
        {{ saving ? '保存中…' : '保存修改' }}
      </button>
      <p v-if="msg" class="msg" :class="msgOk ? 'ok' : 'error'">{{ msg }}</p>

      <!-- 修改密码 -->
      <div class="pwd-divider"></div>
      <h3 class="pwd-title">🔑 修改密码</h3>
      <form class="pwd-form" @submit.prevent="changePassword">
        <input v-model="pwd.old" type="password" placeholder="原密码" autocomplete="current-password" />
        <input v-model="pwd.neu" type="password" placeholder="新密码（至少 8 位，含字母数字）" autocomplete="new-password" />
        <input v-model="pwd.confirm" type="password" placeholder="确认新密码" autocomplete="new-password" />
        <button
          class="btn btn-primary save-btn"
          type="submit"
          :disabled="pwdBusy || !pwd.old || pwd.neu.length < 8 || pwd.neu !== pwd.confirm"
        >{{ pwdBusy ? '保存中…' : '修改密码' }}</button>
      </form>
      <p v-if="pwdMsg" class="msg" :class="pwdOk ? 'ok' : 'error'">{{ pwdMsg }}</p>
    </div>
  </div>
</template>

<style scoped>
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 60;
  padding: 20px;
  animation: modal-fade 0.2s ease both;
}
.profile-box {
  position: relative;
  width: 100%;
  max-width: 440px;
  max-height: 88vh;
  overflow-y: auto;
  background: #fff;
  border-radius: 16px;
  padding: 22px 24px;
  box-shadow: 0 18px 48px rgba(0, 0, 0, 0.2);
  animation: modal-pop 0.22s ease both;
}
.box-title {
  margin: 0 0 6px;
  font-size: 16px;
}
.modal-close {
  position: absolute;
  top: 14px;
  right: 14px;
  width: 30px;
  height: 30px;
  border-radius: 50%;
  border: 0;
  background: var(--c-bg-soft, #f3f4f6);
  color: var(--c-muted, #6b7280);
  font-size: 13px;
  cursor: pointer;
}
.field-label {
  font-size: 12px;
  color: var(--c-muted, #6b7280);
  margin: 12px 0 4px;
}
.avatar-big {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 52px;
  height: 52px;
  border-radius: 50%;
  background: var(--c-primary);
  font-size: 26px;
  box-shadow: 0 2px 10px rgba(16, 185, 129, 0.3);
}
.avatar-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.avatar-pick {
  width: 42px;
  height: 42px;
  border-radius: 50%;
  border: 2.5px solid transparent;
  font-size: 20px;
  cursor: pointer;
  transition: transform 0.14s ease, border-color 0.14s ease;
}
.avatar-pick:hover {
  transform: scale(1.1);
}
.avatar-pick.picked {
  border-color: #0f172a;
  box-shadow: 0 0 0 3px rgba(15, 23, 42, 0.12);
}
input {
  width: 100%;
  box-sizing: border-box;
  border: 1.5px solid var(--c-border, #e5e7eb);
  border-radius: 10px;
  padding: 9px 12px;
  font-size: 13px;
  font-family: inherit;
  outline: none;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
input:focus {
  border-color: #6ee7b7;
  box-shadow: 0 0 0 3px rgb(16 185 129 / 12%);
}
input.readonly {
  background: var(--c-bg-soft, #f3f4f6);
  color: var(--c-muted, #6b7280);
}
.save-btn {
  margin-top: 14px;
  width: 100%;
  padding: 10px;
}
.pwd-divider {
  border-top: 1px dashed var(--c-border, #e5e7eb);
  margin: 18px 0 10px;
}
.pwd-title {
  margin: 0 0 8px;
  font-size: 14px;
}
.pwd-form {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
@keyframes modal-fade {
  from { opacity: 0; }
  to { opacity: 1; }
}
@keyframes modal-pop {
  from { opacity: 0; transform: translateY(12px) scale(0.97); }
  to { opacity: 1; transform: translateY(0) scale(1); }
}
</style>
