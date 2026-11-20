import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import AuthPage from './AuthPage.vue'
import AccountPage from './AccountPage.vue'
import BookDetailPage from './BookDetailPage.vue'
import HomePage from './HomePage.vue'
import LibraryPage from './LibraryPage.vue'
import ChildModePage from './ChildModePage.vue'
import AdminPage from './AdminPage.vue'
import { mapApiBook } from '../data/preview'
import { ApiError } from '../api/client'

const wrappers = []
const render = (component, props) => { const wrapper = mount(component, { props, global: { stubs: { BookCarousel: true } } }); wrappers.push(wrapper); return wrapper }
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }
afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount())
  localStorage.clear()
  sessionStorage.clear()
})

describe('forms wait for completion', () => {
  it.each(['login', 'register'])('prevents duplicate %s and retains fields after failure', async (mode) => {
    const request = deferred()
    const action = vi.fn().mockReturnValue(request.promise)
    const wrapper = render(AuthPage, { login: action, register: action, health: 'ready' })
    if (mode === 'register') await wrapper.findAll('.auth-tabs button')[1].trigger('click')
    await wrapper.find('input[autocomplete="username"]').setValue('parent-test')
    await wrapper.find('input[type="password"]').setValue('password123')
    await wrapper.find('form').trigger('submit')
    await wrapper.find('form').trigger('submit')
    expect(action).toHaveBeenCalledTimes(1)
    expect(wrapper.find('button[type="submit"]').element.disabled).toBe(true)
    request.reject(new Error('网络不可用'))
    await flushPromises()
    expect(wrapper.find('.form-error').text()).toContain('网络不可用')
    expect(wrapper.find('input[autocomplete="username"]').element.value).toBe('parent-test')
    expect(wrapper.find('button[type="submit"]').element.disabled).toBe(false)
  })

  it('keeps profile editor open while saving and on error, then closes on success', async () => {
    const request = deferred()
    const save = vi.fn().mockReturnValueOnce(request.promise).mockResolvedValueOnce({ childProfileId: 2 })
    const wrapper = render(AccountPage, { section: 'children', auth: { nickname: '家长' }, totals: { read: 0, pages: 0, sessions: 0 }, saveProfileAction: save })
    await wrapper.findAll('button').find((button) => button.text() === '添加孩子').trigger('click')
    await wrapper.find('form input').setValue('小芽')
    await wrapper.find('form').trigger('submit')
    await wrapper.find('form').trigger('submit')
    expect(save).toHaveBeenCalledTimes(1)
    expect(wrapper.find('form button[type="submit"]').element.disabled).toBe(true)
    expect(wrapper.find('form button[type="button"]').element.disabled).toBe(true)
    request.reject(new Error('暂时无法保存'))
    await flushPromises()
    expect(wrapper.find('form').exists()).toBe(true)
    expect(wrapper.find('[role="alert"]').text()).toBe('暂时无法保存')
    expect(wrapper.find('form input').element.value).toBe('小芽')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(wrapper.find('form').exists()).toBe(false)
  })

  it('confirms profile deletion and waits for the backend action', async () => {
    const remove = vi.fn().mockResolvedValue(true)
    const wrapper = render(AccountPage, {
      section: 'children',
      auth: { nickname: '家长', authenticated: true },
      profiles: [{ childProfileId: 2, nickname: '小芽', ageBand: 'AGE_4_5', interests: [], preferredMinutes: 15 }],
      activeProfileId: 2,
      totals: { read: 0, pages: 0, sessions: 0 },
      saveProfileAction: vi.fn(),
      deleteProfileAction: remove,
    })
    await wrapper.findAll('button').find((button) => button.text() === '删除档案').trigger('click')
    expect(wrapper.get('[role="dialog"]').text()).toContain('小芽')
    await wrapper.findAll('button').find((button) => button.text().includes('确认删除')).trigger('click')
    await flushPromises()
    expect(remove).toHaveBeenCalledWith(2)
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
  })

  it.each([null, '小芽'])('identifies both profiles on their cards and confirmations for nickname %s', async nickname => {
    const profiles = [7, 8].map(childProfileId => ({ childProfileId, nickname, ageBand: 'AGE_4_5', interests: [], preferredMinutes: 10 }))
    const wrapper = render(AccountPage, {
      section: 'children', auth: { authenticated: true }, profiles,
      totals: { read: 0, pages: 0, sessions: 0 }, saveProfileAction: vi.fn(),
    })
    for (const [index, card] of wrapper.findAll('.child-card').entries()) {
      const label = '档案 #' + profiles[index].childProfileId
      expect(card.text()).toContain(label)
      await card.get('button.danger').trigger('click')
      const dialog = wrapper.get('[role="dialog"]')
      expect(dialog.text()).toContain(label)
      await dialog.get('.modal-close').trigger('click')
    }
  })

  it('confirms reading-history deletion without removing successful-row state early', async () => {
    const request = deferred()
    const remove = vi.fn().mockReturnValue(request.promise)
    const wrapper = render(AccountPage, {
      section: 'growth',
      auth: { nickname: '家长', authenticated: true },
      books: [{ storyVersionId: 88, title: '测试绘本' }],
      sessions: [{
        readingSessionId: 7, storyVersionId: 88, visitedPageNumbers: [1, 2, 3],
        startPageNumber: 3, carriedPrefixPageCount: 2, status: 'FINISHED', revision: 2,
      }],
      totals: { read: 1, pages: 1, sessions: 1 },
      saveProfileAction: vi.fn(),
      deleteSessionAction: remove,
    })
    expect(wrapper.get('.recent-item').text()).toContain('浏览 1 页')
    await wrapper.findAll('button').find((button) => button.text() === '删除记录').trigger('click')
    await wrapper.findAll('button').find((button) => button.text().includes('确认删除')).trigger('click')
    expect(remove).toHaveBeenCalledWith(7)
    expect(wrapper.find('[role="dialog"]').exists()).toBe(true)
    request.resolve(true)
    await flushPromises()
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
  })

  it('shows every history record and labels identical readings by record id', async () => {
    const sessions = Array.from({ length: 5 }, (_, index) => ({
      readingSessionId: 10 - index,
      storyVersionId: 88,
      visitedPageNumbers: [1],
      status: 'FINISHED',
      revision: 2,
    }))
    const wrapper = render(AccountPage, {
      section: 'growth',
      auth: { nickname: '家长', authenticated: true },
      books: [{ storyVersionId: 88, title: '同一本绘本' }],
      sessions,
      totals: { read: 1, pages: 5, sessions: 5 },
      saveProfileAction: vi.fn(),
      deleteSessionAction: vi.fn().mockResolvedValue(true),
    })
    const deleteButtons = wrapper.findAll('button').filter((button) => button.text() === '删除记录')
    expect(deleteButtons).toHaveLength(5)
    expect(wrapper.text()).toContain('阅读记录 #10')
    expect(wrapper.text()).toContain('阅读记录 #6')
    await deleteButtons[4].trigger('click')
    expect(wrapper.get('[role="dialog"]').text()).toContain('阅读记录 #6')
  })

  it('keeps refreshed profile data separate from a completed deletion', async () => {
    const reload = vi.fn()
    const wrapper = render(AccountPage, {
      section: 'children',
      auth: { nickname: '家长', authenticated: true },
      profiles: [],
      profilesError: '服务暂不可用，请稍后重试',
      totals: { read: 0, pages: 0, sessions: 0 },
      saveProfileAction: vi.fn(),
      reloadAccountDataAction: reload,
    })
    expect(wrapper.get('[role="alert"]').text()).toContain('服务暂不可用')
    expect(wrapper.findAll('button').find((button) => button.text() === '＋ 添加孩子').element.disabled).toBe(true)
    await wrapper.findAll('button').find((button) => button.text() === '重新读取家长资料').trigger('click')
    expect(reload).toHaveBeenCalledTimes(1)
  })
})

describe('child-mode parent unlock', () => {
  it('requires the guardian password and delegates unlock to the backend action', async () => {
    const request = deferred()
    const unlock = vi.fn().mockReturnValue(request.promise)
    const wrapper = render(ChildModePage, {
      childMode: { sessionId: 9, allowedStoryVersionIds: [88], expiresAt: new Date(Date.now() + 600000).toISOString() },
      book: { title: '测试绘本' },
      auth: { account: 'parent-a' },
      unlockAction: unlock,
    })
    const form = wrapper.get('.child-handoff-unlock')
    await form.find('input[type="password"]').setValue('password123')
    await form.trigger('submit')
    await form.trigger('submit')
    expect(unlock).toHaveBeenCalledTimes(1)
    expect(unlock).toHaveBeenCalledWith('parent-a', 'password123')
    expect(wrapper.get('.child-handoff-unlock button').element.disabled).toBe(true)
    request.resolve(true)
    await flushPromises()
    expect(wrapper.get('.child-handoff-unlock button').element.disabled).toBe(false)
  })

  it.each([
    [
      '密码错误',
      new ApiError('账号或密码错误', { status: 401, code: 'INVALID_CREDENTIALS' }),
      '账号或密码错误',
    ],
    [
      '限流',
      new ApiError('尝试次数过多', { status: 429, code: 'RATE_LIMITED', retryAfter: '60' }),
      '请求过于频繁',
    ],
    [
      '服务不可用',
      new ApiError('服务暂不可用', { status: 503, code: 'DEPENDENCY_UNAVAILABLE' }),
      '服务暂不可用',
    ],
  ])('shows the backend reason when unlock fails because of %s', async (_, failure, expected) => {
    const unlock = vi.fn().mockRejectedValue(failure)
    const wrapper = render(ChildModePage, {
      childMode: { sessionId: 9, allowedStoryVersionCount: 1, expiresAt: new Date(Date.now() + 600000).toISOString() },
      auth: { account: 'parent-a' },
      unlockAction: unlock,
    })
    const form = wrapper.get('.child-handoff-unlock')
    await form.find('input[type="password"]').setValue('password123')
    await form.trigger('submit')
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain(expected)
  })

  it('omits an unverifiable authorization count after restoring a handoff', () => {
    const wrapper = render(ChildModePage, {
      childMode: { sessionId: 9, allowedStoryVersionIds: [], allowedStoryVersionCount: null, expiresAt: null },
      auth: { account: 'parent-a' },
      unlockAction: vi.fn(),
    })
    expect(wrapper.text()).toContain('以儿童端显示为准')
    expect(wrapper.text()).not.toContain('已授权')
  })
})

describe('account display labels', () => {
  it('shows readable ages and reading states in profiles and both history lists', async () => {
    const statuses = ['CREATED', 'IN_PROGRESS', 'FINISHED', 'INTERRUPTED']
    const wrapper = render(AccountPage, {
      section: 'children', auth: {}, totals: {}, saveProfileAction: vi.fn(),
      profiles: [{ childProfileId: 1, ageBand: 'AGE_4_5', preferredMinutes: 15 }],
      sessions: statuses.map((status, index) => ({
        readingSessionId: index + 1, status, currentPage: 1, visitedPageNumbers: [1],
      })),
    })
    expect(wrapper.get('.child-switch').text()).toContain('4–5 岁')
    expect(wrapper.get('.child-card').text()).toContain('4–5 岁')
    for (const section of ['overview', 'growth']) {
      await wrapper.setProps({ section })
      const labels = wrapper.findAll('.recent-item').map((item) => item.text())
      for (const [index, label] of ['未开始', '阅读中', '已完成', '已中断'].entries()) {
        expect(labels[index]).toContain(label)
        expect(labels[index]).not.toContain(statuses[index])
      }
    }
  })
})

describe('real book metadata', () => {
  it('does not borrow author, cover, age or questions from preview books', () => {
    const book = mapApiBook({ storybookId: 101, storyVersionId: 88, title: '后端绘本', status: 'PUBLISHED', pages: [{ pageNumber: 1, text: '真实内容', imageRef: 'page-key' }] })
    expect(book.author).toBe('作者信息暂缺')
    expect(book.ageLabel).toBe('适龄信息暂缺')
    expect(book.cover).toBe('/assets/image-placeholder.svg')
    expect(book.pages[0].question).toBeUndefined()
    expect(book.pages[0].imageRef).toBe('page-key')
    expect(book.topics).toEqual([])
  })

  it.each([['PUBLISHED', '公开已发布'], ['APPROVED_PRIVATE', '本人已审核私有绘本']])('shows the correct %s status', (status, label) => {
    const wrapper = render(BookDetailPage, { book: mapApiBook({ storyVersionId: 88, title: '绘本', status, pages: [] }) })
    expect(wrapper.text()).toContain(label)
  })

  it.each([HomePage, LibraryPage])('does not label a private approved book as public in the catalogue', (component) => {
    const book = mapApiBook({ storyVersionId: 88, title: '私有绘本', status: 'APPROVED_PRIVATE', pages: [] })
    const wrapper = render(component, { books: [book], auth: { authenticated: true } })
    expect(wrapper.text()).toContain('本人已审核私有绘本')
    expect(wrapper.text()).not.toContain('公开已发布')
  })
})

describe('recommendations and content workspace', () => {
  it('shows recommendation reasons and emits a topic-specific refresh', async () => {
    const book = mapApiBook({
      storyVersionId: 88,
      title: '推荐绘本',
      status: 'PUBLISHED',
      ageBand: 'AGE_4_5',
      pages: [{ pageNumber: 1, text: '推荐正文' }],
    })
    const wrapper = render(LibraryPage, {
      books: [book],
      auth: { authenticated: true, role: 'GUARDIAN' },
      activeProfile: { nickname: '小芽', preferredMinutes: 10 },
      recommendations: [{ storyVersionId: 88, reason: '符合 4～5 岁与兴趣「动物」', book }],
    })
    expect(wrapper.text()).toContain('符合 4～5 岁与兴趣「动物」')
    expect(wrapper.text()).toContain('根据年龄、兴趣和阅读时长挑选')
    expect(wrapper.find('.library-recommendations').exists()).toBe(false)
    expect(wrapper.findAll('button').some((button) => button.text() === '推荐')).toBe(false)
    await wrapper.findAll('button').find((button) => button.text() === '主题').trigger('click')
    await wrapper.findAll('button').find((button) => button.text() === '友谊').trigger('click')
    expect(wrapper.emitted('recommend').at(-1)).toEqual(['友谊'])
    expect(wrapper.emitted('filter').at(-1)).toEqual([{ ageBand: '', topic: '友谊' }])
  })

  it.each(['collapse', 'age'])('clears the recommendation topic on %s', async (action) => {
    const wrapper = render(LibraryPage, {})
    const click = (label) => wrapper.findAll('button').find((item) => item.text() === label).trigger('click')
    await click('主题')
    await click('友谊')
    if (action === 'collapse') {
      await click('主题')
    } else {
      await click('年龄')
      await click('4–5 岁')
    }
    expect(wrapper.emitted('filter').at(-1)).toEqual([{ ageBand: action === 'age' ? 'AGE_4_5' : '', topic: '' }])
    expect(wrapper.emitted('recommend')).toEqual([['友谊'], ['']])
  })

  it('builds a draft payload and exposes publish actions', async () => {
    const createStoryAction = vi.fn().mockResolvedValue(true)
    const setStoryStatusAction = vi.fn().mockResolvedValue(true)
    const story = {
      storyVersionId: 201,
      title: '待发布绘本',
      status: 'DRAFT',
      versionNumber: 1,
      pages: [{ pageNumber: 1, text: '第一页', imageRef: 'none' }],
      branches: [],
    }
    const wrapper = render(AdminPage, {
      section: 'storybooks',
      admin: { storyVersions: [story], knowledgeItems: [], loading: false, busy: false, error: '' },
      createStoryAction,
      setStoryStatusAction,
      createKnowledgeAction: vi.fn(),
      updateKnowledgeAction: vi.fn(),
    })
    await wrapper.get('input[placeholder="例如：纸船停在哪里"]').setValue('纸船去哪里')
    await wrapper.get('input[placeholder="互助，自然观察"]').setValue('自然观察，勇气')
    await wrapper.get('input[placeholder="自制或明确授权依据"]').setValue('团队原创')
    const textareas = wrapper.findAll('textarea')
    await textareas[0].setValue('小纸船漂进浅湾。\n小芽把它推回水里。\n船靠岸了。')
    await textareas[1].setValue('asset-first\n\nasset-third')
    await wrapper.get('.admin-form').trigger('submit')
    await flushPromises()
    expect(createStoryAction.mock.calls[0][0]).toMatchObject({
      title: '纸船去哪里',
      topics: ['自然观察', '勇气'],
      licenseNote: '团队原创',
    })
    expect(createStoryAction.mock.calls[0][0].pages.map(page => page.imageRef)).toEqual(['asset-first', 'none', 'asset-third'])
    await wrapper.findAll('button').find((button) => button.text() === '审核并发布').trigger('click')
    expect(setStoryStatusAction).toHaveBeenCalledWith(201, 'PUBLISHED')
  })

  it.each([undefined, '第 2 节'])('shows the full source reference before a reviewer approves, locator %s', async (locator) => {
    const item = { knowledgeItemId: 806, status: 'DRAFT', title: '示例条目', content: '观察画面',
      sourceTitle: '阅读资料', sourceReference: '文献编号 A-17（2025 年版）', locator }
    const updateKnowledgeAction = vi.fn()
    const wrapper = render(AdminPage, { section: 'knowledge', admin: { knowledgeItems: [item] },
      createStoryAction: vi.fn(), setStoryStatusAction: vi.fn(), createKnowledgeAction: vi.fn(), updateKnowledgeAction })
    const card = wrapper.get('.admin-card')
    expect(card.text()).toContain(item.sourceReference)
    if (locator) expect(card.text()).toContain(locator)
    await card.findAll('button').find(button => button.text() === '审核通过').trigger('click')
    expect(updateKnowledgeAction).toHaveBeenCalledWith(806, { status: 'APPROVED' })
  })

  it('creates a knowledge revision instead of editing the historical row', async () => {
    const updateKnowledgeAction = vi.fn().mockResolvedValue(true)
    const item = {
      knowledgeItemId: 301,
      itemKey: 'guided-observation',
      title: '先观察再提问',
      sourceTitle: '内容规范',
      locator: '第 1 节',
      ageBand: null,
      status: 'APPROVED',
      versionNumber: 1,
      content: '先请孩子观察画面。',
    }
    const wrapper = render(AdminPage, {
      section: 'knowledge',
      admin: { storyVersions: [], knowledgeItems: [item], loading: false, busy: false, error: '' },
      createStoryAction: vi.fn(),
      setStoryStatusAction: vi.fn(),
      createKnowledgeAction: vi.fn(),
      updateKnowledgeAction,
    })
    await wrapper.findAll('button').find((button) => button.text() === '创建修订').trigger('click')
    await wrapper.get('[role="dialog"] textarea').setValue('先请孩子观察，再让他说。')
    await wrapper.findAll('button').find((button) => button.text() === '保存新版本').trigger('click')
    await flushPromises()
    expect(updateKnowledgeAction).toHaveBeenCalledWith(301, {
      content: '先请孩子观察，再让他说。',
      status: 'APPROVED',
    })
  })
})
