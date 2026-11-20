<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { countReadPages } from '../src/data/readingProgress'
import demoBooks from './data/demo-books.json'
import ReaderView from './components/ReaderView.vue'
import ReadingSummary from './components/ReadingSummary.vue'
import BookDetailView from './components/BookDetailView.vue'
import { DemoReadingSessionsApi, DemoVoiceApi } from './api/demo'
import { ReadingSessionsApi } from './api/readingSessions'
import { VoiceApi } from './api/voice'
import { ApiError, readCookie, requestJson } from './api/client'
import { LiveBooksApi } from './api/liveBooks'

const STORAGE_KEY = 'zhiya_vue_demo_reading_records'
const SHELF_KEY = 'zhiya_vue_private_shelf'
const FAVORITE_KEY = 'zhiya_vue_favorites'
const RECENT_KEY = 'zhiya_vue_recent_books'
const configuredAssetBase = import.meta.env.VITE_ASSET_BASE || import.meta.env.BASE_URL
const assetBase = configuredAssetBase.endsWith('/') ? configuredAssetBase : `${configuredAssetBase}/`
const resolveAsset = (value) => value?.startsWith('/') ? `${assetBase}${value.slice(1)}` : value
const placeholder = resolveAsset('/assets/image-placeholder.svg')
const books = demoBooks.books.map((book) => ({
  ...book,
  cover: resolveAsset(book.cover),
  pages: book.pages.map((page) => ({ ...page, image: resolveAsset(page.image), audio: resolveAsset(page.audio) })),
}))
const mode = ref('demo')
const activeTab = ref('home')
const selectedBook = ref(books[0])
const detailBook = ref(null)
const detailOrigin = ref('home')
const readerConfig = ref(null)
const childProfileId = ref(1)
const storyVersionId = ref('')
const sessionId = ref('')
const searchQuery = ref('')
const libraryAge = ref('全部')
const libraryTheme = ref('全部')
const searchInput = ref(null)
const recommendationIndex = ref(0)
const originalAge = ref('3-4 岁')
const originalTheme = ref('勇气')
const originalCharacter = ref('小狐狸')
const originalScene = ref('雨后森林')
const originalNotice = ref('')
const demoRecords = ref(loadRecords())
const liveRecords = ref([])
const liveBooks = ref([])
const liveBookPage = ref(0)
const historyPage = ref(0)
const moreLiveBooks = ref(true)
const moreHistory = ref(true)
const booksPending = ref(false)
const historyPending = ref(false)
const readerPending = ref(false)
const booksError = ref('')
const historyError = ref('')
const readerError = ref('')
const pageSize = 20
let accountCookie = ''
let accountScope
let accountTimer
let historyGeneration = 0
const liveFilteredBooks = computed(() => liveBooks.value.filter((book) =>
  book.title.toLowerCase().includes(searchQuery.value.trim().toLowerCase())))
const recordCount = computed(() => mode.value === 'live' ? liveRecords.value.length : demoRecords.value.length)
const shelfIds = ref(loadShelf())
const favoriteIds = ref(loadFavorites())
const recentBookIds = ref(loadRecent())
const validId = (value) => Number.isSafeInteger(value) && value > 0
const liveReady = computed(() => validId(Number(sessionId.value)) ||
  (validId(childProfileId.value) && validId(storyVersionId.value)))
const ageOptions = ['全部', '3-4 岁', '4-5 岁', '5-6 岁']
const themeOptions = ['全部', ...new Set(books.flatMap((book) => book.themes))]
const shelfBooks = computed(() => books.filter((book) => shelfIds.value.includes(book.storyVersionId)))
const recommendedBook = computed(() => books[recommendationIndex.value % books.length])
const recentBooks = computed(() => recentBookIds.value.map((id) => books.find((book) => book.storyVersionId === id)).filter(Boolean).slice(0, 6))
const rankedBooks = computed(() => [...books].sort((a, b) => a.storyVersionId - b.storyVersionId).slice(0, 8))
const filteredBooks = computed(() => {
  const query = searchQuery.value.trim().toLowerCase()
  return books.filter((book) => {
    const matchesQuery = !query || [book.title, book.bookNo, ...book.themes].some((value) => String(value).toLowerCase().includes(query))
    const matchesAge = libraryAge.value === '全部' || book.age === libraryAge.value
    const matchesTheme = libraryTheme.value === '全部' || book.themes.includes(libraryTheme.value)
    return matchesQuery && matchesAge && matchesTheme
  })
})
const navItems = [
  { id: 'home', label: '首页', icon: 'M3 11 12 4l9 7v9h-6v-6H9v6H3z' },
  { id: 'library', label: '绘本馆', icon: 'M4 4h5v16H4zM10 4h4v16h-4zM17 5h3v15h-3z' },
  { id: 'original', label: '原创故事', icon: 'M6 3h9l4 4v14H6zM15 3v5h5M9 13h7M9 17h5' },
  { id: 'growth', label: '成长', icon: 'M5 19V9M12 19V4M19 19v-7' },
  { id: 'profile', label: '我的', icon: 'M12 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM5 20c.7-4 3-6 7-6s6.3 2 7 6' },
]

function loadRecords() {
  try {
    const value = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]')
    return Array.isArray(value) ? value : []
  } catch { return [] }
}
function loadShelf() {
  try {
    const value = JSON.parse(localStorage.getItem(SHELF_KEY) || '[]')
    return Array.isArray(value) ? value : []
  } catch { return [] }
}
function saveRecords() { localStorage.setItem(STORAGE_KEY, JSON.stringify(demoRecords.value.slice(0, 30))) }
function saveShelf() { localStorage.setItem(SHELF_KEY, JSON.stringify(shelfIds.value)) }
function loadFavorites() {
  try { const value = JSON.parse(localStorage.getItem(FAVORITE_KEY) || '[]'); return Array.isArray(value) ? value : [] } catch { return [] }
}
function saveFavorites() { localStorage.setItem(FAVORITE_KEY, JSON.stringify(favoriteIds.value)) }
function loadRecent() {
  try { const value = JSON.parse(localStorage.getItem(RECENT_KEY) || '[]'); return Array.isArray(value) ? value : [] } catch { return [] }
}
function recordRecent(book) {
  recentBookIds.value = [book.storyVersionId, ...recentBookIds.value.filter((id) => id !== book.storyVersionId)].slice(0, 12)
  localStorage.setItem(RECENT_KEY, JSON.stringify(recentBookIds.value))
}
function selectTab(tab) { activeTab.value = tab; detailBook.value = null; readerError.value = '' }
function chooseBook(book) { selectedBook.value = book }
function openBookDetail(book, origin = activeTab.value) { chooseBook(book); recordRecent(book); selectedBook.value = book; detailOrigin.value = origin; detailBook.value = book; activeTab.value = 'detail' }
function closeDetail() { activeTab.value = detailOrigin.value; detailBook.value = null }
function nextRecommendation() { recommendationIndex.value = (recommendationIndex.value + 1) % books.length }
function recommendationReason(book) {
  const first = book.themes[0]
  if (first === '勇气') return '一个关于害怕、尝试和一点点长大的温柔故事。'
  if (first === '友谊') return '适合边读边聊：朋友之间怎样靠近、倾听和互相帮助。'
  if (first === '情绪安抚') return '适合睡前慢慢读，帮助孩子识别情绪、放松下来。'
  if (first === '自然观察') return '从画面里的小动物和天气出发，引导孩子说出自己的发现。'
  return '留出想象空间，邀请孩子一起补完故事。'
}
function toggleShelf(book) {
  const index = shelfIds.value.indexOf(book.storyVersionId)
  if (index >= 0) shelfIds.value.splice(index, 1)
  else shelfIds.value.push(book.storyVersionId)
  saveShelf()
}
function isInShelf(book) { return shelfIds.value.includes(book.storyVersionId) }
function isFavorite(book) { return favoriteIds.value.includes(book.storyVersionId) }
function toggleFavorite(book) {
  const index = favoriteIds.value.indexOf(book.storyVersionId)
  if (index >= 0) favoriteIds.value.splice(index, 1)
  else favoriteIds.value.push(book.storyVersionId)
  saveFavorites()
}
function startDemo(book = selectedBook.value) {
  selectedBook.value = book
  readerConfig.value = { key: `demo-${book.storyVersionId}-${Date.now()}`, mode: 'demo', book, sessionApi: new DemoReadingSessionsApi({ book }), voiceApi: new DemoVoiceApi(), childProfileId: 1, storyVersionId: book.storyVersionId, initialSessionId: null }
}
function imageFallback(event) {
  if (event.target.getAttribute('src') !== placeholder) event.target.src = placeholder
}

function scopeIsCurrent(scope) {
  return scope && accountScope === scope && !scope.signal.aborted && mode.value === 'live' &&
    readCookie('zhiya_csrf') === accountCookie
}

function accountRequest(scope) {
  const csrfToken = accountCookie
  return async (path, options) => {
    if (!scopeIsCurrent(scope)) throw new ApiError('登录状态已变更')
    const signal = options?.signal ? AbortSignal.any([scope.signal, options.signal]) : scope.signal
    const result = await requestJson(path, { ...options, signal, csrfToken })
    if (!scopeIsCurrent(scope)) throw new ApiError('登录状态已变更')
    return result
  }
}

function invalidateHistory() {
  historyGeneration += 1
  liveRecords.value = []
  historyPage.value = 0
  moreHistory.value = true
  historyPending.value = false
  historyError.value = ''
}

function clearLiveData() {
  invalidateHistory()
  liveBooks.value = []
  liveBookPage.value = 0
  moreLiveBooks.value = true
  booksPending.value = readerPending.value = false
  booksError.value = readerError.value = ''
}

function resetLiveInputs() {
  childProfileId.value = 1
  storyVersionId.value = sessionId.value = ''
}

function refreshLiveView() {
  if (mode.value !== 'live') return
  if ((activeTab.value === 'library' || activeTab.value === 'growth') && !liveBookPage.value && !booksError.value) void loadLiveBooks()
  if (activeTab.value === 'growth' && !historyPage.value && !historyError.value) void loadHistory()
}

function syncAccount() {
  const cookie = readCookie('zhiya_csrf')
  if (mode.value !== 'live' || (accountScope && cookie === accountCookie)) return
  const changed = Boolean(accountScope)
  accountScope?.abort()
  accountCookie = cookie
  accountScope = new AbortController()
  if (changed) {
    resetLiveInputs()
    if (readerConfig.value) exitReader()
  }
  clearLiveData()
  refreshLiveView()
  return changed
}

watch(mode, () => {
  accountScope?.abort()
  accountScope = undefined
  resetLiveInputs()
  if (readerConfig.value) exitReader()
  if (activeTab.value === 'detail' || activeTab.value === 'shelf') selectTab('home')
  clearLiveData()
  syncAccount()
}, { flush: 'sync' })
watch(activeTab, refreshLiveView)
onMounted(() => {
  window.addEventListener('focus', syncAccount)
  document.addEventListener('visibilitychange', syncAccount)
  accountTimer = setInterval(syncAccount, 1000)
})
onUnmounted(() => {
  window.removeEventListener('focus', syncAccount)
  document.removeEventListener('visibilitychange', syncAccount)
  clearInterval(accountTimer)
  accountScope?.abort()
})

async function loadLiveBooks() {
  if (booksPending.value || !moreLiveBooks.value) return
  const scope = accountScope
  const page = liveBookPage.value + 1
  booksPending.value = true
  booksError.value = ''
  try {
    const items = await new LiveBooksApi(placeholder, accountRequest(scope)).list(page, pageSize)
    if (!scopeIsCurrent(scope)) return
    liveBooks.value.push(...items)
    liveBookPage.value = page
    moreLiveBooks.value = items.length === pageSize
  } catch (error) {
    if (scopeIsCurrent(scope)) booksError.value = error.message
  } finally {
    if (scopeIsCurrent(scope)) booksPending.value = false
  }
}

async function loadHistory() {
  if (historyPending.value || !moreHistory.value) return
  const scope = accountScope
  const page = historyPage.value + 1
  const generation = ++historyGeneration
  const isCurrent = () => scopeIsCurrent(scope) && generation === historyGeneration
  historyPending.value = true
  historyError.value = ''
  try {
    const { data } = await new ReadingSessionsApi(accountRequest(scope)).list({ page, pageSize })
    if (!isCurrent()) return
    liveRecords.value.push(...data.items)
    historyPage.value = page
    moreHistory.value = data.items.length === pageSize
  } catch (error) {
    if (isCurrent()) historyError.value = error.message
  } finally {
    if (isCurrent()) historyPending.value = false
  }
}

function historyBook(record) {
  return liveBooks.value.find((book) => book.storyVersionId === record.storyVersionId)
}

async function startLive({ versionId = storyVersionId.value, restoreId = sessionId.value === '' ? null : Number(sessionId.value) } = {}) {
  if (syncAccount() || readerPending.value) return
  const profileId = childProfileId.value
  if (restoreId !== null && !validId(restoreId)) {
    readerError.value = '请输入有效的恢复会话 ID'
    return
  }
  if (restoreId === null && (!validId(profileId) || !validId(versionId))) {
    readerError.value = '请输入有效的儿童档案 ID 和故事版本 ID'
    return
  }
  const scope = accountScope
  const origin = activeTab.value
  const request = accountRequest(scope)
  const sessionApi = new ReadingSessionsApi(request)
  readerPending.value = true
  readerError.value = ''
  try {
    let initialSnapshot = null
    if (restoreId !== null) {
      const result = await sessionApi.get(restoreId)
      initialSnapshot = result.data
      if (initialSnapshot?.session?.readingSessionId !== restoreId || !validId(initialSnapshot.session.storyVersionId)) {
        throw new ApiError('服务端未返回该会话的绘本版本')
      }
      versionId = initialSnapshot.session.storyVersionId
      if (initialSnapshot.session.status === 'FINISHED') {
        if (scopeIsCurrent(scope) && origin === activeTab.value) {
          readerConfig.value = {
            mode: 'live', initialSnapshot, sessionApi,
            book: historyBook(initialSnapshot.session) ?? { title: `绘本版本 ${versionId}` },
          }
        }
        return
      }
    }
    const book = await new LiveBooksApi(placeholder, request).get(versionId)
    if (!scopeIsCurrent(scope) || origin !== activeTab.value) return
    if (initialSnapshot && !book.pages.some((page) => page.page === initialSnapshot.session.currentPage)) {
      throw new ApiError('服务端会话的当前页在该绘本版本中不可用')
    }
    readerConfig.value = {
      key: `live-${versionId}-${Date.now()}`, mode: 'live', book, sessionApi,
      voiceApi: new VoiceApi(request), childProfileId: profileId, storyVersionId: versionId,
      initialSessionId: initialSnapshot?.session.readingSessionId ?? null, initialSnapshot,
    }
  } catch (error) {
    if (scopeIsCurrent(scope) && origin === activeTab.value) readerError.value = error.message
  } finally {
    if (scopeIsCurrent(scope)) readerPending.value = false
  }
}

function exitReader() {
  if (readerConfig.value?.mode === 'live') invalidateHistory()
  readerConfig.value = null
  activeTab.value = 'home'
}
function handleReadingFinished({ book, session, report }) {
  if (readerConfig.value?.mode !== 'demo') return
  demoRecords.value.unshift({ id: `record-${Date.now()}`, title: book.title, cover: book.cover, pages: session.visitedPageNumbers?.length ?? 0, summary: report?.summary || '', date: new Date().toLocaleDateString('zh-CN'), revision: session.revision })
  saveRecords()
}
async function openSearch() { activeTab.value = 'library'; detailBook.value = null; await nextTick(); searchInput.value?.focus() }
function useStoryTemplate(book) { chooseBook(book); startDemo(book) }
function generateOriginalDraft() { originalNotice.value = `已保留创作参数：${originalAge.value} / ${originalTheme.value} / ${originalCharacter.value} / ${originalScene.value}。当前后端尚未开放故事生成接口，因此不伪造草稿内容。` }
</script>

<template>
  <main v-if="readerConfig?.initialSnapshot?.session.status === 'FINISHED'" class="reader-view reader-content">
    <ReadingSummary
      :key="readerConfig.initialSnapshot.session.readingSessionId"
      v-model:report="readerConfig.initialSnapshot.report"
      :book="readerConfig.book"
      :session="readerConfig.initialSnapshot.session"
      :session-api="readerConfig.sessionApi"
      @exit="exitReader"
    />
  </main>
  <ReaderView v-else-if="readerConfig" :key="readerConfig.key" :book="readerConfig.book" :mode="readerConfig.mode" :session-api="readerConfig.sessionApi" :voice-api="readerConfig.voiceApi" :child-profile-id="readerConfig.childProfileId" :story-version-id="readerConfig.storyVersionId" :initial-session-id="readerConfig.initialSessionId" :initial-snapshot="readerConfig.initialSnapshot" @exit="exitReader" @finished="handleReadingFinished" />

  <div v-else class="library-shell">
    <header v-if="activeTab !== 'detail'" class="app-header">
      <button class="brand-lockup app-brand" type="button" aria-label="返回首页" @click="selectTab('home')">
        <span class="brand-mark">芽</span><span><strong>知芽</strong><small>亲子共读</small></span>
      </button>
      <div class="app-header-actions">
        <button class="icon-action" type="button" aria-label="搜索绘本" @click="openSearch">⌕</button>
        <button class="mode-mini" type="button" @click="mode = mode === 'demo' ? 'live' : 'demo'">{{ mode === 'demo' ? '演示' : '实机' }}</button>
        <button class="avatar-button" type="button" aria-label="家长中心" @click="selectTab('profile')">禾</button>
      </div>
    </header>

    <main class="mobile-main">
      <p v-if="mode === 'live' && readerError" class="inline-error" role="alert">{{ readerError }}</p>
      <template v-if="activeTab === 'home'">
        <section v-if="mode === 'demo'" class="ranking-section">
          <div class="ranking-head">
            <div><p class="eyebrow">知芽精选</p><h1>今日推荐</h1></div>
            <button class="text-button" type="button" @click="selectTab('library')">完整榜单 →</button>
          </div>
          <div class="ranking-grid">
            <button v-for="book in rankedBooks" :key="book.storyVersionId" class="ranking-item" type="button" @click="openBookDetail(book, 'home')">
              <img :src="book.cover" :alt="book.title">
              <span class="ranking-copy"><strong>{{ book.title }}</strong><small>{{ book.age }} · {{ book.themes[0] }}</small></span>
            </button>
          </div>
        </section>
        <section v-if="mode === 'live'" class="live-panel">
          <div class="panel-title"><div><p class="eyebrow">实机会话</p><h2>连接本地后端</h2></div><span class="status-dot">API</span></div>
          <p class="panel-copy">需先登录同一后端。填写已有会话 ID 时优先恢复；留空则创建新的亲子阅读会话。</p>
          <form class="setup-form" @submit.prevent="startLive()">
            <label><span>儿童档案 ID</span><input v-model.number="childProfileId" type="number" min="1" :required="!sessionId" inputmode="numeric"></label>
            <label><span>故事版本 ID</span><input v-model.number="storyVersionId" type="number" min="1" :required="!sessionId" inputmode="numeric"></label>
            <label class="wide-field"><span>恢复会话 ID（可选）</span><input v-model="sessionId" type="number" min="1" placeholder="例如 401" inputmode="numeric"></label>
            <button class="button button-primary button-wide" type="submit" :disabled="!liveReady || readerPending">{{ readerPending ? '加载绘本…' : '进入实机阅读' }}</button>
            <button class="button button-ghost button-wide" type="button" @click="selectTab('library')">打开实机绘本馆</button>
          </form>
        </section>
        <section v-if="mode === 'demo'" class="recent-section">
          <div class="ranking-head compact-head"><div><p class="eyebrow">继续上次</p><h1>最近阅读</h1></div><button class="text-button" type="button" @click="selectTab('library')">查看全部 →</button></div>
          <div v-if="recentBooks.length" class="recent-grid">
            <button v-for="book in recentBooks" :key="book.storyVersionId" class="recent-book" type="button" @click="openBookDetail(book, 'home')">
              <img :src="book.cover" :alt="book.title"><span><strong>{{ book.title }}</strong><small>{{ book.age }} · {{ book.themes[0] }}</small></span>
            </button>
          </div>
          <div v-else class="recent-empty">
            <span>叶</span><p>打开一本绘本后，最近阅读会出现在这里。</p>
            <button class="button button-ghost button-small" type="button" @click="selectTab('library')">去选一本</button>
          </div>
        </section>
      </template>


      <template v-else-if="activeTab === 'library' && mode === 'live'">
        <section class="inner-page-head"><div><p class="eyebrow">实机绘本馆</p><h1>找一本一起读</h1></div></section>
        <label class="search-box"><span>⌕</span><input ref="searchInput" v-model="searchQuery" type="search" placeholder="搜索已加载的书名"></label>
        <label>儿童档案 ID <input v-model.number="childProfileId" type="number" min="1" inputmode="numeric"></label>
        <p v-if="booksError" class="inline-error" role="alert">{{ booksError }}</p>
        <p v-if="booksPending" role="status">加载绘本中…</p>
        <p v-else-if="!liveFilteredBooks.length && !booksError">没有匹配的绘本。</p>
        <div class="library-phone-grid">
          <button v-for="book in liveFilteredBooks" :key="book.storyVersionId" class="library-phone-card" type="button" :disabled="readerPending" @click="startLive({ versionId: book.storyVersionId, restoreId: null })">
            <img :key="book.cover" :src="book.cover" :alt="book.title" @error="imageFallback"><strong>{{ book.title }}</strong>
          </button>
        </div>
        <p v-if="readerPending" role="status">加载阅读内容…</p>
        <button v-if="moreLiveBooks && !booksPending" class="button button-ghost" type="button" @click="loadLiveBooks">{{ booksError ? '重试' : '加载更多绘本' }}</button>
      </template>

      <template v-else-if="activeTab === 'library'">
        <section class="inner-page-head"><div><p class="eyebrow">绘本馆</p><h1>找一本一起读</h1></div><span>{{ filteredBooks.length }} 本</span></section>
        <label class="search-box"><span>⌕</span><input ref="searchInput" v-model="searchQuery" type="search" placeholder="搜索书名、主题或编号"></label>
        <div class="filter-row"><button v-for="age in ageOptions" :key="age" :class="{ active: libraryAge === age }" type="button" @click="libraryAge = age">{{ age }}</button></div>
        <div class="filter-row secondary"><button v-for="theme in themeOptions" :key="theme" :class="{ active: libraryTheme === theme }" type="button" @click="libraryTheme = theme">{{ theme }}</button></div>
        <div v-if="filteredBooks.length" class="library-phone-grid"><button v-for="book in filteredBooks" :key="book.storyVersionId" class="library-phone-card" :class="{ selected: selectedBook.storyVersionId === book.storyVersionId }" type="button" @click="openBookDetail(book, 'library')"><img :src="book.cover" :alt="book.title"><strong>{{ book.title }}</strong><small>{{ book.age }} · {{ book.themes[0] }}</small></button></div>
        <div v-else class="empty-state compact"><span>⌕</span><strong>没有找到匹配绘本</strong><p>换一个年龄或主题试试。</p></div>
        <div class="library-start-bar"><div><small>已选择</small><strong>{{ selectedBook.title }}</strong></div><button class="button button-primary" type="button" @click="startDemo()">开始阅读</button></div>
      </template>

      <template v-else-if="activeTab === 'original'">
        <section class="inner-page-head"><div><p class="eyebrow">原创故事</p><h1>只属于你们的故事</h1></div></section>
        <section class="story-form">
          <label><span>适读年龄</span><select v-model="originalAge"><option>3-4 岁</option><option>4-5 岁</option><option>5-6 岁</option></select></label>
          <label><span>故事主题</span><select v-model="originalTheme"><option>勇气</option><option>友谊</option><option>想象</option><option>情绪安抚</option><option>自然观察</option></select></label>
          <label><span>主角</span><input v-model="originalCharacter" maxlength="8"></label>
          <label><span>故事场景</span><input v-model="originalScene" maxlength="10"></label>
          <button class="button button-primary button-wide" type="button" @click="generateOriginalDraft">生成故事草稿</button>
        </section>
        <p v-if="originalNotice" class="inline-notice">{{ originalNotice }}</p>
        <section v-if="mode === 'demo'" class="info-panel"><strong>原型模板</strong><p>可以先用现有绘本进入阅读页，检查会话、转写和互动流程。生成式故事接口接入后再替换这里。</p><button class="button button-ghost button-small" type="button" @click="useStoryTemplate(books[0])">用《{{ books[0].title }}》体验</button></section>
      </template>

      <template v-else-if="activeTab === 'growth'">
        <section class="inner-page-head"><div><p class="eyebrow">成长记录</p><h1>每一次共读</h1></div><span>{{ recordCount }} 条</span></section>
        <template v-if="mode === 'live'">
          <p v-if="historyError" class="inline-error" role="alert">{{ historyError }}</p>
          <p v-if="historyPending" role="status">加载阅读记录中…</p>
          <div v-if="liveRecords.length" class="record-list">
            <article v-for="record in liveRecords" :key="record.readingSessionId" class="record-card">
              <img :src="historyBook(record)?.cover || placeholder" alt="" @error="imageFallback">
              <div>
                <strong>{{ historyBook(record)?.title || `绘本版本 ${record.storyVersionId}` }}</strong>
                <span>{{ countReadPages(record) }} 页 · {{ { CREATED: '未开始', IN_PROGRESS: '进行中', FINISHED: '已完成', INTERRUPTED: '已中断' }[record.status] }}</span>
                <button class="button button-ghost button-small" type="button" :disabled="readerPending" @click="startLive({ restoreId: record.readingSessionId })">打开会话 {{ record.readingSessionId }}</button>
              </div>
            </article>
          </div>
          <p v-else-if="!historyPending && !historyError">还没有阅读记录。</p>
          <p v-if="readerPending" role="status">加载阅读内容…</p>
          <button v-if="moreHistory && !historyPending" class="button button-ghost" type="button" @click="loadHistory">{{ historyError ? '重试' : '加载更多记录' }}</button>
        </template>
        <div v-else-if="demoRecords.length" class="record-list"><article v-for="record in demoRecords" :key="record.id" class="record-card"><img :src="record.cover" :alt="record.title"><div><strong>{{ record.title }}</strong><span>{{ record.date }} · {{ record.pages }} 页 · rev {{ record.revision }}</span><p>{{ record.summary || '本次没有可展示的小结内容。' }}</p></div></article></div>
        <div v-else class="empty-state"><span>叶</span><strong>还没有阅读记录</strong><p>完成一次阅读后，记录会出现在这里。</p><button class="button button-primary button-small" type="button" @click="selectTab('library')">去绘本馆</button></div>
      </template>

      <template v-else-if="activeTab === 'detail' && detailBook">
        <BookDetailView :book="detailBook" :in-shelf="isInShelf(detailBook)" :is-favorite="isFavorite(detailBook)" @back="closeDetail" @toggle-shelf="toggleShelf" @toggle-favorite="toggleFavorite" @start="startDemo" />
      </template>

      <template v-else-if="activeTab === 'shelf'">
        <section class="inner-page-head"><div><p class="eyebrow">家长中心</p><h1>我的书架</h1></div><span>{{ shelfBooks.length }} 本</span></section>
        <div v-if="shelfBooks.length" class="library-phone-grid">
          <button v-for="book in shelfBooks" :key="book.storyVersionId" class="library-phone-card" type="button" @click="openBookDetail(book, 'shelf')">
            <img :src="book.cover" :alt="book.title"><strong>{{ book.title }}</strong><small>{{ book.age }} · {{ book.themes[0] }}</small>
          </button>
        </div>
        <div v-else class="empty-state"><span>书</span><strong>书架还是空的</strong><p>在绘本详情页点击“加入书架”。</p><button class="button button-primary button-small" type="button" @click="selectTab('library')">去绘本馆</button></div>
      </template>

      <template v-else-if="activeTab === 'profile'">
        <section class="inner-page-head"><div><p class="eyebrow">家长中心</p><h1>芽芽的阅读空间</h1></div></section>
        <article class="profile-card"><span class="profile-avatar">禾</span><div><strong>芽芽</strong><p>3-4 岁 · 喜欢动物和勇气</p></div></article>
        <section class="settings-list">
          <button v-if="mode === 'demo'" class="setting-row" type="button" @click="selectTab('shelf')"><span>我的书架</span><b>{{ shelfBooks.length }} 本 →</b></button>
          <button class="setting-row" type="button" @click="selectTab('growth')"><span>阅读记录</span><b>{{ mode === 'demo' ? `${recordCount} 条 →` : '查看 →' }}</b></button>
          <div class="setting-row"><span>运行模式</span><b>{{ mode === 'demo' ? '原型演示' : '实机接口' }}</b></div>
          <button class="setting-row" type="button" @click="mode = mode === 'demo' ? 'live' : 'demo'"><span>切换模式</span><b>当前：{{ mode === 'demo' ? '演示' : '实机' }} →</b></button>
        </section>
        <p class="profile-note">账号、头像与儿童档案的真实读写仍以业务后端接口为准；此处保留移动端入口和交互位置。</p>
      </template>
    </main>

    <nav v-if="activeTab !== 'detail'" class="bottom-nav" aria-label="主导航">
      <button v-for="item in navItems" :key="item.id" :class="{ active: activeTab === item.id || (activeTab === 'shelf' && item.id === 'profile') }" type="button" @click="selectTab(item.id)">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path :d="item.icon" /></svg><span>{{ item.label }}</span>
      </button>
    </nav>
  </div>
</template>
