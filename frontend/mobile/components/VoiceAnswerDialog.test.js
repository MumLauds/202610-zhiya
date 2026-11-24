import { mount, flushPromises } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import VoiceAnswerDialog from './VoiceAnswerDialog.vue'

function deferred() {
  let resolve
  let reject
  const promise = new Promise((yes, no) => {
    resolve = yes
    reject = no
  })
  return { promise, resolve, reject }
}

function createStream() {
  const track = { stop: vi.fn() }
  return { getTracks: () => [track], track }
}

describe('VoiceAnswerDialog recording lifecycle', () => {
  let wrapper
  let getUserMedia
  let recorders
  let createObjectURL
  let revokeObjectURL

  beforeEach(() => {
    recorders = []
    getUserMedia = vi.fn()
    createObjectURL = vi.fn(() => 'blob:recording')
    revokeObjectURL = vi.fn()
    const MockURL = class extends URL {
      static createObjectURL = createObjectURL
      static revokeObjectURL = revokeObjectURL
    }
    vi.stubGlobal('URL', MockURL)
    vi.stubGlobal('navigator', { mediaDevices: { getUserMedia } })
    vi.stubGlobal('MediaRecorder', class extends EventTarget {
      state = 'inactive'
      mimeType = 'audio/mp4'
      stop = vi.fn(() => { this.state = 'inactive' })

      constructor() {
        super()
        recorders.push(this)
      }

      start() {
        this.state = 'recording'
      }

      finish() {
        this.dispatchEvent(new MessageEvent('dataavailable', {
          data: new Blob(['sample'], { type: this.mimeType }),
        }))
        this.dispatchEvent(new Event('stop'))
      }
    })
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
    vi.unstubAllGlobals()
  })

  function openDialog(voiceApi = {}) {
    wrapper = mount(VoiceAnswerDialog, {
      props: { voiceApi, sessionId: 1, pageNumber: 2, revision: 3 },
    })
  }

  async function startRecording() {
    const stream = createStream()
    getUserMedia.mockResolvedValueOnce(stream)
    await wrapper.get('.record-button').trigger('click')
    await flushPromises()
    return { stream, recorder: recorders.at(-1) }
  }

  it.each(['close', 'manual input', 'unmount'])('releases microphone permission that arrives after %s', async (action) => {
    const permission = deferred()
    const stream = createStream()
    openDialog()
    getUserMedia.mockReturnValueOnce(permission.promise)
    await wrapper.get('.record-button').trigger('click')
    expect(wrapper.get('.record-button').element.disabled).toBe(true)

    if (action === 'close') {
      await wrapper.get('[aria-label="关闭"]').trigger('click')
      expect(wrapper.emitted('close')).toHaveLength(1)
    } else if (action === 'manual input') {
      await wrapper.get('.text-button').trigger('click')
      await wrapper.get('textarea').setValue('孩子的回答')
    } else {
      wrapper.unmount()
      wrapper = null
    }

    permission.resolve(stream)
    await flushPromises()
    expect(stream.track.stop).toHaveBeenCalledTimes(1)
    expect(recorders).toHaveLength(0)
    if (action === 'manual input') {
      expect(wrapper.get('textarea').element.value).toBe('孩子的回答')
    }
  })

  it('ignores a stale permission rejection during a new recording', async () => {
    const permission = deferred()
    openDialog()
    getUserMedia.mockReturnValueOnce(permission.promise)
    await wrapper.get('.record-button').trigger('click')
    await wrapper.get('.text-button').trigger('click')
    await wrapper.get('.review-panel .button-ghost').trigger('click')
    const { stream } = await startRecording()

    permission.reject(new DOMException('denied', 'NotAllowedError'))
    await flushPromises()
    expect(wrapper.get('.record-button').classes()).toContain('is-recording')
    expect(wrapper.find('textarea').exists()).toBe(false)
    expect(stream.track.stop).not.toHaveBeenCalled()
  })

  it.each(['manual input', 'unmount'])('discards a queued stop event after %s', async (action) => {
    openDialog()
    const { stream, recorder } = await startRecording()
    await wrapper.get('.record-button').trigger('click')
    expect(recorder.stop).toHaveBeenCalledTimes(1)
    expect(stream.track.stop).not.toHaveBeenCalled()

    if (action === 'manual input') {
      await wrapper.get('.text-button').trigger('click')
      await wrapper.get('.review-panel .button-ghost').trigger('click')
      await startRecording()
    } else {
      wrapper.unmount()
      wrapper = null
    }

    expect(stream.track.stop).toHaveBeenCalledTimes(1)
    recorder.finish()
    await flushPromises()
    expect(createObjectURL).not.toHaveBeenCalled()
    if (wrapper) expect(wrapper.get('.record-button').classes()).toContain('is-recording')
  })

  it('previews a normally stopped recording and revokes its URL on close', async () => {
    openDialog()
    const { stream, recorder } = await startRecording()
    await wrapper.get('.record-button').trigger('click')
    recorder.finish()
    await flushPromises()

    expect(stream.track.stop).toHaveBeenCalledTimes(1)
    expect(createObjectURL.mock.calls[0][0].type).toBe('audio/mp4')
    expect(wrapper.get('audio').attributes('src')).toBe('blob:recording')
    await wrapper.get('[aria-label="关闭"]').trigger('click')
    expect(revokeObjectURL).toHaveBeenCalledExactlyOnceWith('blob:recording')
  })

  it('preserves typed text when an aborted transcription still resolves', async () => {
    const response = deferred()
    const transcribe = vi.fn(() => response.promise)
    openDialog({ transcribe })
    const { recorder } = await startRecording()
    await wrapper.get('.record-button').trigger('click')
    recorder.finish()
    await flushPromises()
    await wrapper.get('.recorder-panel .button-primary').trigger('click')
    await wrapper.get('.text-button').trigger('click')
    await wrapper.get('textarea').setValue('孩子的回答')

    expect(transcribe.mock.calls[0][0].signal.aborted).toBe(true)
    response.resolve({ data: { text: '迟到的转写结果', reliable: false } })
    await flushPromises()
    expect(wrapper.get('textarea').element.value).toBe('孩子的回答')
    expect(wrapper.find('.warning-note').exists()).toBe(false)
  })
})
