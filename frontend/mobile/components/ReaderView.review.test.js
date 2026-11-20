import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/client'
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

async function openReader({ snapshot = inProgress, sessionApi = {}, voiceApi = {} } = {}) {
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
      initialSnapshot: snapshot ? { session: snapshot, events: [], report: null } : null,
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
    expect(synthesizeInteraction).toHaveBeenCalledExactlyOnceWith({ interactionEventId: 22 })
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
})
