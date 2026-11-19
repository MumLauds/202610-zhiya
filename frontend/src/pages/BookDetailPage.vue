<script setup>
import { bookStatusLabel } from '../data/preview'
defineProps({
  book: { type: Object, default: null },
  loading: { type: Boolean, default: false },
})

const emit = defineEmits(['back', 'start'])
</script>

<template>
  <main class="detail-page page-width">
    <div class="detail-toolbar">
      <button class="secondary" type="button" @click="emit('back')">← 返回绘本馆</button>
      <span class="detail-breadcrumb">{{ book?.topics?.slice(0, 2).join(' / ') || '绘本详情' }}</span>
    </div>
    <div v-if="loading" class="reader-loading"><div class="spinner" /></div>
    <article v-else-if="book" class="detail-card">
      <div class="detail-cover-wrap">
        <div class="detail-cover"><img :src="book.cover" :alt="`${book.title}封面`"></div>
      </div>
      <div class="detail-copy">
        <span class="pill">{{ bookStatusLabel(book.status) }} · 亲子共读</span>
        <h1>{{ book.title }}</h1>
        <p class="detail-author">作者：{{ book.author }}</p>
        <div class="book-meta">
          <span>{{ book.ageLabel }}</span>
          <span>{{ book.pageCount }} 页</span>
          <span v-for="topic in book.topics" :key="topic">{{ topic }}</span>
        </div>
        <p class="detail-summary">{{ book.summary }}</p>
        <blockquote v-if="book.quote" class="detail-quote">{{ book.quote }}</blockquote>
        <div class="detail-actions">
          <button class="primary" type="button" @click="emit('start', book)">开始阅读</button>
          <button class="secondary" type="button" @click="emit('back')">继续选书</button>
        </div>
      </div>
    </article>
    <div v-else class="empty-state"><div><h2>绘本暂不可用</h2><p>可能尚未发布，或当前账号没有访问权限。</p></div></div>
  </main>
</template>
