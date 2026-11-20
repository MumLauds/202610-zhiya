import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, expect, it, vi } from 'vitest'
import { DemoReadingSessionsApi } from '../api/demo'
import { ReadingSessionsApi } from '../api/readingSessions'
import ReaderView from './ReaderView.vue'

const book = {
  title: '共读测试', storyVersionId: 6, cover: '/cover.png',
  pages: [{ page: 1, scene: '第一页', tokens: [], defaultNextPageNumber: null }],
}
const session = {
  readingSessionId: 42, storyVersionId: 6, status: 'IN_PROGRESS',
  currentPage: 1, revision: 1, visitedPageNumbers: [1], mode: 'PARENT_CHILD',
}
const question = {
  status: 'GENERATED', strategyType: 'CONTEXT_QUESTION', interactionEventId: 22,
  outputText: '你看到了什么？', revision: 2,
}
const blocked = {
  ...question, status: 'BLOCKED', strategyType: 'SAFETY_BLOCK',
  outputText: '请家长陪伴孩子，换一个适合本页的话题。',
}
const skipped = { ...question, strategyType: 'SKIP', interactionEventId: 23, outputText: '' }
const questionEvent = { ...question, eventType: 'QUESTION', pageNumber: 1 }
const skipEvent = { ...skipped, eventType: 'FEEDBACK', feedback: 'SKIPPED', pageNumber: 1 }
const detailPath = '/api/v1/reading-sessions/42'
const guidancePath = detailPath + '/guidance'
const skipNotice = '已跳过本页互动。'
const json = (body, status = 200) => new Response(JSON.stringify(body), { status })
const result = (data) => json({ data }, 201)
const detail = (revision, events = []) => json({ data: { session: { ...session, revision }, events, report: null } })
const conflict = () => json({ error: { code: 'SESSION_CONFLICT', message: '阅读进度已变化' } }, 409)

let wrapper

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  vi.unstubAllGlobals()
})

async function openReader(fetch, { restore = false } = {}) {
  vi.stubGlobal('fetch', fetch)
  wrapper = mount(ReaderView, {
    props: {
      book, mode: 'live', storyVersionId: book.storyVersionId,
      voiceApi: { synthesizePage: vi.fn().mockRejectedValue(new Error('暂时无法准备朗读')) },
      sessionApi: new ReadingSessionsApi(),
      initialSessionId: restore ? 42 : null,
      initialSnapshot: restore ? null : { session, events: [], report: null },
    },
  })
  await flushPromises()
}

function button(label) {
  const control = wrapper.findAll('button').find((candidate) => candidate.text() === label)
  expect(control, 'missing button: ' + label).toBeDefined()
  return control
}

async function click(label) {
  const control = button(label)
  expect(control.element.disabled, 'disabled button: ' + label).toBe(false)
  await control.trigger('click')
  await flushPromises()
}

function guidanceWrites(fetch) {
  return fetch.mock.calls
    .filter(([path]) => path === guidancePath)
    .map(([, options]) => JSON.parse(options.body))
}

async function expectAnswerUnavailable() {
  const answer = button('孩子回答')
  expect(answer.element.disabled).toBe(true)
  await answer.trigger('click')
  await flushPromises()
  expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
}

it('shows a safety block from the automatic API-017 request without opening the answer modal', async () => {
  const fetch = vi.fn().mockResolvedValueOnce(result(blocked))
  await openReader(fetch)

  expect(wrapper.text()).toContain(blocked.outputText)
  await expectAnswerUnavailable()
  expect(guidanceWrites(fetch).map(({ feedback }) => feedback)).toEqual(['INITIAL'])
  expect(fetch).toHaveBeenCalledTimes(1)
})

it('keeps a safety-blocked result non-answerable when recovered by same-key replay', async () => {
  const fetch = vi.fn()
    .mockResolvedValueOnce(new Response('<h1>Gateway Timeout</h1>', { status: 504 }))
    .mockResolvedValueOnce(detail(1))
    .mockResolvedValueOnce(detail(1))
    .mockResolvedValueOnce(result(blocked))
  await openReader(fetch)
  await click('重新核对进度')

  expect(wrapper.text()).toContain(blocked.outputText)
  await expectAnswerUnavailable()
  const writes = guidanceWrites(fetch)
  expect(writes).toHaveLength(2)
  expect(writes[0]).toMatchObject({ feedback: 'INITIAL', clientEventId: expect.any(String) })
  expect(writes[1]).toEqual(writes[0])
  expect(fetch.mock.calls.map(([path]) => path)).toEqual([guidancePath, detailPath, detailPath, guidancePath])
})

it('recovers the latest skip from API-013 and only allows a newly generated question to be answered', async () => {
  const fresh = { ...question, outputText: '新的共读问题', interactionEventId: 24, revision: 4 }
  const fetch = vi.fn()
    .mockResolvedValueOnce(detail(3, [questionEvent, skipEvent]))
    .mockResolvedValueOnce(result(fresh))
  await openReader(fetch, { restore: true })

  expect(wrapper.text()).toContain(skipNotice)
  expect(wrapper.text()).not.toContain(question.outputText)
  await expectAnswerUnavailable()
  expect(fetch).toHaveBeenCalledExactlyOnceWith(detailPath, expect.objectContaining({ method: 'GET' }))
  await click('生成本页共读问题')

  expect(wrapper.text()).toContain(fresh.outputText)
  expect(wrapper.text()).not.toContain(question.outputText)
  expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
  await click('孩子回答')
  expect(wrapper.find('[role="dialog"]').exists()).toBe(true)
  expect(guidanceWrites(fetch)).toEqual([expect.objectContaining({ feedback: 'INITIAL', expectedRevision: 3 })])
  expect(fetch).toHaveBeenCalledTimes(2)
})

it('submits a separate skip after the initial question returns a business 503', async () => {
  const fetch = vi.fn()
    .mockResolvedValueOnce(json({ error: { code: 'DEPENDENCY_UNAVAILABLE', message: '暂时无法生成问题' } }, 503))
    .mockResolvedValueOnce(result(skipped))
  await openReader(fetch)
  expect(wrapper.text()).toContain('暂时无法生成问题')
  await click('跳过')

  expect(wrapper.text()).toContain(skipNotice)
  await expectAnswerUnavailable()
  const writes = guidanceWrites(fetch)
  expect(writes.map(({ feedback }) => feedback)).toEqual(['INITIAL', 'SKIPPED'])
  expect(writes[1]).toMatchObject({ expectedRevision: 1, clientEventId: expect.any(String) })
  expect(writes[1].clientEventId).not.toBe(writes[0].clientEventId)
  expect(fetch).toHaveBeenCalledTimes(2)
})

it.each([
  ['success', () => result({ ...question, revision: 3 })],
  ['conflict', conflict],
])('skips while initial generation is pending and ignores its late %s response', async (_name, lateResponse) => {
  let resolveInitial
  const pendingInitial = new Promise((resolve) => { resolveInitial = resolve })
  const fetch = vi.fn()
    .mockReturnValueOnce(pendingInitial)
    .mockResolvedValueOnce(result(skipped))
  await openReader(fetch)
  expect(guidanceWrites(fetch).map(({ feedback }) => feedback)).toEqual(['INITIAL'])

  await click('跳过')
  expect(wrapper.text()).toContain(skipNotice)
  resolveInitial(lateResponse())
  await flushPromises()

  expect(wrapper.text()).toContain(skipNotice)
  expect(wrapper.text()).not.toContain(question.outputText)
  expect(wrapper.get('.revision-tag').text()).toBe('rev 2')
  await expectAnswerUnavailable()
  expect(guidanceWrites(fetch)).toEqual([
    expect.objectContaining({ feedback: 'INITIAL', expectedRevision: 1 }),
    expect.objectContaining({ feedback: 'SKIPPED', expectedRevision: 1 }),
  ])
  expect(fetch).toHaveBeenCalledTimes(2)
})

it('restores the committed initial question when a concurrent skip returns a revision conflict', async () => {
  let resolveInitial
  const pendingInitial = new Promise((resolve) => { resolveInitial = resolve })
  const fetch = vi.fn()
    .mockReturnValueOnce(pendingInitial)
    .mockResolvedValueOnce(conflict())
    .mockResolvedValueOnce(detail(2, [questionEvent]))
    .mockResolvedValueOnce(result({ ...skipped, revision: 3 }))
  await openReader(fetch)
  await click('跳过')
  resolveInitial(result(question))
  await flushPromises()

  expect(wrapper.text()).toContain(question.outputText)
  expect(wrapper.text()).not.toContain(skipNotice)
  expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
  await click('跳过')

  expect(wrapper.text()).toContain(skipNotice)
  expect(guidanceWrites(fetch)).toEqual([
    expect.objectContaining({ feedback: 'INITIAL', expectedRevision: 1 }),
    expect.objectContaining({ feedback: 'SKIPPED', expectedRevision: 1 }),
    expect.objectContaining({ feedback: 'SKIPPED', expectedRevision: 2 }),
  ])
  expect(fetch.mock.calls.map(([path]) => path)).toEqual([guidancePath, guidancePath, detailPath, guidancePath])
})

it('persists a real demo skip before the pending first question can commit', async () => {
  const api = new DemoReadingSessionsApi({ book, latency: 0 })
  const created = await api.create()
  const id = created.data.readingSessionId
  await api.addEvent(id, { eventType: 'STARTED', pageNumber: 1, expectedRevision: 0 })
  const snapshot = (await api.get(id)).data
  const submitGuidance = vi.spyOn(api, 'submitGuidance')
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  try {
    api.latency = 100
    const props = { book, mode: 'demo', storyVersionId: book.storyVersionId, voiceApi: {}, sessionApi: api }
    wrapper = mount(ReaderView, { props: { ...props, initialSnapshot: snapshot } })
    await flushPromises()
    await click('生成本页共读问题')
    expect(submitGuidance).toHaveBeenCalledTimes(1)
    const lateInitial = submitGuidance.mock.results[0].value.catch((error) => error)

    api.latency = 0
    await click('跳过')
    await vi.advanceTimersByTimeAsync(0)
    expect(wrapper.text()).toContain(skipNotice)
    await vi.advanceTimersByTimeAsync(100)
    expect(await lateInitial).toMatchObject({ status: 409, code: 'SESSION_CONFLICT' })
    expect(wrapper.text()).toContain(skipNotice)
    await expectAnswerUnavailable()

    const restored = api.get(id)
    await vi.advanceTimersByTimeAsync(0)
    const { data } = await restored
    expect(data.session.revision).toBe(2)
    expect(data.events.map(({ eventType }) => eventType)).toEqual(['STARTED', 'FEEDBACK'])
    expect(data.events[1]).toMatchObject({ pageNumber: 1, feedback: 'SKIPPED', strategyType: 'SKIP', outputText: '' })
    wrapper.unmount()
    wrapper = mount(ReaderView, { props: { ...props, initialSessionId: id } })
    await vi.advanceTimersByTimeAsync(0)
    await flushPromises()

    expect(wrapper.text()).toContain(skipNotice)
    await expectAnswerUnavailable()
    expect(submitGuidance.mock.calls.map(([, payload]) => payload.feedback)).toEqual(['INITIAL', 'SKIPPED'])
  } finally {
    vi.useRealTimers()
  }
})

it('rechecks the committed first question after a concurrent skip fails with a business 503', async () => {
  let resolveInitial
  const pendingInitial = new Promise((resolve) => { resolveInitial = resolve })
  let questionCommitted = false
  const restoredRevisions = []
  const fetch = vi.fn(async (path, options) => {
    if (path === detailPath) {
      const revision = questionCommitted ? 2 : 1
      restoredRevisions.push(revision)
      return questionCommitted ? detail(revision, [questionEvent]) : detail(revision)
    }
    if (path !== guidancePath) return new Response(null, { status: 404 })
    const { feedback } = JSON.parse(options.body)
    if (feedback === 'INITIAL') return pendingInitial
    return json({ error: { code: 'DEPENDENCY_UNAVAILABLE', message: '暂时无法跳过' } }, 503)
  })
  await openReader(fetch)
  await click('跳过')
  expect(wrapper.text()).not.toContain(skipNotice)
  expect(wrapper.find('[role="dialog"]').exists()).toBe(false)

  questionCommitted = true
  resolveInitial(result(question))
  await flushPromises()

  expect(restoredRevisions).toContain(2)
  expect(wrapper.text()).toContain(question.outputText)
  expect(wrapper.text()).not.toContain(skipNotice)
  await click('孩子回答')
  expect(wrapper.find('[role="dialog"]').exists()).toBe(true)
  expect(guidanceWrites(fetch).map(({ feedback }) => feedback)).toEqual(['INITIAL', 'SKIPPED'])
})
