<script setup>
import { computed } from 'vue'

const props = defineProps({
  book: { type: Object, required: true },
  inShelf: { type: Boolean, default: false },
  isFavorite: { type: Boolean, default: false },
})
const emit = defineEmits(['back', 'start', 'toggle-shelf', 'toggle-favorite'])

const seed = computed(() => Number(props.book.storyVersionId) || 1)
const rating = computed(() => (4.6 + (seed.value % 4) * 0.1).toFixed(1))
const ratingsCount = computed(() => 80 + seed.value * 17)
const likes = computed(() => 320 + seed.value * 23)
const favorites = computed(() => 120 + seed.value * 11)
const introductions = {
  勇气: '一个关于害怕、尝试和一点点长大的温柔故事。适合亲子共读时慢慢观察角色的表情与选择。',
  友谊: '用简单的场景讲清楚朋友之间如何靠近、倾听和互相帮助，适合边读边聊。',
  情绪安抚: '适合睡前阅读的安静故事，帮助孩子识别情绪，也让大人放慢语速陪孩子一起呼吸。',
  自然观察: '从画面里的小动物、天气和植物出发，引导孩子讲述自己看见了什么。',
  想象: '留出很多可以想象的空白，鼓励孩子补充角色接下来会发生的事情。',
}
const intro = computed(() => introductions[props.book.themes[0]] || '一个适合亲子共读的温柔故事，适合边看画面边和孩子聊一聊。')
const comments = computed(() => [
  { user: '禾禾妈妈', text: '画面很温柔，孩子会主动问下一页会发生什么。', meta: '亲子共读 · 4 分钟前' },
  { user: '小树爸爸', text: '拼音和朗读放在一起很好用，孩子会跟着一起念。', meta: '已加入书架 · 昨天' },
  { user: '芽芽家长', text: '适合睡前读，文字长度刚好，不会一下子读太久。', meta: '示例书评 · 2 天前' },
])
</script>

<template>
  <section class="book-detail-page">
    <header class="detail-topbar">
      <button class="topbar-back" type="button" aria-label="返回绘本馆" @click="emit('back')">←</button>
      <span>绘本详情</span>
      <span class="demo-data-tag">示例数据</span>
    </header>

    <div class="detail-scroll">
      <div class="detail-cover-wrap">
        <img :src="book.cover" :alt="book.title">
      </div>
      <div class="detail-title-block">
        <p class="eyebrow">{{ book.bookNo }} · {{ book.age }}</p>
        <h1>{{ book.title }}</h1>
        <div class="detail-author"><span class="author-avatar">芽</span><span>知芽编辑部</span><button type="button" @click="emit('toggle-favorite', book)">{{ isFavorite ? '♥ 已收藏' : '♡ 收藏' }}</button></div>
      </div>

      <dl class="detail-metrics">
        <div><dt>{{ rating }} 分</dt><dd>{{ ratingsCount }} 人点评</dd></div>
        <div><dt>{{ likes }}</dt><dd>获赞</dd></div>
        <div><dt>{{ favorites }}</dt><dd>收藏</dd></div>
      </dl>

      <section class="detail-section">
        <div class="detail-section-head"><h2>简介</h2><div class="detail-tags"><span v-for="theme in book.themes" :key="theme">{{ theme }}</span></div></div>
        <p>{{ intro }}</p>
        <p class="detail-facts">{{ book.pages.length }} 页绘本 · {{ book.age }} · 逐字拼音 · 页面朗读</p>
      </section>

      <section class="detail-section review-section">
        <div class="detail-section-head"><h2>书评 · {{ rating }}</h2><span>更多书评</span></div>
        <article v-for="comment in comments" :key="comment.user" class="review-card">
          <span class="review-avatar">{{ comment.user.slice(0, 1) }}</span>
          <div><strong>{{ comment.user }}</strong><p>{{ comment.text }}</p><small>★★★★★ · {{ comment.meta }}</small></div>
        </article>
      </section>
    </div>

    <div class="detail-action-bar">
      <button class="shelf-action" type="button" @click="emit('toggle-shelf', book)">
        <span>{{ inShelf ? '✓' : '+' }}</span>
        {{ inShelf ? '已加入书架' : '加入书架' }}
      </button>
      <button class="button button-primary detail-read-button" type="button" @click="emit('start', book)">开始阅读</button>
    </div>
  </section>
</template>
