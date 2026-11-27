import { computed, reactive, readonly } from 'vue'
import { ApiError, createClientEventId, describeApiError, readCsrfCookie } from '../api/client'
import { checkHealth, currentIdentity, login, logout, parentUnlock, register } from '../api/auth'
import {
  createAdminKnowledge,
  createAdminStory,
  listAdminKnowledge,
  listAdminStoryVersions,
  updateAdminKnowledge,
  updateAdminStoryStatus,
} from '../api/admin'
import { createProfile, deleteProfile, listProfiles, updateProfile } from '../api/profiles'
import { getStoryVersion, listStorybooks, recommendStorybooks } from '../api/storybooks'
import {
  addReadingEvent,
  askReadingQuestion,
  chooseReadingBranch,
  completeReadingSession,
  createReadingSession,
  deleteReadingSession,
  getDeletionStatus,
  getReadingSession,
  listReadingSessions,
  requestReadingGuidance,
  updateReadingMode,
} from '../api/reading'
import { approveStoryVersion, generateStory } from '../api/storyWorkshop'
import { previewBooks, mapApiBook } from '../data/preview'
import { canContinueStory, countReadPages } from '../data/readingProgress'
import { readingCreationKey } from '../data/readingCreation'
import { parseMobileRoute } from '../data/mobileRoute'

const CHILD_SESSION_KEY = 'zhiya.childHandoffSessionId'
const PENDING_SESSION_DELETES = 'zhiya.pendingSessionDeletes'

function parseRoute(hash = location.hash) {
  const parts = hash.replace(/^#/, '').split('/').filter(Boolean)
  const name = parts[0] || 'home'
  if (name === 'account') return { name, section: parts[1] || 'overview' }
  if (name === 'admin') return { name, section: parts[1] || 'storybooks' }
  if (name === 'child') return { name, sessionId: Number(parts[1]) || null }
  if (name === 'detail') {
    const source = ['api', 'preview'].includes(parts[1]) ? parts[1] : null
    return { name, source, versionId: Number(parts[source ? 2 : 1]) || null }
  }
  if (name === 'reader' && parts[1] === 'preview') return { name, previewVersionId: Number(parts[2]) || null }
  if (name === 'reader' && parts[1] === 'setup') return { name, childSetup: true, sessionId: Number(parts[2]) || null }
  if (name === 'reader') return { name, sessionId: Number(parts[1]) || null }
  if (name === 'workshop') {
    return {
      name,
      sourceSessionId: parts[1] === 'continue' ? Number(parts[2]) || null : null,
    }
  }
  if (['library', 'auth'].includes(name)) return { name }
  return { name: 'home' }
}

function savedProfileId() {
  const value = localStorage.getItem('zhiya.activeProfileId')
  return value === null ? null : Number(value)
}

const savedNickname = localStorage.getItem('zhiya.guardianNickname') || '知芽家长'

function emptyWorkshopState() {
  return {
    draft: null,
    generationStatus: 'idle',
    generationError: '',
    pendingGeneration: null,
    approvalBusy: false,
    approvedVersion: null,
    shelfBusy: false,
    shelfError: '',
    continuation: null,
    continuationLoading: false,
    continuationError: '',
    switchPending: null,
    switchBusy: false,
    switchError: '',
  }
}

const state = reactive({
  route: parseRoute(),
  health: 'checking',
  accountViewKey: 0,
  auth: {
    status: 'checking',
    authenticated: false,
    role: 'GUARDIAN',
    accountId: null,
    account: localStorage.getItem('zhiya.account') || '',
    nickname: savedNickname,
  },
  profiles: [],
  profilesLoading: false,
  profilesError: '',
  activeProfileId: savedProfileId(),
  books: previewBooks,
  booksSource: 'preview',
  booksLoading: false,
  booksError: '',
  nextBooksPage: null,
  recommendations: [],
  recommendationTopic: '',
  recommendationsLoading: false,
  recommendationsError: '',
  bookFilters: { ageBand: '', topic: '' },
  selectedBookId: '',
  manualBookId: '',
  pendingBookRestore: null,
  currentBook: null,
  detailLoading: false,
  currentSession: null,
  currentEvents: [],
  currentReport: null,
  sessionLoading: false,
  readerLoadRetryable: false,
  sessionBusy: false,
  readingCreationRecovery: null,
  profileDeleteBusyId: null,
  sessionDeleteBusyId: null,
  pendingDeletionChecks: { profile: new Set(), session: new Set() },
  childMode: null,
  readingSyncRequired: false,
  sessions: [],
  nextSessionsPage: null,
  sessionsLoaded: false,
  sessionsLoading: false,
  sessionsError: '',
  admin: {
    storyVersions: [],
    knowledgeItems: [],
    nextStoryPage: null,
    nextKnowledgePage: null,
    storiesLoaded: false,
    storiesLoading: false,
    knowledgeLoaded: false,
    knowledgeError: '',
    loading: false,
    busy: false,
    error: '',
  },
  workshop: emptyWorkshopState(),
  toast: { message: '', tone: 'info', visible: false },
})

let toastTimer = null
let authEpoch = 0
// This cookie rotates with the HttpOnly session. Keep the comparison in this tab's memory.
let knownCsrf = readCsrfCookie()
let profilesRequest = 0
let booksRequest = 0
let firstShelfPage = null
let previewFromEmptyCatalog = false
let selectionRevision = 0
let loadedBookFilters = null
let detailRequest = 0
const detailReadsByVersion = new Map()
let sessionsRequest = 0
let recommendationRequest = 0
let readingEpoch = 0
let pendingReading = null
let activeInitialGuidance = null
let pendingAdminWrite = null
let workshopRequest = 0

function clearAccountViews() {
  clearTimeout(toastTimer)
  state.toast = { message: '', tone: 'info', visible: false }
  state.readingCreationRecovery = null
  activeInitialGuidance = null
  state.profiles = []
  state.profilesLoading = false
  state.profilesError = ''
  state.activeProfileId = null
  state.sessions = []
  state.nextSessionsPage = null
  state.sessionsLoaded = false
  state.sessionsError = ''
  state.recommendations = []
  state.recommendationTopic = ''
  state.recommendationsLoading = false
  state.recommendationsError = ''
  pendingAdminWrite = null
  workshopRequest += 1
  state.workshop = emptyWorkshopState()
  state.admin.storyVersions = []
  state.admin.knowledgeItems = []
  state.admin.nextStoryPage = state.admin.nextKnowledgePage = null
  state.admin.storiesLoaded = false
  state.admin.storiesLoading = false
  state.admin.knowledgeLoaded = false
  state.admin.knowledgeError = ''
  state.admin.loading = false
  state.admin.busy = false
  state.admin.error = ''
  for (const ids of Object.values(state.pendingDeletionChecks)) ids.clear()
  sessionStorage.removeItem(PENDING_SESSION_DELETES)
  state.currentBook = null
  state.currentSession = null
  state.currentEvents = []
  state.currentReport = null
  state.bookFilters = { ageBand: '', topic: '' }
  state.selectedBookId = ''
  firstShelfPage = null
  state.pendingBookRestore = null
  detailReadsByVersion.clear()
  profilesRequest += 1
  booksRequest += 1
  sessionsRequest += 1
  recommendationRequest += 1
  detailRequest += 1
  readingEpoch += 1
  state.sessionLoading = state.sessionBusy = state.sessionsLoading = state.detailLoading = state.booksLoading = false
  state.readerLoadRetryable = false
  state.readingSyncRequired = false
  state.childMode = null
  state.profileDeleteBusyId = null
  state.sessionDeleteBusyId = null
  pendingReading = null
  loadPreviewBooks()
}

function resetAuthentication() {
  authEpoch += 1
  knownCsrf = readCsrfCookie()
  if (state.auth.authenticated) localStorage.removeItem(bookSelectionKey('preview'))
  rememberChildSession(null)
  for (const key of ['account', 'role', 'activeProfileId', 'selectedBookId', 'previewSelectedBookId', 'bookSelectionExplicit', 'plan', 'guardianNickname']) {
    localStorage.removeItem(`zhiya.${key}`)
  }
  state.auth = { status: 'anonymous', authenticated: false, role: 'GUARDIAN', accountId: null, account: '', nickname: '知芽家长' }
  clearAccountViews()
}

function activeProfile() {
  return state.profiles.find((profile) => profile.childProfileId === state.activeProfileId) || null
}

function setToast(message, tone = 'info') {
  state.toast.message = message
  state.toast.tone = tone
  state.toast.visible = true
  clearTimeout(toastTimer)
  if (tone !== 'error') toastTimer = window.setTimeout(dismissToast, 2800)
}

function dismissToast() {
  clearTimeout(toastTimer)
  state.toast.visible = false
}

function navigate(path) {
  const target = `#${String(path).replace(/^#/, '')}`
  if (location.hash !== target) location.hash = target
}

function replaceRoute(path) {
  const hash = `#${String(path).replace(/^#/, '')}`
  window.history.replaceState(window.history.state, '', `${location.pathname}${location.search}${hash}`)
  state.route = parseRoute(hash)
  updateBodyView()
}

function savedChildSessionId() {
  return Number(localStorage.getItem(CHILD_SESSION_KEY) || sessionStorage.getItem(CHILD_SESSION_KEY)) || null
}

function rememberChildSession(sessionId) {
  for (const storage of [localStorage, sessionStorage]) {
    if (sessionId) storage.setItem(CHILD_SESSION_KEY, String(sessionId))
    else storage.removeItem(CHILD_SESSION_KEY)
  }
}

function updateBodyView() {
  document.body.dataset.view = state.route.name
}

function bookSelectionKey(source) {
  if (source === 'api') return 'zhiya.selectedBookId'
  return state.auth.authenticated ? `zhiya.previewSelectedBookId:${state.auth.account}` : 'zhiya.guestPreviewSelectedBookId'
}

function applyBooks(items, source, message = '') {
  state.books = items
  state.booksSource = source
  state.booksError = message
  state.manualBookId = localStorage.getItem(bookSelectionKey(source)) || ''
  if (source === 'preview' || !items.some((book) => book.id === state.selectedBookId)) {
    state.selectedBookId = items.find((book) => book.id === state.manualBookId)?.id || ''
  }
}

function loadPreviewBooks(message = '', emptyCatalog = false) {
  previewFromEmptyCatalog = emptyCatalog
  state.nextBooksPage = null
  const { ageBand, topic } = state.bookFilters
  const filtered = previewBooks.filter((book) => {
    if (ageBand && book.ageBand !== ageBand) return false
    if (topic && !book.topics.includes(topic)) return false
    return true
  })
  applyBooks(filtered, 'preview', message)
}

function shelfRestoreTarget() {
  if (state.pendingBookRestore) return state.pendingBookRestore
  if (state.booksSource !== 'api' || loadedBookFilters !== state.bookFilters) return null
  if (state.books.some((book) => book.id === state.manualBookId)) return state.manualBookId
  return null
}

function loadBooks(filters = {}, restoreBookId = shelfRestoreTarget()) {
  const next = { ...state.bookFilters, ...filters }
  if (next.ageBand !== state.bookFilters.ageBand || next.topic !== state.bookFilters.topic) {
    state.bookFilters = next
    state.pendingBookRestore = null
  } else {
    state.pendingBookRestore = restoreBookId
  }
  state.nextBooksPage = null
  return fetchBooksPage(1)
}

async function fetchBooksPage(page) {
  const epoch = authEpoch
  const request = ++booksRequest
  const selectionAtStart = selectionRevision
  if (page === 1) previewFromEmptyCatalog = false
  const pageSize = 30
  const keepSelection = state.pendingBookRestore !== null || Boolean(state.booksError && state.selectedBookId)
  state.booksLoading = true
  state.booksError = ''
  let pageRequest
  try {
    if (!state.auth.authenticated) {
      loadPreviewBooks('登录后可读取组内已发布绘本，当前展示本地设计预览。')
      return
    }
    pageRequest = listStorybooks({
      ageBand: state.bookFilters.ageBand || undefined,
      topic: state.bookFilters.topic || undefined,
      page,
      pageSize,
    })
    if (page === 1) firstShelfPage = pageRequest
    const items = await pageRequest
    if (epoch !== authEpoch || request !== booksRequest) return
    state.nextBooksPage = items.length === pageSize ? page + 1 : null
    if (page > 1) {
      const existing = new Set(state.books.map((book) => book.storyVersionId))
      const added = items.filter((book) => !existing.has(book.storyVersionId)).map(mapApiBook)
      applyBooks([...state.books, ...added], 'api')
      if (!keepSelection && selectionAtStart === selectionRevision && added.length && !added.some((book) => book.id === state.selectedBookId)) {
        state.selectedBookId = added[0].id
      }
    } else if (!items.length && !state.bookFilters.ageBand && !state.bookFilters.topic) {
      loadPreviewBooks('后端当前没有可见的已发布绘本，当前展示本地设计预览。', true)
    } else {
      loadedBookFilters = state.bookFilters
      applyBooks(items.map(mapApiBook), 'api')
    }
    if (state.pendingBookRestore) {
      const key = bookSelectionKey('api')
      if (localStorage.getItem(key) !== state.pendingBookRestore) {
        state.pendingBookRestore = null
      } else if (state.books.some((book) => book.id === state.pendingBookRestore)) {
        state.selectedBookId = state.pendingBookRestore
        state.pendingBookRestore = null
      } else if (state.nextBooksPage) {
        return await fetchBooksPage(state.nextBooksPage)
      } else {
        if (!state.bookFilters.ageBand && !state.bookFilters.topic) localStorage.removeItem(key)
        state.pendingBookRestore = null
      }
    }
    return true
  } catch (error) {
    if (epoch !== authEpoch || request !== booksRequest) return
    if (error instanceof ApiError && error.status === 401) {
      resetAuthentication()
      loadPreviewBooks('登录后读取后端绘本，当前展示本地设计预览。')
    } else if (state.pendingBookRestore || state.nextBooksPage !== null) {
      state.nextBooksPage = page
      state.booksError = error.message || '书架加载失败，请重试。'
    } else {
      loadPreviewBooks(error.message || '绘本列表暂时不可用，已切换到本地预览。')
    }
  } finally {
    if (firstShelfPage === pageRequest) firstShelfPage = null
    if (request === booksRequest) state.booksLoading = false
  }
}

function loadMoreBooks() {
  if (!state.booksLoading && state.nextBooksPage && state.auth.status === 'authenticated') {
    return fetchBooksPage(state.nextBooksPage)
  }
}

function clearRecommendations() {
  recommendationRequest += 1
  state.recommendations = []
  state.recommendationsError = ''
  state.recommendationsLoading = false
}

async function loadRecommendations(topic = state.recommendationTopic) {
  const profile = activeProfile()
  if (!profile || !state.auth.authenticated || state.auth.role !== 'GUARDIAN') {
    clearRecommendations()
    return false
  }
  const epoch = authEpoch
  const request = ++recommendationRequest
  const isCurrent = () => epoch === authEpoch && request === recommendationRequest && activeProfile()?.childProfileId === profile.childProfileId
  const nextTopic = String(topic || '').trim()
  if (nextTopic !== state.recommendationTopic) state.recommendations = []
  state.recommendationTopic = nextTopic
  state.recommendationsLoading = true
  state.recommendationsError = ''
  try {
    const items = await recommendStorybooks({
      childProfileId: profile.childProfileId,
      topic: state.recommendationTopic || undefined,
      minutes: profile.preferredMinutes,
    })
    if (!isCurrent()) return false
    const books = new Map(state.books.filter((book) => book.source === 'api').map((book) => [book.storyVersionId, book]))
    const missing = items.map((item) => item.storyVersionId).filter((id) => !books.has(id))
    const loaded = await Promise.allSettled(missing.map((id) => getStoryVersion(id)))
    if (!isCurrent()) return false
    for (const result of loaded) {
      if (result.status === 'fulfilled') {
        const book = mapApiBook(result.value)
        books.set(book.storyVersionId, book)
      }
    }
    state.recommendations = items.flatMap((item) => {
      const book = books.get(item.storyVersionId)
      return book ? [{ ...item, book }] : []
    })
    return true
  } catch (error) {
    if (isCurrent()) {
      state.recommendations = []
      state.recommendationsError = describeApiError(error)
    }
    return false
  } finally {
    if (isCurrent()) state.recommendationsLoading = false
  }
}

function hasAdminSession() {
  return state.auth.status === 'authenticated' && state.auth.authenticated && state.auth.role === 'ADMIN'
}

async function trackAdminWrite(request) {
  pendingAdminWrite = request
  try {
    return await request
  } finally {
    if (pendingAdminWrite === request) pendingAdminWrite = null
  }
}

async function loadAdminContent(section = state.route.section, { append = false } = {}) {
  const knowledge = section === 'knowledge'
  const loading = knowledge ? 'loading' : 'storiesLoading'
  const loaded = knowledge ? 'knowledgeLoaded' : 'storiesLoaded'
  const errorField = knowledge ? 'knowledgeError' : 'error'
  const list = knowledge ? listAdminKnowledge : listAdminStoryVersions
  const itemsField = knowledge ? 'knowledgeItems' : 'storyVersions'
  const nextField = knowledge ? 'nextKnowledgePage' : 'nextStoryPage'
  const idField = knowledge ? 'knowledgeItemId' : 'storyVersionId'
  const page = append ? state.admin[nextField] : 1
  if (!page || !hasAdminSession() || state.admin[loading]) return false
  const epoch = authEpoch
  state.admin[loading] = true
  state.admin[errorField] = ''
  try {
    const pageSize = 50
    const { items } = await list({ page, pageSize })
    if (epoch !== authEpoch) return false
    const merged = append ? [...state.admin[itemsField], ...items] : items
    state.admin[itemsField] = [...new Map(merged.map(item => [item[idField], item])).values()]
    state.admin[nextField] = items.length === pageSize ? page + 1 : null
    state.admin[loaded] = true
    return true
  } catch (error) {
    if (epoch === authEpoch) state.admin[errorField] = error.status === 404
      ? (knowledge ? '后端尚未提供知识管理接口。' : '后端尚未提供绘本列表接口。') : describeApiError(error)
    return false
  } finally {
    if (epoch === authEpoch) state.admin[loading] = false
  }
}

async function adminCreateStory(payload) {
  if (state.admin.busy || !hasAdminSession()) return false
  const epoch = authEpoch
  state.admin.busy = true
  state.admin.error = ''
  try {
    const story = await trackAdminWrite(createAdminStory(payload))
    if (epoch !== authEpoch) return false
    state.admin.storyVersions = [story, ...state.admin.storyVersions]
    setToast('绘本草稿已创建')
    return true
  } catch (error) {
    if (epoch === authEpoch) {
      state.admin.error = error.status === 404 ? '后端尚未提供绘本创建接口。' : describeApiError(error)
      setToast(state.admin.error, 'error')
    }
    return false
  } finally {
    if (epoch === authEpoch) state.admin.busy = false
  }
}

async function adminSetStoryStatus(versionId, status) {
  if (state.admin.busy || !hasAdminSession()) return false
  const epoch = authEpoch
  state.admin.busy = true
  state.admin.error = ''
  try {
    const story = await trackAdminWrite(updateAdminStoryStatus(versionId, status))
    if (epoch !== authEpoch) return false
    const published = story.status === 'PUBLISHED'
    state.admin.storyVersions = state.admin.storyVersions.map((item) => {
      if (item.storyVersionId === versionId) return { ...item, ...story }
      if (published && item.storybookId === story.storybookId && item.status === 'PUBLISHED') {
        return { ...item, status: 'RETIRED' }
      }
      return item
    })
    if (published) {
      state.admin.storiesLoaded = false
      await loadAdminContent('storybooks')
      if (epoch !== authEpoch) return false
    }
    setToast(status === 'PUBLISHED' ? '绘本已发布' : '绘本已撤回')
    return true
  } catch (error) {
    if (epoch === authEpoch) {
      state.admin.error = describeApiError(error)
      setToast(state.admin.error, 'error')
    }
    return false
  } finally {
    if (epoch === authEpoch) state.admin.busy = false
  }
}

async function adminCreateKnowledge(payload) {
  if (state.admin.busy || !hasAdminSession()) return false
  const epoch = authEpoch
  state.admin.busy = true
  state.admin.knowledgeError = ''
  try {
    const item = await trackAdminWrite(createAdminKnowledge(payload))
    if (epoch !== authEpoch) return false
    state.admin.knowledgeItems = [item, ...state.admin.knowledgeItems]
    setToast(item.status === 'APPROVED' ? '知识条目已创建并审核' : '知识草稿已创建')
    return true
  } catch (error) {
    if (epoch === authEpoch) {
      state.admin.knowledgeError = describeApiError(error)
      setToast(state.admin.knowledgeError, 'error')
    }
    return false
  } finally {
    if (epoch === authEpoch) state.admin.busy = false
  }
}

async function adminUpdateKnowledge(knowledgeId, payload) {
  if (state.admin.busy || !hasAdminSession()) return false
  const epoch = authEpoch
  state.admin.busy = true
  state.admin.knowledgeError = ''
  try {
    const item = await trackAdminWrite(updateAdminKnowledge(knowledgeId, payload))
    if (epoch !== authEpoch) return false
    await loadAdminContent('knowledge')
    if (epoch !== authEpoch) return false
    setToast(item.status === 'RETIRED' ? '知识条目已停用' : '已创建新的知识版本')
    return true
  } catch (error) {
    if (epoch === authEpoch) {
      state.admin.knowledgeError = describeApiError(error)
      setToast(state.admin.knowledgeError, 'error')
    }
    return false
  } finally {
    if (epoch === authEpoch) state.admin.busy = false
  }
}

function hasGuardianSession() {
  return state.auth.status === 'authenticated'
    && state.auth.authenticated
    && state.auth.role === 'GUARDIAN'
}

function workshopPayload(form) {
  const topic = String(form?.topic || '').trim()
  const scene = String(form?.scene || '').trim()
  const characters = [...new Set((form?.characters || []).map((item) => String(item).trim()).filter(Boolean))]
  if (!topic || !scene || !characters.length) return { error: '请完整选择主题、角色和场景。' }
  if (topic.length > 120 || scene.length > 120 || characters.some((item) => item.length > 120)) {
    return { error: '故事条件每项不能超过 120 个字符。' }
  }
  if (characters.length > 8) return { error: '最多同时使用 8 个角色。' }
  const continuation = state.workshop.continuation
  if (!continuation && !['AGE_3_4', 'AGE_4_5', 'AGE_5_6'].includes(form?.ageBand)) {
    return { error: '请选择适读年龄段。' }
  }
  if (continuation && (
    continuation.session.status !== 'IN_PROGRESS'
    || continuation.session.mode !== 'PARENT_CHILD'
  )) {
    return { error: '当前阅读会话已不能继续创作。' }
  }
  return {
    payload: {
      ...(continuation
        ? {
            sourceReadingSessionId: continuation.session.readingSessionId,
            expectedRevision: continuation.session.revision,
          }
        : { ageBand: form.ageBand }),
      topic,
      characters,
      scene,
      branching: Boolean(form?.branching),
      ...(form?.generateIllustrations ? { generateIllustrations: true } : {}),
      clientRequestId: createClientEventId('story'),
    },
  }
}

async function runWorkshopGeneration(payload, checking = false) {
  if (!hasGuardianSession()) {
    setToast('请先登录家长账号。', 'error')
    return false
  }
  if (['running', 'checking'].includes(state.workshop.generationStatus)) return false
  const epoch = authEpoch
  const request = ++workshopRequest
  state.workshop.pendingGeneration = payload
  state.workshop.generationStatus = checking ? 'checking' : 'running'
  state.workshop.generationError = ''
  try {
    const draft = await generateStory(payload)
    if (epoch !== authEpoch || request !== workshopRequest) return false
    state.workshop.draft = draft
    state.workshop.approvedVersion = null
    state.workshop.generationStatus = 'review'
    state.workshop.pendingGeneration = null
    setToast('草稿已生成，等待你逐页审核')
    return true
  } catch (error) {
    if (epoch !== authEpoch || request !== workshopRequest) return false
    if (error.code === 'CONTINUATION_STALE' && payload.sourceReadingSessionId) {
      if (await loadWorkshopContinuation(payload.sourceReadingSessionId)) {
        setToast('源会话进度已更新，请核对已读页数后再生成。')
      }
      return false
    }
    if (error.status === 504 && error.code === 'REQUEST_OUTCOME_UNKNOWN') {
      state.workshop.generationStatus = 'endedUnknown'
      state.workshop.generationError = '服务端已结束这次请求，无法确认生成结果。你可以结束本次创作，再决定是否重新生成。'
      state.workshop.pendingGeneration = null
      return false
    }
    const unresolved = error.code === 'REQUEST_IN_PROGRESS'
      || error.code === 'REQUEST_OUTCOME_UNKNOWN'
      || error.status === 504
    state.workshop.generationStatus = unresolved ? 'unknown' : 'failed'
    const message = payload.generateIllustrations && error.code === 'DEPENDENCY_UNAVAILABLE'
      ? error.message : describeApiError(error)
    state.workshop.generationError = `${message}${
      unresolved ? ' 可稍后使用同一请求确认生成结果。' : ''
    }`
    if (!unresolved) state.workshop.pendingGeneration = null
    setToast(state.workshop.generationError, 'error')
    return false
  }
}

async function generateWorkshopDraft(form) {
  if (['running', 'checking', 'endedUnknown'].includes(state.workshop.generationStatus)) return false
  if (['review', 'approved'].includes(state.workshop.generationStatus) && state.workshop.draft) return false
  if (state.workshop.generationStatus === 'unknown' && state.workshop.pendingGeneration) {
    return retryWorkshopGeneration()
  }
  const result = workshopPayload(form)
  if (result.error) {
    state.workshop.generationError = result.error
    return false
  }
  return runWorkshopGeneration(result.payload)
}

async function retryWorkshopGeneration() {
  const request = state.workshop.pendingGeneration
  if (!request) {
    state.workshop.generationError = '没有可确认的生成请求，请重新填写条件。'
    return false
  }
  return runWorkshopGeneration(request, true)
}

function resetWorkshopDraft() {
  if (['running', 'checking'].includes(state.workshop.generationStatus)) return false
  if (state.workshop.approvalBusy || state.workshop.switchBusy) return false
  state.workshop.draft = null
  state.workshop.approvedVersion = null
  state.workshop.generationStatus = 'idle'
  state.workshop.generationError = ''
  state.workshop.pendingGeneration = null
  state.workshop.switchPending = null
  state.workshop.switchError = ''
  return true
}

async function loadWorkshopContinuation(sessionId) {
  if (!sessionId || !hasGuardianSession()) return false
  const samePage = currentWorkshopPage()
  const request = ++workshopRequest
  state.workshop = {
    ...emptyWorkshopState(),
    continuationLoading: true,
  }
  try {
    const detail = await getReadingSession(sessionId)
    if (!samePage() || request !== workshopRequest) return false
    const version = await getStoryVersion(detail.session.storyVersionId)
    if (!samePage() || request !== workshopRequest) return false
    if (detail.session.status !== 'IN_PROGRESS' || detail.session.mode !== 'PARENT_CHILD') {
      throw new Error('只有进行中的亲子共读会话可以续写。')
    }
    if (!canContinueStory(detail.session, version)) {
      throw new Error('只有家长审核过的私人原创绘本可以续写。')
    }
    const profileId = detail.session.childProfileId
    if (!Number.isSafeInteger(profileId) || profileId < 1) {
      throw new Error('无法确认源会话所属的儿童档案。')
    }
    state.workshop.continuation = {
      session: detail.session,
      profile: state.profiles.find(profile => profile.childProfileId === profileId),
      version,
      resumePageNumber: (detail.session.visitedPageNumbers || []).length + 1,
    }
    return true
  } catch (error) {
    if (samePage() && request === workshopRequest) {
      state.workshop.continuationError = error instanceof ApiError ? describeApiError(error) : error.message
      setToast(state.workshop.continuationError, 'error')
    }
    return false
  } finally {
    if (request === workshopRequest) state.workshop.continuationLoading = false
  }
}

function installApprovedWorkshopVersion(version) {
  state.workshop.draft = version
  state.workshop.approvedVersion = version
  state.workshop.shelfError = ''
  state.workshop.generationStatus = 'approved'
  state.workshop.generationError = ''
  state.workshop.pendingGeneration = null
}

async function recoverApprovedWorkshopVersion(versionId) {
  try {
    const version = await getStoryVersion(versionId)
    return version?.status === 'APPROVED_PRIVATE' ? version : null
  } catch {
    return null
  }
}

async function approveWorkshopDraft() {
  const draft = state.workshop.draft
  if (!draft || draft.status !== 'PENDING_REVIEW' || state.workshop.approvalBusy) return false
  const epoch = authEpoch
  const workshop = state.workshop
  const isCurrent = () => epoch === authEpoch && state.workshop === workshop
  workshop.approvalBusy = true
  state.workshop.generationError = ''
  try {
    const approved = await approveStoryVersion(draft.storyVersionId, draft.versionNumber)
    if (!isCurrent()) return false
    installApprovedWorkshopVersion(approved)
    setToast('审核完成，绘本已进入你的私人书架')
    return true
  } catch (error) {
    if (!isCurrent()) return false
    if (error.code === 'REQUEST_OUTCOME_UNKNOWN' || error.status === 409 || error.status === 504) {
      const recovered = await recoverApprovedWorkshopVersion(draft.storyVersionId)
      if (!isCurrent()) return false
      if (recovered) {
        installApprovedWorkshopVersion(recovered)
        setToast('审核结果已确认，绘本已进入你的私人书架')
        return true
      }
    }
    state.workshop.generationError = describeApiError(error)
    setToast(state.workshop.generationError, 'error')
    return false
  } finally {
    if (isCurrent()) workshop.approvalBusy = false
  }
}

function currentWorkshopPage() {
  const epoch = authEpoch
  const route = state.route
  const hash = location.hash
  return () => epoch === authEpoch && route === state.route && hash === location.hash
}

async function openApprovedPrivateShelf() {
  const workshop = state.workshop
  const version = workshop.approvedVersion
  if (!version || workshop.shelfBusy || !hasGuardianSession()) return false
  const samePage = currentWorkshopPage()
  const id = `version-${version.storyVersionId}`
  workshop.shelfBusy = true
  workshop.shelfError = ''
  try {
    let loaded = await loadBooks({ ageBand: '', topic: '' }, null)
    while (samePage() && state.workshop === workshop && workshop.approvedVersion === version) {
      if (!loaded || state.booksSource !== 'api') {
        workshop.shelfError = state.booksError || '暂时无法读取私人书架，请重试。'
        return false
      }
      if (state.books.some(book => book.id === id)) {
        selectBook(id)
        navigate('library')
        return true
      }
      if (!state.nextBooksPage) {
        workshop.shelfError = '当前书架中未找到这本绘本。'
        return false
      }
      loaded = await fetchBooksPage(state.nextBooksPage)
    }
    return false
  } finally {
    workshop.shelfBusy = false
  }
}

async function switchWorkshopContinuation() {
  const workshop = state.workshop
  const target = workshop.approvedVersion
  const continuation = workshop.continuation
  if (workshop.switchBusy) return false
  if (!target?.continuation || !continuation?.session) {
    state.workshop.switchError = '当前版本不含可切换的续写快照。'
    return false
  }
  const payload = state.workshop.switchPending || {
    childProfileId: continuation.session.childProfileId,
    storyVersionId: target.storyVersionId,
    mode: 'PARENT_CHILD',
    sourceReadingSessionId: continuation.session.readingSessionId,
    expectedRevision: target.continuation.sourceRevision,
    clientRequestId: createClientEventId('switch'),
  }
  const samePage = currentWorkshopPage()
  state.workshop.switchPending = payload
  state.workshop.switchBusy = true
  state.workshop.switchError = ''
  try {
    const session = await createReadingSession(payload)
    if (!samePage() || state.workshop !== workshop) return false
    state.workshop.switchPending = null
    state.currentSession = session
    state.currentBook = mapApiBook(target)
    state.currentEvents = []
    state.currentReport = null
    navigate(`reader/${session.readingSessionId}`)
    setToast(`已从第 ${session.currentPage} 页继续阅读`)
    void loadSessions()
    return true
  } catch (error) {
    if (!samePage() || state.workshop !== workshop) return false
    const unresolved = error.code === 'REQUEST_IN_PROGRESS'
      || error.code === 'REQUEST_OUTCOME_UNKNOWN'
      || error.status === 504
    if (!unresolved) state.workshop.switchPending = null
    if (error.code === 'CONTINUATION_STALE') {
      if (await loadWorkshopContinuation(continuation.session.readingSessionId) && samePage()) {
        setToast('源会话进度已更新，请核对前文后重新生成续写。')
      }
      return false
    }
    state.workshop.switchError = `${describeApiError(error)}${
      unresolved ? ' 可稍后确认原切换请求的结果。' : ''
    }`
    setToast(state.workshop.switchError, 'error')
    return false
  } finally {
    workshop.switchBusy = false
  }
}

function refreshShelfAfterNotFound() {
  state.pendingBookRestore = shelfRestoreTarget()
  state.nextBooksPage = 1
  return fetchBooksPage(1)
}

async function loadProfiles() {
  const epoch = authEpoch
  const request = ++profilesRequest
  const isCurrent = () => epoch === authEpoch && request === profilesRequest
  state.profilesLoading = true
  state.profilesError = ''
  try {
    const items = await listProfiles()
    if (!isCurrent()) return null
    state.profiles = items
    const previousId = state.activeProfileId
    // Only a new login defaults to the first child. A removed selection needs a guardian's choice.
    if (state.activeProfileId === null) {
      state.activeProfileId = items[0]?.childProfileId || 0
    } else if (!items.some((profile) => profile.childProfileId === state.activeProfileId)) {
      state.activeProfileId = 0
    }
    if (previousId !== state.activeProfileId) clearRecommendations()
    if (items.length) localStorage.setItem('zhiya.activeProfileId', String(state.activeProfileId))
    else localStorage.removeItem('zhiya.activeProfileId')
    return items
  } catch (error) {
    if (!isCurrent()) return null
    state.profilesError = describeApiError(error)
    throw error
  } finally {
    if (isCurrent()) state.profilesLoading = false
  }
}

async function bootstrap() {
  const epoch = authEpoch
  try {
    await checkHealth()
    state.health = 'ready'
  } catch {
    state.health = 'offline'
  }
  if (epoch === authEpoch) await refreshIdentity()
}

function invalidateChangedIdentity() {
  const csrf = readCsrfCookie()
  const accountChanged = state.auth.authenticated && state.auth.account !== (localStorage.getItem('zhiya.account') || '')
  if (csrf === knownCsrf && !accountChanged) return
  knownCsrf = csrf
  authEpoch += 1
  state.accountViewKey += 1
  clearAccountViews()
  state.activeProfileId = savedProfileId()
  state.auth = {
    status: 'checking', authenticated: false, role: null, accountId: null,
    account: '', nickname: localStorage.getItem('zhiya.guardianNickname') || '知芽家长',
  }
}

function syncStoredIdentity(event) {
  if (event.storageArea === localStorage && [null, 'zhiya.account', 'zhiya.role', CHILD_SESSION_KEY].includes(event.key)) {
    return refreshIdentity()
  }
}

function beginHandoffCheck(sessionId, { resumeReader = false, account = state.auth.account } = {}) {
  authEpoch += 1
  knownCsrf = readCsrfCookie()
  clearAccountViews()
  rememberChildSession(sessionId)
  state.auth = {
    ...state.auth, account, status: 'verifying-handoff', authenticated: false, role: null, accountId: null,
    resumeReaderId: resumeReader ? sessionId : null,
  }
  replaceRoute(sessionId ? `child/${sessionId}` : 'child')
}

async function refreshIdentity() {
  if (state.auth.status === 'signing-out') return
  invalidateChangedIdentity()
  const epoch = authEpoch
  const previous = state.auth.status
  const verifying = previous === 'verifying-handoff'
  if (previous === 'child' || previous === 'handoff' || verifying) state.activeProfileId = savedProfileId()
  try {
    const identity = await currentIdentity()
    if (epoch !== authEpoch) return
    if (identity.role === 'ADMIN') {
      state.auth = { ...state.auth, status: 'authenticated', authenticated: true, role: 'ADMIN',
        accountId: identity.accountId, account: localStorage.getItem('zhiya.account') || '' }
      if (state.route.name !== 'admin') replaceRoute('admin/storybooks')
      await handleRoute()
      return
    }
    const profiles = await loadProfiles()
    if (!profiles || epoch !== authEpoch) return
    if (previous === 'authenticated') return
    rememberChildSession(null)
    state.childMode = null
    state.currentSession = null
    state.currentBook = null
    state.sessionLoading = false
    state.auth.accountId = null
    state.auth.account = localStorage.getItem('zhiya.account') || (verifying ? state.auth.account : '')
    if (verifying && state.auth.account) localStorage.setItem('zhiya.account', state.auth.account)
    state.auth.status = 'authenticated'
    state.auth.authenticated = true
    state.auth.role = 'GUARDIAN'
    localStorage.setItem('zhiya.role', state.auth.role)
    restorePendingSessionDeletes()
    const readerId = state.auth.resumeReaderId
    delete state.auth.resumeReaderId
    if (state.route.name === 'child') {
      replaceRoute(readerId ? `reader/${readerId}` : 'account/overview')
      await Promise.all([loadBooks(), handleRoute()])
      return
    }
    await Promise.all([loadBooks({}, localStorage.getItem(bookSelectionKey('api'))), handleRoute()])
    if (epoch === authEpoch) void loadRecommendations()
    return
  } catch (error) {
    if (epoch !== authEpoch) return
    const remembered = savedChildSessionId()
    const hasHandoff = remembered || state.route.name === 'child' || previous === 'child' || previous === 'handoff' || verifying
    // API-004 also returns 401 once a child's reading time expires. Only API-021
    // can distinguish that still-unlockable credential from a signed-out device.
    const isChild = error.status === 403 && error.code === 'FORBIDDEN'
    if (isChild || (hasHandoff && error.status === 401)) {
      const status = isChild ? 'child' : 'handoff'
      if (previous !== status || (state.childMode && remembered && state.childMode.sessionId !== remembered)) {
        authEpoch += 1
        clearAccountViews()
      }
      state.profilesError = ''
      state.auth = { ...state.auth, status, authenticated: false, role: isChild ? 'CHILD' : null, accountId: null }
      delete state.auth.resumeReaderId
    } else if (hasHandoff && (verifying || previous === 'checking' || previous === 'unavailable')) {
      state.auth.status = 'verifying-handoff'
    } else if (error.status === 401) {
      if (previous === 'anonymous') {
        state.profilesError = ''
        return
      }
      resetAuthentication()
      loadPreviewBooks('登录后读取后端绘本，当前展示本地设计预览。')
    } else if (previous === 'checking' || previous === 'unavailable') {
      state.auth.status = 'unavailable'
      loadPreviewBooks(state.health === 'offline' ? '后端暂未启动，当前展示本地设计预览。' : describeApiError(error))
      // A failed identity retry does not invalidate an online detail already loaded for this route.
      if (state.route.name === 'detail' && state.currentBook?.source === 'api' && state.currentBook.storyVersionId === state.route.versionId) return
    } else {
      return
    }
  }
  await handleRoute()
}

async function signIn(account, password) {
  resetAuthentication()
  const epoch = authEpoch
  const entryHash = location.hash
  let result
  try {
    result = await login(account, password)
  } catch (error) {
    if (epoch === authEpoch && error.status === 403 && error.code === 'FORBIDDEN') {
      state.auth = { ...state.auth, status: 'handoff', role: null, account }
      replaceRoute('child')
    }
    throw error
  }
  if (epoch !== authEpoch) return
  const identity = await currentIdentity()
  if (epoch !== authEpoch) return
  if (identity.accountId !== result.data.accountId || !['ADMIN', 'GUARDIAN'].includes(identity.role)) {
    throw new Error('登录身份已变化，请刷新后重试。')
  }
  knownCsrf = readCsrfCookie()
  state.auth.accountId = identity.accountId
  state.auth.account = account
  state.auth.authenticated = true
  state.auth.status = 'authenticated'
  state.auth.role = identity.role
  localStorage.setItem('zhiya.account', account)
  localStorage.setItem('zhiya.role', state.auth.role)
  if (state.auth.role === 'ADMIN') {
    state.auth.nickname = account
    navigate('admin/storybooks')
    setToast('已登录内容管理空间')
    return
  }
  const params = new URLSearchParams(location.search)
  if (state.route.name === 'auth' && params.get('entry') === 'mobile') {
    const route = parseMobileRoute(params.get('mobileRoute') || '')
    window.location.replace(`/mobile.html${route.hash}`)
    return
  }
  await loadProfiles()
  if (epoch !== authEpoch) return
  await loadBooks()
  if (epoch !== authEpoch) return
  setToast('已登录知芽家长空间')
  const route = parseRoute()
  if (route.name === 'reader' && route.sessionId) {
    const readingSameRoute = state.route.name === 'reader' && state.route.sessionId === route.sessionId
    if (!readingSameRoute || (!state.sessionLoading && state.currentSession?.readingSessionId !== route.sessionId)) {
      await syncRoute()
    }
  } else if (route.name === 'account' && ['growth', 'overview'].includes(route.section)) {
    if (!state.sessionsLoaded && !state.sessionsLoading) await syncRoute()
  } else if (location.hash === entryHash) {
    if (route.name === 'workshop') {
      await handleRoute()
    } else {
      navigate(state.books.length ? 'library' : 'account/children')
    }
  }
  if (epoch === authEpoch) await loadRecommendations()
}

async function signUp(account, password) {
  await register(account, password)
  try {
    await signIn(account, password)
  } catch {
    const message = '账号已创建，自动登录未完成。请用刚注册的账号登录。'
    setToast(message, 'error')
    throw new ApiError(message, { code: 'ACCOUNT_CREATED_LOGIN_FAILED' })
  }
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
      if (state.auth.role === 'ADMIN') {
        // Read the catalogue after any already-sent mutation settles. Never replay the mutation.
        if (pendingAdminWrite) await Promise.allSettled([pendingAdminWrite])
        if (epoch !== authEpoch) return false
        clearAccountViews()
        state.auth = { ...state.auth, status: 'checking', authenticated: false, role: null, accountId: null }
        await refreshIdentity()
        if (state.auth.status === 'anonymous') return true
        if (epoch !== authEpoch) return false
        if (state.auth.status === 'unavailable') {
          setToast('退出尚未确认。请在网络恢复后重新确认身份。', 'error')
          return false
        }
      } else {
        state.auth.status = 'authenticated'
        state.sessionBusy = false
        state.readingSyncRequired = Boolean(state.currentSession && !state.currentSession.preview)
      }
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
  clearRecommendations()
  state.activeProfileId = Number(profileId)
  localStorage.setItem('zhiya.activeProfileId', String(state.activeProfileId))
  setToast(`已切换到${activeProfile()?.nickname || '孩子的'}阅读空间`)
  void loadRecommendations()
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
  if (state.activeProfileId === profileId) {
    clearRecommendations()
    void loadRecommendations()
  }
  setToast('儿童档案已更新')
  return profile
}

function applyProfileRemoval(profileId) {
  state.profiles = state.profiles.filter((profile) => profile.childProfileId !== profileId)
  state.sessions = []
  state.nextSessionsPage = null
  state.sessionsLoaded = false
  if (!activeProfile()) {
    clearRecommendations()
    state.activeProfileId = 0
    readingEpoch += 1
    state.sessionLoading = state.sessionBusy = false
    state.currentSession = null
    state.currentBook = null
    state.currentEvents = []
    state.currentReport = null
    if (state.profiles.length) localStorage.setItem('zhiya.activeProfileId', '0')
    else localStorage.removeItem('zhiya.activeProfileId')
  }
  void loadSessions()
}

function applySessionRemoval(sessionId) {
  const wasLoading = state.sessionsLoading
  sessionsRequest += 1
  state.sessions = state.sessions.filter((session) => session.readingSessionId !== sessionId)
  if (state.currentSession?.readingSessionId === sessionId) {
    readingEpoch += 1
    state.sessionLoading = state.sessionBusy = false
    state.currentSession = null
    state.currentBook = null
    state.currentEvents = []
    state.currentReport = null
  }
  if (wasLoading || state.nextSessionsPage) void loadSessions()
}

function markSessionDelete(id, pending) {
  const ids = state.pendingDeletionChecks.session
  if (pending) ids.add(id)
  else ids.delete(id)
  if (state.auth.account && ids.size) {
    sessionStorage.setItem(PENDING_SESSION_DELETES, JSON.stringify({ account: state.auth.account, ids: [...ids] }))
  } else {
    sessionStorage.removeItem(PENDING_SESSION_DELETES)
  }
}

function restorePendingSessionDeletes() {
  const saved = sessionStorage.getItem(PENDING_SESSION_DELETES)
  if (!saved) return
  try {
    const { account, ids } = JSON.parse(saved)
    if (account && account === state.auth.account && Array.isArray(ids)) {
      for (const id of ids) {
        if (Number.isSafeInteger(id) && id > 0) state.pendingDeletionChecks.session.add(id)
      }
      return
    }
  } catch {
    // Ignore incomplete data from an older browser session.
  }
  sessionStorage.removeItem(PENDING_SESSION_DELETES)
}

async function checkDeletion(type, id) {
  const epoch = authEpoch
  let present
  if (type === 'profile') {
    const items = await loadProfiles()
    if (!items || epoch !== authEpoch) return 'unconfirmed'
    present = items.some(profile => profile.childProfileId === id)
    if (!present) applyProfileRemoval(id)
  } else {
    if (!await loadSessions() || epoch !== authEpoch) return 'unconfirmed'
    present = state.sessions.some(session => session.readingSessionId === id)
    while (!present && state.nextSessionsPage) {
      if (!await loadSessions({ append: true }) || epoch !== authEpoch) return 'unconfirmed'
      present = state.sessions.some(session => session.readingSessionId === id)
    }
    if (!present) {
      // API-012 also omits sessions whose book was withdrawn.
      const exists = await getDeletionStatus(id)
      if (epoch !== authEpoch || typeof exists !== 'boolean') return 'unconfirmed'
      if (exists) return 'unlisted'
      applySessionRemoval(id)
    }
  }
  if (type === 'session') markSessionDelete(id, false)
  else state.pendingDeletionChecks.profile.delete(id)
  if (present) return 'present'
  setToast(type === 'profile' ? '已确认儿童档案不在当前列表中' : '已确认这条阅读记录已不存在')
  return 'absent'
}

function handleDeleteError(error, type, id, epoch) {
  if (epoch !== authEpoch) return false
  if (error.code !== 'REQUEST_OUTCOME_UNKNOWN' && error.status !== 404) throw error
  if (type === 'session') markSessionDelete(id, true)
  else state.pendingDeletionChecks.profile.add(id)
  return 'unconfirmed'
}

async function removeProfile(profileId) {
  if (state.profileDeleteBusyId !== null) return false
  if (state.pendingDeletionChecks.profile.has(profileId)) return 'unconfirmed'
  const epoch = authEpoch
  state.profileDeleteBusyId = profileId
  try {
    await deleteProfile(profileId)
    if (epoch !== authEpoch) return false
    applyProfileRemoval(profileId)
    void reloadProfiles()
    setToast('儿童档案已删除，关联阅读记录已清理')
    return true
  } catch (error) {
    return handleDeleteError(error, 'profile', profileId, epoch)
  } finally {
    if (epoch === authEpoch) state.profileDeleteBusyId = null
  }
}

async function removeReadingSession(sessionId, confirmedUnlisted = false) {
  if (state.sessionDeleteBusyId !== null) return false
  if (state.pendingDeletionChecks.session.has(sessionId) && !confirmedUnlisted) return 'unconfirmed'
  const epoch = authEpoch
  state.sessionDeleteBusyId = sessionId
  let alreadyAbsent = false
  try {
    await deleteReadingSession(sessionId)
  } catch (error) {
    alreadyAbsent = error.status === 404 && error.code === 'RESOURCE_NOT_FOUND'
    if (!alreadyAbsent) return handleDeleteError(error, 'session', sessionId, epoch)
  } finally {
    if (epoch === authEpoch) state.sessionDeleteBusyId = null
  }
  if (epoch !== authEpoch) return false
  markSessionDelete(sessionId, false)
  applySessionRemoval(sessionId)
  if (alreadyAbsent && !state.sessionsLoading) void loadSessions()
  setToast(alreadyAbsent ? '已确认这条阅读记录已不存在' : '阅读记录已删除')
  return true
}

function selectBook(bookId) {
  if (!state.books.some((book) => book.id === bookId)) return
  selectionRevision += 1
  if (state.booksSource === 'api') {
    // A first-page refresh will replace the visible shelf, including the book just picked.
    const refreshingSameShelf = loadedBookFilters === state.bookFilters && (firstShelfPage || state.nextBooksPage === 1)
    state.pendingBookRestore = refreshingSameShelf ? bookId : null
  }
  state.selectedBookId = bookId
  state.manualBookId = bookId
  localStorage.setItem(bookSelectionKey(state.booksSource), bookId)
}

function openDetail(bookOrVersionId) {
  const versionId = Number(bookOrVersionId?.storyVersionId || bookOrVersionId)
  const source = bookOrVersionId?.source
  if (versionId) navigate(source ? `detail/${source}/${versionId}` : `detail/${versionId}`)
}

async function readOnlineDetail(versionId) {
  // Keep overlapping reads in start order. A failed retry cannot settle whether a book is still available.
  const reads = detailReadsByVersion.get(versionId) || []
  const read = { outcome: 'pending', filters: state.bookFilters, epoch: authEpoch }
  reads.push(read)
  detailReadsByVersion.set(versionId, reads)
  try {
    const version = await getStoryVersion(versionId)
    read.outcome = 'available'
    return version
  } catch (error) {
    read.outcome = error.status === 404 ? 'missing' : 'failed'
    throw error
  } finally {
    if (detailReadsByVersion.get(versionId) === reads) {
      while (reads.at(-1)?.outcome === 'failed') reads.pop()
      const latest = reads.at(-1)
      if (latest?.outcome !== 'pending') {
        detailReadsByVersion.delete(versionId)
        if (latest?.outcome === 'missing' && latest.epoch === authEpoch && latest.filters === state.bookFilters) {
          const key = bookSelectionKey('api')
          const missingId = `version-${versionId}`
          if (state.auth.authenticated && localStorage.getItem(key) === missingId) localStorage.removeItem(key)
          if (state.pendingBookRestore === missingId) state.pendingBookRestore = null
          const remaining = state.books.filter((book) => book.storyVersionId !== versionId)
          const listed = state.booksSource === 'api' && remaining.length < state.books.length
          if (listed) applyBooks(remaining, 'api')
          if (listed || state.booksLoading) void refreshShelfAfterNotFound()
        }
      }
    }
  }
}

async function loadDetail(id, source = null) {
  const versionId = Number(id)
  const epoch = authEpoch
  const request = ++detailRequest
  const route = location.hash
  const isCurrent = () => epoch === authEpoch && request === detailRequest && route === location.hash
  state.detailLoading = true
  state.currentBook = null
  try {
    const preview = previewBooks.find((book) => book.storyVersionId === versionId)
    // Legacy numeric links may refer to a demo when the server has confirmed an empty catalog.
    if (!source && preview && firstShelfPage && state.booksSource === 'preview') {
      await firstShelfPage.catch(() => {})
      if (!isCurrent()) return
    }
    if (source === 'preview' || (!source && preview && (state.auth.status === 'anonymous' || previewFromEmptyCatalog))) {
      state.currentBook = preview || null
      return
    }
    const version = await readOnlineDetail(versionId)
    if (!isCurrent()) return
    state.currentBook = mapApiBook(version)
    // Keep a resolved legacy link online across identity retries on this detail page.
    if (!source && state.route.name === 'detail' && state.route.versionId === versionId) state.route.source = 'api'
  } catch (error) {
    if (isCurrent()) setToast(describeApiError(error), 'error')
  } finally {
    if (epoch === authEpoch && request === detailRequest) state.detailLoading = false
  }
}

async function retryReadingIdentity() {
  if (state.auth.status !== 'unavailable' || state.profilesLoading) return
  const epoch = authEpoch
  const route = location.hash
  await refreshIdentity()
  if (epoch === authEpoch && route === location.hash && state.auth.status === 'unavailable') {
    setToast('暂时无法核验家长身份，请稍后重试。', 'error')
  }
}

async function startReading(book, { childSetup = false } = {}) {
  if (!book || state.sessionBusy) return
  const epoch = authEpoch
  if (book.source !== 'preview' && state.auth.status !== 'authenticated') {
    if (state.auth.status === 'unavailable') {
      await retryReadingIdentity()
    } else if (state.auth.status === 'anonymous') {
      navigate('auth')
    }
    return
  }
  state.currentBook = book
  state.currentEvents = []
  state.currentReport = null
  if (book.source === 'preview') {
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
    setToast(state.profiles.length ? '请先选择本次阅读使用的儿童档案' : '请先为孩子创建阅读档案', 'error')
    navigate('account/children')
    return
  }
  // Reapplying the same filters or loading another page keeps the same shelf.
  const filtersAtStart = state.bookFilters
  const creation = readingCreationKey(state.auth.accountId, profile.childProfileId, book.storyVersionId)
  try {
    state.sessionBusy = true
    const created = await createReadingSession({
      childProfileId: profile.childProfileId,
      storyVersionId: book.storyVersionId,
      clientRequestId: creation.requestId,
    })
    creation.clear()
    if (epoch !== authEpoch) return
    state.readingCreationRecovery = null
    state.currentSession = created
    navigate(`reader/${childSetup ? 'setup/' : ''}${created.readingSessionId}`)
  } catch (error) {
    if (epoch !== authEpoch) return
    if (['REQUEST_OUTCOME_UNKNOWN', 'REQUEST_IN_PROGRESS'].includes(error.code) || error.status >= 500) {
      state.readingCreationRecovery = { book, profileId: profile.childProfileId, childSetup }
      setToast('阅读会话是否创建成功尚未确认。可以核对原请求，避免重复创建。', 'error')
      return
    }
    if (creation.isRecovery && [401, 403, 429].includes(error.status)) {
      state.readingCreationRecovery = { book, profileId: profile.childProfileId, childSetup }
      setToast(describeApiError(error), 'error')
      return
    }
    creation.clear()
    state.readingCreationRecovery = null
    if (error.status === 404) {
      // API-011 returns the same 404 for an unavailable profile or book.
      const profiles = await loadProfiles().catch((lookupError) => {
        if (epoch === authEpoch) setToast(describeApiError(lookupError), 'error')
        return null
      })
      if (!profiles || epoch !== authEpoch) return
      if (!profiles.some((item) => item.childProfileId === profile.childProfileId)) {
        if (activeProfile()) return
        setToast(profiles.length ? '原儿童档案已失效，请到个人中心重新选择儿童档案。' : '原儿童档案已失效，请先添加儿童档案。', 'error')
        return
      }
      if (state.bookFilters === filtersAtStart) void refreshShelfAfterNotFound()
    }
    setToast(describeApiError(error), 'error')
  } finally {
    if (epoch === authEpoch) state.sessionBusy = false
  }
}

async function retryReadingCreation() {
  const recovery = state.readingCreationRecovery
  if (!recovery) return
  if (activeProfile()?.childProfileId !== recovery.profileId) {
    setToast('请选择原来的儿童档案，再核对这次阅读请求。', 'error')
    return
  }
  await startReading(recovery.book, { childSetup: recovery.childSetup })
}

function isTemporaryReadFailure(error) {
  return error.status === 503 || error.status === 504 || (error.status === 0 && error.retryable)
}

async function loadReaderRoute() {
  const epoch = authEpoch
  const generation = ++readingEpoch
  activeInitialGuidance = null
  const route = { ...state.route }
  const routeHash = location.hash
  const isCurrent = () => epoch === authEpoch && generation === readingEpoch && routeHash === location.hash
  state.sessionLoading = true
  state.readerLoadRetryable = false
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
    const versionId = detail.session.storyVersionId
    const shelfBook = () => state.books.find((item) => item.source === 'api' && item.storyVersionId === versionId)
    let book = shelfBook()
    if (!book) {
      try {
        book = mapApiBook(await readOnlineDetail(versionId))
      } catch (error) {
        if (!isCurrent()) return
        if (!isTemporaryReadFailure(error)) throw error
        // On cold start the authorized shelf can still be loading the same full version.
        if (firstShelfPage) await firstShelfPage.catch(() => {})
        if (!isCurrent()) return
        book = shelfBook()
        if (!book) throw error
      }
    }
    if (!isCurrent()) return
    applyReadingDetail(detail)
    state.currentBook = book
    if (!route.childSetup && detail.session.status === 'CREATED' && detail.session.mode !== 'CHILD_SOLO') {
      await startCurrentReading()
    }
  } catch (error) {
    if (isCurrent()) {
      state.readerLoadRetryable = isTemporaryReadFailure(error)
      setToast(describeApiError(error), 'error')
      state.currentSession = null
    }
  } finally {
    if (isCurrent()) state.sessionLoading = false
  }
}

async function startCurrentReading() {
  const session = state.currentSession
  if (!session || session.preview || session.status !== 'CREATED' || state.sessionBusy || state.readingSyncRequired) return false
  return writeReading({
    sessionId: session.readingSessionId,
    kind: 'page',
    payload: {
      eventType: 'STARTED',
      pageNumber: session.startPageNumber ?? 1,
      expectedRevision: session.revision,
      clientEventId: createClientEventId('start'),
    },
  })
}

async function enterChildMode(sessionId, allowedStoryVersionIds, timeLimitSeconds) {
  const session = state.currentSession
  const book = state.currentBook
  if (!session || session.preview || session.readingSessionId !== sessionId || session.status !== 'CREATED'
      || session.mode !== 'PARENT_CHILD' || !state.auth.authenticated || state.sessionBusy) {
    return false
  }
  const epoch = authEpoch
  state.sessionBusy = true
  try {
    const result = await updateReadingMode(sessionId, {
      mode: 'CHILD_SOLO',
      allowedStoryVersionIds,
      timeLimitSeconds,
    })
    if (epoch !== authEpoch) return false
    knownCsrf = readCsrfCookie()
    // The same 200 also acknowledges a duration change from a second guardian device.
    // Only install the handoff after the guardian-only endpoint confirms a child credential.
    try {
      await listProfiles()
      if (epoch !== authEpoch) return false
      if (state.currentSession?.readingSessionId === sessionId) {
        Object.assign(state.currentSession, { mode: result.mode, expiresAt: result.expiresAt })
      }
      setToast('儿童授权已更新，本机仍在家长空间。')
      return true
    } catch (error) {
      if (epoch !== authEpoch) return false
      if (error.status !== 403 || error.code !== 'FORBIDDEN') {
        beginHandoffCheck(sessionId)
        if (error.status === 401) state.auth.status = 'handoff'
        else state.profilesError = describeApiError(error)
        return true
      }
    }
    authEpoch += 1
    clearAccountViews()
    state.currentBook = book
    state.currentSession = {
      ...session,
      mode: 'CHILD_SOLO',
      expiresAt: result.expiresAt,
    }
    state.childMode = {
      sessionId,
      bookTitle: state.currentBook?.title || '',
      allowedStoryVersionIds: result.allowedStoryVersionIds,
      allowedStoryVersionCount: result.allowedStoryVersionIds.length,
      expiresAt: result.expiresAt,
    }
    rememberChildSession(sessionId)
    state.auth = { ...state.auth, status: 'child', authenticated: false, role: 'CHILD' }
    navigate(`child/${sessionId}`)
    setToast('儿童阅读模式已开启，请交给儿童端继续')
    return true
  } catch (error) {
    if (epoch !== authEpoch) return false
    if (error.code === 'REQUEST_OUTCOME_UNKNOWN') {
      beginHandoffCheck(sessionId, { resumeReader: true })
      await refreshIdentity()
      return true
    }
    setToast(describeApiError(error), 'error')
    return false
  } finally {
    if (epoch === authEpoch) state.sessionBusy = false
  }
}

async function restoreChildMode(sessionId) {
  if (!sessionId || state.auth.status !== 'child') return false
  const epoch = authEpoch
  state.sessionLoading = true
  try {
    const detail = await getReadingSession(sessionId)
    if (epoch !== authEpoch || state.auth.status !== 'child') return false
    const version = await getStoryVersion(detail.session.storyVersionId)
    if (epoch !== authEpoch || state.auth.status !== 'child') return false
    state.currentSession = detail.session
    state.currentEvents = detail.events || []
    state.currentReport = null
    state.currentBook = mapApiBook(version)
    state.childMode = {
      sessionId,
      bookTitle: state.currentBook.title,
      allowedStoryVersionIds: [],
      allowedStoryVersionCount: null,
      expiresAt: detail.session.expiresAt,
    }
    rememberChildSession(sessionId)
    return true
  } catch (error) {
    if (epoch === authEpoch && state.auth.status === 'child') {
      state.childMode = null
      setToast(describeApiError(error), 'error')
    }
    return false
  } finally {
    if (epoch === authEpoch) state.sessionLoading = false
  }
}

async function unlockChildMode(account, password) {
  if (state.sessionBusy || !['child', 'handoff'].includes(state.auth.status)) return false
  let epoch = authEpoch
  state.sessionBusy = true
  try {
    const result = await parentUnlock(account, password)
    if (epoch !== authEpoch) return false
    epoch = ++authEpoch
    knownCsrf = readCsrfCookie()
    rememberChildSession(null)
    clearAccountViews()
    state.activeProfileId = savedProfileId()
    state.auth = {
      status: 'authenticated',
      authenticated: true,
      role: 'GUARDIAN',
      accountId: result.accountId,
      account,
      nickname: localStorage.getItem('zhiya.guardianNickname') || '知芽家长',
    }
    localStorage.setItem('zhiya.account', account)
    replaceRoute('account/overview')
    setToast('家长身份已恢复，正在读取家长空间')
    await reloadAccountData()
    if (epoch === authEpoch && state.profilesError) {
      setToast('家长身份已恢复，但儿童档案暂时未能读取，可在家长空间重试。', 'error')
    }
    return true
  } catch (error) {
    if (epoch !== authEpoch) return false
    if (error.code === 'REQUEST_OUTCOME_UNKNOWN' || (error.status === 403 && error.code === 'FORBIDDEN')) {
      const csrf = readCsrfCookie()
      const credentialChanged = csrf && csrf !== knownCsrf && error.code === 'REQUEST_OUTCOME_UNKNOWN'
      const sessionId = state.childMode?.sessionId || savedChildSessionId() || state.route.sessionId
      beginHandoffCheck(sessionId, { account: credentialChanged ? account : state.auth.account })
      await refreshIdentity()
      return true
    }
    if (error.status === 401 && error.code === 'AUTH_REQUIRED') {
      resetAuthentication()
      replaceRoute('auth')
      setToast('设备凭证已失效，请重新登录。', 'error')
      return false
    }
    throw error
  } finally {
    if (epoch === authEpoch) state.sessionBusy = false
  }
}

async function reloadProfiles() {
  try {
    return Boolean(await loadProfiles())
  } catch {
    // loadProfiles leaves the failure visible next to the retry button.
    return false
  }
}

async function reloadAccountData() {
  const epoch = authEpoch
  const [profilesReady, , sessionsReady] = await Promise.all([reloadProfiles(), loadBooks(), loadSessions()])
  if (epoch !== authEpoch || !profilesReady || !sessionsReady) return false
  await loadRecommendations()
  if (epoch !== authEpoch) return false
  setToast('家长空间资料已重新读取')
  return true
}

function applyReadingDetail(detail) {
  state.currentSession = detail.session
  state.currentEvents = detail.events || []
  state.currentReport = detail.report
}

function sameReading(operation, epoch, generation) {
  return epoch === authEpoch && generation === readingEpoch && state.currentSession?.readingSessionId === operation.sessionId
}

function appendReadingInteraction(operation, result) {
  const session = state.currentSession
  let event
  if (operation.kind === 'guidance') {
    const initial = operation.payload.feedback === 'INITIAL'
    event = {
      interactionEventId: result.interactionEventId,
      eventType: initial ? 'QUESTION' : 'FEEDBACK',
      pageNumber: operation.payload.pageNumber,
      outputText: result.outputText,
      strategyType: result.strategyType,
      feedback: initial ? null : operation.payload.feedback,
    }
    session.revision = result.revision
  } else if (operation.kind === 'ask') {
    event = { ...result, eventType: 'ASK', pageNumber: operation.payload.pageNumber }
    session.revision = result.revision
  } else if (operation.kind === 'answer') {
    event = {
      interactionEventId: result.interactionEventId,
      eventType: 'ANSWER',
      pageNumber: operation.payload.pageNumber,
      inputText: result.agentReceipt?.reason === 'SAFETY_BLOCK' ? undefined : operation.payload.inputText,
      replyText: result.replyText,
      ...(result.agentReceipt ? { agentReceipt: result.agentReceipt } : {}),
      ...(result.followupQuestion ? { followupQuestion: result.followupQuestion } : {}),
    }
    session.revision = Math.max(session.revision, result.revision)
    if (state.currentEvents.some(item => item.interactionEventId === event.interactionEventId)) {
      state.currentEvents = state.currentEvents.map(item => item.interactionEventId === event.interactionEventId
        ? { ...item, ...event }
        : item)
      return
    }
  } else if (operation.kind === 'branch') {
    event = {
      interactionEventId: result.interactionEventId,
      eventType: 'BRANCH_CHOSEN',
      pageNumber: operation.pageNumber,
      branchId: result.branchId,
    }
    session.currentPage = result.currentPage
    session.visitedPageNumbers = result.visitedPageNumbers
    session.revision = result.revision
    session.status = 'IN_PROGRESS'
  }
  if (event) state.currentEvents = [...state.currentEvents, event]
}

function readingRequest(operation) {
  if (operation.kind === 'finish') return completeReadingSession(operation.sessionId, operation.payload)
  if (operation.kind === 'guidance') return requestReadingGuidance(operation.sessionId, operation.payload)
  if (operation.kind === 'ask') return askReadingQuestion(operation.sessionId, operation.payload)
  if (operation.kind === 'branch') return chooseReadingBranch(operation.sessionId, operation.payload)
  return addReadingEvent(operation.sessionId, operation.payload)
}

function onlyInitialAdvancedSession(initial, expectedRevision, { session, events }) {
  if (!initial || initial.payload.expectedRevision !== expectedRevision) return false
  const latest = events?.at(-1)
  return session.readingSessionId === initial.sessionId && session.status === 'IN_PROGRESS'
    && session.currentPage === initial.payload.pageNumber && session.revision === expectedRevision + 1
    && latest?.eventType === 'QUESTION' && latest.pageNumber === session.currentPage && latest.revision === session.revision
}

async function writeReading(operation) {
  const initial = operation.kind === 'guidance' && operation.payload.feedback === 'INITIAL'
  if (state.sessionBusy || (initial && activeInitialGuidance)) return false
  const epoch = authEpoch
  const generation = readingEpoch
  const routeHash = location.hash
  const supersededInitial = ['page', 'finish'].includes(operation.kind) ? activeInitialGuidance : null
  // User writes supersede an automatic prompt. The server arbitrates via expectedRevision.
  activeInitialGuidance = initial ? operation : null
  if (!initial) state.sessionBusy = true
  const isCurrent = () => routeHash === location.hash && sameReading(operation, epoch, generation) && (!initial || (
    activeInitialGuidance === operation && state.currentSession.status === 'IN_PROGRESS'
    && state.currentSession.currentPage === operation.payload.pageNumber
    && state.currentSession.revision === operation.payload.expectedRevision
  ))
  try {
    const result = await readingRequest(operation)
    if (!isCurrent()) return false
    if (operation.kind === 'finish') {
      state.currentSession = { ...state.currentSession, ...result.session }
      state.currentReport = result.report
      setToast('阅读记录已保存')
    } else if (operation.kind === 'guidance') {
      appendReadingInteraction(operation, result)
      const feedback = operation.payload.feedback
      setToast(feedback === 'INITIAL'
        ? '已生成本页共读问题'
        : feedback === 'SKIPPED'
          ? '已跳过本页提问'
          : result.outputText?.trim()
            ? '已根据理解程度换一个引导问题'
            : '已记录理解反馈，保留当前问题')
    } else if (operation.kind === 'ask') {
      appendReadingInteraction(operation, result)
      setToast('已保存本页问答')
    } else if (operation.kind === 'answer') {
      appendReadingInteraction(operation, result)
      setToast('孩子的回答已保存')
    } else if (operation.kind === 'branch') {
      appendReadingInteraction(operation, result)
      setToast(`已进入第 ${result.currentPage} 页的故事分支`)
    } else {
      const session = state.currentSession
      // Keep the visit boundary in the same order as API-013 snapshots.
      if (result.interactionEventId) state.currentEvents = [...state.currentEvents, {
        interactionEventId: result.interactionEventId, eventType: operation.payload.eventType, pageNumber: operation.payload.pageNumber,
      }]
      session.currentPage = operation.payload.pageNumber
      session.visitedPageNumbers = [...session.visitedPageNumbers, operation.payload.pageNumber]
      session.status = 'IN_PROGRESS'
      session.revision = result.revision
    }
    pendingReading = null
    state.readingSyncRequired = false
    return true
  } catch (error) {
    if (!isCurrent()) return false
    const terminalUnknown = error.status === 504 && error.code === 'REQUEST_OUTCOME_UNKNOWN'
    const answerMayBeSaved = operation.kind === 'answer' && error.status === 503 && error.retryable
    const retrySameKey = error.status === 0 || error.code === 'REQUEST_IN_PROGRESS'
      || (error.status === 504 && !terminalUnknown) || answerMayBeSaved
    pendingReading = retrySameKey ? { ...operation, retryAt: Date.now() + (Number(error.retryAfter) || 1) * 1000 } : null
    state.readingSyncRequired = retrySameKey || terminalUnknown || error.status === 409
    if (state.readingSyncRequired) {
      try {
        const detail = await getReadingSession(operation.sessionId)
        if (!isCurrent()) return false
        applyReadingDetail(detail)
        // An answer advances the revision before its reply is ready. Replay its original key after readback.
        if (!pendingReading || (operation.kind !== 'answer' && detail.session.revision !== operation.payload.expectedRevision)) {
          pendingReading = null
          state.readingSyncRequired = false
        }
        if (error.status === 409 && error.code === 'SESSION_CONFLICT'
            && routeHash === location.hash && state.route.name === 'reader' && state.route.sessionId === operation.sessionId
            && onlyInitialAdvancedSession(supersededInitial, operation.payload.expectedRevision, detail)) {
          // Only the automatic question won. Reapply the user's action to its confirmed revision.
          state.sessionBusy = false
          if (operation.kind === 'finish') return await completeReading()
          return await recordPageChange(operation.payload.pageNumber)
        }
      } catch {
        // Keep writes disabled until the authoritative session can be read.
        if (!isCurrent()) return false
      }
    }
    if (terminalUnknown && !state.readingSyncRequired) {
      setToast('已按服务器记录同步进度，请根据当前页面决定是否继续。')
    } else {
      setToast(`${describeApiError(error)}${state.readingSyncRequired ? ' 请使用“确认并同步进度”。' : ''}`, 'error')
    }
    return false
  } finally {
    if (initial) {
      if (activeInitialGuidance === operation) activeInitialGuidance = null
    } else if (sameReading(operation, epoch, generation)) state.sessionBusy = false
  }
}

async function refreshReading() {
  const session = state.currentSession
  if (!session || session.preview || state.sessionBusy) return false
  const epoch = authEpoch
  const generation = readingEpoch
  const target = { sessionId: session.readingSessionId }
  const routeHash = location.hash
  const isCurrent = () => routeHash === location.hash && sameReading(target, epoch, generation)
  state.sessionBusy = true
  try {
    const detail = await getReadingSession(target.sessionId)
    if (!isCurrent()) return false
    if (session.status === 'FINISHED' && !state.readingSyncRequired) {
      state.currentReport = detail.report
      return true
    }
    applyReadingDetail(detail)
    const operation = pendingReading
    if (operation && (operation.kind === 'answer' || (
      ['CREATED', 'IN_PROGRESS'].includes(detail.session.status) && detail.session.revision === operation.payload.expectedRevision
    ))) {
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
    if (isCurrent()) setToast(describeApiError(error), 'error')
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
    session.visitedPageNumbers = [...session.visitedPageNumbers, pageNumber]
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

async function requestGuidance(feedback = 'INITIAL') {
  const session = state.currentSession
  if (!session || session.preview || session.status !== 'IN_PROGRESS'
      || state.sessionBusy || state.readingSyncRequired
      || !['INITIAL', 'UNDERSTOOD', 'NOT_UNDERSTOOD', 'SKIPPED'].includes(feedback)) {
    return false
  }
  return writeReading({
    sessionId: session.readingSessionId,
    kind: 'guidance',
    payload: {
      pageNumber: session.currentPage,
      feedback,
      expectedRevision: session.revision,
      clientEventId: createClientEventId('guidance'),
    },
  })
}

async function submitReadingText(inputText, kind) {
  const session = state.currentSession
  const text = typeof inputText === 'string' ? inputText.trim() : ''
  if (!session || session.preview || session.status !== 'IN_PROGRESS'
      || state.sessionBusy || state.readingSyncRequired
      || !text || text.length > 200) {
    return false
  }
  return writeReading({
    sessionId: session.readingSessionId,
    kind,
    payload: {
      ...(kind === 'answer' ? { eventType: 'ANSWER' } : {}),
      pageNumber: session.currentPage,
      inputText: text,
      expectedRevision: session.revision,
      clientEventId: createClientEventId(kind),
    },
  })
}

async function chooseBranch(branchId) {
  const session = state.currentSession
  if (!session || session.preview || session.status !== 'IN_PROGRESS'
      || state.sessionBusy || state.readingSyncRequired
      || !Number.isInteger(branchId) || branchId < 1) {
    return false
  }
  return writeReading({
    sessionId: session.readingSessionId,
    kind: 'branch',
    pageNumber: session.currentPage,
    payload: {
      branchId,
      expectedRevision: session.revision,
      clientEventId: createClientEventId('branch'),
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
      summary: `本次共读浏览了 ${countReadPages(session)} 页，可在家长陪伴下继续交流故事内容。`,
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

async function loadSessions({ append = false } = {}) {
  const page = append ? state.nextSessionsPage : 1
  if (!page || (append && state.sessionsLoading)) return false
  const epoch = authEpoch
  const request = ++sessionsRequest
  const isCurrent = () => epoch === authEpoch && request === sessionsRequest
  state.sessionsError = ''
  if (!state.auth.authenticated) {
    state.sessions = []
    state.nextSessionsPage = null
    state.sessionsLoaded = false
    state.sessionsLoading = false
    return false
  }
  state.sessionsLoading = true
  try {
    const pageSize = 50
    const items = await listReadingSessions(page, pageSize)
    if (!isCurrent()) return false
    const merged = append ? [...state.sessions, ...items] : items
    state.sessions = [...new Map(merged.map(session => [session.readingSessionId, session])).values()]
    state.nextSessionsPage = items.length === pageSize ? page + 1 : null
    state.sessionsLoaded = true
    return true
  } catch (error) {
    if (isCurrent()) state.sessionsError = describeApiError(error)
    return false
  } finally {
    if (isCurrent()) state.sessionsLoading = false
  }
}

async function handleRoute() {
  updateBodyView()
  if (['child', 'handoff', 'verifying-handoff'].includes(state.auth.status)) {
    const rememberedSessionId = state.childMode?.sessionId || savedChildSessionId()
    const sessionId = rememberedSessionId || state.route.sessionId || null
    if (
      state.route.name !== 'child'
      || (rememberedSessionId && state.route.sessionId !== rememberedSessionId)
    ) {
      replaceRoute(sessionId ? `child/${sessionId}` : 'child')
    }
  } else if (state.auth.status === 'authenticated' && state.route.name === 'child') {
    replaceRoute('account/overview')
  } else if (state.auth.status === 'anonymous' && state.route.name === 'child') {
    replaceRoute('auth')
  }

  const route = state.route
  if (route.name === 'workshop') {
    if (!state.auth.authenticated) return
    if (state.auth.role !== 'GUARDIAN') {
      replaceRoute('admin/storybooks')
      return
    }
    if (!route.sourceSessionId && (state.workshop.continuation || state.workshop.continuationLoading || state.workshop.continuationError)) {
      workshopRequest += 1
      state.workshop = emptyWorkshopState()
    }
    if (route.sourceSessionId
        && state.workshop.continuation?.session.readingSessionId !== route.sourceSessionId) {
      await loadWorkshopContinuation(route.sourceSessionId)
    }
    return
  }
  if (route.name === 'admin') {
    if (!state.auth.authenticated) {
      replaceRoute('auth')
      return
    }
    if (state.auth.role !== 'ADMIN') {
      replaceRoute('home')
      return
    }
    if (!(route.section === 'knowledge' ? state.admin.knowledgeLoaded : state.admin.storiesLoaded)) {
      await loadAdminContent(route.section)
    }
    return
  }
  if (route.name === 'library') {
    if (state.auth.authenticated && !state.booksLoading && state.booksSource !== 'api' && state.nextBooksPage === null) {
      await loadBooks()
    }
  } else if (route.name === 'detail' && route.versionId) {
    await loadDetail(route.versionId, route.source)
  } else if (route.name === 'reader') {
    if (route.previewVersionId || state.auth.authenticated) await loadReaderRoute()
  } else if (route.name === 'child') {
    if (state.auth.status === 'child' && !state.childMode) await restoreChildMode(route.sessionId)
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
    pages: visited.reduce((sum, session) => sum + countReadPages(session), 0),
  }
})

export const zhiya = readonly(state)
export const zhiyaActions = {
  bootstrap,
  refreshIdentity,
  syncStoredIdentity,
  navigate,
  syncRoute,
  signIn,
  signUp,
  signOut,
  unlockChildMode,
  reloadAccountData,
  reloadProfiles,
  selectProfile,
  addProfile,
  editProfile,
  removeProfile,
  checkDeletion,
  loadProfiles,
  loadBooks,
  loadMoreBooks,
  loadRecommendations,
  loadAdminContent,
  adminCreateStory,
  adminSetStoryStatus,
  adminCreateKnowledge,
  adminUpdateKnowledge,
  generateWorkshopDraft,
  retryWorkshopGeneration,
  resetWorkshopDraft,
  loadWorkshopContinuation,
  approveWorkshopDraft,
  openApprovedPrivateShelf,
  switchWorkshopContinuation,
  selectBook,
  openDetail,
  loadDetail,
  startReading,
  retryReadingCreation,
  retryReadingIdentity,
  startCurrentReading,
  enterChildMode,
  restoreChildMode,
  loadReaderRoute,
  recordPageChange,
  requestGuidance,
  submitAnswer: text => submitReadingText(text, 'answer'),
  askQuestion: text => submitReadingText(text, 'ask'),
  chooseBranch,
  completeReading,
  removeReadingSession,
  refreshReading,
  loadSessions,
  setToast,
  dismissToast,
}
export { activeProfile, totals, parseRoute }
