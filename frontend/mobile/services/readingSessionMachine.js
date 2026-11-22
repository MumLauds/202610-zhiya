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
    this.answerOptions = initialSnapshot ? (initialSnapshot.answerOptions ?? []) : null
    this.report = initialSnapshot?.report ?? null
    this.lastRequestId = null
    this.pending = false
    this.activeMutation = null
    this.syncRequired = false
    this.unconfirmed = null
  }

  snapshot() {
    return {
      session: this.session,
      events: this.events,
      answerOptions: this.answerOptions,
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
    if (Array.isArray(data?.events)) {
      const isChildPageRead = Array.isArray(data.answerOptions)
        && data.session?.readingSessionId === this.session?.readingSessionId
      // Child snapshots only return the visible page. Keep already seen prompts on earlier pages.
      const earlierQuestions = isChildPageRead ? this.events.filter(event =>
        event.pageNumber !== data.session.currentPage && ['QUESTION', 'FEEDBACK'].includes(event.eventType)) : []
      this.events = [...earlierQuestions, ...data.events]
    }
    if (data?.session) this.session = data.session
    this.answerOptions = data?.answerOptions ?? []
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
      this.answerOptions = null
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

  _addGuardianVisit(eventType, pageNumber, { interactionEventId } = {}) {
    if (this.session.mode !== 'PARENT_CHILD') return
    this.events = [...this.events, { eventType, pageNumber, interactionEventId }]
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
          visitedPageNumbers: [...(this.session.visitedPageNumbers ?? []), pageNumber],
        }
        this._addGuardianVisit('STARTED', pageNumber, data)
        this.answerOptions = null
      },
    })
  }

  async changePage(pageNumber, { signal, clientEventId = this.keyFactory('page') } = {}) {
    if (!this.session) throw new Error('尚无阅读会话')
    return this._runMutation({
      canSupersedeInitial: true,
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
          visitedPageNumbers: [...(this.session.visitedPageNumbers ?? []), pageNumber],
        }
        this._addGuardianVisit('PAGE_CHANGED', pageNumber, data)
        this.answerOptions = null
      },
    })
  }

  async chooseBranch(branchId, { signal, clientEventId = this.keyFactory('branch') } = {}) {
    if (this.session?.status !== 'IN_PROGRESS') throw new Error('请先开始阅读')
    const { readingSessionId, revision } = this.session
    const payload = { branchId, expectedRevision: revision, clientEventId }
    return this._runMutation({
      canSupersedeInitial: true,
      signal,
      request: () => this.api.chooseBranch(readingSessionId, payload, { signal }),
      apply: (data) => {
        this.session = {
          ...this.session,
          currentPage: data.currentPage,
          visitedPageNumbers: data.visitedPageNumbers,
          revision: data.revision,
        }
        this._addGuardianVisit('BRANCH_CHOSEN', data.currentPage, data)
        this.answerOptions = null
      },
    })
  }

  async finish({ signal, clientEventId = this.keyFactory('finish') } = {}) {
    if (!this.session) throw new Error('尚无阅读会话')
    return this._runMutation({
      canSupersedeInitial: true,
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
        this.answerOptions = null
      },
    })
  }

  async answer(inputText) {
    const text = inputText.trim()
    if (!text || [...text].length > 200) throw new Error('回答应为 1 至 200 个字符')
    if (this.session?.status !== 'IN_PROGRESS') throw new Error('请先开始阅读')
    const { readingSessionId, currentPage, revision, visitedPageNumbers } = this.session
    const payload = {
      eventType: 'ANSWER', inputText: text, pageNumber: currentPage,
      expectedRevision: revision, clientEventId: this.keyFactory('answer'),
    }
    return this._runMutation({
      answer: payload,
      answerVisitCount: visitedPageNumbers?.length,
      request: () => this.api.addEvent(readingSessionId, payload),
      apply: (data) => {
        this.session = { ...this.session, revision: Math.max(this.session.revision, data.revision) }
        if (this.session.mode !== 'PARENT_CHILD' || !data.interactionEventId) return
        const answerEvent = { ...data, eventType: 'ANSWER', pageNumber: currentPage, inputText: text }
        const index = this.events.findIndex(event => event.interactionEventId >= data.interactionEventId)
        if (index < 0) this.events = [...this.events, answerEvent]
        else if (this.events[index].interactionEventId === data.interactionEventId) {
          this.events = this.events.map((event, position) => position === index ? { ...event, ...answerEvent } : event)
        } else {
          // A delayed receipt belongs before any navigation event saved after it.
          this.events = [...this.events.slice(0, index), answerEvent, ...this.events.slice(index)]
        }
      },
    })
  }

  async guidance(feedback, {
    pageNumber = this.session?.currentPage,
    signal,
    clientEventId = this.keyFactory(feedback.toLowerCase()),
  } = {}) {
    if (!this.session) throw new Error('尚无阅读会话')
    const expectedRevision = this.session.revision
    return this._runMutation({
      feedback,
      clientEventId,
      signal,
      request: () => this.api.submitGuidance(this.session.readingSessionId, {
        pageNumber,
        feedback,
        expectedRevision,
        clientEventId,
      }, { signal }),
      apply: (data) => {
        this.session = { ...this.session, revision: data.revision }
        if (Array.isArray(data.answerOptions)) this.answerOptions = data.answerOptions
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
    if (this.session?.status !== 'IN_PROGRESS') throw new Error('请先开始阅读')
    const expectedRevision = this.session.revision
    return this._runMutation({
      canSupersedeInitial: true,
      clientEventId,
      signal,
      request: () => this.api.submitQuestion(this.session.readingSessionId, {
        pageNumber,
        inputText,
        expectedRevision,
        clientEventId,
      }, { signal }),
      apply: (data) => {
        this.session = { ...this.session, revision: data.revision }
        this.events = [...this.events, { ...data, pageNumber, eventType: 'ASK' }]
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
      const currentVisit = answer?.pageNumber === this.session.currentPage
        && retry.operation.answerVisitCount === this.session.visitedPageNumbers?.length
      return { ...recovered, confirmedAnswer: currentVisit ? answer.inputText : undefined }
    } catch (error) {
      if (!(error instanceof ApiError) || this.unconfirmed) throw error
      // A retryable 503 during INITIAL replay does not settle the original request.
      const retryInitial = retry.operation.feedback === 'INITIAL' && error.status === 503 && error.retryable
      if (retryInitial || (answer && (error.status === 0 || error.status >= 500))) {
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
    const supersedesInitial = (operation.feedback === 'SKIPPED' || operation.canSupersedeInitial)
      && this.activeMutation?.initialReply
    if (this.pending && !supersedesInitial) throw new Error('已有请求正在处理')
    if (this.syncRequired) throw new Error('请先重新核对会话进度')
    if (supersedesInitial) operation.supersededInitial = this.activeMutation
    this.activeMutation = operation
    this.pending = true
    this._emit()
    let attempts = 0
    try {
      while (this.activeMutation === operation) {
        try {
          const reply = request()
          if (operation.feedback === 'INITIAL') operation.initialReply = reply
          const response = await reply
          if (this.activeMutation !== operation) break
          apply(response.data)
          this.lastRequestId = response.requestId
          this._emit()
          return result ? { result: response.data, ...this.snapshot() } : this.snapshot()
        } catch (error) {
          if (this.activeMutation !== operation) break
          if (signal?.aborted) throw error
          if (error instanceof ApiError && error.code === 'REQUEST_IN_PROGRESS' && attempts < 2) {
            attempts += 1
            await this.sleep(retryDelay(error))
            continue
          }
          const recoverable = error instanceof ApiError && (RECOVERABLE_CODES.has(error.code)
            || error.code === 'REQUEST_IN_PROGRESS' || isSavedAnswerDependencyError(operation, error))
          const initial = operation.supersededInitial
          if (!recoverable && !initial) throw error
          this.syncRequired = true
          this.unconfirmed = pendingReceipt(operation, this.session.revision, error)
          if (initial && !this.unconfirmed) {
            // A rejected skip leaves INITIAL free to commit, even after its connection closes.
            try {
              await initial.initialReply
            } catch (initialError) {
              this.unconfirmed = pendingReceipt(initial, this.session.revision, initialError)
            }
          }
          const recovered = await this.restore(this.session.readingSessionId, { signal })
          if (recoverable) {
            throw new RecoverableSessionError(
              isSavedAnswerDependencyError(operation, error)
                ? '回答已保存。请重新核对进度以确认原回答。'
                : error.code === 'REQUEST_OUTCOME_UNKNOWN'
                  ? '请求结果未知，已重新读取会话状态。'
                  : '阅读进度已变化，已恢复为服务端最新状态。',
              { code: error.code, cause: error, recoveredSession: recovered.session },
            )
          }
          throw error
        }
      }
      // The foreground operation owns UI and recovery once it supersedes INITIAL.
      return this.snapshot()
    } finally {
      if (this.activeMutation === operation) {
        this.activeMutation = null
        this.pending = false
        this._emit()
      }
    }
  }
}

function pendingReceipt(operation, revision, error) {
  if (!(error instanceof ApiError)) return null
  const transportUnconfirmed = error.code === 'REQUEST_OUTCOME_UNKNOWN' && !error.payload?.error?.code
  if (!transportUnconfirmed && error.code !== 'REQUEST_IN_PROGRESS' && !isSavedAnswerDependencyError(operation, error)) return null
  return { operation, revision, retryAt: Date.now() + (error.retryAfter ? retryDelay(error) : 0) }
}

function isSavedAnswerDependencyError(operation, error) {
  // API-015's retryable 503 is returned after committing the answer but before finalizing its reply.
  return Boolean(operation.answer && error.status === 503 && error.code === 'DEPENDENCY_UNAVAILABLE' && error.retryable)
}

function retryDelay(error) {
  const seconds = Number(error.retryAfter)
  return Number.isFinite(seconds) && seconds > 0 ? seconds * 1000 : 1000
}

function defaultSleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

