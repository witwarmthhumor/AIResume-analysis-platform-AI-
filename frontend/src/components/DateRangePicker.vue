<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'

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
    <div class="drp-input" :class="{ active: showPicker }" @click="toggle">
      <span class="drp-calendar">📅</span>
      <span class="drp-text" :class="{ placeholder: !modelValue?.start }">{{ displayText }}</span>
      <span class="drp-arrow">▾</span>
    </div>

    <div v-if="showPicker" class="drp-popup" @click.stop>
      <!-- 月份切换 -->
      <div class="drp-header">
        <button type="button" class="month-btn" @click="prevMonth">‹</button>
        <span class="drp-ym">{{ viewYear }}年{{ viewMonth + 1 }}月</span>
        <button type="button" class="month-btn" @click="nextMonth">›</button>
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
          <input type="time" v-model="tempStartTime" />
        </label>
        <label class="time-field">
          <span>结束</span>
          <input type="time" v-model="tempEndTime" />
        </label>
      </div>

      <!-- 操作按钮 -->
      <div class="drp-actions">
        <button type="button" class="act-cancel" @click="cancel">取消</button>
        <button type="button" class="act-confirm" @click="confirm">确定</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.drp-wrapper {
  position: relative;
  display: inline-block;
}
.drp-input {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 240px;
  padding: 6px 10px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #fff;
  font-size: 12.5px;
  cursor: pointer;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.drp-input.active,
.drp-input:hover {
  border-color: #10b981;
  box-shadow: 0 0 0 3px rgb(16 185 129 / 10%);
}
.drp-calendar {
  font-size: 13px;
}
.drp-text {
  flex: 1;
  color: #1a1b1c;
  white-space: nowrap;
}
.drp-text.placeholder {
  color: #9ca3af;
}
.drp-arrow {
  color: #9ca3af;
  font-size: 10px;
}

/* 浮层 */
.drp-popup {
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  z-index: 100;
  width: 280px;
  background: #fff;
  border-radius: 14px;
  box-shadow: 0 10px 40px rgba(0, 0, 0, 0.18);
  padding: 14px;
  animation: pop-in 0.16s ease both;
}
@keyframes pop-in {
  from { opacity: 0; transform: translateY(-6px); }
  to { opacity: 1; transform: translateY(0); }
}

.drp-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}
.drp-ym {
  font-size: 13.5px;
  font-weight: 700;
  color: #1a1b1c;
}
.month-btn {
  width: 28px;
  height: 28px;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  background: #fff;
  color: #6b7280;
  font-size: 16px;
  line-height: 1;
  cursor: pointer;
  transition: background 0.12s ease, border-color 0.12s ease;
}
.month-btn:hover {
  background: #f9fafb;
  border-color: #10b981;
  color: #059669;
}

.drp-weekdays,
.drp-days {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  gap: 2px;
}
.drp-weekdays span {
  text-align: center;
  font-size: 11px;
  color: #9ca3af;
  padding: 4px 0;
}
.drp-days {
  margin-bottom: 10px;
}
.day {
  text-align: center;
  font-size: 12px;
  padding: 6px 0;
  cursor: pointer;
  border-radius: 6px;
  color: #374151;
  transition: background 0.1s ease;
}
.day:hover {
  background: rgb(16 185 129 / 10%);
}
.day.other-month {
  color: #d1d5db;
}
.day.in-range {
  background: rgb(16 185 129 / 12%);
  border-radius: 0;
}
.day.selected {
  background: #10b981;
  color: #fff;
  font-weight: 700;
  border-radius: 50%;
  box-shadow: 0 2px 6px rgb(16 185 129 / 35%);
}

.drp-times {
  display: flex;
  gap: 10px;
  margin-bottom: 12px;
}
.time-field {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #6b7280;
}
.time-field input {
  flex: 1;
  min-width: 0;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  padding: 4px 6px;
  font-size: 12px;
  font-family: inherit;
  outline: none;
}
.time-field input:focus {
  border-color: #10b981;
}

.drp-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
.act-cancel,
.act-confirm {
  padding: 6px 16px;
  border-radius: 8px;
  font-size: 12.5px;
  font-family: inherit;
  cursor: pointer;
  transition: background 0.12s ease;
}
.act-cancel {
  border: 1px solid #e5e7eb;
  background: #fff;
  color: #6b7280;
}
.act-cancel:hover {
  background: #f9fafb;
}
.act-confirm {
  border: 1px solid #10b981;
  background: #10b981;
  color: #fff;
  font-weight: 600;
}
.act-confirm:hover {
  background: #059669;
}
</style>
