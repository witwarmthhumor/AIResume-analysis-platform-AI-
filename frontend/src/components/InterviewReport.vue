<script setup>
import { computed, onMounted, ref } from 'vue'

import { get } from '../api.js'

const props = defineProps({
  report: { type: Object, required: true },
  // 当前场次 id：用于从历史对比列表里排除自己
  sessionId: { type: Number, default: null },
})

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
    <h2>面试结束评价</h2>

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
            <select v-model="compareId">
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
.report-body {
  display: flex;
  gap: 24px;
  align-items: flex-start;
  margin-bottom: 6px;
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
  stroke: #e2e8f0;
  stroke-width: 1;
}
.axis {
  stroke: #e2e8f0;
  stroke-width: 1;
}
.current-area {
  fill: rgba(16, 185, 129, 0.18);
  stroke: #059669;
  stroke-width: 2;
}
.current-dot {
  fill: #059669;
}
.compare-area {
  fill: rgba(100, 116, 139, 0.1);
  stroke: #64748b;
  stroke-width: 1.5;
  stroke-dasharray: 5 4;
}
.axis-label {
  font-size: 11.5px;
  fill: var(--c-muted);
}
.compare-bar {
  margin-top: 8px;
}
.compare-pick {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12.5px;
  color: var(--c-muted);
}
.compare-pick select {
  flex: 1;
  min-width: 0;
  padding: 6px 8px;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  font-size: 12.5px;
  color: var(--c-text-2);
  background: #fff;
}
.compare-hint {
  margin: 0;
  font-size: 12.5px;
  color: var(--c-muted);
}
.legend {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 8px 0 0;
  font-size: 12px;
  color: var(--c-muted);
}
.swatch {
  display: inline-block;
  width: 14px;
  height: 3px;
  border-radius: 2px;
  margin-left: 4px;
}
.swatch.current {
  background: #059669;
}
.swatch.compare {
  background: #64748b;
}
.scores {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding-top: 6px;
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
.compare-scores {
  margin: 2px 0 0;
  font-size: 12.5px;
  color: var(--c-muted);
  line-height: 1.8;
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
