import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import flipBookScript from '../../public/vendor/openpageflip/index.iife.js?raw'
import ReaderPage from './ReaderPage.vue'
import { synthesize, transcribe } from '../api/voice'
import { readPlanProgress, recordPlanActivity } from '../data/readingPlan'

vi.mock('../api/voice', () => ({ synthesize: vi.fn(), transcribe: vi.fn() }))
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }
const book = { storyVersionId: 88, title: '绘本', pageCount: 3, pages: [{ pageNumber: 1, text: '一', defaultNextPageNumber: 3 }, { pageNumber: 3, text: '三', defaultNextPageNumber: 7 }, { pageNumber: 7, text: '七', defaultNextPageNumber: null }] }
const initialSession = { readingSessionId: 55, mode: 'PARENT_CHILD', currentPage: 1, visitedPageNumbers: [1], revision: 1, status: 'IN_PROGRESS' }
const questionEvent = { interactionEventId: 10, eventType: 'QUESTION', pageNumber: 1, outputText: '小兔在哪里？', strategyType: 'CONTEXT_QUESTION' }
let wrapper, track, getUserMedia, recorders, audios, showPage
const button = (text) => wrapper.findAll('button').find((item) => item.text().includes(text))

function render(props = {}, useRealFlipBook = false) {
  const FlipStub = defineComponent({ name: 'FlipBook', props: ['pages', 'startPage'], emits: ['page-change'], setup(_, { expose }) { expose({ showPage }); return () => null } })
  wrapper = mount(ReaderPage, {
    attachTo: useRealFlipBook ? document.body : undefined,
    props: {
      book,
      session: { ...initialSession },
      events: [questionEvent],
      authenticated: true,
      requestGuidance: vi.fn().mockResolvedValue(true),
      submitAnswer: vi.fn().mockResolvedValue(true),
      chooseBranch: vi.fn().mockResolvedValue(true),
      recordPage: vi.fn().mockResolvedValue(false),
      completeReading: vi.fn(),
      refreshReading: vi.fn(),
      ...props,
    },
    global: { stubs: { FlipBook: useRealFlipBook ? false : FlipStub } },
  })
  return wrapper
}

function displayedPageNumber() {
  const { pages, startPage } = wrapper.getComponent({ name: 'FlipBook' }).props()
  return pages[startPage]?.pageNumber
}

async function startRecording() {
  await wrapper.get('.reader-answer .mic-button').trigger('click')
  await flushPromises()
}

async function receiveUnconfirmedTranscript() {
  transcribe.mockResolvedValueOnce({ text: '小免在哪里？', status: 'RECOGNIZED', reliable: false })
  render()
  await startRecording()
  await wrapper.get('.mic-button').trigger('click')
  await flushPromises()
  return wrapper.get('.reader-answer')
}

beforeEach(() => {
  vi.resetAllMocks()
  track = { stop: vi.fn() }
  getUserMedia = vi.fn().mockResolvedValue({ getTracks: () => [track] })
  vi.stubGlobal('navigator', { mediaDevices: { getUserMedia } })
  recorders = []
  audios = []
  showPage = vi.fn()
  vi.stubGlobal('MediaRecorder', class {
    state = 'inactive'
    mimeType = 'audio/webm'
    constructor() { recorders.push(this) }
    start() { this.state = 'recording' }
    stop() {
      const data = new Blob(['voice'], { type: this.mimeType })
      this.state = 'inactive'
      this.mimeType = ''
      this.ondataavailable?.({ data })
      this.onstop?.()
    }
  })
  vi.stubGlobal('Audio', class {
    constructor(url) { this.url = url; audios.push(this) }
    pause = vi.fn()
    play = vi.fn().mockResolvedValue(undefined)
    removeAttribute = vi.fn()
    load = vi.fn()
  })
  vi.stubGlobal('speechSynthesis', { cancel: vi.fn(), speak: vi.fn() })
  synthesize.mockResolvedValue({ audioUrl: '/api/v1/media/audio-key' })
  transcribe.mockResolvedValue({ text: '识别的声音', reliable: true })
})
afterEach(() => { wrapper?.unmount(); wrapper = null; vi.unstubAllGlobals() })

it('places page turns on the book and offers one answer entry with text and recording', async () => {
  render()
  const sidebar = wrapper.get('.reader-side')
  expect(sidebar.findAll(':scope > .side-card').map(card => card.get('h2').text())).toEqual([
    expect.stringContaining('阅读模式'), expect.stringContaining('共读小提问'), expect.stringContaining('家长的共读提示'),
  ])
  expect(sidebar.find('.reader-edge').exists()).toBe(false)
  expect(wrapper.find('.reader-heading p').exists()).toBe(false)
  expect(wrapper.find('.flip-hint').exists()).toBe(false)
  expect(wrapper.get('.reader-book-area .reader-edge--previous').element.disabled).toBe(true)
  expect(wrapper.get('.reader-book-area .reader-edge--next').element.disabled).toBe(false)
  expect(sidebar.get('.reader-narration-button').text()).toContain('朗读')
  expect(sidebar.text()).not.toContain('语音识别')
  expect(sidebar.findAll('.reader-answer button[type="submit"]')).toHaveLength(1)
  expect(wrapper.get('.reader-answer textarea').element.value).toBe('')
  expect(wrapper.get('.reader-answer .mic-button').element.disabled).toBe(false)
  expect(getUserMedia).not.toHaveBeenCalled()
})

it('keeps the inline recording controls in the same SVG when switching to stop', async () => {
  render()
  const mic = wrapper.get('.reader-answer .mic-button')
  const icon = mic.get('svg').element
  await startRecording()
  expect(mic.attributes('aria-label')).toBe('停止并识别')
  expect(mic.get('svg').element).toBe(icon)
  expect(mic.get('svg rect').attributes()).toMatchObject({ x: '6', y: '6', width: '12', height: '12' })
})

it('starts page narration and stops it when switching to free reading', async () => {
  render()
  const modes = wrapper.get('.reader-mode-options').findAll('button')
  expect(modes[0].attributes('aria-pressed')).toBe('true')
  expect(modes[1].attributes('aria-pressed')).toBe('false')
  await modes[1].trigger('click')
  await flushPromises()
  expect(modes[1].attributes('aria-pressed')).toBe('true')
  expect(audios.at(-1).play).toHaveBeenCalledOnce()
  await modes[0].trigger('click')
  expect(modes[0].attributes('aria-pressed')).toBe('true')
  expect(audios.at(-1).pause).toHaveBeenCalled()
})

it('locks reading actions until the edge animation and the page write both settle', async () => {
  let onFlip
  let finishAnimation
  let finishWrite
  const bookWidget = {
    on: vi.fn((name, callback) => { if (name === 'flip') onFlip = callback }),
    flipTo: vi.fn(() => new Promise(resolve => { finishAnimation = resolve })),
    turnTo: vi.fn(),
    destroy: vi.fn(),
  }
  vi.stubGlobal('OpenPageFlip', { createBook: () => bookWidget })
  vi.stubGlobal('matchMedia', () => ({ matches: false }))
  const recordPage = vi.fn(() => new Promise(resolve => { finishWrite = resolve }))
  render({ recordPage }, true)
  await wrapper.get('.reader-edge--next').trigger('click')
  expect(bookWidget.flipTo).toHaveBeenCalledExactlyOnceWith(2)
  expect(button('结束本次阅读').element.disabled).toBe(true)
  expect(wrapper.get('.reader-answer textarea').element.disabled).toBe(true)
  expect(recordPage).not.toHaveBeenCalled()

  onFlip({ page: 2 })
  finishAnimation(true)
  await flushPromises()
  expect(recordPage).toHaveBeenCalledExactlyOnceWith(3)
  expect(button('结束本次阅读').element.disabled).toBe(true)

  finishWrite(false)
  await flushPromises()
  expect(button('结束本次阅读').element.disabled).toBe(false)
  expect(wrapper.get('.reader-edge--next').element.disabled).toBe(false)
})

it('offers continuation only for the private original when reading retired versions', async () => {
  render({ session: { ...initialSession, sourceType: 'ORIGINAL' }, book: { ...book, status: 'RETIRED' } })
  await button('续写这个故事').trigger('click')
  expect(wrapper.emitted('continue-story')).toHaveLength(1)
  await wrapper.setProps({ session: { ...initialSession, sourceType: 'LIBRARY' } })
  expect(button('续写这个故事')).toBeUndefined()
})

describe('recording lifecycle', () => {
  it.each(['audio/ogg; codecs=opus', 'audio/webm;codecs=opus'])(
    'uploads %s when the recorder clears its MIME type on stop',
    async (mimeType) => {
      render()
      await startRecording()
      recorders[0].mimeType = mimeType
      await wrapper.get('.mic-button').trigger('click')
      await flushPromises()

      expect(transcribe).toHaveBeenCalledTimes(1)
      expect(transcribe.mock.calls[0][0].type).toBe(mimeType)
    },
  )

  it('cancels without upload and stops all microphone tracks', async () => {
    render()
    const editor = wrapper.get('.reader-answer textarea')
    await editor.setValue('录音前写下的回答')
    await startRecording()
    expect(recorders[0].state).toBe('recording')
    await button('取消录音').trigger('click')
    await flushPromises()
    expect(recorders[0].state).toBe('inactive')
    expect(track.stop).toHaveBeenCalled()
    expect(transcribe).not.toHaveBeenCalled()
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(editor.element.value).toBe('录音前写下的回答')
  })

  it('stops a late microphone permission result after cancellation', async () => {
    const permission = deferred()
    getUserMedia.mockReturnValueOnce(permission.promise)
    render()
    await startRecording()
    expect(wrapper.find('.mic-button').element.disabled).toBe(true)
    await button('取消录音').trigger('click')
    permission.resolve({ getTracks: () => [track] })
    await flushPromises()
    expect(track.stop).toHaveBeenCalledTimes(1)
    expect(recorders).toHaveLength(0)
    expect(transcribe).not.toHaveBeenCalled()
  })

  it('only submits explicit stop, captures the page snapshot, and ignores a late response', async () => {
    const recognition = deferred()
    transcribe.mockReturnValueOnce(recognition.promise)
    render()
    await startRecording()
    await wrapper.find('.mic-button').trigger('click')
    expect(transcribe).toHaveBeenCalledTimes(1)
    expect(transcribe.mock.calls[0][1]).toEqual({ readingSessionId: 55, pageNumber: 1, revision: 1 })
    expect(wrapper.find('.mic-button').element.disabled).toBe(true)
    await button('取消录音').trigger('click')
    expect(transcribe.mock.calls[0][2].aborted).toBe(true)
    recognition.resolve({ text: '迟到的结果', reliable: true })
    await flushPromises()
    expect(wrapper.get('.reader-answer textarea').element.value).toBe('')
    expect(track.stop).toHaveBeenCalled()
  })

  it('does not upload if recording stops without an explicit submit', async () => {
    render()
    await startRecording()
    recorders[0].stop()
    await flushPromises()
    expect(transcribe).not.toHaveBeenCalled()
    expect(track.stop).toHaveBeenCalled()
  })

  it.each(['page', 'logout', 'unmount'])('cancels a live recording on %s', async (action) => {
    render()
    await startRecording()
    if (action === 'page') await wrapper.setProps({ session: { ...initialSession, currentPage: 3, visitedPageNumbers: [1, 3], revision: 2 } })
    if (action === 'logout') await wrapper.setProps({ authenticated: false })
    if (action === 'unmount') { wrapper.unmount(); wrapper = null }
    expect(track.stop).toHaveBeenCalled()
    expect(transcribe).not.toHaveBeenCalled()
  })
})

describe('parent transcript review', () => {
  it('shows a pending reply, blocks duplicate answers, and then shows the server reply below the answer', async () => {
    const pending = deferred()
    const submitAnswer = vi.fn(() => pending.promise)
    render({ submitAnswer })
    const form = wrapper.get('.reader-answer')
    await form.get('textarea').setValue('雨伞')
    await form.trigger('submit')
    expect(wrapper.get('.question-reply').text()).toContain('芽芽正在想怎么回应')
    expect(button('正在保存').element.disabled).toBe(true)
    await form.trigger('submit')
    expect(submitAnswer).toHaveBeenCalledExactlyOnceWith('雨伞')
    await wrapper.setProps({ events: [questionEvent, {
      eventType: 'ANSWER', pageNumber: 1, inputText: '雨伞', replyText: '我听到你的观察啦。',
    }] })
    pending.resolve(true)
    await flushPromises()
    expect(wrapper.get('.question-saved').text()).toContain('雨伞')
    expect(wrapper.get('.question-reply').text()).toBe('芽芽：我听到你的观察啦。')
  })

  it('keeps a late answer failure off the new reading page', async () => {
    const pending = deferred()
    render({ submitAnswer: vi.fn(() => pending.promise) })
    const form = wrapper.get('.reader-answer')
    await form.get('textarea').setValue('雨伞')
    await form.trigger('submit')
    expect(wrapper.get('.question-reply').text()).toContain('芽芽正在想怎么回应')
    await wrapper.setProps({ session: { ...initialSession, currentPage: 3, visitedPageNumbers: [1, 3], revision: 2 } })
    pending.resolve(false)
    await flushPromises()
    expect(wrapper.find('.question-reply').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('回答尚未确认保存')
  })

  it('does not display a late short-answer acknowledgement on the next page', async () => {
    const pending = deferred()
    const rolePrompt = { ...questionEvent, outputText: '你能指一指画面里的角色吗？' }
    const nextPrompt = { ...questionEvent, interactionEventId: 13, pageNumber: 3, outputText: '这一页的伞是什么颜色？' }
    render({ events: [rolePrompt], submitAnswer: vi.fn(() => pending.promise) })
    await wrapper.get('.reader-answer textarea').setValue('猫猫')
    await wrapper.get('.reader-answer').trigger('submit')
    expect(wrapper.get('.question-reply').text()).toContain('芽芽正在想怎么回应')

    await wrapper.setProps({
      session: { ...initialSession, currentPage: 3, visitedPageNumbers: [1, 3], revision: 4 },
      events: [rolePrompt, nextPrompt],
    })
    await wrapper.setProps({ events: [rolePrompt, {
      interactionEventId: 11, revision: 2, eventType: 'ANSWER', pageNumber: 1,
      inputText: '猫猫', replyText: '你说的是猫猫。我们一起找找图里有没有。'
    }, nextPrompt] })
    pending.resolve(true)
    await flushPromises()
    expect(wrapper.text()).toContain(nextPrompt.outputText)
    expect(wrapper.text()).not.toContain('你说的是猫猫')
    expect(wrapper.find('.question-reply').exists()).toBe(false)
  })

  it.each(['direct', 'denied', 'unsupported'])('accepts a typed answer with %s input', async (entry) => {
    const submitAnswer = vi.fn().mockResolvedValue(true)
    render({ submitAnswer })
    if (entry !== 'direct') {
      if (entry === 'denied') getUserMedia.mockRejectedValueOnce(new Error('permission denied'))
      if (entry === 'unsupported') vi.stubGlobal('MediaRecorder', undefined)
      await startRecording()
    }
    const editor = wrapper.get('.reader-answer textarea')
    expect(button('提交回答').element.disabled).toBe(true)
    await editor.setValue('  小兔回家了。 ')
    expect(getUserMedia).toHaveBeenCalledTimes(entry === 'denied' ? 1 : 0)
    expect(transcribe).not.toHaveBeenCalled()
    expect(submitAnswer).not.toHaveBeenCalled()
    expect(synthesize.mock.calls.map(([source]) => source)).toEqual([
      { readingSessionId: 55, pageNumber: 1, revision: 1, preload: true },
    ])
    await wrapper.get('.reader-answer').trigger('submit')
    await flushPromises()
    expect(submitAnswer).toHaveBeenCalledExactlyOnceWith('小兔回家了。')
    expect(editor.element.value).toBe('')
  })

  it('allows correction and sends only the text explicitly submitted by the parent', async () => {
    const form = await receiveUnconfirmedTranscript()
    const editor = form.get('textarea')
    expect(form.get('[role="status"]').text()).toContain('核对转写')
    expect(editor.element.value).toBe('小免在哪里？')
    await editor.setValue('   ')
    expect(button('提交回答').element.disabled).toBe(true)
    await editor.setValue('  小兔在哪里？  ')
    expect(wrapper.props('submitAnswer')).not.toHaveBeenCalled()
    await form.trigger('submit')
    await flushPromises()
    expect(wrapper.props('submitAnswer')).toHaveBeenCalledExactlyOnceWith('小兔在哪里？')
    expect(transcribe).toHaveBeenCalledTimes(1)
  })

  it('allows submission without editing and clears a cancelled transcription', async () => {
    const form = await receiveUnconfirmedTranscript()
    await form.trigger('submit')
    await flushPromises()
    expect(wrapper.props('submitAnswer')).toHaveBeenCalledExactlyOnceWith('小免在哪里？')
    await startRecording()
    await form.get('.mic-button').trigger('click')
    await flushPromises()
    expect(form.get('textarea').element.value).toBe('识别的声音')
    await button('清除转写').trigger('click')
    expect(form.get('textarea').element.value).toBe('')
    expect(wrapper.props('submitAnswer')).toHaveBeenCalledTimes(1)
  })

  it('replaces the draft on re-recording and uploads only after the next explicit stop', async () => {
    const form = await receiveUnconfirmedTranscript()
    await form.get('textarea').setValue('上一段草稿')
    await startRecording()
    expect(recorders[1].state).toBe('recording')
    expect(form.get('textarea').element.disabled).toBe(true)
    expect(transcribe).toHaveBeenCalledTimes(1)
    await form.get('.mic-button').trigger('click')
    await flushPromises()
    expect(transcribe).toHaveBeenCalledTimes(2)
    expect(form.get('[role="status"]').text()).toContain('核对转写')
    expect(form.get('textarea').element.value).toBe('识别的声音')
    expect(wrapper.props('submitAnswer')).not.toHaveBeenCalled()
  })

  it('discards unconfirmed edits when the reading page changes', async () => {
    const form = await receiveUnconfirmedTranscript()
    await form.get('textarea').setValue('只属于上一页的草稿')
    await wrapper.setProps({
      session: { ...initialSession, currentPage: 3, visitedPageNumbers: [1, 3], revision: 2 },
      events: [{ ...questionEvent, pageNumber: 3, interactionEventId: 11 }],
    })
    expect(form.get('textarea').element.value).toBe('')
    expect(wrapper.props('submitAnswer')).not.toHaveBeenCalled()
    expect(transcribe).toHaveBeenCalledTimes(1)
  })

  it('keeps the editable draft when permission for a replacement recording is denied', async () => {
    const form = await receiveUnconfirmedTranscript()
    await form.get('textarea').setValue('仍可确认的文字')
    getUserMedia.mockRejectedValueOnce(new Error('permission denied'))
    await startRecording()
    expect(form.get('[role="alert"]').text()).toContain('麦克风不可用')
    expect(form.get('textarea').element.value).toBe('仍可确认的文字')
    await form.trigger('submit')
    await flushPromises()
    expect(wrapper.props('submitAnswer')).toHaveBeenCalledExactlyOnceWith('仍可确认的文字')
    expect(transcribe).toHaveBeenCalledTimes(1)
  })
})

describe('parent child-mode handoff', () => {
  it('sends the current story and selected duration to the child-mode action', async () => {
    const enterChildModeAction = vi.fn().mockResolvedValue(true)
    render({
      session: { ...initialSession, status: 'CREATED', revision: 0, visitedPageNumbers: [] },
      availableBooks: [book, { storyVersionId: 99, title: '另一本' }],
      enterChildModeAction,
    })
    await button('设置并进入儿童模式').trigger('click')
    const dialog = wrapper.get('[role="dialog"]')
    expect(dialog.text()).toContain('儿童端可能是竖版 Web、横版硬件')
    await dialog.find('select').setValue('15')
    await dialog.findAll('input[type="checkbox"]')[1].setValue(true)
    await dialog.findAll('button').find((item) => item.text().includes('开启儿童模式')).trigger('click')
    await flushPromises()
    expect(enterChildModeAction).toHaveBeenCalledWith(55, [88, 99], 900)
  })

  it('blocks switching after the parent-child session has started', () => {
    render()
    expect(button('设置并进入儿童模式')).toBeUndefined()
  })

  it('keeps an already authorized CREATED session out of first-time handoff', () => {
    const enterChildModeAction = vi.fn()
    render({ session: { ...initialSession, mode: 'CHILD_SOLO', status: 'CREATED' }, enterChildModeAction })
    expect(button('设置并进入儿童模式')).toBeUndefined()
    expect(wrapper.get('.reader-heading .pill').text()).toBe('儿童自主阅读')
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(enterChildModeAction).not.toHaveBeenCalled()
  })
})

describe('narration lifecycle', () => {
  it('pauses the actual player and reuses its prepared source on the same page', async () => {
    render()
    await button('朗读').trigger('click')
    await flushPromises()
    expect(audios).toHaveLength(1)
    await button('暂停朗读').trigger('click')
    expect(audios[0].pause).toHaveBeenCalledTimes(1)
    expect(audios[0].removeAttribute).not.toHaveBeenCalled()
    await button('朗读').trigger('click')
    await flushPromises()
    expect(synthesize).toHaveBeenCalledOnce()
    expect(audios[0].play).toHaveBeenCalledTimes(2)
    expect(speechSynthesis.cancel).toHaveBeenCalled()
  })

  it('preserves active narration when a same-page question advances the revision', async () => {
    render()
    await button('朗读').trigger('click')
    await flushPromises()
    await wrapper.setProps({ session: { ...initialSession, revision: 2 }, events: [questionEvent, {
      ...questionEvent, interactionEventId: 11, outputText: '小兔看到了什么？',
    }] })
    expect(audios[0].play).toHaveBeenCalledTimes(1)
    expect(audios[0].pause).not.toHaveBeenCalled()
  })

  it('prepares and plays narration while INITIAL is still waiting for the model', async () => {
    const pending = deferred()
    const requestGuidance = vi.fn().mockReturnValue(pending.promise)
    render({ events: [], requestGuidance })
    await flushPromises()
    expect(requestGuidance).toHaveBeenCalledWith('INITIAL')
    expect(synthesize.mock.calls[0][0]).toEqual({ readingSessionId: 55, pageNumber: 1, revision: 1, preload: true })
    expect(audios[0].play).not.toHaveBeenCalled()
    await button('朗读').trigger('click')
    await flushPromises()
    expect(audios[0].play).toHaveBeenCalledOnce()
    await wrapper.setProps({ session: { ...initialSession, revision: 2 }, events: [questionEvent] })
    pending.resolve(true)
    await flushPromises()
    expect(synthesize).toHaveBeenCalledOnce()
  })

  it('keeps preparing the page but never plays after cancelling the loading state', async () => {
    const speech = deferred()
    synthesize.mockReturnValueOnce(speech.promise)
    render()
    await button('朗读').trigger('click')
    await button('取消朗读').trigger('click')
    expect(synthesize.mock.calls[0][1].aborted).toBe(false)
    speech.resolve({ audioUrl: '/late' })
    await flushPromises()
    expect(audios).toHaveLength(1)
    expect(audios[0].play).not.toHaveBeenCalled()
  })

  it.each(['page', 'logout', 'unmount'])('releases playback on %s', async (action) => {
    render()
    await button('朗读').trigger('click')
    await flushPromises()
    if (action === 'page') await wrapper.setProps({ session: { ...initialSession, currentPage: 3, visitedPageNumbers: [1, 3], revision: 2 } })
    if (action === 'logout') await wrapper.setProps({ authenticated: false })
    if (action === 'unmount') { wrapper.unmount(); wrapper = null }
    expect(audios[0].pause).toHaveBeenCalledTimes(1)
  })

  it('does not silently fall back to browser speech on a backend error', async () => {
    synthesize.mockRejectedValue(new Error('服务暂不可用'))
    render()
    await button('朗读').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('服务暂不可用')
    expect(speechSynthesis.speak).not.toHaveBeenCalled()
  })
})

describe('reader state and visual progress', () => {
  it.each([1, 7])('finishes from page %i without an extra turn or duplicate completion', async (currentPage) => {
    const pending = deferred()
    const completeReading = vi.fn().mockReturnValue(pending.promise)
    const visitedPageNumbers = currentPage === 1 ? [1] : [1, 3, 7]
    render({ session: { ...initialSession, currentPage, visitedPageNumbers }, completeReading })
    const finish = button('结束本次阅读')
    await finish.trigger('click')
    await finish.trigger('click')
    expect(completeReading).toHaveBeenCalledTimes(1)
    expect(wrapper.props('recordPage')).not.toHaveBeenCalled()
    pending.resolve(true)
    await wrapper.setProps({ session: { ...initialSession, currentPage, visitedPageNumbers, status: 'FINISHED' }, report: { status: 'READY', summary: '本次已读记录' } })
    expect(wrapper.text()).toContain('本次已读记录')
    expect(button('结束本次阅读')).toBeUndefined()
  })

  it('only enables forward navigation to previously visited pages after an early finish', async () => {
    const session = { ...initialSession, currentPage: 3, visitedPageNumbers: [1, 3] }
    render({ session })
    await button('结束本次阅读').trigger('click')
    await wrapper.setProps({ session: { ...session, status: 'FINISHED' } })
    await flushPromises()
    expect(button('下一页').element.disabled).toBe(true)

    await button('上一页').trigger('click')
    expect(showPage).toHaveBeenLastCalledWith(0)
    expect(button('下一页').element.disabled).toBe(false)
    await button('下一页').trigger('click')
    expect(showPage).toHaveBeenLastCalledWith(1)
    expect(button('下一页').element.disabled).toBe(true)
    expect(wrapper.props('recordPage')).not.toHaveBeenCalled()
  })

  it('retraces live branch visits in order without writing progress', async () => {
    // The branch skips page 3, then revisits page 1 before returning to page 7.
    const session = { ...initialSession, currentPage: 7, visitedPageNumbers: [1, 7, 1, 7] }
    render({ session })
    for (const page of [1, 7, 1]) {
      await button('上一页').trigger('click')
      expect(displayedPageNumber()).toBe(page)
      expect(button('朗读').element.disabled).toBe(true)
    }
    expect(button('上一页').element.disabled).toBe(true)
    for (const page of [7, 1, 7]) {
      await button('下一页').trigger('click')
      expect(displayedPageNumber()).toBe(page)
    }
    expect(button('朗读').element.disabled).toBe(false)
    expect(wrapper.props('recordPage')).not.toHaveBeenCalled()
  })

  it('follows the visited branch on a backward swipe and restores live controls on return', async () => {
    let flip
    vi.stubGlobal('OpenPageFlip', { createBook: () => ({
      on: (_, callback) => { flip = callback }, turnTo: vi.fn(), setPages: vi.fn(), destroy: vi.fn(),
    }) })
    render({ session: { ...initialSession, currentPage: 7, visitedPageNumbers: [1, 7] } }, true)
    flip({ page: 0 })
    await flushPromises()
    expect(displayedPageNumber()).toBe(1)
    expect(wrapper.findAll('.flip-page--text')).toHaveLength(2)
    flip({ page: 2 })
    await flushPromises()
    expect(displayedPageNumber()).toBe(7)
    expect(wrapper.findAll('.flip-page--text')).toHaveLength(2)
    expect(button('朗读').element.disabled).toBe(false)
    expect(wrapper.props('recordPage')).not.toHaveBeenCalled()
  })

  it('follows the visited branch when reviewing a finished session with buttons or gestures', async () => {
    const session = { ...initialSession, currentPage: 3, visitedPageNumbers: [1, 3] }
    const branchedBook = { ...book, pageCount: 4, pages: [
      { ...book.pages[0], defaultNextPageNumber: 2 },
      { pageNumber: 2, text: '二', defaultNextPageNumber: 3 },
      ...book.pages.slice(1),
    ] }
    let onFlip
    const widget = { on: (_, callback) => { onFlip = callback }, turnTo: vi.fn(), setPages: vi.fn(), destroy: vi.fn() }
    vi.stubGlobal('OpenPageFlip', { createBook: () => widget })
    render({ book: branchedBook, session }, true)
    await button('结束本次阅读').trigger('click')
    await wrapper.setProps({ session: { ...session, status: 'FINISHED' } })
    await button('上一页').trigger('click')
    expect(displayedPageNumber()).toBe(1)
    expect(button('下一页').element.disabled).toBe(false)
    await button('下一页').trigger('click')
    expect(displayedPageNumber()).toBe(3)
    expect(button('下一页').element.disabled).toBe(true)

    expect(widget.destroy).toHaveBeenCalledTimes(1)
    expect(wrapper.findAll('.flip-page--text').map(page => page.attributes('aria-label'))).toEqual(['第 1 页文字', '第 3 页文字'])
    expect(wrapper.find('.page-number').text()).toContain('共 4 页')
    onFlip({ page: 0 })
    await flushPromises()
    expect(displayedPageNumber()).toBe(1)
    onFlip({ page: 2 })
    await flushPromises()
    expect(displayedPageNumber()).toBe(3)
    expect(wrapper.props('recordPage')).not.toHaveBeenCalled()
  })

  it.each([{ visits: [1, 3] }, { visits: [1, 3, 1, 3] }])('returns to live progress through the same branch visits $visits', async ({ visits }) => {
    const branchedBook = { ...book, pageCount: 4, pages: [
      { ...book.pages[0], defaultNextPageNumber: 2 },
      { pageNumber: 2, text: '二', defaultNextPageNumber: 3 },
      ...book.pages.slice(1),
    ] }
    render({ book: branchedBook, session: { ...initialSession, currentPage: 3, visitedPageNumbers: visits } })

    for (let i = visits.length - 2; i >= 0; i--) {
      await button('上一页').trigger('click')
      expect(displayedPageNumber()).toBe(visits[i])
      expect(button('朗读').element.disabled).toBe(true)
    }
    expect(button('上一页').element.disabled).toBe(true)
    for (let i = 1; i < visits.length; i++) {
      await button('下一页').trigger('click')
      expect(displayedPageNumber()).toBe(visits[i])
      expect(button('朗读').element.disabled).toBe(i < visits.length - 1)
    }

    expect(wrapper.props('recordPage')).not.toHaveBeenCalled()
    await button('下一页').trigger('click')
    expect(wrapper.props('recordPage')).toHaveBeenCalledExactlyOnceWith(7)
  })

  function renderWithFlipWidget(props) {
    // Run the shipped widget with a two-page spread and immediate flips in jsdom.
    vi.stubGlobal('OpenPageFlip', new Function(flipBookScript + '\nreturn OpenPageFlip')())
    vi.stubGlobal('matchMedia', () => ({ matches: true }))
    vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
    vi.spyOn(Element.prototype, 'clientWidth', 'get').mockReturnValue(840)
    vi.spyOn(Element.prototype, 'clientHeight', 'get').mockReturnValue(560)
    render(props, true)
  }

  async function clickEdge(clientX) {
    const spread = wrapper.get('.flipbook-book')
    const position = { clientX, clientY: 280, button: 0, pointerType: 'mouse', pointerId: 1 }
    await spread.trigger('pointerdown', position)
    await spread.trigger('pointerup', position)
  }

  it('follows repeated visits by clicking the book edges, including the physical first page', async () => {
    const session = { ...initialSession, currentPage: 3, visitedPageNumbers: [1, 3, 1, 3] }
    renderWithFlipWidget({ session })
    for (const page of wrapper.findAll('.flip-page--text')) {
      const dots = page.findAll('.page-dots i').map(dot => dot.classes('active'))
      const label = page.attributes('aria-label')
      expect(dots).toEqual(book.pages.map(source => label === `第 ${source.pageNumber} 页文字`))
    }

    for (const page of [1, 3, 1]) {
      await clickEdge(5)
      expect(displayedPageNumber()).toBe(page)
      expect(button('朗读').element.disabled).toBe(true)
    }
    expect(button('上一页').element.disabled).toBe(true)
    for (const page of [3, 1, 3]) {
      await clickEdge(835)
      expect(displayedPageNumber()).toBe(page)
    }
    expect(button('朗读').element.disabled).toBe(false)
    expect(wrapper.props('recordPage')).not.toHaveBeenCalled()
    await clickEdge(835)
    expect(wrapper.props('recordPage')).toHaveBeenCalledExactlyOnceWith(7)
    await wrapper.setProps({ session: { ...session, currentPage: 7, visitedPageNumbers: [...session.visitedPageNumbers, 7] } })
    expect(displayedPageNumber()).toBe(7)
  })

  it('keeps the first visit to page 1 when finishing a repeated branch', async () => {
    const session = { ...initialSession, currentPage: 3, visitedPageNumbers: [1, 3, 1, 3] }
    render({ session })
    for (const page of [1, 3, 1]) {
      await button('上一页').trigger('click')
      expect(displayedPageNumber()).toBe(page)
    }
    expect(button('上一页').element.disabled).toBe(true)
    await button('结束本次阅读').trigger('click')
    await wrapper.setProps({ session: { ...session, status: 'FINISHED' } })
    expect(wrapper.props('completeReading')).toHaveBeenCalledOnce()
    expect(displayedPageNumber()).toBe(1)
    expect(button('上一页').element.disabled).toBe(true)
    for (const page of [3, 1, 3]) {
      await button('下一页').trigger('click')
      expect(displayedPageNumber()).toBe(page)
    }
    expect(button('下一页').element.disabled).toBe(true)
    expect(wrapper.props('recordPage')).not.toHaveBeenCalled()
  })

  it.each([12, 13, 200])('uses global visit positions as the window moves through %i finished visits', async (count) => {
    const visits = Array.from({ length: count }, (_, index) => index % 2 === 0 ? 1 : 3)
    vi.stubGlobal('OpenPageFlip', { createBook: () => ({ on: vi.fn(), turnTo: vi.fn(), setPages: vi.fn(), destroy: vi.fn() }) })
    render({ session: { ...initialSession, status: 'FINISHED', currentPage: visits.at(-1), visitedPageNumbers: visits } }, true)

    function expectWindow(firstVisit) {
      const pages = wrapper.findAll('.flip-page--text')
      expect(pages).toHaveLength(5)
      pages.forEach((page, offset) => {
        const visitIndex = firstVisit + offset
        expect(page.attributes('aria-label')).toBe(`第 ${visits[visitIndex]} 页文字`)
        const dots = page.findAll('.page-dots i')
        if (count === 12) {
          expect(dots.map(dot => dot.classes('active'))).toEqual(visits.map((_, index) => index === visitIndex))
        } else {
          expect(dots).toHaveLength(0)
          expect(page.get('.page-number').text()).toContain(`回看 ${visitIndex + 1} / ${count}`)
        }
      })
    }
    expectWindow(count - 5)
    for (let step = 0; step < 5; step++) await button('上一页').trigger('click')
    expectWindow(count - 8)
  })

  it.each([12, 16])('shows the confirmed visit position while retracing %i live visits', async (count) => {
    const visits = Array.from({ length: count }, (_, index) => index % 2 === 0 ? 1 : 3)
    renderWithFlipWidget({ session: { ...initialSession, currentPage: 3, visitedPageNumbers: visits } })

    const visibleFooter = () => wrapper.findAll('.flip-page--text').find(page => page.isVisible()).get('.page-number')
    for (const position of [count - 1, count - 2, count - 3]) {
      await button('上一页').trigger('click')
      const footer = visibleFooter()
      expect(footer.text()).toContain(`第 ${visits[position - 1]} 页`)
      if (count === 12) {
        expect(footer.findAll('.page-dots i').map(dot => dot.classes('active')))
          .toEqual(visits.map((_, index) => index + 1 === position))
      } else {
        expect(footer.text()).toContain(`回看 ${position} / ${count}`)
        expect(wrapper.text()).not.toContain(`回看 ${count + 1} / ${count}`)
      }
    }
    for (let i = 0; i < 3; i++) await button('下一页').trigger('click')
    expect(visibleFooter().findAll('.page-dots i').map(dot => dot.classes('active')))
      .toEqual(book.pages.map(page => page.pageNumber === 3))
    expect(button('朗读').element.disabled).toBe(false)
    expect(wrapper.props('recordPage')).not.toHaveBeenCalled()
  })

  it('keeps the same page indicator when a 15-page book crosses the dot threshold', async () => {
    const pages = Array.from({ length: 15 }, (_, i) => ({
      pageNumber: i + 1, text: String(i + 1), defaultNextPageNumber: i < 14 ? i + 2 : null,
    }))
    const sessionAt = (currentPage) => ({
      ...initialSession, currentPage, visitedPageNumbers: pages.slice(0, currentPage).map(page => page.pageNumber),
    })
    renderWithFlipWidget({ book: { ...book, pages, pageCount: pages.length }, session: sessionAt(11) })
    for (const currentPage of [11, 12]) {
      await wrapper.setProps({ session: sessionAt(currentPage) })
      const spread = wrapper.get(`[aria-label="第 ${currentPage} 页文字"]`)
      expect(spread.get('.page-number').text()).toContain(`第 ${currentPage} 页 · 共 15 页`)
      expect(spread.find('.page-dots').exists()).toBe(false)
    }
  })

  it('locks the dock and ignores book gestures until submitted feedback settles', async () => {
    const feedback = deferred()
    const requestGuidance = vi.fn(() => feedback.promise)
    render({ requestGuidance })
    await button('已理解').trigger('click')
    expect(requestGuidance).toHaveBeenCalledWith('UNDERSTOOD')
    expect(button('下一页').element.disabled).toBe(true)
    expect(button('结束本次阅读').element.disabled).toBe(true)
    wrapper.findComponent({ name: 'FlipBook' }).vm.$emit('page-change', { storyIndex: 1 })
    await flushPromises()
    expect(wrapper.props('recordPage')).not.toHaveBeenCalled()
    feedback.resolve(true)
    await flushPromises()
    await button('下一页').trigger('click')
    expect(wrapper.props('recordPage')).toHaveBeenCalledExactlyOnceWith(3)
  })

  it('moves a bounded window through repeated visits and writes only after returning to live progress', async () => {
    const visits = Array.from({ length: 200 }, (_, index) => index % 2 === 0 ? 1 : 3)
    visits[194] = 7
    const session = { ...initialSession, currentPage: 3, visitedPageNumbers: visits }
    renderWithFlipWidget({ session })

    const mountedPages = () => wrapper.findAll('.flip-page--text').map(page => page.attributes('aria-label'))
    function expectCurrentPage(pageNumber) {
      const visibleText = wrapper.findAll('.flip-page--text').filter(page => page.isVisible())
      expect(visibleText.map(page => page.attributes('aria-label'))).toEqual([`第 ${pageNumber} 页文字`])
    }
    expect(mountedPages()).toEqual([1, 3, 1, 3, 7].map(number => `第 ${number} 页文字`))
    for (const spread of wrapper.findAll('.flip-page--text')) {
      expect(spread.findAll('.page-dots i')).toHaveLength(book.pageCount)
    }
    // Move farther than a full window, including the repeated visits around the detour to page 7.
    for (let index = 198; index >= 190; index--) {
      await clickEdge(5)
      expectCurrentPage(visits[index])
      expect(mountedPages().length).toBeLessThanOrEqual(5)
    }
    expect(button('朗读').element.disabled).toBe(true)
    for (let index = 191; index < visits.length; index++) {
      await clickEdge(835)
      expectCurrentPage(visits[index])
    }
    expect(button('朗读').element.disabled).toBe(false)
    expect(wrapper.props('recordPage')).not.toHaveBeenCalled()
    await clickEdge(835)
    expect(wrapper.props('recordPage')).toHaveBeenCalledExactlyOnceWith(7)
    await wrapper.setProps({ session: { ...session, currentPage: 7, visitedPageNumbers: [...visits, 7] } })
    expectCurrentPage(7)
    expect(mountedPages()).toHaveLength(5)
  })

  it('keeps the position of each visit when an approved branch revisits a page', async () => {
    render({ session: { ...initialSession, status: 'FINISHED', currentPage: 1, visitedPageNumbers: [1, 7, 1] } })
    expect(button('上一页').element.disabled).toBe(false)
    await button('上一页').trigger('click')
    expect(displayedPageNumber()).toBe(7)
    await button('上一页').trigger('click')
    expect(button('上一页').element.disabled).toBe(true)
    await button('下一页').trigger('click')
    expect(displayedPageNumber()).toBe(7)
    await button('下一页').trigger('click')
    expect(displayedPageNumber()).toBe(1)
    expect(button('下一页').element.disabled).toBe(true)
    expect(wrapper.props('recordPage')).not.toHaveBeenCalled()
  })

  it('records a default next page that was visited before a branch returned to the current page', async () => {
    render({ session: { ...initialSession, visitedPageNumbers: [1, 3, 1] } })
    await button('下一页').trigger('click')
    expect(wrapper.props('recordPage')).toHaveBeenCalledExactlyOnceWith(3)
  })

  it('finishes while reviewing an earlier page without changing the saved progress', async () => {
    const completeReading = vi.fn()
    render({ session: { ...initialSession, currentPage: 3, visitedPageNumbers: [1, 3] }, completeReading })
    await button('上一页').trigger('click')
    await button('结束本次阅读').trigger('click')
    expect(completeReading).toHaveBeenCalledTimes(1)
    expect(wrapper.props('recordPage')).not.toHaveBeenCalled()
  })

  it('can finish at a branch-only first page while the next visited page remains available', async () => {
    const session = { ...initialSession, currentPage: 3, visitedPageNumbers: [1, 3] }
    const branchedBook = {
      ...book,
      pages: [{ ...book.pages[0], defaultNextPageNumber: null }, ...book.pages.slice(1)],
      branches: [3, 7].map(nextPageNumber => ({
        branchId: nextPageNumber, fromPageNumber: 1, nextPageNumber, choiceLabel: `去第${nextPageNumber}页`, reviewStatus: 'APPROVED',
      })),
    }
    render({ session, book: branchedBook })
    await button('上一页').trigger('click')
    expect(displayedPageNumber()).toBe(1)
    expect(button('下一页').element.disabled).toBe(false)
    expect(button('结束本次阅读')).toBeDefined()
    expect(button('结束本次阅读').element.disabled).toBe(false)
    await button('结束本次阅读').trigger('click')
    await wrapper.setProps({ session: { ...session, status: 'FINISHED' } })
    expect(wrapper.props('completeReading')).toHaveBeenCalledOnce()
    expect(displayedPageNumber()).toBe(1)
    expect(button('上一页').element.disabled).toBe(true)
    await button('下一页').trigger('click')
    expect(displayedPageNumber()).toBe(3)
    expect(wrapper.props('recordPage')).not.toHaveBeenCalled()
  })

  it.each([
    ['early', 1, [1], 0],
    ['approved choice still available', 3, [1, 3], 0],
    ['shorter approved path', 7, [1, 7], 1],
  ])('only counts a finished book at the end of the visited path (%s)', async (_, currentPage, visitedPageNumbers, count) => {
    const session = { ...initialSession, childProfileId: 2, currentPage, visitedPageNumbers }
    const branchBook = {
      ...book,
      pages: book.pages.map(page => page.pageNumber === 3 ? { ...page, defaultNextPageNumber: null } : page),
      branches: [
        { branchId: 1, fromPageNumber: 1, nextPageNumber: 7, reviewStatus: 'APPROVED' },
        { branchId: 2, fromPageNumber: 3, nextPageNumber: 7, reviewStatus: 'APPROVED' },
      ],
    }
    const now = new Date(2026, 10, 18, 12)
    const clock = vi.spyOn(Date, 'now').mockReturnValue(now.getTime())
    const key = '8.2'
    const storageKey = 'zhiya.readingActivity.8.2'
    localStorage.removeItem(storageKey)
    recordPlanActivity(key, now.getTime(), 90_000)
    const completeReading = vi.fn(async () => {
      await wrapper.setProps({ session: { ...session, status: 'FINISHED', revision: 2 } })
      return true
    })
    try {
      render({ book: branchBook, session, accountId: 8, completeReading })
      await flushPromises()
      expect(readPlanProgress(key, now).books).toBe(0)
      await button(count ? '完成阅读并生成小结' : '结束本次阅读').trigger('click')
      await flushPromises()
      expect(completeReading).toHaveBeenCalledOnce()
      expect(readPlanProgress(key, now)).toMatchObject({ minutes: 1, books: count })
    } finally {
      wrapper?.unmount()
      wrapper = null
      localStorage.removeItem(storageKey)
      clock.mockRestore()
    }
  })

  it('counts a visible minute only once across two reader windows', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date(2026, 10, 18, 12))
    vi.spyOn(document, 'hidden', 'get').mockReturnValue(false)
    const key = '8.2'
    const storageKey = `zhiya.readingActivity.${key}`
    localStorage.removeItem(storageKey)
    const props = { session: { ...initialSession, childProfileId: 2 }, accountId: 8 }
    const first = render(props)
    try {
      vi.advanceTimersByTime(15_000)
      render(props)
      vi.advanceTimersByTime(60_000)
      expect(readPlanProgress(key).minutes).toBe(1)
    } finally {
      first.unmount()
      wrapper?.unmount()
      wrapper = null
      localStorage.removeItem(storageKey)
      vi.useRealTimers()
    }
  })

  it('opens a confirmation on the final page and shows the summary in the same dialog', async () => {
    const session = { ...initialSession, currentPage: 7, visitedPageNumbers: [1, 3, 7] }
    const summary = '已保存本次阅读记录'
    render({ session })
    await flushPromises()
    expect(wrapper.get('[role="dialog"]').text()).toContain('读到最后一页啦')
    expect(wrapper.props('completeReading')).not.toHaveBeenCalled()
    const dialog = wrapper.get('[role="dialog"]').element
    await button('完成阅读并生成小结').trigger('click')
    expect(wrapper.props('completeReading')).toHaveBeenCalledOnce()
    await wrapper.setProps({ session: { ...session, status: 'FINISHED' }, report: { status: 'PENDING', summary } })
    const card = wrapper.get('.reader-summary[role="dialog"]')
    expect(card.element).toBe(dialog)
    expect(card.get('h2').text()).toBe('本次共读小结')
    expect(card.text()).toContain(summary)
    await wrapper.setProps({ report: { status: 'READY', summary } })
    expect(card.text()).toContain('小结已生成')
  })

  it('offers to refresh a finished session with a missing report', async () => {
    render({ session: { ...initialSession, status: 'FINISHED' } })
    expect(wrapper.get('.reader-summary [role="status"]').text()).toContain('小结暂未加载')
    await button('刷新小结').trigger('click')
    expect(wrapper.props('refreshReading')).toHaveBeenCalledOnce()
    expect(wrapper.props('completeReading')).not.toHaveBeenCalled()
  })

  it.each(['READY', 'FAILED'])('shows the pending facts and refreshes a %s report in place', async (status) => {
    const refreshReading = vi.fn()
    const completeReading = vi.fn()
    render({
      session: { ...initialSession, status: 'FINISHED' },
      report: { status: 'PENDING', summary: '事实摘要' }, refreshReading, completeReading,
    })
    expect(wrapper.text()).toContain('小结正在生成')
    expect(wrapper.text()).toContain('事实摘要')
    await button('刷新小结').trigger('click')
    expect(refreshReading).toHaveBeenCalledTimes(1)
    expect(completeReading).not.toHaveBeenCalled()
    const summary = status === 'READY' ? '最终小结' : '事实摘要'
    await wrapper.setProps({ report: { status, summary } })
    expect(wrapper.text()).toContain(status === 'READY' ? '小结已生成' : '小结生成失败')
    expect(wrapper.text()).toContain(summary)
    expect(button('刷新小结')).toBeUndefined()
  })

  it('keeps the displayed page during a report-only refresh', async () => {
    render({
      session: { ...initialSession, status: 'FINISHED', currentPage: 3, visitedPageNumbers: [1, 3] },
      report: { status: 'PENDING', summary: '事实摘要' },
    })
    await button('上一页').trigger('click')
    expect(showPage).toHaveBeenLastCalledWith(0)
    await button('刷新小结').trigger('click')
    await flushPromises()
    expect(showPage).toHaveBeenLastCalledWith(0)
  })

  it('shows the activity, materials and ordered steps only when the summary is ready', async () => {
    const activity = { name: '画出故事中的一幕', materials: ['纸', '彩笔'], steps: ['翻开第 3 页。', '画一画喜欢的角色。'] }
    const report = { summary: '本次共读阅读了第 1、3 页。', activities: [activity] }
    render({ session: { ...initialSession, status: 'FINISHED' }, report: { ...report, status: 'PENDING' } })
    expect(wrapper.text()).not.toContain(activity.name)

    await wrapper.setProps({ report: { ...report, status: 'READY' } })
    const section = wrapper.get('.report-activity')
    expect(section.get('h3').text()).toContain(activity.name)
    expect(section.text()).toContain('纸、彩笔')
    expect(section.findAll('ol li').map(step => step.text())).toEqual(activity.steps)
    expect(wrapper.text()).toContain(report.summary)

    await wrapper.setProps({ report: { ...report, status: 'FAILED' } })
    expect(wrapper.find('.report-activity').exists()).toBe(false)
    expect(wrapper.text()).toContain(report.summary)
  })

  it.each([{}, { activities: [] }, { activities: [{ name: '旧活动' }] }])('keeps a ready summary without structured activities readable', (legacy) => {
    render({ session: { ...initialSession, status: 'FINISHED' }, report: { status: 'READY', summary: '已保存的小结', ...legacy } })
    expect(wrapper.text()).toContain('已保存的小结')
    expect(wrapper.find('.report-activity').exists()).toBe(false)
  })

  it('waits for a confirmed page and rejects a second gesture during the write', async () => {
    const pending = deferred()
    const record = vi.fn().mockReturnValue(pending.promise)
    render({ recordPage: record })
    await button('下一页').trigger('click')
    expect(button('结束本次阅读').element.disabled).toBe(true)
    wrapper.findComponent({ name: 'FlipBook' }).vm.$emit('page-change', { storyIndex: 2 })
    expect(record).toHaveBeenCalledTimes(1)
    expect(record).toHaveBeenCalledWith(3)
    expect(showPage).toHaveBeenLastCalledWith(0)
    await wrapper.setProps({ session: { ...initialSession, currentPage: 3, revision: 2, visitedPageNumbers: [1, 3] } })
    pending.resolve(true)
    await flushPromises()
    expect(showPage).toHaveBeenLastCalledWith(1)
    expect(wrapper.findComponent({ name: 'FlipBook' }).props('startPage')).toBe(1)
  })

  it('keeps the visual turn during confirmation and restores the widget on rejection', async () => {
    const pending = deferred()
    render({ recordPage: vi.fn().mockReturnValue(pending.promise) })
    const editor = wrapper.get('.reader-answer textarea')
    await editor.setValue('还没提交的回答')
    showPage.mockClear()
    await wrapper.get('.reader-edge--next').trigger('click')
    expect(showPage).not.toHaveBeenCalled()
    pending.resolve(false)
    await flushPromises()
    expect(showPage).toHaveBeenLastCalledWith(0)
    expect(wrapper.findComponent({ name: 'FlipBook' }).props('startPage')).toBe(0)
    expect(editor.element.value).toBe('还没提交的回答')
  })

  it.each(['IN_PROGRESS', 'FINISHED', 'INTERRUPTED'])('keeps the revisit hint consistent with media controls in %s sessions', async (status) => {
    const record = vi.fn()
    render({
      session: { ...initialSession, status, currentPage: 3, visitedPageNumbers: [1, 3], revision: 2 },
      events: [{ ...questionEvent, pageNumber: 3 }],
      recordPage: record,
    })
    await button('上一页').trigger('click')
    expect(wrapper.text()).toContain('回看已读页')
    expect(wrapper.text().includes('返回当前进度后可继续朗读与录音')).toBe(status === 'IN_PROGRESS')
    expect(button('朗读').element.disabled).toBe(true)
    expect(wrapper.get('.reader-answer textarea').element.disabled).toBe(true)
    expect(record).not.toHaveBeenCalled()
    await button('下一页').trigger('click')
    expect(showPage).toHaveBeenLastCalledWith(1)
    expect(record).not.toHaveBeenCalled()
    expect(button('朗读').element.disabled).toBe(status !== 'IN_PROGRESS')
    expect(wrapper.get('.reader-answer textarea').element.disabled).toBe(status !== 'IN_PROGRESS')
  })

  it('locks every reading interaction while progress is pending', () => {
    render({ syncRequired: true })
    expect(button('已理解').element.disabled).toBe(true)
    expect(button('还没懂').element.disabled).toBe(true)
    expect(button('跳过本页').element.disabled).toBe(true)
    expect(wrapper.get('.reader-answer textarea').element.disabled).toBe(true)
    expect(button('下一页').element.disabled).toBe(true)
    expect(button('结束本次阅读').element.disabled).toBe(true)
    expect(button('朗读').element.disabled).toBe(true)
    expect(wrapper.text()).toContain('进度待确认')
    expect(button('确认并同步进度')).toBeDefined()
  })
})

describe('guided reading interactions', () => {
  it('preserves the child answer when cancelling a separate story question', async () => {
    render({ askQuestion: vi.fn() })
    const editor = wrapper.get('.reader-answer textarea')
    await editor.setValue('这是孩子的回答。')
    await button('问问芽芽').trigger('click')
    const dialog = wrapper.get('[role="dialog"]')
    expect(dialog.get('textarea').element.value).toBe('')
    await dialog.get('textarea').setValue('这是给芽芽的问题。')
    await dialog.get('.modal-close').trigger('click')
    expect(editor.element.value).toBe('这是孩子的回答。')
    expect(wrapper.props('submitAnswer')).not.toHaveBeenCalled()
    expect(wrapper.props('askQuestion')).not.toHaveBeenCalled()
  })

  it('plays the saved Agent reply and detaches the avatar from audio after playback', async () => {
    const answer = { eventType: 'ANSWER', pageNumber: 1, interactionEventId: 44, revision: 2,
      replyText: '一起听一听雨声。', agentReceipt: { version: '2.0', action: 'RESPOND' } }
    render({ session: { ...initialSession, revision: 2 }, events: [questionEvent, answer] })
    await flushPromises()
    synthesize.mockResolvedValueOnce({ audioUrl: '/api/v1/media/reply' })
    await button('播放芽芽的回应').trigger('click')
    await flushPromises()
    expect(synthesize).toHaveBeenCalledWith({ interactionEventId: 44, readReply: true }, expect.any(AbortSignal))
    const avatar = wrapper.getComponent({ name: 'YayaAvatar' })
    expect(avatar.props('media')).toBe(audios.at(-1))
    audios.at(-1).onended()
    await flushPromises()
    expect(avatar.props('media')).toBeNull()
    await wrapper.setProps({ session: { ...initialSession, revision: 3 } })
    expect(button('播放芽芽的回应')).toBeUndefined()
  })

  it('submits a guardian-confirmed question separately and plays only its saved event', async () => {
    const askQuestion = vi.fn().mockResolvedValue(true)
    const submitAnswer = vi.fn()
    render({ askQuestion, submitAnswer })
    const editor = wrapper.get('.reader-answer textarea')
    await editor.setValue('本页回答草稿。')
    await button('问问芽芽').trigger('click')
    await wrapper.get('[role="dialog"] textarea').setValue(' 谁拿着伞？ ')
    await button('确认文字').trigger('click')
    expect(askQuestion).not.toHaveBeenCalled()
    await button('提交问题').trigger('click')
    await flushPromises()
    expect(askQuestion).toHaveBeenCalledWith('谁拿着伞？')
    expect(submitAnswer).not.toHaveBeenCalled()
    const answer = { eventType: 'ASK', pageNumber: 1, outputText: '小狐狸举着伞。', interactionEventId: 19, revision: 2 }
    await wrapper.setProps({ session: { ...initialSession, revision: 2 }, events: [questionEvent, answer] })
    expect(editor.element.value).toBe('本页回答草稿。')
    synthesize.mockResolvedValueOnce({ audioUrl: '/api/v1/media/audio-test' })
    await button('播放故事回答').trigger('click')
    await flushPromises()
    expect(synthesize).toHaveBeenCalledWith({ interactionEventId: 19 }, expect.any(AbortSignal))
    expect(audios.at(-1).play).toHaveBeenCalledOnce()
  })

  it('requests an initial question once when a confirmed page has no recoverable prompt', async () => {
    const requestGuidance = vi.fn().mockResolvedValue(true)
    render({ events: [], requestGuidance })
    await flushPromises()
    expect(requestGuidance).toHaveBeenCalledTimes(1)
    expect(requestGuidance).toHaveBeenCalledWith('INITIAL')
    await wrapper.setProps({
      session: { ...initialSession, revision: 2 },
      events: [{ interactionEventId: 11, eventType: 'QUESTION', pageNumber: 1, outputText: '这一页发生了什么？', strategyType: 'CONTEXT_QUESTION' }],
    })
    await flushPromises()
    expect(requestGuidance).toHaveBeenCalledTimes(1)
  })

  it('keeps page turning and skipping available while the automatic prompt is pending', async () => {
    const pending = deferred()
    const requestGuidance = vi.fn().mockReturnValueOnce(pending.promise).mockResolvedValue(true)
    render({ events: [], requestGuidance })
    await flushPromises()
    expect(wrapper.get('.reader-answer textarea').element.disabled).toBe(true)
    expect(button('跳过本页').element.disabled).toBe(false)
    expect(button('下一页').element.disabled).toBe(false)
    await button('跳过本页').trigger('click')
    expect(requestGuidance).toHaveBeenCalledWith('SKIPPED')
    pending.resolve(false)
  })

  it('restores a saved question and submits understanding feedback for a new prompt', async () => {
    const requestGuidance = vi.fn().mockResolvedValue(true)
    render({ requestGuidance })
    expect(wrapper.text()).toContain('小兔在哪里？')
    const editor = wrapper.get('.reader-answer textarea')
    await editor.setValue('这是对原问题的回答。')
    await button('还没懂').trigger('click')
    await flushPromises()
    expect(requestGuidance).toHaveBeenCalledWith('NOT_UNDERSTOOD')
    await wrapper.setProps({
      session: { ...initialSession, revision: 2 },
      events: [questionEvent, { ...questionEvent, interactionEventId: 11, outputText: '你看到哪棵树？' }],
    })
    expect(editor.element.value).toBe('')
  })

  it('shows the latest safety response and leaves skip and navigation available', async () => {
    const requestGuidance = vi.fn().mockResolvedValue(true)
    const blocked = { interactionEventId: 11, eventType: 'FEEDBACK', pageNumber: 1,
      feedback: 'NOT_UNDERSTOOD', strategyType: 'SAFETY_BLOCK', outputText: '这个问题我们请爸爸妈妈来一起说好吗？' }
    render({ requestGuidance, events: [questionEvent, blocked] })
    expect(wrapper.text()).toContain(blocked.outputText)
    expect(wrapper.text()).not.toContain(questionEvent.outputText)
    for (const label of ['已理解', '还没懂', '提交回答']) {
      expect(button(label).element.disabled).toBe(true)
    }
    expect(button('下一页').element.disabled).toBe(false)
    await button('跳过本页').trigger('click')
    expect(requestGuidance.mock.calls).toEqual([['SKIPPED']])
  })

  it('restores skipped prompts and does not silently generate another one', async () => {
    const requestGuidance = vi.fn().mockResolvedValue(true)
    render({
      requestGuidance,
      events: [
        questionEvent,
        { interactionEventId: 11, eventType: 'FEEDBACK', pageNumber: 1, feedback: 'SKIPPED', strategyType: 'SKIP', outputText: '' },
      ],
    })
    await flushPromises()
    expect(wrapper.text()).toContain('这一页的提问已跳过')
    expect(button('已理解').element.disabled).toBe(true)
    expect(wrapper.get('.reader-answer textarea').element.disabled).toBe(true)
    expect(requestGuidance).not.toHaveBeenCalled()
  })

  it('keeps transcript review separate from the ANSWER write', async () => {
    const submitAnswer = vi.fn().mockResolvedValue(true)
    render({ submitAnswer })
      const form = wrapper.get('.reader-answer')
    await form.get('textarea').setValue('  小兔在河边看云。 ')
    expect(submitAnswer).not.toHaveBeenCalled()
    await form.trigger('submit')
    await flushPromises()
    expect(submitAnswer).toHaveBeenCalledWith('小兔在河边看云。')
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
  })

  it('shows YaYa’s reply beside the saved answer and clears it on the next page', async () => {
    const submitAnswer = vi.fn().mockResolvedValue(true)
    render({ submitAnswer })
    await wrapper.get('.reader-answer textarea').setValue('小兔躲在树后。')
    await wrapper.get('.reader-answer').trigger('submit')
    await wrapper.setProps({
      events: [
        questionEvent,
        { interactionEventId: 12, eventType: 'ANSWER', pageNumber: 1, inputText: '小兔躲在树后。', replyText: '你从画面哪里看出来的？' },
      ],
    })
    expect(wrapper.text()).toContain('孩子的回答：')
    expect(wrapper.text()).toContain('小兔躲在树后。')
    expect(wrapper.get('.question-card [role="status"] .question-reply').text()).toBe('芽芽：你从画面哪里看出来的？')

    await wrapper.setProps({
      session: { ...initialSession, currentPage: 3, visitedPageNumbers: [1, 3], revision: 4 },
    })
    expect(displayedPageNumber()).toBe(3)
    expect(wrapper.text()).not.toContain('小兔躲在树后。')
    expect(wrapper.text()).not.toContain('你从画面哪里看出来的？')
  })

  it('isolates pending answers across sessions and renders only the reply read back on return', async () => {
    const first = deferred()
    const second = deferred()
    const submitAnswer = vi.fn().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    render({ submitAnswer })
    const submit = async (text) => {
      await wrapper.get('.reader-answer textarea').setValue(text)
      await wrapper.get('.reader-answer').trigger('submit')
    }

    await submit('第一场的回答')
    expect(wrapper.get('.question-reply').text()).toContain('芽芽正在想怎么回应')
    const newQuestion = { ...questionEvent, interactionEventId: 20 }
    await wrapper.setProps({ session: { ...initialSession, readingSessionId: 66 }, events: [newQuestion] })
    expect(wrapper.find('.question-reply').exists()).toBe(false)
    expect(wrapper.get('.reader-answer textarea').element.disabled).toBe(false)
    await submit('第二场的回答')
    expect(submitAnswer.mock.calls).toEqual([['第一场的回答'], ['第二场的回答']])

    first.resolve(false)
    await flushPromises()
    expect(wrapper.get('.question-reply').text()).toContain('芽芽正在想怎么回应')
    expect(wrapper.find('.question-error').exists()).toBe(false)

    const saved = { interactionEventId: 12, eventType: 'ANSWER', pageNumber: 1,
      inputText: '第一场已保存的回答', replyText: '这是读回的芽芽回应。' }
    await wrapper.setProps({ session: { ...initialSession, revision: 2 }, events: [questionEvent, saved] })
    expect(wrapper.get('.question-reply').text()).toBe('芽芽：这是读回的芽芽回应。')
    expect(wrapper.get('.reader-answer textarea').element.disabled).toBe(false)
    second.resolve(false)
    await flushPromises()
    expect(wrapper.get('.question-reply').text()).toBe('芽芽：这是读回的芽芽回应。')
    expect(wrapper.find('.question-error').exists()).toBe(false)
  })

  it('ignores the abandoned submission after the same answer view is reloaded', async () => {
    const pending = deferred()
    render({ submitAnswer: vi.fn().mockReturnValueOnce(pending.promise) })
    await wrapper.get('.reader-answer textarea').setValue('旧回答')
    await wrapper.get('.reader-answer').trigger('submit')
    await wrapper.setProps({ loading: true })
    await wrapper.setProps({ loading: false })
    expect(wrapper.find('.question-reply').exists()).toBe(false)
    const editor = wrapper.get('.reader-answer textarea')
    expect(editor.element.disabled).toBe(false)
    await editor.setValue('重新载入后的草稿')
    pending.resolve(true)
    await flushPromises()
    expect(editor.element.value).toBe('重新载入后的草稿')
  })

  it('ignores a pending answer from the earlier visit after a branch returns to the same page', async () => {
    const pending = deferred()
    const submitAnswer = vi.fn().mockReturnValueOnce(pending.promise)
    render({ submitAnswer })
    await wrapper.get('.reader-answer textarea').setValue('上一次来时的回答')
    await wrapper.get('.reader-answer').trigger('submit')
    expect(wrapper.get('.question-reply').text()).toContain('芽芽正在想怎么回应')

    await wrapper.setProps({
      session: { ...initialSession, visitedPageNumbers: [1, 3, 1], revision: 6 },
      events: [questionEvent,
        { interactionEventId: 12, eventType: 'BRANCH_CHOSEN', pageNumber: 1 },
        { interactionEventId: 13, eventType: 'BRANCH_CHOSEN', pageNumber: 3 },
        { ...questionEvent, interactionEventId: 14, outputText: '回到这里，你看到了什么？' }],
    })
    expect(displayedPageNumber()).toBe(1)
    expect(wrapper.find('.question-reply').exists()).toBe(false)
    const editor = wrapper.get('.reader-answer textarea')
    expect(editor.element.disabled).toBe(false)
    await editor.setValue('这一次来的新草稿')

    pending.resolve(false)
    await flushPromises()
    expect(wrapper.find('.question-error').exists()).toBe(false)
    expect(editor.element.value).toBe('这一次来的新草稿')
  })

  it('offers only approved branches from the current page', async () => {
    const chooseBranch = vi.fn().mockResolvedValue(true)
    render({
      chooseBranch,
      book: {
        ...book,
        branches: [
          { branchId: 501, fromPageNumber: 1, nextPageNumber: 3, choiceLabel: '跟着小船走', reviewStatus: 'APPROVED' },
          { branchId: 502, fromPageNumber: 3, nextPageNumber: 7, choiceLabel: '先回森林', reviewStatus: 'APPROVED' },
        ],
      },
    })
    expect(wrapper.text()).toContain('跟着小船走')
    expect(wrapper.text()).not.toContain('先回森林')
    await button('跟着小船走').trigger('click')
    await flushPromises()
    expect(chooseBranch).toHaveBeenCalledWith(501)
  })
})

it('uses a saved follow-up as the current question and does not print it twice', async () => {
  const answer = {
    interactionEventId: 11, eventType: 'ANSWER', pageNumber: 1, inputText: '树后。',
    replyText: '你找到了一条线索。你还看到了什么？', followupQuestion: '你还看到了什么？',
  }
  render()
  await wrapper.setProps({ events: [questionEvent, answer] })
  const card = wrapper.get('.question-card')
  expect(card.findAll('.question-saved, .question-reply, .question-followup').map(item => item.text())).toEqual([
    '上一轮回答：树后。', '芽芽：你找到了一条线索。', '芽芽追问：你还看到了什么？',
  ])
  expect(wrapper.text().split(answer.followupQuestion)).toHaveLength(2)
  expect(card.text()).not.toContain(questionEvent.outputText)
  await wrapper.get('.reader-answer textarea').setValue('草地。')
  await wrapper.get('.reader-answer').trigger('submit')
  expect(wrapper.props('submitAnswer')).toHaveBeenCalledWith('草地。')
})

it('restores the follow-up from a saved answer on a fresh reader view', () => {
  render({ events: [questionEvent, {
    interactionEventId: 11, eventType: 'ANSWER', pageNumber: 1, inputText: '树后。',
    replyText: '很好。你还看到了什么？', followupQuestion: '你还看到了什么？',
  }] })
  expect(wrapper.get('.reader-side').text()).toContain('你还看到了什么？')
  expect(wrapper.get('.reader-side').text()).not.toContain(questionEvent.outputText)
})

it('keeps the real question after a saved answer without a follow-up and labels the next round correctly', async () => {
  const firstFollowup = '小兔脚边是什么？'
  const followupQuestion = '草地上还有什么？'
  const earlierAnswer = {
    interactionEventId: 11, eventType: 'ANSWER', pageNumber: 1, inputText: '在树旁。',
    replyText: '你找到小兔了。' + firstFollowup, followupQuestion: firstFollowup,
  }
  const latestFollowup = {
    interactionEventId: 12, eventType: 'ANSWER', pageNumber: 1, inputText: '有草地。',
    replyText: '我们再看看。' + followupQuestion, followupQuestion,
  }
  const noFollowup = {
    interactionEventId: 13, eventType: 'ANSWER', pageNumber: 1, inputText: '有一棵树。',
    replyText: '谢谢你分享。我们一起看看画面。',
  }
  const newerQuestion = {
    interactionEventId: 14, eventType: 'FEEDBACK', pageNumber: 1, feedback: 'UNDERSTOOD',
    outputText: '树旁边有什么？', strategyType: 'CONTEXT_QUESTION',
  }
  const priorEvents = [questionEvent, earlierAnswer, latestFollowup]
  const terminalEvents = [...priorEvents, noFollowup]
  const submitAnswer = vi.fn().mockImplementation(async () => {
    await wrapper.setProps({ events: terminalEvents })
    return true
  })
  render({ events: priorEvents, submitAnswer })
  await wrapper.get('.reader-answer textarea').setValue(noFollowup.inputText)
  await wrapper.get('.reader-answer').trigger('submit')
  await flushPromises()

  function expectRetainedQuestion() {
    const card = wrapper.get('.question-card')
    expect(card.text()).not.toContain(questionEvent.outputText)
    expect(card.text()).not.toContain(firstFollowup)
    expect(card.text().split(followupQuestion)).toHaveLength(2)
    expect(card.text().indexOf(followupQuestion)).toBeLessThan(card.text().indexOf(noFollowup.inputText))
    expect(card.get('.question-saved').text()).toBe('孩子的回答：' + noFollowup.inputText)
    expect(card.get('.question-reply').text()).toBe('芽芽：' + noFollowup.replyText)
  }

  expectRetainedQuestion()
  wrapper.unmount()
  render({ events: terminalEvents })
  expectRetainedQuestion()
  await wrapper.setProps({ events: [...terminalEvents, newerQuestion] })
  const card = wrapper.get('.question-card')
  expect(card.text()).toContain(newerQuestion.outputText)
  expect(card.text()).not.toContain(followupQuestion)
  expect(card.get('.question-saved').text()).toBe('上一轮回答：' + noFollowup.inputText)
})

it.each(['IN_PROGRESS', 'FINISHED'])('separates answers on repeated visits when viewing %s history', async (status) => {
  const firstQuestion = '第一次：小兔在哪儿？'
  const firstFollowup = '第一次：小兔在做什么？'
  const secondQuestion = '第二次：小兔身边有什么？'
  const terminalReply = '第二次：我们再看看图。'
  render({
    session: { ...initialSession, visitedPageNumbers: [1, 3, 1], status, revision: 8 },
    events: [
      { interactionEventId: 1, eventType: 'STARTED', pageNumber: 1 },
      { ...questionEvent, interactionEventId: 2, outputText: firstQuestion },
      { interactionEventId: 3, eventType: 'ANSWER', pageNumber: 1, inputText: '它在草地上。',
        replyText: '第一次：你看到草地了。' + firstFollowup, followupQuestion: firstFollowup },
      { interactionEventId: 4, eventType: 'ANSWER', pageNumber: 1, inputText: '它在走路。', replyText: '第一次：再看看。' },
      { interactionEventId: 5, eventType: 'BRANCH_CHOSEN', pageNumber: 1 },
      { interactionEventId: 6, eventType: 'BRANCH_CHOSEN', pageNumber: 3 },
      { ...questionEvent, interactionEventId: 7, outputText: secondQuestion },
      { interactionEventId: 8, eventType: 'ANSWER', pageNumber: 1, inputText: '它身边有花朵。', replyText: terminalReply },
    ],
  })
  const card = wrapper.get('.question-card')
  expect(displayedPageNumber()).toBe(1)
  expect(card.text()).toContain(secondQuestion)
  expect(card.text()).toContain(terminalReply)
  expect(card.text()).not.toContain(firstFollowup)

  await button('上一页').trigger('click')
  expect(displayedPageNumber()).toBe(3)
  await button('上一页').trigger('click')
  expect(displayedPageNumber()).toBe(1)
  expect(card.text()).toContain(firstFollowup)
  expect(card.get('.question-saved').text()).toBe('孩子的回答：它在走路。')
  expect(card.text()).not.toContain(secondQuestion)
  expect(card.text()).not.toContain(terminalReply)
})

it('requests current-visit guidance after a branch returns to the same page', () => {
  const requestGuidance = vi.fn().mockResolvedValue(true)
  render({
    session: { ...initialSession, visitedPageNumbers: [1, 3, 1], revision: 6 },
    events: [questionEvent, {
      interactionEventId: 11, eventType: 'ANSWER', pageNumber: 1,
      replyText: '很好。旧追问？', followupQuestion: '旧追问？',
    }, {
      interactionEventId: 12, eventType: 'BRANCH_CHOSEN', pageNumber: 1,
    }, {
      interactionEventId: 13, eventType: 'BRANCH_CHOSEN', pageNumber: 3,
    }],
    requestGuidance,
  })
  expect(requestGuidance).toHaveBeenCalledExactlyOnceWith('INITIAL')
  expect(wrapper.get('.reader-side').text()).not.toContain('旧追问？')
})
