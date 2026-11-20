import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { ApiError } from './api/client'
import { listProfiles } from './api/profiles'
import { getStoryVersion, listStorybooks } from './api/storybooks'
import { createReadingSession } from './api/reading'

vi.mock('./api/auth', () => ({ checkHealth: vi.fn(), login: vi.fn(), parentUnlock: vi.fn() }))
vi.mock('./api/profiles', () => ({ listProfiles: vi.fn(async () => []), deleteProfile: vi.fn() }))
vi.mock('./api/storybooks', () => ({ getStoryVersion: vi.fn(), listStorybooks: vi.fn() }))
vi.mock('./api/reading', () => ({ getReadingSession: vi.fn(), addReadingEvent: vi.fn(), createReadingSession: vi.fn(), deleteReadingSession: vi.fn(), getDeletionStatus: vi.fn(), listReadingSessions: vi.fn(), updateReadingMode: vi.fn() }))

const withdrawnBook = { storyVersionId: 88, title: '已下架的绘本', status: 'PUBLISHED' }
const availableBook = { ...withdrawnBook, storyVersionId: 89, title: '仍可阅读的绘本' }
const oldShelf = [withdrawnBook, availableBook]
const allQuery = { page: 1, pageSize: 30, ageBand: undefined, topic: undefined }
let wrapper

beforeEach(() => {
  vi.resetModules()
  vi.resetAllMocks()
  localStorage.clear()
  sessionStorage.clear()
  localStorage.setItem('zhiya.account', 'parent-a')
  listProfiles.mockResolvedValue([{ childProfileId: 7 }])
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = undefined
  vi.unstubAllGlobals()
})

it.each(['details', 'reading creation'])('refreshes the same shelf after a pending %s request returns 404', async (request) => {
  let rejectRequest
  let finishOldShelf
  const pendingRequest = new Promise((_, reject) => { rejectRequest = reject })
  listStorybooks.mockResolvedValueOnce(oldShelf)
    .mockReturnValueOnce(new Promise((resolve) => { finishOldShelf = resolve }))
    .mockResolvedValue([availableBook])
  if (request === 'details') getStoryVersion.mockReturnValueOnce(pendingRequest)
  else createReadingSession.mockReturnValueOnce(pendingRequest)

  window.history.replaceState({}, '', request === 'details' ? '/#detail/88' : '/#library')
  const { default: App } = await import('./App.vue')
  wrapper = mount(App, { global: { stubs: { BookCarousel: true } } })
  await flushPromises()

  if (request === 'details') {
    expect(getStoryVersion).toHaveBeenCalledExactlyOnceWith(88)
    await wrapper.get('.detail-toolbar button').trigger('click')
    await vi.waitFor(() => expect(wrapper.find('.library-page').exists()).toBe(true))
  } else {
    await wrapper.findAll('button').find((button) => button.text() === '开始阅读').trigger('click')
    expect(createReadingSession).toHaveBeenCalledExactlyOnceWith({ storyVersionId: 88, childProfileId: 7 })
  }

  expect(listStorybooks.mock.calls).toEqual([[allQuery]])
  const all = wrapper.get('.library-category-chip.active')
  expect(all.text()).toBe('全部')
  await all.trigger('click')
  await flushPromises()
  expect(listStorybooks.mock.calls).toEqual([[allQuery], [allQuery]])

  rejectRequest(new ApiError('资源不可用', { status: 404, code: 'RESOURCE_NOT_FOUND' }))
  await flushPromises()
  finishOldShelf(oldShelf)
  await flushPromises()

  expect(listStorybooks.mock.calls).toEqual([[allQuery], [allQuery], [allQuery]])
  expect(wrapper.getComponent({ name: 'BookCarousel' }).props('books').map(({ storyVersionId }) => storyVersionId))
    .toEqual([89])
  expect(wrapper.get('.library-feature-copy h2').text()).toBe(availableBook.title)
  expect(wrapper.text()).not.toContain(withdrawnBook.title)
})

it('restores saved book 63 after reselecting All during the initial shelf restore', async () => {
  localStorage.setItem('zhiya.selectedBookId', 'version-63')
  const catalog = Array.from({ length: 63 }, (_, index) => ({
    ...availableBook,
    storyVersionId: index + 1,
    title: '绘本' + (index + 1),
  }))
  let finishInitialPage
  listStorybooks.mockReturnValueOnce(new Promise((resolve) => { finishInitialPage = resolve }))
    .mockImplementation(async ({ page, pageSize }) => catalog.slice((page - 1) * pageSize, page * pageSize))

  window.history.replaceState({}, '', '/#library')
  const { default: App } = await import('./App.vue')
  wrapper = mount(App, { global: { stubs: { BookCarousel: true } } })
  await flushPromises()
  expect(listStorybooks.mock.calls).toEqual([[allQuery]])

  const all = wrapper.get('.library-category-chip.active')
  expect(all.text()).toBe('全部')
  await all.trigger('click')
  await flushPromises()

  expect(listStorybooks.mock.calls).toEqual([1, 1, 2, 3].map((page) => [{ ...allQuery, page }]))
  expect(wrapper.get('.library-feature-copy h2').text()).toBe('绘本63')

  finishInitialPage(catalog.slice(0, 30))
  await flushPromises()
  expect(listStorybooks).toHaveBeenCalledTimes(4)
  expect(wrapper.get('.library-feature-copy h2').text()).toBe('绘本63')
  expect(localStorage.getItem('zhiya.selectedBookId')).toBe('version-63')
})

it('keeps restored book 63 after reselecting All once the initial shelf restore completes', async () => {
  localStorage.setItem('zhiya.selectedBookId', 'version-63')
  const catalog = Array.from({ length: 63 }, (_, index) => ({
    ...availableBook,
    storyVersionId: index + 1,
    title: '绘本' + (index + 1),
  }))
  listStorybooks.mockImplementation(async ({ page, pageSize }) => catalog.slice((page - 1) * pageSize, page * pageSize))

  window.history.replaceState({}, '', '/#library')
  const { default: App } = await import('./App.vue')
  wrapper = mount(App, { global: { stubs: { BookCarousel: true } } })
  await flushPromises()

  expect(listStorybooks.mock.calls).toEqual([1, 2, 3].map((page) => [{ ...allQuery, page }]))
  expect(wrapper.get('.library-feature-copy h2').text()).toBe('绘本63')
  const all = wrapper.get('.library-category-chip.active')
  expect(all.text()).toBe('全部')
  await all.trigger('click')
  await flushPromises()

  expect(listStorybooks.mock.calls).toEqual([1, 2, 3, 1, 2, 3].map((page) => [{ ...allQuery, page }]))
  expect(wrapper.get('.library-feature-copy h2').text()).toBe('绘本63')
  expect(localStorage.getItem('zhiya.selectedBookId')).toBe('version-63')
})

it.each([
  { savedId: 63, expectedId: 62 },
  { savedId: null, expectedId: 60 },
])('keeps book $expectedId picked during a same-filter refresh (saved book: $savedId)', async ({ savedId, expectedId }) => {
  const selectionKey = 'zhiya.selectedBookId'
  if (savedId) localStorage.setItem(selectionKey, `version-${savedId}`)
  const catalog = Array.from({ length: 63 }, (_, index) => ({
    ...availableBook,
    storyVersionId: index + 1,
    title: `绘本${index + 1}`,
  }))
  listStorybooks.mockImplementation(async ({ page, pageSize }) => catalog.slice((page - 1) * pageSize, page * pageSize))
  createReadingSession.mockRejectedValueOnce(new ApiError('暂时无法开读', { status: 503 }))
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })

  window.history.replaceState({}, '', '/#library')
  const { default: App } = await import('./App.vue')
  wrapper = mount(App)
  await flushPromises()

  const button = (label) => wrapper.findAll('button').find((item) => item.text() === label)
  if (!savedId) {
    await button('加载更多绘本').trigger('click')
    await flushPromises()
    await button('加载更多绘本').trigger('click')
    await flushPromises()
    expect(localStorage.getItem(selectionKey)).toBeNull()
  }
  expect(wrapper.get('.library-feature-copy h2').text()).toBe(`绘本${expectedId + 1}`)

  let finishFirstPage
  let finishSecondPage
  listStorybooks
    .mockReturnValueOnce(new Promise((resolve) => { finishFirstPage = resolve }))
    .mockReturnValueOnce(new Promise((resolve) => { finishSecondPage = resolve }))
  await wrapper.get('.library-category-chip.active').trigger('click')
  expect(listStorybooks.mock.calls).toEqual([1, 2, 3, 1].map((page) => [{ ...allQuery, page }]))
  await wrapper.get('button[aria-label="切换到上一本"]').trigger('click')
  expect(localStorage.getItem(selectionKey)).toBe(`version-${expectedId}`)

  finishFirstPage(catalog.slice(0, allQuery.pageSize))
  await flushPromises()
  expect(listStorybooks).toHaveBeenLastCalledWith({ ...allQuery, page: 2 })
  expect(button('开始阅读')).toBeUndefined()

  finishSecondPage(catalog.slice(allQuery.pageSize, allQuery.pageSize * 2))
  await flushPromises()

  expect(wrapper.get('.library-feature-copy h2').text()).toBe(`绘本${expectedId}`)
  expect(localStorage.getItem(selectionKey)).toBe(`version-${expectedId}`)
  await button('开始阅读').trigger('click')
  expect(createReadingSession).toHaveBeenCalledExactlyOnceWith({ storyVersionId: expectedId, childProfileId: 7 })
})

it('does not restore a book picked on the old shelf after reselecting a new age filter', async () => {
  const selectionKey = 'zhiya.selectedBookId'
  localStorage.setItem(selectionKey, 'version-63')
  const books = Array.from({ length: 63 }, (_, index) => ({
    ...availableBook,
    storyVersionId: index + 1,
    title: `绘本${index + 1}`,
  }))
  listStorybooks.mockImplementation(async ({ page, pageSize }) => books.slice((page - 1) * pageSize, page * pageSize))
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })

  window.history.replaceState({}, '', '/#library')
  const { default: App } = await import('./App.vue')
  wrapper = mount(App)
  await flushPromises()
  const title = () => wrapper.get('.library-feature-copy h2').text()
  expect(title()).toBe('绘本63')

  let finishOlderFilter
  let finishLatestFilter
  listStorybooks
    .mockReturnValueOnce(new Promise((resolve) => { finishOlderFilter = resolve }))
    .mockReturnValueOnce(new Promise((resolve) => { finishLatestFilter = resolve }))
  await wrapper.findAll('.library-category-chip').find((chip) => chip.text() === '年龄').trigger('click')
  const age = wrapper.findAll('.library-option-chip').find((chip) => chip.text() === '3–4 岁')
  await age.trigger('click')
  await wrapper.get('button[aria-label="切换到上一本"]').trigger('click')
  expect(localStorage.getItem(selectionKey)).toBe('version-62')
  await age.trigger('click')

  finishLatestFilter(books.slice(0, allQuery.pageSize))
  await flushPromises()
  expect(title()).toBe('绘本1')

  finishOlderFilter(oldShelf)
  await flushPromises()
  const ageQuery = { ...allQuery, ageBand: 'AGE_3_4' }
  expect(listStorybooks.mock.calls).toEqual([
    ...[1, 2, 3].map((page) => [{ ...allQuery, page }]),
    [ageQuery],
    [ageQuery],
  ])
  expect(title()).toBe('绘本1')
  expect(localStorage.getItem(selectionKey)).toBe('version-62')
})
