/* api.js — 统一 HTTP 请求封装（P3 清单项）

  所有组件通过此文件调后端，不再裸 fetch。
  成功返回 data，错误统一抛出 { code, message, details } 对象。
  自动 credentials: include，自动 JSON 序列化。
*/

const BASE = ''

export async function request(method, url, body = null) {
  const opts = { method, credentials: 'include', headers: {} }
  if (body instanceof FormData) {
    opts.body = body
    // 不设 Content-Type，让浏览器自动设 multipart boundary
  } else if (body != null) {
    opts.headers['Content-Type'] = 'application/json'
    opts.body = JSON.stringify(body)
  }
  const res = await fetch(BASE + url, opts)
  if (!res.ok) {
    let err = { code: 'http_error', message: `请求失败 (${res.status})`, details: null }
    try {
      const body = await res.json()
      err = { code: body.code || err.code, message: body.message || err.message, details: body.details }
    } catch {}
    throw err
  }
  if (res.status === 204) return null
  return res.json()
}

export function get(url) { return request('GET', url) }

export function post(url, body = null) { return request('POST', url, body) }

export function del(url) { return request('DELETE', url) }

/* SSE 流式对话：POST body 后读取 Server-Sent Events 流
   返回 { reader, abort }，调用者通过 reader 读取事件自行解析。
*/
export function streamChat(url, body) {
  const controller = new AbortController()
  const promise = fetch(BASE + url, {
    method: 'POST', credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body), signal: controller.signal,
  })
  return {
    reader: promise.then(async r => {
      if (!r.ok) {
        let message = `请求失败 (${r.status})`
        try {
          const b = await r.json()
          message = b.message || message
        } catch {}
        throw new Error(message)
      }
      return r.body.getReader()
    }),
    abort: () => controller.abort(),
  }
}