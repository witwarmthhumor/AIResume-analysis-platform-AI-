<script setup>
/* KbAdminView —— 管理端语料库管理：列出/删除全部知识库文档，上传可直接入预置语料。
   归属校验与配额由后端负责，前端预检只是体验层（扩展名/大小）。 */
import { onMounted, ref } from 'vue'
import { del, get, post } from '../api.js'

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
    <div class="head">
      <div>
        <h2>📚 语料库管理</h2>
        <p class="sub">管理全站语料：上传为系统预置（所有用户可见）、删除任意文档（含预置）</p>
      </div>
      <button class="btn btn-primary" @click="fileInput.click()">
        上传预置文档（txt/md/pdf）
      </button>
      <input ref="fileInput" type="file" accept=".txt,.md,.markdown,.pdf" hidden @change="onFileSelected" />
    </div>

    <p v-if="error" class="msg error">{{ error }}</p>
    <p v-if="uploadError" class="msg error">{{ uploadError }}</p>

    <div v-if="loading" class="loading">加载中…</div>

    <template v-else>
      <div v-if="!docs.length" class="empty">
        还没有任何文档。点击上方按钮上传第一篇预置语料。
      </div>

      <table v-else class="tbl">
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
              <span class="badge" :class="doc.source_type === 'preset' ? 'ok' : 'gray'">
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
            <td class="time">{{ new Date(doc.created_at).toLocaleString() }}</td>
            <td>
              <button class="btn btn-ghost del-btn" @click="removeDoc(doc)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
    </template>
  </section>
</template>

<style scoped>
.head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 12px;
}
.head h2 {
  font-size: 16px;
  margin: 0 0 4px;
}
.sub {
  font-size: 12.5px;
  color: var(--c-faint, #9ca3af);
  margin: 0;
}
.loading {
  color: var(--c-faint, #9ca3af);
  font-size: 13px;
  padding: 20px 0;
  text-align: center;
}
.empty {
  color: var(--c-faint, #9ca3af);
  font-size: 13px;
  padding: 32px 0;
  text-align: center;
  background: var(--c-bg-soft, #f9fafb);
  border-radius: 10px;
}
.tbl {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
  margin: 8px 0;
}
.tbl th,
.tbl td {
  text-align: left;
  padding: 9px 8px;
  border-bottom: 1px solid var(--c-border, #e5e7eb);
  vertical-align: middle;
}
.tbl th {
  color: var(--c-muted, #6b7280);
  font-weight: 600;
  font-size: 12px;
  white-space: nowrap;
}
.tbl tbody tr:hover {
  background: rgb(16 185 129 / 3%);
}
.doc-title {
  font-weight: 600;
  color: var(--c-text, #1a1b1c);
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.owner {
  font-size: 12px;
  color: var(--c-text-2, #374151);
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.time {
  font-size: 12px;
  color: var(--c-faint, #9ca3af);
  white-space: nowrap;
}
.del-btn {
  padding: 4px 10px;
  font-size: 12px;
  color: #ef4444;
}
.del-btn:hover {
  background: rgb(239 68 68 / 8%);
}
.badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 6px;
  font-size: 11px;
  font-weight: 600;
}
.badge.ok {
  background: #dcfce7;
  color: #15803d;
}
.badge.warn {
  background: #fef3c7;
  color: #b45309;
}
.badge.bad {
  background: #fee2e2;
  color: #b91c1c;
}
.badge.gray {
  background: #f3f4f6;
  color: #6b7280;
}
</style>
