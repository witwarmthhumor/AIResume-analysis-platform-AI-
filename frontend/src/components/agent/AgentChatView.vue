<script setup>
/* AgentChatView —— 用户端「AI 客服」整页（v3.4）
   左侧历史会话列表（新建/切换/删除，持久化），右侧 AgentChatCore；
   v4.0 增加页签模式：「AI 对话」与「一键求职准备」（AgentRunView）互切。 */
import { onMounted, ref } from 'vue'
import { del, get, post } from '../../api.js'
import AgentChatCore from './AgentChatCore.vue'
import AgentRunView from './AgentRunView.vue'
import Icon from '../Icon.vue'

const mode = ref('chat') // chat=普通对话框 run=一键求职准备
const sessions = ref([])
const activeSessionId = ref(null)
const loading = ref(false)
const listError = ref('')

async function loadSessions() {
  loading.value = true
  listError.value = ''
  try {
    sessions.value = await get('/api/agent/sessions')
  } catch (e) {
    listError.value = e.message || '对话列表加载失败'
  } finally {
    loading.value = false
  }
}

async function newSession() {
  try {
    const s = await post('/api/agent/sessions', {})
    sessions.value.unshift(s)
    activeSessionId.value = s.id
  } catch (e) {
    listError.value = e.message || '创建对话失败'
  }
}

function selectSession(s) {
  if (s.id !== activeSessionId.value) activeSessionId.value = s.id
}

async function deleteSession(s) {
  if (!confirm(`删除对话「${s.title}」？删除后不可恢复。`)) return
  try {
    await del(`/api/agent/sessions/${s.id}`)
    sessions.value = sessions.value.filter((x) => x.id !== s.id)
    if (activeSessionId.value === s.id) activeSessionId.value = null
  } catch (e) {
    listError.value = e.message || '删除失败'
  }
}

// Core 自动新建会话后同步到列表
function onSessionCreated(s) {
  if (!sessions.value.some((x) => x.id === s.id)) sessions.value.unshift(s)
  activeSessionId.value = s.id
}

onMounted(loadSessions)
</script>

<template>
  <section class="agent-view">
    <aside class="av-sidebar">
      <button class="av-new" @click="newSession"><span class="plus"><Icon name="plus" :size="14" /></span> 新建对话</button>
      <div class="av-mode">
        <button
          class="av-tab"
          :class="{ active: mode === 'chat' }"
          @click="mode = 'chat'"
        >
<Icon name="chat" :size="14" /> 对话
</button>
        <button
          class="av-tab"
          :class="{ active: mode === 'run' }"
          @click="mode = 'run'"
        >
<Icon name="zap" :size="14" /> 一键求职准备
</button>
      </div>
      <div class="av-list">
        <div v-if="loading" class="loading-row"><span class="spinner"></span>加载中…</div>
        <div v-else-if="listError" class="msg error">{{ listError }}</div>
        <template v-else>
          <div
            v-for="s in sessions"
            :key="s.id"
            class="av-item"
            :class="{ active: s.id === activeSessionId }"
            @click="selectSession(s)"
          >
            <span class="av-title"><Icon name="chat" :size="13" /> {{ s.title }}</span>
            <button class="icon-btn danger av-del" aria-label="删除" title="删除" @click.stop="deleteSession(s)"><Icon name="trash" :size="14" /></button>
          </div>
          <div v-if="!sessions.length" class="empty">暂无历史对话</div>
        </template>
      </div>
    </aside>

    <div class="av-main">
      <AgentRunView v-if="mode === 'run'" />
      <AgentChatCore
        v-else
        :session-id="activeSessionId"
        @session-created="onSessionCreated"
        @title-updated="loadSessions"
      />
    </div>
  </section>
</template>

<style scoped>
.agent-view {
  display: flex;
  gap: var(--sp-3);
  height: calc(100vh - 130px);
  min-height: 520px;
}
.av-sidebar {
  width: 210px;
  flex-shrink: 0;
  background: #fff;
  border: 1px solid var(--c-border);
  border-radius: var(--radius-md);
  display: flex;
  flex-direction: column;
  padding: var(--sp-3);
}
.av-new {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--sp-1);
  padding: var(--sp-2) var(--sp-3);
  border: 1px solid var(--c-primary-border);
  border-radius: var(--radius-sm);
  background: var(--wash-primary);
  color: var(--c-primary-dark);
  font-size: var(--fs-sm);
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
  flex-shrink: 0;
  transition: background var(--dur-fast) var(--ease);
}
.av-new:hover {
  background: var(--c-primary-light);
}
.plus {
  display: inline-flex;
  align-items: center;
}
.av-mode {
  display: flex;
  flex-direction: column;
  gap: var(--sp-1);
  margin-top: var(--sp-3);
}
.av-tab {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  padding: var(--sp-2) var(--sp-3);
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--c-text-2);
  font-size: var(--fs-sm);
  font-family: inherit;
  text-align: left;
  cursor: pointer;
  transition:
    background var(--dur-fast) var(--ease),
    color var(--dur-fast) var(--ease);
}
.av-tab:hover {
  background: var(--c-hover);
}
.av-tab.active {
  background: var(--wash-primary);
  color: var(--c-primary-dark);
  font-weight: 600;
}
.av-list {
  flex: 1;
  overflow-y: auto;
  margin-top: var(--sp-3);
  display: flex;
  flex-direction: column;
  gap: var(--sp-1);
}
.av-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-1);
  padding: var(--sp-1) var(--sp-2);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: background var(--dur-fast) var(--ease);
}
.av-item:hover {
  background: var(--c-hover);
}
.av-item.active {
  background: var(--wash-primary);
  box-shadow: inset 2px 0 0 var(--c-primary);
}
.av-title {
  font-size: var(--fs-sm);
  color: var(--c-text-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
  min-width: 0;
}
.av-title .ui-icon {
  margin-right: var(--sp-1);
  color: var(--c-faint);
}
.av-item.active .av-title {
  color: var(--c-primary-dark);
  font-weight: 600;
}
/* 删除按钮样式收编到全局 .icon-btn.danger（常显，不再 hover 才出现） */
.av-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: #fff;
  border: 1px solid var(--c-border);
  border-radius: var(--radius-md);
  padding: var(--sp-4);
}
@media (max-width: 768px) {
  .av-sidebar {
    width: 64px;
  }
  .av-title,
  .av-del,
  .av-new .plus {
    display: none;
  }
}
</style>
