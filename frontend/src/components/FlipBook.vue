<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { mediaUrl } from '../api/client'

const props = defineProps({
  pages: { type: Array, required: true },
  title: { type: String, default: '绘本' },
  pageCount: { type: Number, default: null },
  reviewingHistory: { type: Boolean, default: false },
  startPage: { type: Number, default: 0 },
  disabled: { type: Boolean, default: false },
})

const emit = defineEmits(['page-change', 'error'])
const MAX_HISTORY_DOTS = 12
const container = ref(null)
const imageErrors = ref(new Set())
let book = null
let syncing = false

function showPage(storyIndex) {
  syncing = true
  try { book?.turnTo(storyIndex * 2) } finally { syncing = false }
}

watch(() => props.startPage, showPage)

function markImageError(pageNumber) {
  imageErrors.value = new Set([...imageErrors.value, pageNumber])
}

onMounted(() => {
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
      startPage: Math.max(0, Math.min(props.pages.length * 2 - 1, props.startPage * 2)),
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
      emit('page-change', {
        storyIndex: Math.max(0, Math.min(props.pages.length - 1, Math.floor(page / 2))),
        bookPage: page,
      })
    })
  } catch (error) {
    emit('error', error.message || '翻书组件初始化失败')
  }
})

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
    <template v-for="(page, index) in pages" :key="index">
      <section class="flip-page flip-page--illustration" data-density="soft" :aria-label="`第 ${page.pageNumber} 页插图`">
        <div v-if="!page.imageRef || imageErrors.has(page.pageNumber)" class="flip-page-empty">
          <div><h2>{{ page.imageRef ? '插图暂时无法显示' : '本页暂无插图' }}</h2><p>文字内容仍可继续阅读。</p></div>
        </div>
        <img v-else :src="mediaUrl(page.imageRef)" :alt="`${title} · 第 ${page.pageNumber} 页`" draggable="false" @error="markImageError(page.pageNumber)">
      </section>
      <section class="flip-page flip-page--text" data-density="soft" :aria-label="`第 ${page.pageNumber} 页文字`">
        <div class="flip-page-inner flip-page-inner--text">
          <p class="eyebrow">{{ title }} · 第 {{ page.pageNumber }} 页</p>
          <blockquote class="page-quote">{{ page.text }}</blockquote>
          <div class="page-number">
            <span>第 {{ page.pageNumber }} 页 · 共 {{ pageCount ?? pages.length }} 页</span>
            <span v-if="reviewingHistory && pages.length > MAX_HISTORY_DOTS">回看 {{ index + 1 }} / {{ pages.length }}</span>
            <span v-else class="page-dots"><i v-for="position in pages.length" :key="position" :class="{ active: position === index + 1 }" /></span>
          </div>
        </div>
      </section>
    </template>
  </div>
</template>
