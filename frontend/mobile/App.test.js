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
const child = { childProfileId: 13, nickname: '安安', ageBand: 'AGE_4_5', interests: ['动物', '自然'] }
let wrapper

function login(cookie) {
  document.cookie = `zhiya_csrf=${cookie}; path=/`
  window.dispatchEvent(new Event('focus'))
}

function mountApp(handler, props = {}) {
  const fetch = vi.fn(handler)
  vi.stubGlobal('fetch', fetch)
  wrapper = mount(App, { props, global: { stubs: { ReaderView: true } } })
  return fetch
}

afterEach(() => {
  wrapper?.unmount()
  window.history.replaceState(null, '', location.pathname)
  document.cookie = 'zhiya_csrf=; Max-Age=0; path=/'
  localStorage.clear()
  vi.unstubAllGlobals()
})

describe('mobile live mode', () => {
  it('restores the book and reading snapshot from their addresses without creating a session', async () => {
    window.history.replaceState(null, '', '#book/700')
    const snapshot = { session: session(73, 700), events: [], report: null }
    const fetch = mountApp(async path => {
      if (path === '/api/v1/story-versions/700') return json(story)
      if (path === '/api/v1/reading-sessions/73') return json(snapshot)
      if (path === '/api/v1/child-profiles') return json({ items: [child] })
      return json({ items: [] })
    }, { publicSite: true })
    await flushPromises()
    expect(wrapper.get('.profile-card h1').text()).toBe(story.title)

    window.history.pushState(null, '', '#reader/73')
    window.dispatchEvent(new Event('popstate'))
    await flushPromises()
    expect(wrapper.getComponent(ReaderView).props('initialSnapshot')).toEqual(snapshot)
    expect(fetch.mock.calls.some(([, options]) => options?.method === 'POST')).toBe(false)

    window.history.pushState(null, '', '#library')
    window.dispatchEvent(new Event('popstate'))
    await flushPromises()
    expect(wrapper.findComponent(ReaderView).exists()).toBe(false)
    expect(wrapper.get('input[type=search]').exists()).toBe(true)
  })

  it('keeps the offline demo selection separate from a live detail', async () => {
    mountApp(async path => {
      if (path === '/api/v1/story-versions/700') return json(story)
      if (path === '/api/v1/child-profiles') return json({ items: [child] })
      if (path.includes('/storybooks?')) return json({ items: [story] })
      return json({ items: [] })
    })
    await wrapper.get('.mode-mini').trigger('click')
    await flushPromises()
    await wrapper.get('a[href="#book/700"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('.profile-card h1').text()).toBe(story.title)

    await wrapper.get('.mobile-main > .text-button').trigger('click')
    await wrapper.get('.mode-mini').trigger('click')
    await wrapper.findAll('.bottom-nav button')[1].trigger('click')
    expect(wrapper.get('.library-start-bar').text()).not.toContain(story.title)
  })

  it('shows the exact API age range and author on the public mobile card', async () => {
    mountApp(async path => {
      if (path.endsWith('/child-profiles')) return json({ items: [] })
      if (path.includes('/storybooks?')) return json({ items: [{
        ...story, ageMin: 3, ageMax: 5, ageBand: 'AGE_3_4', author: '抹大拉（笔名）',
      }] })
      throw new Error(path)
    }, { publicSite: true })
    await flushPromises()
    const metadata = wrapper.get('.library-phone-card small').text()
    expect(metadata).toContain('3–5 岁')
    expect(metadata).toContain('抹大拉（笔名）')
  })

  it('opens a real book with the child selected by name on the public home page', async () => {
    mountApp(async path => {
      if (path.endsWith('/child-profiles')) return json({ items: [
        { childProfileId: 13, nickname: '安安' }, { childProfileId: 25, nickname: '乐乐' },
      ] })
      if (path.includes('/storybooks?')) return json({ items: [story] })
      if (path.endsWith('/story-versions/700')) return json(story)
      throw new Error(path)
    }, { publicSite: true })
    await flushPromises()
    expect(wrapper.find('input[type="number"]').exists()).toBe(false)
    expect(wrapper.get('.library-phone-card').element.disabled).toBe(true)
    await wrapper.get('select').setValue('25')
    await wrapper.get('.library-phone-card').trigger('click')
    await flushPromises()
    expect(wrapper.getComponent(ReaderView).props()).toMatchObject({ childProfileId: 25, storyVersionId: 700 })
  })

  it('clears the previous family selection on cross-tab login and offers profile creation for an empty account', async () => {
    login('guardian-a')
    mountApp(async path => {
      if (path.endsWith('/child-profiles')) return json({ items: document.cookie.includes('guardian-a')
        ? [{ childProfileId: 42, nickname: '安安' }] : [] })
      return json({ items: [story] })
    }, { publicSite: true })
    await flushPromises()
    expect(wrapper.get('select').element.value).toBe('42')
    document.cookie = 'zhiya_csrf=guardian-b; path=/'
    window.dispatchEvent(new StorageEvent('storage', { key: 'zhiya.account', storageArea: localStorage }))
    await flushPromises()
    expect(wrapper.find('select').exists()).toBe(false)
    expect(wrapper.get('a[href="/#account/children"]').text()).toContain('创建')
    expect(wrapper.get('.library-phone-card').element.disabled).toBe(true)
  })

  it('shows the selected server profile and links to the working workshop in local live mode', async () => {
    mountApp(async path => path.endsWith('/child-profiles')
      ? json({ items: [child, { childProfileId: 25, nickname: '乐乐', ageBand: 'AGE_5_6', interests: ['音乐'] }] })
      : json({ items: [story] }))
    await wrapper.get('.mode-mini').trigger('click')
    await flushPromises()
    expect(wrapper.find('input[type="number"]').exists()).toBe(false)
    await wrapper.get('.avatar-button').trigger('click')
    expect(wrapper.find('.profile-card').exists()).toBe(false)
    await wrapper.get('select').setValue('25')
    expect(wrapper.get('.profile-card').text()).toContain('乐乐')
    expect(wrapper.get('.profile-card').text()).toContain('5–6 岁 · 音乐')
    await wrapper.findAll('.bottom-nav button')[2].trigger('click')
    expect(wrapper.get('a[href="/#workshop"]').text()).toBe('前往创作工坊')
    expect(wrapper.find('input').exists()).toBe(false)
  })

  it('restores the version bound by API-013 and keeps real pages and media', async () => {
    const snapshot = { session: session(73, 700), events: [], report: null }
    const fetch = mountApp(async (path) => {
      if (path === '/api/v1/reading-sessions/73') return json(snapshot)
      if (path === '/api/v1/story-versions/700') return json(story)
      if (path.includes('/reading-sessions?')) return json({ items: [session(73, 91)] })
      return json({ items: [] })
    })
    await wrapper.get('.mode-mini').trigger('click')
    await wrapper.findAll('.bottom-nav button')[3].trigger('click')
    await flushPromises()
    await wrapper.get('.record-card button').trigger('click')
    await flushPromises()

    const reader = wrapper.getComponent(ReaderView)
    expect(reader.props('initialSnapshot')).toEqual(snapshot)
    expect(reader.props('storyVersionId')).toBe(700)
    expect(reader.props('book')).toMatchObject({
      title: story.title, cover: '/api/v1/media/asset-700-cover?variant=thumbnail',
      pages: [
        { page: 4, text: '第一幕。', image: '/api/v1/media/asset-700-page-4?variant=reading', defaultNextPageNumber: 9 },
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
    const fetch = mountApp(async (path) => {
      if (path.endsWith('/child-profiles')) return json({ items: [child] })
      if (path.startsWith('/api/v1/storybooks?')) return json({ items: [story] })
      return available ? json(story) : json(null, 404)
    })
    await wrapper.get('.mode-mini').trigger('click')
    await wrapper.findAll('.bottom-nav button')[1].trigger('click')
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

  it('paginates history and notices a cookie change when a request settles without an event', async () => {
    login('guardian-a')
    localStorage.setItem('zhiya_vue_reading_records', JSON.stringify([{ title: '旧账户混合记录' }]))
    let replyPageTwo
    const fetch = mountApp(async (path) => {
      const account = document.cookie
      if (path.endsWith('/child-profiles')) return json({ items: [child] })
      if (path.includes('storybooks')) return json({ items: [story] })
      if (path.includes('page=2')) return new Promise((resolve) => { replyPageTwo = resolve })
      return json({ items: account.includes('guardian-a')
        ? Array.from({ length: 20 }, (_, index) => session(100 + index, story.storyVersionId))
        : [{ ...session(888, story.storyVersionId), visitedPageNumbers: [4, 9] }] })
    })
    await wrapper.get('.mode-mini').trigger('click')
    await wrapper.findAll('.bottom-nav button')[3].trigger('click')
    await flushPromises()
    expect(wrapper.findAll('.record-card')).toHaveLength(20)
    expect(wrapper.get('.record-card button').text()).toBe('继续阅读')
    await wrapper.findAll('button').find((button) => button.text() === '加载更多记录').trigger('click')
    expect(fetch.mock.calls.some(([path]) => path === '/api/v1/reading-sessions?page=2&pageSize=20')).toBe(true)

    document.cookie = 'zhiya_csrf=guardian-b; path=/'
    replyPageTwo(json({ items: [session(999, story.storyVersionId)] }))
    await flushPromises()
    expect(wrapper.findAll('.record-card')).toHaveLength(1)
    expect(wrapper.get('.record-card').text()).toContain('2 页 · 进行中')
    await wrapper.get('.mode-mini').trigger('click')
    expect(wrapper.text()).not.toContain('旧账户混合记录')
    expect(wrapper.find('.record-card').exists()).toBe(false)
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
