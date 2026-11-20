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
})

const emit = defineEmits(['page-change', 'error'])
const MAX_SPREADS_WITH_DOTS = 12
const SPREADS_PER_WINDOW = 5
const windowStart = computed(() => Math.max(0, Math.min(
  props.startPage - Math.floor(SPREADS_PER_WINDOW / 2),
  props.pages.length - SPREADS_PER_WINDOW,
)))
const visiblePages = computed(() => props.pages.slice(windowStart.value, windowStart.value + SPREADS_PER_WINDOW))
const dotPages = computed(() => props.visitPages ?? props.bookPages ?? props.pages)
const container = ref(null)
const settledImages = ref(new Set())
const imageErrors = ref(new Set())
const currentImage = computed(() => props.pages[props.startPage]?.imageRef)
let book = null
let syncing = false

function showPage(storyIndex) {
  const localIndex = storyIndex - windowStart.value
  if (localIndex < 0 || localIndex >= visiblePages.value.length) return
  syncing = true
  try { book?.turnTo(localIndex * 2) } finally { syncing = false }
}

watch([() => props.startPage, windowStart], ([index]) => showPage(index), { flush: 'post' })

watch([() => props.startPage, windowStart], ([index, start], [, previousStart]) => {
  if (start !== previousStart || !settledImages.value.has(currentImage.value)) {
    settledImages.value.clear()
    return
  }
  const nearby = props.pages.slice(Math.max(0, index - 1), index + 2).map((page) => page.imageRef)
  for (const imageRef of settledImages.value) {
    if (!nearby.includes(imageRef)) settledImages.value.delete(imageRef)
  }
})

function shouldLoadImage(index) {
  const distance = Math.abs(windowStart.value + index - props.startPage)
  return distance === 0 || (
    distance === 1 && (
      !currentImage.value || settledImages.value.has(currentImage.value) || imageErrors.value.has(currentImage.value)
    )
  )
}

function settleImage(imageRef, failed = false) {
  settledImages.value.add(imageRef)
  if (failed) imageErrors.value.add(imageRef)
}

function mountBook() {
  if (!container.value || !window.OpenPageFlip) {
    emit('error', '翻书组件未加载')
    return
  }
  try {
    book = window.OpenPageFlip.createBook(container.value, {
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
// The widget captures its page elements at mount. Sliding a full window reuses the same slots.
watch(() => visiblePages.value.length, () => {
  book?.destroy()
  book = null
  mountBook()
}, { flush: 'post' })

onBeforeUnmount(() => {
  book?.destroy()
  book = null
})

defineExpose({
  showPage,
})
</script>

<template>
  <div ref="container" class="flipbook-book" :inert="disabled" :aria-label="`${title}软页绘本`">
    <template v-for="(page, index) in visiblePages" :key="index">
      <section class="flip-page flip-page--illustration" data-density="soft" :aria-label="`第 ${page.pageNumber} 页插图`">
        <div v-if="!page.imageRef || imageErrors.has(page.imageRef)" class="flip-page-empty">
          <div><h2>{{ page.imageRef ? '插图暂时无法显示' : '本页暂无插图' }}</h2><p>文字内容仍可继续阅读。</p></div>
        </div>
        <img
          v-else-if="shouldLoadImage(index)"
          :key="page.imageRef"
          :src="mediaUrl(page.imageRef, 'reading')"
          :alt="`${title} · 第 ${page.pageNumber} 页`"
          :fetchpriority="windowStart + index === startPage ? 'high' : 'low'"
          draggable="false"
          @load="settleImage(page.imageRef)"
          @error="settleImage(page.imageRef, true)"
        >
      </section>
      <section class="flip-page flip-page--text" data-density="soft" :aria-label="`第 ${page.pageNumber} 页文字`">
        <div class="flip-page-inner flip-page-inner--text">
          <p class="eyebrow">{{ title }} · 第 {{ page.pageNumber }} 页</p>
          <blockquote class="page-quote">{{ page.text }}</blockquote>
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
