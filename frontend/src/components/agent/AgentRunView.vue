<script setup>
/* AgentRunView —— 「一键求职准备」任务面板（v4.0 LangGraph 试点，PRD §8）
   发起 run → 步骤条随 node_start/end/retry 流转 → 审批卡（TTL 倒计时）→
   结果分区（分析/匹配/出题）。审批后 202 转 /stream 续听；
   断点恢复靠进页时扫 waiting_approval 的 run。
   流守卫沿用 v3.7 约定：runSeq 竞态丢弃 + activeAbort 中止句柄。 */
import { computed, onBeforeUnmount, onDeactivated, onMounted, ref } from 'vue'
import { get, parseSseBlock, post, streamChat, streamGet } from '../../api.js'
import Icon from '../Icon.vue'

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
    // 服务端 90s 无事件防御性断流，或 waiting_approval 主动关流：
    // 前者客户端可重连，审批态照常展示（approval_required 已置 waiting_approval）
    if (data.reason === 'waiting_approval') runStatus.value = 'waiting_approval'
    return
  }
  // snapshot（/stream 重连首帧）：恢复步骤条/产物/审批卡（等待审批时后端带 approval）
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
  // 等待审批：用后端快照里的 approval（与事件/get 详情同结构）恢复审批卡；
  // 其余状态清掉审批卡并停表，防过期残留的倒计时在别的状态下继续跑
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
      <div class="rv-hi"><Icon name="zap" :size="16" />一键求职准备</div>
      <div class="rv-sub">贴入目标岗位 JD，AI 会自动完成简历分析 → 岗位匹配 → 定制出题，创建面试场次前会先征求你的确认</div>
      <textarea
        v-model="jdText"
        class="input"
        rows="5"
        placeholder="粘贴岗位 JD（岗位职责、任职要求…）"
      ></textarea>
      <div class="rv-row">
        <select v-model="positionType" class="input rv-select">
          <option v-for="o in POSITION_OPTIONS" :key="o.value" :value="o.value">{{ o.label }}</option>
        </select>
        <button class="btn btn-primary rv-go" :disabled="!canStart" @click="startRun">
          {{ running ? '任务进行中…' : '开始' }}
        </button>
      </div>
    </div>

    <!-- 断点恢复提示 -->
    <div v-if="resumableRun && !hasRun" class="rv-resume">
      <span>有一个任务正等待你的审批（#{{ resumableRun.id }}）</span>
      <button class="btn btn-primary" @click="resumeRun">继续处理</button>
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
        <span class="rv-dot">
          <span v-if="s.state === 'waiting'" class="rv-wait-dot"></span>
          <span v-else-if="s.state === 'running'" class="spinner rv-spin"></span>
          <Icon v-else-if="s.state === 'retry'" name="refresh" :size="12" />
          <Icon v-else-if="s.state === 'done'" name="check" :size="12" />
          <Icon v-else name="x" :size="12" />
        </span>
        <span class="rv-name">{{ NODE_LABELS[s.node] }}</span>
      </div>
      <div v-if="runStatus === 'running'" class="rv-running-hint">
        <span class="spinner rv-spin"></span>执行中…
      </div>
    </div>

    <!-- 审批卡 -->
    <div v-if="approval" class="rv-approval">
      <div class="rv-ap-title"><Icon name="alert" :size="16" />需要你的确认</div>
      <div class="rv-ap-summary">{{ approval.summary }}</div>
      <div class="rv-ap-timer" :class="{ urgent: countdown <= 60 }">
        {{ countdown > 0 ? `剩余 ${Math.floor(countdown / 60)} 分 ${countdown % 60} 秒` : '已超时，请重新发起任务' }}
      </div>
      <div class="rv-ap-actions">
        <button class="btn btn-primary" @click="decide('approved')">批准并创建场次</button>
        <button class="btn btn-ghost" @click="decide('rejected')">不用了</button>
      </div>
    </div>

    <!-- 结果分区 -->
    <div v-if="output && runStatus === 'completed'" class="rv-result">
      <div v-if="output.analysis" class="rv-card">
        <div class="rv-card-title"><Icon name="list" :size="14" />简历分析</div>
        <div class="rv-card-body">
          <div v-if="output.analysis.target_position">目标岗位：{{ output.analysis.target_position }}</div>
          <div v-if="output.analysis.position_match">{{ output.analysis.position_match }}</div>
          <div v-if="output.analysis.strengths?.length" class="rv-list">
            <span v-for="t in output.analysis.strengths" :key="t" class="rv-tag plus">{{ t }}</span>
          </div>
          <div v-if="output.analysis.suggestions?.length" class="rv-suggest">
            <div v-for="t in output.analysis.suggestions" :key="t">· {{ t }}</div>
          </div>
        </div>
      </div>
      <div v-if="output.match" class="rv-card">
        <div class="rv-card-title"><Icon name="trophy" :size="14" />JD 匹配（{{ output.match.match_score ?? '-' }} 分）</div>
        <div class="rv-card-body">
          <div v-if="output.match.matched_keywords?.length" class="rv-list">
            <span v-for="t in output.match.matched_keywords" :key="t" class="rv-tag plus">{{ t }}</span>
          </div>
          <div v-if="output.match.missing_keywords?.length" class="rv-list">
            <span v-for="t in output.match.missing_keywords" :key="t" class="rv-tag minus">{{ t }}</span>
          </div>
          <div v-if="output.match.suggestions?.length" class="rv-suggest">
            <div v-for="t in output.match.suggestions" :key="t">· {{ t }}</div>
          </div>
        </div>
      </div>
      <div v-if="output.questions?.length" class="rv-card">
        <div class="rv-card-title"><Icon name="mic" :size="14" />定制面试题</div>
        <div class="rv-card-body">
          <ol class="rv-questions">
            <li v-for="(q, i) in output.questions" :key="i">{{ q }}</li>
          </ol>
          <div v-if="output.question_sources?.length" class="rv-cite">出题依据：{{ output.question_sources.join('、') }}</div>
        </div>
      </div>
      <div v-if="output.session_id" class="rv-session">
        <Icon name="check" :size="14" />
        面试场次已创建（#{{ output.session_id }}），可在「模拟面试」页继续
      </div>
      <button class="btn btn-ghost rv-again" @click="backToForm">再准备一个岗位</button>
    </div>

    <!-- 失败卡 -->
    <div v-if="runStatus === 'failed'" class="rv-fail">
      <div class="rv-fail-title"><Icon name="alert" :size="16" />任务失败</div>
      <div class="rv-fail-body">{{ error || '任务执行失败' }}</div>
      <div v-if="failReasons.length" class="rv-fail-reasons">
        <div v-for="(r, i) in failReasons" :key="i">· {{ r }}</div>
      </div>
      <button class="btn btn-primary" @click="retryFailed">从失败节点重试</button>
    </div>

    <!-- 进行中可放弃 -->
    <div v-if="hasRun && (runStatus === 'running' || runStatus === 'waiting_approval' || runStatus === 'aborted')" class="rv-toolbar">
      <button v-if="runStatus !== 'aborted'" class="btn btn-ghost" @click="abortRun">放弃任务</button>
      <button class="btn btn-ghost" @click="backToForm">返回发起页</button>
    </div>
  </div>
</template>

<style scoped>
.run-view {
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
  height: 100%;
  overflow-y: auto;
}
.rv-form,
.rv-approval,
.rv-result,
.rv-fail,
.rv-resume {
  background: var(--wash-primary);
  border: 1px solid var(--c-border);
  border-radius: var(--radius-lg);
  padding: var(--sp-4);
}
.rv-hi {
  font-size: var(--fs-lg);
  font-weight: 600;
  color: var(--c-text);
  display: flex;
  align-items: center;
  gap: var(--sp-2);
}
.rv-sub {
  font-size: var(--fs-sm);
  color: var(--c-text-2);
  margin: 6px 0 var(--sp-3);
}
.rv-row {
  display: flex;
  gap: var(--sp-2);
  margin-top: var(--sp-2);
}
/* 边框/聚焦态走全局 .input，这里只放开行内布局宽度 */
.rv-select {
  width: auto;
}
/* 开始按钮走全局 .btn/.btn-primary，这里只补占满剩余宽度 */
.rv-go {
  flex: 1;
}
.rv-resume {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--sp-2);
  font-size: var(--fs-sm);
  color: var(--c-text-2);
}
.rv-steps {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  flex-wrap: wrap;
  background: #fff;
  border: 1px solid var(--c-border);
  border-radius: var(--radius-lg);
  padding: var(--sp-3);
}
.rv-step {
  display: flex;
  align-items: center;
  gap: var(--sp-1);
  padding: var(--sp-1) var(--sp-2);
  border-radius: var(--radius-full);
  font-size: var(--fs-xs);
  background: var(--c-hover);
  color: var(--c-muted);
}
.rv-step.running {
  background: var(--wash-info);
  color: var(--c-info);
  font-weight: 600;
}
.rv-step.done {
  background: var(--wash-primary);
  color: var(--c-ok);
}
.rv-step.retry {
  background: var(--wash-warn);
  color: var(--c-warn);
}
.rv-step.failed {
  background: var(--wash-bad);
  color: var(--c-bad);
}
/* 步骤状态符：等待=空心圆点，运行=小号 spinner（蓝以免在蓝底胶囊里撞色） */
.rv-dot {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 12px;
  height: 12px;
  flex-shrink: 0;
}
.rv-wait-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  border: 2px solid var(--c-border-strong);
}
.rv-spin {
  width: 12px;
  height: 12px;
}
.rv-step.running .spinner {
  border-color: var(--c-info-bg);
  border-top-color: var(--c-info);
}
.rv-running-hint {
  font-size: var(--fs-xs);
  color: var(--c-faint);
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: var(--sp-1);
}
.rv-approval {
  border-color: var(--c-warn-bg);
  background: var(--wash-warn);
}
.rv-ap-title {
  font-weight: 600;
  font-size: var(--fs-md);
  color: var(--c-warn);
  display: flex;
  align-items: center;
  gap: var(--sp-2);
}
.rv-ap-summary {
  font-size: var(--fs-sm);
  color: var(--c-text);
  margin: var(--sp-2) 0;
}
.rv-ap-timer {
  font-size: var(--fs-xs);
  color: var(--c-warn);
  margin-bottom: var(--sp-2);
}
.rv-ap-timer.urgent {
  color: var(--c-bad);
  font-weight: 600;
}
.rv-ap-actions {
  display: flex;
  gap: var(--sp-2);
}
.rv-card {
  background: #fff;
  border: 1px solid var(--c-border);
  border-radius: var(--radius-lg);
  padding: var(--sp-3) var(--sp-4);
  margin-bottom: var(--sp-2);
}
.rv-card-title {
  font-weight: 600;
  font-size: var(--fs-md);
  color: var(--c-text);
  margin-bottom: var(--sp-2);
  display: flex;
  align-items: center;
  gap: var(--sp-2);
}
.rv-card-body {
  font-size: var(--fs-sm);
  color: var(--c-text-2);
  display: flex;
  flex-direction: column;
  gap: var(--sp-2);
}
.rv-list {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.rv-tag {
  padding: 2px var(--sp-2);
  border-radius: var(--radius-full);
  font-size: var(--fs-xs);
}
.rv-tag.plus {
  background: var(--wash-primary);
  color: var(--c-primary);
}
.rv-tag.minus {
  background: var(--wash-bad);
  color: var(--c-bad);
}
.rv-suggest {
  color: var(--c-muted);
}
.rv-questions {
  margin: 0;
  padding-left: var(--sp-4);
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.rv-cite {
  font-size: var(--fs-xs);
  color: var(--c-faint);
}
.rv-session {
  font-size: var(--fs-sm);
  color: var(--c-ok);
  font-weight: 600;
  display: inline-flex;
  align-items: center;
  gap: var(--sp-1);
}
.rv-again {
  align-self: flex-start;
}
.rv-fail {
  border-color: var(--c-bad-bg);
  background: var(--wash-bad);
}
.rv-fail-title {
  font-weight: 600;
  color: var(--c-bad);
  font-size: var(--fs-md);
  display: flex;
  align-items: center;
  gap: var(--sp-2);
}
.rv-fail-body {
  font-size: var(--fs-sm);
  color: var(--c-text);
  margin: var(--sp-2) 0;
}
.rv-fail-reasons {
  font-size: var(--fs-xs);
  color: var(--c-text-2);
  margin-bottom: var(--sp-2);
}
.rv-toolbar {
  display: flex;
  gap: var(--sp-2);
}
</style>
