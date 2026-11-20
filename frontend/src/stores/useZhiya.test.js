import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/client'
import * as auth from '../api/auth'
import * as profiles from '../api/profiles'
import * as books from '../api/storybooks'
import * as reading from '../api/reading'
import * as adminApi from '../api/admin'

vi.mock('../api/auth', () => ({ checkHealth: vi.fn(), currentIdentity: vi.fn(), login: vi.fn(), logout: vi.fn(), parentUnlock: vi.fn(), register: vi.fn() }))
vi.mock('../api/profiles', () => ({ listProfiles: vi.fn(), createProfile: vi.fn(), updateProfile: vi.fn(), deleteProfile: vi.fn() }))
vi.mock('../api/storybooks', () => ({ listStorybooks: vi.fn(), getStoryVersion: vi.fn(), recommendStorybooks: vi.fn() }))
vi.mock('../api/admin', () => ({ listAdminStoryVersions: vi.fn(), listAdminKnowledge: vi.fn(), createAdminStory: vi.fn(), updateAdminStoryStatus: vi.fn(), createAdminKnowledge: vi.fn(), updateAdminKnowledge: vi.fn() }))
vi.mock('../api/reading', () => ({
  createReadingSession: vi.fn(),
  getReadingSession: vi.fn(),
  addReadingEvent: vi.fn(),
  requestReadingGuidance: vi.fn(),
  chooseReadingBranch: vi.fn(),
  completeReadingSession: vi.fn(),
  listReadingSessions: vi.fn(),
  deleteReadingSession: vi.fn(),
  updateReadingMode: vi.fn(),
}))

const session = (overrides = {}) => ({ readingSessionId: 55, storyVersionId: 88, mode: 'PARENT_CHILD', currentPage: 1, visitedPageNumbers: [1], revision: 1, status: 'IN_PROGRESS', ...overrides })
const detail = (overrides = {}) => ({ session: session(overrides), events: [], report: null })
const version = { storyVersionId: 88, title: '测试绘本', status: 'APPROVED_PRIVATE', pages: [{ pageNumber: 1, text: '一', defaultNextPageNumber: 3 }, { pageNumber: 3, text: '三', defaultNextPageNumber: 7 }, { pageNumber: 7, text: '七', defaultNextPageNumber: null }] }
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }
let state, actions

beforeEach(async () => {
  vi.resetModules()
  vi.resetAllMocks()
  vi.useFakeTimers()
  localStorage.clear()
  sessionStorage.clear()
  window.history.replaceState({}, '', '/#reader/55')
  auth.login.mockResolvedValue({ data: { accountId: 1 } })
  auth.logout.mockResolvedValue(null)
  profiles.listProfiles.mockResolvedValue([{ childProfileId: 1, nickname: '小芽' }])
  books.listStorybooks.mockResolvedValue([version])
  books.getStoryVersion.mockResolvedValue(version)
  books.recommendStorybooks.mockResolvedValue([])
  auth.currentIdentity.mockResolvedValue({ role: 'GUARDIAN', accountId: 1 })
  adminApi.listAdminStoryVersions.mockResolvedValue({ items: [] })
  adminApi.listAdminKnowledge.mockResolvedValue({ items: [], page: 1, pageSize: 100 })
  reading.getReadingSession.mockImplementation(async () => detail())
  reading.addReadingEvent.mockResolvedValue({ revision: 2 })
  reading.requestReadingGuidance.mockResolvedValue({ interactionEventId: 2, revision: 2, outputText: '你能指一指小兔吗？', strategyType: 'CONCRETE_RECALL', status: 'GENERATED', sources: [] })
  reading.chooseReadingBranch.mockResolvedValue({ readingSessionId: 55, branchId: 501, currentPage: 3, revision: 2, interactionEventId: 3, visitedPageNumbers: [1, 3], nextPage: { pageNumber: 3 } })
  reading.listReadingSessions.mockResolvedValue([session()])
  profiles.deleteProfile.mockResolvedValue(undefined)
  reading.deleteReadingSession.mockResolvedValue(undefined)
  reading.updateReadingMode.mockResolvedValue({ mode: 'CHILD_SOLO', childGrantId: 4, allowedStoryVersionIds: [88], expiresAt: '2026-11-18T08:00:00Z' })
  auth.parentUnlock.mockResolvedValue({ accountId: 1 })
  const store = await import('./useZhiya')
  state = store.zhiya
  actions = store.zhiyaActions
  await actions.loadReaderRoute()
})
afterEach(() => { vi.clearAllTimers(); vi.useRealTimers() })

describe('cold reader bookshelf fallback', () => {
  it.each([
    ['the bookshelf resolves before a 503', true, 503],
    ['the bookshelf resolves after a 503', false, 503],
    ['the bookshelf resolves after a 404', false, 404],
  ])('handles the existing session when %s', async (_, bookshelfFirst, status) => {
    localStorage.setItem('zhiya.account', 'parent-a')
    const firstPage = deferred()
    const bookDetail = deferred()
    books.listStorybooks.mockClear().mockReturnValueOnce(firstPage.promise)
    if (status === 404) books.listStorybooks.mockResolvedValueOnce([{ ...version, storyVersionId: 89 }])
    books.getStoryVersion.mockClear().mockReturnValueOnce(bookDetail.promise)
    reading.getReadingSession.mockClear()
    vi.resetModules()
    const { zhiya: refreshed, zhiyaActions: refreshedActions } = await import('./useZhiya')
    const bootstrapping = refreshedActions.bootstrap()

    await vi.waitFor(() => expect(books.getStoryVersion).toHaveBeenCalledWith(88))
    expect(books.listStorybooks.mock.calls.map(([request]) => request.page)).toEqual([1])
    if (bookshelfFirst) {
      firstPage.resolve([version])
      await vi.waitFor(() => expect(refreshed.booksSource).toBe('api'))
      bookDetail.reject(new ApiError('暂不可用', { status }))
    } else {
      bookDetail.reject(new ApiError('暂不可用', { status }))
      await vi.advanceTimersByTimeAsync(0)
      if (status === 404) {
        expect(books.listStorybooks.mock.calls.map(([request]) => request.page)).toEqual([1, 1])
        expect(refreshed.booksSource).toBe('api')
      }
      firstPage.resolve([version])
    }
    await bootstrapping

    expect(location.hash).toBe('#reader/55')
    expect(refreshed.sessionLoading).toBe(false)
    expect(reading.getReadingSession.mock.calls).toEqual([[55]])
    expect(books.getStoryVersion.mock.calls).toEqual([[88]])
    expect(reading.createReadingSession).not.toHaveBeenCalled()
    expect(reading.addReadingEvent).not.toHaveBeenCalled()
    if (status === 404) {
      expect(refreshed.books.some((book) => book.source === 'api' && book.storyVersionId === 88)).toBe(false)
      expect(refreshed.currentSession).toBeNull()
      expect(refreshed.currentBook).toBeNull()
    } else {
      expect(refreshed.currentSession).toEqual(session())
      expect(refreshed.currentBook).toMatchObject({
        storyVersionId: 88,
        source: 'api',
        title: version.title,
        pages: version.pages.map(({ text }) => ({ text })),
      })
    }
  })
})

describe('authoritative reading progress', () => {
  it('preserves each visit after resuming a branch and revisiting the default next page', async () => {
    reading.getReadingSession.mockResolvedValueOnce(detail({ visitedPageNumbers: [1, 3, 1], revision: 3 }))
    await actions.loadReaderRoute()
    reading.addReadingEvent.mockResolvedValueOnce({ revision: 4 })
    expect(await actions.recordPageChange(3)).toBe(true)
    expect(state.currentSession.visitedPageNumbers).toEqual([1, 3, 1, 3])
    expect(state.currentSession.revision).toBe(4)
  })

  it('serializes page writes and rejects destinations other than the default next page', async () => {
    const request = deferred()
    reading.addReadingEvent.mockReturnValueOnce(request.promise)
    const first = actions.recordPageChange(3)
    expect(await actions.recordPageChange(7)).toBe(false)
    expect(await actions.completeReading()).toBe(false)
    expect(state.currentSession.currentPage).toBe(1)
    request.resolve({ revision: 2 })
    expect(await first).toBe(true)
    expect(reading.addReadingEvent).toHaveBeenCalledTimes(1)
    expect(reading.addReadingEvent.mock.calls[0][1]).toMatchObject({ pageNumber: 3, expectedRevision: 1, eventType: 'PAGE_CHANGED' })
    expect(await actions.recordPageChange(1)).toBe(false)
    reading.addReadingEvent.mockResolvedValueOnce({ revision: 3 })
    expect(await actions.recordPageChange(7)).toBe(true)
    expect(reading.addReadingEvent.mock.calls[1][1].expectedRevision).toBe(2)
    expect(state.currentSession.currentPage).toBe(7)
  })

  it('preserves repeated visits when a new page change follows a branch back', async () => {
    reading.getReadingSession.mockResolvedValueOnce(detail({ currentPage: 1, visitedPageNumbers: [1, 3, 1] }))
    await actions.loadReaderRoute()
    expect(await actions.recordPageChange(3)).toBe(true)
    expect(state.currentSession.visitedPageNumbers).toEqual([1, 3, 1, 3])
  })

  it('resynchronizes all session details after a 409 and uses the new revision', async () => {
    reading.addReadingEvent.mockRejectedValueOnce(new ApiError('changed', { status: 409, code: 'SESSION_CONFLICT' }))
    reading.getReadingSession.mockResolvedValueOnce(detail({ currentPage: 3, visitedPageNumbers: [1, 3], revision: 5 }))
    expect(await actions.recordPageChange(3)).toBe(false)
    expect(state.currentSession.currentPage).toBe(3)
    expect(state.readingSyncRequired).toBe(false)
    await actions.recordPageChange(7)
    expect(reading.addReadingEvent.mock.calls[1][1].expectedRevision).toBe(5)
  })

  it('does not replay a timed out write when GET confirms newer progress', async () => {
    reading.addReadingEvent.mockRejectedValueOnce(new ApiError('timeout', { status: 504, code: 'REQUEST_OUTCOME_UNKNOWN' }))
    reading.getReadingSession.mockResolvedValueOnce(detail({ currentPage: 3, visitedPageNumbers: [1, 3], revision: 2 }))
    await actions.recordPageChange(3)
    expect(state.currentSession.currentPage).toBe(3)
    expect(state.readingSyncRequired).toBe(false)
    expect(reading.addReadingEvent).toHaveBeenCalledTimes(1)
  })

  it.each([
    [0, 'REQUEST_OUTCOME_UNKNOWN'],
    [409, 'REQUEST_IN_PROGRESS'],
    [504, 'REQUEST_FAILED'],
  ])('reuses the exact original write after a transport error or pending response (%s/%s)', async (status, code) => {
    reading.addReadingEvent.mockRejectedValueOnce(new ApiError('pending', { status, code }))
    reading.getReadingSession.mockRejectedValueOnce(new Error('offline'))
    await actions.recordPageChange(3)
    const original = structuredClone(reading.addReadingEvent.mock.calls[0][1])
    expect(state.readingSyncRequired).toBe(true)
    expect(await actions.recordPageChange(3)).toBe(false)
    expect(await actions.completeReading()).toBe(false)
    await actions.refreshReading()
    expect(reading.addReadingEvent).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1100)
    await actions.refreshReading()
    expect(reading.addReadingEvent.mock.calls[1][1]).toEqual(original)
    expect(state.currentSession.currentPage).toBe(3)
    expect(state.readingSyncRequired).toBe(false)
  })

  it('reads back a terminal 504 and creates a different event only on the next user action', async () => {
    reading.addReadingEvent.mockRejectedValueOnce(new ApiError('unknown', { status: 504, code: 'REQUEST_OUTCOME_UNKNOWN' }))
    expect(await actions.recordPageChange(3)).toBe(false)
    expect(state.currentSession.currentPage).toBe(1)
    expect(state.readingSyncRequired).toBe(false)
    expect(reading.addReadingEvent).toHaveBeenCalledTimes(1)
    const original = reading.addReadingEvent.mock.calls[0][1]
    expect(await actions.recordPageChange(3)).toBe(true)
    const next = reading.addReadingEvent.mock.calls[1][1]
    expect(next.expectedRevision).toBe(original.expectedRevision)
    expect(next.clientEventId).not.toBe(original.clientEventId)
    expect(state.currentSession.currentPage).toBe(3)
  })

  it('keeps writes locked until the read after a terminal 504 succeeds, without replaying the old key', async () => {
    reading.completeReadingSession.mockRejectedValueOnce(new ApiError('unknown', { status: 504, code: 'REQUEST_OUTCOME_UNKNOWN' }))
    reading.getReadingSession.mockRejectedValueOnce(new Error('offline'))
    expect(await actions.completeReading()).toBe(false)
    expect(state.readingSyncRequired).toBe(true)
    expect(await actions.recordPageChange(3)).toBe(false)
    await vi.advanceTimersByTimeAsync(2000)
    expect(await actions.refreshReading()).toBe(true)
    expect(state.readingSyncRequired).toBe(false)
    expect(reading.completeReadingSession).toHaveBeenCalledTimes(1)
    expect(reading.addReadingEvent).not.toHaveBeenCalled()
  })

  it.each(['READY', 'FAILED'])('reads an updated %s report without submitting completion again', async (status) => {
    const finished = detail({ status: 'FINISHED', revision: 2 })
    reading.completeReadingSession.mockResolvedValueOnce({ ...finished, report: { status: 'PENDING', summary: '事实摘要' } })
    expect(await actions.completeReading()).toBe(true)
    const report = { status, summary: status === 'READY' ? '最终小结' : '事实摘要' }
    reading.getReadingSession.mockRejectedValueOnce(new Error('离线')).mockResolvedValueOnce({ ...finished, report })
    expect(await actions.refreshReading()).toBe(false)
    expect(state.currentReport.status).toBe('PENDING')
    expect(state.readingSyncRequired).toBe(false)
    expect(await actions.refreshReading()).toBe(true)
    expect(state.currentReport).toEqual(report)
    expect(reading.completeReadingSession).toHaveBeenCalledTimes(1)
    expect(reading.addReadingEvent).not.toHaveBeenCalled()
  })

  it('handles completion conflicts using the same refresh path', async () => {
    reading.completeReadingSession.mockRejectedValueOnce(new ApiError('changed', { status: 409, code: 'SESSION_CONFLICT' }))
    reading.getReadingSession.mockResolvedValueOnce({ ...detail({ status: 'FINISHED', revision: 8 }), report: { summary: '已完成' } })
    expect(await actions.completeReading()).toBe(false)
    expect(state.currentSession.status).toBe('FINISHED')
    expect(state.currentReport.summary).toBe('已完成')
    expect(state.readingSyncRequired).toBe(false)
  })

  it('starts a restored CREATED session with its authoritative revision', async () => {
    reading.getReadingSession.mockResolvedValueOnce(detail({ status: 'CREATED', revision: 0, visitedPageNumbers: [] }))
    reading.addReadingEvent.mockResolvedValueOnce({ revision: 1 })
    await actions.loadReaderRoute()
    expect(reading.addReadingEvent).not.toHaveBeenCalled()
    expect(await actions.startCurrentReading()).toBe(true)
    expect(reading.addReadingEvent.mock.calls[0][1]).toMatchObject({ eventType: 'STARTED', expectedRevision: 0, pageNumber: 1 })
    expect(state.currentSession.status).toBe('IN_PROGRESS')
  })

  it('uses API-017 for an initial question and appends the recoverable event', async () => {
    expect(await actions.requestGuidance('INITIAL')).toBe(true)
    expect(reading.requestReadingGuidance).toHaveBeenCalledWith(55, expect.objectContaining({
      pageNumber: 1,
      feedback: 'INITIAL',
      expectedRevision: 1,
    }))
    expect(state.currentSession.revision).toBe(2)
    expect(state.currentEvents.at(-1)).toMatchObject({
      interactionEventId: 2,
      eventType: 'QUESTION',
      pageNumber: 1,
      outputText: '你能指一指小兔吗？',
      strategyType: 'CONCRETE_RECALL',
    })
  })

  it('records understanding feedback as recoverable FEEDBACK without advancing the page', async () => {
    expect(await actions.requestGuidance('UNDERSTOOD')).toBe(true)
    expect(reading.requestReadingGuidance.mock.calls[0][1]).toMatchObject({
      pageNumber: 1,
      feedback: 'UNDERSTOOD',
      expectedRevision: 1,
    })
    expect(state.currentSession.currentPage).toBe(1)
    expect(state.currentEvents.at(-1)).toMatchObject({
      eventType: 'FEEDBACK',
      feedback: 'UNDERSTOOD',
      outputText: '你能指一指小兔吗？',
    })
  })

  it('submits a trimmed ANSWER through API-015 and updates the revision', async () => {
    reading.addReadingEvent.mockResolvedValueOnce({ interactionEventId: 9, revision: 3 })
    expect(await actions.submitAnswer('  小兔躲在树后。  ')).toBe(true)
    expect(reading.addReadingEvent.mock.calls[0][1]).toMatchObject({
      eventType: 'ANSWER',
      pageNumber: 1,
      inputText: '小兔躲在树后。',
      expectedRevision: 1,
    })
    expect(state.currentSession.revision).toBe(3)
    expect(state.currentEvents.at(-1)).toMatchObject({ eventType: 'ANSWER', interactionEventId: 9 })
  })

  it('chooses an approved branch through API-018 and installs the authoritative path', async () => {
    expect(await actions.chooseBranch(501)).toBe(true)
    expect(reading.chooseReadingBranch).toHaveBeenCalledWith(55, expect.objectContaining({
      branchId: 501,
      expectedRevision: 1,
    }))
    expect(state.currentSession).toMatchObject({
      currentPage: 3,
      visitedPageNumbers: [1, 3],
      revision: 2,
      status: 'IN_PROGRESS',
    })
    expect(state.currentEvents.at(-1)).toMatchObject({
      eventType: 'BRANCH_CHOSEN',
      branchId: 501,
      pageNumber: 1,
    })
  })

  it.each(['page', 'skip'])('allows %s to supersede a pending automatic question', async (kind) => {
    const initial = deferred()
    reading.requestReadingGuidance.mockReturnValueOnce(initial.promise)
    const generating = actions.requestGuidance('INITIAL')
    if (kind === 'page') {
      expect(await actions.recordPageChange(3)).toBe(true)
    } else {
      reading.requestReadingGuidance.mockResolvedValueOnce({ interactionEventId: 5, revision: 2, strategyType: 'SKIP', outputText: '' })
      expect(await actions.requestGuidance('SKIPPED')).toBe(true)
    }
    initial.resolve({ interactionEventId: 99, revision: 2, outputText: '迟到的问题' })
    expect(await generating).toBe(false)
    expect(state.currentEvents.some(event => event.interactionEventId === 99)).toBe(false)
    expect(state.currentSession.currentPage).toBe(kind === 'page' ? 3 : 1)
    expect(state.currentSession.revision).toBe(2)
    expect(state.readingSyncRequired).toBe(false)
  })

  it('completes one page turn when INITIAL was saved before its response reached the reader', async () => {
    const response = deferred()
    const server = detail()
    const question = { interactionEventId: 4, eventType: 'QUESTION', pageNumber: 1, revision: 2, outputText: '小兔在哪里？', strategyType: 'CONTEXT_QUESTION' }
    reading.requestReadingGuidance.mockImplementationOnce(() => {
      server.session.revision = question.revision
      server.events.push(question)
      return response.promise
    })
    reading.getReadingSession.mockImplementationOnce(async () => structuredClone(server))
    reading.addReadingEvent.mockImplementation(async (_, payload) => {
      if (payload.expectedRevision !== server.session.revision) throw new ApiError('changed', { status: 409, code: 'SESSION_CONFLICT' })
      server.session.currentPage = payload.pageNumber
      server.session.visitedPageNumbers.push(payload.pageNumber)
      return { revision: ++server.session.revision }
    })

    const generating = actions.requestGuidance('INITIAL')
    expect(state.currentSession.revision).toBe(1)
    expect(await actions.recordPageChange(3)).toBe(true)
    const attempts = reading.addReadingEvent.mock.calls.map(([, payload]) => payload)
    expect(attempts.map(p => [p.eventType, p.pageNumber, p.expectedRevision])).toEqual([['PAGE_CHANGED', 3, 1], ['PAGE_CHANGED', 3, 2]])
    expect(attempts[1].clientEventId).not.toBe(attempts[0].clientEventId)
    response.resolve(question)
    expect(await generating).toBe(false)
    expect(state.currentSession).toEqual(server.session)
    expect(state.currentSession.visitedPageNumbers).toEqual([1, 3])
    expect(state.currentEvents).toEqual([question])
    expect(state.readingSyncRequired).toBe(false)
  })

  it.each([
    ['another kind of event', 'ANSWER', 1, 2, 2],
    ['a historical question', 'QUESTION', 1, 1, 2],
    ['a different page', 'QUESTION', 3, 2, 2],
    ['additional writes', 'QUESTION', 1, 3, 3],
  ])('does not repeat a page turn when readback shows %s', async (_, eventType, pageNumber, eventRevision, revision) => {
    const response = deferred()
    reading.requestReadingGuidance.mockReturnValueOnce(response.promise)
    const generating = actions.requestGuidance('INITIAL')
    reading.addReadingEvent.mockRejectedValueOnce(new ApiError('changed', { status: 409, code: 'SESSION_CONFLICT' }))
    reading.getReadingSession.mockResolvedValueOnce({ ...detail({ revision }), events: [{ eventType, pageNumber, revision: eventRevision }] })

    expect(await actions.recordPageChange(3)).toBe(false)
    expect(reading.addReadingEvent).toHaveBeenCalledTimes(1)
    expect(state.currentSession.currentPage).toBe(1)
    response.resolve({ revision: 2 })
    expect(await generating).toBe(false)
  })

  it('keeps the authoritative progress when another write beats the retried turn', async () => {
    const response = deferred()
    reading.requestReadingGuidance.mockReturnValueOnce(response.promise)
    const generating = actions.requestGuidance('INITIAL')
    const conflict = new ApiError('changed', { status: 409, code: 'SESSION_CONFLICT' })
    reading.addReadingEvent.mockRejectedValueOnce(conflict).mockRejectedValueOnce(conflict)
    reading.getReadingSession
      .mockResolvedValueOnce({ ...detail({ revision: 2 }), events: [{ eventType: 'QUESTION', pageNumber: 1, revision: 2 }] })
      .mockResolvedValueOnce(detail({ currentPage: 3, visitedPageNumbers: [1, 3], revision: 3 }))

    expect(await actions.recordPageChange(3)).toBe(false)
    expect(reading.addReadingEvent).toHaveBeenCalledTimes(2)
    expect(state.currentSession.visitedPageNumbers).toEqual([1, 3])
    response.resolve({ revision: 2 })
    expect(await generating).toBe(false)
  })

  it.each(['reader', 'library'])('drops the old turn when navigating to %s during the conflict readback', async (destination) => {
    const response = deferred()
    const readback = deferred()
    reading.requestReadingGuidance.mockReturnValueOnce(response.promise)
    const generating = actions.requestGuidance('INITIAL')
    reading.addReadingEvent.mockRejectedValueOnce(new ApiError('changed', { status: 409, code: 'SESSION_CONFLICT' }))
    reading.getReadingSession.mockClear().mockReturnValueOnce(readback.promise)

    const turning = actions.recordPageChange(3)
    await vi.waitFor(() => expect(reading.getReadingSession).toHaveBeenCalledOnce())
    if (destination === 'reader') {
      reading.getReadingSession.mockResolvedValueOnce(detail({ currentPage: 7, visitedPageNumbers: [1, 3, 7], revision: 5 }))
      await actions.loadReaderRoute()
    } else {
      actions.navigate('library')
      await actions.syncRoute()
    }
    readback.resolve({ ...detail({ revision: 2 }), events: [{ eventType: 'QUESTION', pageNumber: 1, revision: 2 }] })
    expect(await turning).toBe(false)
    response.resolve({ revision: 2 })
    expect(await generating).toBe(false)
    expect(reading.addReadingEvent).toHaveBeenCalledTimes(1)
    if (destination === 'reader') {
      expect(state.currentSession.currentPage).toBe(7)
      expect(state.currentSession.revision).toBe(5)
    } else {
      expect(state.route.name).toBe('library')
    }
  })

  it('ignores a question for an older revision after an authoritative refresh', async () => {
    const initial = deferred()
    reading.requestReadingGuidance.mockReturnValueOnce(initial.promise)
    const generating = actions.requestGuidance('INITIAL')
    reading.getReadingSession.mockResolvedValueOnce(detail({ currentPage: 3, visitedPageNumbers: [1, 3], revision: 6 }))
    expect(await actions.refreshReading()).toBe(true)
    initial.resolve({ interactionEventId: 99, revision: 2, outputText: '旧问题' })
    expect(await generating).toBe(false)
    expect(state.currentSession.revision).toBe(6)
    expect(state.currentEvents).toEqual([])
  })

  it('replays the exact guidance request after an unknown transport outcome', async () => {
    reading.requestReadingGuidance.mockRejectedValueOnce(new ApiError('pending', { status: 0, code: 'REQUEST_OUTCOME_UNKNOWN' }))
    reading.getReadingSession.mockRejectedValueOnce(new Error('offline'))
    expect(await actions.requestGuidance('INITIAL')).toBe(false)
    const original = structuredClone(reading.requestReadingGuidance.mock.calls[0][1])
    expect(state.readingSyncRequired).toBe(true)
    await vi.advanceTimersByTimeAsync(1100)
    expect(await actions.refreshReading()).toBe(true)
    expect(reading.requestReadingGuidance).toHaveBeenCalledTimes(2)
    expect(reading.requestReadingGuidance.mock.calls[1][1]).toEqual(original)
    expect(state.readingSyncRequired).toBe(false)
  })

  it('reloads the session after a guidance conflict and uses its new revision', async () => {
    reading.requestReadingGuidance.mockRejectedValueOnce(new ApiError('changed', { status: 409, code: 'SESSION_CONFLICT' }))
    reading.getReadingSession.mockResolvedValueOnce(detail({ revision: 6 }))
    expect(await actions.requestGuidance('INITIAL')).toBe(false)
    expect(state.currentSession.revision).toBe(6)
    expect(state.readingSyncRequired).toBe(false)
    reading.requestReadingGuidance.mockResolvedValueOnce({ interactionEventId: 18, revision: 7, outputText: '再看一次画面。', strategyType: 'CONCRETE_RECALL' })
    expect(await actions.requestGuidance('INITIAL')).toBe(true)
    expect(reading.requestReadingGuidance.mock.calls[1][1].expectedRevision).toBe(6)
    expect(state.currentSession.revision).toBe(7)
  })

  it('does not overwrite a reloaded session with a late result from the old view', async () => {
    const pending = deferred()
    reading.addReadingEvent.mockReturnValueOnce(pending.promise)
    const oldWrite = actions.recordPageChange(3)
    reading.getReadingSession.mockResolvedValueOnce(detail({ currentPage: 7, visitedPageNumbers: [1, 3, 7], revision: 5 }))
    await actions.loadReaderRoute()
    pending.resolve({ revision: 2 })
    await oldWrite
    expect(state.currentSession.currentPage).toBe(7)
    expect(state.currentSession.revision).toBe(5)
  })
})

describe('recommendations and content operations', () => {
  it('loads recommendations for the active profile and resolves their shelf records', async () => {
    books.recommendStorybooks.mockResolvedValueOnce([
      { storyVersionId: 88, reason: '符合 4～5 岁与兴趣「动物」', sources: [] },
    ])
    auth.login.mockResolvedValueOnce({ data: { accountId: 1, role: 'GUARDIAN' } })
    await actions.signIn('parent-a', 'password123')
    expect(books.recommendStorybooks).toHaveBeenCalledWith({
      childProfileId: 1,
      topic: undefined,
      minutes: undefined,
    })
    expect(state.recommendations).toHaveLength(1)
    expect(state.recommendations[0]).toMatchObject({
      storyVersionId: 88,
      reason: '符合 4～5 岁与兴趣「动物」',
      book: { storyVersionId: 88, title: '测试绘本' },
    })
  })

  it('clears displayed topic reasons immediately and ignores late results after reset', async () => {
    await actions.signIn('parent-a', 'password123')
    const old = { storyVersionId: 88, reason: '符合主题「友谊」' }
    books.recommendStorybooks.mockResolvedValueOnce([old])
    await actions.loadRecommendations('友谊')
    expect(state.recommendations[0].reason).toBe(old.reason)
    const pending = deferred()
    const reset = deferred()
    books.recommendStorybooks.mockReturnValueOnce(pending.promise).mockReturnValueOnce(reset.promise)
    const refreshing = actions.loadRecommendations('友谊')
    const resetting = actions.loadRecommendations('')
    expect(state.recommendations).toEqual([])
    reset.resolve([{ storyVersionId: 88, reason: '符合年龄' }])
    expect(await resetting).toBe(true)
    pending.resolve([old])
    expect(await refreshing).toBe(false)
    expect(state.recommendations[0].reason).toBe('符合年龄')
  })

  it('clears recommendations and discards a late result after deleting the selected profile', async () => {
    await actions.signIn('parent-a', 'password123')
    const pending = deferred()
    books.recommendStorybooks.mockReturnValueOnce(pending.promise)
    const loading = actions.loadRecommendations()
    profiles.listProfiles.mockResolvedValue([])
    expect(await actions.removeProfile(1)).toBe(true)
    pending.resolve([{ storyVersionId: 88, reason: '旧档案的推荐' }])
    expect(await loading).toBe(false)
    expect(state.recommendations).toEqual([])
    expect(state.recommendationsLoading).toBe(false)
  })

  it.each([true, false])('retires the old publication after publish (catalogue refresh succeeds: %s)', async (refreshSucceeds) => {
    auth.login.mockResolvedValueOnce({ data: { accountId: 9 } })
    auth.currentIdentity.mockResolvedValue({ role: 'ADMIN', accountId: 9 })
    await actions.signIn('content-admin', 'password123')
    const old = { storybookId: 201, storyVersionId: 301, status: 'PUBLISHED' }
    const draft = { ...old, storyVersionId: 302, status: 'DRAFT' }
    const other = { ...old, storybookId: 202, storyVersionId: 303 }
    adminApi.listAdminStoryVersions.mockResolvedValueOnce({ items: [old, draft, other] })
    await actions.loadAdminContent('storybooks')
    const published = { ...draft, status: 'PUBLISHED' }
    adminApi.updateAdminStoryStatus.mockResolvedValueOnce(published)
    if (refreshSucceeds) {
      adminApi.listAdminStoryVersions.mockResolvedValueOnce({ items: [
        { ...old, status: 'RETIRED' }, { ...published, title: '服务端标题' }, other,
      ] })
    } else {
      adminApi.listAdminStoryVersions.mockRejectedValueOnce(new ApiError('暂不可用', { status: 503 }))
    }
    expect(await actions.adminSetStoryStatus(302, 'PUBLISHED')).toBe(true)
    expect(state.admin.storyVersions.map(item => item.status)).toEqual(['RETIRED', 'PUBLISHED', 'PUBLISHED'])
    if (refreshSucceeds) expect(state.admin.storyVersions[1].title).toBe('服务端标题')
    else {
      expect(state.admin.error).toBeTruthy()
      expect(state.admin.storiesLoaded).toBe(false)
    }
    expect(adminApi.updateAdminStoryStatus).toHaveBeenCalledTimes(1)
  })

  it('routes a content role to the prepared management workspace', async () => {
    auth.login.mockResolvedValueOnce({ data: { accountId: 9 } })
    auth.currentIdentity.mockResolvedValue({ role: 'ADMIN', accountId: 9 })
    profiles.listProfiles.mockClear()
    adminApi.listAdminKnowledge.mockResolvedValueOnce({
      items: [{ knowledgeItemId: 301, title: '引导知识', status: 'DRAFT' }],
      page: 1,
      pageSize: 100,
    })
    await actions.signIn('content-admin', 'password123')
    expect(state.auth.role).toBe('ADMIN')
    expect(location.hash).toBe('#admin/storybooks')
    expect(profiles.listProfiles).not.toHaveBeenCalled()
    expect(adminApi.listAdminKnowledge).not.toHaveBeenCalled()
    adminApi.listAdminStoryVersions.mockRejectedValueOnce(new ApiError('未上线', { status: 404 }))
    expect(await actions.loadAdminContent('storybooks')).toBe(false)
    expect(state.admin.error).toContain('绘本列表')
    expect(state.admin.knowledgeError).toBe('')
    window.history.replaceState({}, '', '/#admin/knowledge')
    vi.resetModules()
    const restored = await import('./useZhiya')
    await restored.zhiyaActions.bootstrap()
    expect(restored.zhiya.auth.role).toBe('ADMIN')
    expect(restored.zhiya.route.section).toBe('knowledge')
    expect(restored.zhiya.admin.knowledgeItems).toHaveLength(1)
    expect(profiles.listProfiles).not.toHaveBeenCalled()
    expect(restored.zhiya.admin.knowledgeItems[0].title).toBe('引导知识')
  })
})

describe('admin workspace recovery after failed logout', () => {
  beforeEach(async () => {
    auth.login.mockResolvedValueOnce({ data: { accountId: 9 } })
    auth.currentIdentity.mockResolvedValue({ role: 'ADMIN', accountId: 9 })
    await actions.signIn('content-admin', 'password123')
    auth.logout.mockRejectedValueOnce(new ApiError('暂不可用', { status: 503 }))
  })

  it.each(['storybooks', 'knowledge'])('reloads %s and ignores a stale list response', async (section) => {
    const knowledge = section === 'knowledge'
    const list = knowledge ? adminApi.listAdminKnowledge : adminApi.listAdminStoryVersions
    const pending = deferred()
    list.mockReturnValueOnce(pending.promise).mockResolvedValueOnce({ items: [{ title: '当前结果' }] })
    window.history.replaceState({}, '', '/#admin/' + section)
    const firstLoad = actions.syncRoute()
    expect(await actions.signOut()).toBe(false)
    expect(auth.currentIdentity).toHaveBeenCalledTimes(2)
    expect(list).toHaveBeenCalledTimes(2)
    pending.resolve({ items: [{ title: '旧请求结果' }] })
    await firstLoad
    expect(state.admin[knowledge ? 'loading' : 'storiesLoading']).toBe(false)
    expect(state.admin[knowledge ? 'knowledgeItems' : 'storyVersions']).toEqual([{ title: '当前结果' }])
    expect(await actions.loadAdminContent(section)).toBe(true)
  })

  it('reads the committed publication after the in-flight write settles without repeating it', async () => {
    await actions.syncRoute()
    adminApi.listAdminStoryVersions.mockClear()
    const pending = deferred()
    adminApi.updateAdminStoryStatus.mockReturnValueOnce(pending.promise)
    const publishing = actions.adminSetStoryStatus(302, 'PUBLISHED')
    const recovery = actions.signOut()
    await vi.advanceTimersByTimeAsync(0)
    expect(adminApi.listAdminStoryVersions).not.toHaveBeenCalled()
    const published = { storybookId: 201, storyVersionId: 302, status: 'PUBLISHED' }
    adminApi.listAdminStoryVersions.mockResolvedValueOnce({ items: [published] })
    pending.resolve(published)
    await publishing
    expect(await recovery).toBe(false)
    expect(state.admin.busy).toBe(false)
    expect(state.admin.storyVersions).toEqual([published])
    expect(adminApi.updateAdminStoryStatus).toHaveBeenCalledTimes(1)
    expect(adminApi.listAdminStoryVersions).toHaveBeenCalledTimes(1)
  })

  it('keeps old lists and writes inaccessible while identity is unavailable, then recovers on retry', async () => {
    adminApi.listAdminStoryVersions.mockResolvedValueOnce({ items: [{ title: '退出前的数据' }] })
    await actions.syncRoute()
    auth.currentIdentity.mockRejectedValueOnce(new ApiError('无法确认会话', { status: 503 }))
    expect(await actions.signOut()).toBe(false)
    expect(state.auth.status).toBe('unavailable')
    expect(state.admin.storyVersions).toEqual([])
    expect(await actions.adminSetStoryStatus(302, 'PUBLISHED')).toBe(false)
    expect(adminApi.updateAdminStoryStatus).not.toHaveBeenCalled()
    expect(state.toast.message).toContain('重新确认身份')
    adminApi.listAdminStoryVersions.mockResolvedValueOnce({ items: [{ title: '已核实的列表' }] })
    await actions.refreshIdentity()
    expect(state.auth.role).toBe('ADMIN')
    expect(state.admin.storyVersions).toEqual([{ title: '已核实的列表' }])
  })

  it('keeps the workspace closed when the identity check confirms the session has ended', async () => {
    await actions.syncRoute()
    adminApi.listAdminStoryVersions.mockClear()
    auth.currentIdentity.mockRejectedValueOnce(new ApiError('登录已失效', { status: 401 }))
    expect(await actions.signOut()).toBe(true)
    expect(state.auth.authenticated).toBe(false)
    expect(state.admin.storyVersions).toEqual([])
    expect(adminApi.listAdminStoryVersions).not.toHaveBeenCalled()
  })
})

describe('account isolation and logout', () => {
  it('does not carry a guardian’s preview selection across logout or the next login', async () => {
    books.listStorybooks.mockResolvedValue([])
    await actions.signIn('parent-a', 'password123')
    expect(state.booksSource).toBe('preview')
    const selectedId = state.books[1].id
    actions.selectBook(selectedId)
    expect(state.selectedBookId).toBe(selectedId)

    expect(await actions.signOut()).toBe(true)
    expect(state.booksSource).toBe('preview')
    expect(state.selectedBookId).toBe('')

    auth.login.mockResolvedValueOnce({ data: { accountId: 2 } })
    auth.currentIdentity.mockResolvedValueOnce({ role: 'GUARDIAN', accountId: 2 })
    await actions.signIn('parent-b', 'password123')
    expect(state.auth.accountId).toBe(2)
    expect(state.booksSource).toBe('preview')
    expect(state.selectedBookId).toBe('')
  })

  it.each([
    ['same guardian with the book still visible', true, false, true],
    ['same guardian with the book removed', false, false, false],
    ['a different guardian who can also see the book', true, true, false],
  ])('restores manual book selection after refresh only when allowed (%s)', async (_, bookVisible, switchAccount, restoresSelection) => {
    const secondBook = { ...version, storyVersionId: 99, title: '第二本测试绘本' }
    books.listStorybooks.mockResolvedValue([version, secondBook])
    await actions.signIn('parent-a', 'password123')
    const selectedId = state.books[1].id
    actions.selectBook(selectedId)
    expect(state.selectedBookId).toBe(selectedId)

    books.listStorybooks.mockResolvedValue(bookVisible ? [version, secondBook] : [version])
    window.history.replaceState({}, '', '/#home')
    vi.resetModules()
    const { zhiya: refreshed, zhiyaActions: refreshedActions } = await import('./useZhiya')
    if (switchAccount) {
      auth.login.mockResolvedValueOnce({ data: { accountId: 2 } })
      auth.currentIdentity.mockResolvedValueOnce({ role: 'GUARDIAN', accountId: 2 })
      await refreshedActions.signIn('parent-b', 'password123')
    } else {
      await refreshedActions.bootstrap()
    }

    expect(refreshed.auth.authenticated).toBe(true)
    expect(refreshed.booksSource).toBe('api')
    expect(refreshed.selectedBookId).toBe(restoresSelection ? selectedId : '')
  })

  it.each([
    ['with a manual selection', true],
    ['after loading more without a manual selection', false],
  ])('refreshes a paginated bookshelf %s', async (_, manuallySelect) => {
    const firstPage = Array.from({ length: 30 }, (_, index) => ({ ...version, storyVersionId: index + 1 }))
    const laterBook = { ...version, storyVersionId: 31 }
    books.listStorybooks.mockImplementation(async ({ page }) => page === 1 ? firstPage : [laterBook])
    await actions.signIn('parent-a', 'password123')
    await actions.loadMoreBooks()
    const laterBookId = state.books.at(-1).id
    expect(state.books.at(-1).storyVersionId).toBe(laterBook.storyVersionId)
    expect(state.selectedBookId).toBe(laterBookId)
    if (manuallySelect) actions.selectBook(laterBookId)

    books.listStorybooks.mockClear()
    window.history.replaceState({}, '', '/#home')
    vi.resetModules()
    const { zhiya: refreshed, zhiyaActions: refreshedActions } = await import('./useZhiya')
    await refreshedActions.bootstrap()

    expect(books.listStorybooks.mock.calls.map(([request]) => request.page)).toEqual(manuallySelect ? [1, 2] : [1])
    expect(refreshed.selectedBookId).toBe(manuallySelect ? laterBookId : '')
  })

  it.each([
    ['the first page fails', 1, null, [1, 1, 2, 3]],
    ['the second page fails', 2, null, [1, 2, 2, 3]],
    ['the guardian changes selection after the second page fails', 2, 'version-5', [1, 2, 2]],
  ])('retries a paginated bookshelf restore when %s', async (_, failedPage, manualId, expectedPages) => {
    const savedId = 'version-63'
    const selectionKey = 'zhiya.selectedBookId'
    localStorage.setItem('zhiya.account', 'parent-a')
    localStorage.setItem(selectionKey, savedId)
    window.history.replaceState({}, '', '/#home')
    const catalog = Array.from({ length: 63 }, (_, index) => ({ ...version, storyVersionId: index + 1 }))
    let failed = false
    books.listStorybooks.mockClear()
    books.listStorybooks.mockImplementation(async ({ page, pageSize }) => {
      if (page === failedPage && !failed) {
        failed = true
        throw new Error('offline')
      }
      return catalog.slice((page - 1) * pageSize, page * pageSize)
    })
    vi.resetModules()
    const { zhiya: refreshed, zhiyaActions: refreshedActions } = await import('./useZhiya')
    await refreshedActions.bootstrap()

    expect(refreshed.booksError).toContain('offline')
    expect(refreshed.nextBooksPage).toBe(failedPage)
    expect(localStorage.getItem(selectionKey)).toBe(savedId)
    if (manualId) refreshedActions.selectBook(manualId)
    await refreshedActions.loadMoreBooks()

    expect(books.listStorybooks.mock.calls.map(([request]) => request.page)).toEqual(expectedPages)
    expect(refreshed.booksSource).toBe('api')
    expect(refreshed.booksError).toBe('')
    expect(refreshed.selectedBookId).toBe(manualId || savedId)
    expect(localStorage.getItem(selectionKey)).toBe(manualId || savedId)
  })

  it('keeps the bookshelf retry available after a direct library refresh', async () => {
    localStorage.setItem('zhiya.account', 'parent-a')
    localStorage.setItem('zhiya.selectedBookId', 'version-63')
    window.history.replaceState({}, '', '/#library')
    books.listStorybooks.mockClear()
    books.listStorybooks.mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValue([{ ...version, storyVersionId: 63 }])
    vi.resetModules()
    const { zhiya: refreshed, zhiyaActions: refreshedActions } = await import('./useZhiya')
    await refreshedActions.bootstrap()
    await refreshedActions.syncRoute()

    expect(books.listStorybooks.mock.calls.map(([request]) => request.page)).toEqual([1])
    expect(refreshed.booksError).toContain('offline')
    expect(refreshed.nextBooksPage).toBe(1)

    await refreshedActions.loadMoreBooks()

    expect(books.listStorybooks.mock.calls.map(([request]) => request.page)).toEqual([1, 1])
    expect(refreshed.booksError).toBe('')
    expect(refreshed.selectedBookId).toBe('version-63')
  })

  it('restores the saved bookshelf selection when navigating from home during the first request', async () => {
    localStorage.setItem('zhiya.account', 'parent-a')
    localStorage.setItem('zhiya.selectedBookId', 'version-63')
    window.history.replaceState({}, '', '/#home')
    const catalog = Array.from({ length: 63 }, (_, index) => ({ ...version, storyVersionId: index + 1 }))
    const firstPage = deferred()
    books.listStorybooks.mockClear()
    books.listStorybooks.mockImplementation(async ({ page, pageSize }) => {
      if (page === 1) await firstPage.promise
      return catalog.slice((page - 1) * pageSize, page * pageSize)
    })
    vi.resetModules()
    const { zhiya: refreshed, zhiyaActions: refreshedActions } = await import('./useZhiya')
    const bootstrapping = refreshedActions.bootstrap()
    await vi.waitFor(() => expect(books.listStorybooks).toHaveBeenCalledTimes(1))

    refreshedActions.navigate('library')
    const routing = refreshedActions.syncRoute()
    expect(refreshed.route.name).toBe('library')
    firstPage.resolve()
    await Promise.all([bootstrapping, routing])

    expect(books.listStorybooks.mock.calls.map(([request]) => request.page)).toEqual([1, 2, 3])
    expect(refreshed.selectedBookId).toBe('version-63')
  })

  it('loads the reader on cold start while the saved bookshelf selection is still restoring', async () => {
    localStorage.setItem('zhiya.account', 'parent-a')
    localStorage.setItem('zhiya.selectedBookId', 'version-63')
    const catalog = Array.from({ length: 63 }, (_, index) => ({ ...version, storyVersionId: index + 1 }))
    const secondPage = deferred()
    books.listStorybooks.mockClear()
    books.listStorybooks.mockImplementation(async ({ page, pageSize }) => {
      if (page === 2) await secondPage.promise
      return catalog.slice((page - 1) * pageSize, page * pageSize)
    })
    reading.getReadingSession.mockClear()
    books.getStoryVersion.mockClear()
    vi.resetModules()
    const { zhiya: refreshed, zhiyaActions: refreshedActions } = await import('./useZhiya')
    const bootstrapping = refreshedActions.bootstrap()
    await vi.waitFor(() => {
      expect(books.listStorybooks.mock.calls.map(([request]) => request.page)).toEqual([1, 2])
      expect(refreshed.currentSession?.readingSessionId).toBe(55)
      expect(refreshed.currentBook?.storyVersionId).toBe(88)
    })
    expect(reading.getReadingSession).toHaveBeenCalledTimes(1)
    expect(books.getStoryVersion).toHaveBeenCalledWith(88)

    secondPage.resolve()
    await bootstrapping

    expect(reading.getReadingSession).toHaveBeenCalledTimes(1)
    expect(books.listStorybooks.mock.calls.map(([request]) => request.page)).toEqual([1, 2, 3])
    expect(refreshed.selectedBookId).toBe('version-63')
  })

  it.each(['account', 'credential'])('invalidates pending guardian reads on a change of %s', async (change) => {
    await actions.signIn('parent-a', 'password123')
    await actions.loadSessions()
    const oldBooks = deferred(), oldHistory = deferred(), oldReader = deferred()
    books.listStorybooks.mockReturnValueOnce(oldBooks.promise)
    reading.listReadingSessions.mockReturnValueOnce(oldHistory.promise)
    reading.getReadingSession.mockReturnValueOnce(oldReader.promise)
    window.history.replaceState({}, '', '/#reader/55')
    const staleReads = [actions.loadBooks(), actions.loadSessions(), actions.syncRoute()]
    if (change === 'account') localStorage.setItem('zhiya.account', 'parent-b')
    else document.cookie = 'zhiya_csrf=fixture-new-session; path=/'
    try {
      window.history.replaceState({}, '', '/#account/growth')
      await actions.syncRoute()
      const newProfiles = deferred()
      profiles.listProfiles.mockReturnValueOnce(newProfiles.promise)
      const refreshing = actions.refreshIdentity()
      expect(state.auth.authenticated).toBe(false)
      expect(state.profiles).toEqual([])
      expect(state.sessions).toEqual([])
      expect(state.currentBook).toBeNull()
      expect(state.currentReport).toBeNull()
      expect(state.booksSource).toBe('preview')
      const newBook = { ...version, storyVersionId: 99, title: '新书' }
      books.listStorybooks.mockResolvedValueOnce([newBook])
      reading.listReadingSessions.mockResolvedValueOnce([session({ readingSessionId: 66, storyVersionId: 99 })])
      newProfiles.resolve([{ childProfileId: 2, nickname: '小禾' }])
      await refreshing
      oldBooks.resolve([version])
      oldHistory.resolve([session()])
      oldReader.resolve({ ...detail(), report: { title: '先前的小结' } })
      await Promise.all(staleReads)
      expect(state.auth.authenticated).toBe(true)
      expect(state.profiles.map(p => p.childProfileId)).toEqual([2])
      expect(state.books.map(b => b.storyVersionId)).toEqual([99])
      expect(state.sessions.map(s => s.readingSessionId)).toEqual([66])
      expect(state.currentSession).toBeNull()
      expect(state.currentReport).toBeNull()
    } finally {
      document.cookie = 'zhiya_csrf=; Max-Age=0; path=/'
    }
  })

  it('keeps a late success from restoring details after the latest request received a 404', async () => {
    window.history.replaceState({}, '', '/#detail/88')
    const first = deferred()
    books.getStoryVersion.mockReturnValueOnce(first.promise)
    const stale = actions.loadDetail(88)
    books.getStoryVersion.mockRejectedValueOnce(new ApiError('资源不可用', { status: 404 }))
    await actions.loadDetail(88)
    first.resolve(version)
    await stale
    expect(state.currentBook).toBeNull()
  })

  it('retains an honest authentication state and explicit message when logout fails', async () => {
    await actions.signIn('parent-a', 'password123')
    auth.logout.mockRejectedValueOnce(new Error('offline'))
    expect(await actions.signOut()).toBe(false)
    expect(state.auth.authenticated).toBe(true)
    expect(state.auth.status).toBe('authenticated')
    expect(state.toast.message).toContain('退出尚未确认')
    expect(localStorage.getItem('zhiya.account')).toBe('parent-a')
  })

  it('clears private account state and stored preferences only on confirmed logout', async () => {
    await actions.signIn('parent-a', 'password123')
    localStorage.setItem('zhiya.plan', '{"minutes":20}')
    localStorage.setItem('zhiya.guardianNickname', '家长A')
    localStorage.setItem('other-app-setting', 'keep')
    expect(await actions.signOut()).toBe(true)
    expect(state.auth.authenticated).toBe(false)
    expect(state.profiles).toEqual([])
    expect(state.currentSession).toBeNull()
    expect(state.currentBook).toBeNull()
    expect(state.currentEvents).toEqual([])
    expect(state.currentReport).toBeNull()
    expect(state.books.every((book) => book.source === 'preview')).toBe(true)
    expect(localStorage.getItem('zhiya.plan')).toBeNull()
    expect(localStorage.getItem('zhiya.guardianNickname')).toBeNull()
    expect(localStorage.getItem('zhiya.activeProfileId')).toBeNull()
    expect(localStorage.getItem('other-app-setting')).toBe('keep')
  })

  it('ignores private books, profiles and sessions arriving after logout', async () => {
    await actions.signIn('parent-a', 'password123')
    const booksRequest = deferred(), profileRequest = deferred(), sessionRequest = deferred()
    books.listStorybooks.mockReturnValueOnce(booksRequest.promise)
    profiles.listProfiles.mockReturnValueOnce(profileRequest.promise)
    reading.listReadingSessions.mockReturnValueOnce(sessionRequest.promise)
    const pending = [actions.loadBooks(), actions.loadProfiles(), actions.loadSessions()]
    await actions.signOut()
    booksRequest.resolve([version])
    profileRequest.resolve([{ childProfileId: 99, nickname: '上个账号' }])
    sessionRequest.resolve([session()])
    await Promise.all(pending)
    expect(state.profiles).toEqual([])
    expect(state.sessions).toEqual([])
    expect(state.booksSource).toBe('preview')
  })

  it.each(['success', 'failure'])('keeps a newer child selection after a late profile lookup %s', async (outcome) => {
    await actions.signIn('parent-a', 'password123')
    const lookup = deferred()
    profiles.listProfiles.mockReturnValueOnce(lookup.promise)
    reading.createReadingSession.mockRejectedValueOnce(new ApiError('资源不可用', { status: 404 }))
    const start = actions.startReading(state.books[0])
    await Promise.resolve()
    expect(profiles.listProfiles).toHaveBeenCalledTimes(2)

    const replacement = { childProfileId: 9, nickname: '小禾' }
    profiles.listProfiles.mockResolvedValueOnce([replacement])
    await actions.loadProfiles()
    actions.selectProfile(replacement.childProfileId)
    if (outcome === 'success') lookup.resolve([])
    else lookup.reject(new ApiError('暂不可用', { status: 503 }))
    await start
    expect(state.profiles).toEqual([replacement])
    expect(state.activeProfileId).toBe(9)
    expect(state.toast.message).toContain('小禾')
    expect(books.listStorybooks).toHaveBeenCalledTimes(1)
  })

  it('ignores an old session response after changing routes', async () => {
    const pending = deferred()
    reading.getReadingSession.mockReturnValueOnce(pending.promise)
    const loading = actions.loadReaderRoute()
    window.history.replaceState({}, '', '/#home')
    await actions.syncRoute()
    pending.resolve(detail({ readingSessionId: 99 }))
    await loading
    expect(state.currentSession).toBeNull()
  })

  it('deletes a child profile and refreshes the active profile and reading history', async () => {
    await actions.signIn('parent-a', 'password123')
    profiles.listProfiles.mockResolvedValueOnce([])
    reading.listReadingSessions.mockResolvedValueOnce([])
    expect(await actions.removeProfile(1)).toBe(true)
    expect(profiles.deleteProfile).toHaveBeenCalledWith(1)
    expect(state.profiles).toEqual([])
    expect(state.activeProfileId).toBe(0)
    expect(localStorage.getItem('zhiya.activeProfileId')).toBeNull()
  })

  it('removes a deleted reading session from account state', async () => {
    await actions.signIn('parent-a', 'password123')
    await actions.loadSessions()
    expect(await actions.removeReadingSession(55)).toBe(true)
    expect(reading.deleteReadingSession).toHaveBeenCalledWith(55)
    expect(state.sessions).toEqual([])
  })

  it('keeps an uncertain deletion pending until every history page can be checked', async () => {
    await actions.signIn('parent-a', 'password123')
    reading.deleteReadingSession.mockRejectedValueOnce(new ApiError('连接中断', { code: 'REQUEST_OUTCOME_UNKNOWN' }))
    const firstPage = Array.from({ length: 50 }, (_, i) => session({ readingSessionId: 200 - i }))
    reading.listReadingSessions.mockResolvedValueOnce(firstPage).mockRejectedValueOnce(new ApiError('暂不可用', { status: 503 }))
    expect(await actions.removeReadingSession(55)).toBe('unconfirmed')
    expect(await actions.checkDeletion('session', 55)).toBe('unconfirmed')
    expect(await actions.removeReadingSession(55)).toBe('unconfirmed')
    reading.listReadingSessions.mockResolvedValueOnce(firstPage).mockResolvedValueOnce([session()])
    expect(await actions.checkDeletion('session', 55)).toBe('present')
    expect(reading.deleteReadingSession).toHaveBeenCalledOnce()
    expect(reading.listReadingSessions.mock.calls).toEqual([[1, 50], [2, 50], [1, 50], [2, 50]])
  })

  it('ignores a pre-deletion history read arriving after the fresh list', async () => {
    await actions.signIn('parent-a', 'password123')
    const deleted = session({ readingSessionId: 11, storyVersionId: 1, visitedPageNumbers: [1, 2] })
    const remaining = session({ readingSessionId: 12, storyVersionId: 2 })
    reading.listReadingSessions.mockResolvedValueOnce([deleted, remaining])
    await actions.loadSessions()
    const oldRead = deferred()
    reading.listReadingSessions.mockReturnValueOnce(oldRead.promise).mockResolvedValueOnce([remaining])
    const oldHistory = actions.loadSessions()
    expect(await actions.removeReadingSession(11)).toBe(true)
    oldRead.resolve([deleted, remaining])
    await oldHistory
    const { totals } = await import('./useZhiya')
    expect(state.sessions.map(item => item.readingSessionId)).toEqual([12])
    expect(totals.value).toEqual({ sessions: 1, read: 1, pages: 1 })
    expect(state.sessionsLoading).toBe(false)
  })

  it('keeps a deleted profile removed when refreshing its list fails', async () => {
    await actions.signIn('parent-a', 'password123')
    profiles.listProfiles.mockRejectedValueOnce(new ApiError('服务暂不可用', {
      status: 503,
      code: 'DEPENDENCY_UNAVAILABLE',
    }))
    reading.listReadingSessions.mockResolvedValueOnce([])
    expect(await actions.removeProfile(1)).toBe(true)
    expect(state.profiles).toEqual([])
    expect(state.activeProfileId).toBe(0)
    expect(state.profilesError).toContain('服务暂不可用')
    expect(state.toast.message).toContain('儿童档案已删除')
  })
})

describe('child-mode handoff', () => {
  async function openNewParentSession() {
    await actions.bootstrap()
    books.listStorybooks.mockClear()
    reading.getReadingSession.mockResolvedValueOnce(detail({ status: 'CREATED', revision: 0, visitedPageNumbers: [] }))
    await actions.loadReaderRoute()
  }

  function confirmChildCredential() {
    profiles.listProfiles.mockRejectedValueOnce(new ApiError('仅限家长', { status: 403, code: 'FORBIDDEN' }))
  }

  it('keeps a guardian on the reader if another device already issued the grant', async () => {
    await openNewParentSession()
    expect(await actions.enterChildMode(55, [88], 300)).toBe(true)
    expect(reading.updateReadingMode).toHaveBeenCalledOnce()
    expect(state.auth.status).toBe('authenticated')
    expect(state.currentSession.mode).toBe('CHILD_SOLO')
    expect(state.currentSession.expiresAt).toBe('2026-11-18T08:00:00Z')
    expect(state.childMode).toBeNull()
    expect(localStorage.getItem('zhiya.childHandoffSessionId')).toBeNull()
    expect(sessionStorage.getItem('zhiya.childHandoffSessionId')).toBeNull()
    expect(location.hash).toBe('#reader/55')
    expect(await actions.enterChildMode(55, [88], 300)).toBe(false)
    expect(reading.updateReadingMode).toHaveBeenCalledOnce()
  })

  it('restricts the device while its credential cannot be confirmed after a successful mode change', async () => {
    await openNewParentSession()
    profiles.listProfiles.mockRejectedValueOnce(new ApiError('暂不可用', { status: 503 }))
    expect(await actions.enterChildMode(55, [88], 600)).toBe(true)
    expect(state.auth.authenticated).toBe(false)
    expect(state.profiles).toEqual([])
    expect(location.hash).toBe('#child/55')
    await actions.refreshIdentity()
    expect(state.auth.status).toBe('authenticated')
    expect(location.hash).toBe('#account/overview')
    expect(reading.updateReadingMode).toHaveBeenCalledOnce()
  })

  it('enters child mode only from CREATED and restores the parent after password unlock', async () => {
    await openNewParentSession()
    expect(await actions.startCurrentReading()).toBe(true)
    expect(await actions.enterChildMode(55, [88], 600)).toBe(false)

    reading.getReadingSession.mockResolvedValueOnce(detail({ status: 'CREATED', revision: 0, visitedPageNumbers: [] }))
    await actions.loadReaderRoute()
    confirmChildCredential()
    expect(await actions.enterChildMode(55, [88], 600)).toBe(true)
    expect(reading.updateReadingMode).toHaveBeenCalledWith(55, {
      mode: 'CHILD_SOLO',
      allowedStoryVersionIds: [88],
      timeLimitSeconds: 600,
    })
    expect(state.auth.status).toBe('child')
    expect(state.auth.authenticated).toBe(false)
    expect(state.childMode.sessionId).toBe(55)

    expect(await actions.unlockChildMode('parent-a', 'password123')).toBe(true)
    expect(auth.parentUnlock).toHaveBeenCalledWith('parent-a', 'password123')
    expect(state.childMode).toBeNull()
    expect(state.auth.status).toBe('authenticated')
    expect(state.auth.authenticated).toBe(true)
  })

  it('keeps the child credential on the handoff when browser navigation changes the hash', async () => {
    await openNewParentSession()
    confirmChildCredential()
    expect(await actions.enterChildMode(55, [88], 600)).toBe(true)
    window.history.replaceState({}, '', '/#library')
    await actions.syncRoute()
    expect(state.auth.status).toBe('child')
    expect(state.route).toEqual({ name: 'child', sessionId: 55 })
    expect(location.hash).toBe('#child/55')
    expect(sessionStorage.getItem('zhiya.childHandoffSessionId')).toBe('55')
  })

  it('routes a guardian credential away from an old child handoff link', async () => {
    await actions.signIn('parent-a', 'password123')
    reading.getReadingSession.mockClear()
    window.history.replaceState({}, '', '/#child/55')
    await actions.syncRoute()
    expect(state.auth.status).toBe('authenticated')
    expect(state.route).toEqual({ name: 'account', section: 'overview' })
    expect(location.hash).toBe('#account/overview')
    expect(reading.getReadingSession).not.toHaveBeenCalled()
  })

  it('leaves the handoff as soon as unlock succeeds, even if profile refresh fails', async () => {
    await openNewParentSession()
    confirmChildCredential()
    expect(await actions.enterChildMode(55, [88], 600)).toBe(true)
    const profileRefresh = deferred()
    profiles.listProfiles.mockReturnValueOnce(profileRefresh.promise)
    const unlocking = actions.unlockChildMode('parent-a', 'password123')
    await Promise.resolve()
    await Promise.resolve()
    await Promise.resolve()
    expect(state.auth.status).toBe('authenticated')
    expect(state.childMode).toBeNull()
    expect(state.route).toEqual({ name: 'account', section: 'overview' })
    profileRefresh.reject(new ApiError('服务暂不可用', {
      status: 503,
      code: 'DEPENDENCY_UNAVAILABLE',
    }))
    expect(await unlocking).toBe(true)
    expect(state.profilesError).toContain('服务暂不可用')
    expect(auth.parentUnlock).toHaveBeenCalledTimes(1)

    profiles.listProfiles.mockResolvedValueOnce([{ childProfileId: 1, nickname: '小芽' }])
    expect(await actions.reloadProfiles()).toBe(true)
    expect(state.profiles).toEqual([{ childProfileId: 1, nickname: '小芽' }])
    expect(reading.listReadingSessions).toHaveBeenCalledTimes(1)
    expect(books.listStorybooks).toHaveBeenCalledTimes(1)
    expect(auth.parentUnlock).toHaveBeenCalledTimes(1)
  })

  it('keeps a rejected password on the handoff and routes a lost credential to login', async () => {
    window.history.replaceState({}, '', '/#child/55')
    await actions.syncRoute()
    profiles.listProfiles.mockRejectedValueOnce(new ApiError('请先登录', { status: 401, code: 'AUTH_REQUIRED' }))
    await actions.bootstrap()
    expect(state.auth.authenticated).toBe(false)
    auth.parentUnlock.mockRejectedValueOnce(new ApiError('账号或密码错误', { status: 401, code: 'INVALID_CREDENTIALS' }))
    await expect(actions.unlockChildMode('parent-a', 'wrong-password')).rejects.toMatchObject({ code: 'INVALID_CREDENTIALS' })
    expect(location.hash).toBe('#child/55')
    auth.parentUnlock.mockRejectedValueOnce(new ApiError('请先登录', { status: 401, code: 'AUTH_REQUIRED' }))
    expect(await actions.unlockChildMode('parent-a', 'password123')).toBe(false)
    expect(location.hash).toBe('#auth')
  })

  it('does not claim an exact authorization count that cannot be restored', async () => {
    await openNewParentSession()
    confirmChildCredential()
    reading.updateReadingMode.mockResolvedValueOnce({
      mode: 'CHILD_SOLO',
      childGrantId: 4,
      allowedStoryVersionIds: [88, 99],
      expiresAt: '2026-11-18T08:00:00Z',
    })
    expect(await actions.enterChildMode(55, [88, 99], 600)).toBe(true)
    expect(state.childMode.allowedStoryVersionCount).toBe(2)
    expect(await actions.restoreChildMode(55)).toBe(true)
    expect(state.childMode.allowedStoryVersionCount).toBeNull()
  })
})

describe('growth statistics', () => {
  it.each([
    { path: [1, 2, 3, 4], count: 2 },
    { path: [1], count: 0 },
    { path: [1, 4], count: 1 },
  ])('counts $count visits outside the carried prefix in path $path', async ({ path, count }) => {
    await actions.signIn('parent-a', 'password123')
    const { totals } = await import('./useZhiya')
    const continued = session({
      readingSessionId: 56, startPageNumber: 3, carriedPrefixPageCount: 2, visitedPageNumbers: path,
    })
    reading.listReadingSessions.mockResolvedValueOnce([
      session({ readingSessionId: 55, visitedPageNumbers: [1, 2] }),
      { ...continued, sourceReadingSessionId: 55 },
    ]).mockResolvedValueOnce([continued])

    await actions.loadSessions()
    expect(totals.value.pages).toBe(2 + count)
    await actions.loadSessions()
    expect(totals.value.pages).toBe(count)
    expect(state.sessions[0].visitedPageNumbers).toEqual(path)
  })

  it('counts actual visits and only in-progress or finished sessions', async () => {
    await actions.signIn('parent-a', 'password123')
    const { totals } = await import('./useZhiya')
    const created = session({ readingSessionId: 1, status: 'CREATED', visitedPageNumbers: [] })
    reading.listReadingSessions.mockResolvedValueOnce([created])
    await actions.loadSessions()
    expect(totals.value).toEqual({ sessions: 0, read: 0, pages: 0 })
    reading.listReadingSessions.mockResolvedValueOnce([
      created, session({ readingSessionId: 2, visitedPageNumbers: [1, 3] }),
      session({ readingSessionId: 3, status: 'FINISHED', visitedPageNumbers: [1, 3, 7] }),
      session({ readingSessionId: 4, storyVersionId: 99, status: 'INTERRUPTED', visitedPageNumbers: [1] }),
    ])
    await actions.loadSessions()
    expect(totals.value).toEqual({ sessions: 2, read: 2, pages: 6 })
  })

  it('includes later pages and counts a session only once when paginated history overlaps', async () => {
    await actions.signIn('parent-a', 'password123')
    const { totals } = await import('./useZhiya')
    const first = Array.from({ length: 50 }, (_, index) => session({ readingSessionId: 60 - index }))
    reading.listReadingSessions.mockResolvedValueOnce(first).mockResolvedValueOnce([
      first.at(-1), session({ readingSessionId: 10, storyVersionId: 99 }),
    ])
    await actions.loadSessions()
    expect(reading.listReadingSessions.mock.calls).toEqual([[1, 50], [2, 50]])
    expect(totals.value).toEqual({ sessions: 51, read: 2, pages: 51 })
  })
})
