import { ApiError } from '../api/client.js'
import { createClientEventId } from './ids.js'

export class RecoverableSessionError extends Error {
  constructor(message, options = {}) {
    super(message)
    this.name = 'RecoverableSessionError'
    this.code = options.code ?? 'SESSION_RECOVERED'
    this.cause = options.cause
    this.recoveredSession = options.recoveredSession ?? null
  }
}

const RECOVERABLE_CODES = new Set([
  'SESSION_CONFLICT',
  'IDEMPOTENCY_CONFLICT',
  'REQUEST_OUTCOME_UNKNOWN',
])

export class ReadingSessionMachine {
  constructor({ api, onChange, keyFactory = createClientEventId, sleep = defaultSleep }) {
    this.api = api
    this.onChange = onChange ?? (() => {})
    this.keyFactory = keyFactory
    this.sleep = sleep
    this.session = null
    this.events = []
    this.report = null
    this.lastRequestId = null
    this.pending = false
  }

  snapshot() {
    return {
      session: this.session,
      events: this.events,
      report: this.report,
      lastRequestId: this.lastRequestId,
      pending: this.pending,
    }
  }

  _emit() {
    this.onChange(this.snapshot())
  }

  _setSnapshot(data, requestId) {
    if (data?.session) this.session = data.session
    if (Array.isArray(data?.events)) this.events = data.events
    if (Object.hasOwn(data ?? {}, 'report')) this.report = data.report
    this.lastRequestId = requestId ?? this.lastRequestId
    this._emit()
  }

  async create({ childProfileId, storyVersionId, signal }) {
    this.pending = true
    this._emit()
    try {
      const result = await this.api.create({ childProfileId, storyVersionId, signal })
      this.session = result.data
      this.events = []
      this.report = null
      this.lastRequestId = result.requestId
      return this.snapshot()
    } finally {
      this.pending = false
      this._emit()
    }
  }

  async restore(sessionId, { signal } = {}) {
    this.pending = true
    this._emit()
    try {
      const result = await this.api.get(sessionId, { signal })
      this._setSnapshot(result.data, result.requestId)
      return this.snapshot()
    } finally {
      this.pending = false
      this._emit()
    }
  }

  async start({ signal, clientEventId = this.keyFactory('start') } = {}) {
    if (!this.session) throw new Error('尚无阅读会话')
    const pageNumber = this.session.currentPage
    return this._runMutation({
      clientEventId,
      signal,
      request: () => this.api.addEvent(this.session.readingSessionId, {
        eventType: 'STARTED',
        pageNumber,
        expectedRevision: this.session.revision,
        clientEventId,
      }, { signal }),
      apply: (data) => {
        this.session = {
          ...this.session,
          status: 'IN_PROGRESS',
          currentPage: pageNumber,
          revision: data.revision,
          visitedPageNumbers: appendUnique(this.session.visitedPageNumbers, pageNumber),
        }
      },
    })
  }

  async changePage(pageNumber, { signal, clientEventId = this.keyFactory('page') } = {}) {
    if (!this.session) throw new Error('尚无阅读会话')
    return this._runMutation({
      clientEventId,
      signal,
      request: () => this.api.addEvent(this.session.readingSessionId, {
        eventType: 'PAGE_CHANGED',
        pageNumber,
        expectedRevision: this.session.revision,
        clientEventId,
      }, { signal }),
      apply: (data) => {
        this.session = {
          ...this.session,
          currentPage: pageNumber,
          revision: data.revision,
          visitedPageNumbers: appendUnique(this.session.visitedPageNumbers, pageNumber),
        }
      },
    })
  }

  async finish({ signal, clientEventId = this.keyFactory('finish') } = {}) {
    if (!this.session) throw new Error('尚无阅读会话')
    return this._runMutation({
      clientEventId,
      signal,
      request: () => this.api.finish(this.session.readingSessionId, {
        reason: 'USER_FINISH',
        expectedRevision: this.session.revision,
        clientEventId,
      }, { signal }),
      apply: (data) => {
        this.session = data.session
        this.report = data.report
      },
    })
  }

  async guidance(feedback, {
    pageNumber = this.session?.currentPage,
    signal,
    clientEventId = this.keyFactory(feedback.toLowerCase()),
  } = {}) {
    if (!this.session) throw new Error('尚无阅读会话')
    return this._runMutation({
      clientEventId,
      signal,
      request: () => this.api.submitGuidance(this.session.readingSessionId, {
        pageNumber,
        feedback,
        expectedRevision: this.session.revision,
        clientEventId,
      }, { signal }),
      apply: (data) => {
        this.session = { ...this.session, revision: data.revision }
      },
      result: true,
    })
  }

  async askQuestion(inputText, {
    pageNumber = this.session?.currentPage,
    signal,
    clientEventId = this.keyFactory('question'),
  } = {}) {
    if (!this.session) throw new Error('尚无阅读会话')
    return this._runMutation({
      clientEventId,
      signal,
      request: () => this.api.submitQuestion(this.session.readingSessionId, {
        pageNumber,
        inputText,
        expectedRevision: this.session.revision,
        clientEventId,
      }, { signal }),
      apply: (data) => {
        this.session = { ...this.session, revision: data.revision }
      },
      result: true,
    })
  }

  async _runMutation({ request, apply, result = false, signal }) {
    if (!this.session) throw new Error('尚无阅读会话')
    if (this.pending) throw new Error('已有请求正在处理')
    this.pending = true
    this._emit()
    let attempts = 0
    try {
      while (true) {
        try {
          const response = await request()
          apply(response.data)
          this.lastRequestId = response.requestId
          this._emit()
          return result ? { result: response.data, ...this.snapshot() } : this.snapshot()
        } catch (error) {
          if (signal?.aborted) throw error
          if (error instanceof ApiError && error.code === 'REQUEST_IN_PROGRESS' && attempts < 2) {
            attempts += 1
            await this.sleep(retryDelay(error))
            continue
          }
          if (error instanceof ApiError && RECOVERABLE_CODES.has(error.code)) {
            const recovered = await this.restore(this.session.readingSessionId, { signal })
            throw new RecoverableSessionError(
              error.code === 'REQUEST_OUTCOME_UNKNOWN'
                ? '请求结果未知，已重新读取会话状态。'
                : '阅读进度已变化，已恢复为服务端最新状态。',
              { code: error.code, cause: error, recoveredSession: recovered.session },
            )
          }
          throw error
        }
      }
    } finally {
      this.pending = false
      this._emit()
    }
  }
}

function appendUnique(list, value) {
  const source = Array.isArray(list) ? list : []
  return source.includes(value) ? source.slice() : [...source, value]
}

function retryDelay(error) {
  const seconds = Number(error.retryAfter)
  return Number.isFinite(seconds) && seconds > 0 ? seconds * 1000 : 1000
}

function defaultSleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

