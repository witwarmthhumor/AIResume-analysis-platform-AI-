// parseSseBlock 单测（P1-5b）：SSE 块解析是所有流组件的第一道防线。
// 口径：event 行必须有；多行 data: 聚合后 JSON 解析；畸形块返回 null 由调用方跳过。
// 帧构造用 join 避免 JS 源码里出现跨行字符串字面量。
import { describe, expect, it } from 'vitest'
import { parseSseBlock } from '../src/api.js'

const frame = (...lines) => lines.join('\n')

describe('parseSseBlock', () => {
  it('解析标准块：event + JSON data', () => {
    expect(parseSseBlock(frame('event: delta', 'data: {"content":"你好"}'))).toEqual({
      event: 'delta',
      data: { content: '你好' },
    })
  })

  it('聚合多行 data:：按换行拼接后整体 JSON 解析（SSE 规范语义）', () => {
    // 跨行的单个 JSON：拼接换行后仍是合法 JSON，证明多行 data 确实被聚合
    expect(parseSseBlock(frame('event: delta', 'data: {"text":', 'data: "值"}'))).toEqual({
      event: 'delta',
      data: { text: '值' },
    })
  })
  it('多行 data 拼接后不是合法 JSON：返回 null（调用方跳过）', () => {
    // 两段 JSON 拼一起解析必失败——畸形块统一走 null 口径
    expect(parseSseBlock(frame('event: delta', 'data: {"a":1}', 'data: {"b":2}'))).toBeNull()
  })

  it('data: 后无空格也能解析（宽松口径）', () => {
    expect(parseSseBlock(frame('event: meta', 'data:{}'))).toEqual({
      event: 'meta',
      data: {},
    })
  })

  it('缺 event 行返回 null（调用方跳过）', () => {
    expect(parseSseBlock('data: {"content":"x"}')).toBeNull()
  })

  it('data 不是合法 JSON 返回 null（畸形块不中断流）', () => {
    expect(parseSseBlock(frame('event: delta', 'data: {broken'))).toBeNull()
  })

  it('有 event 无 data：data 为 null（如 reset 事件可无载荷）', () => {
    expect(parseSseBlock('event: reset')).toEqual({ event: 'reset', data: null })
  })
})
