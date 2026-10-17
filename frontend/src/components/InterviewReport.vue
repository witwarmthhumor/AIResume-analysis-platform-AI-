<script setup>
// 面试结束评价卡（v3.5 起）：四维评分 + 手写 SVG 雷达图，可叠加历史场次对比。
// v3.8：提供「导出 PDF」——走浏览器打印对话框，打印样式在 assets/main.css 的 @media print。
import Icon from './Icon.vue'
import { computed, onMounted, ref } from 'vue'

import { get } from '../api.js'

const props = defineProps({
  report: { type: Object, required: true },
  // 当前场次 id：用于从历史对比列表里排除自己
  sessionId: { type: Number, default: null },
})

/**
 * 导出 PDF：调起浏览器打印对话框（目标打印机选「另存为 PDF」）。
 * 雷达图是内联 SVG，打印 CSS 里已限制 max-width，避免撑破一页。
 */
function exportPdf() {
  window.print()
}

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

/* —— 雷达图（v3.5）：纯手写 SVG，与项目"零图表库"约定一致 ——
   四轴对应报告里的四个维度，支持叠加一条历史场次做对比。 */

const AXES = [
  { key: 'technical_depth', label: '技术深度' },
  { key: 'communication', label: '表达结构' },
  { key: 'project_authenticity', label: '项目真实性' },
  { key: 'overall', label: '整体表现' },
]

const CX = 150
const CY = 112
const R = 70
const GRID_LEVELS = [2.5, 5, 7.5, 10]

// 极坐标取点：第 index 根轴、分值 value（0~10）在画布上的坐标
function axisPoint(index, value) {
  const angle = -Math.PI / 2 + (Math.PI * 2 * index) / AXES.length
  const radius = (Math.max(0, Math.min(10, Number(value) || 0)) / 10) * R
  return [CX + radius * Math.cos(angle), CY + radius * Math.sin(angle)]
}

function toPolygon(values) {
  return values.map((value, index) => axisPoint(index, value).join(',')).join(' ')
}

const gridPolygons = computed(() =>
  GRID_LEVELS.map((level) => toPolygon(AXES.map(() => level)))
)

const axisLabels = computed(() =>
  AXES.map((axis, index) => {
    const [x, y] = axisPoint(index, 12.3) // 顶点再往外一点
    // 左右两侧标签分别左/右对齐，上下居中，避免文字压到图形
    const anchor = index === 1 ? 'start' : index === 3 ? 'end' : 'middle'
    return { ...axis, x, y, anchor }
  })
)

const currentPolygon = computed(() => toPolygon(AXES.map((a) => props.report[a.key])))

// 顶点小圆点（当前场次）
const currentDots = computed(() =>
  AXES.map((axis, index) => {
    const [x, y] = axisPoint(index, props.report[axis.key])
    return { x, y, key: axis.key }
  })
)

/* —— 历史场次对比 —— */
const history = ref([])
const compareId = ref('')
const historyError = ref('')

const compareItem = computed(
  () => history.value.find((item) => String(item.id) === String(compareId.value)) || null
)

const comparePolygon = computed(() =>
  compareItem.value ? toPolygon(AXES.map((a) => compareItem.value.scores?.[a.key])) : ''
)

// 下拉里排除当前场次（自己跟自己比没意义）
const compareOptions = computed(() =>
  history.value.filter((item) => item.id !== props.sessionId)
)

function formatItem(item) {
  const date = item.created_at ? item.created_at.slice(0, 10) : '未知日期'
  const position = { intern: '实习', fresh: '校招', senior: '社招' }[item.position_type] || '通用'
  const overall = item.scores?.overall
  return `${date} · ${position} · 整体 ${overall ?? '-'}/10`
}

onMounted(async () => {
  try {
    const data = await get('/api/interviews/scores?limit=10')
    history.value = data?.items || []
  } catch (err) {
    // 对比是增强功能，拉取失败不影响主报告展示
    historyError.value = err?.message || '历史场次加载失败'
  }
})
</script>

<template>
  <section class="card report">
    <div class="head">
      <h2>面试结束评价</h2>
      <button
        class="btn btn-ghost btn-sm no-print export-btn"
        title="用浏览器打印对话框导出为 PDF"
        @click="exportPdf"
      >
        <Icon name="printer" :size="14" /> 导出 PDF
      </button>
    </div>

    <div class="report-body">
      <div class="radar-col">
        <svg class="radar" viewBox="0 0 300 230" role="img" aria-label="面试分维度评分雷达图">
          <polygon
            v-for="(points, i) in gridPolygons"
            :key="`grid-${i}`"
            :points="points"
            class="grid"
          />
          <line
            v-for="(axis, i) in AXES"
            :key="`axis-${axis.key}`"
            :x1="CX"
            :y1="CY"
            :x2="axisPoint(i, 10)[0]"
            :y2="axisPoint(i, 10)[1]"
            class="axis"
          />
          <polygon v-if="comparePolygon" :points="comparePolygon" class="compare-area" />
          <polygon :points="currentPolygon" class="current-area" />
          <circle
            v-for="dot in currentDots"
            :key="`dot-${dot.key}`"
            :cx="dot.x"
            :cy="dot.y"
            r="3"
            class="current-dot"
          />
          <text
            v-for="axis in axisLabels"
            :key="`label-${axis.key}`"
            :x="axis.x"
            :y="axis.y"
            :text-anchor="axis.anchor"
            class="axis-label"
            dominant-baseline="central"
          >
            {{ axis.label }}
          </text>
        </svg>

        <div class="compare-bar">
          <label v-if="compareOptions.length" class="compare-pick">
            <span>对比场次</span>
            <select v-model="compareId" class="input">
              <option value="">不对比</option>
              <option v-for="item in compareOptions" :key="item.id" :value="String(item.id)">
                {{ formatItem(item) }}
              </option>
            </select>
          </label>
          <p v-else-if="historyError" class="compare-hint">{{ historyError }}</p>
          <p v-else class="compare-hint">暂无其他已完成场次可对比</p>
          <p v-if="compareItem" class="legend">
            <i class="swatch current"></i>本次
            <i class="swatch compare"></i>对比场次
          </p>
        </div>
      </div>

      <div class="scores">
        <div v-for="item in SCORES" :key="item[0]" class="score-row">
          <span class="lab">{{ item[0] }}</span>
          <div class="bar" :class="tone(item[1])">
            <i :style="{ width: `${Math.max(0, Math.min(100, (item[1] ?? 0) * 10))}%` }"></i>
          </div>
          <span class="val" :class="tone(item[1])">{{ item[1] ?? '-' }}/10</span>
        </div>
        <p v-if="compareItem" class="compare-scores">
          对比场次：技术深度 {{ compareItem.scores?.technical_depth ?? '-' }} · 表达结构
          {{ compareItem.scores?.communication ?? '-' }} · 项目真实性
          {{ compareItem.scores?.project_authenticity ?? '-' }} · 整体
          {{ compareItem.scores?.overall ?? '-' }}
        </p>
      </div>
    </div>

    <h3 class="sec-title">整体评价</h3>
    <p class="summary">{{ report.summary }}</p>

    <h3 class="sec-title"><Icon name="check" :size="14" /> 表现亮点</h3>
    <ul class="flist good">
      <li v-for="(item, i) in report.highlights || []" :key="i">{{ item }}</li>
    </ul>

    <h3 class="sec-title"><Icon name="zap" :size="14" /> 改进建议</h3>
    <ul class="flist todo">
      <li v-for="(item, i) in report.improvements || []" :key="i">{{ item }}</li>
    </ul>
  </section>
</template>

<style scoped>
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.report h2 {
  margin-bottom: var(--sp-3);
}
/* 导出按钮靠右，与 h2 基线对齐（h2 自带下边距，这里补掉视觉偏移） */
.export-btn {
  margin: -8px 0 0 auto;
}
.report-body {
  display: flex;
  gap: var(--sp-6);
  align-items: flex-start;
  margin-bottom: var(--sp-1);
}
.radar-col {
  flex: 0 0 300px;
  max-width: 300px;
}
.radar {
  width: 100%;
  height: auto;
  display: block;
}
.grid {
  fill: none;
  stroke: var(--c-border);
  stroke-width: 1;
}
.axis {
  stroke: var(--c-border);
  stroke-width: 1;
}
.current-area {
  fill: var(--wash-primary);
  stroke: var(--c-primary);
  stroke-width: 2;
}
.current-dot {
  fill: var(--c-primary);
}
.compare-area {
  fill: rgb(107 114 128 / 10%);
  stroke: var(--c-muted);
  stroke-width: 1.5;
  stroke-dasharray: 5 4;
}
.axis-label {
  font-size: var(--fs-xs);
  fill: var(--c-muted);
}
.compare-bar {
  margin-top: var(--sp-2);
}
.compare-pick {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  font-size: var(--fs-sm);
  color: var(--c-muted);
}
.compare-pick select {
  flex: 1;
  min-width: 0;
}
.compare-hint {
  margin: 0;
  font-size: var(--fs-sm);
  color: var(--c-muted);
}
.legend {
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  margin: var(--sp-2) 0 0;
  font-size: var(--fs-xs);
  color: var(--c-muted);
}
.swatch {
  display: inline-block;
  width: 14px;
  height: 3px;
  border-radius: var(--radius-full);
  margin-left: var(--sp-1);
}
.swatch.current {
  background: var(--c-primary);
}
.swatch.compare {
  background: var(--c-muted);
}
.scores {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--sp-3);
  padding-top: var(--sp-1);
}
.score-row {
  display: flex;
  align-items: center;
  gap: var(--sp-3);
  font-size: var(--fs-sm);
}
.lab {
  width: 76px;
  color: var(--c-muted);
  flex-shrink: 0;
}
.bar {
  flex: 1;
  height: 8px;
  border-radius: var(--radius-full);
  background: var(--c-bg-soft);
  overflow: hidden;
}
.bar > i {
  display: block;
  height: 100%;
  border-radius: var(--radius-full);
  transition: width var(--dur) var(--ease);
}
/* 评分条实色，按语义分段（tone() 在 script 里按 0~10 分档） */
.bar.green > i {
  background: var(--c-ok);
}
.bar.amber > i {
  background: var(--c-warn);
}
.bar.red > i {
  background: var(--c-bad);
}
.val {
  width: 46px;
  text-align: right;
  font-weight: 600;
  font-size: var(--fs-md);
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
}
.val.green {
  color: var(--c-ok);
}
.val.amber {
  color: var(--c-warn);
}
.val.red {
  color: var(--c-bad);
}
.compare-scores {
  margin: var(--sp-1) 0 0;
  font-size: var(--fs-sm);
  color: var(--c-muted);
  line-height: 1.6;
}
.summary {
  margin: 0;
  color: var(--c-text-2);
  font-size: var(--fs-md);
  line-height: 1.6;
}
.flist {
  margin: 0;
  padding: 0;
  list-style: none;
  font-size: var(--fs-md);
  color: var(--c-text-2);
  line-height: 1.6;
}
.flist li {
  margin-bottom: var(--sp-1);
}
/* 手画对勾/三角收编为语义色圆点 */
.flist li::before {
  content: '';
  display: inline-block;
  width: 6px;
  height: 6px;
  margin-right: var(--sp-2);
  border-radius: var(--radius-full);
  vertical-align: 2px;
}
.flist.good li::before {
  background: var(--c-ok);
}
.flist.todo li::before {
  background: var(--c-warn);
}

@media (max-width: 900px) {
  .report-body {
    flex-direction: column;
  }
  .radar-col {
    flex: none;
    width: 100%;
    max-width: 340px;
    margin: 0 auto;
  }
  .scores {
    width: 100%;
  }
}
</style>
