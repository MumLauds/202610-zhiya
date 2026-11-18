import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { ApiError } from './api/client'
import { getStoryVersion, listStorybooks } from './api/storybooks'
import { addReadingEvent, getReadingSession } from './api/reading'

vi.mock('./api/auth', () => ({ checkHealth: vi.fn() }))
vi.mock('./api/profiles', () => ({ listProfiles: vi.fn(async () => []) }))
vi.mock('./api/storybooks', () => ({ getStoryVersion: vi.fn(), listStorybooks: vi.fn() }))
vi.mock('./api/reading', () => ({ getReadingSession: vi.fn(), addReadingEvent: vi.fn() }))

const book = { storyVersionId: 88, title: '书架上的绘本', status: 'PUBLISHED', pages: [{ pageNumber: 1, text: '只在授权时可读的正文' }] }
const firstPage = Array.from({ length: 30 }, (_, i) => ({ ...book, storyVersionId: i + 1, title: '绘本' + (i + 1) }))
const missing = () => new ApiError('资源不可用', { status: 404, code: 'RESOURCE_NOT_FOUND' })
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
  listStorybooks.mockResolvedValue([book])
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

it.each([false, true])('recovers the shifted book after a withdrawal (end of list already seen: %s)', async (endSeen) => {
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
  getStoryVersion.mockRejectedValueOnce(missing())
  await button('查看详情').trigger('click')
  await vi.waitFor(() => expect(wrapper.text()).toContain('绘本暂不可用'))
  await wrapper.get('.detail-toolbar button').trigger('click')
  await vi.waitFor(() => expect(wrapper.find('.library-page').exists()).toBe(true))
  await button('加载更多绘本').trigger('click')
  await flushPromises()

  expect(wrapper.getComponent({ name: 'BookCarousel' }).props('books').map((item) => item.storyVersionId))
    .toEqual(catalog.map((item) => item.storyVersionId))
  expect(button('加载更多绘本')).toBeUndefined()
  expect(listStorybooks.mock.calls.map(([{ page, ageBand }]) => [page, ageBand])).toEqual([
    [1, undefined], ...(endSeen ? [1, 2, 1, 2] : [1, 1, 2]).map((page) => [page, 'AGE_4_5']),
  ])
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
  expect(addReadingEvent).toHaveBeenCalledTimes(1)
  await wrapper.get('.reader-heading button.primary').trigger('click')
  await flushPromises()
  const writes = addReadingEvent.mock.calls.map(([, event]) => event)
  expect(writes).toHaveLength(2)
  expect(writes[1]).toMatchObject({ eventType: 'STARTED', expectedRevision: 0, pageNumber: 1 })
  expect(writes[1].clientEventId).not.toBe(writes[0].clientEventId)
  expect(wrapper.text()).not.toContain('开始本次阅读')
})
