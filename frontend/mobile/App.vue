<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { ageLabels } from '../src/data/bookMetadata'
import { countReadPages } from '../src/data/readingProgress'
import { parseMobileRoute } from '../src/data/mobileRoute'
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
const { publicSite } = defineProps({ publicSite: Boolean })
const mode = ref(publicSite ? 'live' : 'demo')
const activeTab = ref('home')
const selectedBook = ref(books[0])
const detailBook = ref(null)
const detailOrigin = ref('library')
const readerConfig = ref(null)
let handledHash
let navigation = 0
let readerOrigin = 'home'
const childProfileId = ref(null)
const profiles = ref([])
const profilesPending = ref(false)
const profilesError = ref('')
const activeProfile = computed(() => profiles.value.find((profile) => profile.childProfileId === childProfileId.value))
const profileTab = computed(() => ['home', 'library', 'profile', 'detail'].includes(activeTab.value))
const offlineDemo = window.location.protocol === 'file:'
const searchQuery = ref('')
const libraryAge = ref('全部')
const libraryTheme = ref('全部')
const searchInput = ref(null)
const recommendationIndex = ref(0)
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
let historyGeneration = 0
const liveFilteredBooks = computed(() => liveBooks.value.filter((book) =>
  book.title.toLowerCase().includes(searchQuery.value.trim().toLowerCase())))
const displayedBooks = computed(() => activeTab.value === 'home' ? liveBooks.value : liveFilteredBooks.value)
const recordCount = computed(() => mode.value === 'live' ? liveRecords.value.length : demoRecords.value.length)
const shelfIds = ref(loadShelf())
const favoriteIds = ref(loadFavorites())
const recentBookIds = ref(loadRecent())
const validId = (value) => Number.isSafeInteger(value) && value > 0
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
function selectTab(tab) {
  goTo(`#${tab}`)
}
function chooseBook(book) { selectedBook.value = book }
function openBookDetail(book, origin = activeTab.value) {
  detailOrigin.value = origin
  goTo(`#book/${book.storyVersionId}`)
}
function closeDetail() { selectTab(detailOrigin.value) }

function goTo(hash) {
  if (window.location.hash !== hash) window.history.pushState(null, '', hash)
  applyRoute()
}

function applyRoute() {
  const hash = window.location.hash
  if (handledHash === hash) return
  handledHash = hash
  const route = parseMobileRoute(hash)
  const routeId = ++navigation
  if (readerConfig.value?.mode === 'live') invalidateHistory()
  readerConfig.value = detailBook.value = null
  readerError.value = ''
  readerPending.value = false
  syncAccount()
  if (routeId !== navigation) return

  if (route.name === 'book') {
    activeTab.value = 'detail'
    void loadBookDetail(route.id, routeId)
  } else if (route.name === 'reader' && mode.value === 'live') {
    activeTab.value = 'growth'
    void startLive({ restoreId: route.id })
  } else {
    activeTab.value = (route.name === 'reader' || (route.name === 'shelf' && mode.value === 'live')) ? 'home' : route.name
    refreshLiveView()
  }
}

async function loadBookDetail(id, routeId) {
  const scope = accountScope
  readerPending.value = true
  try {
    const book = mode.value === 'live'
      ? await new LiveBooksApi(placeholder, accountRequest(scope)).get(id)
      : books.find((item) => item.storyVersionId === id)
    if (navigation !== routeId) return
    if (!book) throw new ApiError('未找到这本绘本')
    if (mode.value === 'demo') recordRecent(book)
    chooseBook(book)
    detailBook.value = book
  } catch (error) {
    if (navigation === routeId) readerError.value = error.message
  } finally {
    if (navigation === routeId) readerPending.value = false
  }
}
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
  readerOrigin = activeTab.value
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
    if (accountScope === scope) syncAccount()
    if (!scopeIsCurrent(scope)) throw new ApiError('登录状态已变更')
    const signal = options?.signal ? AbortSignal.any([scope.signal, options.signal]) : scope.signal
    try {
      return await requestJson(path, { ...options, signal, csrfToken })
    } finally {
      if (accountScope === scope) syncAccount()
      if (!scopeIsCurrent(scope)) throw new ApiError('登录状态已变更')
    }
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
  childProfileId.value = null
  profiles.value = []
  profilesPending.value = false
  profilesError.value = ''
  liveBooks.value = []
  liveBookPage.value = 0
  moreLiveBooks.value = true
  booksPending.value = readerPending.value = false
  booksError.value = readerError.value = ''
}

function refreshLiveView() {
  if (mode.value !== 'live') return
  if (['home', 'library', 'growth'].includes(activeTab.value) && !liveBookPage.value && !booksError.value) void loadLiveBooks()
  if (activeTab.value === 'growth' && !historyPage.value && !historyError.value) void loadHistory()
}

function syncAccount() {
  const cookie = readCookie('zhiya_csrf')
  if (mode.value !== 'live' || (accountScope && cookie === accountCookie)) return
  const changed = Boolean(accountScope)
  accountScope?.abort()
  accountCookie = cookie
  accountScope = new AbortController()
  if (changed && (readerConfig.value || ['book', 'reader'].includes(parseMobileRoute(window.location.hash).name))) {
    goTo('#home')
  }
  clearLiveData()
  void loadProfiles()
  refreshLiveView()
  return changed
}

function syncStoredAccount(event) {
  if (event.storageArea === localStorage && [null, 'zhiya.account', 'zhiya.role'].includes(event.key)) syncAccount()
}

watch(mode, () => {
  accountScope?.abort()
  accountScope = undefined
  if (readerConfig.value) exitReader()
  if (activeTab.value === 'detail' || activeTab.value === 'shelf') {
    selectTab('home')
  }
  clearLiveData()
  syncAccount()
}, { flush: 'sync' })
watch(activeTab, refreshLiveView)
onMounted(() => {
  applyRoute()
  window.addEventListener('popstate', applyRoute)
  window.addEventListener('hashchange', applyRoute)
  window.addEventListener('focus', syncAccount)
  window.addEventListener('pageshow', syncAccount)
  window.addEventListener('storage', syncStoredAccount)
  document.addEventListener('visibilitychange', syncAccount)
})
onUnmounted(() => {
  window.removeEventListener('popstate', applyRoute)
  window.removeEventListener('hashchange', applyRoute)
  window.removeEventListener('focus', syncAccount)
  window.removeEventListener('pageshow', syncAccount)
  window.removeEventListener('storage', syncStoredAccount)
  document.removeEventListener('visibilitychange', syncAccount)
  accountScope?.abort()
  accountScope = undefined
})

async function loadProfiles() {
  if (profilesPending.value) return
  const scope = accountScope
  profilesPending.value = true
  profilesError.value = ''
  try {
    const { data } = await accountRequest(scope)('/api/v1/child-profiles')
    if (!scopeIsCurrent(scope)) return
    profiles.value = data.items
    if (!activeProfile.value) childProfileId.value = data.items.length === 1 ? data.items[0].childProfileId : null
  } catch (error) {
    if (!scopeIsCurrent(scope)) return
    if (publicSite && error.status === 401) window.location.replace('/?entry=mobile#auth')
    else profilesError.value = error.message
  } finally {
    if (scopeIsCurrent(scope)) profilesPending.value = false
  }
}

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

async function startLive({ versionId, restoreId = null } = {}) {
  if (syncAccount() || readerPending.value) return
  const profileId = childProfileId.value
  if (restoreId !== null && !validId(restoreId)) {
    readerError.value = '无法打开这条阅读记录'
    return
  }
  if (restoreId === null && (!validId(profileId) || !validId(versionId))) {
    readerError.value = '请先选择儿童档案和绘本'
    return
  }
  const scope = accountScope
  const origin = activeTab.value
  const routeId = navigation
  readerOrigin = origin
  const isCurrent = () => scopeIsCurrent(scope) && routeId === navigation
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
        if (isCurrent()) {
          readerConfig.value = {
            mode: 'live', initialSnapshot, sessionApi,
            book: historyBook(initialSnapshot.session) ?? { title: '共读绘本' },
          }
          rememberReadingRoute(restoreId)
        }
        return
      }
    }
    const book = await new LiveBooksApi(placeholder, request).get(versionId)
    if (!isCurrent()) return
    if (initialSnapshot && !book.pages.some((page) => page.page === initialSnapshot.session.currentPage)) {
      throw new ApiError('服务端会话的当前页在该绘本版本中不可用')
    }
    readerConfig.value = {
      key: `live-${versionId}-${Date.now()}`, mode: 'live', book, sessionApi,
      voiceApi: new VoiceApi(request), childProfileId: profileId, storyVersionId: versionId,
      initialSessionId: initialSnapshot?.session.readingSessionId ?? null, initialSnapshot,
    }
    if (restoreId !== null) rememberReadingRoute(restoreId)
  } catch (error) {
    if (isCurrent()) readerError.value = error.message
  } finally {
    if (isCurrent()) readerPending.value = false
  }
}

function rememberReadingRoute(sessionId) {
  if (readerConfig.value?.mode !== 'live' || !validId(sessionId)) return
  const hash = `#reader/${sessionId}`
  if (window.location.hash !== hash) window.history.pushState(null, '', hash)
  handledHash = hash
}

function exitReader() {
  if (readerConfig.value?.mode === 'live') invalidateHistory()
  const demoDetail = readerConfig.value?.mode === 'demo' && readerOrigin === 'detail'
  readerConfig.value = null
  if (demoDetail) return
  selectTab(readerOrigin === 'detail' ? 'library' : readerOrigin)
}
function handleReadingFinished({ book, session, report }) {
  if (readerConfig.value?.mode !== 'demo') return
  demoRecords.value.unshift({ id: `record-${Date.now()}`, title: book.title, cover: book.cover, pages: session.visitedPageNumbers?.length ?? 0, summary: report?.summary || '', date: new Date().toLocaleDateString('zh-CN'), revision: session.revision })
  saveRecords()
}
async function openSearch() { selectTab('library'); await nextTick(); searchInput.value?.focus() }
function useStoryTemplate(book) { chooseBook(book); startDemo(book) }
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
  <ReaderView v-else-if="readerConfig" :key="readerConfig.key" :book="readerConfig.book" :mode="readerConfig.mode" :session-api="readerConfig.sessionApi" :voice-api="readerConfig.voiceApi" :child-profile-id="readerConfig.childProfileId" :story-version-id="readerConfig.storyVersionId" :initial-session-id="readerConfig.initialSessionId" :initial-snapshot="readerConfig.initialSnapshot" @exit="exitReader" @session="rememberReadingRoute" @finished="handleReadingFinished" />

  <div v-else class="library-shell">
    <header v-if="activeTab !== 'detail'" class="app-header">
      <button class="brand-lockup app-brand" type="button" aria-label="返回首页" @click="selectTab('home')">
        <span class="brand-mark">芽</span><span><strong>知芽</strong><small>亲子共读</small></span>
      </button>
      <div class="app-header-actions">
        <button class="icon-action" type="button" aria-label="搜索绘本" @click="openSearch">⌕</button>
        <button v-if="!publicSite" class="mode-mini" type="button" @click="mode = mode === 'demo' ? 'live' : 'demo'">{{ mode === 'demo' ? '演示' : '实机' }}</button>
        <button class="avatar-button" type="button" aria-label="家长中心" @click="selectTab('profile')">{{ mode === 'demo' ? '禾' : activeProfile?.nickname?.charAt(0) || '家' }}</button>
      </div>
    </header>

    <main class="mobile-main">
      <p v-if="mode === 'live' && readerError" class="inline-error" role="alert">{{ readerError }}</p>
      <template v-if="mode === 'live' && profileTab">
        <section class="inner-page-head"><div><h1>{{ { home: '今天和谁一起读？', library: '绘本馆', profile: '我的阅读空间' }[activeTab] }}</h1></div></section>
        <p v-if="profilesPending" role="status">正在读取儿童档案…</p>
        <div v-else-if="profilesError" role="alert">{{ profilesError }} <button type="button" @click="loadProfiles">重试</button></div>
        <label v-else-if="profiles.length" class="family-profile">儿童档案
          <select v-model="childProfileId" :disabled="readerPending">
            <option disabled :value="null">请选择孩子</option>
            <option v-for="(profile, index) in profiles" :key="profile.childProfileId" :value="profile.childProfileId">{{ profile.nickname || ('孩子 ' + (index + 1)) }}</option>
          </select>
        </label>
        <div v-else class="empty-state compact"><strong>先为孩子建一份阅读档案</strong>
          <a href="/#account/children" target="_blank" rel="noopener">去创建儿童档案</a>
          <button class="text-button" type="button" @click="loadProfiles">已创建，刷新档案</button>
        </div>
      </template>

      <template v-if="activeTab === 'home' && mode === 'demo'">
        <section class="ranking-section">
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
        <section class="recent-section">
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


      <template v-else-if="mode === 'live' && (activeTab === 'library' || activeTab === 'home')">
        <label v-if="activeTab === 'library'" class="search-box"><span>⌕</span><input ref="searchInput" v-model="searchQuery" type="search" placeholder="搜索已加载的书名"></label>
        <p v-if="booksError" class="inline-error" role="alert">{{ booksError }}</p>
        <p v-if="booksPending" role="status">加载绘本中…</p>
        <p v-else-if="!displayedBooks.length && !booksError">暂无可展示的绘本。</p>
        <div class="library-phone-grid">
          <article v-for="book in displayedBooks" :key="book.storyVersionId">
            <button class="library-phone-card" type="button" :disabled="readerPending || !activeProfile" @click="startLive({ versionId: book.storyVersionId })">
              <img :key="book.cover" :src="book.cover" :alt="book.title" @error="imageFallback"><strong>{{ book.title }}</strong>
              <small>{{ book.age }}<template v-if="book.author"> · {{ book.author }}</template></small>
            </button>
            <a class="text-button" :href="`#book/${book.storyVersionId}`" :aria-label="`查看《${book.title}》详情`" @click.prevent="openBookDetail(book)">详情</a>
          </article>
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
        <section v-if="!offlineDemo" class="info-panel">
          <p>在创作工坊设置故事主题，生成并审核绘本，再加入私人书架。</p>
          <a class="button button-primary" href="/#workshop">前往创作工坊</a>
        </section>
        <section v-if="mode === 'demo'" class="info-panel"><strong>演示绘本</strong><button class="button button-ghost button-small" type="button" @click="useStoryTemplate(books[0])">用《{{ books[0].title }}》体验共读</button></section>
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
                <strong>{{ historyBook(record)?.title || '共读绘本' }}</strong>
                <span>{{ countReadPages(record) }} 页 · {{ { CREATED: '未开始', IN_PROGRESS: '进行中', FINISHED: '已完成', INTERRUPTED: '已中断' }[record.status] }}</span>
                <button class="button button-ghost button-small" type="button" :disabled="readerPending" @click="startLive({ restoreId: record.readingSessionId })">{{ record.status === 'FINISHED' ? '查看阅读小结' : '继续阅读' }}</button>
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

      <template v-else-if="activeTab === 'detail'">
        <BookDetailView v-if="detailBook && mode === 'demo'" :book="detailBook" :in-shelf="isInShelf(detailBook)" :is-favorite="isFavorite(detailBook)" @back="closeDetail" @toggle-shelf="toggleShelf" @toggle-favorite="toggleFavorite" @start="startDemo" />
        <template v-else>
          <button class="text-button" type="button" @click="closeDetail">← 返回</button>
          <p v-if="readerPending" role="status">正在加载绘本…</p>
          <article v-if="detailBook" class="profile-card">
            <img class="profile-avatar" :src="detailBook.cover" :alt="detailBook.title" @error="imageFallback">
            <div><h1>{{ detailBook.title }}</h1><p>{{ [detailBook.age, detailBook.author].filter(Boolean).join(' · ') }}</p></div>
          </article>
          <button v-if="detailBook" class="button button-primary" type="button" :disabled="readerPending || !activeProfile" @click="startLive({ versionId: detailBook.storyVersionId })">开始阅读</button>
        </template>
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
        <section v-if="mode === 'demo'" class="inner-page-head"><div><p class="eyebrow">演示档案</p><h1>芽芽的阅读空间</h1></div></section>
        <article v-if="mode === 'demo'" class="profile-card"><span class="profile-avatar">禾</span><div><strong>芽芽</strong><p>3-4 岁 · 喜欢动物和勇气</p></div></article>
        <article v-else-if="activeProfile" class="profile-card">
          <span class="profile-avatar">{{ activeProfile.nickname?.charAt(0) || '童' }}</span>
          <div>
            <strong>{{ activeProfile.nickname || '儿童档案' }}</strong>
            <p>{{ [ageLabels[activeProfile.ageBand], ...(activeProfile.interests || [])].filter(Boolean).join(' · ') }}</p>
          </div>
        </article>
        <section class="settings-list">
          <button v-if="mode === 'demo'" class="setting-row" type="button" @click="selectTab('shelf')"><span>我的书架</span><b>{{ shelfBooks.length }} 本 →</b></button>
          <button class="setting-row" type="button" @click="selectTab('growth')"><span>阅读记录</span><b>{{ mode === 'demo' ? `${recordCount} 条 →` : '查看 →' }}</b></button>
          <a v-if="mode === 'live'" class="setting-row" href="/#account/children">管理儿童档案 →</a>
          <div v-else class="setting-row"><span>运行模式</span><b>{{ mode === 'demo' ? '原型演示' : '实机接口' }}</b></div>
          <button v-if="!publicSite" class="setting-row" type="button" @click="mode = mode === 'demo' ? 'live' : 'demo'"><span>切换模式</span><b>当前：{{ mode === 'demo' ? '演示' : '实机' }} →</b></button>
        </section>
      </template>
    </main>

    <nav v-if="activeTab !== 'detail'" class="bottom-nav" aria-label="主导航">
      <button v-for="item in navItems" :key="item.id" :class="{ active: activeTab === item.id || (activeTab === 'shelf' && item.id === 'profile') }" type="button" @click="selectTab(item.id)">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path :d="item.icon" /></svg><span>{{ item.label }}</span>
      </button>
    </nav>
  </div>
</template>
