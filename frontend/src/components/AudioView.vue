<script setup>
/* AudioView —— 录音分析模块（v4.2，参考页面形态对齐）。
   三步流：录音转文本（上传→Celery 转写→轮询→可编辑文本）→ 角色审核（LLM 分段标注，可编辑）
   → 面试审核（四维评分报告）。右上角「历史记录」抽屉切换历史录音。 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { get, post, put } from '../api.js'
import Icon from './Icon.vue'

const activeTab = ref('transcribe') // transcribe / role / interview
const uploading = ref(false)
const uploadError = ref('')
const current = ref(null) // 当前录音详情（GET /api/audio/analyses/{id}）
const pollTimer = ref(null)
const savingTranscript = ref(false)
const transcriptDraft = ref('')
const transcriptMsg = ref('')
const roleReviewing = ref(false)
const roleText = ref('') // 角色审核结果的可编辑文本（【角色】内容）
const reviewError = ref('')
const interviewReviewing = ref(false)
const showHistory = ref(false)
const history = ref([])
const historyState = ref('loading') // loading / ready / empty / error

const statusMap = {
  transcribing: { label: '转写中', cls: 'warn' },
  transcribed: { label: '已转写', cls: 'ok' },
  reviewed: { label: '已审核', cls: 'ok' },
  failed: { label: '转写失败', cls: 'error' },
}

const review = computed(() => current.value?.interview_review || null)
const roleMarked = computed(() => {
  if (roleText.value) return roleText.value
  const segs = current.value?.role_review?.segments || []
  return segs.map((s) => `【${s.speaker}】${s.text}`).join('\n')
})

function pickTab(tab) {
  activeTab.value = tab
  if (tab === 'interview' && !review.value) {
    // 进面试审核页时把角色标注结果带入可编辑框（对齐参考："同步到面试审核"）
    roleText.value = roleMarked.value
  }
}

async function loadHistory() {
  historyState.value = 'loading'
  try {
    history.value = await get('/api/audio/analyses')
    historyState.value = history.value.length ? 'ready' : 'empty'
  } catch {
    historyState.value = 'error'
  }
}

async function openHistoryItem(id) {
  showHistory.value = false
  await loadAudio(id)
  activeTab.value = 'transcribe' // 从历史打开一律回到转写页（文本可能需编辑）
}

async function loadAudio(id) {
  try {
    current.value = await get(`/api/audio/analyses/${id}`)
    transcriptDraft.value = current.value?.transcript || ''
    roleText.value = ''
  } catch (e) {
    uploadError.value = e.message || '加载失败'
  }
}

async function onFilePicked(event) {
  const file = event.target.files?.[0]
  if (!file || uploading.value) return
  uploading.value = true
  uploadError.value = ''
  try {
    const form = new FormData()
    form.append('file', file)
    const body = await post('/api/audio/analyses', form)
    await loadAudio(body.id)
    await loadHistory()
    startPolling(body.id)
  } catch (e) {
    uploadError.value = e.message || '上传失败，请重试'
  } finally {
    uploading.value = false
    event.target.value = ''
  }
}

function startPolling(id) {
  stopPolling()
  pollTimer.value = setInterval(async () => {
    try {
      current.value = await get(`/api/audio/analyses/${id}`)
      transcriptDraft.value = current.value?.transcript || ''
      if (current.value.status !== 'transcribing') stopPolling()
    } catch {
      stopPolling()
    }
  }, 3000)
}

function stopPolling() {
  if (pollTimer.value) {
    clearInterval(pollTimer.value)
    pollTimer.value = null
  }
}

async function saveTranscript() {
  if (!current.value || savingTranscript.value) return
  savingTranscript.value = true
  transcriptMsg.value = ''
  try {
    const body = await put(`/api/audio/analyses/${current.value.id}/transcript`, {
      transcript: transcriptDraft.value,
    })
    current.value.transcript = body.transcript
    current.value.status = body.status
    roleText.value = '' // 文本变了，旧角色标注作废
    transcriptMsg.value = '文本已保存'
  } catch (e) {
    transcriptMsg.value = e.message || '保存失败'
  } finally {
    savingTranscript.value = false
  }
}

async function runRoleReview() {
  if (roleReviewing.value || !current.value) return
  roleReviewing.value = true
  reviewError.value = ''
  try {
    const body = await post(`/api/audio/analyses/${current.value.id}/role-review`)
    current.value.role_review = body.role_review
    roleText.value = '' // 让 computed 从最新 segments 渲染
  } catch (e) {
    reviewError.value = e.message || '审核失败，请稍后重试'
  } finally {
    roleReviewing.value = false
  }
}

async function runInterviewReview() {
  if (interviewReviewing.value || !current.value) return
  interviewReviewing.value = true
  reviewError.value = ''
  try {
    // G-1 修复：把「面试审核」页可编辑的送审文本真实发给后端（后端优先使用请求文本；
    // 未编辑且无角色标注时留空，由后端回落原文）
    const payload = roleText.value.trim() ? { text: roleText.value.trim() } : {}
    const body = await post(
      `/api/audio/analyses/${current.value.id}/interview-review`,
      payload,
    )
    current.value.interview_review = body.interview_review
    current.value.status = 'reviewed'
  } catch (e) {
    reviewError.value = e.message || '审核失败，请稍后重试'
  } finally {
    interviewReviewing.value = false
  }
}

onMounted(loadHistory)
onBeforeUnmount(stopPolling)
</script>

<template>
  <section class="audio-view">
    <div class="head-row">
      <h2 class="page-title">录音分析</h2>
      <button class="btn btn-ghost btn-sm" @click="showHistory = !showHistory">历史记录</button>
    </div>

    <!-- 历史记录抽屉 -->
    <div v-if="showHistory" class="card history-card">
      <p v-if="historyState === 'loading'" class="msg muted">加载中…</p>
      <p v-else-if="historyState === 'error'" class="msg error">历史加载失败 <button class="link-btn" @click="loadHistory">重试</button></p>
      <p v-else-if="historyState === 'empty'" class="msg muted">还没有录音记录——上传一段面试录音开始。</p>
      <ul v-else class="hist-list">
        <li v-for="h in history" :key="h.id">
          <div class="hist-info">
            <span class="hist-name">{{ h.filename }}</span>
            <span class="hist-meta">
              <span class="badge" :class="statusMap[h.status]?.cls">{{ statusMap[h.status]?.label || h.status }}</span>
              {{ h.duration_seconds != null ? `${Math.round(h.duration_seconds / 60)} 分钟` : '' }}
              {{ new Date(h.created_at).toLocaleString() }}
            </span>
          </div>
          <button class="btn btn-ghost btn-sm" @click="openHistoryItem(h.id)">打开</button>
        </li>
      </ul>
    </div>

    <!-- 三步 Tab -->
    <div class="tabs">
      <button class="tab" :class="{ active: activeTab === 'transcribe' }" @click="pickTab('transcribe')">录音转文本</button>
      <button class="tab" :class="{ active: activeTab === 'role' }" @click="pickTab('role')">角色审核</button>
      <button class="tab" :class="{ active: activeTab === 'interview' }" @click="pickTab('interview')">面试审核</button>
    </div>

    <p v-if="uploadError" class="msg error">{{ uploadError }}</p>
    <p v-if="reviewError" class="msg error">{{ reviewError }}</p>

    <!-- ① 录音转文本 -->
    <section v-if="activeTab === 'transcribe'" class="card">
      <label class="dropzone" :class="{ busy: uploading }">
        <input type="file" accept=".wav,.mp3,.m4a,.webm,.flac,.ogg" :disabled="uploading" @change="onFilePicked" />
        <span class="dz-icon"><Icon name="mic" :size="30" /></span>
        <p class="dz-title">{{ uploading ? '上传中…' : '点击上传录音文件' }}</p>
        <p class="dz-sub">支持 wav / mp3 / m4a / webm · 50MB 内 · 本地 whisper 转写（约 1~3 分钟）</p>
      </label>

      <template v-if="current">
        <div class="status-row">
          <span class="badge" :class="statusMap[current.status]?.cls">{{ statusMap[current.status]?.label || current.status }}</span>
          <span class="muted">{{ current.filename }}</span>
          <span v-if="current.status === 'transcribing'" class="muted">转写中，页面可离开稍后回来…</span>
        </div>
        <p v-if="current.status === 'failed'" class="msg error">{{ current.error || '转写失败' }}</p>
        <template v-if="current.status !== 'transcribing' && current.status !== 'failed'">
          <div class="field-label">面试审核文本（可编辑）</div>
          <textarea v-model="transcriptDraft" class="big-textarea" rows="10"></textarea>
          <button class="btn btn-primary" :disabled="savingTranscript" @click="saveTranscript">
            {{ savingTranscript ? '保存中…' : '保存文本' }}
          </button>
          <span v-if="transcriptMsg" class="muted save-msg">{{ transcriptMsg }}</span>
        </template>
      </template>
      <p v-else class="msg muted">上传或从「历史记录」打开一段录音开始。</p>
    </section>

    <!-- ② 角色审核 -->
    <section v-else-if="activeTab === 'role'" class="card">
      <p class="muted">角色审核（LLM 按面试官/候选人分段标注），标注结果将同步到面试审核。</p>
      <button class="btn btn-primary" :disabled="roleReviewing || !current?.transcript" @click="runRoleReview">
        {{ roleReviewing ? '审核中…' : '👁️ 开始角色审核' }}
      </button>
      <div v-if="current?.role_review" class="role-result">
        <div
          v-for="(seg, i) in current.role_review.segments"
          :key="i"
          class="role-line"
          :class="{ interviewer: seg.speaker === '面试官' }"
        >
          <span class="role-tag">{{ seg.speaker }}</span>
          <span class="role-text">{{ seg.text }}</span>
          <span v-if="seg.reason" class="role-reason">{{ seg.reason }}</span>
        </div>
        <p v-if="current.role_review.summary" class="muted">结构：{{ current.role_review.summary }}</p>
      </div>
      <p v-else-if="current?.transcript" class="msg muted">还没有角色标注——点上方「开始角色审核」。</p>
      <p v-else class="msg muted">先在「录音转文本」页拿到文本。</p>
    </section>

    <!-- ③ 面试审核 -->
    <section v-else class="card">
      <p class="muted">面试审核：基于角色标注的对话做四维评分（与模拟面试报告同口径）。</p>
      <div class="field-label">送审对话（预填角色标注结果，可编辑）</div>
      <textarea v-model="roleText" class="big-textarea" rows="8" :placeholder="roleMarked || '先在「角色审核」页生成标注，或直接粘贴对话文本'"></textarea>
      <button
        class="btn btn-primary"
        :disabled="interviewReviewing || (!current?.role_review && !roleText.trim())"
        @click="runInterviewReview"
      >
        {{ interviewReviewing ? '审核中…' : '📝 开始面试审核' }}
      </button>
      <p v-if="current?.role_review" class="muted save-msg">将使用上方编辑后的文本送审（{{ roleText ? '已修改' : '与角色标注一致' }}）</p>

      <div v-if="review" class="review-result">
        <div class="score-row">
          <span>技术深度 <b>{{ review.technical_depth }}</b>/10</span>
          <span>表达结构 <b>{{ review.communication }}</b>/10</span>
          <span>项目真实性 <b>{{ review.project_authenticity }}</b>/10</span>
          <span>整体 <b>{{ review.overall }}</b>/10</span>
        </div>
        <p class="review-summary">{{ review.summary }}</p>
        <div v-if="review.highlights?.length" class="review-block">
          <b>亮点</b>
          <ul><li v-for="h in review.highlights" :key="h">{{ h }}</li></ul>
        </div>
        <div v-if="review.improvements?.length" class="review-block">
          <b>改进建议</b>
          <ul><li v-for="m in review.improvements" :key="m">{{ m }}</li></ul>
        </div>
      </div>
    </section>
  </section>
</template>

<style scoped>
.audio-view {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.head-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.page-title {
  margin: 0;
  font-size: 18px;
}
.history-card {
  border-left: 3px solid var(--c-primary);
}
.hist-list {
  list-style: none;
  margin: 0;
  padding: 0;
}
.hist-list li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 9px 4px;
  border-bottom: 1px solid var(--c-border);
}
.hist-list li:last-child {
  border-bottom: 0;
}
.hist-info {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.hist-name {
  font-size: 13px;
  font-weight: 600;
}
.hist-meta {
  font-size: 11.5px;
  color: var(--c-muted);
  display: flex;
  gap: 8px;
  align-items: center;
}
.tabs {
  display: flex;
  gap: 8px;
}
.tab {
  border: 1.5px solid var(--c-border);
  background: #fff;
  border-radius: 10px;
  padding: 8px 16px;
  font-size: 13px;
  font-family: inherit;
  cursor: pointer;
  transition: all 0.15s ease;
}
.tab.active {
  background: var(--c-primary);
  border-color: var(--c-primary);
  color: #fff;
  font-weight: 600;
}
.dropzone {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  border: 2px dashed var(--c-border);
  border-radius: 14px;
  padding: 28px 16px;
  cursor: pointer;
  transition: border-color 0.15s ease;
}
.dropzone:hover {
  border-color: var(--c-primary);
}
.dropzone.busy {
  opacity: 0.6;
  pointer-events: none;
}
.dropzone input {
  display: none;
}
.dz-icon {
  font-size: 30px;
}
.dz-title {
  font-size: 14px;
  font-weight: 700;
  margin: 0;
}
.dz-sub {
  font-size: 12px;
  color: var(--c-muted);
  margin: 0;
}
.status-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 12px;
}
.field-label {
  font-size: 12px;
  color: var(--c-muted);
  margin: 14px 0 4px;
}
.big-textarea {
  width: 100%;
  box-sizing: border-box;
  border: 1.5px solid var(--c-border);
  border-radius: 10px;
  padding: 12px;
  font-size: 13px;
  font-family: inherit;
  line-height: 1.8;
  resize: vertical;
}
.big-textarea:focus {
  outline: none;
  border-color: #6ee7b7;
}
.save-msg {
  margin-left: 10px;
  font-size: 12px;
}
.role-result {
  margin-top: 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.role-line {
  font-size: 13px;
  line-height: 1.7;
  padding: 6px 10px;
  border-radius: 8px;
  background: var(--c-bg-soft, #f6f7f9);
}
.role-line.interviewer {
  background: rgb(16 185 129 / 8%);
}
.role-tag {
  font-weight: 700;
  color: var(--c-primary-dark);
  margin-right: 8px;
}
.role-reason {
  color: var(--c-faint);
  font-size: 11px;
  margin-left: 8px;
}
.review-result {
  margin-top: 14px;
  border-top: 1px dashed var(--c-border);
  padding-top: 12px;
}
.score-row {
  display: flex;
  gap: 18px;
  flex-wrap: wrap;
  font-size: 13px;
}
.score-row b {
  color: var(--c-primary-dark);
  font-size: 16px;
}
.review-summary {
  font-size: 13px;
  line-height: 1.8;
  margin: 10px 0;
}
.review-block {
  font-size: 12.5px;
  margin-bottom: 8px;
}
.review-block ul {
  margin: 4px 0 0;
  padding-left: 20px;
}
.muted {
  color: var(--c-muted);
  font-size: 12.5px;
}
.link-btn {
  border: 0;
  background: transparent;
  color: var(--c-primary-dark);
  cursor: pointer;
  font-family: inherit;
  font-size: 12.5px;
  text-decoration: underline;
  padding: 0;
}
.btn-sm {
  padding: 6px 12px;
  font-size: 12px;
}
</style>
