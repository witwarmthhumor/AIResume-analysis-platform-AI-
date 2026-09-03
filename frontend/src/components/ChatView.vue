<script setup>
import { nextTick, onMounted, ref } from 'vue'
import { del, get, post, streamChat } from '../api.js'

// —— 会话管理 ——
const sessions = ref([])
const activeSessionId = ref(null)
const sidebarCollapsed = ref(false)
const sessionsLoading = ref(false)
const sessionsError = ref('')

// —— 聊天 ——
const messages = ref([]) // { role: 'user' | 'assistant' | 'error', content, citations }
const input = ref('')
const streaming = ref(false)
const error = ref('')
const chatBox = ref(null)

async function loadSessions() {
  sessionsLoading.value = true
  sessionsError.value = ''
  try {
    sessions.value = await get('/api/chat/sessions')
  } catch (e) {
    sessionsError.value = e.message || '对话列表加载失败'
  } finally {
    sessionsLoading.value = false
  }
}

async function newSession() {
  try {
    const s = await post('/api/chat/sessions', {})
    sessions.value.unshift(s)
    activeSessionId.value = s.id
    messages.value = []
    error.value = ''
  } catch (e) {
    error.value = e.message || '创建对话失败'
  }
}

async function selectSession(s) {
  if (s.id === activeSessionId.value) return
  activeSessionId.value = s.id
  messages.value = []
  error.value = ''
  try {
    messages.value = await get(`/api/chat/sessions/${s.id}/messages`)
  } catch (e) {
    error.value = e.message || '历史消息加载失败'
  }
  await nextTick()
  if (chatBox.value) chatBox.value.scrollTop = chatBox.value.scrollHeight
}

async function deleteSession(s) {
  if (!confirm(`删除对话「${s.title}」？删除后不可恢复。`)) return
  try {
    await del(`/api/chat/sessions/${s.id}`)
    sessions.value = sessions.value.filter((x) => x.id !== s.id)
    if (activeSessionId.value === s.id) {
      activeSessionId.value = null
      messages.value = []
    }
  } catch (e) {
    error.value = e.message || '删除失败'
  }
}

// —— SSE 事件解析（复用 Playground 逻辑） ——
function handleEvent(block, last) {
  const event = block.match(/^event: (.+)$/m)?.[1]
  const dataLine = block.match(/^data: (.+)$/m)?.[1]
  if (!event || !dataLine) return
  const data = JSON.parse(dataLine)
  if (event === 'meta') {
    messages.value.push({ id: `assistant-${Date.now()}`, role: 'assistant', content: '', citations: [], error: false })
  } else if (event === 'delta') {
    last.value.content += data.content
  } else if (event === 'done') {
    last.value.citations = data.citations || []
  } else if (event === 'error') {
    if (last.value && last.value.content === '') {
      messages.value.push({ id: `error-${Date.now()}`, role: 'error', content: data.content })
    } else {
      error.value = data.content
    }
  }
}

async function send() {
  const content = input.value.trim()
  if (!content || streaming.value) return

  // 无对话时自动新建（决策点4）
  if (!activeSessionId.value) {
    await newSession()
    if (!activeSessionId.value) return // 创建失败则不发送
  }

  input.value = ''
  messages.value.push({ id: `user-${Date.now()}`, role: 'user', content })
  streaming.value = true
  error.value = ''
  try {
    const { reader } = streamChat('/api/playground/ask', { content, session_id: activeSessionId.value })
    const stream = await reader
    const decoder = new TextDecoder()
    let buffer = ''
    const last = { value: null }
    while (true) {
      const { done, value } = await stream.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let boundary
      while ((boundary = buffer.indexOf('\n\n')) >= 0) {
        const block = buffer.slice(0, boundary)
        buffer = buffer.slice(boundary + 2)
        handleEvent(block, last)
        last.value = messages.value[messages.value.length - 1]
        await nextTick()
        if (chatBox.value) chatBox.value.scrollTop = chatBox.value.scrollHeight
      }
    }
    // 发送完成后刷新会话列表（标题可能已更新）
    loadSessions()
  } catch (e) {
    error.value = e.message || '网络异常，请重试'
  } finally {
    streaming.value = false
  }
}

onMounted(loadSessions)
</script>

<template>
  <section class="chat-view">
    <!-- —— 对话列表侧栏 —— -->
    <aside class="chat-sidebar" :class="{ collapsed: sidebarCollapsed }">
      <button v-if="!sidebarCollapsed" class="new-chat-btn" @click="newSession">
        <span class="plus">+</span> 新建对话
      </button>
      <button v-else class="new-chat-btn collapsed" @click="newSession" title="新建对话">+</button>

      <div class="session-list">
        <div v-if="sessionsLoading" class="session-empty">加载中…</div>
        <div v-else-if="sessionsError" class="session-empty error">{{ sessionsError }}</div>
        <template v-else>
          <div
            v-for="s in sessions"
            :key="s.id"
            class="session-item"
            :class="{ active: s.id === activeSessionId }"
            @click="selectSession(s)"
          >
            <span v-if="!sidebarCollapsed" class="session-title">{{ s.title }}</span>
            <span v-else class="session-title" :title="s.title">💬</span>
            <button
              v-if="!sidebarCollapsed"
              class="session-del"
              title="删除对话"
              @click.stop="deleteSession(s)"
            >🗑</button>
          </div>
          <div v-if="!sessions.length" class="session-empty">暂无对话</div>
        </template>
      </div>

      <button class="sidebar-toggle" @click="sidebarCollapsed = !sidebarCollapsed" :title="sidebarCollapsed ? '展开侧栏' : '折叠侧栏'">
        {{ sidebarCollapsed ? '»' : '«' }}
      </button>
    </aside>

    <!-- —— 聊天主区域 —— -->
    <div class="chat-main">
      <template v-if="activeSessionId">
        <p v-if="error" class="msg error">{{ error }}</p>

        <div ref="chatBox" class="chat-box">
          <div v-if="!messages.length" class="empty">
            💡 试试问：HashMap 的底层原理是什么？TCP 三次握手为什么是三次？
          </div>
          <div v-for="m in messages" :key="m.id" class="msg-line" :class="m.role">
            <span class="avatar" :class="m.role">{{ m.role === 'user' ? '我' : m.role === 'error' ? '⚠️' : '🤖' }}</span>
            <div class="bubble" :class="m.role">
              <template v-if="m.role === 'assistant' && !m.content && streaming">
                <span class="typing"><i></i><i></i><i></i></span>
              </template>
              <template v-else>
                {{ m.content }}<span v-if="streaming && m === messages[messages.length - 1] && m.role === 'assistant'" class="cursor">▋</span>
              </template>
              <div v-if="m.citations && m.citations.length" class="citations">
                <details v-for="(c, i) in m.citations" :key="i">
                  <summary>📎 {{ c.title }} · 相似度 {{ (c.similarity * 100).toFixed(0) }}%</summary>
                  <pre class="cite-body">{{ c.content }}</pre>
                </details>
              </div>
            </div>
          </div>
        </div>

        <div class="input-row">
          <textarea
            v-model="input"
            :disabled="streaming"
            rows="2"
            placeholder="向知识库提问，如：Redis 分布式锁怎么实现…"
            @keydown.enter.exact.prevent="send"
          ></textarea>
          <button class="btn btn-primary send" :disabled="streaming || !input.trim()" @click="send">
            {{ streaming ? '回答中…' : '提问' }}
          </button>
        </div>
      </template>

      <!-- 无对话空态 -->
      <div v-else class="chat-empty">
        <div class="chat-empty-icon">💬</div>
        <div class="chat-empty-title">开始新的对话吧</div>
        <div class="chat-empty-desc">点击左侧「+ 新建对话」，或直接在下方输入问题自动创建</div>
        <div class="input-row empty-input">
          <textarea
            v-model="input"
            :disabled="streaming"
            rows="2"
            placeholder="输入问题，自动创建对话…"
            @keydown.enter.exact.prevent="send"
          ></textarea>
          <button class="btn btn-primary send" :disabled="streaming || !input.trim()" @click="send">
            {{ streaming ? '回答中…' : '提问' }}
          </button>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.chat-view {
  display: flex;
  gap: 14px;
  height: calc(100vh - 130px);
  min-height: 520px;
}

/* —— 对话列表侧栏 —— */
.chat-sidebar {
  width: 230px;
  flex-shrink: 0;
  background: #fff;
  border: 1px solid var(--c-border, #e5e7eb);
  border-radius: 14px;
  display: flex;
  flex-direction: column;
  padding: 12px 10px;
  transition: width 0.2s ease;
  overflow: hidden;
}
.chat-sidebar.collapsed {
  width: 56px;
  padding: 12px 8px;
}
.new-chat-btn {
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
  transition: background 0.15s ease, transform 0.1s ease;
  flex-shrink: 0;
}
.new-chat-btn:hover {
  background: rgb(16 185 129 / 14%);
}
.new-chat-btn:active {
  transform: scale(0.98);
}
.new-chat-btn.collapsed {
  padding: 9px 0;
  font-size: 18px;
}
.new-chat-btn .plus {
  font-size: 16px;
  font-weight: 700;
}

.session-list {
  flex: 1;
  overflow-y: auto;
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.session-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  padding: 8px 10px;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.12s ease;
  min-width: 0;
}
.session-item:hover {
  background: rgb(16 185 129 / 6%);
}
.session-item.active {
  background: linear-gradient(135deg, rgb(16 185 129 / 14%), rgb(16 185 129 / 6%));
  box-shadow: inset 2px 0 0 var(--c-primary, #10b981);
}
.session-title {
  font-size: 12.5px;
  color: var(--c-text-2, #374151);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
  min-width: 0;
}
.session-item.active .session-title {
  color: var(--c-primary-dark, #059669);
  font-weight: 600;
}
.session-del {
  border: 0;
  background: transparent;
  padding: 2px 4px;
  border-radius: 4px;
  font-size: 12px;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.12s ease, background 0.12s ease;
  flex-shrink: 0;
}
.session-item:hover .session-del {
  opacity: 1;
}
.session-del:hover {
  background: rgb(239 68 68 / 10%);
}
.session-empty {
  font-size: 12px;
  color: var(--c-faint, #9ca3af);
  text-align: center;
  padding: 20px 8px;
}
.session-empty.error {
  color: #ef4444;
}

.sidebar-toggle {
  margin-top: 8px;
  padding: 6px;
  border: 0;
  background: transparent;
  border-radius: 6px;
  color: var(--c-faint, #9ca3af);
  font-size: 14px;
  font-weight: 700;
  cursor: pointer;
  transition: background 0.12s ease, color 0.12s ease;
  flex-shrink: 0;
}
.sidebar-toggle:hover {
  background: rgb(0 0 0 / 4%);
  color: var(--c-muted, #6b7280);
}

/* —— 聊天主区域 —— */
.chat-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: #fff;
  border: 1px solid var(--c-border, #e5e7eb);
  border-radius: 14px;
  padding: 16px;
}

/* 无对话空态 */
.chat-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  text-align: center;
}
.chat-empty-icon {
  font-size: 48px;
  margin-bottom: 8px;
}
.chat-empty-title {
  font-size: 18px;
  font-weight: 700;
  color: var(--c-text, #1a1b1c);
}
.chat-empty-desc {
  font-size: 13px;
  color: var(--c-faint, #9ca3af);
  margin-bottom: 20px;
}
.empty-input {
  width: 100%;
  max-width: 560px;
}

/* —— 聊天区（复用 Playground 样式） —— */
.chat-box {
  flex: 1;
  overflow-y: auto;
  background: var(--c-bg-soft, #f9fafb);
  border-radius: 12px;
  padding: 16px;
  min-height: 0;
}
.msg-line {
  display: flex;
  gap: 10px;
  margin-bottom: 14px;
  align-items: flex-start;
}
.msg-line.user {
  flex-direction: row-reverse;
}
.avatar {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 600;
  color: #fff;
}
.avatar.assistant {
  background: linear-gradient(135deg, var(--c-primary, #10b981), var(--c-primary-dark, #059669));
  box-shadow: 0 2px 8px rgb(16 185 129 / 30%);
}
.avatar.user {
  background: #e0f2fe;
  border: 1px solid #bae6fd;
  color: #0369a1;
}
.avatar.error {
  background: #fee2e2;
  color: #b91c1c;
}
.bubble {
  max-width: 78%;
  padding: 10px 14px;
  font-size: 13.5px;
  line-height: 1.75;
  white-space: pre-wrap;
  word-break: break-word;
  border-radius: 14px;
}
.bubble.assistant {
  background: #fff;
  border: 1px solid var(--c-border, #e5e7eb);
  border-top-left-radius: 4px;
  color: var(--c-text-2, #374151);
}
.bubble.user {
  background: linear-gradient(135deg, var(--c-primary, #10b981), var(--c-primary-dark, #059669));
  color: #fff;
  border-top-right-radius: 4px;
  box-shadow: 0 2px 10px rgb(16 185 129 / 25%);
}
.bubble.error {
  background: #fee2e2;
  color: #b91c1c;
}
.typing {
  display: inline-flex;
  gap: 4px;
  padding: 4px 2px;
}
.typing i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--c-primary, #10b981);
  animation: typing-blink 1.2s infinite;
}
.typing i:nth-child(2) { animation-delay: 0.2s; }
.typing i:nth-child(3) { animation-delay: 0.4s; }
.cursor {
  color: var(--c-primary, #10b981);
  animation: typing-blink 1s infinite;
}
@keyframes typing-blink {
  0%, 100% { opacity: 0.3; }
  50% { opacity: 1; }
}

/* 引用来源 */
.citations {
  margin-top: 10px;
  border-top: 1px dashed var(--c-border, #e5e7eb);
  padding-top: 8px;
}
.citations details {
  margin: 4px 0;
}
.citations summary {
  font-size: 12px;
  color: var(--c-primary-dark, #059669);
  cursor: pointer;
  user-select: none;
}
.cite-body {
  background: var(--c-bg-soft, #f9fafb);
  border-radius: 6px;
  padding: 8px 10px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--c-muted, #6b7280);
  white-space: pre-wrap;
  word-break: break-word;
  margin: 6px 0 0;
  max-height: 180px;
  overflow: auto;
}

/* 输入区 */
.input-row {
  display: flex;
  gap: 10px;
  margin-top: 12px;
}
.input-row textarea {
  flex: 1;
  resize: vertical;
  border: 1.5px solid var(--c-border, #e5e7eb);
  border-radius: 10px;
  padding: 10px 12px;
  font: inherit;
  font-size: 13.5px;
  outline: none;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.input-row textarea:focus {
  border-color: #6ee7b7;
  box-shadow: 0 0 0 3px rgb(16 185 129 / 12%);
}
.send {
  align-self: flex-end;
  padding: 10px 22px;
}

/* 窄屏：侧栏折叠为图标条 */
@media (max-width: 768px) {
  .chat-view {
    height: calc(100vh - 110px);
  }
  .chat-sidebar {
    width: 56px !important;
    padding: 12px 8px !important;
  }
  .chat-sidebar .session-title,
  .chat-sidebar .session-del {
    display: none;
  }
  .chat-sidebar .new-chat-btn {
    padding: 9px 0;
    font-size: 18px;
  }
  .chat-sidebar .new-chat-btn .plus {
    display: none;
  }
}
</style>
