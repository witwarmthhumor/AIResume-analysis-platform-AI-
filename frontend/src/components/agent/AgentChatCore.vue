<script setup>
/* AgentChatCore —— AI 客服聊天核心（v3.4）
   只负责：消息列表（含工具调用过程）、输入框、SSE 流式。
   会话侧栏/悬浮外壳由父组件（AgentChatView / AgentWidget）提供。
   父组件通过 :session-id 指定当前会话；无会话时发送会自动新建并 emit session-created。 */
import { nextTick, ref, watch } from 'vue'
import { get, post, streamChat } from '../../api.js'

const props = defineProps({
  sessionId: { type: [Number, null], default: null },
  compact: { type: Boolean, default: false } // 悬浮窗紧凑模式
})
const emit = defineEmits(['session-created', 'title-updated'])

const messages = ref([])
const input = ref('')
const streaming = ref(false)
const error = ref('')
const chatBox = ref(null)
let currentSessionId = props.sessionId

async function scrollBottom() {
  await nextTick()
  if (chatBox.value) chatBox.value.scrollTop = chatBox.value.scrollHeight
}

async function loadMessages(sid) {
  messages.value = []
  error.value = ''
  if (!sid) return
  try {
    const rows = await get(`/api/agent/sessions/${sid}/messages`)
    messages.value = rows.map((m) => ({
      id: m.id,
      role: m.role,
      content: m.content || '',
      citations: m.citations || [],
      toolSteps: m.tool_steps || [],
      loading: false,
    }))
    scrollBottom()
  } catch (e) {
    error.value = e.message || '历史消息加载失败'
  }
}

watch(
  () => props.sessionId,
  (sid) => {
    currentSessionId = sid
    loadMessages(sid)
  },
  { immediate: true }
)

async function ensureSession() {
  if (currentSessionId) return true
  try {
    const s = await post('/api/agent/sessions', {})
    currentSessionId = s.id
    emit('session-created', s)
    return true
  } catch (e) {
    error.value = e.message || '创建对话失败'
    return false
  }
}

// —— SSE 事件 ——
function handleEvent(block) {
  const event = block.match(/^event: (.+)$/m)?.[1]
  const dataLine = block.match(/^data: (.+)$/m)?.[1]
  if (!event || !dataLine) return
  const data = JSON.parse(dataLine)
  const last = messages.value[messages.value.length - 1]

  if (event === 'meta') {
    messages.value.push({ id: `a-${Date.now()}`, role: 'assistant', content: '', toolSteps: [], citations: [], loading: true })
  } else if (event === 'action') {
    if (last) last.toolSteps.push({ tool: data.tool, input: data.input, preview: '', running: true })
  } else if (event === 'reset') {
    // 工具决策轮同时吐出的文本 token 是中间过程，清空累计的回答内容
    if (last) last.content = ''
  } else if (event === 'observation') {
    if (last && last.toolSteps.length) {
      const step = last.toolSteps[last.toolSteps.length - 1]
      step.preview = data.preview
      step.running = false
    }
  } else if (event === 'delta') {
    if (last) last.content += data.content
  } else if (event === 'done') {
    if (last) {
      last.content = data.content || last.content
      last.citations = data.citations || []
      last.loading = false
    }
  } else if (event === 'error') {
    if (last && last.loading && !last.content && !last.toolSteps.length) {
      last.role = 'error'
      last.content = data.content
      last.loading = false
    } else {
      error.value = data.content
    }
  }
}

async function send() {
  const content = input.value.trim()
  if (!content || streaming.value) return
  if (!(await ensureSession())) return

  input.value = ''
  messages.value.push({ id: `u-${Date.now()}`, role: 'user', content, toolSteps: [], citations: [], loading: false })
  streaming.value = true
  error.value = ''
  try {
    const { reader } = streamChat('/api/agent/ask', { content, session_id: currentSessionId })
    const stream = await reader
    const decoder = new TextDecoder()
    let buffer = ''
    while (true) {
      const { done, value } = await stream.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let boundary
      while ((boundary = buffer.indexOf('\n\n')) >= 0) {
        const block = buffer.slice(0, boundary)
        buffer = buffer.slice(boundary + 2)
        handleEvent(block)
        scrollBottom()
      }
    }
    const last = messages.value[messages.value.length - 1]
    if (last) last.loading = false
    emit('title-updated')
  } catch (e) {
    error.value = e.message || '网络异常，请重试'
    const last = messages.value[messages.value.length - 1]
    if (last) last.loading = false
  } finally {
    streaming.value = false
    scrollBottom()
  }
}

const suggestions = ['synchronized 和 ReentrantLock 区别？', '什么是 RAG？', 'MySQL 索引为什么用 B+ 树？']
</script>

<template>
  <div class="agent-core" :class="{ compact }">
    <div ref="chatBox" class="ac-box">
      <div v-if="!messages.length" class="ac-welcome">
        <div class="ac-hi">🤖 你好，我是技术面试 AI 客服</div>
        <div class="ac-sub">我会自主检索平台知识库回答，也能看到我的检索过程</div>
        <div class="ac-suggest">
          <button v-for="s in suggestions" :key="s" class="ac-chip" @click="input = s; send()">{{ s }}</button>
        </div>
      </div>

      <div v-for="m in messages" :key="m.id" class="ac-line" :class="m.role">
        <span class="ac-avatar" :class="m.role">{{ m.role === 'user' ? '我' : m.role === 'error' ? '⚠️' : '🤖' }}</span>
        <div class="ac-bubble" :class="m.role">
          <!-- 工具调用过程（用户强调：过程可见） -->
          <div v-if="m.toolSteps && m.toolSteps.length" class="ac-tools">
            <div v-for="(step, i) in m.toolSteps" :key="i" class="ac-tool">
              <details open>
                <summary>
                  <span class="ac-tool-badge">{{ step.running ? '⏳' : '🔧' }}</span>
                  调用 {{ step.tool }}（{{ step.input }}）
                </summary>
                <div v-if="step.preview" class="ac-tool-prev">{{ step.preview }}</div>
              </details>
            </div>
          </div>

          <span v-if="m.loading && !m.content && !(m.toolSteps && m.toolSteps.length)" class="ac-typing"><i></i><i></i><i></i></span>
          <template v-else>{{ m.content }}</template>
          <span v-if="m.loading && m.content" class="ac-cursor">▋</span>

          <div v-if="m.citations && m.citations.length" class="ac-cites">
            <details v-for="(c, i) in m.citations" :key="i">
              <summary>📎 {{ c.title }} · 相似度 {{ (c.similarity * 100).toFixed(0) }}%</summary>
            </details>
          </div>
        </div>
      </div>
    </div>

    <p v-if="error" class="ac-error">{{ error }}</p>

    <div class="ac-input-row">
      <textarea
        v-model="input"
        :disabled="streaming"
        :rows="compact ? 1 : 2"
        placeholder="请输入你的问题…（Enter 发送，Shift+Enter 换行）"
        @keydown.enter.exact.prevent="send"
      ></textarea>
      <button class="ac-send" :disabled="streaming || !input.trim()" @click="send">
        {{ streaming ? '…' : '➤' }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.agent-core {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.ac-box {
  flex: 1;
  overflow-y: auto;
  background: var(--c-bg-soft, #f9fafb);
  border-radius: 12px;
  padding: 14px;
  min-height: 0;
}
.ac-welcome {
  text-align: center;
  padding: 24px 12px;
}
.ac-hi {
  font-size: 15px;
  font-weight: 700;
  color: var(--c-text, #1a1b1c);
}
.ac-sub {
  font-size: 12.5px;
  color: var(--c-faint, #9ca3af);
  margin-top: 6px;
}
.ac-suggest {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: center;
  margin-top: 16px;
}
.ac-chip {
  border: 1px solid rgb(16 185 129 / 35%);
  background: #fff;
  color: var(--c-primary-dark, #059669);
  border-radius: 999px;
  padding: 6px 12px;
  font-size: 12px;
  font-family: inherit;
  cursor: pointer;
  transition: background 0.12s ease;
}
.ac-chip:hover {
  background: rgb(16 185 129 / 8%);
}

.ac-line {
  display: flex;
  gap: 9px;
  margin-bottom: 13px;
  align-items: flex-start;
}
.ac-line.user {
  flex-direction: row-reverse;
}
.ac-avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 600;
  color: #fff;
}
.ac-avatar.assistant {
  background: linear-gradient(135deg, #10b981, #059669);
  box-shadow: 0 2px 8px rgb(16 185 129 / 30%);
}
.ac-avatar.user {
  background: #e0f2fe;
  border: 1px solid #bae6fd;
  color: #0369a1;
}
.ac-avatar.error {
  background: #fee2e2;
}
.ac-bubble {
  max-width: 80%;
  padding: 9px 13px;
  font-size: 13.5px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
  border-radius: 14px;
}
.ac-bubble.assistant {
  background: #fff;
  border: 1px solid var(--c-border, #e5e7eb);
  border-top-left-radius: 4px;
  color: var(--c-text-2, #374151);
}
.ac-bubble.user {
  background: linear-gradient(135deg, #10b981, #059669);
  color: #fff;
  border-top-right-radius: 4px;
}
.ac-bubble.error {
  background: #fee2e2;
  color: #b91c1c;
}
.compact .ac-bubble {
  font-size: 12.5px;
}

/* 工具调用过程 */
.ac-tools {
  margin-bottom: 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.ac-tool details {
  background: rgb(16 185 129 / 6%);
  border: 1px solid rgb(16 185 129 / 18%);
  border-radius: 8px;
  padding: 6px 9px;
}
.ac-tool summary {
  font-size: 11.5px;
  color: var(--c-primary-dark, #059669);
  cursor: pointer;
  user-select: none;
  list-style: none;
}
.ac-tool summary::-webkit-details-marker {
  display: none;
}
.ac-tool-badge {
  margin-right: 4px;
}
.ac-tool-prev {
  margin-top: 6px;
  font-size: 11px;
  line-height: 1.6;
  color: var(--c-muted, #6b7280);
  background: #fff;
  border-radius: 6px;
  padding: 6px 8px;
  max-height: 120px;
  overflow: auto;
  white-space: pre-wrap;
}
.ac-cites {
  margin-top: 8px;
  border-top: 1px dashed var(--c-border, #e5e7eb);
  padding-top: 6px;
}
.ac-cites summary {
  font-size: 11.5px;
  color: var(--c-primary-dark, #059669);
  cursor: pointer;
}
.ac-typing {
  display: inline-flex;
  gap: 4px;
}
.ac-typing i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #10b981;
  animation: ac-blink 1.2s infinite;
}
.ac-typing i:nth-child(2) {
  animation-delay: 0.2s;
}
.ac-typing i:nth-child(3) {
  animation-delay: 0.4s;
}
.ac-cursor {
  color: #10b981;
  animation: ac-blink 1s infinite;
}
@keyframes ac-blink {
  0%, 100% { opacity: 0.3; }
  50% { opacity: 1; }
}

.ac-error {
  color: #b91c1c;
  font-size: 12px;
  margin: 0;
}
.ac-input-row {
  display: flex;
  gap: 8px;
  align-items: flex-end;
}
.ac-input-row textarea {
  flex: 1;
  resize: none;
  border: 1.5px solid var(--c-border, #e5e7eb);
  border-radius: 10px;
  padding: 9px 11px;
  font: inherit;
  font-size: 13px;
  outline: none;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
  font-family: inherit;
}
.ac-input-row textarea:focus {
  border-color: #6ee7b7;
  box-shadow: 0 0 0 3px rgb(16 185 129 / 12%);
}
.ac-send {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  border: 0;
  flex-shrink: 0;
  background: linear-gradient(135deg, #10b981, #059669);
  color: #fff;
  font-size: 16px;
  cursor: pointer;
  box-shadow: 0 2px 8px rgb(16 185 129 / 30%);
  transition: transform 0.1s ease, opacity 0.12s ease;
}
.ac-send:hover:not(:disabled) {
  transform: scale(1.06);
}
.ac-send:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
