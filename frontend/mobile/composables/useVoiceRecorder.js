import { computed, onBeforeUnmount, ref } from 'vue'

export function useVoiceRecorder() {
  const state = ref('idle')
  const error = ref('')
  const blob = ref(null)
  const objectUrl = ref('')
  const durationMs = ref(0)
  const mimeType = ref('')
  let activeRecording = null

  const recording = computed(() => state.value === 'recording')
  const busy = computed(() => ['requesting', 'transcribing', 'stopping'].includes(state.value))

  async function start() {
    if (activeRecording) return
    error.value = ''
    resetBlob()
    const attempt = { stream: null, recorder: null, resolveStop: null }
    activeRecording = attempt
    state.value = 'requesting'
    try {
      if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
        throw new Error('当前浏览器不支持录音')
      }
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      if (activeRecording !== attempt) {
        stream.getTracks().forEach((track) => track.stop())
        return
      }
      attempt.stream = stream
      const supported = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4']
        .find((type) => MediaRecorder.isTypeSupported?.(type))
      const recorder = new MediaRecorder(stream, supported ? { mimeType: supported } : undefined)
      attempt.recorder = recorder
      const chunks = []
      const startedAt = performance.now()
      recorder.addEventListener('dataavailable', (event) => {
        if (event.data?.size) chunks.push(event.data)
      })
      recorder.addEventListener('error', () => {
        if (activeRecording !== attempt) return
        error.value = '录音失败，请重试'
        cleanupRecording()
        state.value = 'error'
      })
      recorder.addEventListener('stop', () => {
        if (activeRecording !== attempt) return
        durationMs.value = Math.round(performance.now() - startedAt)
        mimeType.value = recorder.mimeType || chunks[0]?.type || 'audio/webm'
        blob.value = new Blob(chunks, { type: mimeType.value })
        objectUrl.value = URL.createObjectURL(blob.value)
        cleanupRecording(blob.value)
        state.value = 'recorded'
      }, { once: true })
      recorder.start()
      state.value = 'recording'
    } catch (cause) {
      if (activeRecording !== attempt) return
      error.value = cause?.name === 'NotAllowedError' ? '麦克风权限被拒绝，可改用手动输入' : (cause?.message ?? '无法开始录音')
      cleanupRecording()
      state.value = 'error'
      throw cause
    }
  }

  function stop() {
    const attempt = activeRecording
    const recorder = attempt?.recorder
    if (!recorder || recorder.state === 'inactive') return Promise.resolve(null)
    state.value = 'stopping'
    return new Promise((resolve) => {
      attempt.resolveStop = resolve
      try {
        recorder.stop()
      } catch {
        error.value = '录音失败，请重试'
        cleanupRecording()
        state.value = 'error'
      }
    })
  }

  function cancel() {
    cleanupRecording()
    resetBlob()
    state.value = 'idle'
    error.value = ''
  }

  function resetBlob() {
    if (objectUrl.value) URL.revokeObjectURL(objectUrl.value)
    objectUrl.value = ''
    blob.value = null
    durationMs.value = 0
    mimeType.value = ''
  }

  function reset() {
    cancel()
  }

  function markTranscribing() {
    state.value = 'transcribing'
    error.value = ''
  }

  function markReview({ text, reliable }) {
    state.value = 'review'
    error.value = text ? '' : '没有识别到清晰内容，请重录或手动输入'
    return { text, reliable }
  }

  function markConfirmed() {
    state.value = 'confirmed'
  }

  function markError(message) {
    state.value = 'error'
    error.value = message || '转写失败，请重录或手动输入'
  }

  function cleanupRecording(result = null) {
    const attempt = activeRecording
    if (!attempt) return
    activeRecording = null
    if (attempt.recorder && attempt.recorder.state !== 'inactive') {
      try { attempt.recorder.stop() } catch { /* release tracks below */ }
    }
    attempt.stream?.getTracks().forEach((track) => track.stop())
    attempt.resolveStop?.(result)
  }

  onBeforeUnmount(cancel)

  return {
    state,
    error,
    blob,
    objectUrl,
    durationMs,
    mimeType,
    recording,
    busy,
    start,
    stop,
    cancel,
    reset,
    markTranscribing,
    markReview,
    markConfirmed,
    markError,
  }
}
