<script setup>
import { computed } from 'vue'

const props = defineProps({ report: { type: Object, required: true } })

const SCORES = computed(() => [
  ['技术深度', props.report.technical_depth],
  ['表达结构', props.report.communication],
  ['项目真实性', props.report.project_authenticity],
  ['整体表现', props.report.overall],
])

// ≥8 绿 / 5~7 琥珀 / <5 红
function tone(v) {
  if (v == null) return 'green'
  if (v >= 8) return 'green'
  if (v >= 5) return 'amber'
  return 'red'
}
</script>

<template>
  <section class="card report">
    <h2>面试结束评价</h2>
    <div class="scores">
      <div v-for="item in SCORES" :key="item[0]" class="score-row">
        <span class="lab">{{ item[0] }}</span>
        <div class="bar" :class="tone(item[1])">
          <i :style="{ width: `${Math.max(0, Math.min(100, (item[1] ?? 0) * 10))}%` }"></i>
        </div>
        <span class="val" :class="tone(item[1])">{{ item[1] ?? '-' }}/10</span>
      </div>
    </div>

    <h3 class="sec-title">📝 整体评价</h3>
    <p class="summary">{{ report.summary }}</p>

    <h3 class="sec-title">✅ 表现亮点</h3>
    <ul class="flist good">
      <li v-for="(item, i) in report.highlights || []" :key="i">{{ item }}</li>
    </ul>

    <h3 class="sec-title">🚀 改进建议</h3>
    <ul class="flist todo">
      <li v-for="(item, i) in report.improvements || []" :key="i">{{ item }}</li>
    </ul>
  </section>
</template>

<style scoped>
.report h2 {
  margin-bottom: 14px;
}
.scores {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin: 4px 0 6px;
}
.score-row {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 13px;
}
.lab {
  width: 76px;
  color: var(--c-muted);
  flex-shrink: 0;
}
.bar {
  flex: 1;
  height: 9px;
  border-radius: 999px;
  background: #f1f5f9;
  overflow: hidden;
}
.bar > i {
  display: block;
  height: 100%;
  border-radius: 999px;
  transition: width 0.6s ease;
}
.bar.green > i {
  background: linear-gradient(90deg, #34d399, #059669);
}
.bar.amber > i {
  background: linear-gradient(90deg, #fbbf24, #d97706);
}
.bar.red > i {
  background: linear-gradient(90deg, #f87171, #dc2626);
}
.val {
  width: 46px;
  text-align: right;
  font-weight: 700;
  font-size: 13.5px;
  flex-shrink: 0;
}
.val.green {
  color: #059669;
}
.val.amber {
  color: #b45309;
}
.val.red {
  color: #dc2626;
}
.summary {
  margin: 0;
  color: var(--c-text-2);
  font-size: 13.5px;
  line-height: 1.8;
}
.flist {
  margin: 0;
  padding: 0;
  list-style: none;
  font-size: 13.5px;
  color: var(--c-text-2);
  line-height: 2;
}
.flist li::before {
  margin-right: 8px;
}
.flist.good li::before {
  content: '✓';
  color: var(--c-primary);
  font-weight: 700;
}
.flist.todo li::before {
  content: '△';
  color: #f59e0b;
  font-weight: 700;
}
</style>
