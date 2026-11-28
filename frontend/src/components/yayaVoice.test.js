import { afterEach, expect, it, vi } from 'vitest'
import { measureVoice } from './yayaVoice'

afterEach(() => vi.unstubAllGlobals())

it('uses the playback fallback during capture startup and closes the mouth during real pauses', () => {
  let sample = 128
  const track = { stop: vi.fn() }
  const media = { captureStream: () => ({ getAudioTracks: () => [track], getTracks: () => [track] }) }
  vi.stubGlobal('AudioContext', class {
    state = 'running'
    sampleRate = 48000
    createMediaStreamSource() { return { connect() {}, disconnect() {} } }
    createAnalyser() {
      return {
        frequencyBinCount: 256,
        getByteTimeDomainData: buffer => buffer.fill(sample),
        getByteFrequencyData: buffer => buffer.fill(50),
      }
    }
    async resume() {}
    async close() {}
  })
  const voice = measureVoice(media)
  expect(voice.sample()).toBeNull()
  sample = 151
  expect(voice.sample().open).toBeGreaterThan(0)
  sample = 128
  expect(voice.sample()).toEqual({ open: 0, round: 0 })
  voice.dispose()
  expect(track.stop).toHaveBeenCalledOnce()
})
