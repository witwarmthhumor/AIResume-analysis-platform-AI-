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
    <p v-if="!resumes.length" class="empty">还没有上传记录</p>
    <ul v-else>
      <li
        v-for="r in resumes"
        :key="r.id"
        :class="{ active: r.id === currentId }"
        @click="emit('select', r.id)"
      >
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
.card {
  background: #fff;
  border-radius: 12px;
  box-shadow: 0 1px 4px rgb(0 0 0 / 6%);
  padding: 20px;
}
h2 {
  margin: 0 0 10px;
  font-size: 17px;
}
.empty {
  color: #9ca3af;
  font-size: 13px;
  margin: 0;
}
ul {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
li {
  display: flex;
  align-items: center;
  gap: 10px;
  border-radius: 8px;
  padding: 8px 10px;
  cursor: pointer;
}
li:hover,
li.active {
  background: #f0fdf4;
}
.name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 14px;
}
.badge {
  border-radius: 999px;
  padding: 2px 10px;
  font-size: 12px;
  white-space: nowrap;
}
.ok {
  background: #d1fae5;
  color: #047857;
}
.warn {
  background: #fef3c7;
  color: #92400e;
}
.bad {
  background: #fee2e2;
  color: #b91c1c;
}
.time {
  color: #9ca3af;
  font-size: 12px;
  white-space: nowrap;
}
</style>
