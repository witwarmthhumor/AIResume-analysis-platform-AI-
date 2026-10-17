<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import Icon from './Icon.vue'

const props = defineProps({
  modelValue: {
    type: Object,
    default: () => ({ start: '', end: '', startTime: '00:00', endTime: '23:59' }),
  },
  placeholder: { type: String, default: '选择日期范围' },
})
const emit = defineEmits(['update:modelValue'])

const showPicker = ref(false)
const wrapperRef = ref(null)

// 浮层内临时选择（确定才 emit，取消则丢弃）
const tempStart = ref('')
const tempEnd = ref('')
const tempStartTime = ref('00:00')
const tempEndTime = ref('23:59')
const viewYear = ref(new Date().getFullYear())
const viewMonth = ref(new Date().getMonth()) // 0-based

const WEEKDAYS = ['一', '二', '三', '四', '五', '六', '日']

function pad(n) {
  return String(n).padStart(2, '0')
}
function fmtISO(d) {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

function syncFromProps() {
  const v = props.modelValue || {}
  tempStart.value = v.start || ''
  tempEnd.value = v.end || ''
  tempStartTime.value = v.startTime || '00:00'
  tempEndTime.value = v.endTime || '23:59'
  const refStr = tempEnd.value || tempStart.value
  const d = refStr ? new Date(refStr + 'T00:00:00') : new Date()
  viewYear.value = d.getFullYear()
  viewMonth.value = d.getMonth()
}

function toggle() {
  if (showPicker.value) {
    showPicker.value = false
  } else {
    syncFromProps()
    showPicker.value = true
  }
}

function prevMonth() {
  if (viewMonth.value === 0) {
    viewMonth.value = 11
    viewYear.value -= 1
  } else {
    viewMonth.value -= 1
  }
}
function nextMonth() {
  if (viewMonth.value === 11) {
    viewMonth.value = 0
    viewYear.value += 1
  } else {
    viewMonth.value += 1
  }
}

// 6x7 日期网格（周一起始）
const days = computed(() => {
  const first = new Date(viewYear.value, viewMonth.value, 1)
  let offset = first.getDay() - 1 // 周日 0 → 6
  if (offset < 0) offset = 6
  const gridStart = new Date(viewYear.value, viewMonth.value, 1 - offset)
  const out = []
  for (let i = 0; i < 42; i++) {
    const d = new Date(gridStart)
    d.setDate(gridStart.getDate() + i)
    const iso = fmtISO(d)
    let cls = 'day'
    if (d.getMonth() !== viewMonth.value) cls += ' other-month'
    if (iso === tempStart.value || iso === tempEnd.value) cls += ' selected'
    else if (tempStart.value && tempEnd.value && iso > tempStart.value && iso < tempEnd.value) {
      cls += ' in-range'
    }
    out.push({ date: d.getDate(), iso, cls })
  }
  return out
})

function selectDay(day) {
  if (!tempStart.value || (tempStart.value && tempEnd.value)) {
    // 无开始，或已有完整范围 → 重新作为开始
    tempStart.value = day.iso
    tempEnd.value = ''
  } else if (day.iso < tempStart.value) {
    // 早于开始 → 交换
    tempEnd.value = tempStart.value
    tempStart.value = day.iso
  } else {
    tempEnd.value = day.iso
  }
}

function confirm() {
  if (!tempStart.value) {
    showPicker.value = false
    return
  }
  emit('update:modelValue', {
    start: tempStart.value,
    end: tempEnd.value || tempStart.value, // 单日：结束=开始
    startTime: tempStartTime.value,
    endTime: tempEndTime.value,
  })
  showPicker.value = false
}

function cancel() {
  showPicker.value = false
}

// 点击外部关闭
function onDocClick(e) {
  if (showPicker.value && wrapperRef.value && !wrapperRef.value.contains(e.target)) {
    showPicker.value = false
  }
}
onMounted(() => document.addEventListener('click', onDocClick))
onUnmounted(() => document.removeEventListener('click', onDocClick))

const displayText = computed(() => {
  const v = props.modelValue || {}
  if (v.start) {
    if (v.end && v.end !== v.start) {
      return `${v.start} ${v.startTime || '00:00'} ~ ${v.end} ${v.endTime || '23:59'}`
    }
    return `${v.start} ${v.startTime || '00:00'}`
  }
  return props.placeholder
})
</script>

<template>
  <div ref="wrapperRef" class="drp-wrapper">
    <div class="drp-input input" :class="{ active: showPicker }" @click="toggle">
      <span class="drp-calendar"><Icon name="calendar" :size="14" /></span>
      <span class="drp-text" :class="{ placeholder: !modelValue?.start }">{{ displayText }}</span>
      <span class="drp-arrow"><Icon name="chevron-down" :size="12" /></span>
    </div>

    <div v-if="showPicker" class="drp-popup popup" @click.stop>
      <!-- 月份切换 -->
      <div class="drp-header">
        <button type="button" class="month-btn" aria-label="上一月" title="上一月" @click="prevMonth"><Icon name="arrow-left" :size="14" /></button>
        <span class="drp-ym">{{ viewYear }}年{{ viewMonth + 1 }}月</span>
        <button type="button" class="month-btn" aria-label="下一月" title="下一月" @click="nextMonth"><Icon name="arrow-right" :size="14" /></button>
      </div>

      <!-- 星期表头 -->
      <div class="drp-weekdays">
        <span v-for="w in WEEKDAYS" :key="w">{{ w }}</span>
      </div>

      <!-- 日期网格 -->
      <div class="drp-days">
        <span
          v-for="day in days"
          :key="day.iso"
          :class="day.cls"
          @click="selectDay(day)"
        >{{ day.date }}</span>
      </div>

      <!-- 时间选择 -->
      <div class="drp-times">
        <label class="time-field">
          <span>开始</span>
          <input v-model="tempStartTime" class="input" type="time" />
        </label>
        <label class="time-field">
          <span>结束</span>
          <input v-model="tempEndTime" class="input" type="time" />
        </label>
      </div>

      <!-- 操作按钮 -->
      <div class="drp-actions">
        <button type="button" class="btn btn-ghost" @click="cancel">取消</button>
        <button type="button" class="btn btn-primary" @click="confirm">确定</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.drp-wrapper {
  position: relative;
  display: inline-block;
}
/* 边框/字号/聚焦环由全局 .input 提供，这里只留布局与激活态 */
.drp-input {
  width: auto;
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  min-width: 240px;
  cursor: pointer;
}
.drp-input.active,
.drp-input:hover {
  border-color: var(--c-primary);
  box-shadow: 0 0 0 3px var(--wash-primary);
}
.drp-calendar {
  display: inline-flex;
  align-items: center;
  color: var(--c-muted);
}
.drp-text {
  flex: 1;
  color: var(--c-text);
  white-space: nowrap;
}
.drp-text.placeholder {
  color: var(--c-faint);
}
.drp-arrow {
  display: inline-flex;
  align-items: center;
  color: var(--c-faint);
}

/* 浮层（圆角/阴影/边框由全局 .popup 提供） */
.drp-popup {
  position: absolute;
  top: calc(100% + var(--sp-2));
  left: 0;
  z-index: var(--z-pop);
  width: 280px;
  padding: var(--sp-3);
  animation: pop-in var(--dur-fast) var(--ease) both;
}
@keyframes pop-in {
  from { opacity: 0; transform: translateY(-6px); }
  to { opacity: 1; transform: translateY(0); }
}

.drp-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--sp-3);
}
.drp-ym {
  font-size: var(--fs-md);
  font-weight: 600;
  color: var(--c-text);
}
.month-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: 1px solid var(--c-border);
  border-radius: var(--radius-sm);
  background: #fff;
  color: var(--c-muted);
  cursor: pointer;
  transition:
    background var(--dur-fast) var(--ease),
    border-color var(--dur-fast) var(--ease),
    color var(--dur-fast) var(--ease);
}
.month-btn:hover {
  background: var(--c-bg-soft);
  border-color: var(--c-primary-border);
  color: var(--c-primary);
}

.drp-weekdays,
.drp-days {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  gap: var(--sp-1);
}
.drp-weekdays span {
  text-align: center;
  font-size: var(--fs-xs);
  color: var(--c-faint);
  padding: var(--sp-1) 0;
}
.drp-days {
  margin-bottom: var(--sp-3);
}
.day {
  text-align: center;
  font-size: var(--fs-xs);
  padding: var(--sp-1) 0;
  cursor: pointer;
  border-radius: var(--radius-sm);
  color: var(--c-text-2);
  transition: background var(--dur-fast) var(--ease);
}
.day:hover {
  background: var(--wash-primary);
}
.day.other-month {
  color: var(--c-border-strong);
}
.day.in-range {
  background: var(--wash-primary);
  border-radius: 0;
}
.day.selected {
  background: var(--c-primary);
  color: #fff;
  font-weight: 600;
  border-radius: var(--radius-full);
}

.drp-times {
  display: flex;
  gap: var(--sp-3);
  margin-bottom: var(--sp-3);
}
.time-field {
  flex: 1;
  display: flex;
  align-items: center;
  gap: var(--sp-2);
  font-size: var(--fs-xs);
  color: var(--c-muted);
}
.time-field .input {
  flex: 1;
  min-width: 0;
}

.drp-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--sp-2);
}
</style>
