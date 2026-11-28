// Capture only the character's playback. The original media element keeps its own output.
export function measureVoice(media) {
  const capture = media.captureStream || media.mozCaptureStream
  const Context = window.AudioContext || window.webkitAudioContext
  if (!capture || !Context) return null
  let context, input, stream
  function dispose() {
    input?.disconnect()
    stream?.getTracks().forEach(track => track.stop())
    if (context && context.state !== 'closed') void context.close().catch(() => {})
  }
  try {
    stream = capture.call(media)
    if (!stream.getAudioTracks().length) { dispose(); return null }
    context = new Context()
    input = context.createMediaStreamSource(stream)
    const analyser = context.createAnalyser()
    analyser.fftSize = 512
    input.connect(analyser)
    void context.resume().catch(() => {})
    const samples = new Uint8Array(analyser.fftSize)
    const bands = new Uint8Array(analyser.frequencyBinCount)
    const hz = context.sampleRate / analyser.fftSize
    let receivedSamples = false
    return {
      sample() {
        if (context.state !== 'running') return null
        analyser.getByteTimeDomainData(samples)
        let power = 0
        for (const value of samples) power += ((value - 128) / 128) ** 2
        // A newly attached capture can return empty buffers while the element is already playing.
        if (!receivedSamples && power === 0) return null
        receivedSamples = true
        const open = Math.min(1, Math.max(0, (Math.sqrt(power / samples.length) - .008) * 7))
        analyser.getByteFrequencyData(bands)
        let low = 0, high = 0
        for (let i = 1; i < bands.length && i * hz < 3200; i++) {
          if (i * hz < 850) low += bands[i] ** 2
          else high += bands[i] ** 2
        }
        return { open, round: open * Math.min(1, 1.4 * low / (low + high + 1)) }
      },
      dispose,
    }
  } catch { dispose(); return null }
}
