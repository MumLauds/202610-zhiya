<script setup>
import { computed, ref } from 'vue'
import BookCarousel from '../components/BookCarousel.vue'
import { bookStatusLabel } from '../data/preview'

const props = defineProps({
  books: { type: Array, default: () => [] },
  selectedId: { type: String, default: '' },
  loading: { type: Boolean, default: false },
  nextPage: { type: Number, default: null },
  source: { type: String, default: 'preview' },
  error: { type: String, default: '' },
})

const emit = defineEmits(['select', 'detail', 'start', 'filter', 'login', 'more'])
const category = ref('')

const ageOptions = [
  ['AGE_3_4', '3–4 岁'],
  ['AGE_4_5', '4–5 岁'],
  ['AGE_5_6', '5–6 岁'],
]
const topicOptions = ['亲子依恋', '安全感', '想象力', '友谊', '自然观察', '勇气', '生命成长'].map((topic) => [topic, topic])
const options = computed(() => category.value === 'age' ? ageOptions : category.value === 'topic' ? topicOptions : [])
const selected = computed(() => props.books.find((book) => book.id === props.selectedId) || props.books[0] || null)

function toggleCategory(value) {
  category.value = category.value === value ? '' : value
  if (!category.value) emit('filter', { ageBand: '', topic: '' })
}

function resetFilters() {
  category.value = ''
  emit('filter', { ageBand: '', topic: '' })
}

function chooseFilter(value) {
  if (category.value === 'age') emit('filter', { ageBand: value, topic: '' })
  if (category.value === 'topic') emit('filter', { ageBand: '', topic: value })
}
</script>

<template>
  <main class="page page-width library-page">
    <div class="library-content">
      <div class="library-top-row">
        <aside class="library-filter-panel" :class="{ expanded: category }" aria-labelledby="library-title">
          <h2 id="library-title">绘本馆</h2>
          <p>展示公开绘本和当前账号已审核的私有绘本</p>
          <div class="library-category-rows">
            <div class="library-category-row">
              <button class="library-category-chip" :class="{ active: !category }" type="button" @click="resetFilters">全部</button>
              <button class="library-category-chip" :class="{ active: category === 'age' }" type="button" :aria-expanded="category === 'age'" @click="toggleCategory('age')">年龄</button>
              <button class="library-category-chip" :class="{ active: category === 'topic' }" type="button" :aria-expanded="category === 'topic'" @click="toggleCategory('topic')">主题</button>
              <button class="library-category-chip" type="button" disabled>阅读时长</button>
            </div>
          </div>
          <div v-if="options.length" class="library-filter-expand">
            <strong>{{ category === 'age' ? '年龄' : '主题' }}：选择具体条件</strong>
            <div class="library-option-list">
              <button class="library-option-chip" type="button" @click="chooseFilter('')">全部</button>
              <button v-for="[value, label] in options" :key="value" class="library-option-chip" type="button" @click="chooseFilter(value)">{{ label }}</button>
            </div>
          </div>
          <p v-if="source === 'preview'" class="filter-api-note">
            {{ error || '登录并后端返回可见绘本后，这里会自动切换为真实数据。' }}
          </p>
        </aside>

        <div v-if="loading && nextPage === null" class="library-feature-card">
          <div class="library-feature-cover skeleton" />
          <div><div class="skeleton" style="height:22px;width:46%" /><div class="skeleton" style="height:38px;width:68%;margin-top:13px" /></div>
        </div>
        <article v-else-if="selected" class="library-feature-card">
          <div class="library-feature-cover"><img :src="selected.cover" :alt="`${selected.title}封面`"></div>
          <div class="library-feature-copy">
            <span class="pill">{{ bookStatusLabel(selected.status) }} · 亲子共读</span>
            <h2>{{ selected.title }}</h2>
            <p>{{ selected.author }} · {{ selected.ageLabel }} · {{ selected.pageCount }} 页</p>
            <div class="reason-box"><strong>内容简介：</strong>{{ selected.summary }}</div>
            <div class="feature-actions">
              <button class="primary" type="button" @click="emit('detail', selected)">查看详情</button>
              <button class="secondary" type="button" @click="emit('start', selected)">开始阅读</button>
            </div>
          </div>
        </article>
        <div v-else class="empty-state">
          <div>
            <h2>没有找到符合条件的可见绘本</h2>
            <p>当前账号可读取的绘本为空，可切换条件或等待内容发布。</p>
            <button class="primary" type="button" @click="emit('filter', { ageBand: '', topic: '' })">查看全部</button>
          </div>
        </div>
      </div>
      <BookCarousel v-if="books.length" :books="books" :selected-id="selected?.id" @select="emit('select', $event)" />
      <div v-if="nextPage !== null">
        <p v-if="error" role="alert">{{ error }}</p>
        <button class="secondary" type="button" :disabled="loading" @click="emit('more')">
          <template v-if="loading">正在加载…</template>
          <template v-else>{{ nextPage === 1 ? '重新加载书架' : '加载更多绘本' }}</template>
        </button>
      </div>
    </div>
  </main>
</template>
