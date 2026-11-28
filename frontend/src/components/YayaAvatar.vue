<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { measureVoice } from './yayaVoice'

const props = defineProps({
  media: Object,
  speaking: Boolean,
  listening: Boolean,
  thinking: Boolean,
})
const host = ref(null)
const ready = ref(false)
const position = ref(null)
const dragging = ref(false)
const floatingStyle = computed(() => {
  if (!position.value) return
  const { left, top, width, height } = position.value
  return { left: `${left}px`, top: `${top}px`, width: `${width}px`, height: `${height}px` }
})
let grab = null
let dragged = false

function place(left, top, { width, height }) {
  position.value = {
    left: Math.min(Math.max(left, 0), Math.max(0, window.innerWidth - width)),
    top: Math.min(Math.max(top, 0), Math.max(0, window.innerHeight - height)),
    width, height,
  }
}

function startDrag(event) {
  if (grab || event.button !== 0) return
  grab = { id: event.pointerId, x: event.clientX, y: event.clientY, rect: host.value.getBoundingClientRect() }
  dragged = false
  host.value.setPointerCapture(event.pointerId)
}

function moveDrag(event) {
  if (grab?.id !== event.pointerId) return
  const dx = event.clientX - grab.x
  const dy = event.clientY - grab.y
  if (!dragged && Math.hypot(dx, dy) < 6) return
  dragged = true
  dragging.value = true
  place(grab.rect.left + dx, grab.rect.top + dy, grab.rect)
}

function endDrag(event) {
  if (grab?.id !== event.pointerId) return
  grab = null
  dragging.value = false
}

function wave(event) {
  if (!dragged || !event.detail) actor?.wave()
}

function moveWithKeys(event) {
  const offsets = { ArrowLeft: [-16, 0], ArrowRight: [16, 0], ArrowUp: [0, -16], ArrowDown: [0, 16] }
  const offset = offsets[event.key]
  if (!offset) return
  event.preventDefault()
  const rect = host.value.getBoundingClientRect()
  place(rect.left + offset[0], rect.top + offset[1], rect)
}

function keepOnScreen() {
  if (position.value) place(position.value.left, position.value.top, position.value)
}
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
  window.addEventListener('resize', keepOnScreen)
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
  window.removeEventListener('resize', keepOnScreen)
  meter?.dispose()
  actor?.dispose()
})
</script>

<template>
  <span v-if="position" class="yaya-avatar-space" aria-hidden="true"
    :style="{ width: floatingStyle.width, height: floatingStyle.height }"></span>
  <button
    ref="host" v-bind="$attrs" class="yaya-avatar" type="button"
    :class="{ 'yaya-avatar--floating': position, 'yaya-avatar--dragging': dragging }"
    :style="floatingStyle" aria-label="芽芽，可拖动，按方向键移动"
    title="拖动芽芽，轻点挥手"
    @pointerdown="startDrag" @pointermove="moveDrag" @pointerup="endDrag"
    @pointercancel="endDrag" @lostpointercapture="endDrag"
    @click="wave" @keydown="moveWithKeys"
  >
    <img v-if="!ready" src="/assets/mascot/yaya-reader.webp" alt="芽芽" draggable="false" />
  </button>
</template>

<style scoped>
.yaya-avatar { display: block; position: relative; width: 118px; height: 138px; padding: 0; border: 0; background: transparent; cursor: grab; touch-action: none; user-select: none; }
.yaya-avatar-space { display: block; }
.yaya-avatar.yaya-avatar--floating { position: fixed; right: auto; bottom: auto; max-width: none; z-index: 35; }
.yaya-avatar.yaya-avatar--dragging { cursor: grabbing; }
.yaya-avatar::before { content: ''; position: absolute; inset: auto 19% 1.8%; height: 6%; background: radial-gradient(ellipse, #5f512e36, transparent 70%); }
.yaya-avatar:focus-visible { outline: 2px solid #397c52; border-radius: 24px; }
.yaya-avatar img, .yaya-avatar :deep(canvas) { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: contain; }
</style>
