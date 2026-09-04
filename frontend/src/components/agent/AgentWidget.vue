<script setup>
/* AgentWidget —— 管理端右下角悬浮 AI 客服（v3.4，仅 admin 由 App 挂载）
   收起：56px 青绿气泡；展开：轻遮罩 + 400px 浮层，内嵌 AgentChatCore(compact)。
   历史会话持久化：头部「历史」下拉切换、「新建」开新会话。 */
import { nextTick, ref } from 'vue'
import { del, get, post } from '../../api.js'
import AgentChatCore from './AgentChatCore.vue'

const open = ref(false)
const sessions = ref([])
const activeId = ref(null)
const showHistory = ref(false)
const loaded = ref(false)

async function loadSessions() {
  try {
    sessions.value = await get('/api/agent/sessions')
  } catch {
    /* 静默：浮层内 Core 会展示错误 */
  }
}

async function toggle() {
  open.value = !open.value
  if (open.value && !loaded.value) {
    loaded.value = true
    loadSessions()
  }
  await nextTick()
}

function close() {
  open.value = false
  showHistory.value = false
}

async function newSession() {
  try {
    const s = await post('/api/agent/sessions', {})
    sessions.value.unshift(s)
    activeId.value = s.id
    showHistory.value = false
  } catch {
    /* 忽略 */
  }
}

function pick(s) {
  activeId.value = s.id
  showHistory.value = false
}

async function remove(s, evt) {
  evt.stopPropagation()
  if (!confirm(`删除对话「${s.title}」？`)) return
  await del(`/api/agent/sessions/${s.id}`)
  sessions.value = sessions.value.filter((x) => x.id !== s.id)
  if (activeId.value === s.id) activeId.value = null
}

function onSessionCreated(s) {
  if (!sessions.value.some((x) => x.id === s.id)) sessions.value.unshift(s)
  activeId.value = s.id
}
</script>

<template>
  <div class="agent-widget">
    <!-- 收起态气泡 -->
    <button v-if="!open" class="aw-fab" @click="toggle" title="AI 客服">
      <svg viewBox="0 0 32 32" width="26" height="26" fill="none" aria-hidden="true">
        <path d="M5 9a4 4 0 0 1 4-4h14a4 4 0 0 1 4 4v9a4 4 0 0 1-4 4H13l-6 4.5V22a4 4 0 0 1-2-3.46V9z" fill="#fff"/>
        <circle cx="12" cy="13.5" r="1.7" fill="#10b981"/>
        <circle cx="16.5" cy="13.5" r="1.7" fill="#10b981"/>
        <circle cx="21" cy="13.5" r="1.7" fill="#10b981"/>
      </svg>
    </button>

    <!-- 轻遮罩 -->
    <div v-if="open" class="aw-mask" @click="close"></div>

    <!-- 展开浮层 -->
    <div v-if="open" class="aw-panel">
      <div class="aw-head">
        <span class="aw-dot"></span>
        <div class="aw-title">
          <div class="aw-name">AI 客服</div>
          <div class="aw-sub">在线为您解答技术问题</div>
        </div>
        <button class="aw-head-btn" @click="showHistory = !showHistory" title="历史对话">🕘 历史</button>
        <button class="aw-head-btn" @click="newSession" title="新建对话">＋ 新建</button>
        <button class="aw-close" @click="close">✕</button>

        <!-- 历史下拉 -->
        <div v-if="showHistory" class="aw-history">
          <div v-if="!sessions.length" class="aw-h-empty">暂无历史对话</div>
          <div
            v-for="s in sessions"
            :key="s.id"
            class="aw-h-item"
            :class="{ active: s.id === activeId }"
            @click="pick(s)"
          >
            <span class="aw-h-title">💬 {{ s.title }}</span>
            <button class="aw-h-del" @click="remove(s, $event)">🗑</button>
          </div>
        </div>
      </div>

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
  right: 26px;
  bottom: 26px;
  z-index: 40;
}
.aw-fab {
  width: 56px;
  height: 56px;
  border-radius: 50%;
  border: 0;
  cursor: pointer;
  background: linear-gradient(135deg, #10b981, #059669);
  box-shadow: 0 6px 20px rgb(16 185 129 / 40%);
  display: flex;
  align-items: center;
  justify-content: center;
  transition: transform 0.15s ease;
}
.aw-fab:hover {
  transform: translateY(-2px) scale(1.04);
}
.aw-mask {
  position: fixed;
  inset: 0;
  background: rgb(0 0 0 / 18%);
  z-index: 41;
  animation: aw-fade 0.18s ease both;
}
.aw-panel {
  position: fixed;
  right: 26px;
  bottom: 26px;
  width: 400px;
  max-width: calc(100vw - 40px);
  height: min(620px, calc(100vh - 80px));
  background: #fff;
  border-radius: 18px;
  box-shadow: 0 18px 50px rgb(0 0 0 / 22%);
  z-index: 42;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  animation: aw-pop 0.2s ease both;
}
@keyframes aw-fade {
  from { opacity: 0; }
  to { opacity: 1; }
}
@keyframes aw-pop {
  from { opacity: 0; transform: translateY(14px) scale(0.98); }
  to { opacity: 1; transform: translateY(0) scale(1); }
}
.aw-head {
  position: relative;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 14px;
  border-bottom: 1px solid var(--c-border, #e5e7eb);
  background: linear-gradient(135deg, rgb(16 185 129 / 8%), #fff);
}
.aw-dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: #10b981;
  box-shadow: 0 0 0 3px rgb(16 185 129 / 18%);
  flex-shrink: 0;
}
.aw-title {
  flex: 1;
  min-width: 0;
}
.aw-name {
  font-size: 14px;
  font-weight: 700;
}
.aw-sub {
  font-size: 10.5px;
  color: var(--c-faint, #9ca3af);
}
.aw-head-btn {
  border: 1px solid rgb(16 185 129 / 30%);
  background: #fff;
  color: var(--c-primary-dark, #059669);
  border-radius: 8px;
  font-size: 11.5px;
  font-family: inherit;
  padding: 5px 8px;
  cursor: pointer;
  white-space: nowrap;
}
.aw-head-btn:hover {
  background: rgb(16 185 129 / 8%);
}
.aw-close {
  border: 0;
  background: transparent;
  font-size: 14px;
  color: var(--c-faint, #9ca3af);
  cursor: pointer;
  padding: 4px 6px;
}
.aw-close:hover {
  color: var(--c-text, #1a1b1c);
}
.aw-history {
  position: absolute;
  top: calc(100% + 6px);
  right: 46px;
  width: 240px;
  max-height: 280px;
  overflow-y: auto;
  background: #fff;
  border: 1px solid var(--c-border, #e5e7eb);
  border-radius: 12px;
  box-shadow: 0 10px 30px rgb(0 0 0 / 14%);
  padding: 6px;
  z-index: 5;
}
.aw-h-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 7px 9px;
  border-radius: 8px;
  cursor: pointer;
}
.aw-h-item:hover {
  background: rgb(16 185 129 / 6%);
}
.aw-h-item.active {
  background: rgb(16 185 129 / 12%);
}
.aw-h-title {
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.aw-h-del {
  border: 0;
  background: transparent;
  cursor: pointer;
  font-size: 11px;
}
.aw-h-empty {
  font-size: 12px;
  color: var(--c-faint, #9ca3af);
  text-align: center;
  padding: 14px 0;
}
.aw-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  padding: 12px;
}
</style>
