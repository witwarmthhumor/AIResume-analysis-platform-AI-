<script setup>
/* AgentRunView —— 「一键求职准备」任务面板（v4.0 LangGraph 试点，PRD §8）
   发起 run → 步骤条随 node_start/end/retry 流转 → 审批卡（TTL 倒计时）→
   结果分区（分析/匹配/出题）。审批后 202 转 /stream 续听；
   断点恢复靠进页时扫 waiting_approval 的 run。
   流守卫沿用 v3.7 约定：runSeq 竞态丢弃 + activeAbort 中止句柄。 */
import { computed, onBeforeUnmount, onDeactivated, onMounted, ref } from 'vue'
import { get, parseSseBlock, post, streamChat, streamGet } from '../../api.js'

const jdText = ref('')
const positionType = ref('')
const running = ref(false)
const error = ref('')
const runId = ref(null)
const runStatus = ref(null) // running / waiting_approval / completed / failed / aborted
const steps = ref([]) // [{node, state: waiting|running|retry|done|failed, reasons}]
const approval = ref(null) // {approval_id, summary, expires_at}
const countdown = ref(0)
const output = ref(null)
const failReasons = ref([])

let activeAbort = null // 当前流的 abort 句柄
let runSeq = 0 // 竞态守卫：旧流的事件一律丢弃
let timer = null

const NODE_LABELS = {
  load_resume: '读取简历',
  analyzer: 'AI 分析',
  matcher: '岗位匹配',
  questioner: '模拟出题',
  deliver: '整理交付',
}
const POSITION_OPTIONS = [
  { value: '', label: '通用' },
  { value: 'fresh', label: '校招应届' },
  { value: 'intern', label: '实习' },
  { value: 'senior', label: '社招资深' },
]

const hasRun = computed(() => runId.value != null)
const canStart = computed(() => (jdText.value.trim() || '').length > 0 && !running.value)

// —— 步骤条状态 ——
function initSteps(plan) {
  steps.value = (plan || [])
    .filter((p) => NODE_LABELS[p.node])
    .map((p) => ({ node: p.node, state: 'waiting', reasons: [] }))
}
function stepOf(node) {
  return steps.value.find((s) => s.node === node)
}

// —— TTL 倒计时 ——
function startCountdown(expiresAt) {
  stopCountdown()
  const tick = () => {
    countdown.value = Math.max(0, Math.round((new Date(expiresAt).getTime() - Date.now()) / 1000))
    if (countdown.value <= 0) stopCountdown()
  }
  tick()
  timer = setInterval(tick, 1000)
}
function stopCountdown() {
  if (timer) clearInterval(timer)
  timer = null
}

// —— 事件处理（runSeq 守卫内） ——
function handleEvent(event, data) {
  if (event === 'meta') {
    runId.value = data.run_id
    runStatus.value = 'running'
    return
  }
  if (event === 'plan') {
    initSteps(data.plan)
    return
  }
  if (event === 'node_start') {
    const s = stepOf(data.node)
    if (s) s.state = 'running'
    return
  }
  if (event === 'node_end') {
    const s = stepOf(data.node)
    if (s && s.state !== 'retry') s.state = 'done'
    return
  }
  if (event === 'retry') {
    const s = stepOf(data.node)
    if (s) {
      s.state = 'retry'
      s.reasons = data.reasons || []
    }
    return
  }
  if (event === 'approval_required') {
    runStatus.value = 'waiting_approval'
    approval.value = data
    startCountdown(data.expires_at)
    return
  }
  if (event === 'session_created') {
    output.value = { ...(output.value || {}), session_id: data.session_id }
    return
  }
  if (event === 'done') {
    runStatus.value = 'completed'
    output.value = data.output || null
    return
  }
  if (event === 'fatal') {
    runStatus.value = 'failed'
    error.value = data.content || '任务失败'
    failReasons.value = collectFailReasons()
    return
  }
  if (event === 'stream_closed') {
    // 服务端 30s 防御性断流或 waiting_approval 关流：等待审批态照常展示
    if (data.reason === 'waiting_approval') runStatus.value = 'waiting_approval'
    return
  }
  // snapshot（/stream 重连首帧）：恢复步骤条与审批卡
  if (event === 'snapshot') {
    applySnapshot(data)
  }
}

// 失败原因从步骤条 retry 记录里收
function collectFailReasons() {
  const list = []
  for (const s of steps.value) {
    if (s.reasons?.length) list.push(`${NODE_LABELS[s.node]}：${s.reasons.join('；')}`)
  }
  return list
}

function applySnapshot(snap) {
  runId.value = snap.run_id
  runStatus.value = snap.status
  if (snap.plan) initSteps(snap.plan)
  if (snap.output) output.value = snap.output
  if (snap.status === 'waiting_approval' && snap.approval) {
    approval.value = snap.approval
    startCountdown(snap.approval.expires_at)
  } else {
    approval.value = null
    stopCountdown()
  }
}

// —— 流消费：SSE 块切分与解析复用 v1 套路 ——
async function consume(readerPromise, seq) {
  const stream = await readerPromise
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    if (seq !== runSeq) return // 已被更新的 run/模式切走
    const { done, value } = await stream.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let boundary
    while ((boundary = buffer.indexOf('\n\n')) >= 0) {
      const block = buffer.slice(0, boundary)
      buffer = buffer.slice(boundary + 2)
      if (seq !== runSeq) return
      const parsed = parseSseBlock(block)
      if (parsed) handleEvent(parsed.event, parsed.data)
    }
  }
}

function resetStream() {
  activeAbort?.()
  activeAbort = null
}

function finishTerminal(status) {
  if (status) runStatus.value = status
  running.value = false
  resetStream()
}

// —— 三个动作：发起 / 审批后续听 / 断点恢复 ——
async function startRun() {
  if (!canStart.value) return
  const seq = ++runSeq
  running.value = true
  error.value = ''
  output.value = null
  approval.value = null
  failReasons.value = []
  steps.value = []
  runStatus.value = 'running'
  try {
    const { reader, abort } = streamChat('/api/agent-v2/runs', {
      jd_text: jdText.value.trim(),
      position_type: positionType.value || null,
    })
    activeAbort = abort
    await consume(reader, seq)
  } catch (e) {
    if (seq === runSeq && e?.name !== 'AbortError') error.value = e.message || '发起失败'
  } finally {
    if (seq === runSeq) finishTerminal()
  }
}

// approve 返回 202：从 /stream 续听剩余事件（服务端已从断点续跑）
async function decide(decision) {
  if (!approval.value) return
  stopCountdown()
  const seq = runSeq
  try {
    await post(`/api/agent-v2/runs/${runId.value}/approve`, { decision })
    approval.value = null
    runStatus.value = 'running'
    running.value = true
    const { reader, abort } = streamGet(`/api/agent-v2/runs/${runId.value}/stream`)
    activeAbort = abort
    await consume(reader, seq)
  } catch (e) {
    if (e?.name !== 'AbortError') error.value = e.message || '审批请求失败'
    // 410 等：审批单已过期，回填状态便于用户重发
    runStatus.value = 'failed'
    running.value = false
  } finally {
    if (seq === runSeq) finishTerminal()
  }
}

async function abortRun() {
  if (!runId.value) return
  try {
    await post(`/api/agent-v2/runs/${runId.value}/abort`)
  } catch {} // 已结束的 run abort 会 409，不影响前端
  const seq = ++runSeq
  resetStream()
  stopCountdown()
  runStatus.value = 'aborted'
  running.value = false
}

// 从失败节点重试：服务端新建 run 注入已完成产物（D2），前端切到新 run 的流
async function retryFailed() {
  if (runStatus.value !== 'failed') return
  const seq = ++runSeq
  running.value = true
  error.value = ''
  failReasons.value = []
  try {
    const { reader, abort } = streamChat(`/api/agent-v2/runs/${runId.value}/retry-node`, {})
    activeAbort = abort
    await consume(reader, seq) // 流首帧 meta 会把 runId 切到新 run
  } catch (e) {
    if (seq === runSeq && e?.name !== 'AbortError') error.value = e.message || '重试失败'
  } finally {
    if (seq === runSeq) finishTerminal()
  }
}

// —— 断点恢复：进页扫 waiting_approval ——
const resumableRun = ref(null)
async function checkResumable() {
  try {
    const list = await get('/api/agent-v2/runs?page=1&page_size=10')
    const hit = (list.items || []).find((r) => r.status === 'waiting_approval')
    if (hit) resumableRun.value = hit
  } catch {} // 未登录/接口关闭都静默
}
async function resumeRun() {
  const id = resumableRun.value?.id
  if (!id) return
  resumableRun.value = null
  const seq = ++runSeq
  running.value = true
  error.value = ''
  output.value = null
  failReasons.value = []
  steps.value = []
  try {
    const detail = await get(`/api/agent-v2/runs/${id}`) // 先拿 plan/产物快照
    runId.value = detail.id
    runStatus.value = detail.status
    initSteps(detail.plan)
    if (detail.output) output.value = detail.output
    if (detail.status === 'waiting_approval' && detail.approval) {
      approval.value = detail.approval
      startCountdown(detail.approval.expires_at)
      running.value = false
      return // 等审批即可，不用听流
    }
    const { reader, abort } = streamGet(`/api/agent-v2/runs/${id}/stream`)
    activeAbort = abort
    await consume(reader, seq)
  } catch (e) {
    if (seq === runSeq && e?.name !== 'AbortError') error.value = e.message || '恢复失败'
  } finally {
    if (seq === runSeq) finishTerminal()
  }
}

function backToForm() {
  const seq = ++runSeq
  resetStream()
  stopCountdown()
  running.value = false
  runId.value = null
  runStatus.value = null
  steps.value = []
  output.value = null
  approval.value = null
  error.value = ''
  failReasons.value = []
}

onMounted(checkResumable)
onBeforeUnmount(() => {
  const seq = ++runSeq // 组件卸载后旧流事件全部作废
  resetStream()
  stopCountdown()
})
onDeactivated(() => {
  const seq = ++runSeq
  resetStream()
  stopCountdown()
})
</script>

<template>
  <div class="run-view">
    <!-- 发起表单（无进行中任务时显示） -->
    <div v-if="!hasRun" class="rv-form">
      <div class="rv-hi">🚀 一键求职准备</div>
      <div class="rv-sub">贴入目标岗位 JD，AI 会自动完成简历分析 → 岗位匹配 → 定制出题，创建面试场次前会先征求你的确认</div>
      <textarea
        v-model="jdText"
        class="rv-jd"
        rows="5"
        placeholder="粘贴岗位 JD（岗位职责、任职要求…）"
      ></textarea>
      <div class="rv-row">
        <select v-model="positionType" class="rv-select">
          <option v-for="o in POSITION_OPTIONS" :key="o.value" :value="o.value">{{ o.label }}</option>
        </select>
        <button class="rv-go" :disabled="!canStart" @click="startRun">
          {{ running ? '任务进行中…' : '开始' }}
        </button>
      </div>
    </div>

    <!-- 断点恢复提示 -->
    <div v-if="resumableRun && !hasRun" class="rv-resume">
      <span>有一个任务正等待你的审批（#{{ resumableRun.id }}）</span>
      <button class="rv-btn-main" @click="resumeRun">继续处理</button>
    </div>

    <!-- 步骤条 -->
    <div v-if="steps.length" class="rv-steps">
      <div
        v-for="s in steps"
        :key="s.node"
        class="rv-step"
        :class="s.state"
        :title="(s.reasons || []).join('；')"
      >
        <span class="rv-dot">{{ { waiting: '○', running: '◌', retry: '↻', done: '●', failed: '✕' }[s.state] }}</span>
        <span class="rv-name">{{ NODE_LABELS[s.node] }}</span>
      </div>
      <div v-if="runStatus === 'running'" class="rv-running-hint">执行中…</div>
    </div>

    <!-- 审批卡 -->
    <div v-if="approval" class="rv-approval">
      <div class="rv-ap-title">⚠️ 需要你的确认</div>
      <div class="rv-ap-summary">{{ approval.summary }}</div>
      <div class="rv-ap-timer" :class="{ urgent: countdown <= 60 }">
        {{ countdown > 0 ? `剩余 ${Math.floor(countdown / 60)} 分 ${countdown % 60} 秒` : '已超时，请重新发起任务' }}
      </div>
      <div class="rv-ap-actions">
        <button class="rv-btn-main" @click="decide('approved')">批准并创建场次</button>
        <button class="rv-btn-ghost" @click="decide('rejected')">不用了</button>
      </div>
    </div>

    <!-- 结果分区 -->
    <div v-if="output && runStatus === 'completed'" class="rv-result">
      <div v-if="output.analysis" class="rv-card">
        <div class="rv-card-title">📋 简历分析</div>
        <div class="rv-card-body">
          <div v-if="output.analysis.target_position">目标岗位：{{ output.analysis.target_position }}</div>
          <div v-if="output.analysis.position_match">{{ output.analysis.position_match }}</div>
          <div v-if="output.analysis.strengths?.length" class="rv-list">
            <span class="rv-tag plus" v-for="t in output.analysis.strengths" :key="t">{{ t }}</span>
          </div>
          <div v-if="output.analysis.suggestions?.length" class="rv-suggest">
            <div v-for="t in output.analysis.suggestions" :key="t">· {{ t }}</div>
          </div>
        </div>
      </div>
      <div v-if="output.match" class="rv-card">
        <div class="rv-card-title">🎯 JD 匹配（{{ output.match.match_score ?? '-' }} 分）</div>
        <div class="rv-card-body">
          <div v-if="output.match.matched_keywords?.length" class="rv-list">
            <span class="rv-tag plus" v-for="t in output.match.matched_keywords" :key="t">{{ t }}</span>
          </div>
          <div v-if="output.match.missing_keywords?.length" class="rv-list">
            <span class="rv-tag minus" v-for="t in output.match.missing_keywords" :key="t">{{ t }}</span>
          </div>
          <div v-if="output.match.suggestions?.length" class="rv-suggest">
            <div v-for="t in output.match.suggestions" :key="t">· {{ t }}</div>
          </div>
        </div>
      </div>
      <div v-if="output.questions?.length" class="rv-card">
        <div class="rv-card-title">🎤 定制面试题</div>
        <div class="rv-card-body">
          <ol class="rv-questions">
            <li v-for="(q, i) in output.questions" :key="i">{{ q }}</li>
          </ol>
          <div v-if="output.question_sources?.length" class="rv-cite">出题依据：{{ output.question_sources.join('、') }}</div>
        </div>
      </div>
      <div v-if="output.session_id" class="rv-session">✅ 面试场次已创建（#{{ output.session_id }}），可在「模拟面试」页继续</div>
      <button class="rv-btn-ghost rv-again" @click="backToForm">再准备一个岗位</button>
    </div>

    <!-- 失败卡 -->
    <div v-if="runStatus === 'failed'" class="rv-fail">
      <div class="rv-fail-title">⚠️ 任务失败</div>
      <div class="rv-fail-body">{{ error || '任务执行失败' }}</div>
      <div v-if="failReasons.length" class="rv-fail-reasons">
        <div v-for="(r, i) in failReasons" :key="i">· {{ r }}</div>
      </div>
      <button class="rv-btn-main" @click="retryFailed">从失败节点重试</button>
    </div>

    <!-- 进行中可放弃 -->
    <div v-if="hasRun && (runStatus === 'running' || runStatus === 'waiting_approval' || runStatus === 'aborted')" class="rv-toolbar">
      <button v-if="runStatus !== 'aborted'" class="rv-btn-ghost" @click="abortRun">放弃任务</button>
      <button class="rv-btn-ghost" @click="backToForm">返回发起页</button>
    </div>
  </div>
</template>

<style scoped>
.run-view {
  display: flex;
  flex-direction: column;
  gap: 12px;
  height: 100%;
  overflow-y: auto;
}
.rv-form,
.rv-approval,
.rv-result,
.rv-fail,
.rv-resume {
  background: rgb(16 185 129 / 4%);
  border: 1px solid var(--c-border, #e5e7eb);
  border-radius: 12px;
  padding: 16px;
}
.rv-hi {
  font-size: 16px;
  font-weight: 700;
  color: var(--c-text, #1f2937);
}
.rv-sub {
  font-size: 12.5px;
  color: var(--c-text-2, #6b7280);
  margin: 6px 0 12px;
}
.rv-jd {
  width: 100%;
  box-sizing: border-box;
  border: 1.5px solid var(--c-border, #e5e7eb);
  border-radius: 10px;
  padding: 10px 12px;
  font-size: 13px;
  font-family: inherit;
  resize: vertical;
}
.rv-row {
  display: flex;
  gap: 10px;
  margin-top: 10px;
}
.rv-select {
  border: 1.5px solid var(--c-border, #e5e7eb);
  border-radius: 10px;
  padding: 8px 10px;
  font-size: 13px;
  font-family: inherit;
  background: #fff;
}
.rv-go {
  flex: 1;
  padding: 9px 0;
  border: 0;
  border-radius: 10px;
  background: var(--c-primary, #10b981);
  color: #fff;
  font-size: 14px;
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
}
.rv-go:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.rv-resume {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  font-size: 13px;
  color: var(--c-text-2, #374151);
}
.rv-btn-main {
  padding: 8px 16px;
  border: 0;
  border-radius: 8px;
  background: var(--c-primary, #10b981);
  color: #fff;
  font-size: 13px;
  font-weight: 600;
  font-family: inherit;
  cursor: pointer;
}
.rv-btn-ghost {
  padding: 8px 16px;
  border: 1.5px solid var(--c-border, #e5e7eb);
  border-radius: 8px;
  background: #fff;
  color: var(--c-text-2, #374151);
  font-size: 13px;
  font-family: inherit;
  cursor: pointer;
}
.rv-steps {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  background: #fff;
  border: 1px solid var(--c-border, #e5e7eb);
  border-radius: 12px;
  padding: 12px 14px;
}
.rv-step {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 5px 10px;
  border-radius: 999px;
  font-size: 12.5px;
  background: rgb(156 163 175 / 10%);
  color: var(--c-text-2, #6b7280);
}
.rv-step.running {
  background: rgb(59 130 246 / 12%);
  color: #2563eb;
  font-weight: 600;
}
.rv-step.done {
  background: rgb(16 185 129 / 12%);
  color: #059669;
}
.rv-step.retry {
  background: rgb(245 158 11 / 14%);
  color: #b45309;
}
.rv-step.failed {
  background: rgb(239 68 68 / 12%);
  color: #dc2626;
}
.rv-running-hint {
  font-size: 12px;
  color: var(--c-faint, #9ca3af);
  margin-left: auto;
}
.rv-approval {
  border-color: rgb(245 158 11 / 40%);
  background: rgb(245 158 11 / 6%);
}
.rv-ap-title {
  font-weight: 700;
  font-size: 14px;
  color: #b45309;
}
.rv-ap-summary {
  font-size: 13px;
  color: var(--c-text, #1f2937);
  margin: 8px 0;
}
.rv-ap-timer {
  font-size: 12.5px;
  color: #b45309;
  margin-bottom: 10px;
}
.rv-ap-timer.urgent {
  color: #dc2626;
  font-weight: 600;
}
.rv-ap-actions {
  display: flex;
  gap: 10px;
}
.rv-card {
  background: #fff;
  border: 1px solid var(--c-border, #e5e7eb);
  border-radius: 12px;
  padding: 14px;
  margin-bottom: 10px;
}
.rv-card-title {
  font-weight: 700;
  font-size: 13.5px;
  color: var(--c-text, #1f2937);
  margin-bottom: 8px;
}
.rv-card-body {
  font-size: 13px;
  color: var(--c-text-2, #374151);
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.rv-list {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.rv-tag {
  padding: 2px 10px;
  border-radius: 999px;
  font-size: 12px;
}
.rv-tag.plus {
  background: rgb(16 185 129 / 12%);
  color: #059669;
}
.rv-tag.minus {
  background: rgb(239 68 68 / 10%);
  color: #dc2626;
}
.rv-suggest {
  color: var(--c-text-2, #6b7280);
}
.rv-questions {
  margin: 0;
  padding-left: 18px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.rv-cite {
  font-size: 12px;
  color: var(--c-faint, #9ca3af);
}
.rv-session {
  font-size: 13px;
  color: #059669;
  font-weight: 600;
}
.rv-again {
  align-self: flex-start;
}
.rv-fail {
  border-color: rgb(239 68 68 / 35%);
  background: rgb(239 68 68 / 5%);
}
.rv-fail-title {
  font-weight: 700;
  color: #dc2626;
  font-size: 14px;
}
.rv-fail-body {
  font-size: 13px;
  color: var(--c-text, #1f2937);
  margin: 8px 0;
}
.rv-fail-reasons {
  font-size: 12.5px;
  color: var(--c-text-2, #6b7280);
  margin-bottom: 10px;
}
.rv-toolbar {
  display: flex;
  gap: 10px;
}
</style>
