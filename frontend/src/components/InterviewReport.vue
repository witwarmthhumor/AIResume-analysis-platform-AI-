<script setup>
defineProps({ report: { type: Object, required: true } })
</script>

<template>
  <section class="card report">
    <h2>面试结束评价</h2>
    <div class="scores">
      <div v-for="item in [
        ['技术深度', report.technical_depth],
        ['表达结构', report.communication],
        ['项目真实性', report.project_authenticity],
        ['整体表现', report.overall],
      ]" :key="item[0]" class="score">
        <strong>{{ item[1] }}/10</strong>
        <span>{{ item[0] }}</span>
      </div>
    </div>
    <h3>整体评价</h3>
    <p class="summary">{{ report.summary }}</p>
    <h3>表现亮点</h3>
    <ul><li v-for="(item, i) in report.highlights || []" :key="i">{{ item }}</li></ul>
    <h3>改进建议</h3>
    <ul><li v-for="(item, i) in report.improvements || []" :key="i">{{ item }}</li></ul>
  </section>
</template>

<style scoped>
.card { background: #fff; border-radius: 12px; box-shadow: 0 1px 4px rgb(0 0 0 / 6%); padding: 20px 24px; }
h2 { margin: 0 0 16px; font-size: 18px; }
h3 { margin: 18px 0 6px; font-size: 14px; color: #111827; }
.scores { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
.score { display: flex; flex-direction: column; align-items: center; gap: 4px; border-radius: 8px; background: #f0fdf4; padding: 10px 4px; }
.score strong { color: #047857; font-size: 20px; }
.score span { color: #6b7280; font-size: 12px; }
.summary, li { color: #4b5563; font-size: 14px; line-height: 1.7; }
.summary { margin: 0; }
ul { margin: 0; padding-left: 20px; }
@media (max-width: 520px) { .scores { grid-template-columns: repeat(2, 1fr); } }
</style>
