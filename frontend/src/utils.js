/* utils.js — 前端共享工具函数（v3.7 审计：消除 AdminPanel/HistoryView/ProfileView/HomeView/ResumeList 的复制粘贴）

  全部纯函数，无副作用，任何组件可直接 import。
*/

// 数字千分位：0/空值回落 0
export function fmtNum(n) {
  return Number(n || 0).toLocaleString()
}

// '2026-09-19' → '9/19'（柱图 x 轴用）；非标准格式原样返回
export function shortDate(s) {
  const p = String(s).split('-')
  return p.length === 3 ? `${Number(p[1])}/${Number(p[2])}` : s
}

function pad(n) {
  return String(n).padStart(2, '0')
}

// ISO 时间 → '2026/9/19 08:30'（本地时区）
export function fmtDateTime(iso) {
  if (!iso) return '-'
  const d = new Date(iso)
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

// 简历解析状态徽标映射：label 与 cls 统一口径（此前 HomeView 与 ResumeList 各写一份且文案不一致）
export const RESUME_STATUS = {
  success: { label: '解析成功', cls: 'ok' },
  unsupported: { label: '扫描件（暂不支持）', cls: 'warn' },
  failed: { label: '解析失败', cls: 'bad' },
  pending: { label: '解析中', cls: 'warn' },
}

// 分页页码序列：当前页附近全显，超过 7 页用省略号折叠（1 … 4 5 6 … N）。
// 算法与原 AdminPanel/HistoryView 各自的实现逐字节一致，只是收敛到一处
export function pageNumbers(current, total) {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1)
  const pages = [1]
  if (current > 3) pages.push('…')
  for (let i = Math.max(2, current - 1); i <= Math.min(total - 1, current + 1); i++) {
    pages.push(i)
  }
  if (current < total - 2) pages.push('…')
  pages.push(total)
  return pages
}
