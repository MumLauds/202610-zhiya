<script setup>
import { computed, watch } from 'vue'
import { useAudioController } from '../composables/useAudioController'

const props = defineProps({
  src: { type: String, default: '' },
  tokens: { type: Array, default: () => [] },
  mode: { type: String, default: 'demo' },
  disabled: { type: Boolean, default: false },
})
const emit = defineEmits(['highlight', 'media-error', 'before-play'])

const audio = useAudioController((url) => emit('media-error', url))
defineExpose({ stop: audio.stop })
const highlightIndex = computed(() => {
  if (!audio.progress.value || !props.tokens.length) return -1
  return Math.min(props.tokens.length - 1, Math.floor(audio.progress.value * props.tokens.length))
})
watch(highlightIndex, (index) => emit('highlight', index))
watch(() => props.src, () => {
  audio.stop()
  emit('highlight', -1)
})

async function toggle() {
  if (props.disabled) return
  if (!audio.playing.value) emit('before-play')
  if (audio.activeSource.value === 'page') {
    audio.togglePause()
    return
  }
  await audio.play({ url: props.src, source: 'page' })
}

function formatTime(value) {
  if (!Number.isFinite(value)) return '0:00'
  return `${Math.floor(value / 60)}:${String(Math.floor(value % 60)).padStart(2, '0')}`
}
</script>

<template>
  <div class="audio-card">
    <button class="audio-play" type="button" :disabled="disabled || !src" :aria-label="audio.playing.value ? '暂停朗读' : '播放朗读'" @click="toggle">
      {{ audio.playing.value ? 'Ⅱ' : '▶' }}
    </button>
    <div class="audio-meta">
      <div class="audio-heading">
        <strong>{{ audio.playing.value ? '正在朗读' : '听知芽读这一页' }}</strong>
        <span class="simulation-tag">进度模拟</span>
      </div>
      <div class="audio-track" aria-hidden="true">
        <span :style="{ width: `${audio.progress.value * 100}%` }" />
      </div>
      <div class="audio-foot">
        <span>{{ formatTime(audio.currentTime.value) }} / {{ formatTime(audio.duration.value) }}</span>
        <span>{{ mode === 'demo' ? '本地演示音频' : '页面朗读接口' }}</span>
      </div>
      <p v-if="audio.error.value" class="inline-error">{{ audio.error.value }}</p>
      <p class="simulation-note">高亮按音频播放时长模拟，不是字级时间戳对齐。</p>
    </div>
  </div>
</template>
