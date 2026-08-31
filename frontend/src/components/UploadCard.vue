<script setup>
import { ref } from 'vue'

// 上传卡片：本地预检（类型/大小）→ POST /api/resumes → 把结果交给父页面展示
const emit = defineEmits(['uploaded'])

const fileInput = ref(null)
const uploading = ref(false)
const error = ref('')
const info = ref('')
const MAX_SIZE = 5 * 1024 * 1024 // 与后端一致的 5MB 上限，本地先拦一道省一次请求

function pick() {
  fileInput.value.click()
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
    const res = await fetch('/api/resumes', { method: 'POST', body: fd })
    const body = await res.json()
    if (!res.ok) {
      error.value = body.detail || '上传失败，请稍后再试'
      return
    }
    if (body.duplicate) info.value = '这份简历之前上传过，已直接调出历史解析结果'
    if (body.resume.parse_status !== 'success') error.value = body.resume.parse_error
    emit('uploaded', body.resume)
  } catch {
    error.value = '网络异常，请确认后端服务已启动'
  } finally {
    uploading.value = false
  }
}
</script>

<template>
  <section class="card">
    <h2>上传简历</h2>
    <p class="hint">仅支持文本型 PDF · 不超过 5MB / 5 页 · 扫描件暂不支持</p>
    <input ref="fileInput" type="file" accept=".pdf" hidden @change="onFileChosen" />
    <button :disabled="uploading" @click="pick">
      {{ uploading ? '上传解析中…' : '选择 PDF 文件' }}
    </button>
    <p v-if="error" class="msg error">{{ error }}</p>
    <p v-if="info" class="msg info">{{ info }}</p>
  </section>
</template>

<style scoped>
.card {
  background: #fff;
  border-radius: 12px;
  box-shadow: 0 1px 4px rgb(0 0 0 / 6%);
  padding: 20px;
}
h2 {
  margin: 0 0 6px;
  font-size: 17px;
}
.hint {
  margin: 0 0 14px;
  color: #6b7280;
  font-size: 13px;
}
button {
  border: none;
  border-radius: 8px;
  background: #10b981;
  color: #fff;
  font-size: 14px;
  padding: 9px 18px;
  cursor: pointer;
}
button:hover:not(:disabled) {
  background: #059669;
}
button:disabled {
  opacity: 0.6;
  cursor: wait;
}
.msg {
  border-radius: 8px;
  padding: 8px 12px;
  font-size: 13px;
}
.error {
  background: #fee2e2;
  color: #b91c1c;
}
.info {
  background: #d1fae5;
  color: #047857;
}
</style>
