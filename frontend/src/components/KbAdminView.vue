<script setup>
/* KbAdminView —— 管理端语料库管理：列出/删除全部知识库文档，上传可直接入预置语料。
   归属校验与配额由后端负责，前端预检只是体验层（扩展名/大小）。 */
import { onMounted, ref } from 'vue'
import { del, get, post } from '../api.js'
import Icon from './Icon.vue'
import { fmtDateTime } from '../utils.js'

const docs = ref([])
const loading = ref(false)
const error = ref('')
const uploadError = ref('')
const fileInput = ref(null)

const STATUS = {
  pending: { label: '排队中', cls: 'warn' },
  processing: { label: '入库中', cls: 'warn' },
  ready: { label: '就绪', cls: 'ok' },
  failed: { label: '失败', cls: 'bad' },
}

async function loadDocs() {
  loading.value = true
  error.value = ''
  try {
    docs.value = await get('/api/admin/kb/documents')
  } catch (e) {
    error.value = e.message || '语料库加载失败'
  } finally {
    loading.value = false
  }
}

const KB_ALLOWED_EXTENSIONS = ['txt', 'md', 'markdown', 'pdf'] // 与后端白名单一致
const KB_MAX_SIZE = 5 * 1024 * 1024

async function onFileSelected(event) {
  const file = event.target.files?.[0]
  if (!file) return
  // 前端预检只是体验层（免得用户等一次必然失败的上传）；真正的校验在后端
  const ext = file.name.split('.').pop().toLowerCase()
  if (!KB_ALLOWED_EXTENSIONS.includes(ext)) {
    uploadError.value = '只支持 txt / md / pdf 文件'
    event.target.value = ''
    return
  }
  if (file.size > KB_MAX_SIZE) {
    uploadError.value = '文件超过 5MB 限制'
    event.target.value = ''
    return
  }
  uploadError.value = ''
  const form = new FormData()
  form.append('file', file)
  try {
    await post('/api/admin/kb/documents', form)
    await loadDocs()
  } catch (e) {
    uploadError.value = e.message || '上传失败'
  } finally {
    event.target.value = ''
  }
}

async function removeDoc(doc) {
  if (!confirm(`删除「${doc.title}」？\n来源：${doc.source_type === 'preset' ? '系统预置' : doc.owner_email}\n删除后不可恢复（软删除，可审计）。`)) return
  try {
    await del(`/api/admin/kb/documents/${doc.id}`)
    await loadDocs()
  } catch (e) {
    error.value = e.message || '删除失败'
  }
}

onMounted(loadDocs)
</script>

<template>
  <section class="card">
    <div class="page-head">
      <div>
        <h2><Icon name="book" :size="18" /> 语料库管理</h2>
        <p class="ph-sub">管理全站语料：上传为系统预置（所有用户可见）、删除任意文档（含预置）</p>
      </div>
      <div class="ph-actions">
        <button class="btn btn-primary" @click="fileInput.click()">
          上传预置文档（txt/md/pdf）
        </button>
        <input ref="fileInput" type="file" accept=".txt,.md,.markdown,.pdf" hidden @change="onFileSelected" />
      </div>
    </div>

    <p v-if="error" class="msg error">{{ error }}</p>
    <p v-if="uploadError" class="msg error">{{ uploadError }}</p>

    <div v-if="loading" class="loading-row"><span class="spinner"></span>加载中…</div>

    <template v-else>
      <div v-if="!docs.length" class="empty-state">
        <span class="es-icon"><Icon name="inbox" :size="24" /></span>
        <p class="es-text">还没有任何文档。点击上方按钮上传第一篇预置语料。</p>
      </div>

      <div v-else class="tbl-wrap">
        <table class="tbl">
          <thead>
            <tr>
              <th>ID</th>
              <th>标题</th>
              <th>来源</th>
              <th>所有者</th>
              <th>块数</th>
              <th>状态</th>
              <th>上传时间</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="doc in docs" :key="doc.id">
              <td>{{ doc.id }}</td>
              <td class="doc-title">{{ doc.title }}</td>
              <td>
                <span class="badge" :class="{ ok: doc.source_type === 'preset' }">
                  {{ doc.source_type === 'preset' ? '预置' : '上传' }}
                </span>
              </td>
              <td class="owner">{{ doc.owner_email }}</td>
              <td>{{ doc.chunk_count != null ? doc.chunk_count : '-' }}</td>
              <td>
                <span class="badge" :class="STATUS[doc.status]?.cls">
                  {{ STATUS[doc.status]?.label ?? doc.status }}
                </span>
              </td>
              <td class="time">{{ fmtDateTime(doc.created_at) }}</td>
              <td>
                <button class="icon-btn danger" aria-label="删除" title="删除" @click="removeDoc(doc)"><Icon name="trash" :size="14" /></button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </section>
</template>

<style scoped>
.page-head {
  margin-bottom: var(--sp-3);
}
/* 表格壳/表头/行 hover 由全局 .tbl-wrap + .tbl 提供，这里只留列宽补充 */
.tbl-wrap {
  margin-top: var(--sp-2);
}
.doc-title {
  font-weight: 600;
  color: var(--c-text);
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.owner {
  font-size: var(--fs-xs);
  color: var(--c-text-2);
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.time {
  font-size: var(--fs-xs);
  color: var(--c-faint);
  white-space: nowrap;
}
</style>
