<script setup>
import { nextTick, onMounted, ref } from 'vue'
import { del, get, post, streamChat } from '../api.js'

const messages = ref([]) // { role: 'user' | 'assistant', content, citations, error }
const input = ref('')
const streaming = ref(false)
const error = ref('')
const chatBox = ref(null)
const kbOpen = ref(false)

// —— 语料库管理 ——
const docs = ref([])
const kbLoading = ref(false)
const kbError = ref('')
const fileInput = ref(null)

const STATUS = {
  pending: { label: '排队中', cls: 'warn' },
  processing: { label: '入库中', cls: 'warn' },
  ready: { label: '就绪', cls: 'ok' },
  failed: { label: '失败', cls: 'bad' },
}

async function loadDocs() {
  kbLoading.value = true
  kbError.value = ''
  try {
    docs.value = await get('/api/kb/documents')
  } catch (e) {
    kbError.value = e.message || '语料库加载失败'
  } finally {
    kbLoading.value = false
  }
}

async function onFileSelected(event) {
  const file = event.target.files?.[0]
  if (!file) return
  const form = new FormData()
  form.append('file', file)
  try {
    await post('/api/kb/documents', form)
    await loadDocs()
  } catch (e) {
    kbError.value = e.message || '上传失败'
  } finally {
    event.target.value = ''
  }
}

async function removeDoc(doc) {
  if (!confirm(`删除「${doc.title}」？删除后不可恢复。`)) return
  try {
    await del(`/api/kb/documents/${doc.id}`)
    await loadDocs()
  } catch (e) {
    kbError.value = e.message || '删除失败'
  }
}

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
      // AI 尚未产出内容就报错：把错误话术作为独立消息展示
      messages.value.push({ id: `error-${Date.now()}`, role: 'error', content: data.content })
    } else {
      error.value = data.content
    }
  }
}

async function send() {
  const content = input.value.trim()
  if (!content || streaming.value) return
  input.value = ''
  messages.value.push({ id: `user-${Date.now()}`, role: 'user', content })
  streaming.value = true
  error.value = ''
  try {
    const { reader } = streamChat('/api/playground/ask', { content })
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
  } catch (e) {
    error.value = e.message || '网络异常，请重试'
  } finally {
    streaming.value = false
  }
}

onMounted(loadDocs)
</script>

<template>
  <section class="card playground">
    <div class="head">
      <div>
        <h2>🧪 Playground 知识库问答</h2>
        <p class="sub">基于面试题库 / 八股文 / 岗位 JD 语料库，RAG 检索回答，带引用来源</p>
      </div>
      <button class="btn btn-ghost" @click="kbOpen = !kbOpen">
        {{ kbOpen ? '收起语料库' : '语料库管理' }}
      </button>
    </div>

    <p v-if="error" class="msg error">{{ error }}</p>

    <!-- 语料库管理面板 -->
    <div v-if="kbOpen" class="kb-panel">
      <div class="kb-toolbar">
        <button class="btn btn-primary" @click="fileInput.click()">上传文档（txt/md/pdf）</button>
        <input ref="fileInput" type="file" accept=".txt,.md,.markdown,.pdf" hidden @change="onFileSelected" />
        <span v-if="kbLoading" class="loading">加载中…</span>
      </div>
      <p v-if="kbError" class="msg error">{{ kbError }}</p>
      <div v-if="!docs.length" class="empty">还没有可见文档（预置语料为空或未就绪）</div>
      <div v-for="doc in docs" :key="doc.id" class="doc-row">
        <div class="doc-info">
          <span class="doc-title">{{ doc.title }}</span>
          <span class="pill">{{ doc.source_type === 'preset' ? '预置' : '上传' }}</span>
          <span class="badge" :class="STATUS[doc.status]?.cls">
            {{ STATUS[doc.status]?.label ?? doc.status }}
          </span>
          <span v-if="doc.chunk_count != null" class="pill">{{ doc.chunk_count }} 块</span>
        </div>
        <button
          v-if="doc.source_type === 'uploaded'"
          class="btn btn-ghost doc-del"
          title="删除"
          @click="removeDoc(doc)"
        >
          删除
        </button>
      </div>
    </div>

    <!-- 聊天区 -->
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
          <!-- 引用来源：折叠展开 -->
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
  </section>
</template>

<style scoped>
.head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}
.playground h2 {
  font-size: 16px;
}

/* —— 语料库面板 —— */
.kb-panel {
  border: 1px solid var(--c-border);
  border-radius: var(--radius-md);
  padding: 12px 14px;
  margin-bottom: 14px;
  background: var(--c-bg-soft);
}
.kb-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
}
.loading {
  color: var(--c-faint);
  font-size: 12.5px;
}
.doc-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 7px 0;
  border-bottom: 1px dashed var(--c-border);
}
.doc-row:last-child {
  border-bottom: 0;
}
.doc-info {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.doc-title {
  font-size: 13px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.doc-del {
  padding: 4px 10px;
  font-size: 12px;
}

/* —— 聊天区 —— */
.chat-box {
  height: 420px;
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
  background: linear-gradient(135deg, var(--c-primary), var(--c-primary-dark));
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
  border: 1px solid var(--c-border);
  border-top-left-radius: 4px;
  color: var(--c-text-2);
}
.bubble.user {
  background: linear-gradient(135deg, var(--c-primary), var(--c-primary-dark));
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

/* —— 引用来源 —— */
.citations {
  margin-top: 10px;
  border-top: 1px dashed var(--c-border);
  padding-top: 8px;
}
.citations details {
  margin: 4px 0;
}
.citations summary {
  font-size: 12px;
  color: var(--c-primary-dark);
  cursor: pointer;
  user-select: none;
}
.cite-body {
  background: var(--c-bg-soft);
  border-radius: var(--radius-sm);
  padding: 8px 10px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--c-muted);
  white-space: pre-wrap;
  word-break: break-word;
  margin: 6px 0 0;
  max-height: 180px;
  overflow: auto;
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

@media (max-width: 520px) {
  .input-row {
    flex-direction: column;
  }
  .send {
    align-self: stretch;
  }
}
</style>
