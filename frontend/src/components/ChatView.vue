<script setup>
import { nextTick, onBeforeUnmount, onDeactivated, onMounted, ref } from 'vue'
import { del, get, parseSseBlock, post, streamChat } from '../api.js'
import Icon from './Icon.vue'

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
let activeAbort = null // 当前流的 abort 句柄：切会话/组件失活时中止后台流
let loadSeq = 0 // 历史消息加载序号守卫：快速连点会话时旧响应不得覆盖新状态

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
  if (streaming.value) activeAbort?.() // 切走即中止旧流：事件只属于归属会话，不再串台
  const seq = ++loadSeq
  activeSessionId.value = s.id
  messages.value = []
  error.value = ''
  try {
    const rows = await get(`/api/chat/sessions/${s.id}/messages`)
    if (seq !== loadSeq) return // 竞态旧响应：丢弃
    messages.value = rows
  } catch (e) {
    if (seq !== loadSeq) return
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
  const parsed = parseSseBlock(block)
  if (!parsed || !parsed.data) return // 畸形块：跳过，不中断整条流
  const { event, data } = parsed
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
  const sentSessionId = activeSessionId.value // 流的归属会话快照：中途切会话即中止旧流
  messages.value.push({ id: `user-${Date.now()}`, role: 'user', content })
  streaming.value = true
  error.value = ''
  try {
    const { reader, abort } = streamChat('/api/playground/ask', { content, session_id: sentSessionId })
    activeAbort = abort
    const stream = await reader
    const decoder = new TextDecoder()
    let buffer = ''
    const last = { value: null }
    while (true) {
      if (activeSessionId.value !== sentSessionId) { activeAbort?.(); break } // 已切走：旧流立即中止
      const { done, value } = await stream.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let boundary
      while ((boundary = buffer.indexOf('\n\n')) >= 0) {
        const block = buffer.slice(0, boundary)
        buffer = buffer.slice(boundary + 2)
        if (activeSessionId.value !== sentSessionId) break // 事件只写入归属会话
        handleEvent(block, last)
        last.value = messages.value[messages.value.length - 1]
        await nextTick()
        if (chatBox.value) chatBox.value.scrollTop = chatBox.value.scrollHeight
      }
    }
    // 发送完成后刷新会话列表（标题可能已更新）
    loadSessions()
  } catch (e) {
    if (e?.name !== 'AbortError') error.value = e.message || '网络异常，请重试'
  } finally {
    activeAbort = null
    streaming.value = false
  }
}

onMounted(loadSessions)
onBeforeUnmount(() => activeAbort?.())
onDeactivated(() => activeAbort?.())
</script>

<template>
  <section class="chat-view">
    <!-- —— 对话列表侧栏 —— -->
    <aside class="chat-sidebar" :class="{ collapsed: sidebarCollapsed }">
      <button v-if="!sidebarCollapsed" class="btn btn-primary new-chat-btn" @click="newSession">
        <Icon name="plus" :size="14" /><span class="ncb-label">新建对话</span>
      </button>
      <button v-else class="btn btn-primary new-chat-btn collapsed" title="新建对话" aria-label="新建对话" @click="newSession">
        <Icon name="plus" :size="16" />
      </button>

      <div class="session-list">
        <div v-if="sessionsLoading" class="loading-row"><span class="spinner"></span>加载中…</div>
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
            <span v-else class="session-title" :title="s.title"><Icon name="chat" :size="14" /></span>
            <button
              v-if="!sidebarCollapsed"
              class="icon-btn danger"
              aria-label="删除"
              title="删除对话"
              @click.stop="deleteSession(s)"
            >
              <Icon name="trash" :size="14" />
            </button>
          </div>
          <div v-if="!sessions.length" class="empty">暂无对话</div>
        </template>
      </div>

      <button
        class="sidebar-toggle"
        :title="sidebarCollapsed ? '展开侧栏' : '折叠侧栏'"
        :aria-label="sidebarCollapsed ? '展开侧栏' : '折叠侧栏'"
        @click="sidebarCollapsed = !sidebarCollapsed"
      >
        <Icon :name="sidebarCollapsed ? 'chevrons-right' : 'chevrons-left'" :size="14" />
      </button>
    </aside>

    <!-- —— 聊天主区域 —— -->
    <div class="chat-main">
      <template v-if="activeSessionId">
        <p v-if="error" class="msg error">{{ error }}</p>

        <div ref="chatBox" class="chat-box">
          <div v-if="!messages.length" class="empty-state">
            <div class="es-icon"><Icon name="spark" :size="24" /></div>
            <p class="es-text">试试问：HashMap 的底层原理是什么？TCP 三次握手为什么是三次？</p>
          </div>
          <div v-for="m in messages" :key="m.id" class="msg-line" :class="m.role">
            <span class="avatar" :class="m.role">
              <template v-if="m.role === 'user'">我</template>
              <Icon v-else-if="m.role === 'error'" name="alert" :size="16" />
              <Icon v-else name="bot" :size="16" />
            </span>
            <div class="bubble" :class="{ 'bubble-ai': m.role === 'assistant', 'bubble-me': m.role === 'user', error: m.role === 'error' }">
              <template v-if="m.role === 'assistant' && !m.content && streaming">
                <span class="typing"><i></i><i></i><i></i></span>
              </template>
              <template v-else>
                {{ m.content }}<span v-if="streaming && m === messages[messages.length - 1] && m.role === 'assistant'" class="cursor">▋</span>
              </template>
              <div v-if="m.citations && m.citations.length" class="citations">
                <details v-for="(c, i) in m.citations" :key="i">
                  <summary><Icon name="paperclip" :size="12" /> {{ c.title }} · 相似度 {{ (c.similarity * 100).toFixed(0) }}%</summary>
                  <pre class="cite-body">{{ c.content }}</pre>
                </details>
              </div>
            </div>
          </div>
        </div>
</template>

      <!-- 无对话空态 -->
      <div v-else class="chat-empty empty-state">
        <div class="es-icon"><Icon name="chat" :size="24" /></div>
        <div class="chat-empty-title">开始新的对话吧</div>
        <div class="chat-empty-desc">点击左侧「新建对话」，或直接在下方输入问题自动创建</div>
      </div>

      <!-- 输入区两态共用一份（仅 placeholder 不同）：有会话直接提问，无会话输入即自动创建 -->
      <div class="input-row" :class="{ 'empty-input': !activeSessionId }">
        <textarea
          v-model="input"
          class="input"
          :disabled="streaming"
          rows="2"
          :placeholder="activeSessionId ? '向知识库提问，如：Redis 分布式锁怎么实现…' : '输入问题，自动创建对话…'"
          @keydown.enter.exact.prevent="send"
        ></textarea>
        <button class="btn btn-primary send" :disabled="streaming || !input.trim()" @click="send">
          {{ streaming ? '回答中…' : '提问' }}
        </button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.chat-view {
  display: flex;
  gap: var(--sp-3);
  height: calc(100vh - 130px);
  min-height: 520px;
}

/* —— 对话列表侧栏 —— */
.chat-sidebar {
  width: 230px;
  flex-shrink: 0;
  background: #fff;
  border: 1px solid var(--c-border);
  border-radius: var(--radius-lg);
  display: flex;
  flex-direction: column;
  padding: var(--sp-3) var(--sp-2);
  transition: width var(--dur) var(--ease);
  overflow: hidden;
}
.chat-sidebar.collapsed {
  width: 56px;
  padding: var(--sp-3) var(--sp-2);
}
/* 主操作按钮走全局 .btn/.btn-primary（实色），这里只补满宽与折叠态 */
.new-chat-btn {
  width: 100%;
  flex-shrink: 0;
}
.new-chat-btn.collapsed {
  padding: var(--sp-2) 0;
}

.session-list {
  flex: 1;
  overflow-y: auto;
  margin-top: var(--sp-2);
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.session-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  padding: var(--sp-2);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: background var(--dur-fast) var(--ease);
  min-width: 0;
}
.session-item:hover {
  background: var(--c-hover);
}
.session-item.active {
  background: var(--wash-primary);
  box-shadow: inset 2px 0 0 var(--c-primary);
}
.session-title {
  font-size: var(--fs-sm);
  color: var(--c-text-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
  min-width: 0;
}
.session-item.active .session-title {
  color: var(--c-primary-dark);
  font-weight: 600;
}
/* 删除按钮走全局 .icon-btn.danger（常显，hover 才变红） */
.session-empty {
  font-size: var(--fs-xs);
  color: var(--c-faint);
  text-align: center;
  padding: var(--sp-5) var(--sp-2);
}
.session-empty.error {
  color: var(--c-bad);
}

.sidebar-toggle {
  margin-top: var(--sp-2);
  padding: 6px;
  border: 0;
  background: transparent;
  border-radius: var(--radius-sm);
  color: var(--c-faint);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background var(--dur-fast) var(--ease), color var(--dur-fast) var(--ease);
  flex-shrink: 0;
}
.sidebar-toggle:hover {
  background: var(--c-hover);
  color: var(--c-muted);
}

/* —— 聊天主区域 —— */
.chat-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: #fff;
  border: 1px solid var(--c-border);
  border-radius: var(--radius-lg);
  padding: var(--sp-4);
}

/* 无对话空态（骨架复用全局 .empty-state，这里补满高居中） */
.chat-empty {
  flex: 1;
  justify-content: center;
}
.chat-empty-title {
  font-size: var(--fs-lg);
  font-weight: 600;
  color: var(--c-text);
}
.chat-empty-desc {
  font-size: var(--fs-sm);
  color: var(--c-faint);
}
.empty-input {
  width: 100%;
  max-width: 560px;
}

/* —— 聊天区（复用 Playground 样式） —— */
.chat-box {
  flex: 1;
  overflow-y: auto;
  background: var(--c-bg-soft);
  border-radius: var(--radius-md);
  padding: var(--sp-4);
  min-height: 0;
}
.msg-line {
  display: flex;
  gap: var(--sp-2);
  margin-bottom: var(--sp-3);
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
  font-size: var(--fs-xs);
  font-weight: 600;
  color: #fff;
}
.avatar.assistant {
  background: var(--c-primary);
}
.avatar.user {
  background: var(--c-info-bg);
  border: 1px solid var(--c-info-bg);
  color: var(--c-info);
}
.avatar.error {
  background: var(--c-bad-bg);
  color: var(--c-bad);
}
/* 气泡皮肤走全局 .bubble/.bubble-ai/.bubble-me，这里只补流式排版与错误态 */
.bubble {
  white-space: pre-wrap;
}
.bubble.error {
  background: var(--c-bad-bg);
  color: var(--c-bad);
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
  background: var(--c-primary);
  animation: typing-blink 1.2s infinite;
}
.typing i:nth-child(2) { animation-delay: 0.2s; }
.typing i:nth-child(3) { animation-delay: 0.4s; }
.cursor {
  color: var(--c-primary);
  animation: typing-blink 1s infinite;
}
@keyframes typing-blink {
  0%, 100% { opacity: 0.3; }
  50% { opacity: 1; }
}

/* 引用来源 */
.citations {
  margin-top: var(--sp-2);
  border-top: 1px dashed var(--c-border);
  padding-top: var(--sp-2);
}
.citations details {
  margin: var(--sp-1) 0;
}
.citations summary {
  font-size: var(--fs-xs);
  color: var(--c-primary-dark);
  cursor: pointer;
  user-select: none;
}
.cite-body {
  background: var(--c-bg-soft);
  border-radius: var(--radius-sm);
  padding: var(--sp-2);
  font-size: var(--fs-xs);
  line-height: 1.7;
  color: var(--c-muted);
  white-space: pre-wrap;
  word-break: break-word;
  margin: 6px 0 0;
  max-height: 180px;
  overflow: auto;
}

/* 输入区（输入框走全局 .input，这里只留布局） */
.input-row {
  display: flex;
  gap: var(--sp-2);
  margin-top: var(--sp-3);
}
.input-row textarea {
  flex: 1;
}
.send {
  align-self: flex-end;
  padding: var(--sp-2) var(--sp-5);
}

/* 窄屏：侧栏折叠为图标条（同 specificity 下媒体块在后即生效，无需 !important） */
@media (max-width: 768px) {
  .chat-view {
    height: calc(100vh - 110px);
  }
  .chat-sidebar,
  .chat-sidebar.collapsed {
    width: 56px;
    padding: var(--sp-3) var(--sp-2);
  }
  .chat-sidebar .session-title,
  .chat-sidebar .icon-btn {
    display: none;
  }
  .chat-sidebar .new-chat-btn {
    padding: var(--sp-2) 0;
  }
  .chat-sidebar .ncb-label {
    display: none;
  }
}
</style>
