// 工具中文名（v4.3 单一数据源改造）：唯一数据源在后端 registry（GET /api/agent/tools）。
// 拉取一次后缓存；未加载完成或接口失败时回退英文原名，新增工具不会显示为空。
import { reactive } from 'vue'
import { get } from './api.js'

export const toolLabels = reactive({})

let loaded = false

export async function loadToolLabels() {
  if (loaded) return
  loaded = true
  try {
    const data = await get('/api/agent/tools')
    for (const t of data.tools || []) toolLabels[t.name] = t.label
  } catch {
    // 拉取失败不打断聊天，保持英文原名回退
  }
}

export function toolLabel(name) {
  if (name === 'unknown_tool') return '未知工具'
  return toolLabels[name] || name
}
