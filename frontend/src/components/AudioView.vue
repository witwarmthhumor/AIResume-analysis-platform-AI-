<script setup>
/* AudioView —— 录音分析模块（v4.2，参考页面形态对齐）。
   三步流：录音转文本（上传→Celery 转写→轮询→可编辑文本）→ 角色审核（LLM 分段标注，可编辑）
   → 面试审核（四维评分报告）。右上角「历史记录」抽屉切换历史录音。 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { get, post, put } from '../api.js'
import { fmtDateTime } from '../utils.js'
import Icon from './Icon.vue'

const activeTab = ref('transcribe') // transcribe / role / interview
const uploading = ref(false)
const fastMode = ref(false) // v4.4.1 快速转写档：base 小模型换速度（准确率略降）
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
    if (fastMode.value) form.append('fast', 'true')
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
    <div class="page-head">
      <div><h2>录音分析</h2></div>
      <div class="ph-actions">
        <button class="btn btn-ghost btn-sm" @click="showHistory = !showHistory">历史记录</button>
      </div>
    </div>

    <!-- 历史记录抽屉 -->
    <div v-if="showHistory" class="card history-card">
      <div v-if="historyState === 'loading'" class="loading-row">
        <span class="spinner"></span>加载中…
      </div>
      <p v-else-if="historyState === 'error'" class="msg error">历史加载失败 <button class="link-btn" @click="loadHistory">重试</button></p>
      <p v-else-if="historyState === 'empty'" class="msg muted">还没有录音记录——上传一段面试录音开始。</p>
      <ul v-else class="hist-list">
        <li v-for="h in history" :key="h.id">
          <div class="hist-info">
            <span class="hist-name">{{ h.filename }}</span>
            <span class="hist-meta">
              <span class="badge" :class="statusMap[h.status]?.cls">{{ statusMap[h.status]?.label || h.status }}</span>
              {{ h.duration_seconds != null ? `${Math.round(h.duration_seconds / 60)} 分钟` : '' }}
              {{ fmtDateTime(h.created_at) }}
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
      <label class="fast-toggle">
        <input v-model="fastMode" type="checkbox" :disabled="uploading" />
        快速转写（base 小模型，约快一倍，准确率略降）
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
          <textarea v-model="transcriptDraft" class="input big-textarea" rows="10"></textarea>
          <button class="btn btn-primary" :disabled="savingTranscript" @click="saveTranscript">
            {{ savingTranscript ? '保存中…' : '保存文本' }}
          </button>
          <span v-if="transcriptMsg" class="muted save-msg">{{ transcriptMsg }}</span>
        </template>
      </template>
      <div v-else class="empty-state">
        <span class="es-icon"><Icon name="mic" :size="24" /></span>
        <p class="es-text">上传一段录音，或从右上角「历史记录」打开已有记录</p>
      </div>
    </section>

    <!-- ② 角色审核 -->
    <section v-else-if="activeTab === 'role'" class="card">
      <p class="muted">角色审核（LLM 按面试官/候选人分段标注），标注结果将同步到面试审核。</p>
      <button class="btn btn-primary" :disabled="roleReviewing || !current?.transcript" @click="runRoleReview">
        {{ roleReviewing ? '审核中…' : '开始角色审核' }}
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
      <textarea v-model="roleText" class="input big-textarea" rows="8" :placeholder="roleMarked || '先在「角色审核」页生成标注，或直接粘贴对话文本'"></textarea>
      <button
        class="btn btn-primary"
        :disabled="interviewReviewing || (!current?.role_review && !roleText.trim())"
        title="开始面试审核"
        @click="runInterviewReview"
      >
        <Icon name="pen" :size="14" /> {{ interviewReviewing ? '审核中…' : '开始面试审核' }}
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
  gap: var(--sp-4);
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
  gap: var(--sp-3);
  padding: var(--sp-2) var(--sp-1);
  border-bottom: 1px solid var(--c-border);
}
.hist-list li:last-child {
  border-bottom: 0;
}
.hist-info {
  display: flex;
  flex-direction: column;
  gap: var(--sp-1);
}
.hist-name {
  font-size: var(--fs-sm);
  font-weight: 600;
}
.hist-meta {
  font-size: var(--fs-xs);
  color: var(--c-muted);
  display: flex;
  gap: var(--sp-2);
  align-items: center;
}
.tabs {
  display: flex;
  gap: var(--sp-2);
}
.tab {
  border: 1px solid var(--c-border);
  background: #fff;
  border-radius: var(--radius-md);
  padding: var(--sp-2) var(--sp-4);
  font-size: var(--fs-sm);
  font-family: inherit;
  cursor: pointer;
  transition:
    border-color var(--dur-fast) var(--ease),
    background var(--dur-fast) var(--ease),
    color var(--dur-fast) var(--ease);
}
.tab:hover:not(.active) {
  border-color: var(--c-primary-border);
  color: var(--c-primary-dark);
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
  border: 1px dashed var(--c-border-strong);
  border-radius: var(--radius-md);
  padding: var(--sp-6) var(--sp-4);
  cursor: pointer;
  transition: border-color var(--dur-fast) var(--ease);
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
.dz-title {
  font-size: var(--fs-md);
  font-weight: 600;
  margin: 0;
}
.fast-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-top: var(--sp-3);
  font-size: var(--fs-xs);
  color: var(--c-text-2);
  cursor: pointer;
}
.fast-toggle input {
  accent-color: var(--c-primary);
}
.dz-sub {
  font-size: var(--fs-xs);
  color: var(--c-muted);
  margin: 0;
}
.status-row {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  margin-top: var(--sp-3);
}
.field-label {
  font-size: var(--fs-xs);
  color: var(--c-muted);
  margin: var(--sp-3) 0 var(--sp-1);
}
/* 文本域统一走全局 .input；这里只保留长文本的行高习惯 */
.big-textarea {
  line-height: 1.8;
}
.save-msg {
  margin-left: var(--sp-3);
}
.role-result {
  margin-top: var(--sp-3);
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.role-line {
  font-size: var(--fs-sm);
  line-height: 1.7;
  padding: 6px var(--sp-3);
  border-radius: var(--radius-sm);
  background: var(--c-bg-soft);
}
.role-line.interviewer {
  background: var(--wash-primary);
}
.role-tag {
  font-weight: 600;
  color: var(--c-primary-dark);
  margin-right: var(--sp-2);
}
.role-reason {
  color: var(--c-faint);
  font-size: var(--fs-xs);
  margin-left: var(--sp-2);
}
.review-result {
  margin-top: var(--sp-3);
  border-top: 1px dashed var(--c-border);
  padding-top: var(--sp-3);
}
.score-row {
  display: flex;
  gap: var(--sp-4);
  flex-wrap: wrap;
  font-size: var(--fs-sm);
}
.score-row b {
  color: var(--c-primary-dark);
  font-size: var(--fs-lg);
}
.review-summary {
  font-size: var(--fs-sm);
  line-height: 1.8;
  margin: var(--sp-3) 0;
}
.review-block {
  font-size: var(--fs-sm);
  margin-bottom: var(--sp-2);
}
.review-block ul {
  margin: var(--sp-1) 0 0;
  padding-left: 20px;
}
.muted {
  font-size: var(--fs-sm);
}
.link-btn {
  border: 0;
  background: transparent;
  color: var(--c-primary-dark);
  cursor: pointer;
  font-family: inherit;
  font-size: var(--fs-sm);
  text-decoration: underline;
  padding: 0;
}

/* —— 响应式（v4.2.1）—— */
@media (max-width: 768px) {
  .tabs {
    flex-wrap: wrap;
  }
  .tab {
    flex: 1 1 30%;
    text-align: center;
  }
  .dropzone {
    padding: var(--sp-5) var(--sp-3);
  }
  .hist-list li {
    flex-wrap: wrap;
  }
  .score-row {
    gap: var(--sp-3);
  }
}

</style>
