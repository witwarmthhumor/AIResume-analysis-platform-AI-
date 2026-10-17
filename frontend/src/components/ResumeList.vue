<script setup>
// 解析历史列表：数据由父页面传入，点击某条时把 id 交给父页面去加载详情
import Icon from './Icon.vue'
import { ref } from 'vue'
import { del } from '../api.js'

const props = defineProps({
  resumes: { type: Array, default: () => [] },
  currentId: { type: Number, default: null },
})
const emit = defineEmits(['select', 'deleted'])
const deletingId = ref(null)
const delError = ref('') // 删除失败提示（原 alert 的展示层替代，显示在卡片顶部）

import { RESUME_STATUS as STATUS, fmtDateTime } from '../utils.js' // 与 HomeView 同一份映射

async function remove(id, e) {
  e.stopPropagation()
  if (!confirm('确定删除这份简历吗？删除后不可恢复（仅从列表移除，不删文件）。')) return
  deletingId.value = id
  delError.value = ''
  try {
    await del(`/api/resumes/${id}`)
    emit('deleted', id)
  } catch {
    delError.value = '删除失败，请稍后重试'
  } finally {
    deletingId.value = null
  }
}
</script>

<template>
  <section class="card">
    <h2>解析历史</h2>
    <p v-if="delError" class="msg error">{{ delError }}</p>
    <div v-if="!resumes.length" class="empty-state">
      <span class="es-icon"><Icon name="file" :size="24" /></span>
      <p class="es-text">还没有上传记录——上传一份简历后，这里会显示解析历史</p>
    </div>
    <ul v-else class="rlist">
      <li
        v-for="r in resumes"
        :key="r.id"
        :class="['ritem', { active: r.id === currentId }]"
        @click="emit('select', r.id)"
      >
        <span class="dot" :class="STATUS[r.parse_status]?.cls"></span>
        <span class="name">{{ r.filename }}</span>
        <span class="badge" :class="STATUS[r.parse_status]?.cls">
          {{ STATUS[r.parse_status]?.label ?? r.parse_status }}
        </span>
        <span class="time">{{ fmtDateTime(r.created_at) }}</span>
        <button
          class="icon-btn danger"
          :disabled="deletingId === r.id"
          aria-label="删除"
          title="删除"
          @click="remove(r.id, $event)"
        >
          <Icon name="trash" :size="14" />
        </button>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.rlist {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.ritem {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  padding: var(--sp-3);
  border-radius: var(--radius-md);
  border: 1px solid transparent;
  cursor: pointer;
  transition:
    background var(--dur-fast) var(--ease),
    border-color var(--dur-fast) var(--ease);
}
.ritem:hover {
  background: var(--c-hover);
  border-color: var(--c-primary-border);
}
.ritem.active {
  background: var(--c-primary-light);
  border-color: var(--c-primary-border);
}
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}
.dot.ok {
  background: var(--c-primary);
}
.dot.warn {
  background: var(--c-warn);
}
.dot.bad {
  background: var(--c-bad);
}
.name {
  flex: 1;
  font-size: var(--fs-sm);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.time {
  color: var(--c-faint);
  font-size: var(--fs-xs);
  white-space: nowrap;
}
/* 删除按钮统一走全局 .icon-btn.danger（常显，hover 变红）；这里只补删除进行中的禁用态 */
.icon-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* —— 响应式（v4.2.1）—— */
@media (max-width: 640px) {
  .ritem {
    flex-wrap: wrap;
  }
  .time {
    display: none;
  }
}

</style>
