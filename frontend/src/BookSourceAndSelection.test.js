import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { mountApp } from '../tests/mountApp'
import { ApiError } from './api/client'
import { listProfiles } from './api/profiles'
import { getStoryVersion, listStorybooks } from './api/storybooks'
import { createReadingSession, getReadingSession } from './api/reading'
import { previewBooks } from './data/preview'

vi.mock('./api/auth', () => ({ checkHealth: vi.fn(), currentIdentity: async () => ({ role: 'GUARDIAN', accountId: 1 }) }))
vi.mock('./api/profiles', () => ({ listProfiles: vi.fn(async () => []) }))
vi.mock('./api/storybooks', () => ({ getStoryVersion: vi.fn(), listStorybooks: vi.fn() }))
vi.mock('./api/reading', () => ({ createReadingSession: vi.fn(), getReadingSession: vi.fn() }))

const selectionKey = 'zhiya.selectedBookId'
const book = (storyVersionId) => ({
  storyVersionId,
  title: '线上绘本' + storyVersionId,
  status: 'PUBLISHED',
  pages: [{ pageNumber: 1, text: '线上正文' }],
})
const firstPage = Array.from({ length: 30 }, (_, index) => book(index + 1))
let wrapper

async function open(path) {
  window.history.replaceState({}, '', '/#' + path)
  const flipBook = { template: '<div />', methods: { showPage() {} } }
  wrapper = await mountApp({ global: { stubs: { BookCarousel: true, FlipBook: flipBook } } })
  await flushPromises()
}

function button(label) {
  return wrapper.findAll('button').find((item) => item.text() === label)
}

async function clickRoute(target) {
  const changed = new Promise((resolve) => window.addEventListener('hashchange', resolve, { once: true }))
  await target.trigger('click')
  await changed
  await flushPromises()
}

function holdShelfPage() {
  let release
  listStorybooks.mockReturnValueOnce(new Promise((resolve) => { release = resolve }))
  return release
}

beforeEach(() => {
  vi.resetModules()
  vi.clearAllMocks()
  listStorybooks.mockReset()
  getStoryVersion.mockReset()
  localStorage.clear()
  sessionStorage.clear()
  localStorage.setItem('zhiya.account', 'parent-a')
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = undefined
  vi.useRealTimers()
})

it('keeps the preview chosen on the loading homepage through details and reading', async () => {
  const releaseShelf = holdShelfPage()
  const preview = previewBooks.find(({ storyVersionId }) => storyVersionId === 1001)
  getStoryVersion.mockResolvedValue(book(1001))
  await open('home')
  expect(listStorybooks).toHaveBeenCalledTimes(1)
  expect(wrapper.get('.home-info-card h2').text()).toBe(preview.title)
  await clickRoute(wrapper.get('.hero-book-cover'))

  releaseShelf([book(1001)])
  await flushPromises()

  expect(wrapper.get('.detail-card h1').text()).toBe(preview.title)
  expect(getStoryVersion).not.toHaveBeenCalled()
  await clickRoute(button('开始阅读'))
  expect(window.location.hash).toBe('#reader/preview/1001')
  expect(wrapper.get('.reader-page h1').text()).toBe(preview.title)
  expect(createReadingSession).not.toHaveBeenCalled()
})

it('opens the online version from a cold detail link when the shelf returns 503', async () => {
  localStorage.setItem(selectionKey, 'version-63')
  listStorybooks.mockRejectedValue(new ApiError('书架暂不可用', { status: 503 }))
  getStoryVersion.mockResolvedValue(book(1001))

  await open('detail/1001')

  expect(listStorybooks).toHaveBeenCalledTimes(1)
  expect(getStoryVersion).toHaveBeenCalledWith(1001)
  expect(wrapper.get('.detail-card h1').text()).toBe(book(1001).title)
})

it.each([1001, 88])('keeps online version %s while retrying a failed identity check', async (storyVersionId) => {
  const onlineBook = book(storyVersionId)
  const profile = { childProfileId: 7 }
  const session = {
    readingSessionId: 55,
    storyVersionId,
    status: 'CREATED',
    mode: 'PARENT_CHILD',
    currentPage: 1,
    visitedPageNumbers: [],
    revision: 1,
  }
  listProfiles.mockRejectedValueOnce(new ApiError('档案暂不可用', { status: 503 }))
  listStorybooks.mockResolvedValue([onlineBook])
  getStoryVersion.mockResolvedValue(onlineBook)
  createReadingSession.mockResolvedValueOnce(session)
  getReadingSession.mockResolvedValueOnce({ session, events: [] })

  await open('detail/api/' + storyVersionId)
  expect(listProfiles).toHaveBeenCalledTimes(1)
  expect(wrapper.get('.detail-card h1').text()).toBe(onlineBook.title)
  listProfiles.mockRejectedValueOnce(new ApiError('档案仍暂不可用', { status: 503 }))
  await button('重试身份核验').trigger('click')
  await flushPromises()
  expect(listProfiles).toHaveBeenCalledTimes(2)
  expect(button('重试身份核验').element.disabled).toBe(false)

  expect(window.location.hash).toBe('#detail/api/' + storyVersionId)
  expect(wrapper.get('.detail-card h1').text()).toBe(onlineBook.title)
  expect(wrapper.find('.reader-page').exists()).toBe(false)
  expect(createReadingSession).not.toHaveBeenCalled()

  listProfiles.mockResolvedValueOnce([profile])
  await button('重试身份核验').trigger('click')
  await flushPromises()
  expect(listProfiles).toHaveBeenCalledTimes(3)
  expect(wrapper.get('.detail-card h1').text()).toBe(onlineBook.title)
  expect(createReadingSession).not.toHaveBeenCalled()
  await clickRoute(button('开始阅读'))

  expect(createReadingSession.mock.calls).toEqual([[{ storyVersionId, childProfileId: 7 }]])
  expect(window.location.hash).toBe('#reader/55')
  expect(wrapper.get('.reader-page h1').text()).toBe(onlineBook.title)
})

it('legacy detail keeps the online book through identity recovery and reading', async () => {
  const onlineBook = book(1001)
  const session = {
    readingSessionId: 55,
    storyVersionId: 1001,
    status: 'CREATED',
    mode: 'PARENT_CHILD',
    currentPage: 1,
    visitedPageNumbers: [],
    revision: 1,
  }
  listProfiles.mockRejectedValueOnce(new ApiError('档案暂不可用', { status: 503 }))
  listProfiles.mockResolvedValueOnce([{ childProfileId: 7 }])
  listStorybooks.mockResolvedValue([onlineBook])
  getStoryVersion.mockResolvedValue(onlineBook)
  createReadingSession.mockResolvedValueOnce(session)
  getReadingSession.mockResolvedValueOnce({ session, events: [] })

  await open('detail/1001')
  expect(getStoryVersion).toHaveBeenCalledWith(1001)
  expect(wrapper.get('.detail-card h1').text()).toBe(onlineBook.title)
  expect(wrapper.get('.detail-card').text()).not.toContain('设计预览')
  expect(createReadingSession).not.toHaveBeenCalled()

  await button('重试身份核验').trigger('click')
  await flushPromises()
  expect(listProfiles).toHaveBeenCalledTimes(2)
  expect(window.location.hash).toBe('#detail/1001')
  expect(wrapper.get('.detail-card h1').text()).toBe(onlineBook.title)
  await clickRoute(button('开始阅读'))

  expect(createReadingSession.mock.calls).toEqual([[{ storyVersionId: 1001, childProfileId: 7 }]])
  expect(window.location.hash).toBe('#reader/55')
  expect(wrapper.get('.reader-page h1').text()).toBe(onlineBook.title)
})

it('legacy detail offers identity retry in place when the online book request also fails', async () => {
  const onlineBook = book(1001)
  listProfiles.mockRejectedValueOnce(new ApiError('档案暂不可用', { status: 503 }))
  listProfiles.mockRejectedValueOnce(new ApiError('档案仍暂不可用', { status: 503 }))
  listStorybooks.mockResolvedValue([onlineBook])
  getStoryVersion.mockRejectedValue(new ApiError('正文暂不可用', { status: 503 }))

  await open('detail/1001')
  expect(getStoryVersion).toHaveBeenCalledWith(1001)
  expect(wrapper.text()).not.toContain('设计预览')
  expect(button('开始阅读')).toBeUndefined()

  await button('重试身份核验').trigger('click')
  await flushPromises()
  expect(listProfiles).toHaveBeenCalledTimes(2)
  expect(window.location.hash).toBe('#detail/1001')
  expect(button('重试身份核验').element.disabled).toBe(false)
  expect(createReadingSession).not.toHaveBeenCalled()
})

it.each([200, 404])('legacy detail preserves its online source when identity recovery finds an empty catalog and detail returns %s', async (status) => {
  const onlineBook = book(1001)
  listProfiles.mockRejectedValueOnce(new ApiError('档案暂不可用', { status: 503 }))
  listProfiles.mockResolvedValueOnce([{ childProfileId: 7 }])
  listStorybooks.mockResolvedValue([])
  getStoryVersion.mockResolvedValueOnce(onlineBook)
  if (status === 404) {
    getStoryVersion.mockRejectedValueOnce(new ApiError('资源不可用', { status: 404, code: 'RESOURCE_NOT_FOUND' }))
  } else {
    getStoryVersion.mockResolvedValueOnce(onlineBook)
  }

  await open('detail/1001')
  expect(listProfiles).toHaveBeenCalledTimes(1)
  expect(listStorybooks).not.toHaveBeenCalled()
  expect(getStoryVersion).toHaveBeenCalledExactlyOnceWith(1001)
  expect(wrapper.get('.detail-card h1').text()).toBe(onlineBook.title)

  await button('重试身份核验').trigger('click')
  await flushPromises()

  expect(listProfiles).toHaveBeenCalledTimes(2)
  expect(listStorybooks).toHaveBeenCalled()
  expect(getStoryVersion.mock.calls).toEqual([[1001], [1001]])
  expect(window.location.hash).toBe('#detail/1001')
  if (status === 404) {
    expect(wrapper.find('.detail-card').exists()).toBe(false)
    expect(wrapper.get('.detail-page .empty-state h2').text()).toBe('绘本暂不可用')
    expect(wrapper.get('.toast').text()).toBe('资源不可用')
    expect(button('开始阅读')).toBeUndefined()
  } else {
    expect(wrapper.get('.detail-card h1').text()).toBe(onlineBook.title)
    expect(button('开始阅读')).toBeDefined()
  }
  expect(createReadingSession).not.toHaveBeenCalled()
})

it('keeps the homepage carousel rotating after the shelf defaults to the next page', async () => {
  vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
  listStorybooks.mockResolvedValueOnce(firstPage).mockResolvedValueOnce([book(31)])
  await open('library')
  await button('加载更多绘本').trigger('click')
  await flushPromises()
  expect(wrapper.get('.library-feature-copy h2').text()).toBe(book(31).title)

  await clickRoute(button('首页'))
  const homepageTitle = () => wrapper.get('.home-info-card h2').text()
  const initialTitle = homepageTitle()
  await vi.advanceTimersByTimeAsync(3600)
  expect(homepageTitle()).not.toBe(initialTitle)
  expect(localStorage.getItem(selectionKey)).toBeNull()
})

it('preserves a manual shelf selection made while loading the next page', async () => {
  listStorybooks.mockResolvedValueOnce(firstPage)
  const releaseNextPage = holdShelfPage()
  await open('library')
  await button('加载更多绘本').trigger('click')
  expect(listStorybooks.mock.calls.map(([query]) => query.page)).toEqual([1, 2])

  wrapper.getComponent({ name: 'BookCarousel' }).vm.$emit('select', 'version-2')
  await flushPromises()
  expect(wrapper.get('.library-feature-copy h2').text()).toBe(book(2).title)

  releaseNextPage([book(31)])
  await flushPromises()
  expect(wrapper.get('.library-feature-copy h2').text()).toBe(book(2).title)
  expect(localStorage.getItem(selectionKey)).toBe('version-2')
})
