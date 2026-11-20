import { afterEach, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { mountApp } from '../tests/mountApp'
import { ApiError } from './api/client'
import { createReadingSession, getReadingSession } from './api/reading'
import { getStoryVersion, listStorybooks } from './api/storybooks'

vi.mock('./api/auth', () => ({ checkHealth: vi.fn(), currentIdentity: async () => ({ role: 'GUARDIAN', accountId: 1 }) }))
vi.mock('./api/profiles', () => ({ listProfiles: async () => [{ childProfileId: 7 }] }))
vi.mock('./api/storybooks', () => ({ getStoryVersion: vi.fn(), listStorybooks: vi.fn() }))
vi.mock('./api/reading', () => ({ createReadingSession: vi.fn(), getReadingSession: vi.fn() }))

let wrapper

afterEach(() => {
  wrapper?.unmount()
  localStorage.clear()
  sessionStorage.clear()
})

it('checks an unconfirmed desktop reading creation with the original request key after an auth failure', async () => {
  vi.resetModules()
  localStorage.setItem('zhiya.account', 'parent-a')
  window.history.replaceState({}, '', '/#detail/api/88')
  const book = { storyVersionId: 88, title: '书架上的绘本', status: 'PUBLISHED', pages: [{ pageNumber: 1, text: '正文' }] }
  const session = {
    readingSessionId: 55, storyVersionId: 88, status: 'CREATED', mode: 'PARENT_CHILD',
    currentPage: 1, visitedPageNumbers: [], revision: 0,
  }
  listStorybooks.mockResolvedValue([book])
  getStoryVersion.mockResolvedValue(book)
  createReadingSession
    .mockRejectedValueOnce(new ApiError('连接中断', { status: 0, code: 'REQUEST_OUTCOME_UNKNOWN' }))
    .mockRejectedValueOnce(new ApiError('登录状态已变更', { status: 403, code: 'CSRF_INVALID' }))
    .mockResolvedValueOnce(session)
  getReadingSession.mockResolvedValue({ session, events: [] })
  const flipBook = { template: '<div />', methods: { showPage() {} } }
  wrapper = await mountApp({ global: { stubs: { BookCarousel: true, FlipBook: flipBook } } })
  await flushPromises()

  await wrapper.get('.detail-actions .primary').trigger('click')
  await flushPromises()
  expect(createReadingSession).toHaveBeenCalledExactlyOnceWith({
    childProfileId: 7, storyVersionId: 88, clientRequestId: expect.stringMatching(/^create-reading-.+$/),
  })
  expect(window.location.hash).toBe('#detail/api/88')

  const recoveryButton = wrapper.get('.toast button:not([aria-label])')
  expect(recoveryButton.text()).toBe('核对原阅读请求')
  await recoveryButton.trigger('click')
  await flushPromises()
  expect(window.location.hash).toBe('#detail/api/88')
  await wrapper.get('.toast button:not([aria-label])').trigger('click')
  await vi.waitFor(() => expect(window.location.hash).toBe('#reader/55'))
  expect(createReadingSession.mock.calls).toEqual([
    createReadingSession.mock.calls[0],
    createReadingSession.mock.calls[0],
    createReadingSession.mock.calls[0],
  ])
})
