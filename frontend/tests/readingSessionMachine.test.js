import test from 'node:test'
import assert from 'node:assert/strict'
import { ApiError } from '../mobile/api/client.js'
import { ReadingSessionMachine, RecoverableSessionError } from '../mobile/services/readingSessionMachine.js'

function session(overrides = {}) {
  return {
    readingSessionId: 401,
    storyVersionId: 301,
    currentPage: 1,
    visitedPageNumbers: [],
    mode: 'PARENT_CHILD',
    status: 'CREATED',
    revision: 0,
    expiresAt: null,
    ...overrides,
  }
}

function fakeApi(overrides = {}) {
  const calls = []
  return {
    calls,
    async create(payload) {
      calls.push(['create', payload])
      return { data: session(), requestId: 'req-create' }
    },
    async get(sessionId) {
      calls.push(['get', sessionId])
      return {
        data: { session: session({ status: 'IN_PROGRESS', revision: 4, currentPage: 3, visitedPageNumbers: [1, 2, 3] }), events: [], report: null },
        requestId: 'req-get',
      }
    },
    async addEvent(sessionId, payload) {
      calls.push(['addEvent', sessionId, payload])
      return { data: { interactionEventId: 801, revision: payload.expectedRevision + 1 }, requestId: 'req-event' }
    },
    async submitGuidance(sessionId, payload) {
      calls.push(['guidance', sessionId, payload])
      return {
        data: {
          interactionEventId: 901,
          revision: payload.expectedRevision + 1,
          outputText: '先看看画面里谁需要帮助。',
          strategyType: 'CONCRETE_RECALL',
          status: 'FALLBACK',
          sources: [],
        },
        requestId: 'req-agent',
      }
    },
    async finish(sessionId, payload) {
      calls.push(['finish', sessionId, payload])
      return {
        data: {
          session: session({ status: 'FINISHED', revision: payload.expectedRevision + 1, visitedPageNumbers: [1] }),
          report: { readingSessionId: 401, summary: '实际摘要', activities: [], status: 'PENDING' },
        },
        requestId: 'req-finish',
      }
    },
    ...overrides,
  }
}

test('STARTED and PAGE_CHANGED update revision and current page', async () => {
  const api = fakeApi()
  const machine = new ReadingSessionMachine({ api, keyFactory: (prefix) => `${prefix}-fixed` })
  await machine.create({ childProfileId: 101, storyVersionId: 301 })
  await machine.start()
  assert.equal(machine.session.status, 'IN_PROGRESS')
  assert.equal(machine.session.revision, 1)
  assert.equal(machine.session.currentPage, 1)
  await machine.changePage(2)
  assert.equal(machine.session.currentPage, 2)
  assert.equal(machine.session.revision, 2)
  assert.deepEqual(machine.session.visitedPageNumbers, [1, 2])
  assert.equal(api.calls[2][2].expectedRevision, 1)
  assert.equal(api.calls[2][2].clientEventId, 'page-fixed')
})

test('a page turn supersedes a slow INITIAL and discards its late result', async () => {
  let reply
  const api = fakeApi({ submitGuidance: () => new Promise(resolve => { reply = resolve }) })
  const machine = new ReadingSessionMachine({ api })
  await machine.create({ childProfileId: 101, storyVersionId: 301 })
  await machine.start()
  const pending = machine.guidance('INITIAL')
  await machine.changePage(2)
  reply({ data: { interactionEventId: 901, revision: 2, outputText: '旧页问题' } })
  await pending
  assert.equal(machine.session.currentPage, 2)
  assert.equal(machine.session.revision, 2)
  assert.equal(machine.events.some(event => event.interactionEventId === 901), false)
})

test('SESSION_CONFLICT reloads the latest session before reporting the conflict', async () => {
  const api = fakeApi({
    async addEvent() {
      throw new ApiError('会话状态已变化', { status: 409, code: 'SESSION_CONFLICT' })
    },
  })
  const machine = new ReadingSessionMachine({ api, keyFactory: (prefix) => `${prefix}-fixed` })
  await machine.create({ childProfileId: 101, storyVersionId: 301 })
  await assert.rejects(() => machine.start(), (error) => {
    assert.ok(error instanceof RecoverableSessionError)
    assert.equal(error.code, 'SESSION_CONFLICT')
    return true
  })
  assert.equal(machine.session.revision, 4)
  assert.equal(machine.session.currentPage, 3)
  assert.equal(api.calls.at(-1)[0], 'get')
})

test('REQUEST_IN_PROGRESS retries with the same idempotency key', async () => {
  let attempt = 0
  const api = fakeApi({
    async addEvent(sessionId, payload) {
      attempt += 1
      if (attempt === 1) {
        throw new ApiError('请求正在处理', {
          status: 409,
          code: 'REQUEST_IN_PROGRESS',
          retryable: true,
          retryAfter: '0',
        })
      }
      return { data: { interactionEventId: 802, revision: payload.expectedRevision + 1 }, requestId: 'req-retry' }
    },
  })
  const machine = new ReadingSessionMachine({
    api,
    keyFactory: (prefix) => `${prefix}-same`,
    sleep: async () => {},
  })
  await machine.create({ childProfileId: 101, storyVersionId: 301 })
  await machine.start({ clientEventId: 'start-same' })
  assert.equal(attempt, 2)
  assert.equal(machine.session.revision, 1)
})

test('finish sends USER_FINISH with the current revision and stores report', async () => {
  const api = fakeApi()
  const machine = new ReadingSessionMachine({ api, keyFactory: (prefix) => `${prefix}-fixed` })
  await machine.create({ childProfileId: 101, storyVersionId: 301 })
  await machine.start()
  await machine.finish()
  const finishCall = api.calls.find((call) => call[0] === 'finish')
  assert.equal(finishCall[2].reason, 'USER_FINISH')
  assert.equal(finishCall[2].expectedRevision, 1)
  assert.equal(machine.session.status, 'FINISHED')
  assert.equal(machine.report.summary, '实际摘要')
})

test('guidance returns the G10 AgentResult and advances revision', async () => {
  const api = fakeApi()
  const machine = new ReadingSessionMachine({ api, keyFactory: (prefix) => `${prefix}-fixed` })
  await machine.create({ childProfileId: 101, storyVersionId: 301 })
  await machine.start()
  const output = await machine.guidance('NOT_UNDERSTOOD')
  assert.equal(output.result.outputText, '先看看画面里谁需要帮助。')
  assert.equal(output.result.strategyType, 'CONCRETE_RECALL')
  assert.equal(machine.session.revision, 2)
})

test('INITIAL retains its original key after transport loss and a SKIPPED 503', async () => {
  const snapshot = session({ status: 'IN_PROGRESS', revision: 1, visitedPageNumbers: [1] })
  const calls = []
  const question = { interactionEventId: 901, revision: 2, outputText: '你看到了什么？' }
  let rejectInitial
  const initialReply = new Promise((_, reject) => { rejectInitial = reject })
  const api = {
    async submitGuidance(sessionId, payload) {
      calls.push(['guidance', sessionId, payload])
      if (payload.feedback === 'SKIPPED') {
        throw new ApiError('依赖暂不可用', { status: 503, code: 'DEPENDENCY_UNAVAILABLE' })
      }
      if (calls.length === 1) return initialReply
      return { data: question }
    },
    async get(sessionId) {
      calls.push(['get', sessionId])
      return { data: { session: snapshot, events: [], report: null } }
    },
  }
  let nextKey = 0
  const machine = new ReadingSessionMachine({
    api,
    initialSnapshot: { session: snapshot },
    keyFactory: (prefix) => prefix + '-' + ++nextKey,
  })
  const initial = machine.guidance('INITIAL')
  const skip = machine.guidance('SKIPPED')
  const settled = Promise.allSettled([initial, skip])
  await new Promise((resolve) => setImmediate(resolve))
  rejectInitial(new ApiError('写入结果尚未确认', { code: 'REQUEST_OUTCOME_UNKNOWN' }))
  await settled

  const original = { pageNumber: 1, feedback: 'INITIAL', expectedRevision: 1, clientEventId: 'initial-1' }
  assert.deepEqual(calls, [
    ['guidance', 401, original],
    ['guidance', 401, { ...original, feedback: 'SKIPPED', clientEventId: 'skipped-2' }],
    ['get', 401],
  ])
  assert.equal(machine.snapshot().pending, false)
  assert.equal(machine.snapshot().syncRequired, true)
  await assert.rejects(() => machine.guidance('INITIAL'), /请先重新核对/)
  assert.equal(calls.length, 3)

  const recovered = await machine.reconcile()
  assert.deepEqual(calls.slice(3), [['get', 401], ['guidance', 401, original]])
  assert.deepEqual(recovered.result, question)
  assert.equal(machine.snapshot().session.revision, 2)
  assert.equal(machine.snapshot().syncRequired, false)
})

for (const retryable of [true, false]) {
  test('INITIAL recovery handles dependency 503 with retryable=' + retryable, async () => {
    const snapshot = session({ status: 'IN_PROGRESS', revision: 1, visitedPageNumbers: [1] })
    const writes = []
    const question = { interactionEventId: 901, revision: 2, outputText: '你看到了什么？' }
    const failure = new ApiError('依赖暂不可用', {
      status: 503, code: 'DEPENDENCY_UNAVAILABLE', retryable,
      payload: { error: { code: 'DEPENDENCY_UNAVAILABLE', retryable } },
    })
    const api = fakeApi({
      async get() {
        return { data: { session: snapshot, events: [], report: null } }
      },
      async submitGuidance(_sessionId, payload) {
        writes.push(payload)
        if (writes.length === 1) throw new ApiError('写入结果尚未确认', { code: 'REQUEST_OUTCOME_UNKNOWN' })
        if (writes.length <= (retryable ? 3 : 2)) throw failure
        return { data: question }
      },
    })
    let key = 0
    const machine = new ReadingSessionMachine({
      api, initialSnapshot: { session: snapshot }, keyFactory: (prefix) => prefix + '-' + ++key,
    })
    await assert.rejects(() => machine.guidance('INITIAL'), { code: 'REQUEST_OUTCOME_UNKNOWN' })
    const original = { pageNumber: 1, feedback: 'INITIAL', expectedRevision: 1, clientEventId: 'initial-1' }

    for (let attempt = 0; attempt < (retryable ? 2 : 1); attempt += 1) {
      await assert.rejects(() => machine.reconcile(), { code: 'DEPENDENCY_UNAVAILABLE', retryable })
      assert.equal(machine.snapshot().pending, false)
      assert.equal(machine.snapshot().syncRequired, retryable)
      assert.deepEqual(writes.at(-1), original)
      if (retryable) {
        await assert.rejects(() => machine.guidance('INITIAL'), /请先重新核对/)
        assert.equal(writes.length, attempt + 2)
      }
    }

    if (retryable) {
      const recovered = await machine.reconcile()
      assert.deepEqual(recovered.result, question)
      assert.deepEqual(writes, [original, original, original, original])
    } else {
      await machine.guidance('INITIAL')
      assert.deepEqual(writes, [original, original, { ...original, clientEventId: 'initial-2' }])
    }
    assert.equal(machine.snapshot().session.revision, 2)
    assert.equal(machine.snapshot().syncRequired, false)
  })
}
