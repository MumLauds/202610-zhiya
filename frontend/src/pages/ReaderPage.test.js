import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import ReaderPage from './ReaderPage.vue'
import { synthesize, transcribe } from '../api/voice'

vi.mock('../api/voice', () => ({ synthesize: vi.fn(), transcribe: vi.fn() }))
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }
const book = { storyVersionId: 88, title: '绘本', pageCount: 3, pages: [{ pageNumber: 1, text: '一', defaultNextPageNumber: 3 }, { pageNumber: 3, text: '三', defaultNextPageNumber: 7 }, { pageNumber: 7, text: '七', defaultNextPageNumber: null }] }
const initialSession = { readingSessionId: 55, mode: 'PARENT_CHILD', currentPage: 1, visitedPageNumbers: [1], revision: 1, status: 'IN_PROGRESS' }
let wrapper, track, getUserMedia, recorders, audios, showPage
const button = (text) => wrapper.findAll('button').find((item) => item.text().includes(text))

function render(props = {}, useRealFlipBook = false) {
  const FlipStub = defineComponent({ name: 'FlipBook', props: ['startPage'], emits: ['page-change'], setup(_, { expose }) { expose({ showPage }); return () => null } })
  wrapper = mount(ReaderPage, { props: { book, session: { ...initialSession }, authenticated: true, recordPage: vi.fn().mockResolvedValue(false), completeReading: vi.fn(), refreshReading: vi.fn(), ...props }, global: { stubs: { FlipBook: useRealFlipBook ? false : FlipStub } } })
  return wrapper
}

async function startRecording() {
  await button('语音识别').trigger('click')
  await wrapper.find('.mic-button').trigger('click')
  await flushPromises()
}

async function receiveUnconfirmedTranscript() {
  transcribe.mockResolvedValueOnce({ text: '小免在哪里？', status: 'RECOGNIZED', reliable: false })
  render()
  await startRecording()
  await wrapper.get('.mic-button').trigger('click')
  await flushPromises()
  return wrapper.get('[role="dialog"]')
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
    stop() { this.state = 'inactive'; this.ondataavailable?.({ data: new Blob(['voice']) }); this.onstop?.() }
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

describe('recording lifecycle', () => {
  it('cancels without upload and stops all microphone tracks', async () => {
    render()
    await startRecording()
    expect(recorders[0].state).toBe('recording')
    await button('取消并关闭').trigger('click')
    await flushPromises()
    expect(recorders[0].state).toBe('inactive')
    expect(track.stop).toHaveBeenCalled()
    expect(transcribe).not.toHaveBeenCalled()
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
  })

  it('stops a late microphone permission result after cancellation', async () => {
    const permission = deferred()
    getUserMedia.mockReturnValueOnce(permission.promise)
    render()
    await startRecording()
    expect(wrapper.find('.mic-button').element.disabled).toBe(true)
    await button('取消并关闭').trigger('click')
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
    await button('取消并关闭').trigger('click')
    expect(transcribe.mock.calls[0][2].aborted).toBe(true)
    recognition.resolve({ text: '迟到的结果', reliable: true })
    await flushPromises()
    await button('语音识别').trigger('click')
    expect(wrapper.text()).not.toContain('迟到的结果')
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
  it.each(['direct', 'denied', 'unsupported'])('accepts a local typed question with %s input', async (entry) => {
    render()
    if (entry === 'direct') {
      await button('输入文字').trigger('click')
    } else {
      if (entry === 'denied') getUserMedia.mockRejectedValueOnce(new Error('permission denied'))
      if (entry === 'unsupported') vi.stubGlobal('MediaRecorder', undefined)
      await startRecording()
    }
    const dialog = wrapper.get('[role="dialog"]')
    expect(dialog.get('label').text()).toBe('输入问题')
    expect(button('确认文字').element.disabled).toBe(true)
    await dialog.get('textarea').setValue('  小兔回家了吗？ ')
    await button('确认文字').trigger('click')
    expect(dialog.get('[role="status"]').text()).toBe('已确认的文字')
    expect(dialog.get('.transcript-box p').text()).toBe('小兔回家了吗？')
    expect(dialog.text()).toContain('仅在本次弹窗内查看')
    expect(getUserMedia).toHaveBeenCalledTimes(entry === 'denied' ? 1 : 0)
    expect(transcribe).not.toHaveBeenCalled()
    expect(synthesize).not.toHaveBeenCalled()
    await dialog.get('.modal-close').trigger('click')
    await button('输入文字').trigger('click')
    expect(wrapper.get('textarea').element.value).toBe('')
  })

  it('displays the corrected text after the parent confirms it', async () => {
    const dialog = await receiveUnconfirmedTranscript()
    const editor = dialog.get('textarea')
    expect(dialog.get('label[for="' + editor.attributes('id') + '"]').text()).toBe('待确认的转写')
    expect(editor.element.value).toBe('小免在哪里？')
    expect(dialog.text()).not.toContain('识别到的问题')
    await editor.setValue('   ')
    expect(button('确认转写').element.disabled).toBe(true)
    await editor.setValue('  小兔在哪里？  ')
    await button('确认转写').trigger('click')
    expect(dialog.text()).toContain('已确认的转写')
    expect(dialog.get('.transcript-box p').text()).toBe('小兔在哪里？')
    expect(dialog.find('textarea').exists()).toBe(false)
    expect(transcribe).toHaveBeenCalledTimes(1)
  })

  it('allows confirmation without editing and clears the result when the dialog closes', async () => {
    const dialog = await receiveUnconfirmedTranscript()
    await button('确认转写').trigger('click')
    expect(dialog.get('.transcript-box p').text()).toBe('小免在哪里？')
    await dialog.get('.modal-close').trigger('click')
    await button('语音识别').trigger('click')
    expect(wrapper.text()).not.toContain('小免在哪里？')
    expect(wrapper.find('textarea').exists()).toBe(false)
    expect(transcribe).toHaveBeenCalledTimes(1)
  })

  it('replaces the draft on re-recording and uploads only after the next explicit stop', async () => {
    const dialog = await receiveUnconfirmedTranscript()
    await dialog.get('textarea').setValue('上一段草稿')
    await button('重新录音').trigger('click')
    await flushPromises()
    expect(recorders[1].state).toBe('recording')
    expect(dialog.find('textarea').exists()).toBe(false)
    expect(transcribe).toHaveBeenCalledTimes(1)
    await dialog.get('.mic-button').trigger('click')
    await flushPromises()
    expect(transcribe).toHaveBeenCalledTimes(2)
    expect(dialog.text()).toContain('识别到的问题')
    expect(dialog.text()).toContain('识别的声音')
    expect(dialog.text()).not.toContain('上一段草稿')
    expect(button('确认转写')).toBeUndefined()
  })

  it('discards unconfirmed edits when the reading page changes', async () => {
    const dialog = await receiveUnconfirmedTranscript()
    await dialog.get('textarea').setValue('只属于上一页的草稿')
    await wrapper.setProps({ session: { ...initialSession, currentPage: 3, visitedPageNumbers: [1, 3], revision: 2 } })
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    await button('语音识别').trigger('click')
    expect(wrapper.text()).not.toContain('只属于上一页的草稿')
    expect(wrapper.find('textarea').exists()).toBe(false)
    expect(transcribe).toHaveBeenCalledTimes(1)
  })

  it('keeps the editable draft when permission for a replacement recording is denied', async () => {
    const dialog = await receiveUnconfirmedTranscript()
    await dialog.get('textarea').setValue('仍可确认的文字')
    getUserMedia.mockRejectedValueOnce(new Error('permission denied'))
    await button('重新录音').trigger('click')
    await flushPromises()
    expect(dialog.get('[role="alert"]').text()).toContain('麦克风不可用')
    expect(dialog.get('textarea').element.value).toBe('仍可确认的文字')
    await button('确认转写').trigger('click')
    expect(dialog.get('.transcript-box p').text()).toBe('仍可确认的文字')
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
    const modeButton = button('本次阅读已开始')
    expect(modeButton.element.disabled).toBe(true)
  })

  it('keeps an already authorized CREATED session out of first-time handoff', async () => {
    const enterChildModeAction = vi.fn()
    render({ session: { ...initialSession, mode: 'CHILD_SOLO', status: 'CREATED' }, enterChildModeAction })
    const modeButton = button('此会话已开启儿童模式')
    expect(modeButton.element.disabled).toBe(true)
    await modeButton.trigger('click')
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(enterChildModeAction).not.toHaveBeenCalled()
  })
})

describe('narration lifecycle', () => {
  it('pauses the actual player and releases its source', async () => {
    render()
    await button('朗读').trigger('click')
    await flushPromises()
    expect(audios).toHaveLength(1)
    await button('暂停朗读').trigger('click')
    expect(audios[0].pause).toHaveBeenCalledTimes(1)
    expect(audios[0].removeAttribute).toHaveBeenCalledWith('src')
    expect(speechSynthesis.cancel).toHaveBeenCalled()
  })

  it('ignores synthesis returned after cancelling the loading state', async () => {
    const speech = deferred()
    synthesize.mockReturnValueOnce(speech.promise)
    render()
    await button('朗读').trigger('click')
    await button('取消朗读').trigger('click')
    expect(synthesize.mock.calls[0][1].aborted).toBe(true)
    speech.resolve({ audioUrl: '/late' })
    await flushPromises()
    expect(audios).toHaveLength(0)
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
    synthesize.mockRejectedValueOnce(new Error('服务暂不可用'))
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
    const finish = button(currentPage === 1 ? '结束本次阅读' : '完成')
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

  it('follows the visited branch when reviewing a finished session with buttons or gestures', async () => {
    const session = { ...initialSession, currentPage: 3, visitedPageNumbers: [1, 3] }
    const branchedBook = { ...book, pageCount: 4, pages: [
      { ...book.pages[0], defaultNextPageNumber: 2 },
      { pageNumber: 2, text: '二', defaultNextPageNumber: 3 },
      ...book.pages.slice(1),
    ] }
    let onFlip
    const widget = { on: (_, callback) => { onFlip = callback }, turnTo: vi.fn(), destroy: vi.fn() }
    vi.stubGlobal('OpenPageFlip', { createBook: () => widget })
    render({ book: branchedBook, session }, true)
    await button('结束本次阅读').trigger('click')
    await wrapper.setProps({ session: { ...session, status: 'FINISHED' } })
    await button('上一页').trigger('click')
    expect(wrapper.get('.reader-heading p').text()).toContain('第 1 页')
    expect(button('下一页').element.disabled).toBe(false)
    await button('下一页').trigger('click')
    expect(wrapper.get('.reader-heading p').text()).toContain('第 3 页')
    expect(button('下一页').element.disabled).toBe(true)

    expect(widget.destroy).toHaveBeenCalledTimes(1)
    expect(wrapper.findAll('.flip-page--text').map(page => page.attributes('aria-label'))).toEqual(['第 1 页文字', '第 3 页文字'])
    expect(wrapper.find('.page-number').text()).toContain('共 4 页')
    onFlip({ page: 0 })
    await flushPromises()
    expect(wrapper.get('.reader-heading p').text()).toContain('第 1 页')
    onFlip({ page: 2 })
    await flushPromises()
    expect(wrapper.get('.reader-heading p').text()).toContain('第 3 页')
    expect(wrapper.props('recordPage')).not.toHaveBeenCalled()
  })

  it.each([12, 13, 200])('bounds review dots while preserving all %i visits in order', (count) => {
    const visits = Array.from({ length: count }, (_, index) => index % 2 === 0 ? 1 : 3)
    vi.stubGlobal('OpenPageFlip', { createBook: () => ({ on: vi.fn(), turnTo: vi.fn(), destroy: vi.fn() }) })
    render({ session: { ...initialSession, status: 'FINISHED', currentPage: visits.at(-1), visitedPageNumbers: visits } }, true)

    const pages = wrapper.findAll('.flip-page--text')
    expect(pages.map(page => page.attributes('aria-label'))).toEqual(visits.map(number => `第 ${number} 页文字`))
    if (count === 12) {
      expect(wrapper.findAll('.page-dots i')).toHaveLength(count * count)
      expect(wrapper.findAll('.page-dots i.active')).toHaveLength(count)
    } else {
      expect(wrapper.findAll('.page-dots i')).toHaveLength(0)
      pages.forEach((page, index) => {
        expect(page.get('.page-number').text()).toContain(`回看 ${index + 1} / ${count}`)
      })
    }
  })

  it('keeps the position of each visit when an approved branch revisits a page', async () => {
    render({ session: { ...initialSession, status: 'FINISHED', currentPage: 1, visitedPageNumbers: [1, 7, 1] } })
    expect(button('上一页').element.disabled).toBe(false)
    await button('上一页').trigger('click')
    expect(wrapper.get('.reader-heading p').text()).toContain('第 7 页')
    await button('上一页').trigger('click')
    expect(button('上一页').element.disabled).toBe(true)
    await button('下一页').trigger('click')
    expect(wrapper.get('.reader-heading p').text()).toContain('第 7 页')
    await button('下一页').trigger('click')
    expect(wrapper.get('.reader-heading p').text()).toContain('第 1 页')
    expect(button('下一页').element.disabled).toBe(true)
    expect(wrapper.props('recordPage')).not.toHaveBeenCalled()
  })

  it('finishes while reviewing an earlier page without changing the saved progress', async () => {
    const completeReading = vi.fn()
    render({ session: { ...initialSession, currentPage: 3, visitedPageNumbers: [1, 3] }, completeReading })
    await button('上一页').trigger('click')
    await button('结束本次阅读').trigger('click')
    expect(completeReading).toHaveBeenCalledTimes(1)
    expect(wrapper.props('recordPage')).not.toHaveBeenCalled()
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

  it('restores the widget after rejection instead of only changing the page label', async () => {
    render()
    wrapper.findComponent({ name: 'FlipBook' }).vm.$emit('page-change', { storyIndex: 1 })
    await flushPromises()
    expect(showPage).toHaveBeenLastCalledWith(0)
    expect(wrapper.findComponent({ name: 'FlipBook' }).props('startPage')).toBe(0)
  })

  it.each(['IN_PROGRESS', 'FINISHED', 'INTERRUPTED'])('keeps the revisit hint consistent with media controls in %s sessions', async (status) => {
    const record = vi.fn()
    render({ session: { ...initialSession, status, currentPage: 3, visitedPageNumbers: [1, 3], revision: 2 }, recordPage: record })
    await button('上一页').trigger('click')
    expect(wrapper.text()).toContain('回看已读页')
    expect(wrapper.text().includes('返回当前进度后可继续朗读与录音')).toBe(status === 'IN_PROGRESS')
    expect(button('朗读').element.disabled).toBe(true)
    expect(button('语音识别').element.disabled).toBe(true)
    expect(record).not.toHaveBeenCalled()
    await button('下一页').trigger('click')
    expect(showPage).toHaveBeenLastCalledWith(1)
    expect(record).not.toHaveBeenCalled()
    expect(button('朗读').element.disabled).toBe(status !== 'IN_PROGRESS')
    expect(button('语音识别').element.disabled).toBe(status !== 'IN_PROGRESS')
  })

  it('marks unintegrated interactions unavailable and unknown progress as pending', () => {
    render({ syncRequired: true })
    expect(button('已理解').element.disabled).toBe(true)
    expect(button('还没懂').element.disabled).toBe(true)
    expect(button('下一页').element.disabled).toBe(true)
    expect(button('结束本次阅读').element.disabled).toBe(true)
    expect(button('朗读').element.disabled).toBe(true)
    expect(wrapper.text()).toContain('进度待确认')
    expect(button('确认并同步进度')).toBeDefined()
  })
})
