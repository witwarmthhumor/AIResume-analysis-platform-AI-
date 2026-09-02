<script setup>
import { nextTick, onMounted, ref, watch } from 'vue'
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

const STAGES = { intro: '自我介绍', technical: '技术问答', deep_dive: '深入追问', wrapup: '收尾' }

async function startOrResume() {
  loading.value = true
  error.value = ''
  try {
    const res = await fetch(`/api/resumes/${props.resume.id}/interviews`, { method: 'POST' })
    const body = await res.json().catch(() => null)
    if (!res.ok) throw new Error(body?.detail || '无法开始面试')
    session.value = body.session
    messages.value = [...body.session.messages]
  } catch (e) {
    error.value = e.message || '网络异常，请稍后重试'
  } finally {
    loading.value = false
  }
}

function handleEvent(block) {
  const event = block.match(/^event: (.+)$/m)?.[1]
  const dataLine = block.match(/^data: (.+)$/m)?.[1]
  if (!event || !dataLine) return
  const data = JSON.parse(dataLine)
  if (event === 'meta') {
    messages.value.push({ id: `stream-${Date.now()}`, role: 'interviewer', content: '' })
  } else if (event === 'delta') {
    messages.value[messages.value.length - 1].content += data.content
  } else if (event === 'done') {
    session.value.turn_count = data.turn
    session.value.stage = data.stage
  } else if (event === 'error') {
    error.value = data.content
    messages.value.pop()
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
    const res = await fetch(`/api/interviews/${session.value.id}/messages`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ content }),
    })
    if (!res.ok) {
      const body = await res.json().catch(() => null)
      throw new Error(body?.detail || '发送失败')
    }
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let boundary
      while ((boundary = buffer.indexOf('\n\n')) >= 0) {
        const block = buffer.slice(0, boundary)
        buffer = buffer.slice(boundary + 2)
        handleEvent(block)
        await nextTick()
        chatBox.value.scrollTop = chatBox.value.scrollHeight
      }
    }
  } catch (e) {
    error.value = e.message || '网络异常，请重试'
  } finally {
    streaming.value = false
  }
}

async function finish() {
  if (finishing.value || streaming.value || !session.value) return
  finishing.value = true
  error.value = ''
  try {
    const res = await fetch(`/api/interviews/${session.value.id}/finish`, { method: 'POST' })
    const body = await res.json().catch(() => null)
    if (!res.ok) throw new Error(body?.detail || '结束面试失败')
    session.value = body
  } catch (e) {
    error.value = e.message || '网络异常，请稍后重试'
  } finally {
    finishing.value = false
  }
}

function isStreamingNow(message) {
  return streaming.value && message === messages.value[messages.value.length - 1]
}

watch(messages, async () => { await nextTick(); if (chatBox.value) chatBox.value.scrollTop = chatBox.value.scrollHeight }, { deep: true })
onMounted(startOrResume)
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
    <p v-if="error" class="msg error">{{ error }}</p>
    <p v-if="loading" class="loading">正在恢复面试会话…</p>
    <template v-else-if="session?.status === 'finished'">
      <InterviewReport :report="session.final_report" />
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
