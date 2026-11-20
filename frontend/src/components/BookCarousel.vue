<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { roundedTrapezoidPath } from '../utils/coverShape'

const props = defineProps({
  books: { type: Array, required: true },
  selectedId: { type: String, default: '' },
})

const emit = defineEmits(['select'])
const wheel = ref(null)
const dots = ref(null)
const panelWidth = ref(900)
const wheelSpread = ref(.77)
const coverClipPath = roundedTrapezoidPath(87.9, 100)
let observer = null

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

onMounted(() => {
  if (!wheel.value) return
  panelWidth.value = wheel.value.clientWidth
  observer = new ResizeObserver(([entry]) => {
    panelWidth.value = entry.contentRect.width
    revealSelectedDot()
  })
  observer.observe(wheel.value)
})

onBeforeUnmount(() => observer?.disconnect())

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
  emit('select', book.id)
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
      <div ref="wheel" class="book-wheel">
        <button
          v-for="(book, index) in books"
          :key="book.id"
          class="wheel-book"
          :class="{
            selected: book.id === selectedId,
            'is-center': book.id === selectedId,
            'is-side': offsetFor(index) !== 0 && Math.abs(offsetFor(index)) <= 2,
            'is-hidden': Math.abs(offsetFor(index)) > 2,
          }"
          :style="styleFor(index)"
          type="button"
          :aria-label="`选择《${book.title}》`"
          :aria-current="book.id === selectedId ? 'true' : undefined"
          @click="choose(book)"
        >
          <span class="wheel-book-shell">
            <img class="book-shell" src="/assets/figma-prototype/book-shell.png" alt="">
            <span class="wheel-cover"><img :src="book.cover" :alt="`${book.title}封面`"></span>
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
