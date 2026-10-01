// AgentChatCore SSE 状态机测试（P1-5b）。
// 覆盖 v3.7/v3.8 两次线上事故的根因场景：流事件串台（归属会话快照 + abort）、
// 历史加载竞态（loadSeq）、SSE 事件机的每个分支（meta/action/reset/observation/
// delta/done/error），以及空 delta 拼出 "undefined" 的历史 bug。
// SSE 帧用 frame() 逐行 join 构造，避免源码里出现跨行字符串字面量。
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import AgentChatCore from '../src/components/agent/AgentChatCore.vue'

// mock 网络层，保留真实 parseSseBlock（事件机测试要用真解析器）
vi.mock('../src/api.js', async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    get: vi.fn(),
    post: vi.fn(),
    streamChat: vi.fn(),
  }
})

import { get, post, streamChat } from '../src/api.js'

const enc = new TextEncoder()
// 一个 SSE 事件帧：event 行 + data 行 + 空行（块边界）
const frame = (event, dataJson) =>
  ['event: ' + event, dataJson == null ? null : 'data: ' + dataJson, '', '']
    .filter((l) => l !== null)
    .join('\n')

function deferred() {
  let resolve
  let reject
  const promise = new Promise((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

// 把若干 SSE 帧变成逐帧返回的 reader：每帧一次 read，最后 done
function mockStream(frames) {
  const abort = vi.fn()
  const chunks = frames.map((f) => enc.encode(f))
  let i = 0
  const reader = {
    read: async () => (i < chunks.length ? { done: false, value: chunks[i++] } : { done: true }),
  }
  streamChat.mockReturnValue({ reader: Promise.resolve(reader), abort })
  return { abort }
}

function mountCore(sessionId = null) {
  return mount(AgentChatCore, { props: { sessionId } })
}

beforeEach(() => {
  vi.clearAllMocks()
  get.mockImplementation(async (url) => {
    if (String(url) === '/api/agent/tools') return { tools: [] }
    return []
  })
  post.mockResolvedValue({ id: 9 })
})

async function sendMessage(wrapper, text = '你好') {
  wrapper.find('textarea').setValue(text)
  await wrapper.find('textarea').trigger('keydown.enter')
  await flushPromises()
}

describe('SSE 事件机（handleEvent，经 send 驱动）', () => {
  it('meta→action→observation→reset→delta→done 全流程：占位、工具步骤、内容定格', async () => {
    mockStream([
      frame('meta', '{}'),
      frame('action', '{"tool":"kb_search","input":"RAG"}'),
      frame('observation', '{"preview":"命中 3 块"}'),
      frame('reset', '{}'),
      frame('delta', '{"content":"RAG 是"}'),
      frame('delta', '{"content":"检索增强生成"}'),
      frame('done', '{"content":"RAG 是检索增强生成","citations":[{"title":"A","similarity":0.9}]}'),
    ])
    const w = mountCore()
    await sendMessage(w, '什么是 RAG')

    const msgs = w.vm.messages
    expect(msgs).toHaveLength(2)
    expect(msgs[0].role).toBe('user')
    const answer = msgs[1]
    expect(answer.content).toBe('RAG 是检索增强生成')
    expect(answer.loading).toBe(false)
    expect(answer.toolSteps).toEqual([
      { tool: 'kb_search', input: 'RAG', preview: '命中 3 块', running: false },
    ])
    expect(answer.citations).toEqual([{ title: 'A', similarity: 0.9 }])
    expect(w.vm.streaming).toBe(false)
    expect(w.emitted('title-updated')).toHaveLength(1)
  })

  it('空 delta 不拼出 "undefined"（历史 bug 回归锚点）', async () => {
    mockStream([
      frame('meta', '{}'),
      frame('delta', '{"content":""}'),
      frame('delta', '{}'),
      frame('done', '{"content":"最终回答"}'),
    ])
    const w = mountCore()
    await sendMessage(w)
    expect(w.vm.messages[1].content).toBe('最终回答')
    expect(w.vm.messages[1].content).not.toContain('undefined')
  })

  it('reset 清空中间过程文本：工具决策轮吐的 token 不留在最终回答里', async () => {
    mockStream([
      frame('meta', '{}'),
      frame('delta', '{"content":"中间思考过程"}'),
      frame('reset', '{}'),
      frame('delta', '{"content":"正式回答"}'),
      frame('done', '{"content":""}'),
    ])
    const w = mountCore()
    await sendMessage(w)
    expect(w.vm.messages[1].content).toBe('正式回答')
  })

  it('done 未带 content 时保留已累计内容', async () => {
    mockStream([frame('meta', '{}'), frame('delta', '{"content":"累计内容"}'), frame('done', '{}')])
    const w = mountCore()
    await sendMessage(w)
    expect(w.vm.messages[1].content).toBe('累计内容')
    expect(w.vm.messages[1].loading).toBe(false)
  })

  it('error 事件：空占位转错误消息；已有内容则只设横幅不覆盖回答', async () => {
    mockStream([frame('meta', '{}'), frame('error', '{"content":"服务欠费"}')])
    const w1 = mountCore()
    await sendMessage(w1)
    expect(w1.vm.messages[1].role).toBe('error')
    expect(w1.vm.messages[1].content).toBe('服务欠费')
    expect(w1.vm.error).toBe('')

    mockStream([
      frame('meta', '{}'),
      frame('delta', '{"content":"已有内容"}'),
      frame('error', '{"content":"流中断"}'),
      frame('done', '{}'),
    ])
    const w2 = mountCore()
    await sendMessage(w2)
    expect(w2.vm.messages[1].content).toBe('已有内容')
    expect(w2.vm.error).toBe('流中断')
  })

  it('孤儿事件与畸形块不抛异常、不中断流', async () => {
    mockStream([
      frame('observation', '{"preview":"孤儿事件"}'), // 无占位时的 observation
      frame('meta', '{}'),
      frame('delta', '{"content":"ok"}'),
      frame('done', '{"content":"ok"}'),
    ])
    const w = mountCore()
    await sendMessage(w)
    expect(w.vm.messages[1].content).toBe('ok')
  })
})

describe('流生命周期守卫（v3.7 约定）', () => {
  it('发送前无会话则自动新建并 emit session-created', async () => {
    mockStream([frame('done', '{"content":"hi"}')])
    const w = mountCore(null)
    await sendMessage(w)
    expect(post).toHaveBeenCalledWith('/api/agent/sessions', {})
    expect(w.emitted('session-created')[0][0]).toEqual({ id: 9 })
  })

  it('中途切换会话：旧流立即 abort，迟到帧不写入', async () => {
    // 两帧正常下发，之后 reader 挂起，模拟慢流
    const gate = deferred()
    const abort = vi.fn()
    const chunks = [enc.encode(frame('meta', '{}')), enc.encode(frame('delta', '{"content":"旧会话内容"}'))]
    let i = 0
    streamChat.mockReturnValue({
      reader: Promise.resolve({
        read: async () => {
          if (i < chunks.length) return { done: false, value: chunks[i++] }
          return gate.promise
        },
      }),
      abort,
    })
    const w = mountCore(null)
    w.find('textarea').setValue('问题')
    await w.find('textarea').trigger('keydown.enter')
    await flushPromises()

    await w.setProps({ sessionId: 5 }) // 切走：watch 更新归属会话
    // 挂起中再来一帧数据：top-of-loop 守卫立即 abort，且该帧事件不写入
    gate.resolve({ done: false, value: enc.encode(frame('delta', '{"content":"迟到内容"}')) })
    await flushPromises()

    expect(abort).toHaveBeenCalled()
    expect(w.vm.streaming).toBe(false)
    // 切会话的正确语义：清空视图并加载新会话历史（get 已带 sessionId=5 发出），
    // 新会话暂无消息 => 列表为空；旧流的"迟到内容"绝不串台写入新视图
    expect(get).toHaveBeenCalledWith('/api/agent/sessions/5/messages')
    expect(w.vm.messages.length).toBe(0)
  })

  it('组件卸载中止后台流', async () => {
    const gate = deferred()
    const abort = vi.fn()
    streamChat.mockReturnValue({
      reader: Promise.resolve({ read: () => gate.promise }),
      abort,
    })
    const w = mountCore(null)
    w.find('textarea').setValue('问题')
    await w.find('textarea').trigger('keydown.enter')
    await flushPromises()
    w.unmount()
    expect(abort).toHaveBeenCalled()
  })

  it('流异常（非 Abort）设置错误横幅并结束 loading', async () => {
    streamChat.mockReturnValue({
      reader: Promise.reject(new Error('网络炸了')),
      abort: vi.fn(),
    })
    const w = mountCore(3)
    await sendMessage(w)
    expect(w.vm.error).toBe('网络炸了')
    expect(w.vm.streaming).toBe(false)
    // user 气泡已上屏，loading 兜底收尾
    expect(w.vm.messages[0].role).toBe('user')
  })
})

describe('历史消息加载竞态（loadSeq 守卫）', () => {
  it('快速切会话：先发慢回的旧响应不得覆盖新会话消息', async () => {
    const d1 = deferred()
    const d2 = deferred()
    get.mockImplementation((url) => {
      const u = String(url)
      if (u.includes('/sessions/1/messages')) return d1.promise
      if (u.includes('/sessions/2/messages')) return d2.promise
      if (u === '/api/agent/tools') return Promise.resolve({ tools: [] })
      return Promise.resolve([])
    })

    const w = mountCore(1)
    await flushPromises()
    await w.setProps({ sessionId: 2 })
    await flushPromises()

    d2.resolve([
      { id: 22, role: 'user', content: '新会话消息' },
      { id: 23, role: 'assistant', content: '新会话回答' },
    ])
    await flushPromises()
    // 旧会话的响应后到：必须被丢弃
    d1.resolve([{ id: 11, role: 'user', content: '旧会话消息' }])
    await flushPromises()

    expect(w.vm.messages.map((m) => m.content)).toEqual(['新会话消息', '新会话回答'])
    expect(w.vm.error).toBe('')
  })

  it('历史加载失败设置错误提示（且竞态旧失败不覆盖新状态）', async () => {
    const d1 = deferred()
    get.mockImplementation((url) => {
      const u = String(url)
      if (u.includes('/sessions/1/messages')) return d1.promise
      if (u === '/api/agent/tools') return Promise.resolve({ tools: [] })
      return Promise.reject(new Error('加载失败'))
    })
    const w = mountCore(1)
    await flushPromises()
    await w.setProps({ sessionId: 2 })
    await flushPromises()
    expect(w.vm.error).toBe('加载失败')

    d1.reject(new Error('旧会话的失败'))
    await flushPromises()
    expect(w.vm.error).toBe('加载失败') // 旧失败被丢弃
  })
})
