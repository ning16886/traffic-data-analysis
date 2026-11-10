// 后端统一响应格式：{ code: 0, data: {...}, msg: '' }
// 页面里不要直接写 fetch，一律 import 同目录下的模块函数
const BASE_URL = '/api'

export class ApiError extends Error {
  constructor(code, msg) {
    super(msg || `请求失败（code=${code}）`)
    this.name = 'ApiError'
    this.code = code
  }
}

async function request(path, { method = 'GET', params, body } = {}) {
  const query = new URLSearchParams()
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null && value !== '') {
        query.set(key, value)
      }
    }
  }

  const queryString = query.toString()
  const res = await fetch(BASE_URL + path + (queryString ? `?${queryString}` : ''), {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })

  if (!res.ok) {
    throw new ApiError(res.status, `HTTP ${res.status} ${res.statusText}`)
  }

  const json = await res.json()
  if (json.code !== 0) {
    throw new ApiError(json.code, json.msg)
  }

  return json.data
}

export const api = {
  get: (path, params) => request(path, { params }),
  post: (path, body) => request(path, { method: 'POST', body }),
  put: (path, body) => request(path, { method: 'PUT', body }),
  del: (path) => request(path, { method: 'DELETE' }),
}
