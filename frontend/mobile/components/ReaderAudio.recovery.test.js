import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import PageAudioPlayer from './PageAudioPlayer.vue'
import ReaderView from './ReaderView.vue'

const book = {
  title: '共读测试',
  storyVersionId: 6,
  cover: '/cover.png',
  pages: [{ page: 1, scene: '第一页', tokens: [] }],
}
const inProgress = {
  readingSessionId: 42,
  storyVersionId: book.storyVersionId,
  status: 'IN_PROGRESS',
  mode: 'PARENT_CHILD',
  currentPage: 1,
  revision: 1,
  visitedPageNumbers: [1],
}
const question = {
  status: 'SUCCESS',
  eventType: 'QUESTION',
  pageNumber: 1,
  interactionEventId: 22,
  strategyType: 'CONTEXT_QUESTION',
  outputText: '你看到了什么？',
  revision: 2,
}

let wrapper

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

async function openReader({
  snapshot = { ...inProgress, revision: 2 },
  events = [question],
  voiceApi,
  submitGuidance = vi.fn().mockResolvedValue({ data: question }),
}) {
  wrapper = mount(ReaderView, {
    props: {
      book,
      mode: 'live',
      storyVersionId: book.storyVersionId,
      initialSnapshot: { session: snapshot, events, report: null },
      sessionApi: { submitGuidance },
      voiceApi: {
        synthesizePage: vi.fn().mockRejectedValue(new Error('暂时无法准备朗读')),
        ...voiceApi,
      },
    },
  })
  await flushPromises()
  return submitGuidance
}

function getButton(label) {
  return wrapper.findAll('button').find((button) => button.text() === label)
}

async function clickButton(label) {
  const button = getButton(label)
  expect(button, `missing button: ${label}`).toBeDefined()
  await button.trigger('click')
  await flushPromises()
}

function stubAudio() {
  const media = []
  vi.stubGlobal('Audio', class extends EventTarget {
    currentTime = 0
    duration = 20
    paused = true

    constructor() {
      super()
      media.push(this)
    }

    async play() {
      this.paused = false
      this.dispatchEvent(new Event('play'))
    }

    pause() {
      this.paused = true
      this.dispatchEvent(new Event('pause'))
    }

    removeAttribute() {}
    load() {}
  })
  return media
}

describe('Reader audio recovery', () => {
  it('retains prepared narration when guidance changes the revision on the same page', async () => {
    stubAudio()
    let resolveGuidance
    const submitGuidance = vi.fn().mockImplementation(() => new Promise((resolve) => { resolveGuidance = resolve }))
    const synthesizePage = vi.fn().mockResolvedValue({ data: { audioUrl: '/page.mp3' } })
    await openReader({ snapshot: inProgress, events: [], voiceApi: { synthesizePage }, submitGuidance })

    expect(wrapper.getComponent(PageAudioPlayer).props('src')).toBe('/page.mp3')
    expect(submitGuidance).toHaveBeenCalledExactlyOnceWith(42, expect.objectContaining({
      feedback: 'INITIAL', expectedRevision: 1,
    }), { signal: undefined })
    resolveGuidance({ data: question })
    await flushPromises()
    expect(wrapper.get('.revision-tag').text()).toBe('rev 2')
    expect(wrapper.get('.page-scene').text()).toBe('第一页')
    expect(wrapper.getComponent(PageAudioPlayer).props('src')).toBe('/page.mp3')
    expect(synthesizePage).toHaveBeenCalledExactlyOnceWith({
      readingSessionId: 42, pageNumber: 1, revision: 1, signal: expect.any(AbortSignal),
    })
  })

  it('accepts delayed narration when guidance has changed only the same page’s revision', async () => {
    const media = stubAudio()
    let resolveAudio
    const synthesizePage = vi.fn().mockImplementationOnce(() => new Promise((resolve) => {
      resolveAudio = resolve
    }))
    await openReader({ snapshot: inProgress, events: [], voiceApi: { synthesizePage } })
    expect(wrapper.get('.revision-tag').text()).toBe('rev 2')

    resolveAudio({ data: { audioUrl: '/late.mp3' } })
    await flushPromises()

    expect(wrapper.getComponent(PageAudioPlayer).props('src')).toBe('/late.mp3')
    expect(media[0].paused).toBe(true)
    expect(synthesizePage).toHaveBeenCalledTimes(1)
  })

  it('allows manual preparation at the same revision when narration expires', async () => {
    stubAudio()
    vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] })
    vi.setSystemTime(new Date('2026-11-20T00:00:00Z'))
    const synthesizePage = vi.fn()
      .mockResolvedValueOnce({ data: { audioUrl: '/expired.mp3', expiresAt: '2026-11-20T00:00:02Z' } })
      .mockResolvedValueOnce({ data: { audioUrl: '/fresh.mp3', expiresAt: '2026-11-20T00:00:04Z' } })
    await openReader({ voiceApi: { synthesizePage } })

    expect(wrapper.getComponent(PageAudioPlayer).props('src')).toBe('/expired.mp3')
    await vi.advanceTimersByTimeAsync(2000)
    expect(wrapper.getComponent(PageAudioPlayer).props('src')).toBe('')
    expect(synthesizePage).toHaveBeenCalledTimes(1)

    await clickButton('准备本页朗读音频')
    expect(synthesizePage).toHaveBeenNthCalledWith(2, {
      readingSessionId: 42, pageNumber: 1, revision: 2, signal: expect.any(AbortSignal),
    })
    expect(wrapper.getComponent(PageAudioPlayer).props('src')).toBe('/fresh.mp3')
  })

  it('offers manual preparation when narration media fails before expiry', async () => {
    const media = stubAudio()
    const synthesizePage = vi.fn()
      .mockResolvedValueOnce({ data: { audioUrl: '/expired.mp3' } })
      .mockResolvedValueOnce({ data: { audioUrl: '/fresh.mp3' } })
    await openReader({ voiceApi: { synthesizePage } })
    await wrapper.get('[aria-label="播放朗读"]').trigger('click')
    await flushPromises()
    media[0].dispatchEvent(new Event('error'))
    await flushPromises()

    expect(synthesizePage).toHaveBeenCalledTimes(1)
    await clickButton('准备本页朗读音频')
    expect(synthesizePage).toHaveBeenNthCalledWith(2, {
      readingSessionId: 42, pageNumber: 1, revision: 2, signal: expect.any(AbortSignal),
    })
    expect(wrapper.getComponent(PageAudioPlayer).props('src')).toBe('/fresh.mp3')
  })

  it('can synthesize audio for a restored question at the current revision', async () => {
    const media = stubAudio()
    const synthesizeInteraction = vi.fn().mockResolvedValue({ data: { audioUrl: '/answer.mp3' } })
    const submitGuidance = await openReader({
      snapshot: { ...inProgress, revision: 2 },
      events: [question],
      voiceApi: { synthesizeInteraction },
    })

    expect(wrapper.get('.agent-text').text()).toBe(question.outputText)
    await clickButton('播放回答音频')
    expect(synthesizeInteraction).toHaveBeenCalledExactlyOnceWith({ interactionEventId: question.interactionEventId, signal: expect.any(AbortSignal) })
    expect(media[0].src).toBe('/answer.mp3')
    expect(media[0].paused).toBe(false)
    expect(submitGuidance).not.toHaveBeenCalled()
  })

  it.each([undefined, 1])('keeps restored question text without offering audio for revision %s', async (revision) => {
    const synthesizeInteraction = vi.fn()
    await openReader({
      snapshot: { ...inProgress, revision: 2 },
      events: [{ ...question, revision }],
      voiceApi: { synthesizeInteraction },
    })

    expect(wrapper.get('.agent-text').text()).toBe(question.outputText)
    expect(getButton('播放回答音频')).toBeUndefined()
    expect(synthesizeInteraction).not.toHaveBeenCalled()
  })

  it('resumes narration from the paused position', async () => {
    const media = stubAudio()
    wrapper = mount(PageAudioPlayer, { props: { src: '/narration.mp3' } })
    await wrapper.get('[aria-label="播放朗读"]').trigger('click')
    await flushPromises()
    media[0].currentTime = 8
    media[0].dispatchEvent(new Event('timeupdate'))

    await wrapper.get('[aria-label="暂停朗读"]').trigger('click')
    expect(media[0].paused).toBe(true)
    expect(media[0].currentTime).toBe(8)

    await wrapper.get('[aria-label="播放朗读"]').trigger('click')
    await flushPromises()
    expect(media[0].paused).toBe(false)
    expect(media[0].currentTime).toBe(8)
  })
})
