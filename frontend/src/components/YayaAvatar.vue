<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { measureVoice } from './yayaVoice'

const props = defineProps({
  media: Object,
  speaking: Boolean,
  listening: Boolean,
  thinking: Boolean,
})
const host = ref(null)
const ready = ref(false)
let actor, meter, alive = true
const closedMouth = { open: 0, round: 0 }
function sampleVoice() {
  if (!props.media && !props.speaking) return closedMouth
  const el = props.media
  if (el && (el.paused || el.ended || el.readyState < 3)) return closedMouth
  const level = meter?.sample()
  if (level != null) return level
  const time = el ? el.currentTime : performance.now() / 1000
  const open = .10 + .64 * Math.abs(Math.sin(time * 10) * Math.cos(time * 4))
  return { open, round: open * (.5 + .44 * Math.sin(time * 6.7)) }
}
watch(() => props.media, media => {
  meter?.dispose()
  meter = media && actor ? measureVoice(media) : null
})
onMounted(async () => {
  if (!window.WebGL2RenderingContext) return
  try {
    const { createYaya } = await import('./yayaScene')
    if (!alive) return
    const scene = await createYaya(host.value, props, sampleVoice, () => { ready.value = false })
    if (!alive) { scene.dispose(); return }
    actor = scene
    meter = props.media ? measureVoice(props.media) : null
    ready.value = true
  } catch { /* Keep the illustrated portrait when 3D is unavailable. */ }
})
onBeforeUnmount(() => {
  alive = false
  meter?.dispose()
  actor?.dispose()
})
</script>

<template>
  <button ref="host" class="yaya-avatar" type="button" :disabled="!ready" aria-label="和芽芽挥手打招呼" title="和芽芽挥手打招呼" @click="actor?.wave()">
    <img v-if="!ready" src="/assets/mascot/yaya-reader.webp" alt="芽芽" />
  </button>
</template>

<style scoped>
.yaya-avatar { display: block; position: relative; width: 118px; height: 138px; padding: 0; border: 0; background: transparent; cursor: pointer; }
.yaya-avatar::before { content: ''; position: absolute; inset: auto 19% 1.8%; height: 6%; background: radial-gradient(ellipse, #5f512e36, transparent 70%); }
.yaya-avatar:disabled { opacity: 1; cursor: default; }
.yaya-avatar:focus-visible { outline: 2px solid #397c52; border-radius: 24px; }
.yaya-avatar img, .yaya-avatar :deep(canvas) { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: contain; }
</style>
