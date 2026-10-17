<script setup>
/* AgentChatCore —— AI 客服聊天核心（v3.4）
   只负责：消息列表（含工具调用过程）、输入框、SSE 流式。
   会话侧栏/悬浮外壳由父组件（AgentChatView / AgentWidget）提供。
   父组件通过 :session-id 指定当前会话；无会话时发送会自动新建并 emit session-created。 */
import { nextTick, onBeforeUnmount, onDeactivated, ref, watch } from 'vue'
import { get, parseSseBlock, post, streamChat } from '../../api.js'
import { loadToolLabels, toolLabel } from '../../toolLabels.js'
import Icon from '../Icon.vue'

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
let currentSessionId = null // 由下方 immediate watch 完成初始同步与首次加载
let activeAbort = null // 当前流的 abort 句柄：切会话/组件卸载时中止后台空转的流
let loadSeq = 0 // 历史消息加载序号守卫：快速连点会话时"先发慢回"的旧响应不得覆盖新状态

// v4.3 单一数据源：工具中文名从后端 registry（GET /api/agent/tools）拉取，
// 未加载完成或接口失败时回退英文原名，新增工具不会显示为空
loadToolLabels()

async function scrollBottom() {
  await nextTick()
  if (chatBox.value) chatBox.value.scrollTop = chatBox.value.scrollHeight
}

async function loadMessages(sid) {
  const seq = ++loadSeq // 只有最新一次加载允许写状态，竞态旧响应直接丢弃
  messages.value = []
  error.value = ''
  if (!sid) return
  try {
    const rows = await get(`/api/agent/sessions/${sid}/messages`)
    if (seq !== loadSeq) return
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
    if (seq !== loadSeq) return
    error.value = e.message || '历史消息加载失败'
  }
}

watch(
  () => props.sessionId,
  (sid) => {
    // 回声守卫：ensureSession 刚创建的会话会经父组件把 sessionId 回传到这里，
    // 此时本地已 push 用户消息、服务端尚未持久化——重拉历史会把用户气泡顶掉
    if (sid === currentSessionId) return
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
  const parsed = parseSseBlock(block)
  if (!parsed || !parsed.data) return // 无 data 或畸形块：跳过，不中断整条流
  const { event, data } = parsed
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
    if (last) last.content += data.content || '' // 兜底空 delta，防止拼出字面 "undefined"
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

  const sentSessionId = currentSessionId // 流的归属会话快照：中途切换会话即中止旧流
  input.value = ''
  messages.value.push({ id: `u-${Date.now()}`, role: 'user', content, toolSteps: [], citations: [], loading: false })
  streaming.value = true
  error.value = ''
  try {
    const { reader, abort } = streamChat('/api/agent/ask', { content, session_id: sentSessionId })
    activeAbort = abort
    const stream = await reader
    const decoder = new TextDecoder()
    let buffer = ''
    while (true) {
      if (currentSessionId !== sentSessionId) { activeAbort?.(); break } // 已切走：旧流立即中止
      const { done, value } = await stream.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let boundary
      while ((boundary = buffer.indexOf('\n\n')) >= 0) {
        const block = buffer.slice(0, boundary)
        buffer = buffer.slice(boundary + 2)
        if (currentSessionId !== sentSessionId) break // 事件也只写入归属会话
        handleEvent(block)
        scrollBottom()
      }
    }
    const last = messages.value[messages.value.length - 1]
    if (last) last.loading = false
    emit('title-updated')
  } catch (e) {
    if (e?.name !== 'AbortError') error.value = e.message || '网络异常，请重试'
    const last = messages.value[messages.value.length - 1]
    if (last) last.loading = false
  } finally {
    activeAbort = null
    streaming.value = false
    scrollBottom()
  }
}

// 组件卸载（悬浮窗关闭）或 KeepAlive 失活（切走视图）时中止后台流，不再空转写废弃状态
onBeforeUnmount(() => activeAbort?.())
onDeactivated(() => activeAbort?.())

const suggestions = ['synchronized 和 ReentrantLock 区别？', '什么是 RAG？', 'MySQL 索引为什么用 B+ 树？']
</script>

<template>
  <div class="agent-core" :class="{ compact }">
    <div ref="chatBox" class="ac-box">
      <div v-if="!messages.length" class="ac-welcome">
        <div class="ac-hi"><Icon name="bot" :size="18" />你好，我是技术面试 AI 客服</div>
        <div class="ac-sub">我会自主检索平台知识库回答，也能看到我的检索过程</div>
        <div class="ac-suggest">
          <button v-for="s in suggestions" :key="s" class="ac-chip" @click="input = s; send()">{{ s }}</button>
        </div>
      </div>

      <div v-for="m in messages" :key="m.id" class="ac-line" :class="m.role">
        <span class="ac-avatar" :class="m.role">
          <template v-if="m.role === 'user'">我</template>
          <Icon v-else-if="m.role === 'error'" name="alert" :size="16" />
          <Icon v-else name="bot" :size="16" />
        </span>
        <div class="bubble ac-bubble" :class="{ 'bubble-ai': m.role === 'assistant', 'bubble-me': m.role === 'user', error: m.role === 'error' }">
          <!-- 工具调用过程（用户强调：过程可见） -->
          <div v-if="m.toolSteps && m.toolSteps.length" class="ac-tools">
            <div v-for="(step, i) in m.toolSteps" :key="i" class="ac-tool">
              <details open>
                <summary>
                  <span class="ac-tool-badge">
                    <span v-if="step.running" class="spinner ac-spin"></span>
                    <Icon v-else name="check" :size="12" class="ac-tool-check" />
                  </span>
                  调用 {{ toolLabel(step.tool) }}（{{ step.input }}）
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
              <summary><Icon name="paperclip" :size="12" /> {{ c.title }} · 相似度 {{ (c.similarity * 100).toFixed(0) }}%</summary>
            </details>
          </div>
        </div>
      </div>
    </div>

    <p v-if="error" class="msg error ac-error">{{ error }}</p>

    <div class="ac-input-row">
      <textarea
        v-model="input"
        class="input"
        :disabled="streaming"
        :rows="compact ? 1 : 2"
        placeholder="请输入你的问题…（Enter 发送，Shift+Enter 换行）"
        @keydown.enter.exact.prevent="send"
      ></textarea>
      <button
        class="ac-send"
        title="发送"
        aria-label="发送"
        :disabled="streaming || !input.trim()"
        @click="send"
      >
        <span v-if="streaming" class="spinner"></span>
        <Icon v-else name="send" :size="16" />
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
  gap: var(--sp-2);
}
.ac-box {
  flex: 1;
  overflow-y: auto;
  background: var(--c-bg-soft);
  border-radius: var(--radius-md);
  padding: var(--sp-3);
  min-height: 0;
}
.ac-welcome {
  text-align: center;
  padding: var(--sp-5) var(--sp-3);
}
.ac-hi {
  font-size: var(--fs-lg);
  font-weight: 600;
  color: var(--c-text);
  display: inline-flex;
  align-items: center;
  gap: var(--sp-2);
}
.ac-sub {
  font-size: var(--fs-sm);
  color: var(--c-faint);
  margin-top: 6px;
}
.ac-suggest {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-2);
  justify-content: center;
  margin-top: var(--sp-4);
}
.ac-chip {
  border: 1px solid var(--c-primary-border);
  background: #fff;
  color: var(--c-primary-dark);
  border-radius: var(--radius-full);
  padding: 6px var(--sp-3);
  font-size: var(--fs-xs);
  font-family: inherit;
  cursor: pointer;
  transition: background var(--dur-fast) var(--ease);
}
.ac-chip:hover {
  background: var(--wash-primary);
}

.ac-line {
  display: flex;
  gap: var(--sp-2);
  margin-bottom: var(--sp-3);
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
  font-size: var(--fs-xs);
  font-weight: 600;
  color: #fff;
}
.ac-avatar.assistant {
  background: var(--c-primary);
}
.ac-avatar.user {
  background: var(--c-info-bg);
  border: 1px solid var(--c-info-bg);
  color: var(--c-info);
}
.ac-avatar.error {
  background: var(--c-bad-bg);
  color: var(--c-bad);
}
/* 气泡皮肤走全局 .bubble/.bubble-ai/.bubble-me，这里只补宽度、流式排版与错误态 */
.ac-bubble {
  max-width: 80%;
  white-space: pre-wrap;
}
.ac-bubble.error {
  background: var(--c-bad-bg);
  color: var(--c-bad);
}
.compact .ac-bubble {
  font-size: var(--fs-xs);
}

/* 工具调用过程 */
.ac-tools {
  margin-bottom: var(--sp-2);
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.ac-tool details {
  background: var(--wash-primary);
  border: 1px solid var(--c-primary-border);
  border-radius: var(--radius-sm);
  padding: 6px var(--sp-2);
}
.ac-tool summary {
  font-size: var(--fs-xs);
  color: var(--c-primary-dark);
  cursor: pointer;
  user-select: none;
  list-style: none;
}
.ac-tool summary::-webkit-details-marker {
  display: none;
}
.ac-tool-badge {
  margin-right: var(--sp-1);
  display: inline-flex;
  align-items: center;
}
/* 运行中=小号 spinner，结束=对勾 */
.ac-spin {
  width: 12px;
  height: 12px;
}
.ac-tool-check {
  color: var(--c-ok);
}
.ac-tool-prev {
  margin-top: 6px;
  font-size: var(--fs-xs);
  line-height: 1.6;
  color: var(--c-muted);
  background: #fff;
  border-radius: var(--radius-sm);
  padding: 6px var(--sp-2);
  max-height: 120px;
  overflow: auto;
  white-space: pre-wrap;
}
.ac-cites {
  margin-top: var(--sp-2);
  border-top: 1px dashed var(--c-border);
  padding-top: 6px;
}
.ac-cites summary {
  font-size: var(--fs-xs);
  color: var(--c-primary-dark);
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
  background: var(--c-primary);
  animation: ac-blink 1.2s infinite;
}
.ac-typing i:nth-child(2) {
  animation-delay: 0.2s;
}
.ac-typing i:nth-child(3) {
  animation-delay: 0.4s;
}
.ac-cursor {
  color: var(--c-primary);
  animation: ac-blink 1s infinite;
}
@keyframes ac-blink {
  0%, 100% { opacity: 0.3; }
  50% { opacity: 1; }
}

.ac-error {
  margin: 0;
}
.ac-input-row {
  display: flex;
  gap: var(--sp-2);
  align-items: flex-end;
}
/* 边框/聚焦态走全局 .input；紧凑模式单行输入，禁止拖拽改高 */
.ac-input-row textarea {
  flex: 1;
  resize: none;
}
.ac-send {
  width: 40px;
  height: 40px;
  border: 0;
  border-radius: var(--radius-full);
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: var(--c-primary);
  color: #fff;
  cursor: pointer;
  transition: background var(--dur-fast) var(--ease), opacity var(--dur-fast) var(--ease);
}
.ac-send:hover:not(:disabled) {
  background: var(--c-primary-dark);
}
.ac-send:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
/* 绿底上的 spinner 翻成白环，避免同色系不可见 */
.ac-send .spinner {
  border-color: rgb(255 255 255 / 35%);
  border-top-color: #fff;
}
</style>
