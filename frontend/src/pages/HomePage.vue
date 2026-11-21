<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { readPlan, readPlanProgress, readingProfileKey } from '../data/readingPlan'
import { bookStatusLabel } from '../data/preview'

const props = defineProps({
  books: { type: Array, default: () => [] },
  selectedBookId: { type: String, default: '' },
  activeProfile: { type: Object, default: null },
  auth: { type: Object, required: true },
  booksSource: { type: String, default: 'preview' },
  booksError: { type: String, default: '' },
})

const emit = defineEmits(['select', 'detail', 'start', 'navigate'])
const direction = ref(0)
const selectedIndex = ref(0)
const rotationPaused = ref(false)
const hoveringBooks = ref(false)
const focusWithin = ref(false)
let autoTimer = null
let motionPreference
const AUTO_BOOK_INTERVAL = 3600

const manualIndex = computed(() => props.books.findIndex((book) => book.id === props.selectedBookId))
const selected = computed(() => props.books[selectedIndex.value] || null)
const planKey = computed(() => readingProfileKey(props.auth.accountId, props.activeProfile?.childProfileId))
const plan = computed(() => readPlan(planKey.value, props.activeProfile?.preferredMinutes))
const planProgress = computed(() => readPlanProgress(planKey.value))
const shelfBooks = computed(() => {
  if (!props.books.length) return []
  return [-1, 0, 1, 2, 3].map((offset) =>
    props.books[(selectedIndex.value + offset + props.books.length) % props.books.length],
  )
})

function advanceAutoBook() {
  if (motionPreference?.matches || rotationPaused.value || props.books.length < 2 || document.hidden || hoveringBooks.value || focusWithin.value) return
  selectedIndex.value = (selectedIndex.value + 1) % props.books.length
}

function updateFocus(target) {
  focusWithin.value = Boolean(target?.closest?.('.hero-book-stage, .home-info-card, .featured-shelf, .home-plan'))
}

watch(() => props.books.length, (length) => {
  selectedIndex.value = length ? selectedIndex.value % length : 0
})

watch(manualIndex, (index) => {
  if (index < 0) return
  selectedIndex.value = index
  rotationPaused.value = true
}, { immediate: true })

onMounted(() => {
  motionPreference = typeof window.matchMedia === 'function'
    ? window.matchMedia('(prefers-reduced-motion: reduce)')
    : null
  autoTimer = window.setInterval(advanceAutoBook, AUTO_BOOK_INTERVAL)
})

onBeforeUnmount(() => window.clearInterval(autoTimer))

function moveShelf(step) {
  if (!props.books.length) return
  direction.value = step
  const nextIndex = (selectedIndex.value + step + props.books.length) % props.books.length
  chooseShelf(props.books[nextIndex])
  window.setTimeout(() => { direction.value = 0 }, 340)
}

function chooseShelf(book) {
  if (!book) return
  selectedIndex.value = props.books.indexOf(book)
  rotationPaused.value = true
  emit('select', book.id)
}

</script>

<template>
  <main class="home-page page-width"
    @focusin="updateFocus($event.target)"
    @focusout="updateFocus($event.relatedTarget)">
    <section class="home-hero" aria-labelledby="home-title">
      <div class="home-copy">
        <p class="eyebrow">亲子共读 · 从今天开始</p>
        <h1 id="home-title"><span>和孩子一起</span><span>打开更大的世界</span></h1>
        <p>在绘本中，遇见更有趣的故事。选一本，和孩子慢慢读。</p>
        <div class="home-actions">
          <button class="primary" type="button" @click="emit('navigate', 'library')">去绘本馆 →</button>
          <button v-if="!auth.authenticated" class="secondary" type="button" @click="emit('navigate', 'auth')">登录账号</button>
          <button v-if="books.length > 1" class="secondary home-rotation-toggle" type="button" @click="rotationPaused = !rotationPaused">
            {{ rotationPaused ? '继续自动轮播' : '暂停自动轮播' }}
          </button>
        </div>
        <p v-if="booksError" class="data-source-note">{{ booksError }}</p>
      </div>
      <div class="hero-book-stage" :aria-label="selected ? `本周精选：${selected.title}` : '本周精选绘本'"
        @mouseenter="hoveringBooks = true" @mouseleave="hoveringBooks = false">
        <img class="hero-book-base" src="/assets/figma-prototype/home-book-background.webp" alt="">
        <button v-if="selected" class="hero-book-cover" type="button" :aria-label="`查看《${selected.title}》`" @click="emit('detail', selected)">
          <img :src="selected.cover" :alt="`${selected.title}封面`">
        </button>
      </div>
      <article v-if="selected" class="home-info-card" @mouseenter="hoveringBooks = true" @mouseleave="hoveringBooks = false">
        <span class="pill">{{ bookStatusLabel(selected.status) }} · 亲子共读</span>
        <h2>{{ selected.title }}</h2>
        <p class="author">{{ selected.author }}</p>
        <div class="book-meta">
          <span>{{ selected.ageLabel }}</span>
          <span>{{ selected.pageCount }} 页</span>
          <span>{{ selected.topics.slice(0, 2).join(' · ') }}</span>
        </div>
        <p class="summary">{{ selected.summary }}</p>
        <div class="card-actions">
          <button class="primary" type="button" @click="emit('start', selected)">开始阅读</button>
          <button class="secondary" type="button" @click="emit('detail', selected)">查看详情</button>
        </div>
      </article>
    </section>

    <section class="home-bottom">
      <section class="featured-shelf" aria-labelledby="shelf-title" @mouseenter="hoveringBooks = true" @mouseleave="hoveringBooks = false">
        <div class="shelf-canvas" :class="direction ? `swap-${direction > 0 ? 'next' : 'prev'}` : ''">
          <img class="shelf-surface-art" src="/assets/figma-prototype/shelf-combined.svg" alt="">
          <div class="shelf-title-row">
            <div class="shelf-heading-line">
              <img src="/assets/figma-prototype/shelf-heading.svg" alt="">
              <h2 id="shelf-title">精选绘本</h2>
            </div>
            <button class="text-button" type="button" @click="emit('navigate', 'library')">查看更多 →</button>
          </div>
          <div class="shelf-showcase">
            <button
              v-for="(book, index) in shelfBooks"
              :key="`${book?.id}-${index}`"
              class="shelf-showcase-book"
              :class="['previous', 'featured', 'one', 'two', 'three'][index]"
              type="button"
              :aria-label="book ? `查看《${book.title}》` : '暂无绘本'"
              @click="book && chooseShelf(book)"
            >
              <img v-if="book" :loading="book.id === selected?.id ? 'eager' : 'lazy'" :src="book.cover" :alt="`${book.title}封面`">
            </button>
          </div>
          <button class="shelf-arrow-hit previous" type="button" aria-label="查看前面的绘本" @click="moveShelf(-1)"><span class="arrow-glyph" aria-hidden="true">‹</span></button>
          <button class="shelf-arrow-hit next" type="button" aria-label="查看后面的绘本" @click="moveShelf(1)"><span class="arrow-glyph" aria-hidden="true">›</span></button>
        </div>
      </section>
      <aside class="home-plan" @mouseenter="hoveringBooks = true" @mouseleave="hoveringBooks = false">
        <span class="home-plan-kicker">✦ 今日阅读计划</span>
        <div class="home-plan-time"><strong>{{ planProgress.minutes }}/{{ plan.minutes }}</strong><span>分钟</span></div>
        <progress :value="Math.min(planProgress.minutes, plan.minutes)" :max="plan.minutes" aria-label="今日阅读时长进度" />
        <h2>{{ planProgress.minutes >= plan.minutes ? '今日时长目标已达成！' : '和孩子一起，共读一本绘本吧！' }}</h2>
        <p class="home-plan-counts">今日共读 {{ planProgress.books }}/{{ plan.books }} 本 · 本周 {{ planProgress.weekly }}/{{ plan.weekly }} 天</p>
        <button v-if="selected" class="primary" type="button" @click="emit('start', selected)">开始阅读</button>
        <span class="home-plan-note">{{ auth.authenticated ? '统计本机阅读时长' : '登录并选择儿童档案后记录进度' }}</span>
      </aside>
    </section>
  </main>
</template>
