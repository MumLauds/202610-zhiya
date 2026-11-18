import { computed, reactive, readonly } from 'vue'
import { ApiError, createClientEventId, describeApiError } from '../api/client'
import { checkHealth, login, logout, register } from '../api/auth'
import { createProfile, listProfiles, updateProfile } from '../api/profiles'
import { getStoryVersion, listStorybooks } from '../api/storybooks'
import {
  addReadingEvent,
  completeReadingSession,
  createReadingSession,
  getReadingSession,
  listReadingSessions,
} from '../api/reading'
import { previewBooks, mapApiBook } from '../data/preview'

function parseRoute() {
  const parts = location.hash.replace(/^#/, '').split('/').filter(Boolean)
  const name = parts[0] || 'home'
  if (name === 'account') return { name, section: parts[1] || 'overview' }
  if (name === 'detail') return { name, versionId: Number(parts[1]) || null }
  if (name === 'reader' && parts[1] === 'preview') return { name, previewVersionId: Number(parts[2]) || null }
  if (name === 'reader') return { name, sessionId: Number(parts[1]) || null }
  if (['library', 'auth'].includes(name)) return { name }
  return { name: 'home' }
}

const savedProfileId = Number(localStorage.getItem('zhiya.activeProfileId') || 0)
const savedNickname = localStorage.getItem('zhiya.guardianNickname') || '知芽家长'

const state = reactive({
  route: parseRoute(),
  health: 'checking',
  auth: {
    status: 'checking',
    authenticated: false,
    accountId: null,
    account: localStorage.getItem('zhiya.account') || '',
    nickname: savedNickname,
  },
  profiles: [],
  activeProfileId: savedProfileId,
  books: previewBooks,
  booksSource: 'preview',
  booksLoading: false,
  booksError: '',
  nextBooksPage: null,
  bookFilters: { ageBand: '', topic: '' },
  selectedBookId: localStorage.getItem('zhiya.selectedBookId') || previewBooks[0].id,
  currentBook: null,
  detailLoading: false,
  currentSession: null,
  currentEvents: [],
  currentReport: null,
  sessionLoading: false,
  sessionBusy: false,
  readingSyncRequired: false,
  sessions: [],
  sessionsLoading: false,
  toast: { message: '', tone: 'info', visible: false },
})

let toastTimer = null
let authEpoch = 0
let booksRequest = 0
let detailRequest = 0
let sessionsRequest = 0
let readingEpoch = 0
let pendingReading = null

function clearAccountData() {
  state.profiles = []
  state.activeProfileId = 0
  state.sessions = []
  state.currentBook = null
  state.currentSession = null
  state.currentEvents = []
  state.currentReport = null
  state.bookFilters = { ageBand: '', topic: '' }
  state.selectedBookId = previewBooks[0].id
  readingEpoch += 1
  state.sessionLoading = state.sessionBusy = state.sessionsLoading = state.detailLoading = state.booksLoading = false
  state.readingSyncRequired = false
  pendingReading = null
  for (const key of ['account', 'activeProfileId', 'selectedBookId', 'plan', 'guardianNickname']) {
    localStorage.removeItem(`zhiya.${key}`)
  }
  loadPreviewBooks()
}

function resetAuthentication() {
  authEpoch += 1
  clearAccountData()
  state.auth = { status: 'anonymous', authenticated: false, accountId: null, account: '', nickname: '知芽家长' }
}

function activeProfile() {
  return state.profiles.find((profile) => profile.childProfileId === state.activeProfileId) || state.profiles[0] || null
}

function setToast(message, tone = 'info') {
  state.toast.message = message
  state.toast.tone = tone
  state.toast.visible = true
  clearTimeout(toastTimer)
  toastTimer = window.setTimeout(() => {
    state.toast.visible = false
  }, 2800)
}

function navigate(path) {
  const target = `#${String(path).replace(/^#/, '')}`
  if (location.hash === target) {
    state.route = parseRoute()
  } else {
    location.hash = target
  }
}

function updateBodyView() {
  document.body.dataset.view = state.route.name
}

function applyBooks(items, source, message = '') {
  state.books = items
  state.booksSource = source
  state.booksError = message
  if (!items.some((book) => book.id === state.selectedBookId)) {
    state.selectedBookId = items[0]?.id || ''
  }
}

function loadPreviewBooks(message = '') {
  state.nextBooksPage = null
  const { ageBand, topic } = state.bookFilters
  const filtered = previewBooks.filter((book) => {
    if (ageBand && book.ageBand !== ageBand) return false
    if (topic && !book.topics.includes(topic)) return false
    return true
  })
  applyBooks(filtered, 'preview', message)
}

function loadBooks(filters = {}) {
  state.bookFilters = { ...state.bookFilters, ...filters }
  state.nextBooksPage = null
  return fetchBooksPage(1)
}

async function fetchBooksPage(page) {
  const epoch = authEpoch
  const request = ++booksRequest
  const pageSize = 30
  state.booksLoading = true
  state.booksError = ''
  try {
    if (!state.auth.authenticated) {
      loadPreviewBooks('登录后可读取组内已发布绘本，当前展示本地设计预览。')
      return
    }
    const items = await listStorybooks({
      ageBand: state.bookFilters.ageBand || undefined,
      topic: state.bookFilters.topic || undefined,
      page,
      pageSize,
    })
    if (epoch !== authEpoch || request !== booksRequest) return
    state.nextBooksPage = items.length === pageSize ? page + 1 : null
    if (page > 1) {
      const existing = new Set(state.books.map((book) => book.storyVersionId))
      const added = items.filter((book) => !existing.has(book.storyVersionId)).map(mapApiBook)
      applyBooks([...state.books, ...added], 'api')
      if (added.length) selectBook(added[0].id)
      return
    }
    if (!items.length && !state.bookFilters.ageBand && !state.bookFilters.topic) {
      loadPreviewBooks('后端当前没有可见的已发布绘本，当前展示本地设计预览。')
      return
    }
    applyBooks(items.map(mapApiBook), 'api')
  } catch (error) {
    if (epoch !== authEpoch || request !== booksRequest) return
    if (error instanceof ApiError && error.status === 401) {
      resetAuthentication()
      loadPreviewBooks('登录后读取后端绘本，当前展示本地设计预览。')
    } else if (state.nextBooksPage !== null) {
      state.booksError = error.message || '书架加载失败，请重试。'
    } else {
      loadPreviewBooks(error.message || '绘本列表暂时不可用，已切换到本地预览。')
    }
  } finally {
    if (request === booksRequest) state.booksLoading = false
  }
}

function loadMoreBooks() {
  if (!state.booksLoading && state.nextBooksPage && state.auth.status === 'authenticated') {
    return fetchBooksPage(state.nextBooksPage)
  }
}

async function loadProfiles() {
  const epoch = authEpoch
  const items = await listProfiles()
  if (epoch !== authEpoch) return []
  state.profiles = items
  if (!items.some((profile) => profile.childProfileId === state.activeProfileId)) {
    state.activeProfileId = items[0]?.childProfileId || 0
  }
  localStorage.setItem('zhiya.activeProfileId', String(state.activeProfileId))
  return items
}

async function bootstrap() {
  const epoch = authEpoch
  try {
    await checkHealth()
    state.health = 'ready'
  } catch {
    state.health = 'offline'
  }
  if (epoch !== authEpoch) return
  try {
    await loadProfiles()
    if (epoch !== authEpoch) return
    state.auth.status = 'authenticated'
    state.auth.authenticated = true
    await loadBooks()
  } catch (error) {
    if (epoch !== authEpoch) return
    clearAccountData()
    if (error instanceof ApiError && error.status === 401) {
      state.auth.status = 'anonymous'
    } else {
      state.auth.status = 'unavailable'
      state.booksError = error.message || '后端暂不可用'
    }
    state.auth.authenticated = false
    loadPreviewBooks(state.health === 'offline' ? '后端暂未启动，当前展示本地设计预览。' : '登录后读取后端绘本，当前展示本地设计预览。')
  }
  await handleRoute()
}

async function signIn(account, password) {
  resetAuthentication()
  const epoch = authEpoch
  const result = await login(account, password)
  if (epoch !== authEpoch) return
  state.auth.accountId = result.data.accountId
  state.auth.account = account
  state.auth.authenticated = true
  state.auth.status = 'authenticated'
  localStorage.setItem('zhiya.account', account)
  await loadProfiles()
  await loadBooks()
  if (epoch !== authEpoch) return
  setToast('已登录知芽家长空间')
  navigate(state.books.length ? 'library' : 'account/children')
}

async function signUp(account, password) {
  await register(account, password)
  await signIn(account, password)
  setToast('账号已创建并登录')
}

async function signOut() {
  if (state.auth.status === 'signing-out') return false
  authEpoch += 1
  const epoch = authEpoch
  state.auth.status = 'signing-out'
  try {
    await logout()
  } catch (error) {
    if (epoch !== authEpoch) return false
    if (error.status !== 401) {
      state.auth.status = 'authenticated'
      state.sessionBusy = false
      state.readingSyncRequired = Boolean(state.currentSession && !state.currentSession.preview)
      setToast('退出尚未确认，服务器会话可能仍有效。请检查网络后重试退出。', 'error')
      return false
    }
  }
  if (epoch !== authEpoch) return false
  resetAuthentication()
  loadPreviewBooks('已退出账号，当前展示本地设计预览。')
  navigate('home')
  setToast('已退出当前账号')
  return true
}

function selectProfile(profileId) {
  state.activeProfileId = Number(profileId)
  localStorage.setItem('zhiya.activeProfileId', String(state.activeProfileId))
  setToast(`已切换到${activeProfile()?.nickname || '孩子的'}阅读空间`)
}

async function addProfile(payload) {
  const epoch = authEpoch
  const profile = await createProfile(payload)
  if (epoch !== authEpoch) return
  await loadProfiles()
  if (epoch !== authEpoch) return
  selectProfile(profile.childProfileId)
  setToast('儿童档案已创建')
  return profile
}

async function editProfile(profileId, payload) {
  const epoch = authEpoch
  const profile = await updateProfile(profileId, payload)
  if (epoch !== authEpoch) return
  await loadProfiles()
  if (epoch !== authEpoch) return
  setToast('儿童档案已更新')
  return profile
}

function selectBook(bookId) {
  if (!state.books.some((book) => book.id === bookId)) return
  state.selectedBookId = bookId
  localStorage.setItem('zhiya.selectedBookId', bookId)
}

function openDetail(bookOrVersionId) {
  const versionId = Number(bookOrVersionId?.storyVersionId || bookOrVersionId)
  if (versionId) navigate(`detail/${versionId}`)
}

async function loadDetail(versionId) {
  const epoch = authEpoch
  const request = ++detailRequest
  const route = location.hash
  const isCurrent = () => epoch === authEpoch && request === detailRequest && route === location.hash
  state.detailLoading = true
  state.currentBook = null
  try {
    const cached = state.books.find((book) => book.storyVersionId === Number(versionId))
    if (cached?.source === 'preview') {
      state.currentBook = cached
      return
    }
    const version = await getStoryVersion(Number(versionId))
    if (!isCurrent()) return
    state.currentBook = mapApiBook(version)
  } catch (error) {
    if (!isCurrent()) return
    if (error.status === 404 && state.booksSource === 'api') {
      const remaining = state.books.filter((book) => book.storyVersionId !== Number(versionId))
      const removed = remaining.length < state.books.length
      applyBooks(remaining, 'api')
      if (state.booksLoading && state.nextBooksPage === null) {
        // The old shelf may still be visible while a new filter's first page loads.
        void loadBooks()
      } else if (removed || state.booksLoading) {
        state.nextBooksPage = 1
        void fetchBooksPage(1)
      }
    }
    setToast(describeApiError(error), 'error')
  } finally {
    if (isCurrent()) state.detailLoading = false
  }
}

async function startReading(book) {
  if (!book || state.sessionBusy) return
  const epoch = authEpoch
  state.currentBook = book
  state.currentEvents = []
  state.currentReport = null
  if (book.source === 'preview' || !state.auth.authenticated) {
    state.currentSession = {
      preview: true,
      readingSessionId: `preview-${book.storyVersionId}`,
      storyVersionId: book.storyVersionId,
      currentPage: 1,
      visitedPageNumbers: [1],
      status: 'IN_PROGRESS',
      revision: 1,
    }
    navigate(`reader/preview/${book.storyVersionId}`)
    return
  }
  const profile = activeProfile()
  if (!profile) {
    setToast('请先为孩子创建阅读档案', 'error')
    navigate('account/children')
    return
  }
  try {
    state.sessionBusy = true
    const created = await createReadingSession({
      childProfileId: profile.childProfileId,
      storyVersionId: book.storyVersionId,
    })
    if (epoch !== authEpoch) return
    state.currentSession = created
    navigate(`reader/${created.readingSessionId}`)
  } catch (error) {
    if (epoch === authEpoch) setToast(describeApiError(error), 'error')
  } finally {
    if (epoch === authEpoch) state.sessionBusy = false
  }
}

async function loadReaderRoute() {
  const epoch = authEpoch
  const generation = ++readingEpoch
  const route = { ...state.route }
  const routeHash = location.hash
  const isCurrent = () => epoch === authEpoch && generation === readingEpoch && routeHash === location.hash
  state.sessionLoading = true
  state.sessionBusy = false
  state.readingSyncRequired = false
  pendingReading = null
  state.currentSession = null
  state.currentBook = null
  state.currentEvents = []
  state.currentReport = null
  try {
    if (route.previewVersionId) {
      const book = previewBooks.find((item) => item.storyVersionId === route.previewVersionId)
      if (!book) throw new Error('本地预览绘本不可用')
      state.currentBook = book
      state.currentSession = {
        preview: true,
        readingSessionId: `preview-${book.storyVersionId}`,
        storyVersionId: book.storyVersionId,
        currentPage: 1,
        visitedPageNumbers: [1],
        status: 'IN_PROGRESS',
        revision: 1,
      }
      return
    }
    if (!route.sessionId) return
    const detail = await getReadingSession(route.sessionId)
    if (!isCurrent()) return
    const cached = state.books.find((book) => book.source === 'api' && book.storyVersionId === detail.session.storyVersionId)
    const book = cached || mapApiBook(await getStoryVersion(detail.session.storyVersionId))
    if (!isCurrent()) return
    applyReadingDetail(detail)
    state.currentBook = book
    if (detail.session.status === 'CREATED') {
      await writeReading({
        sessionId: detail.session.readingSessionId,
        kind: 'page',
        payload: { eventType: 'STARTED', pageNumber: 1, expectedRevision: detail.session.revision, clientEventId: createClientEventId('start') },
      })
    }
  } catch (error) {
    if (isCurrent()) {
      setToast(describeApiError(error), 'error')
      state.currentSession = null
    }
  } finally {
    if (isCurrent()) state.sessionLoading = false
  }
}

function applyReadingDetail(detail) {
  state.currentSession = detail.session
  state.currentEvents = detail.events || []
  state.currentReport = detail.report
}

function sameReading(operation, epoch, generation) {
  return epoch === authEpoch && generation === readingEpoch && state.currentSession?.readingSessionId === operation.sessionId
}

async function writeReading(operation) {
  if (state.sessionBusy) return false
  const epoch = authEpoch
  const generation = readingEpoch
  state.sessionBusy = true
  try {
    const result = operation.kind === 'finish'
      ? await completeReadingSession(operation.sessionId, operation.payload)
      : await addReadingEvent(operation.sessionId, operation.payload)
    if (!sameReading(operation, epoch, generation)) return false
    if (operation.kind === 'finish') {
      state.currentSession = result.session
      state.currentReport = result.report
      setToast('阅读记录已保存')
    } else {
      const session = state.currentSession
      session.currentPage = operation.payload.pageNumber
      session.visitedPageNumbers = [...new Set([...session.visitedPageNumbers, operation.payload.pageNumber])]
      session.status = 'IN_PROGRESS'
      session.revision = result.revision
    }
    pendingReading = null
    state.readingSyncRequired = false
    return true
  } catch (error) {
    if (!sameReading(operation, epoch, generation)) return false
    const terminalUnknown = error.status === 504 && error.code === 'REQUEST_OUTCOME_UNKNOWN'
    const retrySameKey = error.status === 0 || error.code === 'REQUEST_IN_PROGRESS' || (error.status === 504 && !terminalUnknown)
    pendingReading = retrySameKey ? { ...operation, retryAt: Date.now() + (Number(error.retryAfter) || 1) * 1000 } : null
    state.readingSyncRequired = retrySameKey || terminalUnknown || error.status === 409
    if (state.readingSyncRequired) {
      try {
        const detail = await getReadingSession(operation.sessionId)
        if (!sameReading(operation, epoch, generation)) return false
        applyReadingDetail(detail)
        if (!pendingReading || detail.session.revision !== operation.payload.expectedRevision) {
          pendingReading = null
          state.readingSyncRequired = false
        }
      } catch {
        // Keep writes disabled until the authoritative session can be read.
        if (!sameReading(operation, epoch, generation)) return false
      }
    }
    if (terminalUnknown && !state.readingSyncRequired) {
      setToast('已按服务器记录同步进度，请根据当前页面决定是否继续。')
    } else {
      setToast(`${describeApiError(error)}${state.readingSyncRequired ? ' 请使用“确认并同步进度”。' : ''}`, 'error')
    }
    return false
  } finally {
    if (sameReading(operation, epoch, generation)) state.sessionBusy = false
  }
}

async function refreshReading() {
  const session = state.currentSession
  if (!session || session.preview || state.sessionBusy) return false
  const epoch = authEpoch
  const generation = readingEpoch
  const target = { sessionId: session.readingSessionId }
  state.sessionBusy = true
  try {
    const detail = await getReadingSession(target.sessionId)
    if (!sameReading(target, epoch, generation)) return false
    if (session.status === 'FINISHED' && !state.readingSyncRequired) {
      state.currentReport = detail.report
      return true
    }
    applyReadingDetail(detail)
    const operation = pendingReading
    if (operation && ['CREATED', 'IN_PROGRESS'].includes(detail.session.status) && detail.session.revision === operation.payload.expectedRevision) {
      if (Date.now() < operation.retryAt) {
        setToast('原请求仍在处理，请稍后再次确认。', 'error')
        return false
      }
      state.sessionBusy = false
      return await writeReading(operation)
    }
    pendingReading = null
    state.readingSyncRequired = false
    return true
  } catch (error) {
    if (sameReading(target, epoch, generation)) setToast(describeApiError(error), 'error')
    return false
  } finally {
    if (sameReading(target, epoch, generation)) state.sessionBusy = false
  }
}

async function recordPageChange(pageNumber) {
  const session = state.currentSession
  if (!session || session.status !== 'IN_PROGRESS' || state.sessionBusy || state.readingSyncRequired) return false
  const currentPage = state.currentBook?.pages.find((page) => page.pageNumber === session.currentPage)
  if (pageNumber !== currentPage?.defaultNextPageNumber) return false
  if (session.preview) {
    session.currentPage = pageNumber
    session.visitedPageNumbers = [...new Set([...session.visitedPageNumbers, pageNumber])]
    session.revision += 1
    return true
  }
  return writeReading({
    sessionId: session.readingSessionId,
    kind: 'page',
    payload: {
      eventType: 'PAGE_CHANGED',
      pageNumber,
      expectedRevision: session.revision,
      clientEventId: createClientEventId('page'),
    },
  })
}

async function completeReading() {
  const session = state.currentSession
  if (!session || session.status !== 'IN_PROGRESS' || state.sessionBusy || state.readingSyncRequired) return false
  if (session.preview) {
    session.status = 'FINISHED'
    state.currentReport = {
      status: 'READY',
      summary: `本次共读浏览了 ${session.visitedPageNumbers.length} 页，可在家长陪伴下继续交流故事内容。`,
      activities: [],
    }
    setToast('本次预览阅读已完成')
    return state.currentReport
  }
  return writeReading({
    sessionId: session.readingSessionId,
    kind: 'finish',
    payload: {
      reason: 'USER_FINISH',
      expectedRevision: session.revision,
      clientEventId: createClientEventId('finish'),
    },
  })
}

async function loadSessions() {
  const epoch = authEpoch
  const request = ++sessionsRequest
  const isCurrent = () => epoch === authEpoch && request === sessionsRequest
  if (!state.auth.authenticated) {
    state.sessions = []
    return
  }
  state.sessionsLoading = true
  try {
    const sessions = new Map()
    const pageSize = 50
    for (let page = 1; ; page += 1) {
      const items = await listReadingSessions(page, pageSize)
      if (!isCurrent()) return
      for (const session of items) sessions.set(session.readingSessionId, session)
      if (items.length < pageSize) break
    }
    state.sessions = [...sessions.values()]
  } catch (error) {
    if (isCurrent()) setToast(describeApiError(error), 'error')
  } finally {
    if (isCurrent()) state.sessionsLoading = false
  }
}

async function handleRoute() {
  updateBodyView()
  const route = state.route
  if (route.name === 'library') {
    if (state.auth.authenticated && state.booksSource !== 'api') await loadBooks()
  } else if (route.name === 'detail' && route.versionId) {
    await loadDetail(route.versionId)
  } else if (route.name === 'reader') {
    await loadReaderRoute()
  } else if (route.name === 'account' && ['growth', 'overview'].includes(route.section)) {
    await loadSessions()
  }
}

function syncRoute() {
  state.route = parseRoute()
  return handleRoute()
}

const totals = computed(() => {
  const visited = state.sessions.filter((session) => session.visitedPageNumbers?.length)
  return {
    sessions: state.sessions.filter((session) => ['IN_PROGRESS', 'FINISHED'].includes(session.status)).length,
    read: new Set(visited.map((session) => session.storyVersionId)).size,
    pages: visited.reduce((sum, session) => sum + session.visitedPageNumbers.length, 0),
  }
})

export const zhiya = readonly(state)
export const zhiyaActions = {
  bootstrap,
  navigate,
  syncRoute,
  signIn,
  signUp,
  signOut,
  selectProfile,
  addProfile,
  editProfile,
  loadProfiles,
  loadBooks,
  loadMoreBooks,
  selectBook,
  openDetail,
  loadDetail,
  startReading,
  loadReaderRoute,
  recordPageChange,
  completeReading,
  refreshReading,
  loadSessions,
  setToast,
}
export { activeProfile, totals, parseRoute }
