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

watch(messages, async () => { await nextTick(); if (chatBox.value) chatBox.value.scrollTop = chatBox.value.scrollHeight }, { deep: true })
onMounted(startOrResume)
</script>

<template>
  <section class="card interview">
    <div class="head">
      <div><h2>文字模拟面试</h2><p v-if="session" class="progress">{{ STAGES[session.stage] || session.stage }} · 已完成 {{ session.turn_count }}/{{ session.max_turns }} 轮</p></div>
      <button class="ghost" @click="emit('close')">返回简历</button>
    </div>
    <p v-if="error" class="msg error">{{ error }}</p>
    <p v-if="loading" class="loading">正在恢复面试会话…</p>
    <template v-else-if="session?.status === 'finished'">
      <InterviewReport :report="session.final_report" />
    </template>
    <template v-else-if="session">
      <div ref="chatBox" class="chat-box">
        <div v-for="message in messages" :key="message.id" class="message" :class="message.role">
          <span class="role">{{ message.role === 'candidate' ? '我' : '面试官' }}</span><p>{{ message.content }}<span v-if="streaming && message === messages[messages.length - 1]" class="cursor">▋</span></p>
        </div>
      </div>
      <div class="input-row"><textarea v-model="input" :disabled="streaming" rows="2" placeholder="输入你的回答…" @keydown.enter.exact.prevent="send"></textarea><button :disabled="streaming || !input.trim()" @click="send">{{ streaming ? '回答中…' : '发送' }}</button></div>
      <button class="finish" :disabled="finishing || streaming || session.turn_count < 1" @click="finish">{{ finishing ? '生成评价中…' : '结束面试并查看评价' }}</button>
    </template>
  </section>
</template>

<style scoped>
.card { background: #fff; border-radius: 12px; box-shadow: 0 1px 4px rgb(0 0 0 / 6%); padding: 20px 24px; }
.head { display: flex; justify-content: space-between; align-items: flex-start; }
h2 { margin: 0; font-size: 18px; }.progress { margin: 4px 0 12px; color: #6b7280; font-size: 13px; }
button { border: none; border-radius: 8px; background: #10b981; color: #fff; padding: 9px 16px; cursor: pointer; } button:disabled { opacity: .55; cursor: wait; }
.ghost { background: #f3f4f6; color: #4b5563; font-size: 12px; }.msg { border-radius: 8px; padding: 8px 12px; font-size: 13px; }.error { background: #fee2e2; color: #b91c1c; }.loading { color: #6b7280; font-size: 13px; }
.chat-box { height: 360px; overflow-y: auto; background: #f8fafc; border-radius: 8px; padding: 14px; }.message { display: flex; gap: 8px; margin: 0 0 14px; align-items: flex-start; }.message p { margin: 0; padding: 9px 12px; border-radius: 10px; max-width: 78%; white-space: pre-wrap; line-height: 1.6; font-size: 14px; }.message.interviewer p { background: #fff; border: 1px solid #e5e7eb; }.message.candidate { flex-direction: row-reverse; }.message.candidate p { background: #d1fae5; color: #065f46; }.role { color: #9ca3af; font-size: 11px; padding-top: 8px; white-space: nowrap; }.cursor { color: #10b981; animation: blink 1s infinite; } @keyframes blink { 50% { opacity: 0; } }
.input-row { display: flex; gap: 8px; margin-top: 12px; }.input-row textarea { flex: 1; resize: vertical; border: 1px solid #d1d5db; border-radius: 8px; padding: 9px; font: inherit; font-size: 14px; }.finish { display: block; margin: 12px auto 0; background: #6b7280; font-size: 12px; }
@media (max-width: 520px) { .input-row { flex-direction: column; } .input-row button { align-self: flex-end; } }
</style>
