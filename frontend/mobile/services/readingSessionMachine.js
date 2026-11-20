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
  constructor({ api, onChange, initialSnapshot, keyFactory = createClientEventId, sleep = defaultSleep }) {
    this.api = api
    this.onChange = onChange ?? (() => {})
    this.keyFactory = keyFactory
    this.sleep = sleep
    this.session = initialSnapshot?.session ?? null
    this.events = initialSnapshot?.events ?? []
    this.report = initialSnapshot?.report ?? null
    this.lastRequestId = null
    this.pending = false
    this.syncRequired = false
    this.unconfirmed = null
  }

  snapshot() {
    return {
      session: this.session,
      events: this.events,
      report: this.report,
      lastRequestId: this.lastRequestId,
      pending: this.pending,
      syncRequired: this.syncRequired,
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
      // A newer revision may belong to another device. Only the original ANSWER key proves receipt.
      if (!this.unconfirmed?.operation.answer && result.data.session.revision !== this.unconfirmed?.revision) {
        this.unconfirmed = null
      }
      this.syncRequired = Boolean(this.unconfirmed)
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

  async answer(inputText) {
    const text = inputText.trim()
    if (!text || [...text].length > 200) throw new Error('回答应为 1 至 200 个字符')
    if (this.session?.status !== 'IN_PROGRESS') throw new Error('请先开始阅读')
    const payload = {
      eventType: 'ANSWER', inputText: text, pageNumber: this.session.currentPage,
      expectedRevision: this.session.revision, clientEventId: this.keyFactory('answer'),
    }
    return this._runMutation({
      answer: payload,
      request: () => this.api.addEvent(this.session.readingSessionId, payload),
      apply: (data) => { this.session = { ...this.session, revision: Math.max(this.session.revision, data.revision) } },
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
        this.events = [...this.events, {
          ...data, pageNumber, feedback, eventType: feedback === 'INITIAL' ? 'QUESTION' : 'FEEDBACK',
        }]
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

  async reconcile() {
    await this.restore(this.session.readingSessionId)
    const retry = this.unconfirmed
    if (!retry) return this.snapshot()
    if (Date.now() < retry.retryAt) throw new Error('原请求仍在处理，请稍后再核对。')
    this.unconfirmed = null
    this.syncRequired = false
    const answer = retry.operation.answer
    try {
      const recovered = await this._runMutation(retry.operation)
      return { ...recovered, confirmedAnswer: answer?.pageNumber === this.session.currentPage ? answer.inputText : undefined }
    } catch (error) {
      // An unavailable receipt store cannot tell us whether the original answer was committed.
      if (answer && !this.unconfirmed && error instanceof ApiError && (error.status === 0 || error.status >= 500)) {
        this.unconfirmed = retry
        this.syncRequired = true
        this._emit()
      }
      throw error
    }
  }

  async _runMutation(operation) {
    const { request, apply, result = false, signal } = operation
    if (!this.session) throw new Error('尚无阅读会话')
    if (this.pending) throw new Error('已有请求正在处理')
    if (this.syncRequired) throw new Error('请先重新核对会话进度')
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
          if (error instanceof ApiError && (RECOVERABLE_CODES.has(error.code) || error.code === 'REQUEST_IN_PROGRESS')) {
            this.syncRequired = true
            // A transport failure can precede the server commit. Retain the same event key
            // if the first read still shows the original revision.
            const transportUnconfirmed = error.code === 'REQUEST_OUTCOME_UNKNOWN' && !error.payload?.error?.code
            if (transportUnconfirmed || error.code === 'REQUEST_IN_PROGRESS') {
              this.unconfirmed = {
                operation, revision: this.session.revision,
                retryAt: Date.now() + (error.retryAfter ? retryDelay(error) : 0),
              }
            }
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

