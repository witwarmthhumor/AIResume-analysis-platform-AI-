<script setup>
/* AgentChatView —— 用户端「AI 客服」整页（v3.4）
   左侧历史会话列表（新建/切换/删除，持久化），右侧 AgentChatCore；
   v4.0 增加页签模式：「AI 对话」与「🚀 一键求职准备」（AgentRunView）互切。 */
import { onMounted, ref } from 'vue'
import { del, get, post } from '../../api.js'
import AgentChatCore from './AgentChatCore.vue'
import AgentRunView from './AgentRunView.vue'

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
      <button class="av-new" @click="newSession"><span class="plus">＋</span> 新建对话</button>
      <div class="av-mode">
        <button
          class="av-tab"
          :class="{ active: mode === 'chat' }"
          @click="mode = 'chat'"
        >💬 对话</button>
        <button
          class="av-tab"
          :class="{ active: mode === 'run' }"
          @click="mode = 'run'"
        >🚀 一键求职准备</button>
      </div>
      <div class="av-list">
        <div v-if="loading" class="av-empty">加载中…</div>
        <div v-else-if="listError" class="av-empty error">{{ listError }}</div>
        <template v-else>
          <div
            v-for="s in sessions"
            :key="s.id"
            class="av-item"
            :class="{ active: s.id === activeSessionId }"
            @click="selectSession(s)"
          >
            <span class="av-title">💬 {{ s.title }}</span>
            <button class="av-del" title="删除对话" @click.stop="deleteSession(s)">🗑</button>
          </div>
          <div v-if="!sessions.length" class="av-empty">暂无历史对话</div>
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
  gap: 14px;
  height: calc(100vh - 130px);
  min-height: 520px;
}
.av-sidebar {
  width: 210px;
  flex-shrink: 0;
  background: #fff;
  border: 1px solid var(--c-border, #e5e7eb);
  border-radius: 14px;
  display: flex;
  flex-direction: column;
  padding: 12px 10px;
}
.av-new {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 9px 12px;
  border: 1.5px solid var(--c-primary, #10b981);
  border-radius: 10px;
  background: linear-gradient(135deg, rgb(16 185 129 / 8%), rgb(16 185 129 / 4%));
  color: var(--c-primary-dark, #059669);
  font-size: 13px;
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
  flex-shrink: 0;
  transition: background 0.15s ease;
}
.av-new:hover {
  background: rgb(16 185 129 / 14%);
}
.av-mode {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 10px;
}
.av-tab {
  padding: 8px 10px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--c-text-2, #374151);
  font-size: 12.5px;
  font-family: inherit;
  text-align: left;
  cursor: pointer;
  transition: background 0.12s ease;
}
.av-tab:hover {
  background: rgb(16 185 129 / 6%);
}
.av-tab.active {
  background: linear-gradient(135deg, rgb(16 185 129 / 14%), rgb(16 185 129 / 6%));
  color: var(--c-primary-dark, #059669);
  font-weight: 600;
}
.av-list {
  flex: 1;
  overflow-y: auto;
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.av-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  padding: 8px 10px;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.12s ease;
}
.av-item:hover {
  background: rgb(16 185 129 / 6%);
}
.av-item.active {
  background: linear-gradient(135deg, rgb(16 185 129 / 14%), rgb(16 185 129 / 6%));
  box-shadow: inset 2px 0 0 var(--c-primary, #10b981);
}
.av-title {
  font-size: 12.5px;
  color: var(--c-text-2, #374151);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
  min-width: 0;
}
.av-item.active .av-title {
  color: var(--c-primary-dark, #059669);
  font-weight: 600;
}
.av-del {
  border: 0;
  background: transparent;
  padding: 2px 4px;
  border-radius: 4px;
  font-size: 12px;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.12s ease;
  flex-shrink: 0;
}
.av-item:hover .av-del {
  opacity: 1;
}
.av-del:hover {
  background: rgb(239 68 68 / 10%);
}
.av-empty {
  font-size: 12px;
  color: var(--c-faint, #9ca3af);
  text-align: center;
  padding: 20px 8px;
}
.av-empty.error {
  color: #ef4444;
}
.av-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: #fff;
  border: 1px solid var(--c-border, #e5e7eb);
  border-radius: 14px;
  padding: 14px;
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
