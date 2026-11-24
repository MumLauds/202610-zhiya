import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/client'
import PageAudioPlayer from './PageAudioPlayer.vue'
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
  mode: 'PARENT_CHILD',
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
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

async function openReader(voiceApi, { mode = session.mode, sessionApi = {}, expiresAt = null, events = [question] } = {}) {
  wrapper = mount(ReaderView, {
    props: {
      book,
      mode: 'live',
      storyVersionId: book.storyVersionId,
      initialSnapshot: { session: { ...session, mode, expiresAt }, events, report: null },
      sessionApi,
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

    removeAttribute(name) { if (name === 'src') this.src = '' }
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

    stop() {
      this.state = 'inactive'
      const data = new Event('dataavailable')
      data.data = new Blob(['audio'], { type: 'audio/webm' })
      this.dispatchEvent(data)
      this.dispatchEvent(new Event('stop'))
    }
  })
  return { permission, getUserMedia, start }
}

async function preparePage(synthesizeInteraction = vi.fn().mockResolvedValue({ data: { audioUrl: '/answer.mp3' } })) {
  const synthesizePage = vi.fn().mockResolvedValue({ data: { audioUrl: '/page.mp3' } })
  await openReader({ synthesizePage, synthesizeInteraction })
  expect(synthesizePage).toHaveBeenCalledExactlyOnceWith({
    readingSessionId: 42, pageNumber: 1, revision: 2, signal: expect.any(AbortSignal),
  })
  expect(wrapper.getComponent(PageAudioPlayer).props('src')).toBe('/page.mp3')
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
    const { media, play } = stubAudio()
    await preparePage()
    expect(playingSources(media)).toEqual([])
    expect(play).not.toHaveBeenCalled()

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

it('reads aloud a child’s explicit story answer only when the child taps play', async () => {
  const { media, play } = stubAudio()
  const submitQuestion = vi.fn().mockResolvedValue({ data: {
    status: 'GENERATED', strategyType: 'CONCRETE_RECALL', interactionEventId: 71,
    outputText: '小兔子找到了纸船。', revision: 3,
  } })
  const synthesizePage = vi.fn().mockResolvedValue({ data: { audioUrl: '/page.mp3' } })
  const synthesizeInteraction = vi.fn().mockResolvedValue({ data: { audioUrl: '/story-answer.mp3' } })
  await openReader({ synthesizePage, synthesizeInteraction }, { mode: 'CHILD_SOLO', sessionApi: { submitQuestion } })

  await clickButton('这一页发生了什么？')
  expect(submitQuestion).toHaveBeenCalledExactlyOnceWith(42, {
    pageNumber: 1, inputText: '这一页发生了什么？', expectedRevision: 2, clientEventId: expect.any(String),
  }, { signal: undefined })
  expect(wrapper.text()).toContain('小兔子找到了纸船。')
  expect(synthesizeInteraction).not.toHaveBeenCalled()
  expect(play).not.toHaveBeenCalled()

  await playPage()
  await clickButton('播放芽芽的回答')
  expect(synthesizeInteraction).toHaveBeenCalledExactlyOnceWith({
    interactionEventId: 71, signal: expect.any(AbortSignal),
  })
  expect(playingSources(media)).toEqual(['/story-answer.mp3'])
})

describe('child reading authorization deadline', () => {
  const savedAnswer = { ...question, eventType: 'ASK', interactionEventId: 71 }

  it.each(['page', 'answer'])('stops and unloads %s audio at expiry', async (source) => {
    const { media } = stubAudio()
    vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] })
    const expiresAt = new Date(Date.now() + 5000).toISOString()
    const synthesizePage = vi.fn().mockResolvedValue({ data: { audioUrl: '/page.mp3' } })
    const synthesizeInteraction = vi.fn().mockResolvedValue({ data: { audioUrl: '/story-answer.mp3' } })
    await openReader({ synthesizePage, synthesizeInteraction }, { mode: 'CHILD_SOLO', expiresAt, events: [question, savedAnswer] })

    if (source === 'page') await playPage()
    else await clickButton('播放芽芽的回答')
    expect(playingSources(media)).toHaveLength(1)

    await vi.advanceTimersByTimeAsync(5000)
    expect(playingSources(media)).toEqual([])
    expect(media.every(element => !element.src)).toBe(true)
    expect(wrapper.get('[role="alert"]').text()).toContain('儿童阅读授权已到期')
  })

  it('checks the deadline when tapping preloaded narration before the timer fires', async () => {
    const { media, play } = stubAudio()
    vi.useFakeTimers({ toFake: ['Date', 'setTimeout', 'clearTimeout'] })
    const deadline = Date.now() + 5000
    const synthesizePage = vi.fn().mockResolvedValue({ data: { audioUrl: '/page.mp3' } })
    await openReader({ synthesizePage }, { mode: 'CHILD_SOLO', expiresAt: new Date(deadline).toISOString() })
    expect(media[0].src).toBe('/page.mp3')

    vi.setSystemTime(deadline + 1)
    await wrapper.get('.audio-play').trigger('click')
    expect(play).not.toHaveBeenCalled()
    expect(media[0].src).toBe('')
    expect(wrapper.get('[role="alert"]').text()).toContain('儿童阅读授权已到期')
  })
})

describe('Reader voice request lock recovery', () => {
  it('aborts pending page synthesis on opening the answer dialog and ignores its late result', async () => {
    const synthesis = deferred()
    const synthesizePage = vi.fn().mockReturnValue(synthesis.promise)
    await openReader({ synthesizePage })
    expect(synthesizePage).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({
      readingSessionId: 42,
      pageNumber: 1,
      revision: 2,
      signal: expect.any(AbortSignal),
    }))
    const [{ signal }] = synthesizePage.mock.calls[0]
    expect(signal.aborted).toBe(false)

    await clickButton('孩子回答')
    expect(wrapper.find('[role="dialog"]').exists()).toBe(true)
    expect(signal.aborted).toBe(true)

    synthesis.resolve({ data: { audioUrl: '/late.mp3' } })
    await flushPromises()
    expect(wrapper.getComponent(PageAudioPlayer).props('src')).toBe('')
    await wrapper.get('[role="dialog"] [aria-label="关闭"]').trigger('click')
    expect(getButton('准备本页朗读音频')).toBeDefined()
    expect(synthesizePage).toHaveBeenCalledOnce()
  })

  it('retries the same recording after a voice lock conflict', async () => {
    const { permission, getUserMedia } = stubRecorder()
    permission.resolve({ getTracks: () => [] })
    vi.stubGlobal('URL', class extends URL {
      static createObjectURL() { return 'blob:voice-test' }
      static revokeObjectURL() {}
    })
    const conflict = new ApiError('该会话正在处理语音，请稍后再试', {
      code: 'VOICE_REQUEST_IN_PROGRESS',
      status: 409,
    })
    const transcribe = vi.fn()
      .mockRejectedValueOnce(conflict)
      .mockResolvedValueOnce({ data: { text: '小兔子', reliable: true } })
    await openReader({
      transcribe, synthesizePage: vi.fn().mockRejectedValue(new Error('暂时无法准备朗读')),
    })

    await clickButton('孩子回答')
    await clickButton('开始录音')
    await wrapper.get('.record-button.is-recording').trigger('click')
    await flushPromises()
    await clickButton('提交转写')
    expect(wrapper.get('[role="dialog"]').text()).toContain('请稍后再次提交这段录音')
    expect(transcribe).toHaveBeenCalledOnce()
    const [firstRequest] = transcribe.mock.calls[0]
    expect(firstRequest.file.size).toBeGreaterThan(0)

    await clickButton('提交转写')
    expect(getUserMedia).toHaveBeenCalledOnce()
    expect(transcribe).toHaveBeenCalledTimes(2)
    const [secondRequest] = transcribe.mock.calls[1]
    expect(secondRequest.file).toBe(firstRequest.file)
    expect(secondRequest).toMatchObject({ readingSessionId: 42, pageNumber: 1, revision: 2 })
    expect(wrapper.get('[role="dialog"] textarea').element.value).toBe('小兔子')
  })
})
