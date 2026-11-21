<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { mediaUrl } from '../api/client'

const props = defineProps({
  pages: { type: Array, required: true },
  bookPages: { type: Array, default: null },
  title: { type: String, default: '绘本' },
  pageCount: { type: Number, default: null },
  visitPages: { type: Array, default: null },
  startPage: { type: Number, default: 0 },
  disabled: { type: Boolean, default: false },
  canNarrate: { type: Boolean, default: false },
})

const emit = defineEmits(['page-change', 'narrate', 'error'])
const MAX_SPREADS_WITH_DOTS = 12
const SPREADS_PER_WINDOW = 5
const windowStart = computed(() => Math.max(0, Math.min(
  props.startPage - Math.floor(SPREADS_PER_WINDOW / 2),
  props.pages.length - SPREADS_PER_WINDOW,
)))
const visiblePages = computed(() => props.pages.slice(windowStart.value, windowStart.value + SPREADS_PER_WINDOW))
const dotPages = computed(() => props.visitPages ?? props.bookPages ?? props.pages)
const container = ref(null)
const settledImages = ref(new Map())
const imageErrors = ref(new Set())
const currentImage = computed(() => props.pages[props.startPage]?.imageRef)
const currentImageSettled = computed(() => !currentImage.value ||
  imageErrors.value.has(currentImage.value) ||
  settledImages.value.get(props.startPage) === currentImage.value)
let book = null
let mountedFrames = []
let syncing = false

function showPage(storyIndex) {
  const localIndex = storyIndex - windowStart.value
  if (localIndex < 0 || localIndex >= visiblePages.value.length) return
  syncing = true
  try { book?.turnTo(localIndex * 2) } finally { syncing = false }
}

function flipToPage(storyIndex) {
  const localIndex = storyIndex - windowStart.value
  if (!book?.flipTo || localIndex < 0 || localIndex >= visiblePages.value.length) return null
  if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return null
  return book.flipTo(localIndex * 2)
}

watch([() => props.startPage, visiblePages], () => {
  for (const [index, imageRef] of settledImages.value) {
    if (props.pages[index]?.imageRef !== imageRef || !shouldLoadImage(index)) {
      settledImages.value.delete(index)
    }
  }
})

function shouldLoadImage(index) {
  const distance = Math.abs(index - props.startPage)
  return distance === 0 || (distance === 1 && currentImageSettled.value)
}

function settleImage(index, imageRef, failed = false) {
  if (failed) imageErrors.value.add(imageRef)
  else settledImages.value.set(index, imageRef)
}

function pageFrames() {
  return Array.from(container.value.querySelectorAll(':scope > .flip-page:not([data-opf-clone])'))
}

function mountBook() {
  if (!container.value || !window.OpenPageFlip) {
    emit('error', '翻书组件未加载')
    return
  }
  try {
    const frames = pageFrames()
    if (!frames.length) return
    book = window.OpenPageFlip.createBook(container.value, {
      pages: frames,
      width: 420,
      height: 560,
      size: 'stretch',
      minWidth: 240,
      maxWidth: 460,
      layout: 'auto',
      cover: false,
      startPage: Math.max(0, Math.min(visiblePages.value.length - 1, props.startPage - windowStart.value)) * 2,
      flipDuration: 920,
      easing: (progress) => 1 - ((1 - progress) ** 3),
      shadows: true,
      shadowOpacity: .3,
      drag: true,
      swipe: true,
      hover: true,
      click: 'edges',
    })
    mountedFrames = frames
    book.on('flip', ({ page }) => {
      if (syncing) return
      const bookPage = windowStart.value * 2 + page
      emit('page-change', { storyIndex: Math.floor(bookPage / 2), bookPage })
    })
  } catch (error) {
    emit('error', error.message || '翻书组件初始化失败')
  }
}

onMounted(mountBook)
// Keep overlapping frames mounted and tell the widget when the window's elements change.
watch([() => props.startPage, visiblePages], ([index], [previousIndex]) => {
  if (!book) {
    mountBook()
    return
  }
  const frames = pageFrames()
  if (!frames.length) {
    book.destroy()
    book = null
    mountedFrames = []
    return
  }
  const framesChanged = frames.length !== mountedFrames.length
    || frames.some((frame, offset) => frame !== mountedFrames[offset])
  if (framesChanged) {
    syncing = true
    try {
      book.setPages(frames)
      mountedFrames = frames
    } finally {
      syncing = false
    }
  }
  if (framesChanged || index !== previousIndex) showPage(index)
}, { flush: 'post' })

onBeforeUnmount(() => {
  book?.destroy()
  book = null
})

defineExpose({
  showPage,
  flipToPage,
})
</script>

<template>
  <div ref="container" class="flipbook-book" :inert="disabled" :aria-label="`${title}软页绘本`">
    <template v-for="(page, index) in visiblePages" :key="windowStart + index">
      <section class="flip-page flip-page--illustration" data-density="soft" :aria-label="`第 ${page.pageNumber} 页插图`">
        <div v-if="!page.imageRef || imageErrors.has(page.imageRef)" class="flip-page-empty">
          <div><h2>{{ page.imageRef ? '插图暂时无法显示' : '本页暂无插图' }}</h2><p>文字内容仍可继续阅读。</p></div>
        </div>
        <img
          v-else-if="shouldLoadImage(windowStart + index)"
          :key="page.imageRef"
          :src="mediaUrl(page.imageRef, 'reading')"
          :alt="`${title} · 第 ${page.pageNumber} 页`"
          :class="{ 'flip-image-ready': settledImages.get(windowStart + index) === page.imageRef }"
          :fetchpriority="windowStart + index === startPage ? 'high' : 'low'"
          draggable="false"
          @load="settleImage(windowStart + index, page.imageRef)"
          @error="settleImage(windowStart + index, page.imageRef, true)"
        >
      </section>
      <section class="flip-page flip-page--text" data-density="soft" :aria-label="`第 ${page.pageNumber} 页文字`">
        <div class="flip-page-inner flip-page-inner--text">
          <p class="eyebrow">{{ title }} · 第 {{ page.pageNumber }} 页</p>
          <blockquote class="page-quote">
            <button
              v-if="canNarrate && windowStart + index === startPage"
              type="button"
              class="page-narration-text"
              title="点击朗读本页"
              @click.stop="emit('narrate')"
            >{{ page.text }}</button>
            <template v-else>{{ page.text }}</template>
          </blockquote>
          <div class="page-number">
            <span>第 {{ page.pageNumber }} 页 · 共 {{ pageCount ?? pages.length }} 页</span>
            <template v-if="!visitPages || windowStart + index < visitPages.length">
              <span v-if="dotPages.length <= MAX_SPREADS_WITH_DOTS" class="page-dots"><i v-for="(dot, position) in dotPages" :key="position" :class="{ active: visitPages ? position === windowStart + index : dot.pageNumber === page.pageNumber }" /></span>
              <span v-else-if="visitPages">回看 {{ windowStart + index + 1 }} / {{ visitPages.length }}</span>
            </template>
          </div>
        </div>
      </section>
    </template>
  </div>
</template>
