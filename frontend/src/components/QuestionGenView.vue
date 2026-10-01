<script setup>
/* QuestionGenView —— 面试题生成模块（v4.2，参考页面形态对齐）。
   选一份解析成功的简历 → 生成定制化面试题（一次 LLM 调用）→ 题目按类别分组展示；
   我的题库列表可进入模拟面试加载该题库，或删除。 */
import { computed, onMounted, ref } from 'vue'
import { del, get, post } from '../api.js'
import Icon from './Icon.vue'

const emit = defineEmits(['navigate'])

const resumes = ref([])
const resumesState = ref('loading') // loading / ready / error
const selectedResumeId = ref(null)
const generating = ref(false)
const currentBank = ref(null) // 刚生成/点开的题库详情
const banks = ref([])
const banksState = ref('loading') // loading / ready / empty / error
const error = ref('')

const groupedQuestions = computed(() => {
  const qs = currentBank.value?.questions || []
  const groups = {}
  for (const q of qs) {
    const cat = q.category || '题目'
    ;(groups[cat] ||= []).push(q)
  }
  return groups
})

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

async function generate() {
  if (!selectedResumeId.value || generating.value) return
  generating.value = true
  error.value = ''
  try {
    currentBank.value = await post('/api/question-banks', {
      resume_id: selectedResumeId.value,
    })
    await loadBanks()
  } catch (e) {
    error.value = e.message || '生成失败，请重试'
  } finally {
    generating.value = false
  }
}

async function openBank(bankId) {
  error.value = ''
  try {
    currentBank.value = await get(`/api/question-banks/${bankId}`)
  } catch (e) {
    error.value = e.message || '题库加载失败'
  }
}

async function removeBank(bankId) {
  try {
    await del(`/api/question-banks/${bankId}`)
    if (currentBank.value?.id === bankId) currentBank.value = null
    await loadBanks()
  } catch (e) {
    error.value = e.message || '删除失败'
  }
}

function goInterview(bank) {
  // 带上题库与对应简历进模拟面试（App 负责传递）
  emit('navigate', 'interview', bank.resume_id, bank.id)
}

onMounted(() => {
  loadResumes()
  loadBanks()
})
</script>

<template>
  <section class="qg">
    <div class="upload-card card">
      <div class="upload-hint">
        <span class="upload-icon"><Icon name="pen" :size="30" /></span>
        <p class="upload-title">基于简历内容生成定制化面试题</p>
        <p class="upload-sub">选择一份解析成功的简历，AI 生成 10~20 道题并保存为题库</p>
      </div>

      <p v-if="resumesState === 'error'" class="msg error">
        简历列表加载失败 <button class="link-btn" @click="loadResumes">重试</button>
      </p>
      <p v-else-if="resumesState === 'loading'" class="msg muted">加载中…</p>
      <div v-else-if="!resumes.length" class="empty-state">
        <p class="es-text">还没有解析成功的简历——先去简历评估上传一份</p>
        <button class="btn btn-primary" @click="emit('navigate', 'resume')">去简历评估上传</button>
      </div>
      <div v-else class="gen-row">
        <select v-model="selectedResumeId" class="resume-select">
          <option :value="null" disabled>选择简历…</option>
          <option v-for="r in resumes" :key="r.id" :value="r.id">{{ r.filename }}</option>
        </select>
        <button class="btn btn-primary" :disabled="generating || !selectedResumeId" @click="generate">
          {{ generating ? '生成中…（约 10~30 秒）' : '生成面试题' }}
        </button>
      </div>
      <p v-if="error" class="msg error">{{ error }}</p>
    </div>

    <!-- 当前题库（按类别分组） -->
    <section v-if="currentBank" class="card bank-detail">
      <div class="bank-head">
        <h3>{{ currentBank.title }}（{{ currentBank.question_count }} 题）</h3>
        <button
          v-if="currentBank.resume_id"
          class="btn btn-ghost btn-sm"
          @click="emit('navigate', 'interview', currentBank.resume_id, currentBank.id)"
        >拿这套题去模拟面试</button>
      </div>
      <div v-for="(items, cat) in groupedQuestions" :key="cat" class="cat-block">
        <div class="cat-title">{{ cat }}</div>
        <ol class="q-list">
          <li v-for="(q, i) in items" :key="i">
            <span class="q-text">{{ q.question }}</span>
            <span class="q-diff" :title="`难度 ${q.difficulty}/5`">{{ '★'.repeat(q.difficulty) }}{{ '☆'.repeat(5 - q.difficulty) }}</span>
          </li>
        </ol>
      </div>
    </section>

    <!-- 我的题库 -->
    <section class="card">
      <div class="bank-head"><h3>我的题库</h3></div>
      <p v-if="banksState === 'loading'" class="msg muted">加载中…</p>
      <p v-else-if="banksState === 'error'" class="msg error">
        题库列表加载失败 <button class="link-btn" @click="loadBanks">重试</button>
      </p>
      <div v-else-if="banksState === 'empty'" class="empty-state">
        <span class="es-icon"><Icon name="book" :size="24" /></span>
        <p class="es-text">还没有题库——选一份简历点「生成面试题」</p>
      </div>
      <ul v-else class="bank-list">
        <li v-for="b in banks" :key="b.id">
          <div class="bank-info">
            <span class="bank-title">{{ b.title }}</span>
            <span class="bank-meta">{{ b.question_count }} 题 · {{ new Date(b.created_at).toLocaleString() }}</span>
          </div>
          <div class="bank-actions">
            <button class="btn btn-ghost btn-sm" @click="openBank(b.id)">查看</button>
            <button v-if="b.resume_id" class="btn btn-primary btn-sm" @click="goInterview(b)">去面试</button>
            <button class="btn btn-ghost btn-sm danger" @click="removeBank(b.id)">删除</button>
          </div>
        </li>
      </ul>
    </section>
  </section>
</template>

<style scoped>
.qg {
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.upload-card {
  text-align: center;
  padding: 30px 24px;
}
.upload-icon {
  font-size: 34px;
}
.upload-title {
  font-size: 15px;
  font-weight: 700;
  margin: 10px 0 4px;
}
.upload-sub {
  font-size: 12px;
  color: var(--c-muted);
  margin: 0 0 14px;
}
.gen-row {
  display: flex;
  gap: 12px;
  justify-content: center;
  align-items: center;
  flex-wrap: wrap;
}
.resume-select {
  min-width: 260px;
  border: 1.5px solid var(--c-border);
  border-radius: 10px;
  padding: 9px 12px;
  font-size: 13px;
  font-family: inherit;
  background: #fff;
}
.bank-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 10px;
}
.bank-head h3 {
  margin: 0;
  font-size: 14.5px;
}
.cat-block {
  margin-bottom: 14px;
}
.cat-title {
  font-size: 12.5px;
  font-weight: 700;
  color: var(--c-primary-dark);
  margin-bottom: 6px;
}
.q-list {
  margin: 0;
  padding-left: 20px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.q-list li {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  font-size: 13px;
  line-height: 1.7;
}
.q-diff {
  color: #f59e0b;
  font-size: 11px;
  flex-shrink: 0;
  letter-spacing: 1px;
  align-self: center;
}
.bank-list {
  list-style: none;
  margin: 0;
  padding: 0;
}
.bank-list li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 4px;
  border-bottom: 1px solid var(--c-border);
}
.bank-list li:last-child {
  border-bottom: 0;
}
.bank-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.bank-title {
  font-size: 13px;
  font-weight: 600;
}
.bank-meta {
  font-size: 11.5px;
  color: var(--c-muted);
}
.bank-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}
.btn-sm {
  padding: 6px 12px;
  font-size: 12px;
}
.danger:hover {
  color: #dc2626;
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
.muted {
  color: var(--c-muted);
  font-size: 12.5px;
}
.empty-box {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
}

/* —— 响应式（v4.2.1）—— */
@media (max-width: 768px) {
  .gen-row {
    flex-direction: column;
    align-items: stretch;
  }
  .resume-select {
    width: 100%;
    min-width: 0;
  }
  .bank-list li {
    flex-wrap: wrap;
  }
  .bank-actions {
    width: 100%;
    justify-content: flex-end;
  }
  .bank-head {
    flex-wrap: wrap;
    gap: 6px;
  }
}

</style>
