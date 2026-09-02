<script setup>
// 解析历史列表：数据由父页面传入，点击某条时把 id 交给父页面去加载详情
defineProps({
  resumes: { type: Array, default: () => [] },
  currentId: { type: Number, default: null },
})
const emit = defineEmits(['select'])

const STATUS = {
  success: { label: '成功', cls: 'ok' },
  unsupported: { label: '扫描件', cls: 'warn' },
  failed: { label: '失败', cls: 'bad' },
  pending: { label: '解析中', cls: 'warn' },
}
</script>

<template>
  <section class="card">
    <h2>解析历史</h2>
    <p v-if="!resumes.length" class="empty">
      <span class="empty-icon">🗂️</span><br />
      还没有上传记录
    </p>
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
        <span class="time">{{ new Date(r.created_at).toLocaleString() }}</span>
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
  gap: 12px;
  padding: 11px 14px;
  border-radius: var(--radius-md);
  border: 1px solid transparent;
  cursor: pointer;
  transition: all 0.18s ease;
}
.ritem:hover {
  background: #f6fdf9;
  border-color: #d1fae5;
  transform: translateX(3px);
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
  box-shadow: 0 0 0 3px rgb(16 185 129 / 15%);
}
.dot.warn {
  background: #f59e0b;
  box-shadow: 0 0 0 3px rgb(245 158 11 / 15%);
}
.dot.bad {
  background: #ef4444;
  box-shadow: 0 0 0 3px rgb(239 68 68 / 15%);
}
.name {
  flex: 1;
  font-size: 13.5px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.time {
  color: var(--c-faint);
  font-size: 12px;
  white-space: nowrap;
}
.empty-icon {
  font-size: 26px;
}
</style>
