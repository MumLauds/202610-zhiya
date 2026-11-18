import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import AuthPage from './AuthPage.vue'
import AccountPage from './AccountPage.vue'
import BookDetailPage from './BookDetailPage.vue'
import HomePage from './HomePage.vue'
import LibraryPage from './LibraryPage.vue'
import { mapApiBook } from '../data/preview'

const wrappers = []
const render = (component, props) => { const wrapper = mount(component, { props, global: { stubs: { BookCarousel: true } } }); wrappers.push(wrapper); return wrapper }
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }
afterEach(() => { wrappers.splice(0).forEach((wrapper) => wrapper.unmount()); localStorage.clear() })

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
