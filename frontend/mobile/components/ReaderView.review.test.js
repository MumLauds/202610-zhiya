import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/client'
import { ReadingSessionsApi } from '../api/readingSessions'
import ReaderView from './ReaderView.vue'

const book = {
  title: '共读测试',
  storyVersionId: 6,
  cover: '/cover.png',
  pages: [
    { page: 1, scene: '第一页', tokens: [], defaultNextPageNumber: 2 },
    { page: 2, scene: '第二页', tokens: [], defaultNextPageNumber: 3 },
    { page: 3, scene: '第三页', tokens: [], defaultNextPageNumber: null },
  ],
}
const inProgress = {
  readingSessionId: 42,
  storyVersionId: book.storyVersionId,
  status: 'IN_PROGRESS',
  currentPage: 1,
  revision: 1,
  visitedPageNumbers: [1],
}
const pageTwo = { ...inProgress, currentPage: 2, revision: 2, visitedPageNumbers: [1, 2] }
const initialQuestion = {
  status: 'SUCCESS',
  interactionEventId: 22,
  outputText: '你看到了什么？',
  revision: 2,
}

let wrapper

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  vi.unstubAllGlobals()
})

async function openReader({ snapshot = inProgress, events = [], sessionApi = {}, voiceApi = {} } = {}) {
  const api = {
    create: vi.fn(),
    get: vi.fn(),
    addEvent: vi.fn(),
    finish: vi.fn(),
    submitGuidance: vi.fn().mockResolvedValue({ data: initialQuestion }),
    ...sessionApi,
  }
  wrapper = mount(ReaderView, {
    props: {
      book,
      mode: 'live',
      storyVersionId: book.storyVersionId,
      initialSnapshot: snapshot ? { session: snapshot, events, report: null } : null,
      sessionApi: api,
      voiceApi,
    },
  })
  await flushPromises()
  return api
}

async function clickButton(label) {
  const button = wrapper.findAll('button').find((candidate) => candidate.text() === label)
  expect(button, `missing button: ${label}`).toBeDefined()
  await button.trigger('click')
  await flushPromises()
}

describe('ReaderView session recovery and interaction', () => {
  it('directs an unknown create outcome to growth records without a retry button', async () => {
    const api = await openReader({
      snapshot: null,
      sessionApi: {
        create: vi.fn().mockRejectedValue(new ApiError('创建响应丢失', { code: 'REQUEST_OUTCOME_UNKNOWN' })),
      },
    })
    expect(wrapper.text()).toContain('成长记录')
    expect(wrapper.findAll('button').map((button) => button.text())).not.toContain('重试')
    expect(api.create).toHaveBeenCalledTimes(1)
  })

  it('exits a reading session from the last page without finishing', async () => {
    const api = await openReader({
      snapshot: { ...pageTwo, currentPage: 3, revision: 3, visitedPageNumbers: [1, 2, 3] },
    })
    expect(wrapper.get('.page-navigation').text()).toContain('完成阅读')
    await wrapper.get('[aria-label="返回绘本馆"]').trigger('click')
    expect(wrapper.emitted('exit')).toHaveLength(1)
    expect(wrapper.emitted('finished')).toBeUndefined()
    expect(api.finish).not.toHaveBeenCalled()
    expect(api.addEvent).not.toHaveBeenCalled()
  })

  it('keeps the finished facts after a failed summary refresh and retries with GET only', async () => {
    const lastPage = { ...inProgress, currentPage: 3, revision: 3, visitedPageNumbers: [1, 2, 3] }
    const finished = { ...lastPage, status: 'FINISHED', revision: 4 }
    const pending = { status: 'PENDING', summary: '本次共读访问了三页。', activities: [] }
    const ready = { ...pending, status: 'READY', summary: '最终小结' }
    const reply = (report) => new Response(JSON.stringify({ data: { session: finished, events: [], report } }))
    const fetch = vi.fn().mockResolvedValueOnce(reply(pending))
      .mockResolvedValueOnce(new Response(null, { status: 503 }))
      .mockResolvedValueOnce(reply(ready))
    vi.stubGlobal('fetch', fetch)
    const api = new ReadingSessionsApi()
    const writes = await openReader({ snapshot: lastPage, sessionApi: { finish: api.finish.bind(api), get: api.get.bind(api) } })
    await clickButton('完成阅读')
    const facts = () => wrapper.get('.summary-facts').findAll('dd').map(item => item.text())
    expect(facts()).toEqual(['3', '4', 'PENDING'])

    await clickButton('刷新小结')
    expect(facts()).toEqual(['3', '4', 'PENDING'])
    expect(wrapper.get('.summary-panel').text()).toContain(pending.summary)
    expect(wrapper.get('.summary-panel [role="alert"]').text()).toContain('503')
    await clickButton('刷新小结')
    expect(facts()).toEqual(['3', '4', 'READY'])
    expect(wrapper.get('.summary-panel').text()).toContain(ready.summary)
    expect(wrapper.findAll('button').some(button => button.text() === '刷新小结')).toBe(false)
    expect(fetch.mock.calls.map(([path, options]) => [options.method, path])).toEqual([
      ['POST', '/api/v1/reading-sessions/42/completions'],
      ['GET', '/api/v1/reading-sessions/42'],
      ['GET', '/api/v1/reading-sessions/42'],
    ])
    expect(writes.addEvent).not.toHaveBeenCalled()
    expect(writes.submitGuidance).not.toHaveBeenCalled()
    expect(wrapper.emitted('finished')).toHaveLength(1)
  })

  it('rechecks and starts the existing CREATED session after a failed STARTED write', async () => {
    const created = { ...inProgress, status: 'CREATED', revision: 0, visitedPageNumbers: [] }
    const api = await openReader({
      snapshot: null,
      sessionApi: {
        create: vi.fn().mockResolvedValue({ data: created }),
        addEvent: vi.fn()
          .mockRejectedValueOnce(new ApiError('暂时无法开始', { code: 'BAD_REQUEST', status: 400 }))
          .mockResolvedValueOnce({ data: { revision: 1 } }),
        get: vi.fn().mockResolvedValue({ data: { session: created } }),
      },
    })
    expect(wrapper.text()).toContain('暂时无法开始')
    await clickButton('开始本次阅读')
    expect(api.create).toHaveBeenCalledTimes(1)
    expect(api.get.mock.calls[0][0]).toBe(42)
    expect(api.addEvent).toHaveBeenCalledTimes(2)
    expect(api.addEvent.mock.calls[1].slice(0, 2)).toEqual([
      42, expect.objectContaining({ eventType: 'STARTED', pageNumber: 1, expectedRevision: 0 }),
    ])
    expect(api.get.mock.invocationCallOrder[0]).toBeLessThan(api.addEvent.mock.invocationCallOrder[1])
    expect(wrapper.get('.page-scene').text()).toBe('第一页')
  })

  it('keeps reviewing visited pages local and hides interaction controls until the current page', async () => {
    const api = await openReader({ snapshot: pageTwo })
    await clickButton('上一页')
    expect(wrapper.get('.page-scene').text()).toBe('第一页')
    expect(wrapper.text()).toContain('正在回看已读页')
    expect(wrapper.find('.interaction-card').exists()).toBe(false)
    expect(wrapper.find('.page-audio-request').exists()).toBe(false)

    await clickButton('下一页 →')
    expect(wrapper.get('.page-scene').text()).toBe('第二页')
    expect(wrapper.find('.interaction-card').exists()).toBe(true)
    expect(api.addEvent).not.toHaveBeenCalled()
    expect(api.submitGuidance).not.toHaveBeenCalled()
  })

  it('sends confirmed manual text as a page-scoped ANSWER event', async () => {
    const api = await openReader({
      sessionApi: { addEvent: vi.fn().mockResolvedValue({ data: { revision: 3 } }) },
    })
    await clickButton('孩子回答')
    await clickButton('改用文字输入')
    await wrapper.get('textarea').setValue(' 小兔子 ')
    await clickButton('确认回答')

    expect(api.addEvent).toHaveBeenCalledTimes(1)
    expect(api.addEvent.mock.calls[0].slice(0, 2)).toEqual([
      42, expect.objectContaining({
        eventType: 'ANSWER',
        inputText: '小兔子',
        pageNumber: 1,
        expectedRevision: 2,
        clientEventId: expect.any(String),
      }),
    ])
    expect(wrapper.get('.answer-confirmation').text()).toContain('小兔子')
  })

  it('synthesizes and plays the first answer audio from a current result', async () => {
    const play = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('Audio', class extends EventTarget {
      play = play
      pause() {}
      removeAttribute() {}
      load() {}
    })
    const synthesizeInteraction = vi.fn().mockResolvedValue({ data: { audioUrl: '/answer.mp3' } })
    await openReader({ voiceApi: { synthesizeInteraction } })

    await clickButton('生成本页共读问题')
    await clickButton('播放回答音频')
    expect(synthesizeInteraction).toHaveBeenCalledExactlyOnceWith({ interactionEventId: 22, signal: expect.any(AbortSignal) })
    expect(play).toHaveBeenCalledTimes(1)
    expect(play.mock.contexts[0].src).toBe('/answer.mp3')
  })

  it('automatically reads progress after losing a write response and advances without replaying', async () => {
    const api = await openReader({
      sessionApi: {
        addEvent: vi.fn().mockRejectedValue(new ApiError('响应丢失', { code: 'REQUEST_OUTCOME_UNKNOWN' })),
        get: vi.fn().mockResolvedValue({ data: { session: pageTwo } }),
      },
    })
    await clickButton('下一页 →')
    expect(api.addEvent).toHaveBeenCalledTimes(1)
    expect(api.get).toHaveBeenCalledTimes(1)
    expect(api.get.mock.calls[0][0]).toBe(42)
    expect(wrapper.get('.page-scene').text()).toBe('第二页')
    expect(wrapper.text()).toContain('已重新读取会话状态')
  })

  it('locks writes after the automatic read fails until manual recheck succeeds', async () => {
    const api = await openReader({
      sessionApi: {
        addEvent: vi.fn()
          .mockRejectedValueOnce(new ApiError('响应丢失', { code: 'REQUEST_OUTCOME_UNKNOWN' }))
          .mockResolvedValueOnce({ data: { revision: 3 } }),
        get: vi.fn()
          .mockRejectedValueOnce(new ApiError('读取失败', { code: 'NETWORK_ERROR' }))
          .mockResolvedValueOnce({ data: { session: pageTwo } }),
      },
    })
    await clickButton('下一页 →')
    expect(wrapper.text()).toContain('读取失败')
    expect(wrapper.find('.page-navigation').exists()).toBe(false)
    expect(wrapper.find('.interaction-card').exists()).toBe(false)
    expect(api.addEvent).toHaveBeenCalledTimes(1)
    expect(api.get).toHaveBeenCalledTimes(1)

    await clickButton('重新核对进度')
    expect(api.get).toHaveBeenCalledTimes(2)
    expect(api.addEvent).toHaveBeenCalledTimes(1)
    expect(wrapper.get('.page-scene').text()).toBe('第二页')

    await clickButton('下一页 →')
    expect(api.addEvent.mock.calls[1].slice(0, 2)).toEqual([
      42, expect.objectContaining({ eventType: 'PAGE_CHANGED', pageNumber: 3, expectedRevision: 2 }),
    ])
    expect(wrapper.get('.page-scene').text()).toBe('第三页')
  })

  it('PR41 followup prompts account history after an HTML 504 without offering another create', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response('<h1>Gateway Timeout</h1>', { status: 504 }))
    vi.stubGlobal('fetch', fetch)
    const api = new ReadingSessionsApi()
    await openReader({ snapshot: null, sessionApi: { create: api.create.bind(api) } })

    expect(wrapper.text()).toContain('请返回成长记录核对刚创建的会话')
    expect(wrapper.findAll('button').map((button) => button.text())).not.toContain('重试')
    expect(fetch).toHaveBeenCalledExactlyOnceWith('/api/v1/reading-sessions', expect.objectContaining({ method: 'POST' }))
  })

  it('PR41 followup preserves the answer after a business 503 and closes the dialog on success', async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        error: { code: 'DEPENDENCY_UNAVAILABLE', message: '暂时无法保存回答', retryable: false },
      }), { status: 503 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ data: { revision: 3 } }), { status: 201 }))
    vi.stubGlobal('fetch', fetch)
    const api = new ReadingSessionsApi()
    const addEvent = vi.fn(api.addEvent.bind(api))
    await openReader({ sessionApi: { addEvent } })
    await clickButton('孩子回答')
    await clickButton('改用文字输入')
    await wrapper.get('textarea').setValue('小兔子在树下')
    await clickButton('确认回答')

    expect(wrapper.get('[role="dialog"]').text()).toContain('暂时无法保存回答')
    expect(wrapper.get('textarea').element.value).toBe('小兔子在树下')
    expect(addEvent).toHaveBeenCalledTimes(1)
    await clickButton('确认回答')

    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(wrapper.get('.answer-confirmation').text()).toContain('小兔子在树下')
    expect(addEvent).toHaveBeenCalledTimes(2)
    expect(addEvent.mock.calls.map(([, payload]) => payload.inputText)).toEqual(['小兔子在树下', '小兔子在树下'])
  })

  it('PR41 followup restores a question from a same-key recheck and opens the answer without asking again', async () => {
    const submitGuidance = vi.fn()
      .mockRejectedValueOnce(new ApiError('响应丢失', { code: 'REQUEST_OUTCOME_UNKNOWN' }))
      .mockResolvedValueOnce({ data: initialQuestion })
    const get = vi.fn().mockResolvedValue({ data: { session: inProgress, events: [], report: null } })
    await openReader({ sessionApi: { submitGuidance, get } })
    await clickButton('生成本页共读问题')
    expect(submitGuidance).toHaveBeenCalledTimes(1)
    await clickButton('重新核对进度')

    expect(get).toHaveBeenCalledTimes(2)
    expect(submitGuidance).toHaveBeenCalledTimes(2)
    expect(submitGuidance.mock.calls[0][1].clientEventId).toEqual(expect.any(String))
    expect(submitGuidance.mock.calls[1].slice(0, 2)).toEqual(submitGuidance.mock.calls[0].slice(0, 2))
    expect(wrapper.text()).toContain(initialQuestion.outputText)
    await clickButton('孩子回答')
    expect(wrapper.find('[role="dialog"]').exists()).toBe(true)
    expect(submitGuidance).toHaveBeenCalledTimes(2)
  })

  it('PR41 followup recovers the current page question from an API-013 snapshot', async () => {
    const events = [
      { eventType: 'QUESTION', pageNumber: 1, interactionEventId: 21, strategyType: 'CONTEXT_QUESTION', outputText: '上一页的问题' },
      { eventType: 'QUESTION', pageNumber: 2, interactionEventId: 22, strategyType: 'CONTEXT_QUESTION', outputText: initialQuestion.outputText },
    ]
    const api = await openReader({ snapshot: pageTwo, events })

    expect(wrapper.text()).toContain(initialQuestion.outputText)
    expect(wrapper.text()).not.toContain('上一页的问题')
    await clickButton('孩子回答')
    expect(wrapper.find('[role="dialog"]').exists()).toBe(true)
    expect(api.submitGuidance).not.toHaveBeenCalled()
  })

  it('PR41 followup terminal unknown releases the reader after checking the same revision without replay', async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      error: { code: 'REQUEST_OUTCOME_UNKNOWN', message: '提交结果未知', retryable: false },
    }), { status: 504 }))
    vi.stubGlobal('fetch', fetch)
    const api = new ReadingSessionsApi()
    const get = vi.fn().mockResolvedValue({ data: { session: inProgress, events: [], report: null } })
    await openReader({ sessionApi: { get, submitGuidance: api.submitGuidance.bind(api) } })
    await clickButton('生成本页共读问题')

    expect(get).toHaveBeenCalledExactlyOnceWith(42, expect.any(Object))
    expect(wrapper.text()).not.toContain('重新核对进度')
    expect(wrapper.get('.page-scene').text()).toBe('第一页')
    expect(wrapper.find('.interaction-card').exists()).toBe(true)
    expect(fetch).toHaveBeenCalledExactlyOnceWith('/api/v1/reading-sessions/42/guidance', expect.objectContaining({ method: 'POST' }))
  })

  it('PR41 followup gateway answer recovers the original text by replaying the same event key', async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(new Response('<h1>Gateway Timeout</h1>', { status: 504 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ data: { revision: 3 } }), { status: 201 }))
    vi.stubGlobal('fetch', fetch)
    const api = new ReadingSessionsApi()
    const get = vi.fn().mockResolvedValue({
      data: { session: { ...inProgress, revision: 2 }, events: [], report: null },
    })
    await openReader({ sessionApi: { get, addEvent: api.addEvent.bind(api) } })
    await clickButton('孩子回答')
    await clickButton('改用文字输入')
    await wrapper.get('textarea').setValue('小兔子在草地上')
    await clickButton('确认回答')

    expect(fetch).toHaveBeenCalledTimes(1)
    expect(wrapper.find('.answer-confirmation').exists()).toBe(false)
    await clickButton('重新核对进度')

    expect(get).toHaveBeenCalledTimes(2)
    expect(fetch).toHaveBeenCalledTimes(2)
    const firstBody = JSON.parse(fetch.mock.calls[0][1].body)
    expect(firstBody).toMatchObject({
      eventType: 'ANSWER', inputText: '小兔子在草地上', pageNumber: 1,
      expectedRevision: 2, clientEventId: expect.any(String),
    })
    expect(fetch.mock.calls.map(([path]) => path)).toEqual(Array(2).fill('/api/v1/reading-sessions/42/events'))
    expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual(firstBody)
    expect(wrapper.get('.answer-confirmation').text()).toContain('小兔子在草地上')
    expect(wrapper.text()).not.toContain('重新核对进度')
  })

  it.each([1, 2])('PR41 final recovery replays a committed answer without regressing the snapshot on page %i', async (currentPage) => {
    const text = '小兔子在草地上'
    const latest = { ...inProgress, currentPage, revision: 5, visitedPageNumbers: currentPage === 1 ? [1] : [1, 2] }
    const committed = new Map()
    const posts = []
    const fetch = vi.fn(async (path, options) => {
      if (path === '/api/v1/reading-sessions/42') {
        return new Response(JSON.stringify({
          data: {
            session: latest,
            events: [
              { ...initialQuestion, eventType: 'QUESTION', pageNumber: 1 },
              ...[...committed.values()].map(({ event }) => event),
            ],
            report: null,
          },
        }))
      }
      if (path !== '/api/v1/reading-sessions/42/events') return new Response(null, { status: 404 })

      const payload = JSON.parse(options.body)
      posts.push(payload)
      const cached = committed.get(payload.clientEventId)
      if (cached) return new Response(JSON.stringify({ data: cached.receipt }), { status: 201 })

      const receipt = { interactionEventId: 23, revision: 3 }
      const event = { ...payload, interactionEventId: receipt.interactionEventId }
      committed.set(payload.clientEventId, { event, receipt })
      return new Response('<h1>Gateway Timeout</h1>', { status: 504 })
    })
    vi.stubGlobal('fetch', fetch)
    const api = new ReadingSessionsApi()
    await openReader({ sessionApi: { get: api.get.bind(api), addEvent: api.addEvent.bind(api) } })
    await clickButton('孩子回答')
    await clickButton('改用文字输入')
    await wrapper.get('textarea').setValue(text)
    await clickButton('确认回答')

    expect(posts).toHaveLength(1)
    expect(wrapper.find('.answer-confirmation').exists()).toBe(false)
    await clickButton('重新核对进度')

    expect(committed.size).toBe(1)
    expect(posts).toHaveLength(2)
    expect(posts[0]).toMatchObject({
      eventType: 'ANSWER', inputText: text, pageNumber: 1,
      expectedRevision: 2, clientEventId: expect.any(String),
    })
    expect(posts[1]).toEqual(posts[0])
    expect(wrapper.get('.revision-tag').text()).toBe('rev 5')
    expect(wrapper.get('.page-scene').text()).toBe(currentPage === 1 ? '第一页' : '第二页')
    expect(wrapper.text()).not.toContain('重新核对进度')
    if (currentPage === 1) {
      expect(wrapper.get('.answer-confirmation').text()).toContain(text)
      await clickButton('孩子回答')
      await clickButton('改用文字输入')
      expect(wrapper.get('textarea').element.value).toBe('')
    } else {
      expect(wrapper.find('.answer-confirmation').exists()).toBe(false)
    }
  })

  it('PR41 final recovery reuses a newly generated question after revisiting a read page', async () => {
    const submitGuidance = vi.fn().mockResolvedValue({ data: { ...initialQuestion, revision: 3 } })
    const api = await openReader({ snapshot: pageTwo, sessionApi: { submitGuidance } })
    await clickButton('生成本页共读问题')
    expect(wrapper.text()).toContain(initialQuestion.outputText)

    await clickButton('上一页')
    expect(wrapper.get('.page-scene').text()).toBe('第一页')
    expect(wrapper.text()).not.toContain(initialQuestion.outputText)
    await clickButton('下一页 →')
    expect(wrapper.get('.page-scene').text()).toBe('第二页')
    expect(wrapper.text()).toContain(initialQuestion.outputText)

    await clickButton('孩子回答')
    expect(wrapper.find('[role="dialog"]').exists()).toBe(true)
    expect(submitGuidance).toHaveBeenCalledExactlyOnceWith(42, expect.objectContaining({
      pageNumber: 2, feedback: 'INITIAL', expectedRevision: 2,
    }), expect.any(Object))
    expect(api.addEvent).not.toHaveBeenCalled()
  })
})
