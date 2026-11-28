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
const savedQuestion = (pageNumber, revision) => ({
  ...initialQuestion, interactionEventId: 21 + pageNumber, eventType: 'QUESTION', pageNumber, revision,
})
const questionedPageOne = { ...inProgress, revision: 2 }
const questionedPageTwo = { ...pageTwo, revision: 3 }
const recoveredPageTwo = {
  session: { ...pageTwo, revision: 4 },
  events: [savedQuestion(1, 2), savedQuestion(2, 4)],
}

let wrapper

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  vi.unstubAllGlobals()
})

async function openReader({
  snapshot = inProgress, initialSessionId = null, events = [], answerOptions, readerBook = book, sessionApi = {}, voiceApi = {},
} = {}) {
  const api = {
    create: vi.fn(),
    get: vi.fn(),
    addEvent: vi.fn(),
    finish: vi.fn(),
    submitGuidance: vi.fn(async (_, { expectedRevision }) => ({
      data: { ...initialQuestion, revision: expectedRevision + 1 },
    })),
    ...sessionApi,
  }
  wrapper = mount(ReaderView, {
    props: {
      book: readerBook,
      mode: 'live',
      storyVersionId: book.storyVersionId,
      initialSessionId,
      initialSnapshot: snapshot ? { session: snapshot, events, report: null, answerOptions } : null,
      sessionApi: api,
      voiceApi: {
        synthesizePage: vi.fn().mockRejectedValue(new ApiError('朗读暂不可用', { status: 503 })),
        ...voiceApi,
      },
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
  it('restores a retried ASK only in the story answer card', async () => {
    const snapshot = { ...inProgress, mode: 'CHILD_SOLO' }
    const reply = { interactionEventId: 12, revision: 2, status: 'GENERATED',
      strategyType: 'CONCRETE_RECALL', outputText: '小狐狸举着伞。' }
    const submitQuestion = vi.fn()
      .mockRejectedValueOnce(new ApiError('offline', { code: 'REQUEST_OUTCOME_UNKNOWN' }))
      .mockResolvedValueOnce({ data: reply })
    await openReader({ snapshot, sessionApi: { submitQuestion,
      submitGuidance: vi.fn().mockRejectedValue(new ApiError('题目暂不可用', { status: 503, code: 'DEPENDENCY_UNAVAILABLE' })),
      get: vi.fn().mockResolvedValue({ data: { session: snapshot, events: [], report: null } }) } })
    await clickButton('故事里有谁？')
    await clickButton('重新核对进度')
    expect(submitQuestion.mock.calls[1][1]).toEqual(submitQuestion.mock.calls[0][1])
    expect(wrapper.text()).toContain(reply.outputText)
    expect(wrapper.getComponent({ name: 'InteractionCard' }).props('questionReady')).toBe(false)
  })

  it('offers child questions without free text and plays the returned story answer', async () => {
    vi.stubGlobal('Audio', class extends EventTarget {
      play = vi.fn().mockResolvedValue(undefined)
      pause() {}
      load() {}
      removeAttribute() {}
    })
    const result = { interactionEventId: 121, revision: 3, outputText: '小狐狸举着伞。', status: 'GENERATED', strategyType: 'CONCRETE_RECALL' }
    const voice = { synthesizeInteraction: vi.fn().mockResolvedValue({ data: { audioUrl: '/story-audio', expiresAt: '2099-01-01' } }) }
    const api = await openReader({ snapshot: { ...inProgress, mode: 'CHILD_SOLO' },
      sessionApi: { submitQuestion: vi.fn().mockResolvedValueOnce({ data: result }) }, voiceApi: voice })
    expect(wrapper.text()).not.toContain('提出本页问题')
    await clickButton('故事里有谁？')
    expect(api.submitQuestion).toHaveBeenCalledWith(42, expect.objectContaining({
      inputText: '故事里有谁？', expectedRevision: 2, pageNumber: 1,
    }), expect.any(Object))
    expect(api.addEvent).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain(result.outputText)
    await clickButton('播放芽芽的回答')
    expect(voice.synthesizeInteraction).toHaveBeenCalledWith({ interactionEventId: 121, signal: expect.any(AbortSignal) })
  })

  it('submits a confirmed mobile question without first answering a generated prompt', async () => {
    const api = await openReader({
      snapshot: questionedPageOne,
      events: [savedQuestion(1, 2)],
      sessionApi: { submitQuestion: vi.fn().mockResolvedValueOnce({ data: {
        interactionEventId: 15, revision: 3, outputText: '一起看看书页吧。', status: 'FALLBACK', strategyType: 'NO_KNOWLEDGE_FALLBACK',
      } }) },
    })
    await clickButton('提出本页问题')
    await clickButton('改用文字输入')
    await wrapper.get('textarea').setValue('它在做什么？')
    expect(api.submitQuestion).not.toHaveBeenCalled()
    await clickButton('确认提问')
    expect(api.submitQuestion).toHaveBeenCalledWith(42, expect.objectContaining({
      inputText: '它在做什么？', expectedRevision: 2,
    }), expect.any(Object))
    expect(api.submitGuidance).not.toHaveBeenCalled()
    expect(api.addEvent).not.toHaveBeenCalled()
  })

  it('does not show or play an ASK reply from before a branch returned to the same page', async () => {
    const oldAnswer = { interactionEventId: 23, revision: 3, eventType: 'ASK', pageNumber: 1,
      outputText: '上次回答：小兔拿着黄伞。', strategyType: 'CONCRETE_RECALL' }
    const newAnswer = { interactionEventId: 28, revision: 8, outputText: '本次回答：小兔走到树旁。',
      status: 'GENERATED', strategyType: 'CONCRETE_RECALL' }
    const api = await openReader({
      snapshot: { ...questionedPageOne, mode: 'PARENT_CHILD', revision: 3 },
      events: [savedQuestion(1, 2), oldAnswer],
      readerBook: { ...book, branches: [{ branchId: 7, fromPageNumber: 2, nextPageNumber: 1,
        choiceLabel: '回到第一页', reviewStatus: 'APPROVED' }] },
      sessionApi: {
        addEvent: vi.fn().mockResolvedValue({ data: { interactionEventId: 24, revision: 4 } }),
        chooseBranch: vi.fn().mockResolvedValue({ data: { interactionEventId: 26, revision: 6,
          branchId: 7, currentPage: 1, visitedPageNumbers: [1, 2, 1] } }),
        submitGuidance: vi.fn(async (_, { expectedRevision }) => ({
          data: { ...initialQuestion, interactionEventId: expectedRevision + 21, revision: expectedRevision + 1 },
        })),
        submitQuestion: vi.fn().mockResolvedValue({ data: newAnswer }),
      },
    })
    expect(wrapper.text()).toContain(oldAnswer.outputText)
    expect(wrapper.text()).toContain('播放芽芽的回答')

    await clickButton('下一页 →')
    await clickButton('回到第一页')
    expect(wrapper.get('.page-scene').text()).toBe('第一页')
    expect(wrapper.text()).not.toContain(oldAnswer.outputText)
    expect(wrapper.text()).not.toContain('播放芽芽的回答')

    await clickButton('提出本页问题')
    await clickButton('改用文字输入')
    await wrapper.get('textarea').setValue('小兔现在在哪里？')
    await clickButton('确认提问')
    expect(api.submitQuestion).toHaveBeenCalledOnce()
    expect(wrapper.text()).toContain(newAnswer.outputText)
    expect(wrapper.text()).toContain('播放芽芽的回答')
  })

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
    const lastPage = { ...inProgress, currentPage: 3, revision: 4, visitedPageNumbers: [1, 2, 3] }
    const finished = { ...lastPage, status: 'FINISHED', revision: 5 }
    const pending = { status: 'PENDING', summary: '本次共读访问了三页。', activities: [] }
    const ready = { ...pending, status: 'READY', summary: '最终小结' }
    const reply = (report) => new Response(JSON.stringify({ data: { session: finished, events: [], report } }))
    const fetch = vi.fn().mockResolvedValueOnce(reply(pending))
      .mockResolvedValueOnce(new Response(null, { status: 503 }))
      .mockResolvedValueOnce(reply(ready))
    vi.stubGlobal('fetch', fetch)
    const api = new ReadingSessionsApi()
    const writes = await openReader({
      snapshot: lastPage,
      events: [savedQuestion(3, 4)],
      sessionApi: { finish: api.finish.bind(api), get: api.get.bind(api) },
    })
    await clickButton('完成阅读')
    const facts = () => wrapper.get('.summary-facts').findAll('dd').map(item => item.text())
    expect(facts()).toEqual(['3', '5', 'PENDING'])

    await clickButton('刷新小结')
    expect(facts()).toEqual(['3', '5', 'PENDING'])
    expect(wrapper.get('.summary-panel').text()).toContain(pending.summary)
    expect(wrapper.get('.summary-panel [role="alert"]').text()).toContain('503')
    await clickButton('刷新小结')
    expect(facts()).toEqual(['3', '5', 'READY'])
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

  it('plays the requested page when the first tap arrives before audio preparation completes', async () => {
    const play = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('Audio', class extends EventTarget {
      play = play
      pause() {}
      removeAttribute() {}
      load() {}
    })
    let resolveAudio
    const synthesizePage = vi.fn(() => new Promise(resolve => { resolveAudio = resolve }))
    await openReader({ snapshot: questionedPageOne, events: [savedQuestion(1, 2)], voiceApi: { synthesizePage } })

    await wrapper.get('button.reading-text').trigger('click')
    expect(synthesizePage).toHaveBeenCalledOnce()
    expect(play).not.toHaveBeenCalled()
    resolveAudio({ data: { audioUrl: '/page-one.mp3' } })
    await flushPromises()
    expect(play).toHaveBeenCalledOnce()
    expect(play.mock.contexts[0].src).toBe('/page-one.mp3')
  })

  it('only preloads after a narration request and does not autoplay on later pages', async () => {
    const play = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal('Audio', class extends EventTarget {
      play = play
      pause() {}
      removeAttribute() {}
      load() {}
    })
    const synthesizePage = vi.fn(async ({ pageNumber }) => ({ data: { audioUrl: `/page-${pageNumber}.mp3` } }))
    await openReader({
      snapshot: questionedPageOne, events: [savedQuestion(1, 2)], voiceApi: { synthesizePage },
      sessionApi: { addEvent: vi.fn().mockResolvedValueOnce({ data: { revision: 3 } })
        .mockResolvedValueOnce({ data: { revision: 5 } }) },
    })
    expect(synthesizePage).not.toHaveBeenCalled()
    await clickButton('下一页 →')
    expect(wrapper.get('.page-scene').text()).toBe('第二页')
    expect(synthesizePage).not.toHaveBeenCalled()

    await clickButton('播放本页朗读')
    expect(play).toHaveBeenCalledOnce()
    await clickButton('下一页 →')
    expect(wrapper.get('.page-scene').text()).toBe('第三页')
    expect(synthesizePage.mock.calls.map(([source]) => source.pageNumber)).toEqual([2, 3])
    expect(play).toHaveBeenCalledOnce()
  })

  it('keeps reviewing visited pages local and hides interaction controls until the current page', async () => {
    const synthesizePage = vi.fn().mockRejectedValue(new ApiError('朗读暂不可用', { status: 503 }))
    const api = await openReader({ snapshot: questionedPageTwo, events: [savedQuestion(2, 3)], voiceApi: { synthesizePage } })
    expect(synthesizePage).not.toHaveBeenCalled()
    await clickButton('上一页')
    expect(wrapper.get('.page-scene').text()).toBe('第一页')
    expect(wrapper.text()).toContain('正在回看已读页')
    expect(wrapper.find('.interaction-card').exists()).toBe(false)
    expect(wrapper.find('.page-audio-request').exists()).toBe(false)
    expect(synthesizePage).not.toHaveBeenCalled()

    await clickButton('下一页 →')
    expect(wrapper.get('.page-scene').text()).toBe('第二页')
    expect(wrapper.find('.interaction-card').exists()).toBe(true)
    expect(api.addEvent).not.toHaveBeenCalled()
    expect(api.submitGuidance).not.toHaveBeenCalled()
  })

  it('returns to live reading only at the final visit even when the page number repeats', async () => {
    const api = await openReader({
      snapshot: { ...questionedPageOne, visitedPageNumbers: [1, 2, 1] },
      events: [savedQuestion(1, 2)],
    })
    await clickButton('上一页')
    expect(wrapper.get('.page-scene').text()).toBe('第二页')
    await clickButton('上一页')
    expect(wrapper.get('.page-scene').text()).toBe('第一页')
    expect(wrapper.text()).toContain('正在回看已读页')
    expect(wrapper.find('.interaction-card').exists()).toBe(false)
    await clickButton('下一页 →')
    expect(wrapper.get('.page-scene').text()).toBe('第二页')
    await clickButton('下一页 →')
    expect(wrapper.get('.page-scene').text()).toBe('第一页')
    expect(wrapper.find('.interaction-card').exists()).toBe(true)
    expect(api.addEvent).not.toHaveBeenCalled()
    expect(api.submitGuidance).not.toHaveBeenCalled()
  })

  it('lets the child submit only an approved option and updates options after a follow-up', async () => {
    const submitGuidance = vi.fn()
      .mockResolvedValueOnce({ data: { ...initialQuestion, answerOptions: ['是', '不是', '不知道'] } })
      .mockResolvedValueOnce({ data: { ...initialQuestion, revision: 4, outputText: '还看到什么？', answerOptions: ['风声', '雨声'] } })
    const api = await openReader({
      snapshot: { ...inProgress, mode: 'CHILD_SOLO' },
      sessionApi: { submitGuidance, addEvent: vi.fn().mockResolvedValue({ data: { revision: 3 } }) },
    })
    expect(wrapper.text()).not.toContain('孩子回答')
    await clickButton('不知道')
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(api.addEvent).toHaveBeenCalledExactlyOnceWith(42, expect.objectContaining({
      eventType: 'ANSWER', inputText: '不知道', pageNumber: 1, expectedRevision: 2,
    }))
    await clickButton('没听懂')
    expect(wrapper.findAll('button').map(button => button.text())).toContain('雨声')
    expect(wrapper.findAll('button').map(button => button.text())).not.toContain('不知道')
  })

  it('uses authoritative restored options and fetches them for a revisited page with a saved question', async () => {
    const events = [savedQuestion(1, 2), savedQuestion(2, 3)]
    const child = { ...questionedPageTwo, mode: 'CHILD_SOLO' }
    const revisited = { ...child, currentPage: 1, visitedPageNumbers: [1, 2, 1], revision: 4 }
    const get = vi.fn()
      .mockResolvedValueOnce({ data: { session: revisited, events: [events[0]], report: null, answerOptions: ['第一页选项'] } })
      .mockResolvedValueOnce({ data: { session: { ...child, revision: 5, visitedPageNumbers: [1, 2, 1, 2] },
        events: [events[1]], report: null, answerOptions: ['第二页选项'] } })
    const api = await openReader({
      snapshot: child, events, answerOptions: ['第二页选项'],
      readerBook: { ...book, pages: book.pages.map(page => page.page === 2 ? { ...page, defaultNextPageNumber: 1 } : page) },
      sessionApi: { get, addEvent: vi.fn()
        .mockResolvedValueOnce({ data: { revision: 4 } }).mockResolvedValueOnce({ data: { revision: 5 } }) },
    })
    expect(wrapper.findAll('button').map(button => button.text())).toContain('第二页选项')
    await clickButton('下一页 →')
    expect(wrapper.get('.page-scene').text()).toBe('第一页')
    expect(wrapper.findAll('button').map(button => button.text())).toContain('第一页选项')
    expect(wrapper.findAll('button').map(button => button.text())).not.toContain('第二页选项')
    expect(api.get).toHaveBeenCalledOnce()
    await clickButton('下一页 →')
    expect(wrapper.get('.page-scene').text()).toBe('第二页')
    expect(wrapper.findAll('button').map(button => button.text())).toContain('第二页选项')
    expect(api.get).toHaveBeenCalledTimes(2)
    expect(api.submitGuidance).not.toHaveBeenCalled()
  })

  it('shows an unavailable state when the server restores an empty list of child options', async () => {
    const api = await openReader({
      snapshot: { ...questionedPageOne, mode: 'CHILD_SOLO' },
      events: [savedQuestion(1, 2)], answerOptions: [],
    })
    expect(wrapper.text()).toContain('本页暂无可提交的答题选项')
    expect(wrapper.text()).not.toContain('孩子回答')
    expect(api.addEvent).not.toHaveBeenCalled()
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

    await clickButton('播放回答音频')
    expect(synthesizeInteraction).toHaveBeenCalledExactlyOnceWith({ interactionEventId: 22, signal: expect.any(AbortSignal) })
    expect(play).toHaveBeenCalledTimes(1)
    expect(play.mock.contexts[0].src).toBe('/answer.mp3')
  })

  it('automatically reads progress after losing a write response and advances without replaying', async () => {
    const api = await openReader({
      snapshot: questionedPageOne,
      events: [savedQuestion(1, 2)],
      sessionApi: {
        addEvent: vi.fn().mockRejectedValue(new ApiError('响应丢失', { code: 'REQUEST_OUTCOME_UNKNOWN' })),
        get: vi.fn().mockResolvedValue({ data: recoveredPageTwo }),
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
      snapshot: questionedPageOne,
      events: [savedQuestion(1, 2)],
      sessionApi: {
        addEvent: vi.fn()
          .mockRejectedValueOnce(new ApiError('响应丢失', { code: 'REQUEST_OUTCOME_UNKNOWN' }))
          .mockResolvedValueOnce({ data: { revision: 5 } }),
        get: vi.fn()
          .mockRejectedValueOnce(new ApiError('读取失败', { code: 'NETWORK_ERROR' }))
          .mockResolvedValueOnce({ data: recoveredPageTwo }),
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
      42, expect.objectContaining({ eventType: 'PAGE_CHANGED', pageNumber: 3, expectedRevision: 4 }),
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
              savedQuestion(1, 2),
              ...(currentPage === 2 ? [savedQuestion(2, 5)] : []),
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

describe('guardian answer follow-ups', () => {
  const parent = { ...questionedPageOne, mode: 'PARENT_CHILD' }
  const question = savedQuestion(1, 2)
  const followupQuestion = '雨伞是什么颜色？'
  const replyText = '你找到小狐狸的雨伞啦。'
  const receipt = { interactionEventId: 23, revision: 3, replyText: replyText + followupQuestion, followupQuestion }
  const answer = { ...receipt, pageNumber: 1, eventType: 'ANSWER', inputText: '雨伞' }

  function expectFollowup() {
    const card = wrapper.get('section[aria-labelledby="interaction-title"]')
    expect(card.findAll('.answer-confirmation, .agent-text').map(item => item.text())).toEqual([
      '上一轮回答：雨伞', '芽芽：' + replyText, '芽芽追问：' + followupQuestion,
    ])
    expect(wrapper.text().split(followupQuestion)).toHaveLength(2)
  }

  it('renders the reply and promotes its follow-up instead of the original question', async () => {
    const api = await openReader({
      snapshot: parent, events: [question],
      sessionApi: { addEvent: vi.fn().mockResolvedValue({ data: receipt }) },
    })
    await clickButton('孩子回答')
    await clickButton('改用文字输入')
    await wrapper.get('textarea').setValue('雨伞')
    await clickButton('确认回答')
    expectFollowup()
    expect(wrapper.text()).not.toContain(question.outputText)
    expect(api.addEvent).toHaveBeenCalledTimes(1)
  })

  it('announces while an answer is in progress and prevents duplicate confirmation', async () => {
    let resolve
    const response = new Promise(done => { resolve = done })
    const addEvent = vi.fn().mockReturnValue(response)
    await openReader({ snapshot: parent, events: [question], sessionApi: { addEvent } })
    await clickButton('孩子回答')
    await clickButton('改用文字输入')
    await wrapper.get('textarea').setValue('雨伞')
    await clickButton('确认回答')

    const dialog = wrapper.get('[role="dialog"]')
    expect(dialog.get('[role="status"]').text()).toBe('芽芽正在想怎么回应…')
    const confirm = dialog.get('.dialog-actions .button-primary')
    expect(confirm.element.disabled).toBe(true)
    await confirm.trigger('click')
    expect(addEvent).toHaveBeenCalledTimes(1)

    resolve({ data: receipt })
    await flushPromises()
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expectFollowup()
  })

  it('restores a completed answer and its exact follow-up from the current visit', async () => {
    const api = await openReader({ snapshot: { ...parent, revision: 3 }, events: [question, answer] })
    expectFollowup()
    expect(api.submitGuidance).not.toHaveBeenCalled()
    expect(api.addEvent).not.toHaveBeenCalled()
  })

  it('keeps the real question after a saved fallback without a follow-up and labels the next round correctly', async () => {
    const secondFollowup = '你在哪里看到了这一把伞？'
    const secondAnswer = { interactionEventId: 24, revision: 4, eventType: 'ANSWER', pageNumber: 1, inputText: '红色。',
      replyText: '你看清了颜色。' + secondFollowup, followupQuestion: secondFollowup }
    const fallbackReceipt = { interactionEventId: 25, revision: 5, replyText: '谢谢你分享。我们一起看看画面。' }
    const noFollowup = { ...fallbackReceipt, eventType: 'ANSWER', pageNumber: 1, inputText: '蓝色。' }
    const blankFeedback = { interactionEventId: 26, revision: 6, status: 'FALLBACK',
      strategyType: 'CONTEXT_QUESTION', outputText: '  ' }
    const nextPromptReceipt = { ...initialQuestion, interactionEventId: 27, revision: 7, outputText: '树旁边有什么？' }
    const priorEvents = [question, answer, secondAnswer]
    await openReader({ snapshot: { ...parent, revision: 4 }, events: priorEvents,
      sessionApi: { addEvent: vi.fn().mockResolvedValue({ data: fallbackReceipt }) } })
    await clickButton('孩子回答')
    await clickButton('改用文字输入')
    await wrapper.get('textarea').setValue(noFollowup.inputText)
    await clickButton('确认回答')

    function expectRetainedQuestion() {
      const card = wrapper.get('section[aria-labelledby="interaction-title"]')
      expect(card.text()).not.toContain(question.outputText)
      expect(card.text()).not.toContain(followupQuestion)
      expect(card.findAll('.agent-text, .answer-confirmation').map(item => item.text())).toEqual([
        secondFollowup, '家长确认文本：' + noFollowup.inputText, '芽芽：' + fallbackReceipt.replyText,
      ])
    }

    expectRetainedQuestion()
    wrapper.unmount()
    const api = await openReader({ snapshot: { ...parent, revision: 5 }, events: [...priorEvents, noFollowup],
      sessionApi: { submitGuidance: vi.fn().mockResolvedValue({ data: blankFeedback }) } })
    expectRetainedQuestion()
    expect(api.submitGuidance).not.toHaveBeenCalled()
    await clickButton('已听懂')
    expect(api.submitGuidance).toHaveBeenCalledTimes(1)
    expectRetainedQuestion()

    wrapper.unmount()
    await openReader({ snapshot: { ...parent, revision: 6 }, events: [...priorEvents, noFollowup, {
      ...blankFeedback, status: undefined, eventType: 'FEEDBACK', pageNumber: 1, feedback: 'UNDERSTOOD',
    }], sessionApi: { submitGuidance: vi.fn().mockResolvedValue({ data: nextPromptReceipt }) } })
    expectRetainedQuestion()
    await clickButton('已听懂')
    const card = wrapper.get('section[aria-labelledby="interaction-title"]')
    expect(card.get('.agent-text').text()).toBe(nextPromptReceipt.outputText)
    expect(card.text()).not.toContain(followupQuestion)
    expect(card.text()).not.toContain(secondFollowup)
    expect(card.get('.answer-confirmation').text()).toBe('上一轮回答：' + noFollowup.inputText)
  })

  it('keeps a blank settled initial receipt without generating another prompt', async () => {
    const api = await openReader({ snapshot: parent, events: [{
      ...question, strategyType: 'CONTEXT_QUESTION', outputText: '',
    }] })
    expect(wrapper.text()).toContain('本次没有生成可展示的文字。')
    expect(api.submitGuidance).not.toHaveBeenCalled()
  })

  it('keeps the earlier ordinary question after a blank feedback result', async () => {
    const api = await openReader({ snapshot: parent, events: [question],
      sessionApi: { submitGuidance: vi.fn().mockResolvedValue({ data: {
        interactionEventId: 23, revision: 3, status: 'FALLBACK', strategyType: 'CONTEXT_QUESTION', outputText: '',
      } }) } })
    await clickButton('已听懂')
    expect(wrapper.get('.agent-text').text()).toBe(question.outputText)
    expect(api.submitGuidance).toHaveBeenCalledTimes(1)
  })

  it('reconciles a saved answer after 503 without re-submitting the draft or repeating its follow-up', async () => {
    const fallback = { interactionEventId: 23, pageNumber: 1, eventType: 'ANSWER', inputText: '雨伞',
      replyText: '我们一起看看画面。' }
    const fetch = vi.fn(async (_path, { method }) => {
      if (method === 'GET') {
        return Response.json({ data: { session: { ...parent, revision: 3 }, events: [question, fallback], report: null } })
      }
      const writes = fetch.mock.calls.filter(([, options]) => options.method === 'POST').length
      if (writes === 1) {
        return Response.json({ error: { code: 'DEPENDENCY_UNAVAILABLE',
          message: '回答已保存，请核对会话进度', retryable: true } }, { status: 503 })
      }
      return Response.json({ data: receipt })
    })
    vi.stubGlobal('fetch', fetch)
    const live = new ReadingSessionsApi()
    const api = await openReader({ snapshot: parent, events: [question],
      sessionApi: { get: live.get.bind(live), addEvent: live.addEvent.bind(live) } })

    await clickButton('孩子回答')
    await clickButton('改用文字输入')
    await wrapper.get('textarea').setValue('雨伞')
    await clickButton('确认回答')

    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('上次操作的结果仍待核对。')
    expect(wrapper.findAll('button').map(button => button.text())).not.toContain('孩子回答')
    expect(fetch.mock.calls.map(([, { method }]) => method)).toEqual(['POST', 'GET'])

    await clickButton('重新核对进度')

    expect(fetch.mock.calls.map(([, { method }]) => method)).toEqual(['POST', 'GET', 'GET', 'POST'])
    expect(JSON.parse(fetch.mock.calls[3][1].body)).toEqual(JSON.parse(fetch.mock.calls[0][1].body))
    expectFollowup()
    expect(api.submitGuidance).not.toHaveBeenCalled()
    await clickButton('孩子回答')
    await clickButton('改用文字输入')
    expect(wrapper.get('textarea').element.value).toBe('')
  })

  it('does not render a prior visit follow-up when a branch revisits the same page', async () => {
    const newPrompt = { ...initialQuestion, outputText: '回到这里，你现在看到了什么？', interactionEventId: 28, revision: 6 }
    const api = await openReader({
      snapshot: { ...parent, revision: 5, visitedPageNumbers: [1, 2, 1] },
      events: [question, answer,
        { eventType: 'PAGE_CHANGED', pageNumber: 2, interactionEventId: 24 },
        { eventType: 'BRANCH_CHOSEN', pageNumber: 2, interactionEventId: 27 },
      ],
      sessionApi: { submitGuidance: vi.fn().mockResolvedValue({ data: newPrompt }) },
    })
    expect(wrapper.get('.agent-text').text()).toBe(newPrompt.outputText)
    expect(wrapper.find('.guardian-answer-reply').exists()).toBe(false)
    expect(wrapper.text()).not.toContain(followupQuestion)
    expect(api.submitGuidance).toHaveBeenCalledTimes(1)
  })
})

describe('guardian short-answer acknowledgement', () => {
  const parent = { ...questionedPageOne, mode: 'PARENT_CHILD' }
  const question = { ...savedQuestion(1, 2), outputText: '你能指一指画面里的角色吗？' }
  const text = '猫猫。'
  const receipt = { interactionEventId: 23, revision: 3, replyText: '你说的是猫猫呀。我们找找画面里有没有。' }
  const answer = { ...receipt, eventType: 'ANSWER', pageNumber: 1, inputText: text }

  async function submitTypedShortAnswer() {
    await clickButton('孩子回答')
    await clickButton('改用文字输入')
    await wrapper.get('textarea').setValue(text)
    await clickButton('确认回答')
  }

  function expectAcknowledgement() {
    const card = wrapper.get('section[aria-labelledby="interaction-title"]')
    expect(card.get('.agent-text').text()).toBe(question.outputText)
    expect(card.get('.answer-confirmation:not(.guardian-answer-reply)').text()).toBe('家长确认文本：' + text)
    expect(card.get('[role="status"] .guardian-answer-reply').text()).toBe('芽芽：' + receipt.replyText)
    expect(wrapper.findAll('.guardian-answer-reply')).toHaveLength(1)
    expect(card.text()).not.toContain('芽芽追问')
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
  }

  it('shows the server short reply once and restores it via a fresh progress read', async () => {
    const api = await openReader({ snapshot: parent, events: [question],
      sessionApi: { addEvent: vi.fn().mockResolvedValue({ data: receipt }) } })
    await submitTypedShortAnswer()
    expect(api.addEvent).toHaveBeenCalledExactlyOnceWith(42, expect.objectContaining({
      eventType: 'ANSWER', inputText: text, pageNumber: 1, expectedRevision: 2,
    }))
    expectAcknowledgement()

    wrapper.unmount()
    const restored = await openReader({ snapshot: null, initialSessionId: 42, sessionApi: {
      get: vi.fn().mockResolvedValue({ data: {
        session: { ...parent, revision: 3 }, events: [question, answer], report: null,
      } }),
    } })
    expectAcknowledgement()
    expect(restored.get).toHaveBeenCalledExactlyOnceWith(42, expect.any(Object))
    expect(restored.addEvent).not.toHaveBeenCalled()
    expect(restored.submitGuidance).not.toHaveBeenCalled()
  })

  it.each([
    [2, [1, 2], []],
    [1, [1, 2, 1], [{ interactionEventId: 25, eventType: 'BRANCH_CHOSEN', pageNumber: 2 }]],
  ])('keeps a late acknowledgement off the new visit to page %i', async (pageNumber, visitedPageNumbers, branchEvents) => {
    let resolveAnswer
    const pending = new Promise(resolve => { resolveAnswer = resolve })
    const api = await openReader({ snapshot: parent, events: [question],
      sessionApi: { addEvent: vi.fn().mockReturnValue(pending) } })
    await submitTypedShortAnswer()
    expect(wrapper.get('.answer-dialog [role="status"]').text()).toBe('芽芽正在想怎么回应…')
    expect(wrapper.get('.page-navigation .button-primary').element.disabled).toBe(true)

    // The original reader can be left or refreshed while its receipt is in flight.
    wrapper.unmount()
    const nextQuestion = {
      ...savedQuestion(pageNumber, 6), interactionEventId: 26, outputText: '你现在看到了什么？',
    }
    const restored = await openReader({
      snapshot: { ...parent, currentPage: pageNumber, visitedPageNumbers, revision: 6 },
      events: [question, answer, { interactionEventId: 24, eventType: 'PAGE_CHANGED', pageNumber: 2 },
        ...branchEvents, nextQuestion],
    })
    expect(wrapper.get('.agent-text').text()).toBe(nextQuestion.outputText)
    expect(wrapper.find('.guardian-answer-reply').exists()).toBe(false)
    resolveAnswer({ data: receipt })
    await flushPromises()
    expect(wrapper.get('.agent-text').text()).toBe(nextQuestion.outputText)
    expect(wrapper.find('.guardian-answer-reply').exists()).toBe(false)
    expect(wrapper.text()).not.toContain(text)
    expect(api.addEvent).toHaveBeenCalledTimes(1)
    expect(restored.addEvent).not.toHaveBeenCalled()
  })

  it('shows the reply only after the guardian confirms a voice-transcribed short answer', async () => {
    const track = { stop: vi.fn() }
    vi.stubGlobal('navigator', { mediaDevices: {
      getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [track] }),
    } })
    vi.stubGlobal('URL', class extends URL {
      static createObjectURL = vi.fn(() => 'blob:short-answer')
      static revokeObjectURL = vi.fn()
    })
    vi.stubGlobal('MediaRecorder', class extends EventTarget {
      state = 'inactive'
      mimeType = 'audio/webm'
      start() { this.state = 'recording' }
      stop() {
        this.state = 'inactive'
        this.dispatchEvent(new MessageEvent('dataavailable', { data: new Blob(['sample'], { type: this.mimeType }) }))
        this.dispatchEvent(new Event('stop'))
      }
    })
    const transcribe = vi.fn().mockResolvedValue({ data: { text, reliable: false } })
    const api = await openReader({ snapshot: parent, events: [question], voiceApi: { transcribe },
      sessionApi: { addEvent: vi.fn().mockResolvedValue({ data: receipt }) } })
    await clickButton('孩子回答')
    await clickButton('开始录音')
    await wrapper.get('.record-button').trigger('click')
    await clickButton('提交转写')
    expect(transcribe).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({
      file: expect.any(Blob), readingSessionId: 42, pageNumber: 1, revision: 2,
    }))
    expect(track.stop).toHaveBeenCalledOnce()
    expect(wrapper.get('textarea').element.value).toBe(text)
    expect(wrapper.get('.warning-note').text()).toContain('请家长仔细核对')
    expect(api.addEvent).not.toHaveBeenCalled()
    expect(wrapper.find('.guardian-answer-reply').exists()).toBe(false)

    await clickButton('确认回答')
    expect(api.addEvent).toHaveBeenCalledExactlyOnceWith(42, expect.objectContaining({
      eventType: 'ANSWER', inputText: text, pageNumber: 1, expectedRevision: 2,
    }))
    expectAcknowledgement()
  })
})
