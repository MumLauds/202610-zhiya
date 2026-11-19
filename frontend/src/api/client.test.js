import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, apiRequest, describeApiError, mediaUrl } from './client'

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); document.cookie = 'zhiya_csrf=; Max-Age=0' })

describe('API contract', () => {
  it('sends cookies and CSRF on writes, preserving JSON payloads', async () => {
    document.cookie = 'zhiya_csrf=csrf%20token'
    const fetch = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
    vi.stubGlobal('fetch', fetch)
    expect(await apiRequest('/auth/sessions/current', { method: 'DELETE', body: { value: 1 } })).toBeNull()
    const [url, options] = fetch.mock.calls[0]
    expect(url).toBe('/api/v1/auth/sessions/current')
    expect(options.credentials).toBe('include')
    expect(options.headers.get('X-CSRF-Token')).toBe('csrf token')
    expect(JSON.parse(options.body)).toEqual({ value: 1 })
  })

  it('does not set JSON content type on voice FormData or retry a 503 write', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { code: 'DEPENDENCY_UNAVAILABLE', message: 'unavailable', retryable: false }, requestId: 'req-1' }), { status: 503, headers: { 'Content-Type': 'application/json' } }))
    vi.stubGlobal('fetch', fetch)
    await expect(apiRequest('/voice/transcriptions', { method: 'POST', body: new FormData() })).rejects.toMatchObject({ status: 503, retryable: false, requestId: 'req-1' })
    expect(fetch.mock.calls[0][1].headers.has('Content-Type')).toBe(false)
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it.each(['GET', 'POST'])('reports timeout appropriately for %s without replaying', async (method) => {
    vi.useFakeTimers()
    const fetch = vi.fn((url, options) => new Promise((resolve, reject) => options.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))))
    vi.stubGlobal('fetch', fetch)
    const check = expect(apiRequest('/reading-sessions/1/events', { method, timeoutMs: 50 })).rejects.toMatchObject({ status: 0, code: method === 'GET' ? 'REQUEST_FAILED' : 'REQUEST_OUTCOME_UNKNOWN', retryable: method === 'GET' })
    await vi.advanceTimersByTimeAsync(50)
    await check
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it('keeps a server-confirmed 504 distinct from a client timeout', async () => {
    const response = Response.json({ error: { code: 'REQUEST_OUTCOME_UNKNOWN' } }, { status: 504 })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response))
    await expect(apiRequest('/reading-sessions/1/events', { method: 'POST' })).rejects.toMatchObject({
      status: 504, code: 'REQUEST_OUTCOME_UNKNOWN',
    })
  })

  it('preserves server Retry-After and distinguishes result unknown', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: { code: 'REQUEST_IN_PROGRESS', message: '处理中', retryable: true } }), { status: 409, headers: { 'Content-Type': 'application/json', 'Retry-After': '3' } })))
    await expect(apiRequest('/reading-sessions/1/events', { method: 'POST' })).rejects.toMatchObject({ retryAfter: '3', code: 'REQUEST_IN_PROGRESS' })
    expect(describeApiError(new ApiError('unknown', { status: 504, code: 'REQUEST_OUTCOME_UNKNOWN' }))).toContain('不要重复提交')
  })

  it.each([
    [401, 'AUTH_REQUIRED', '重新登录'], [403, 'CSRF_INVALID', '刷新页面'],
    [409, 'SESSION_CONFLICT', '刷新当前状态'], [429, 'RATE_LIMITED', '等待 2 秒'],
    [503, 'DEPENDENCY_UNAVAILABLE', '未自动重发'], [504, 'TIMEOUT', '确认当前状态'],
  ])('provides an actionable message for %s', (status, code, message) => {
    expect(describeApiError(new ApiError('失败', { status, code, retryAfter: '2' }))).toContain(message)
  })

  it('encodes media resource keys', () => {
    expect(mediaUrl('image/第1页')).toBe('/api/v1/media/image%2F%E7%AC%AC1%E9%A1%B5')
    expect(mediaUrl(null)).toBe('')
  })
})
