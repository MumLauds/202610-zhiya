import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App.vue'
import ReaderView from './components/ReaderView.vue'

const session = (id, versionId) => ({
  readingSessionId: id, storyVersionId: versionId, currentPage: 4,
  visitedPageNumbers: [4], revision: 3, status: 'IN_PROGRESS',
})
const story = {
  storyVersionId: 700, title: '服务端绘本', coverRef: 'asset-700-cover',
  pages: [
    { pageNumber: 4, text: '第一幕。', imageRef: 'asset-700-page-4', defaultNextPageNumber: 9 },
    { pageNumber: 9, text: '最后一幕。', imageRef: null, defaultNextPageNumber: null },
  ],
  branches: [],
}
const json = (data, status = 200) => new Response(JSON.stringify({ data }), { status })
let wrapper

function login(cookie) {
  document.cookie = `zhiya_csrf=${cookie}; path=/`
  window.dispatchEvent(new Event('focus'))
}

function mountApp(handler) {
  const fetch = vi.fn(handler)
  vi.stubGlobal('fetch', fetch)
  wrapper = mount(App, { global: { stubs: { ReaderView: true } } })
  return fetch
}

afterEach(() => {
  wrapper?.unmount()
  document.cookie = 'zhiya_csrf=; Max-Age=0; path=/'
  localStorage.clear()
  vi.unstubAllGlobals()
})

describe('mobile live mode', () => {
  it('restores the version bound by API-013 and keeps real pages and media', async () => {
    const snapshot = { session: session(73, 700), events: [], report: null }
    const fetch = mountApp(async (path) => {
      if (path === '/api/v1/reading-sessions/73') return json(snapshot)
      if (path === '/api/v1/story-versions/700') return json(story)
      return json({ items: [] })
    })
    await wrapper.get('.mode-mini').trigger('click')
    const inputs = wrapper.findAll('input')
    await inputs[1].setValue('91')
    await inputs[2].setValue('73')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    const reader = wrapper.getComponent(ReaderView)
    expect(reader.props('initialSnapshot')).toEqual(snapshot)
    expect(reader.props('storyVersionId')).toBe(700)
    expect(reader.props('book')).toMatchObject({
      title: story.title, cover: '/api/v1/media/asset-700-cover',
      pages: [
        { page: 4, text: '第一幕。', image: '/api/v1/media/asset-700-page-4', defaultNextPageNumber: 9 },
        { page: 9, image: expect.stringContaining('image-placeholder.svg'), defaultNextPageNumber: null },
      ],
    })
    expect(reader.props('book').pages[0].tokens.map((token) => token.char).join('')).toBe('第一幕。')
    expect(fetch.mock.calls.some(([path]) => path === '/api/v1/story-versions/91')).toBe(false)
    reader.vm.$emit('finished', { book: story, session: snapshot.session, report: { summary: '账户数据' } })
    expect(localStorage.length).toBe(0)
    login('different-account')
    await flushPromises()
    expect(wrapper.findComponent(ReaderView).exists()).toBe(false)
  })

  it('reads a real catalog book and surfaces a detail failure without opening the reader', async () => {
    let available = false
    const fetch = mountApp(async (path) => path.startsWith('/api/v1/storybooks?')
      ? json({ items: [story] })
      : available ? json(story) : json(null, 404))
    await wrapper.get('.mode-mini').trigger('click')
    await wrapper.findAll('input')[2].setValue('73')
    await wrapper.get('.setup-form button[type="button"]').trigger('click')
    await flushPromises()
    await wrapper.get('.library-phone-card').trigger('click')
    await flushPromises()

    expect(fetch.mock.calls.some(([path]) => path === '/api/v1/story-versions/700')).toBe(true)
    expect(wrapper.get('[role="alert"]').text()).toContain('404')
    expect(wrapper.findComponent(ReaderView).exists()).toBe(false)
    available = true
    await wrapper.get('.library-phone-card').trigger('click')
    await flushPromises()
    expect(wrapper.getComponent(ReaderView).props('storyVersionId')).toBe(700)
    expect(wrapper.getComponent(ReaderView).props('initialSessionId')).toBe(null)
    expect(fetch.mock.calls.some(([path]) => path === '/api/v1/reading-sessions/73')).toBe(false)
  })

  it('paginates the current account history and drops delayed responses after account changes', async () => {
    login('guardian-a')
    localStorage.setItem('zhiya_vue_reading_records', JSON.stringify([{ title: '旧账户混合记录' }]))
    let replyPageTwo
    const fetch = mountApp(async (path) => {
      const account = document.cookie
      if (path.includes('storybooks')) return json({ items: [story] })
      if (path.includes('page=2')) return new Promise((resolve) => { replyPageTwo = resolve })
      return json({ items: account.includes('guardian-a')
        ? Array.from({ length: 20 }, (_, index) => session(100 + index, story.storyVersionId))
        : [session(888, story.storyVersionId)] })
    })
    await wrapper.get('.mode-mini').trigger('click')
    await wrapper.findAll('.bottom-nav button')[3].trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('打开会话 100')
    await wrapper.findAll('button').find((button) => button.text() === '加载更多记录').trigger('click')
    expect(fetch.mock.calls.some(([path]) => path === '/api/v1/reading-sessions?page=2&pageSize=20')).toBe(true)

    login('guardian-b')
    await flushPromises()
    replyPageTwo(json({ items: [session(999, story.storyVersionId)] }))
    await flushPromises()
    expect(wrapper.text()).toContain('打开会话 888')
    expect(wrapper.text()).not.toMatch(/打开会话 (100|999)/)
    await wrapper.get('.mode-mini').trigger('click')
    expect(wrapper.text()).not.toContain('旧账户混合记录')
    expect(wrapper.text()).not.toContain('打开会话 888')
  })

  it('refreshes unfinished continuation progress and excludes inherited pages', async () => {
    const original = {
      ...session(73, 700), visitedPageNumbers: [1, 2, 3, 4], startPageNumber: 4, carriedPrefixPageCount: 3,
    }
    let latest = original
    const fetch = mountApp(async (path) => {
      if (path.includes('storybooks')) return json({ items: [story] })
      if (path === '/api/v1/story-versions/700') return json(story)
      if (path === '/api/v1/reading-sessions/73') return json({ session: original, events: [], report: null })
      return json({ items: [latest] })
    })
    await wrapper.get('.mode-mini').trigger('click')
    await wrapper.findAll('.bottom-nav button')[3].trigger('click')
    await flushPromises()
    expect(wrapper.get('.record-card').text()).toContain('1 页 · 进行中')
    await wrapper.get('.record-card button').trigger('click')
    await flushPromises()

    latest = { ...original, currentPage: 9, visitedPageNumbers: [1, 2, 3, 4, 9], revision: 4 }
    wrapper.getComponent(ReaderView).vm.$emit('exit')
    await flushPromises()
    await wrapper.findAll('.bottom-nav button')[3].trigger('click')
    await flushPromises()

    expect(wrapper.get('.record-card').text()).toContain('2 页 · 进行中')
    expect(fetch.mock.calls.filter(([path]) => path === '/api/v1/reading-sessions?page=1&pageSize=20')).toHaveLength(2)
  })
})
