import { computed, onBeforeUnmount, ref } from 'vue'

export function useAudioController() {
  const playing = ref(false)
  const currentTime = ref(0)
  const duration = ref(0)
  const progress = ref(0)
  const activeSource = ref('')
  const error = ref('')
  let audio = null
  let generation = 0

  function ensureAudio() {
    if (audio) return audio
    audio = new Audio()
    audio.preload = 'metadata'
    audio.addEventListener('play', () => { playing.value = true })
    audio.addEventListener('pause', () => { playing.value = false })
    audio.addEventListener('ended', () => {
      playing.value = false
      currentTime.value = duration.value
      progress.value = 1
    })
    audio.addEventListener('timeupdate', () => {
      currentTime.value = audio.currentTime || 0
      duration.value = Number.isFinite(audio.duration) ? audio.duration : 0
      progress.value = duration.value > 0 ? Math.min(currentTime.value / duration.value, 1) : 0
    })
    audio.addEventListener('loadedmetadata', () => {
      duration.value = Number.isFinite(audio.duration) ? audio.duration : 0
    })
    audio.addEventListener('error', () => {
      error.value = '音频加载失败'
      playing.value = false
    })
    return audio
  }

  async function play({ url, source = 'page' }) {
    if (!url) {
      error.value = '当前没有可用音频'
      return false
    }
    stopInternal(false)
    const token = ++generation
    error.value = ''
    const element = ensureAudio()
    activeSource.value = source
    element.src = url
    element.currentTime = 0
    try {
      await element.play()
      if (token !== generation) {
        element.pause()
        return false
      }
      return true
    } catch {
      if (token === generation) {
        error.value = '音频播放被浏览器拦截或加载失败'
        activeSource.value = ''
      }
      return false
    }
  }

  function togglePause() {
    if (!audio) return
    if (audio.paused) audio.play().catch(() => { error.value = '音频播放失败' })
    else audio.pause()
  }

  function stopInternal(resetSource = true) {
    generation += 1
    if (audio) {
      audio.pause()
      try { audio.currentTime = 0 } catch { /* no source */ }
    }
    playing.value = false
    currentTime.value = 0
    duration.value = 0
    progress.value = 0
    if (resetSource) activeSource.value = ''
  }

  function stop() {
    error.value = ''
    stopInternal(true)
  }

  onBeforeUnmount(() => {
    stopInternal(true)
    if (audio) {
      audio.removeAttribute('src')
      audio.load()
      audio = null
    }
  })

  return {
    playing: computed(() => playing.value),
    currentTime: computed(() => currentTime.value),
    duration: computed(() => duration.value),
    progress: computed(() => progress.value),
    activeSource: computed(() => activeSource.value),
    error: computed(() => error.value),
    play,
    togglePause,
    stop,
  }
}


