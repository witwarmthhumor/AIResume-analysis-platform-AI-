<script setup>
import { nextTick, onBeforeUnmount, onDeactivated, onMounted, ref, watch } from 'vue'
import { parseSseBlock, post, streamChat } from '../api.js'
import InterviewReport from './InterviewReport.vue'

const props = defineProps({ resume: { type: Object, required: true } })
const emit = defineEmits(['close'])
const session = ref(null)
const messages = ref([])
const input = ref('')
const loading = ref(true)
const streaming = ref(false)
const finishing = ref(false)
const error = ref('')
const chatBox = ref(null)
const positionType = ref('')  // '' / intern / fresh / senior
let activeAbort = null // 流的 abort 句柄：组件卸载/失活（关闭面试/切视图）时中止后台流
let streamMsgId = null // 当前流式回答的消息 id：error 时按 id 定位删除，避免误删其他消息

const STAGES = { intro: '自我介绍', technical: '技术问答', deep_dive: '深入追问', wrapup: '收尾' }
const POSITIONS = [
  { value: '', label: '通用（建议）' },
  { value: 'intern', label: '实习生' },
  { value: 'fresh', label: '初级/应届' },
  { value: 'senior', label: '高级/资深' },
]

async function startOrResume() {
  loading.value = true
  error.value = ''
  try {
    const body = await post(`/api/resumes/${props.resume.id}/interviews`, {
      position_type: positionType.value || null,
    })
    session.value = body.session
    messages.value = [...body.session.messages]
  } catch (e) {
    error.value = e.message || '网络异常，请稍后重试'
  } finally {
    loading.value = false
  }
}

function handleEvent(block) {
  const parsed = parseSseBlock(block)
  if (!parsed || !parsed.data) return // 畸形块：跳过，不中断整条流
  const { event, data } = parsed
  if (event === 'meta') {
    streamMsgId = `stream-${Date.now()}`
    messages.value.push({ id: streamMsgId, role: 'interviewer', content: '' })
  } else if (event === 'delta') {
    const last = messages.value[messages.value.length - 1]
    if (last) last.content += data.content || ''
  } else if (event === 'done') {
    session.value.turn_count = data.turn
    session.value.stage = data.stage
  } else if (event === 'error') {
    error.value = data.content
    // 只移除"当前这条还没写出内容"的流式消息；已流出部分内容或错位时保留，避免误删
    const idx = messages.value.findIndex((m) => m.id === streamMsgId)
    if (idx >= 0 && !messages.value[idx].content) messages.value.splice(idx, 1)
  }
}

async function send() {
  const content = input.value.trim()
  if (!content || streaming.value || !session.value) return
  input.value = ''
  messages.value.push({ id: `candidate-${Date.now()}`, role: 'candidate', content })
  streaming.value = true
  error.value = ''
  try {
    const { reader, abort } = streamChat(`/api/interviews/${session.value.id}/messages`, { content })
    activeAbort = abort
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
        await nextTick()
        if (chatBox.value) chatBox.value.scrollTop = chatBox.value.scrollHeight
      }
    }
  } catch (e) {
    if (e?.name !== 'AbortError') error.value = e.message || '网络异常，请重试'
  } finally {
    activeAbort = null
    streaming.value = false
  }
}

async function finish() {
  if (finishing.value || streaming.value || !session.value) return
  finishing.value = true
  error.value = ''
  try {
    session.value = await post(`/api/interviews/${session.value.id}/finish`)
  } catch (e) {
    error.value = e.message || '网络异常，请稍后重试'
  } finally {
    finishing.value = false
  }
}

function isStreamingNow(message) {
  return streaming.value && message === messages.value[messages.value.length - 1]
}

// 只 watch 长度：流式期间每个 token 的内容增长由 send 循环内滚动，deep watch 会每 token 全量遍历
watch(() => messages.value.length, async () => { await nextTick(); if (chatBox.value) chatBox.value.scrollTop = chatBox.value.scrollHeight })
onMounted(startOrResume)
onBeforeUnmount(() => activeAbort?.())
onDeactivated(() => activeAbort?.())
</script>

<template>
  <section class="card interview">
    <div class="head">
      <div>
        <h2>文字模拟面试</h2>
        <p v-if="session" class="progress">
          <span class="stage-pill">{{ STAGES[session.stage] || session.stage }}</span>
          <span class="turn">已完成 {{ session.turn_count }}/{{ session.max_turns }} 轮</span>
        </p>
      </div>
      <button class="btn btn-ghost" @click="emit('close')">返回简历</button>
    </div>
    <p v-if="error" class="msg error">
      {{ error }}
      <button v-if="!session && !loading" class="btn btn-ghost" @click="startOrResume">重试</button>
    </p>
    <div v-if="loading" class="setup">
      <p class="loading">正在恢复面试会话…</p>
    </div>
    <template v-else-if="session?.status === 'finished'">
      <InterviewReport :report="session.final_report" :session-id="session.id || null" />
    </template>
    <template v-else-if="session && session.turn_count === 0 && !messages.length">
      <!-- P5 智能出题：开始前选择岗位类型 -->
      <p class="setup-hint">选择面试难度方向（模拟题将按类型调整）</p>
      <div class="pos-grid">
        <button
          v-for="opt in POSITIONS"
          :key="opt.value"
          class="pos-btn"
          :class="{ on: positionType === opt.value }"
          @click="positionType = opt.value; startOrResume()"
        >
          {{ opt.label }}
        </button>
      </div>
    </template>
    <template v-else-if="session">
      <div ref="chatBox" class="chat-box">
        <div
          v-for="message in messages"
          :key="message.id"
          class="msg-line"
          :class="message.role"
        >
          <span class="avatar" :class="message.role">
            {{ message.role === 'candidate' ? '我' : '🤖' }}
          </span>
          <div class="bubble" :class="message.role">
            <template v-if="isStreamingNow(message) && !message.content">
              <span class="typing"><i></i><i></i><i></i></span>
            </template>
            <template v-else>
              {{ message.content }}<span v-if="isStreamingNow(message)" class="cursor">▋</span>
            </template>
          </div>
        </div>
      </div>
      <div class="input-row">
        <textarea
          v-model="input"
          :disabled="streaming"
          rows="2"
          placeholder="输入你的回答…"
          @keydown.enter.exact.prevent="send"
        ></textarea>
        <button class="btn btn-primary send" :disabled="streaming || !input.trim()" @click="send">
          {{ streaming ? '回答中…' : '发送' }}
        </button>
      </div>
      <button
        class="btn finish"
        :disabled="finishing || streaming || session.turn_count < 1"
        @click="finish"
      >
        {{ finishing ? '生成评价中…' : '🏁 结束面试并查看评价' }}
      </button>
    </template>
  </section>
</template>

<style scoped>
.head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
}
.head h2 {
  font-size: 16px;
}
.progress {
  margin: 4px 0 14px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.stage-pill {
  background: var(--c-primary-light);
  color: var(--c-primary-dark);
  border-radius: 999px;
  padding: 3px 12px;
  font-size: 12px;
  font-weight: 600;
}
.turn {
  color: var(--c-faint);
  font-size: 12.5px;
}
.loading {
  color: var(--c-muted);
  font-size: 13px;
  margin: 0;
}
.setup-hint {
  color: var(--c-muted);
  font-size: 13px;
  margin: 0 0 12px;
}
.pos-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
}
.pos-btn {
  border: 1.5px solid var(--c-border);
  border-radius: var(--radius-md);
  background: #fff;
  color: var(--c-muted);
  padding: 12px 8px;
  font-size: 13px;
  cursor: pointer;
  transition: all 0.18s ease;
}
.pos-btn:hover {
  border-color: var(--c-primary-border);
  color: var(--c-primary-dark);
}
.pos-btn.on {
  border-color: var(--c-primary);
  background: var(--c-primary-light);
  color: var(--c-primary-dark);
  font-weight: 600;
}
@media (max-width: 520px) {
  .pos-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}

/* —— 聊天气泡区 —— */
.chat-box {
  height: 380px;
  overflow-y: auto;
  background: var(--c-bg-soft);
  border-radius: 12px;
  padding: 16px;
}
.msg-line {
  display: flex;
  gap: 10px;
  margin-bottom: 14px;
  align-items: flex-start;
}
.msg-line.candidate {
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
.avatar.interviewer {
  background: linear-gradient(135deg, var(--c-primary), var(--c-primary-dark));
  box-shadow: 0 2px 8px rgb(16 185 129 / 30%);
}
.avatar.candidate {
  background: #e0f2fe;
  border: 1px solid #bae6fd;
  color: #0369a1;
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
.bubble.interviewer {
  background: #fff;
  border: 1px solid var(--c-border);
  border-top-left-radius: 4px;
  color: var(--c-text-2);
}
.bubble.candidate {
  background: linear-gradient(135deg, var(--c-primary), var(--c-primary-dark));
  color: #fff;
  border-top-right-radius: 4px;
  box-shadow: 0 2px 10px rgb(16 185 129 / 25%);
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
.typing i:nth-child(2) {
  animation-delay: 0.2s;
}
.typing i:nth-child(3) {
  animation-delay: 0.4s;
}
.cursor {
  color: var(--c-primary);
  animation: typing-blink 1s infinite;
}

/* —— 输入区 —— */
.input-row {
  display: flex;
  gap: 10px;
  margin-top: 12px;
}
.input-row textarea {
  flex: 1;
  resize: vertical;
  border: 1.5px solid var(--c-border);
  border-radius: var(--radius-md);
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
.finish {
  display: block;
  margin: 12px auto 0;
  background: var(--c-text);
  color: #fff;
  font-size: 12.5px;
}
.finish:hover:not(:disabled) {
  background: #374151;
}

@media (max-width: 520px) {
  .input-row {
    flex-direction: column;
  }
  .send {
    align-self: stretch;
  }
}
</style>
