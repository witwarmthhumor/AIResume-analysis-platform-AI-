<script setup>
import { ref } from 'vue'
import { request } from '../api.js'

// 上传卡片：拖拽/点击选文件 → 本地预检（类型/大小）→ POST /api/resumes → 交给父页面
const emit = defineEmits(['uploaded'])

const fileInput = ref(null)
const uploading = ref(false)
const dragging = ref(false)
const error = ref('')
const info = ref('')
const MAX_SIZE = 5 * 1024 * 1024 // 与后端一致的 5MB 上限，本地先拦一道省一次请求

function onDrop(e) {
  dragging.value = false
  const file = e.dataTransfer?.files?.[0]
  if (file) doUpload(file)
}

function onFileChosen(e) {
  const file = e.target.files?.[0]
  e.target.value = '' // 清空选择，保证连续两次选同一个文件也能触发 change
  if (file) doUpload(file)
}

async function doUpload(file) {
  error.value = ''
  info.value = ''
  if (!file.name.toLowerCase().endsWith('.pdf')) {
    error.value = '只支持 PDF 文件，请上传 PDF 格式的简历'
    return
  }
  if (file.size > MAX_SIZE) {
    error.value = '文件超过 5MB 限制，请压缩后重新上传'
    return
  }
  uploading.value = true
  try {
    const fd = new FormData()
    fd.append('file', file)
    const body = await request('POST', '/api/resumes', fd)
    if (body.duplicate) info.value = '这份简历之前上传过，已直接调出历史解析结果'
    if (body.resume.parse_status !== 'success') error.value = body.resume.parse_error
    emit('uploaded', body.resume)
  } catch (e) {
    error.value = e.message || '网络异常，请确认后端服务已启动'
  } finally {
    uploading.value = false
  }
}
</script>

<template>
  <section class="card">
    <h2>上传简历</h2>
    <input ref="fileInput" type="file" accept=".pdf" hidden @change="onFileChosen" />
    <div
      class="drop"
      :class="{ over: dragging }"
      role="button"
      tabindex="0"
      @click="fileInput.click()"
      @keydown.enter="fileInput.click()"
      @dragover.prevent="dragging = true"
      @dragleave.prevent="dragging = false"
      @drop.prevent="onDrop"
    >
      <div class="icon">📄</div>
      <p class="t1">
        点击选择 或 <em>拖拽 PDF 简历</em> 到此处
        <span v-if="uploading"> · 上传解析中…</span>
      </p>
      <p class="t2">仅支持文本型 PDF · 不超过 5MB / 5 页 · 扫描件暂不支持</p>
    </div>
    <p v-if="error" class="msg error">{{ error }}</p>
    <p v-if="info" class="msg info">{{ info }}</p>
  </section>
</template>

<style scoped>
.drop {
  border: 2px dashed var(--c-primary-border);
  border-radius: 12px;
  padding: 26px 20px;
  text-align: center;
  background: linear-gradient(180deg, #f0fdf9, #fff);
  cursor: pointer;
  transition: all 0.2s ease;
}
.drop:hover,
.drop.over {
  border-color: var(--c-primary);
  background: linear-gradient(180deg, #ecfdf5, #fff);
  transform: translateY(-1px);
}
.icon {
  width: 52px;
  height: 52px;
  margin: 0 auto 10px;
  border-radius: 16px;
  background: linear-gradient(135deg, #ecfdf5, #d1fae5);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 24px;
}
.t1 {
  font-size: 14.5px;
  font-weight: 600;
  margin: 0;
}
.t1 em {
  color: var(--c-primary-dark);
  font-style: normal;
}
.t2 {
  font-size: 12.5px;
  color: var(--c-faint);
  margin: 6px 0 0;
}
</style>
