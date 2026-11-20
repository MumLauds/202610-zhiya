import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ReaderView from './ReaderView.vue'

const book = {
  title: '共读测试',
  storyVersionId: 6,
  cover: '/cover.png',
  pages: [{ page: 1, scene: '第一页', tokens: [] }],
}
const session = {
  readingSessionId: 42,
  storyVersionId: book.storyVersionId,
  status: 'IN_PROGRESS',
  currentPage: 1,
  revision: 2,
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
})

async function openReader(voiceApi) {
  wrapper = mount(ReaderView, {
    props: {
      book,
      mode: 'live',
      storyVersionId: book.storyVersionId,
      initialSnapshot: { session, events: [question], report: null },
      sessionApi: {},
      voiceApi,
    },
  })
  await flushPromises()
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
  const play = vi.fn()
  vi.stubGlobal('Audio', class extends EventTarget {
    currentTime = 0
    duration = 20
    paused = true

    constructor() {
      super()
      media.push(this)
    }

    async play() {
      play(this.src)
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
  return { media, play }
}

function deferred() {
  let resolve
  const promise = new Promise((done) => { resolve = done })
  return { promise, resolve }
}

function stubRecorder() {
  const permission = deferred()
  const getUserMedia = vi.fn().mockReturnValue(permission.promise)
  const start = vi.fn()
  vi.stubGlobal('navigator', { mediaDevices: { getUserMedia } })
  vi.stubGlobal('MediaRecorder', class extends EventTarget {
    static isTypeSupported() { return true }
    state = 'inactive'

    start() {
      this.state = 'recording'
      start()
    }

    stop() { this.state = 'inactive' }
  })
  return { permission, getUserMedia, start }
}

async function preparePage(synthesizeInteraction = vi.fn().mockResolvedValue({ data: { audioUrl: '/answer.mp3' } })) {
  const synthesizePage = vi.fn().mockResolvedValue({ data: { audioUrl: '/page.mp3' } })
  await openReader({ synthesizePage, synthesizeInteraction })
  await clickButton('准备本页朗读音频')
}

async function playPage() {
  await wrapper.get('[aria-label="播放朗读"]').trigger('click')
  await flushPromises()
}

function playingSources(media) {
  return media.filter(({ paused }) => !paused).map(({ src }) => src)
}

describe('Reader audio playback and recording exclusion', () => {
  it('plays only the selected source when switching in either direction', async () => {
    const { media } = stubAudio()
    await preparePage()

    await playPage()
    expect(playingSources(media)).toEqual(['/page.mp3'])
    await clickButton('播放回答音频')
    expect(playingSources(media)).toEqual(['/answer.mp3'])
    await playPage()
    expect(playingSources(media)).toEqual(['/page.mp3'])
  })

  it.each(['page', 'answer'])('stops %s audio before microphone permission resolves', async (source) => {
    const { media } = stubAudio()
    const { permission, getUserMedia, start } = stubRecorder()
    await preparePage()
    if (source === 'page') await playPage()
    else await clickButton('播放回答音频')
    expect(playingSources(media)).toEqual([`/${source}.mp3`])

    await clickButton('孩子回答')
    await clickButton('开始录音')
    expect(getUserMedia).toHaveBeenCalledTimes(1)
    expect(start).not.toHaveBeenCalled()
    expect(playingSources(media)).toEqual([])

    permission.resolve({ getTracks: () => [] })
    await flushPromises()

    expect(start).toHaveBeenCalledTimes(1)
    expect(playingSources(media)).toEqual([])
  })

  it.each(['requesting', 'recording'])('ignores an earlier answer synthesis while %s', async (phase) => {
    const { media, play } = stubAudio()
    const { permission, getUserMedia, start } = stubRecorder()
    const synthesis = deferred()
    const synthesizeInteraction = vi.fn().mockReturnValue(synthesis.promise)
    await preparePage(synthesizeInteraction)
    await clickButton('播放回答音频')
    expect(synthesizeInteraction).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({
      interactionEventId: question.interactionEventId,
      signal: expect.any(AbortSignal),
    }))
    const [{ signal }] = synthesizeInteraction.mock.calls[0]
    expect(signal.aborted).toBe(false)

    await clickButton('孩子回答')
    await clickButton('开始录音')
    expect(getUserMedia).toHaveBeenCalledTimes(1)
    expect(signal.aborted).toBe(true)
    if (phase === 'recording') {
      permission.resolve({ getTracks: () => [] })
      await flushPromises()
      expect(start).toHaveBeenCalledTimes(1)
    } else {
      expect(start).not.toHaveBeenCalled()
    }

    synthesis.resolve({ data: { audioUrl: '/answer.mp3' } })
    await flushPromises()

    expect(playingSources(media)).toEqual([])
    expect(play).not.toHaveBeenCalled()
  })
})
