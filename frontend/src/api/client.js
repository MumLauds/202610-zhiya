const API_ROOT = '/api/v1'

export class ApiError extends Error {
  constructor(message, { status = 0, code = 'REQUEST_FAILED', requestId = '', retryable = false, retryAfter = '' } = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.requestId = requestId
    this.retryable = retryable
    this.retryAfter = retryAfter
  }
}

export function readCsrfCookie() {
  const prefix = 'zhiya_csrf='
  return document.cookie
    .split(';')
    .map((part) => part.trim())
    .find((part) => part.startsWith(prefix))
    ?.slice(prefix.length) ?? ''
}

export async function apiRequest(path, options = {}) {
  const { timeoutMs = 30000, signal, ...requestOptions } = options
  const method = (options.method || 'GET').toUpperCase()
  const headers = new Headers(options.headers || {})
  headers.set('Accept', 'application/json')

  let body = options.body
  if (body !== undefined && !(body instanceof FormData) && typeof body !== 'string') {
    headers.set('Content-Type', 'application/json')
    body = JSON.stringify(body)
  }
  if (!['GET', 'HEAD'].includes(method)) {
    const csrf = decodeURIComponent(readCsrfCookie())
    if (csrf) headers.set('X-CSRF-Token', csrf)
  }

  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const response = await fetch(`${API_ROOT}${path}`, {
      ...requestOptions,
      method,
      body,
      headers,
      credentials: 'include',
      signal: signal ? AbortSignal.any([signal, controller.signal]) : controller.signal,
    })
    if (response.status === 204) return null

    const contentType = response.headers.get('content-type') || ''
    const payload = contentType.includes('application/json') ? await response.json() : null
    if (!response.ok) {
      const detail = payload?.error
      throw new ApiError(detail?.message || `请求失败（${response.status}）`, {
        status: response.status,
        code: detail?.code || 'REQUEST_FAILED',
        requestId: payload?.requestId || '',
        retryable: Boolean(detail?.retryable),
        retryAfter: response.headers.get('Retry-After') || '',
      })
    }
    return payload
  } catch (error) {
    if (error instanceof ApiError || signal?.aborted) throw error
    const read = ['GET', 'HEAD'].includes(method)
    throw new ApiError(controller.signal.aborted ? '请求超时' : '无法确认请求结果，请检查网络', {
      code: read ? 'REQUEST_FAILED' : 'REQUEST_OUTCOME_UNKNOWN',
      retryable: read,
    })
  } finally {
    clearTimeout(timer)
  }
}

export function describeApiError(error) {
  if (error.code === 'REQUEST_OUTCOME_UNKNOWN') return '请求结果尚未确认，请先查询状态，不要重复提交。'
  if (error.code === 'REQUEST_IN_PROGRESS') return '原请求仍在处理，请稍后确认原请求。'
  if (error.code === 'INVALID_CREDENTIALS') return error.message || '账号或密码错误'
  if (error.status === 401) return '登录已失效，请重新登录。'
  if (error.code === 'CSRF_INVALID') return '登录校验已变化，请刷新页面后重试。'
  if (error.status === 409) return `${error.message}，请刷新当前状态后再操作。`
  if (error.status === 429) return `请求过于频繁，请${error.retryAfter ? `等待 ${error.retryAfter} 秒后` : '稍后'}再试。`
  if (error.status === 503) return '服务暂不可用，请稍后重试；未自动重发本次操作。'
  if (error.status === 504) return '请求超时，请先确认当前状态。'
  return error.message || '操作失败，请稍后再试。'
}

export function mediaUrl(resourceKey) {
  return resourceKey ? `${API_ROOT}/media/${encodeURIComponent(resourceKey)}` : ''
}

export function createClientEventId(prefix = 'evt') {
  const random = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`
  return `${prefix}-${random}`.slice(0, 64)
}
