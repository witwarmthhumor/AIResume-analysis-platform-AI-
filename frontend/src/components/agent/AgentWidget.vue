<script setup>
/* AgentWidget —— 管理端右下角悬浮 AI 客服（v3.4，仅 admin 由 App 挂载）
   收起：56px 青绿气泡；展开：轻遮罩 + 400px 浮层，内嵌 AgentChatCore(compact)。
   历史会话持久化：头部「历史」下拉切换、「新建」开新会话。 */
import { ref } from 'vue'
import { del, get, post } from '../../api.js'
import AgentChatCore from './AgentChatCore.vue'
import Icon from '../Icon.vue'

const open = ref(false)
const sessions = ref([])
const activeId = ref(null)
const showHistory = ref(false)
const loaded = ref(false)
const widgetError = ref('')

async function loadSessions() {
  try {
    sessions.value = await get('/api/agent/sessions')
  } catch {
    /* 静默：浮层内 Core 会展示错误 */
  }
}

function toggle() {
  open.value = !open.value
  if (open.value && !loaded.value) {
    loaded.value = true
    loadSessions()
  }
}

function close() {
  open.value = false
  showHistory.value = false
}

async function newSession() {
  widgetError.value = ''
  try {
    const s = await post('/api/agent/sessions', {})
    sessions.value.unshift(s)
    activeId.value = s.id
    showHistory.value = false
  } catch (e) {
    widgetError.value = e.message || '创建对话失败' // 失败必须可见，不能静默吞掉
  }
}

function pick(s) {
  activeId.value = s.id
  showHistory.value = false
}

async function remove(s, evt) {
  evt.stopPropagation()
  if (!confirm(`删除对话「${s.title}」？`)) return
  widgetError.value = ''
  try {
    await del(`/api/agent/sessions/${s.id}`)
    sessions.value = sessions.value.filter((x) => x.id !== s.id)
    if (activeId.value === s.id) activeId.value = null
  } catch (e) {
    widgetError.value = e.message || '删除失败'
  }
}

function onSessionCreated(s) {
  if (!sessions.value.some((x) => x.id === s.id)) sessions.value.unshift(s)
  activeId.value = s.id
}
</script>

<template>
  <div class="agent-widget">
    <!-- 收起态气泡 -->
    <button v-if="!open" class="aw-fab" title="AI 客服" @click="toggle">
      <svg viewBox="0 0 32 32" width="26" height="26" fill="none" aria-hidden="true">
        <path d="M5 9a4 4 0 0 1 4-4h14a4 4 0 0 1 4 4v9a4 4 0 0 1-4 4H13l-6 4.5V22a4 4 0 0 1-2-3.46V9z" fill="#fff" />
        <circle cx="12" cy="13.5" r="1.7" fill="var(--c-primary)" />
        <circle cx="16.5" cy="13.5" r="1.7" fill="var(--c-primary)" />
        <circle cx="21" cy="13.5" r="1.7" fill="var(--c-primary)" />
      </svg>
    </button>

    <!-- 轻遮罩 -->
    <div v-if="open" class="aw-mask" @click="close"></div>

    <!-- 展开浮层 -->
    <div v-if="open" class="aw-panel popup">
      <div class="aw-head">
        <span class="aw-dot"></span>
        <div class="aw-title">
          <div class="aw-name">AI 客服</div>
          <div class="aw-sub">在线为您解答技术问题</div>
        </div>
        <button class="aw-head-btn" title="历史对话" @click="showHistory = !showHistory"><Icon name="clock" :size="13" /> 历史</button>
        <button class="aw-head-btn" title="新建对话" @click="newSession"><Icon name="plus" :size="13" /> 新建</button>
        <button class="icon-btn" aria-label="关闭" title="关闭" @click="close"><Icon name="x" :size="16" /></button>

        <!-- 历史下拉 -->
        <div v-if="showHistory" class="aw-history popup">
          <div v-if="!sessions.length" class="aw-h-empty">暂无历史对话</div>
          <div
            v-for="s in sessions"
            :key="s.id"
            class="aw-h-item"
            :class="{ active: s.id === activeId }"
            @click="pick(s)"
          >
            <span class="aw-h-title"><Icon name="chat" :size="13" /> {{ s.title }}</span>
            <button class="icon-btn danger" aria-label="删除" title="删除" @click="remove(s, $event)"><Icon name="trash" :size="14" /></button>
          </div>
        </div>
      </div>

      <p v-if="widgetError" class="msg error aw-error">{{ widgetError }}</p>
      <div class="aw-body">
        <AgentChatCore
          :session-id="activeId"
          compact
          @session-created="onSessionCreated"
          @title-updated="loadSessions"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
.agent-widget {
  position: fixed;
  right: var(--sp-6);
  bottom: var(--sp-6);
  z-index: var(--z-modal);
}
.aw-fab {
  width: 56px;
  height: 56px;
  border-radius: var(--radius-full);
  border: 0;
  cursor: pointer;
  background: var(--c-primary);
  box-shadow: var(--shadow-pop);
  display: flex;
  align-items: center;
  justify-content: center;
  transition:
    transform var(--dur-fast) var(--ease),
    background var(--dur-fast) var(--ease);
}
.aw-fab:hover {
  background: var(--c-primary-dark);
  transform: translateY(-2px);
}
.aw-mask {
  position: fixed;
  inset: 0;
  background: rgb(0 0 0 / 18%);
  animation: aw-fade var(--dur) var(--ease) both;
}
/* 圆角/阴影/边框由全局 .popup 弹层壳提供，这里只留布局 */
.aw-panel {
  position: fixed;
  right: var(--sp-6);
  bottom: var(--sp-6);
  width: 400px;
  max-width: calc(100vw - 40px);
  height: min(620px, calc(100vh - 80px));
  z-index: var(--z-pop);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  animation: aw-pop var(--dur) var(--ease) both;
}
@keyframes aw-fade {
  from { opacity: 0; }
  to { opacity: 1; }
}
@keyframes aw-pop {
  from { opacity: 0; transform: translateY(var(--sp-3)) scale(0.98); }
  to { opacity: 1; transform: translateY(0) scale(1); }
}
.aw-head {
  position: relative;
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  padding: var(--sp-3);
  border-bottom: 1px solid var(--c-border);
  background: var(--c-bg-soft);
}
/* 在线呼吸点：唯一允许使用 --c-primary-bright 的活性指示 */
.aw-dot {
  width: 9px;
  height: 9px;
  border-radius: var(--radius-full);
  background: var(--c-primary-bright);
  flex-shrink: 0;
  animation: pulse-dot 2s var(--ease) infinite;
}
.aw-title {
  flex: 1;
  min-width: 0;
}
.aw-name {
  font-size: var(--fs-md);
  font-weight: 600;
}
.aw-sub {
  font-size: var(--fs-xs);
  color: var(--c-faint);
}
.aw-head-btn {
  display: inline-flex;
  align-items: center;
  gap: var(--sp-1);
  border: 1px solid var(--c-primary-border);
  background: #fff;
  color: var(--c-primary-dark);
  border-radius: var(--radius-sm);
  font-size: var(--fs-xs);
  font-family: inherit;
  padding: var(--sp-1) var(--sp-2);
  cursor: pointer;
  white-space: nowrap;
  transition: background var(--dur-fast) var(--ease);
}
.aw-head-btn:hover {
  background: var(--wash-primary);
}
/* 关闭按钮样式收编到全局 .icon-btn */
.aw-error {
  margin: var(--sp-2) var(--sp-3) 0;
}
.aw-history {
  position: absolute;
  top: calc(100% + var(--sp-2));
  right: 48px;
  width: 240px;
  max-height: 280px;
  overflow-y: auto;
  padding: var(--sp-2);
  z-index: var(--z-pop);
}
.aw-h-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-1);
  padding: var(--sp-1) var(--sp-2);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: background var(--dur-fast) var(--ease);
}
.aw-h-item:hover {
  background: var(--c-hover);
}
.aw-h-item.active {
  background: var(--wash-primary);
}
.aw-h-title {
  font-size: var(--fs-xs);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.aw-h-title .ui-icon {
  margin-right: var(--sp-1);
  color: var(--c-faint);
}
/* 历史删除按钮样式收编到全局 .icon-btn.danger */
.aw-h-empty {
  font-size: var(--fs-xs);
  color: var(--c-faint);
  text-align: center;
  padding: var(--sp-3) 0;
}
.aw-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  padding: var(--sp-3);
}
</style>
