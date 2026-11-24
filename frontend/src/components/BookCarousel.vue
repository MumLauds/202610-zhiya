<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { roundedTrapezoidPath } from '../utils/coverShape'

const props = defineProps({
  books: { type: Array, required: true },
  selectedId: { type: String, default: '' },
  starting: Boolean,
})

const emit = defineEmits(['select', 'start'])
const opening = ref(null)
const wheel = ref(null)
const dots = ref(null)
const panelWidth = ref(900)
const wheelSpread = ref(.77)
const coverClipPath = roundedTrapezoidPath(87.9, 100)
const openingDuration = 740
let observer = null
let openingTimer

const selectedIndex = computed(() => Math.max(0, props.books.findIndex((book) => book.id === props.selectedId)))

function revealSelectedDot() {
  const strip = dots.value
  const dot = strip?.children[selectedIndex.value]
  if (!dot) return
  const right = dot.offsetLeft + dot.offsetWidth
  if (dot.offsetLeft < strip.scrollLeft) strip.scrollLeft = dot.offsetLeft
  else if (right > strip.scrollLeft + strip.clientWidth) strip.scrollLeft = right - strip.clientWidth
}

watch([selectedIndex, () => props.books.length], revealSelectedDot, { flush: 'post' })
watch(() => props.starting, (starting) => {
  if (!starting && openingTimer === undefined) opening.value = null
})

onMounted(() => {
  if (!wheel.value) return
  panelWidth.value = wheel.value.clientWidth
  observer = new ResizeObserver(([entry]) => {
    panelWidth.value = entry.contentRect.width
    revealSelectedDot()
  })
  observer.observe(wheel.value)
})

onBeforeUnmount(() => {
  observer?.disconnect()
  clearTimeout(openingTimer)
})

function offsetFor(index) {
  const count = props.books.length
  let offset = index - selectedIndex.value
  if (offset > count / 2) offset -= count
  if (offset < -count / 2) offset += count
  return offset
}

function styleFor(index) {
  const offset = offsetFor(index)
  const distance = Math.abs(offset)
  const direction = Math.sign(offset)
  const compact = panelWidth.value < 620
  const nearOffset = compact ? .46 : .30
  const farOffset = compact ? .82 : .55
  let spec
  if (distance === 0) {
    spec = { x: 0, y: 6, z: 70, scale: 1.12, rotate: 0, opacity: 1, layer: 90 }
  } else if (distance === 1) {
    spec = { x: direction * panelWidth.value * nearOffset, y: 10, z: 10, scale: .78, rotate: -direction * 13, opacity: .82, layer: 60 }
  } else if (distance === 2) {
    spec = { x: direction * panelWidth.value * farOffset, y: 18, z: -40, scale: .58, rotate: -direction * 22, opacity: .48, layer: 30 }
  } else {
    spec = { x: direction * panelWidth.value * (compact ? .98 : .70), y: 24, z: -80, scale: .5, rotate: -direction * 28, opacity: 0, layer: 10 }
  }
  return {
    '--wheel-x': `${spec.x * wheelSpread.value}px`,
    '--wheel-y': `${spec.y * 3}px`,
    '--wheel-z': `${spec.z}px`,
    '--wheel-scale': String(spec.scale),
    '--wheel-rotate-y': `${spec.rotate}deg`,
    '--wheel-opacity': String(spec.opacity),
    '--wheel-layer': String(spec.layer),
    pointerEvents: distance > 2 ? 'none' : 'auto',
  }
}

function choose(book) {
  if (!opening.value && !props.starting) emit('select', book.id)
}

function openBook(book, index) {
  if (opening.value || props.starting) return
  if (index !== selectedIndex.value) return choose(book)
  if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return emit('start', book)
  opening.value = book
  openingTimer = setTimeout(finishOpening, openingDuration + 80)
}

function finishOpening() {
  const book = opening.value
  if (!book || openingTimer === undefined) return
  clearTimeout(openingTimer)
  openingTimer = undefined
  emit('start', book)
  nextTick(() => { if (!props.starting) opening.value = null })
}

function move(step) {
  if (!props.books.length) return
  const next = (selectedIndex.value + step + props.books.length) % props.books.length
  choose(props.books[next])
}
</script>

<template>
  <section class="library-stage" aria-label="绘本轮播">
    <div class="book-wheel-panel">
      <div ref="wheel" class="book-wheel" :style="{ '--wheel-open-duration': `${openingDuration}ms` }">
        <button
          v-for="(book, index) in books"
          :key="book.id"
          class="wheel-book"
          :class="{
            selected: offsetFor(index) === 0,
            'is-opening': opening?.id === book.id,
            'is-center': book.id === selectedId,
            'is-side': offsetFor(index) !== 0 && Math.abs(offsetFor(index)) <= 2,
            'is-hidden': Math.abs(offsetFor(index)) > 2,
          }"
          :style="styleFor(index)"
          type="button"
          :inert="Math.abs(offsetFor(index)) > 2 || undefined"
          :aria-label="`${offsetFor(index) === 0 ? '阅读' : '选择'}《${book.title}》`"
          :aria-current="offsetFor(index) === 0 ? 'true' : undefined"
          :aria-disabled="Boolean(opening || starting)"
          @click="openBook(book, index)"
        >
          <span class="wheel-book-shell">
            <img class="book-shell" src="/assets/figma-prototype/book-shell.webp" alt="">
            <span class="wheel-paper-edge" aria-hidden="true"></span>
            <template v-if="opening?.id === book.id">
              <span class="wheel-page-block" aria-hidden="true"></span>
              <span class="wheel-turning-leaf" aria-hidden="true"></span>
            </template>
            <span class="wheel-cover" @animationend.self="finishOpening">
              <span class="wheel-cover-front"><img v-if="Math.abs(offsetFor(index)) <= 2" :loading="offsetFor(index) === 0 ? 'eager' : 'lazy'" :src="book.cover" :alt="`${book.title}封面`"></span>
              <span v-if="opening?.id === book.id" class="wheel-cover-back" aria-hidden="true"></span>
            </span>
          </span>
        </button>
      </div>
      <div ref="dots" class="carousel-dots" aria-label="绘本位置">
        <button
          v-for="book in books"
          :key="`dot-${book.id}`"
          class="carousel-dot"
          :class="{ active: book.id === selectedId }"
          type="button"
          :aria-label="`切换到《${book.title}》`"
          @click="choose(book)"
        />
      </div>
      <div class="wheel-controls">
        <button class="round-button" type="button" aria-label="切换到上一本" @click="move(-1)"><span class="arrow-glyph" aria-hidden="true">‹</span></button>
        <button class="round-button" type="button" aria-label="切换到下一本" @click="move(1)"><span class="arrow-glyph" aria-hidden="true">›</span></button>
      </div>
    </div>
    <svg class="wheel-cover-clip-defs" aria-hidden="true" width="0" height="0">
      <defs>
        <clipPath id="zhiya-wheel-cover-clip" clipPathUnits="objectBoundingBox">
          <path id="zhiya-wheel-cover-clip-path" :d="coverClipPath" />
        </clipPath>
      </defs>
    </svg>
  </section>
</template>
