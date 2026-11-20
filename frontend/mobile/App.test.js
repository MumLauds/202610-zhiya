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

  it('PR41 followup shows the finished history summary without requesting restricted story content', async () => {
    const finished = { ...session(73, 700), status: 'FINISHED' }
    const report = { summary: '服务端保存的本次小结', status: 'READY', activities: [] }
    login('guardian-a')
    const fetch = mountApp(async (path) => {
      if (path === '/api/v1/reading-sessions/73') return json({ session: finished, events: [], report })
      if (path === '/api/v1/reading-sessions?page=1&pageSize=20') return json({ items: [finished] })
      if (path.startsWith('/api/v1/story-versions/')) return json(null, 404)
      return json({ items: [] })
    })
    await wrapper.get('.mode-mini').trigger('click')
    await wrapper.findAll('.bottom-nav button')[3].trigger('click')
    await flushPromises()
    await wrapper.get('.record-card button').trigger('click')
    await flushPromises()

    expect(wrapper.get('.summary-panel').text()).toContain(report.summary)
    expect(fetch.mock.calls.filter(([path]) => path === '/api/v1/reading-sessions/73')).toHaveLength(1)
    expect(fetch.mock.calls.some(([path]) => path.startsWith('/api/v1/story-versions/'))).toBe(false)
  })

  it('refreshes a pending history report until its activity is ready using only the same session GET', async () => {
    const finished = { ...session(73, 700), status: 'FINISHED' }
    const pending = { status: 'PENDING', summary: '本次共读访问了一页。', activities: [] }
    const activity = { name: '画出纸船', materials: ['纸', '彩笔'], steps: ['找出纸船停靠的地方。', '一起画出那处场景。'] }
    const ready = { status: 'READY', summary: '已生成最终小结。', activities: [activity] }
    let latest = pending
    login('guardian-a')
    const fetch = mountApp(async (path) => {
      if (path === '/api/v1/reading-sessions/73') return json({ session: finished, events: [], report: latest })
      if (path === '/api/v1/reading-sessions?page=1&pageSize=20') return json({ items: [finished] })
      return json({ items: [] })
    })
    await wrapper.get('.mode-mini').trigger('click')
    await wrapper.findAll('.bottom-nav button')[3].trigger('click')
    await flushPromises()
    fetch.mockClear()
    await wrapper.get('.record-card button').trigger('click')
    await flushPromises()

    const refresh = () => wrapper.get('.summary-panel').findAll('button').find(button => button.text() === '刷新小结')
    expect(refresh()).toBeDefined()
    await refresh().trigger('click')
    await flushPromises()
    expect(wrapper.get('.summary-panel').text()).toContain(pending.summary)
    latest = ready
    await refresh().trigger('click')
    await flushPromises()
    const panel = wrapper.get('.summary-panel')
    expect(panel.findAll('h3').map(heading => heading.text())).toContain(`亲子活动 · ${activity.name}`)
    expect(panel.text()).toContain('准备材料：纸、彩笔')
    expect(panel.findAll('ol li').map(step => step.text())).toEqual(activity.steps)
    expect(refresh()).toBeUndefined()
    expect(fetch.mock.calls.map(([path, options]) => [options.method, path])).toEqual([
      ['GET', '/api/v1/reading-sessions/73'],
      ['GET', '/api/v1/reading-sessions/73'],
      ['GET', '/api/v1/reading-sessions/73'],
    ])
  })

  it('keeps the new account’s history report after the previous account’s refresh returns late', async () => {
    const finished = (id) => ({ ...session(id, 700), status: 'FINISHED' })
    const first = { session: finished(73), events: [], report: { status: 'PENDING', summary: '第一份记录' } }
    const second = { session: finished(84), events: [], report: {
      status: 'READY', summary: '第二份记录', activities: ['旧版活动说明', null, { name: '格式错误的活动', steps: 42 }],
    } }
    let lateReply
    let firstRequests = 0
    login('guardian-a')
    const fetch = mountApp(async (path) => {
      if (path === '/api/v1/reading-sessions/73') {
        if (++firstRequests === 1) return json(first)
        return new Promise(resolve => { lateReply = resolve })
      }
      if (path === '/api/v1/reading-sessions/84') return json(second)
      if (path.includes('/reading-sessions?')) return json({ items: [document.cookie.includes('guardian-a') ? first.session : second.session] })
      return json({ items: [] })
    })
    const openHistory = async () => {
      await wrapper.findAll('.bottom-nav button')[3].trigger('click')
      await flushPromises()
      await wrapper.get('.record-card button').trigger('click')
      await flushPromises()
    }
    await wrapper.get('.mode-mini').trigger('click')
    await openHistory()
    await wrapper.findAll('button').find(button => button.text() === '刷新小结').trigger('click')
    const signal = fetch.mock.calls.at(-1)[1].signal
    login('guardian-b')
    await flushPromises()
    await openHistory()
    lateReply(json({ ...first, report: { status: 'READY', summary: '第一份记录的迟到结果' } }))
    await flushPromises()
    const panel = wrapper.get('.summary-panel')
    expect(signal.aborted).toBe(true)
    expect(panel.text()).toContain('第二份记录')
    expect(panel.text()).toContain('旧版活动说明')
    expect(panel.text()).not.toMatch(/第一份记录|格式错误的活动|\[object Object\]/)
  })
})
