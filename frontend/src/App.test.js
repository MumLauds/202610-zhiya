import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { ApiError } from './api/client'
import { login, parentUnlock } from './api/auth'
import { deleteProfile, listProfiles } from './api/profiles'
import { getStoryVersion, listStorybooks } from './api/storybooks'
import { addReadingEvent, createReadingSession, deleteReadingSession, getDeletionStatus, getReadingSession, listReadingSessions, updateReadingMode } from './api/reading'

vi.mock('./api/auth', () => ({ checkHealth: vi.fn(), login: vi.fn(), parentUnlock: vi.fn() }))
vi.mock('./api/profiles', () => ({ listProfiles: vi.fn(async () => []), deleteProfile: vi.fn() }))
vi.mock('./api/storybooks', () => ({ getStoryVersion: vi.fn(), listStorybooks: vi.fn() }))
vi.mock('./api/reading', () => ({ getReadingSession: vi.fn(), addReadingEvent: vi.fn(), createReadingSession: vi.fn(), deleteReadingSession: vi.fn(), getDeletionStatus: vi.fn(), listReadingSessions: vi.fn(), updateReadingMode: vi.fn() }))

const book = { storyVersionId: 88, title: '书架上的绘本', status: 'PUBLISHED', pages: [{ pageNumber: 1, text: '只在授权时可读的正文' }] }
const firstPage = Array.from({ length: 30 }, (_, i) => ({ ...book, storyVersionId: i + 1, title: '绘本' + (i + 1) }))
const missing = () => new ApiError('资源不可用', { status: 404, code: 'RESOURCE_NOT_FOUND' })
const uncertainDelete = () => new ApiError('连接中断', { code: 'REQUEST_OUTCOME_UNKNOWN' })
const historyRow = (readingSessionId, visitedPageNumbers) => ({ readingSessionId, storyVersionId: 88, status: 'FINISHED', visitedPageNumbers, revision: 2 })
let wrapper

async function open(path) {
  window.history.replaceState({}, '', '/#' + path)
  const { default: App } = await import('./App.vue')
  const flipBook = { template: '<div />', methods: { showPage() {} } }
  wrapper = mount(App, { global: { stubs: { BookCarousel: true, FlipBook: flipBook } } })
  await flushPromises()
}

beforeEach(() => {
  vi.resetModules()
  vi.resetAllMocks()
  localStorage.clear()
  sessionStorage.clear()
  listStorybooks.mockResolvedValue([book])
  listReadingSessions.mockResolvedValue([])
  getDeletionStatus.mockResolvedValue(false)
})
afterEach(() => wrapper?.unmount())

it('displays the 31st book when a guardian loads the next library page', async () => {
  listStorybooks.mockResolvedValueOnce(firstPage).mockResolvedValueOnce([{ ...book, storyVersionId: 31, title: '第31本绘本' }])
  await open('library')
  expect(wrapper.get('.library-feature-copy h2').text()).toBe('绘本1')
  await wrapper.findAll('button').find((button) => button.text() === '加载更多绘本').trigger('click')
  await flushPromises()
  expect(wrapper.get('.library-feature-copy h2').text()).toBe('第31本绘本')
  expect(wrapper.text()).not.toContain('加载更多绘本')
  expect(listStorybooks.mock.calls.map(([query]) => query.page)).toEqual([1, 2])
})

it('retains the library after a failed append and retries the same page', async () => {
  listStorybooks.mockResolvedValueOnce(firstPage).mockRejectedValueOnce(new Error('请求超时')).mockResolvedValueOnce([])
  await open('library')
  const more = () => wrapper.findAll('button').find((button) => button.text() === '加载更多绘本')
  await more().trigger('click')
  await flushPromises()
  expect(wrapper.get('[role="alert"]').text()).toContain('请求超时')
  expect(wrapper.get('.library-feature-copy h2').text()).toBe('绘本1')
  await more().trigger('click')
  await flushPromises()
  expect(listStorybooks.mock.calls.map(([query]) => query.page)).toEqual([1, 2, 2])
  expect(wrapper.get('.library-feature-copy h2').text()).toBe('绘本1')
  expect(more()).toBeUndefined()
})

it('starts a new filtered library at page one and discards a late unfiltered page', async () => {
  let finishOldRequest
  listStorybooks.mockResolvedValueOnce(firstPage)
    .mockReturnValueOnce(new Promise((resolve) => { finishOldRequest = resolve }))
    .mockResolvedValueOnce([book])
  await open('library')
  const button = (label) => wrapper.findAll('button').find((item) => item.text() === label)
  await button('加载更多绘本').trigger('click')
  await button('年龄').trigger('click')
  await button('4–5 岁').trigger('click')
  await flushPromises()
  finishOldRequest([{ ...book, storyVersionId: 31, title: '旧筛选下的绘本' }])
  await flushPromises()
  expect(listStorybooks.mock.calls.map(([{ page, ageBand }]) => [page, ageBand])).toEqual([
    [1, undefined], [2, undefined], [1, 'AGE_4_5'],
  ])
  expect(wrapper.get('.library-feature-copy h2').text()).toBe(book.title)
  expect(button('加载更多绘本')).toBeUndefined()
})

it.each(['start', 'profile lookup'])('keeps the new filtered shelf when the old %s response arrives late', async (pending) => {
  const profiles = [{ childProfileId: 7 }]
  listProfiles.mockResolvedValue(profiles)
  const newShelf = Array.from({ length: 60 }, (_, i) => ({ ...book, storyVersionId: i + 1, title: '新书' + (i + 1) }))
  listStorybooks.mockImplementation(async ({ ageBand, page, pageSize }) => (
    ageBand === 'AGE_5_6' ? newShelf.slice((page - 1) * pageSize, page * pageSize) : [book]
  ))
  let rejectStart
  createReadingSession.mockReturnValueOnce(new Promise((_, reject) => { rejectStart = reject }))
  await open('library')
  const button = (label) => wrapper.findAll('button').find((item) => item.text() === label)
  await button('年龄').trigger('click')
  await button('3–4 岁').trigger('click')
  await flushPromises()
  await button('开始阅读').trigger('click')
  expect(createReadingSession).toHaveBeenCalledWith({ childProfileId: 7, storyVersionId: book.storyVersionId })
  let finishLookup
  if (pending === 'profile lookup') {
    listProfiles.mockReturnValueOnce(new Promise((resolve) => { finishLookup = resolve }))
    rejectStart(missing())
    await flushPromises()
    expect(listProfiles).toHaveBeenCalledTimes(2)
  }

  await button('5–6 岁').trigger('click')
  await flushPromises()
  await button('加载更多绘本').trigger('click')
  await flushPromises()
  const carousel = wrapper.getComponent({ name: 'BookCarousel' })
  carousel.vm.$emit('select', carousel.props('books')[31].id)
  await flushPromises()
  expect(wrapper.get('.library-feature-copy h2').text()).toBe('新书32')

  if (pending === 'start') rejectStart(missing())
  else finishLookup(profiles)
  await flushPromises()
  expect(carousel.props('books').map((item) => item.storyVersionId)).toEqual(newShelf.map((item) => item.storyVersionId))
  expect(wrapper.get('.library-feature-copy h2').text()).toBe('新书32')
  expect(listStorybooks.mock.calls.map(([{ ageBand, page }]) => [ageBand, page])).toEqual([
    [undefined, 1], ['AGE_3_4', 1], ['AGE_5_6', 1], ['AGE_5_6', 2],
  ])
})

it('hides withdrawn details and removes the stale version when returning to the library', async () => {
  listStorybooks.mockResolvedValueOnce([book]).mockResolvedValue([])
  getStoryVersion.mockRejectedValueOnce(missing())
  await open('detail/88')
  expect(getStoryVersion).toHaveBeenCalledWith(88)
  expect(wrapper.text()).toContain('绘本暂不可用')
  expect(wrapper.text()).not.toContain(book.pages[0].text)
  expect(wrapper.text()).not.toContain('开始阅读')
  await wrapper.get('.detail-toolbar button').trigger('click')
  await vi.waitFor(() => expect(wrapper.find('.library-page').exists()).toBe(true))
  expect(wrapper.text()).not.toContain(book.title)
})

it.each([
  ['detail', false], ['detail', true], ['start', true],
])('recovers a withdrawn book’s shifted neighbour via %s (end already seen: %s)', async (entry, endSeen) => {
  listProfiles.mockResolvedValue([{ childProfileId: 7 }])
  const catalog = [...firstPage, { ...book, storyVersionId: 31, title: '绘本31' }]
  listStorybooks.mockImplementation(async ({ page, pageSize }) => catalog.slice((page - 1) * pageSize, page * pageSize))
  await open('library')
  const button = (label) => wrapper.findAll('button').find((item) => item.text() === label)
  await button('年龄').trigger('click')
  await button('4–5 岁').trigger('click')
  await flushPromises()

  catalog.shift()
  if (endSeen) {
    await button('加载更多绘本').trigger('click')
    await flushPromises()
    expect(button('加载更多绘本')).toBeUndefined()
  }
  if (entry === 'detail') {
    getStoryVersion.mockRejectedValueOnce(missing())
    await button('查看详情').trigger('click')
    await vi.waitFor(() => expect(wrapper.text()).toContain('绘本暂不可用'))
    await wrapper.get('.detail-toolbar button').trigger('click')
    await vi.waitFor(() => expect(wrapper.find('.library-page').exists()).toBe(true))
  } else {
    createReadingSession.mockRejectedValueOnce(missing())
    await button('开始阅读').trigger('click')
    await flushPromises()
    expect(createReadingSession).toHaveBeenCalledTimes(1)
    expect(createReadingSession).toHaveBeenCalledWith({ storyVersionId: 1, childProfileId: 7 })
    expect(button('加载更多绘本')).toBeDefined()
  }
  await button('加载更多绘本').trigger('click')
  await flushPromises()

  expect(wrapper.getComponent({ name: 'BookCarousel' }).props('books').map((item) => item.storyVersionId))
    .toEqual(catalog.map((item) => item.storyVersionId))
  expect(button('加载更多绘本')).toBeUndefined()
  expect(listStorybooks.mock.calls.map(([{ page, ageBand }]) => [page, ageBand])).toEqual([
    [1, undefined], ...(endSeen ? [1, 2, 1, 2] : [1, 1, 2]).map((page) => [page, 'AGE_4_5']),
  ])
})

it('keeps the second shelf page until the guardian selects a replacement for a deleted profile', async () => {
  const remaining = [{ childProfileId: 8, nickname: '小雨' }, { childProfileId: 9, nickname: '小禾' }]
  listProfiles.mockResolvedValueOnce([{ childProfileId: 7 }, ...remaining]).mockResolvedValue(remaining)
  const catalog = Array.from({ length: 60 }, (_, i) => ({ ...book, storyVersionId: i + 1, title: '绘本' + (i + 1) }))
  listStorybooks.mockImplementation(async ({ page, pageSize }) => catalog.slice((page - 1) * pageSize, page * pageSize))
  createReadingSession.mockRejectedValueOnce(missing()).mockResolvedValueOnce({ readingSessionId: 55 })
  const session = { readingSessionId: 55, storyVersionId: 32, currentPage: 1, revision: 1, status: 'IN_PROGRESS', visitedPageNumbers: [1] }
  getReadingSession.mockResolvedValue({ session, events: [], report: null })
  getStoryVersion.mockResolvedValue(catalog[31])
  await open('library')
  const button = (label) => wrapper.findAll('button').find((item) => item.text() === label)
  await button('加载更多绘本').trigger('click')
  await flushPromises()
  const carousel = wrapper.getComponent({ name: 'BookCarousel' })
  carousel.vm.$emit('select', carousel.props('books')[31].id)
  await flushPromises()
  await button('开始阅读').trigger('click')
  await flushPromises()
  expect(carousel.props('books')).toHaveLength(60)
  expect(wrapper.get('.library-feature-copy h2').text()).toBe('绘本32')
  expect(wrapper.get('[role="status"]').text()).toContain('重新选择')

  await button('开始阅读').trigger('click')
  await vi.waitFor(() => expect(wrapper.find('.child-card').exists()).toBe(true))
  expect(wrapper.get('.child-switch').text()).toContain('选择儿童档案')
  expect(wrapper.find('.child-card.active').exists()).toBe(false)
  expect(createReadingSession).toHaveBeenCalledTimes(1)
  const chosen = wrapper.findAll('.child-card').find((card) => card.get('h2').text() === '小禾')
  await chosen.get('button').trigger('click')
  await button('绘本馆').trigger('click')
  await vi.waitFor(() => expect(wrapper.find('.library-feature-copy h2').text()).toBe('绘本32'))
  await button('开始阅读').trigger('click')
  await vi.waitFor(() => expect(location.hash).toBe('#reader/55'))
  expect(createReadingSession.mock.calls.map(([input]) => input)).toEqual([
    { childProfileId: 7, storyVersionId: 32 }, { childProfileId: 9, storyVersionId: 32 },
  ])
  expect(listStorybooks.mock.calls.map(([query]) => query.page)).toEqual([1, 2])
})

it('preserves the shelf when the profile lookup fails after a reading 404', async () => {
  listProfiles.mockResolvedValueOnce([{ childProfileId: 7 }]).mockRejectedValueOnce(new ApiError('暂不可用', { status: 503 }))
  createReadingSession.mockRejectedValueOnce(missing())
  await open('library')
  await wrapper.findAll('button').find((item) => item.text() === '开始阅读').trigger('click')
  await flushPromises()
  expect(wrapper.get('[role="status"]').text()).toContain('服务暂不可用')
  expect(wrapper.get('.library-feature-copy h2').text()).toBe(book.title)
  expect(listStorybooks).toHaveBeenCalledTimes(1)
  expect(createReadingSession).toHaveBeenCalledTimes(1)
})

it('retries the shelf refresh when the profile is valid and the catalog is unavailable', async () => {
  listProfiles.mockResolvedValue([{ childProfileId: 7 }])
  listStorybooks.mockResolvedValueOnce([book]).mockRejectedValueOnce(new ApiError('书架暂不可用', { status: 503 }))
    .mockResolvedValueOnce([book])
  createReadingSession.mockRejectedValueOnce(missing())
  await open('library')
  const button = (label) => wrapper.findAll('button').find((item) => item.text() === label)
  await button('开始阅读').trigger('click')
  await flushPromises()
  expect(wrapper.get('.library-feature-copy h2').text()).toBe(book.title)
  expect(wrapper.get('[role="alert"]').text()).toContain('书架暂不可用')
  await button('重新加载书架').trigger('click')
  await flushPromises()
  expect(listStorybooks.mock.calls.map(([query]) => query.page)).toEqual([1, 1, 1])
  expect(wrapper.get('.library-feature-copy h2').text()).toBe(book.title)
  expect(createReadingSession).toHaveBeenCalledTimes(1)
})

it('retains the filtered shelf after a failed withdrawal refresh and retries page one', async () => {
  let failRefresh
  const remaining = [...firstPage.slice(1), { ...book, storyVersionId: 31, title: '绘本31' }]
  listStorybooks.mockResolvedValueOnce(firstPage).mockResolvedValueOnce(firstPage)
    .mockReturnValueOnce(new Promise((_, reject) => { failRefresh = reject }))
    .mockResolvedValueOnce(remaining)
  await open('library')
  const button = (label) => wrapper.findAll('button').find((item) => item.text() === label)
  const books = () => wrapper.getComponent({ name: 'BookCarousel' }).props('books').map((item) => item.storyVersionId)
  await button('年龄').trigger('click')
  await button('4–5 岁').trigger('click')
  await flushPromises()
  getStoryVersion.mockRejectedValueOnce(missing())
  await button('查看详情').trigger('click')
  await vi.waitFor(() => expect(wrapper.text()).toContain('绘本暂不可用'))
  await wrapper.get('.detail-toolbar button').trigger('click')
  await vi.waitFor(() => expect(wrapper.find('.library-page').exists()).toBe(true))

  failRefresh(new ApiError('书架暂不可用', { status: 503 }))
  await flushPromises()
  expect(books()).toEqual(firstPage.slice(1).map((item) => item.storyVersionId))
  expect(wrapper.get('[role="alert"]').text()).toContain('书架暂不可用')
  await button('重新加载书架').trigger('click')
  await flushPromises()
  expect(books()).toEqual(remaining.map((item) => item.storyVersionId))
  expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  expect(listStorybooks.mock.calls.map(([{ page, ageBand }]) => [page, ageBand])).toEqual([
    [1, undefined], [1, 'AGE_4_5'], [1, 'AGE_4_5'], [1, 'AGE_4_5'],
  ])
})

it('allows an explicitly labelled local preview to open without fetching API details', async () => {
  listStorybooks.mockResolvedValue([])
  await open('detail/1001')
  expect(wrapper.get('.detail-card').text()).toContain('设计预览')
  expect(getStoryVersion).not.toHaveBeenCalled()
})

it('returns from an unavailable session to the library', async () => {
  getReadingSession.mockRejectedValueOnce(missing())
  await open('reader/999')
  const empty = wrapper.get('.reader-page .empty-state')
  expect(empty.text()).toContain('阅读会话暂时不可用')
  await empty.get('button').trigger('click')
  await vi.waitFor(() => expect(wrapper.find('.library-page').exists()).toBe(true))
  expect(location.hash).toBe('#library')
})

it('lets the user explicitly start again if a terminal 504 left the session in CREATED', async () => {
  const created = { readingSessionId: 55, storyVersionId: 88, currentPage: 1, revision: 0, status: 'CREATED', visitedPageNumbers: [] }
  getReadingSession.mockResolvedValue({ session: created, events: [], report: null })
  addReadingEvent.mockRejectedValueOnce(new ApiError('unknown', { status: 504, code: 'REQUEST_OUTCOME_UNKNOWN' }))
    .mockResolvedValueOnce({ revision: 1 })
  await open('reader/55')
  expect(addReadingEvent).not.toHaveBeenCalled()
  await wrapper.get('.reader-heading button.primary').trigger('click')
  await flushPromises()
  const writes = addReadingEvent.mock.calls.map(([, event]) => event)
  expect(writes).toHaveLength(1)
  expect(writes[0]).toMatchObject({ eventType: 'STARTED', expectedRevision: 0, pageNumber: 1 })
  expect(wrapper.text()).toContain('开始本次阅读')
  await wrapper.get('.reader-heading button.primary').trigger('click')
  await flushPromises()
  expect(addReadingEvent).toHaveBeenCalledTimes(2)
  expect(addReadingEvent.mock.calls[1][1].clientEventId).not.toBe(addReadingEvent.mock.calls[0][1].clientEventId)
  expect(wrapper.text()).not.toContain('开始本次阅读')
})

it('routes a child credential to the restricted handoff even from the home route', async () => {
  listProfiles.mockRejectedValue(new ApiError('需要儿童授权', { status: 403, code: 'FORBIDDEN' }))
  await open('home')
  expect(listProfiles).toHaveBeenCalled()
  await vi.waitFor(() => expect(location.hash).toBe('#child'))
  expect(wrapper.find('.child-handoff-page').exists()).toBe(true)
  expect(wrapper.find('.site-header').exists()).toBe(false)
  expect(listStorybooks).not.toHaveBeenCalled()
})

it('routes a guardian credential away from a stale child handoff link', async () => {
  await open('child/99')
  expect(location.hash).toBe('#account/overview')
  expect(wrapper.find('.child-handoff-page').exists()).toBe(false)
  expect(wrapper.find('.site-header').exists()).toBe(true)
})

it('restores the remembered child handoff when an old child link is reopened', async () => {
  sessionStorage.setItem('zhiya.childHandoffSessionId', '55')
  listProfiles.mockRejectedValue(new ApiError('需要儿童授权', { status: 403, code: 'FORBIDDEN' }))
  getReadingSession.mockResolvedValueOnce({
    session: {
      readingSessionId: 55,
      storyVersionId: 88,
      currentPage: 1,
      visitedPageNumbers: [1],
      status: 'CREATED',
      revision: 0,
      expiresAt: '2026-11-18T08:00:00Z',
    },
    events: [],
    report: null,
  })
  getStoryVersion.mockResolvedValueOnce(book)
  await open('child/999')
  await vi.waitFor(() => expect(location.hash).toBe('#child/55'))
  expect(getReadingSession).toHaveBeenCalledWith(55)
  expect(getReadingSession).not.toHaveBeenCalledWith(999)
  expect(wrapper.get('.child-handoff-page').text()).toContain(book.title)
})

it.each([
  ['child/55', 'sessionStorage', '55'],
  ['child/55', null, '55'],
  ['home', 'localStorage', '55'],
])('allows a guardian to unlock after the reading time expires on #%s (%s)', async (path, storage, id) => {
  if (storage) window[storage].setItem('zhiya.childHandoffSessionId', id)
  listProfiles.mockRejectedValueOnce(new ApiError('请先登录', { status: 401, code: 'AUTH_REQUIRED' }))
  parentUnlock.mockResolvedValueOnce({ accountId: 7 })
  await open(path)
  expect(location.hash).toBe('#child/' + id)
  expect(wrapper.find('.site-header').exists()).toBe(false)
  expect(getReadingSession).not.toHaveBeenCalled()
  expect(listStorybooks).not.toHaveBeenCalled()
  const form = wrapper.get('.child-handoff-unlock')
  await form.get('input[autocomplete="username"]').setValue('parent-test')
  await form.get('input[type="password"]').setValue('password123')
  await form.trigger('submit')
  await flushPromises()
  expect(parentUnlock).toHaveBeenCalledOnce()
  expect(location.hash).toBe('#account/overview')
  expect(wrapper.find('.child-handoff-page').exists()).toBe(false)
  expect(localStorage.getItem('zhiya.childHandoffSessionId')).toBeNull()
  expect(sessionStorage.getItem('zhiya.childHandoffSessionId')).toBeNull()
})

it('restores the guardian view on focus after another tab unlocks it', async () => {
  listProfiles.mockRejectedValueOnce(new ApiError('仅限家长操作', { status: 403, code: 'FORBIDDEN' }))
  await open('child')
  expect(wrapper.find('.site-header').exists()).toBe(false)
  window.dispatchEvent(new Event('focus'))
  await flushPromises()
  expect(location.hash).toBe('#account/overview')
  expect(wrapper.find('.site-header').exists()).toBe(true)
  expect(parentUnlock).not.toHaveBeenCalled()
})

it('keeps a fresh anonymous visit on login and offers unlock if the server identifies a child device', async () => {
  listProfiles.mockRejectedValueOnce(new ApiError('请先登录', { status: 401, code: 'AUTH_REQUIRED' }))
  login.mockRejectedValueOnce(new ApiError('请先通过家长解锁退出儿童模式', { status: 403, code: 'FORBIDDEN' }))
  await open('auth')
  const form = wrapper.get('.auth-card')
  await form.get('input[autocomplete="username"]').setValue('parent-test')
  await form.get('input[type="password"]').setValue('password123')
  await form.trigger('submit')
  await flushPromises()
  expect(wrapper.find('.child-handoff-unlock').exists()).toBe(true)
  expect(parentUnlock).not.toHaveBeenCalled()
})

it('keeps the login form and its error visible when credentials are rejected', async () => {
  listProfiles.mockRejectedValueOnce(new ApiError('请先登录', { status: 401, code: 'AUTH_REQUIRED' }))
  login.mockRejectedValueOnce(new ApiError('账号或密码错误', { status: 401, code: 'INVALID_CREDENTIALS' }))
  await open('auth')
  const form = wrapper.get('.auth-card')
  await form.get('input[autocomplete="username"]').setValue('parent-test')
  await form.get('input[type="password"]').setValue('wrong-password')
  await form.trigger('submit')
  await flushPromises()
  expect(wrapper.get('.auth-card .form-error').text()).toBe('账号或密码错误')
  expect(wrapper.get('.auth-card input[autocomplete="username"]').element.value).toBe('parent-test')
})

it('restricts an open guardian tab when another tab enters child mode', async () => {
  await open('account/children')
  localStorage.setItem('zhiya.childHandoffSessionId', '55')
  listProfiles.mockRejectedValueOnce(new ApiError('仅限家长操作', { status: 403, code: 'FORBIDDEN' }))
  getReadingSession.mockResolvedValueOnce({ session: { readingSessionId: 55, storyVersionId: 88 }, events: [] })
  getStoryVersion.mockResolvedValueOnce(book)
  window.dispatchEvent(new Event('focus'))
  await flushPromises()
  expect(location.hash).toBe('#child/55')
  expect(wrapper.find('.site-header').exists()).toBe(false)
  expect(wrapper.find('.child-handoff-unlock').exists()).toBe(true)
})

it('closes a completed deletion before the reads finish and retries only failed history', async () => {
  let finishProfiles, failHistory
  listProfiles.mockResolvedValueOnce([{ childProfileId: 7, nickname: '小芽' }])
  listProfiles.mockReturnValueOnce(new Promise(resolve => { finishProfiles = resolve }))
  listReadingSessions.mockReturnValueOnce(new Promise((_, reject) => { failHistory = reject }))
  await open('account/children')
  await wrapper.get('.child-card button.danger').trigger('click')
  await wrapper.get('[role="dialog"] button.danger').trigger('click')
  await flushPromises()
  expect(deleteProfile).toHaveBeenCalledWith(7)
  expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
  expect(wrapper.find('.child-card').exists()).toBe(false)
  finishProfiles([])
  failHistory(new ApiError('服务暂不可用', { status: 503 }))
  await flushPromises()
  const alert = wrapper.get('.account-data-error')
  expect(alert.text()).toContain('阅读记录暂未更新')
  await alert.get('button').trigger('click')
  await flushPromises()
  expect(wrapper.find('.account-data-error').exists()).toBe(false)
  expect(listReadingSessions).toHaveBeenCalledTimes(2)
  expect(listProfiles).toHaveBeenCalledTimes(2)
  expect(deleteProfile).toHaveBeenCalledOnce()
})

it('replaces an open guardian dialog and its history when a different parent signs in in another tab', async () => {
  localStorage.setItem('zhiya.account', 'parent-a')
  listProfiles.mockResolvedValueOnce([{ childProfileId: 7, nickname: '小芽' }])
  listReadingSessions.mockResolvedValueOnce([historyRow(11, [1])])
  await open('account/growth')
  await wrapper.get('.recent-item button.danger').trigger('click')
  expect(wrapper.get('[role="dialog"]').text()).toContain('阅读记录 #11')

  let finishProfiles
  listProfiles.mockReturnValueOnce(new Promise(resolve => { finishProfiles = resolve }))
  localStorage.setItem('zhiya.account', 'parent-b')
  window.dispatchEvent(new StorageEvent('storage', { key: 'zhiya.account', storageArea: localStorage }))
  await flushPromises()
  expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
  expect(wrapper.find('.recent-item').exists()).toBe(false)
  expect(wrapper.text()).not.toContain('小芽')
  listStorybooks.mockResolvedValueOnce([{ ...book, storyVersionId: 99, title: '小禾的绘本' }])
  listReadingSessions.mockResolvedValueOnce([{ ...historyRow(22, [1, 2]), storyVersionId: 99 }])
  finishProfiles([{ childProfileId: 8, nickname: '小禾' }])
  await flushPromises()
  expect(wrapper.get('.account-person').text()).toContain('parent-b')
  expect(wrapper.get('.recent-item').text()).toContain('小禾的绘本')
  expect(wrapper.get('.recent-item').text()).toContain('阅读记录 #22')
  expect(wrapper.text()).not.toContain('阅读记录 #11')
  expect(deleteReadingSession).not.toHaveBeenCalled()
})

const deletionCases = [
  { kind: 'profile', section: 'children', row: '.child-card', items: [{ childProfileId: 7 }, { childProfileId: 8 }], read: listProfiles, remove: deleteProfile },
  { kind: 'session', section: 'growth', row: '.recent-item', items: [historyRow(11, [1]), historyRow(12, [1, 2])], read: listReadingSessions, remove: deleteReadingSession },
]

it.each(deletionCases)('reconciles a lost reply or a 404 for a $kind deletion', async ({ section, row, items, read, remove }) => {
  read.mockResolvedValueOnce(items).mockResolvedValueOnce(items.slice(1)).mockResolvedValueOnce([])
  remove.mockRejectedValueOnce(uncertainDelete()).mockRejectedValueOnce(missing())
  await open('account/' + section)
  for (let i = 0; i < items.length; i++) {
    await wrapper.get(row + ' button.danger').trigger('click')
    await wrapper.get('[role="dialog"] button.danger').trigger('click')
    await flushPromises()
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
    expect(wrapper.findAll(row)).toHaveLength(items.length - i - 1)
    expect(remove).toHaveBeenCalledTimes(i + 1)
    expect(read).toHaveBeenCalledTimes(i + 2)
  }
})

it.each(deletionCases)('only queries after an uncertain $kind deletion until its presence is confirmed', async ({ section, row, items, read, remove }) => {
  read.mockResolvedValue(items)
  await open('account/' + section)
  remove.mockRejectedValueOnce(uncertainDelete())
  read.mockRejectedValueOnce(new ApiError('暂不可用', { status: 503 }))
  await wrapper.get(row + ' button.danger').trigger('click')
  await wrapper.get('[role="dialog"] button.danger').trigger('click')
  await flushPromises()
  expect(wrapper.get('[role="dialog"] button.primary').text()).toBe('重新核对删除结果')
  await wrapper.get('[role="dialog"] .modal-close').trigger('click')
  await wrapper.get(row + ' button.danger').trigger('click')
  expect(wrapper.find('[role="dialog"] button.danger').exists()).toBe(false)
  await wrapper.get('[role="dialog"] button.primary').trigger('click')
  await flushPromises()
  expect(wrapper.get('[role="dialog"]').text()).toContain('目标仍在列表中')
  expect(remove).toHaveBeenCalledOnce()
  read.mockResolvedValue(items.slice(1))
  await wrapper.get('[role="dialog"] button.danger').trigger('click')
  await flushPromises()
  expect(remove).toHaveBeenCalledTimes(2)
  expect(wrapper.findAll(row)).toHaveLength(1)
})

it.each(['overview', 'growth'])('shows unknown totals on %s after a profile deletion until history is available', async (section) => {
  const survivors = [historyRow(12, [1, 2])]
  listProfiles.mockResolvedValueOnce([{ childProfileId: 7 }, { childProfileId: 8 }]).mockResolvedValue([{ childProfileId: 8 }])
  listReadingSessions.mockResolvedValueOnce([historyRow(11, [1]), ...survivors])
  await open('account/' + section)
  const values = () => wrapper.findAll('.stat-card strong').slice(0, 3).map(el => el.text())
  const route = async (part) => {
    window.history.replaceState({}, '', '/#account/' + part)
    window.dispatchEvent(new HashChangeEvent('hashchange'))
    await flushPromises()
  }
  expect(values()).toEqual(['2', '1', '3'])
  await route('children')
  listReadingSessions.mockRejectedValue(new ApiError('暂不可用', { status: 503 }))
  await wrapper.get('.child-card button.danger').trigger('click')
  await wrapper.get('[role="dialog"] button.danger').trigger('click')
  await flushPromises()
  await route(section)
  expect(values()).toEqual(['待更新', '待更新', '待更新'])
  let finishHistory
  listReadingSessions.mockReturnValueOnce(new Promise(resolve => { finishHistory = resolve }))
  await wrapper.get('.account-data-error button').trigger('click')
  expect(values()).toEqual(['读取中', '读取中', '读取中'])
  finishHistory(survivors)
  await flushPromises()
  expect(values()).toEqual(['1', '1', '2'])
  listReadingSessions.mockResolvedValueOnce([])
  await route(section)
  expect(values()).toEqual(['0', '0', '0'])
  expect(deleteProfile).toHaveBeenCalledOnce()
})

const childOnly = () => new ApiError('仅限家长', { status: 403, code: 'FORBIDDEN' })
const offline = () => new ApiError('暂不可用', { status: 503 })
const handoffSession = mode => ({
  session: { readingSessionId: 55, storyVersionId: 88, currentPage: 1, mode, status: 'CREATED', visitedPageNumbers: [], revision: 0 },
  events: [], report: null,
})

it.each(['child', 'guardian'])('reconciles a lost mode-change response and restores the confirmed %s view', async role => {
  getReadingSession.mockResolvedValue(handoffSession('PARENT_CHILD'))
  getStoryVersion.mockResolvedValue(book)
  await open('reader/55')
  await wrapper.get('.child-mode-card button').trigger('click')
  updateReadingMode.mockRejectedValueOnce(uncertainDelete())
  listProfiles.mockRejectedValueOnce(offline())
  await wrapper.get('[aria-labelledby="child-mode-title"] button.primary').trigger('click')
  await flushPromises()
  expect(location.hash).toBe('#child/55')
  expect(wrapper.find('.site-header').exists()).toBe(false)
  expect(wrapper.find('.child-handoff-unlock').exists()).toBe(false)
  expect(wrapper.get('.child-handoff-loading').text()).toContain('重新核对设备身份')
  if (role === 'child') listProfiles.mockRejectedValueOnce(childOnly())
  getReadingSession.mockResolvedValue(handoffSession(role === 'child' ? 'CHILD_SOLO' : 'PARENT_CHILD'))
  await wrapper.get('.child-handoff-loading button').trigger('click')
  await flushPromises()
  expect(location.hash).toBe(role === 'child' ? '#child/55' : '#reader/55')
  expect(wrapper.find('.site-header').exists()).toBe(role === 'guardian')
  expect(wrapper.find('.child-handoff-unlock').exists()).toBe(role === 'child')
  expect(getReadingSession).toHaveBeenLastCalledWith(55)
  expect(updateReadingMode).toHaveBeenCalledOnce()
  expect(parentUnlock).not.toHaveBeenCalled()
})

it.each(['child', 'guardian'])('reconciles a lost unlock response using only reads before restoring the %s', async role => {
  listProfiles.mockRejectedValueOnce(childOnly())
  getReadingSession.mockResolvedValue(handoffSession('CHILD_SOLO'))
  getStoryVersion.mockResolvedValue(book)
  await open('child/55')
  const form = wrapper.get('.child-handoff-unlock')
  await form.get('[autocomplete="username"]').setValue('parent-a')
  await form.get('[type="password"]').setValue('password123')
  parentUnlock.mockImplementationOnce(async () => {
    if (role === 'guardian') document.cookie = 'zhiya_csrf=fixture-after-unlock; path=/'
    throw uncertainDelete()
  })
  listProfiles.mockRejectedValueOnce(offline())
  await form.trigger('submit')
  await flushPromises()
  expect(wrapper.find('.child-handoff-unlock').exists()).toBe(false)
  expect(wrapper.get('.child-handoff-loading').text()).toContain('服务暂不可用')
  let finishShelf
  if (role === 'child') listProfiles.mockRejectedValueOnce(childOnly())
  else listStorybooks.mockReturnValueOnce(new Promise(resolve => { finishShelf = resolve }))
  await wrapper.get('.child-handoff-loading button').trigger('click')
  await flushPromises()
  if (role === 'guardian') {
    document.cookie = 'zhiya_csrf=; Max-Age=0; path=/'
    expect(wrapper.find('.child-handoff-unlock').exists()).toBe(false)
    expect(wrapper.get('.account-person').text()).toContain('parent-a')
    finishShelf([book])
    await flushPromises()
  }
  expect(location.hash).toBe(role === 'child' ? '#child/55' : '#account/overview')
  expect(wrapper.find('.child-handoff-unlock').exists()).toBe(role === 'child')
  expect(parentUnlock).toHaveBeenCalledOnce()
  expect(updateReadingMode).not.toHaveBeenCalled()
})

it('keeps an unlisted deletion actionable after a reload until the owner explicitly confirms it', async () => {
  localStorage.setItem('zhiya.account', 'parent-a')
  listReadingSessions.mockResolvedValueOnce([historyRow(11, [1])])
  deleteReadingSession.mockRejectedValueOnce(uncertainDelete())
  getDeletionStatus.mockResolvedValue(true)
  await open('account/growth')
  await wrapper.get('.recent-item button.danger').trigger('click')
  await wrapper.get('[role="dialog"] button.danger').trigger('click')
  await flushPromises()
  expect(wrapper.get('[role="dialog"]').text()).toContain('仍保存在当前账号')
  await wrapper.get('.modal-close').trigger('click')
  wrapper.unmount()
  vi.resetModules()
  await open('account/growth')
  expect(wrapper.get('.account-data-error').text()).toContain('阅读记录 #11')
  await wrapper.get('.account-data-error button').trigger('click')
  await wrapper.get('[role="dialog"] button.primary').trigger('click')
  await flushPromises()
  expect(getDeletionStatus.mock.calls).toEqual([[11], [11]])
  expect(deleteReadingSession).toHaveBeenCalledOnce()
  await wrapper.get('[role="dialog"] button.danger').trigger('click')
  await flushPromises()
  expect(deleteReadingSession.mock.calls).toEqual([[11], [11]])
  expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
  expect(wrapper.find('.account-data-error').exists()).toBe(false)
  expect(sessionStorage.getItem('zhiya.pendingSessionDeletes')).toBeNull()
})

it('finishes on an authoritative DELETE 404 even if the history refresh fails', async () => {
  listReadingSessions.mockResolvedValueOnce([historyRow(11, [1])]).mockRejectedValueOnce(offline())
  deleteReadingSession.mockRejectedValueOnce(missing())
  await open('account/growth')
  await wrapper.get('.recent-item button.danger').trigger('click')
  await wrapper.get('[role="dialog"] button.danger').trigger('click')
  await flushPromises()
  expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
  expect(wrapper.get('.account-data-error').text()).toContain('阅读记录暂未更新')
  expect(getDeletionStatus).not.toHaveBeenCalled()
  expect(deleteReadingSession).toHaveBeenCalledOnce()
})

it('discards deletion reminders saved for a different guardian', async () => {
  sessionStorage.setItem('zhiya.pendingSessionDeletes', JSON.stringify({ account: 'parent-a', ids: [11] }))
  localStorage.setItem('zhiya.account', 'parent-b')
  await open('account/growth')
  expect(wrapper.text()).not.toContain('阅读记录 #11')
  expect(sessionStorage.getItem('zhiya.pendingSessionDeletes')).toBeNull()
})

it.each([undefined, 'unavailable'])('retains an unlisted deletion if the status lookup is %s', async result => {
  listReadingSessions.mockResolvedValueOnce([historyRow(11, [1])])
  deleteReadingSession.mockRejectedValueOnce(uncertainDelete())
  if (result === 'unavailable') getDeletionStatus.mockRejectedValueOnce(offline())
  else getDeletionStatus.mockResolvedValueOnce(result)
  await open('account/growth')
  await wrapper.get('.recent-item button.danger').trigger('click')
  await wrapper.get('[role="dialog"] button.danger').trigger('click')
  await flushPromises()
  expect(wrapper.find('[role="dialog"] button.danger').exists()).toBe(false)
  await wrapper.get('[role="dialog"] button.primary').trigger('click')
  await flushPromises()
  expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
  expect(deleteReadingSession).toHaveBeenCalledOnce()
})

it.each([
  ['204', 7], ['204', 8], ['unknown', 7], ['unknown', 8],
])('closes only the deleted profile draft after %s when editing #%i', async (reply, editedId) => {
  const profiles = [{ childProfileId: 7, nickname: '小芽' }, { childProfileId: 8, nickname: '小禾' }]
  listProfiles.mockResolvedValueOnce(profiles).mockResolvedValue([profiles[1]])
  if (reply === 'unknown') deleteProfile.mockRejectedValueOnce(uncertainDelete())
  await open('account/children')
  const editingCard = wrapper.findAll('.child-card').find(card => card.text().includes('档案 #' + editedId))
  await editingCard.findAll('button').find(button => button.text() === '编辑').trigger('click')
  await wrapper.get('form.profile-form input').setValue('未保存的草稿')
  await wrapper.get('.child-card button.danger').trigger('click')
  await wrapper.get('[role="dialog"] button.danger').trigger('click')
  await flushPromises()
  expect(wrapper.find('form.profile-form').exists()).toBe(editedId === 8)
  if (editedId === 8) expect(wrapper.get('form.profile-form input').element.value).toBe('未保存的草稿')
  expect(deleteProfile).toHaveBeenCalledOnce()
})
