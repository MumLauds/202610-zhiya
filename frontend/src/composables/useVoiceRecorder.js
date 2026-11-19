import { computed, onBeforeUnmount, ref } from 'vue'

export function useVoiceRecorder() {
  const state = ref('idle')
  const error = ref('')
  const blob = ref(null)
  const objectUrl = ref('')
  const durationMs = ref(0)
  const mimeType = ref('')
  let recorder = null
  let stream = null
  let chunks = []
  let startedAt = 0

  const recording = computed(() => state.value === 'recording')
  const busy = computed(() => ['requesting', 'transcribing', 'stopping'].includes(state.value))

  async function start() {
    error.value = ''
    resetBlob()
    state.value = 'requesting'
    try {
      if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
        throw new Error('当前浏览器不支持录音')
      }
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const supported = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4']
        .find((type) => MediaRecorder.isTypeSupported?.(type))
      recorder = new MediaRecorder(stream, supported ? { mimeType: supported } : undefined)
      chunks = []
      startedAt = performance.now()
      recorder.addEventListener('dataavailable', (event) => {
        if (event.data?.size) chunks.push(event.data)
      })
      recorder.addEventListener('error', () => {
        error.value = '录音失败，请重试'
        cleanupStream()
        state.value = 'error'
      })
      recorder.start()
      state.value = 'recording'
    } catch (cause) {
      error.value = cause?.name === 'NotAllowedError' ? '麦克风权限被拒绝，可改用手动输入' : (cause?.message ?? '无法开始录音')
      cleanupStream()
      state.value = 'error'
      throw cause
    }
  }

  function stop() {
    if (!recorder || recorder.state === 'inactive') return Promise.resolve(null)
    state.value = 'stopping'
    return new Promise((resolve) => {
      recorder.addEventListener('stop', () => {
        durationMs.value = Math.round(performance.now() - startedAt)
        mimeType.value = recorder.mimeType || chunks[0]?.type || 'audio/webm'
        blob.value = new Blob(chunks, { type: mimeType.value })
        objectUrl.value = URL.createObjectURL(blob.value)
        cleanupStream()
        state.value = 'recorded'
        resolve(blob.value)
      }, { once: true })
      recorder.stop()
    })
  }

  function cancel() {
    if (recorder && recorder.state !== 'inactive') {
      try { recorder.stop() } catch { /* already stopped */ }
    }
    cleanupStream()
    resetBlob()
    state.value = 'idle'
    error.value = ''
  }

  function resetBlob() {
    if (objectUrl.value) URL.revokeObjectURL(objectUrl.value)
    objectUrl.value = ''
    blob.value = null
    durationMs.value = 0
    chunks = []
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

  function cleanupStream() {
    stream?.getTracks().forEach((track) => track.stop())
    stream = null
    recorder = null
  }

  onBeforeUnmount(() => {
    if (recorder && recorder.state !== 'inactive') {
      try { recorder.stop() } catch { /* ignore */ }
    }
    cleanupStream()
    if (objectUrl.value) URL.revokeObjectURL(objectUrl.value)
  })

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
