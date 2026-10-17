<script setup>
/* ProfileDialog —— 个人信息弹窗（v4.2，对齐参考页面形态）。
   头像选择（8 预设）+ 用户名（只读）+ 身份证（可编辑）+ 手机号（空可设一次，非空只读）
   + 邮箱/注册时间（只读）+ 内嵌修改密码（成功即全端下线 → 回登录页）。
   保存调 PUT /api/me/profile；成功后 emit('saved', user) 让 App 更新全局用户态。 */
import Icon from './Icon.vue'
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
      id_card: form.value.id_card, // G-7：原样发送，空串=清空（后端 None=不改，两者语义不同）
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
    <div class="popup profile-box">
      <button class="modal-close" aria-label="关闭" title="关闭" @click="emit('close')">
        <Icon name="x" :size="14" />
      </button>
      <h2 class="box-title">个人信息</h2>

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
        >
{{ p.emoji }}
</button>
      </div>

      <!-- 字段 -->
      <div class="field-label">用户名（不可修改）</div>
      <input :value="props.user.username" readonly class="input readonly" />
      <div class="field-label">身份证号</div>
      <input v-model="form.id_card" class="input" placeholder="15 或 18 位；留空不填" />
      <div class="field-label">手机号{{ canSetPhone ? '（仅可设置一次）' : '（不可修改）' }}</div>
      <input
        v-model="form.phone"
        class="input"
        :readonly="!canSetPhone"
        :class="{ readonly: !canSetPhone }"
        placeholder="11 位手机号"
      />
      <div v-if="props.user.email" class="field-label">邮箱（不可修改）</div>
      <input v-if="props.user.email" :value="props.user.email" readonly class="input readonly" />
      <div class="field-label">注册时间</div>
      <input :value="fmtDateTime(props.user.created_at)" readonly class="input readonly" />

      <button class="btn btn-primary save-btn" :disabled="saving" @click="save">
        {{ saving ? '保存中…' : '保存修改' }}
      </button>
      <p v-if="msg" class="msg" :class="msgOk ? 'ok' : 'error'">{{ msg }}</p>

      <!-- 修改密码 -->
      <div class="pwd-divider"></div>
      <h3 class="pwd-title">修改密码</h3>
      <form class="pwd-form" @submit.prevent="changePassword">
        <input v-model="pwd.old" class="input" type="password" placeholder="原密码" autocomplete="current-password" />
        <input v-model="pwd.neu" class="input" type="password" placeholder="新密码（至少 8 位，含字母数字）" autocomplete="new-password" />
        <input v-model="pwd.confirm" class="input" type="password" placeholder="确认新密码" autocomplete="new-password" />
        <button
          class="btn btn-primary save-btn"
          type="submit"
          :disabled="pwdBusy || !pwd.old || pwd.neu.length < 8 || pwd.neu !== pwd.confirm"
        >
{{ pwdBusy ? '保存中…' : '修改密码' }}
</button>
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
  z-index: var(--z-modal);
  padding: var(--sp-5);
  animation: modal-fade var(--dur) var(--ease) both;
}
/* 弹层的圆角/阴影/边框走全局 .popup 壳 */
.profile-box {
  position: relative;
  width: 100%;
  max-width: 440px;
  max-height: 88vh;
  overflow-y: auto;
  padding: var(--sp-6);
  animation: modal-pop var(--dur) var(--ease) both;
}
.box-title {
  margin: 0 0 var(--sp-2);
  font-size: var(--fs-lg);
}
.modal-close {
  position: absolute;
  top: var(--sp-3);
  right: var(--sp-3);
  width: 30px;
  height: 30px;
  border-radius: 50%;
  border: 0;
  background: var(--c-bg-soft);
  color: var(--c-muted);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition:
    background var(--dur-fast) var(--ease),
    color var(--dur-fast) var(--ease);
}
.modal-close:hover {
  background: var(--c-hover);
  color: var(--c-text);
}
.field-label {
  font-size: var(--fs-xs);
  color: var(--c-muted);
  margin: var(--sp-3) 0 var(--sp-1);
}
.avatar-big {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 52px;
  height: 52px;
  border-radius: 50%;
  background: var(--c-primary);
  font-size: 26px; /* emoji 头像的展示尺寸（用户自选表情，保留） */
}
.avatar-grid {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-2);
}
.avatar-pick {
  width: 42px;
  height: 42px;
  border-radius: 50%;
  border: 2px solid transparent;
  font-size: 20px;
  cursor: pointer;
  transition:
    transform var(--dur-fast) var(--ease),
    border-color var(--dur-fast) var(--ease);
}
.avatar-pick:hover {
  transform: scale(1.1);
}
.avatar-pick.picked {
  border-color: var(--c-primary);
  box-shadow: 0 0 0 3px var(--wash-primary);
}
/* 输入控件统一走全局 .input；这里只补只读态的底色区分 */
input.readonly {
  background: var(--c-bg-soft);
  color: var(--c-muted);
}
.save-btn {
  margin-top: var(--sp-3);
  width: 100%;
  padding: 10px;
}
.pwd-divider {
  border-top: 1px dashed var(--c-border);
  margin: var(--sp-4) 0 var(--sp-3);
}
.pwd-title {
  margin: 0 0 var(--sp-2);
  font-size: var(--fs-md);
}
.pwd-form {
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
}
@keyframes modal-fade {
  from { opacity: 0; }
  to { opacity: 1; }
}
@keyframes modal-pop {
  from { opacity: 0; transform: translateY(12px) scale(0.97); }
  to { opacity: 1; transform: translateY(0) scale(1); }
}

/* —— 响应式（v4.2.1）—— */
@media (max-width: 480px) {
  .profile-box {
    padding: var(--sp-4) var(--sp-3);
  }
  .avatar-pick {
    width: 38px;
    height: 38px;
    font-size: 18px;
  }
}

</style>
