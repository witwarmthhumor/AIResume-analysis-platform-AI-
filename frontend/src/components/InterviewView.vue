<script setup>
/* InterviewView —— 模拟面试模块（v4.1 A4 自 HomeView/ProfileView 拆出收敛）。
   三区：继续上次会话（GET /api/interviews/last）· 选简历开新面试 · 面试历史（含三态）。
   进行中的场次经 start 接口的服务端"恢复"语义接上（同简历未结束会话直接续聊）；
   已结束场次直接打开复盘报告。pendingResumeId 来自 ResumeView 的「去模拟面试」。 */
import { onMounted, ref } from 'vue'
import { get } from '../api.js'
import InterviewChat from './InterviewChat.vue'
import InterviewReport from './InterviewReport.vue'

const emit = defineEmits(['navigate', 'pending-consumed'])
const props = defineProps({
  pendingResumeId: { type: Number, default: null },
  pendingBankId: { type: Number, default: null }, // v4.2：从题库页跳转时预选的题库
})

const resumes = ref([]) // 仅 parse 成功的简历可选
const resumesState = ref('loading') // loading / ready / error
const banks = ref([]) // v4.2 我的题库（可选加载）
const banksState = ref('loading') // loading / ready / empty / error
const selectedBankId = ref(null) // 开局是否带题库（题库驱动出题，省 LLM 额度）
const activeResume = ref(null) // 开聊中的简历（挂 InterviewChat）
const lastSession = ref(null) // 最近一场（可能进行中/已结束）
const lastState = ref('loading') // loading / ready / none / error
const history = ref([])
const histState = ref('loading') // loading / ready / empty / error
const reportView = ref(null) // { report, sessionId } 打开复盘报告

async function loadResumes() {
  resumesState.value = 'loading'
  try {
    const all = await get('/api/resumes')
    resumes.value = all.filter((r) => r.parse_status === 'success')
    resumesState.value = 'ready'
  } catch {
    resumesState.value = 'error'
  }
}

async function loadBanks() {
  banksState.value = 'loading'
  try {
    banks.value = await get('/api/question-banks')
    banksState.value = banks.value.length ? 'ready' : 'empty'
  } catch {
    banksState.value = 'error'
  }
}

async function loadHistory() {
  histState.value = 'loading'
  try {
    const res = await get('/api/interviews/scores?limit=10')
    history.value = res.items || []
    histState.value = history.value.length ? 'ready' : 'empty'
  } catch {
    histState.value = 'error'
  }
}

async function loadLast() {
  lastState.value = 'loading'
  try {
    lastSession.value = await get('/api/interviews/last')
    lastState.value = 'ready'
  } catch (e) {
    lastSession.value = null
    // api.js 抛 {code,message}：not_found = 一场都没有（正常空态）；其余按错误展示可重试
    lastState.value = e?.code === 'not_found' ? 'none' : 'error'
  }
}

async function startWith(resume) {
  activeResume.value = resume
}

async function continueLast() {
  const session = lastSession.value
  if (!session) return
  try {
    const resume = await get(`/api/resumes/${session.resume_id}`)
    if (session.status === 'in_progress') {
      activeResume.value = resume // Chat 挂载时 start 接口自动续上未结束场次
    } else {
      openReport(session.id) // 已结束 → 直接看复盘报告
    }
  } catch {
    /* 简历已被删除等异常：给可操作的提示而不是静默 */
    lastState.value = 'error'
  }
}

async function openReport(sessionId) {
  try {
    const session = await get(`/api/interviews/${sessionId}`)
    if (session.final_report) reportView.value = { report: session.final_report, sessionId: session.id }
  } catch {
    /* 报告拉取失败保持当前态，可再次点击 */
  }
}

function closeChat() {
  activeResume.value = null
  loadHistory()
  loadLast()
}

onMounted(async () => {
  await Promise.all([loadResumes(), loadBanks(), loadHistory(), loadLast()])
  if (props.pendingBankId) selectedBankId.value = props.pendingBankId // 题库页跳转预选
  if (props.pendingResumeId) {
    // 从「简历评估 → 去模拟面试」跳转而来：自动开聊
    try {
      const resume = await get(`/api/resumes/${props.pendingResumeId}`)
      if (resume.parse_status === 'success') await startWith(resume)
    } catch {
      /* 简历不存在/未解析：落到手动选择列表 */
    } finally {
      emit('pending-consumed')
    }
  }
})
</script>

<template>
  <h2 class="page-title">模拟面试</h2>

  <!-- 复盘报告（全视图展示） -->
  <template v-if="reportView">
    <button class="btn btn-ghost back-btn" @click="reportView = null">← 返回面试列表</button>
    <InterviewReport :key="reportView.sessionId" :report="reportView.report" :session-id="reportView.sessionId" />
  </template>

  <!-- 对话进行中 -->
  <InterviewChat
    v-else-if="activeResume"
    :key="activeResume.id"
    :resume="activeResume"
    :bank-id="selectedBankId"
    @close="closeChat"
  />

  <template v-else>
    <!-- ① 继续上次会话 -->
    <section v-if="lastState === 'ready' && lastSession" class="card continue-card">
      <div class="continue-main">
        <span class="live-dot" :class="{ on: lastSession.status === 'in_progress' }"></span>
        <div class="continue-text">
          <b>{{ lastSession.status === 'in_progress' ? '上次面试还没结束' : '最近一场面试' }}</b>
          <span class="continue-meta">
            {{ lastSession.turn_count }} 轮 · {{ new Date(lastSession.messages.at(-1)?.created_at || Date.now()).toLocaleString() }}
          </span>
        </div>
      </div>
      <button
        v-if="lastSession.status === 'in_progress'"
        class="btn btn-primary"
        @click="continueLast"
      >继续上次会话 →</button>
      <button v-else class="btn btn-ghost" @click="openReport(lastSession.id)">查看复盘报告</button>
    </section>
    <p v-else-if="lastState === 'error'" class="msg error">
      上次会话状态加载失败 <button class="link-btn" @click="loadLast">重试</button>
    </p>

    <!-- ② 选简历开新面试 -->
    <section class="card">
      <div class="section-head"><h3>开一场新面试</h3><span class="muted">选择简历，可选加载题库</span></div>
      <div v-if="banksState === 'ready'" class="bank-select-row">
        <span class="muted">面试题库</span>
        <select v-model="selectedBankId" class="bank-select">
          <option :value="null">不使用题库（AI 自由出题）</option>
          <option v-for="b in banks" :key="b.id" :value="b.id">{{ b.title }}（{{ b.question_count }} 题）</option>
        </select>
      </div>
      <p v-if="resumesState === 'error'" class="msg error">
        简历列表加载失败 <button class="link-btn" @click="loadResumes">重试</button>
      </p>
      <p v-else-if="resumesState === 'loading'" class="msg muted">加载中…</p>
      <div v-else-if="!resumes.length" class="empty-box">
        <p class="msg muted">还没有解析成功的简历。</p>
        <button class="btn btn-primary" @click="emit('navigate', 'resume')">去简历评估上传</button>
      </div>
      <ul v-else class="resume-pick">
        <li v-for="r in resumes" :key="r.id">
          <span class="pick-name">{{ r.filename }}</span>
          <button class="btn btn-primary btn-sm" @click="startWith(r)">开始面试</button>
        </li>
      </ul>
    </section>

    <!-- ③ 面试历史（三态） -->
    <section class="card">
      <div class="section-head"><h3>面试历史</h3><span v-if="histState === 'ready'" class="muted">最近 {{ history.length }} 场</span></div>
      <p v-if="histState === 'loading'" class="msg muted">加载中…</p>
      <p v-else-if="histState === 'error'" class="msg error">
        历史记录加载失败 <button class="link-btn" @click="loadHistory">重试</button>
      </p>
      <p v-else-if="histState === 'empty'" class="msg muted">还没有已完成的面试——完成一场后会在这里看到四维评分复盘。</p>
      <ul v-else class="history-list">
        <li v-for="h in history" :key="h.id">
          <div class="hist-info">
            <span class="hist-time">{{ new Date(h.created_at).toLocaleString() }}</span>
            <span class="muted">{{ h.turn_count }} 轮 · 综合 {{ h.scores?.overall ?? '-' }} 分</span>
          </div>
          <button class="btn btn-ghost btn-sm" @click="openReport(h.id)">查看报告</button>
        </li>
      </ul>
    </section>
  </template>
</template>

<style scoped>
.page-title {
  margin: 0;
  font-size: 18px;
}
.back-btn {
  align-self: flex-start;
}
.continue-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  border-left: 3px solid var(--c-primary);
}
.continue-main {
  display: flex;
  align-items: center;
  gap: 10px;
}
.live-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: #cbd5e1;
  flex-shrink: 0;
}
.live-dot.on {
  background: #10b981;
  box-shadow: 0 0 0 4px rgb(16 185 129 / 18%);
}
.continue-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: 13.5px;
}
.continue-meta {
  font-size: 12px;
  color: var(--c-muted);
}
.section-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 8px;
}
.section-head h3 {
  margin: 0;
  font-size: 14.5px;
}
.muted {
  color: var(--c-muted);
  font-size: 12px;
}
.empty-box {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 10px;
}
.resume-pick,
.history-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
}
.resume-pick li,
.history-list li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 4px;
  border-bottom: 1px solid var(--c-border);
}
.resume-pick li:last-child,
.history-list li:last-child {
  border-bottom: 0;
}
.pick-name {
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.hist-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: 12.5px;
}
.hist-time {
  font-weight: 600;
}
.btn-sm {
  padding: 6px 12px;
  font-size: 12px;
  flex-shrink: 0;
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
.bank-select-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 10px;
}
.bank-select {
  min-width: 280px;
  border: 1.5px solid var(--c-border);
  border-radius: 10px;
  padding: 8px 12px;
  font-size: 12.5px;
  font-family: inherit;
  background: #fff;
}
</style>
