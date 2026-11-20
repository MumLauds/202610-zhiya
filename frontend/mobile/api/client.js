const DEFAULT_TIMEOUT_MS = 15000

export class ApiError extends Error {
  constructor(message, options = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = options.status ?? 0
    this.code = options.code ?? 'NETWORK_ERROR'
    this.retryable = Boolean(options.retryable)
    this.requestId = options.requestId ?? null
    this.retryAfter = options.retryAfter ?? null
    this.payload = options.payload ?? null
  }
}

export function readCookie(name) {
  if (typeof document === 'undefined') return ''
  const prefix = `${name}=`
  return document.cookie
    .split(';')
    .map((part) => part.trim())
    .find((part) => part.startsWith(prefix))
    ?.slice(prefix.length) ?? ''
}

function mergeAbortSignals(signal, timeoutMs) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(new DOMException('请求超时', 'TimeoutError')), timeoutMs)
  const abort = () => controller.abort(signal?.reason)
  if (signal) {
    if (signal.aborted) abort()
    else signal.addEventListener('abort', abort, { once: true })
  }
  return {
    signal: controller.signal,
    cleanup() {
      clearTimeout(timer)
      signal?.removeEventListener('abort', abort)
    },
  }
}

export async function requestJson(path, options = {}) {
  const method = (options.method ?? 'GET').toUpperCase()
  const isWrite = method !== 'GET' && method !== 'HEAD'
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS
  const merged = mergeAbortSignals(options.signal, timeoutMs)
  const headers = new Headers(options.headers ?? {})
  headers.set('Accept', 'application/json')

  let body = options.body
  if (body != null && !(body instanceof FormData) && typeof body !== 'string') {
    headers.set('Content-Type', 'application/json')
    body = JSON.stringify(body)
  }
  if (isWrite) {
    const csrf = options.csrfToken ?? readCookie('zhiya_csrf')
    if (csrf) headers.set('X-CSRF-Token', csrf)
  }

  try {
    const response = await fetch(path, {
      method,
      headers,
      body,
      credentials: 'include',
      cache: 'no-store',
      signal: merged.signal,
    })
    const text = await response.text()
    let payload = null
    if (text) {
      try {
        payload = JSON.parse(text)
      } catch {
        payload = { raw: text }
      }
    }
    const requestId = payload?.requestId ?? response.headers.get('X-Request-Id')
    if (isWrite && !payload?.error?.code &&
        ([502, 503, 504].includes(response.status) || (response.ok && payload?.data == null))) {
      throw new ApiError('写入结果尚未确认，请先核对会话。', {
        status: response.status, code: 'REQUEST_OUTCOME_UNKNOWN', requestId,
      })
    }
    if (!response.ok) {
      throw new ApiError(payload?.error?.message ?? (response.status >= 500 ? `后端服务异常（${response.status}）` : `请求失败（${response.status}）`), {
        status: response.status,
        code: payload?.error?.code ?? `HTTP_${response.status}`,
        retryable: payload?.error?.retryable ?? false,
        requestId,
        retryAfter: response.headers.get('Retry-After'),
        payload,
      })
    }
    return {
      data: payload?.data ?? null,
      requestId,
      status: response.status,
      payload,
    }
  } catch (error) {
    if (error instanceof ApiError) throw error
    if (isWrite && !options.signal?.aborted) {
      throw new ApiError('写入结果尚未确认，请先核对会话。', { code: 'REQUEST_OUTCOME_UNKNOWN' })
    }
    if (error?.name === 'AbortError' || error?.name === 'TimeoutError') {
      throw new ApiError(error.name === 'TimeoutError' ? '请求超时，请稍后重试' : '请求已取消', {
        code: error.name === 'TimeoutError' ? 'REQUEST_TIMEOUT' : 'REQUEST_ABORTED',
        retryable: error.name === 'TimeoutError',
      })
    }
    throw new ApiError('网络连接失败，请检查服务是否可用', {
      code: 'NETWORK_ERROR',
      retryable: true,
    })
  } finally {
    merged.cleanup()
  }
}
