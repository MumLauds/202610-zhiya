import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import HomePage from './HomePage.vue'
import { recordPlanActivity } from '../data/readingPlan'

const books = [
  { id: 'book-1', title: '第一本', cover: '/one.png', topics: [], pages: [] },
  { id: 'book-2', title: '第二本', cover: '/two.png', topics: [], pages: [] },
  { id: 'book-3', title: '第三本', cover: '/three.png', topics: [], pages: [] },
]

const render = (selectedBookId = '') => mount(HomePage, {
  props: {
    books,
    selectedBookId,
    auth: { authenticated: false },
  },
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

it('updates the daily and weekly goals at local midnight without a remount', async () => {
  vi.useFakeTimers()
  const sundayNight = new Date(2026, 10, 22, 23, 59, 50)
  vi.setSystemTime(sundayNight)
  recordPlanActivity('8.2', sundayNight.getTime(), 15 * 60_000, 55)
  const wrapper = mount(HomePage, { props: {
    books, auth: { authenticated: true, accountId: 8 },
    activeProfile: { childProfileId: 2, preferredMinutes: 15 },
  } })
  try {
    expect(wrapper.get('.home-plan-time strong').text()).toBe('15/15')
    expect(wrapper.get('.home-plan-counts').text()).toContain('1/1 本 · 本周 1/5 天')
    await vi.advanceTimersByTimeAsync(10_000)
    expect(wrapper.get('.home-plan-time strong').text()).toBe('0/15')
    expect(wrapper.get('.home-plan-counts').text()).toContain('0/1 本 · 本周 0/5 天')
  } finally {
    wrapper.unmount()
    localStorage.removeItem('zhiya.readingActivity.8.2')
  }
})

describe('home featured book', () => {
  it('motion preference: respects reduced motion and live preference changes', async () => {
    vi.useFakeTimers()
    const preference = { matches: true }
    const matchMedia = vi.fn(() => preference)
    vi.stubGlobal('matchMedia', matchMedia)
    const wrapper = render()
    const title = wrapper.get('.home-info-card h2')
    try {
      expect(matchMedia).toHaveBeenCalledWith('(prefers-reduced-motion: reduce)')
      await vi.advanceTimersByTimeAsync(3600)
      expect(title.text()).toBe('第一本')

      await wrapper.get('.shelf-arrow-hit.next').trigger('click')
      expect(wrapper.emitted('select')?.at(-1)).toEqual(['book-2'])
      await wrapper.get('.home-rotation-toggle').trigger('click')
      await vi.advanceTimersByTimeAsync(3600)
      expect(title.text()).toBe('第二本')
      preference.matches = false
      await vi.advanceTimersByTimeAsync(3600)
      expect(title.text()).toBe('第三本')

      preference.matches = true
      await vi.advanceTimersByTimeAsync(7200)
      expect(title.text()).toBe('第三本')
    } finally {
      wrapper.unmount()
    }
  })

  it('motion preference: keeps automatic rotation available without matchMedia', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('matchMedia', undefined)
    const wrapper = render()
    try {
      await vi.advanceTimersByTimeAsync(3600)
      expect(wrapper.get('.home-info-card h2').text()).toBe('第二本')
    } finally {
      wrapper.unmount()
    }
  })

  it('cycles the open-book cover and information until the reader selects a book', async () => {
    vi.useFakeTimers()
    const wrapper = render()
    expect(wrapper.get('.hero-book-cover img').attributes('src')).toBe('/one.png')
    expect(wrapper.get('.home-info-card h2').text()).toBe('第一本')

    await vi.advanceTimersByTimeAsync(3600)
    expect(wrapper.get('.hero-book-cover img').attributes('src')).toBe('/two.png')
    expect(wrapper.get('.home-info-card h2').text()).toBe('第二本')
    expect(wrapper.get('.shelf-showcase-book.featured img').attributes('src')).toBe('/two.png')
    expect(wrapper.emitted('select')).toBeUndefined()

    await wrapper.get('.shelf-arrow-hit.next').trigger('click')
    expect(wrapper.emitted('select')?.at(-1)).toEqual(['book-3'])
    await wrapper.get('.shelf-showcase-book.previous').trigger('click')
    expect(wrapper.emitted('select')?.at(-1)).toEqual(['book-2'])
    await wrapper.setProps({ selectedBookId: 'book-2' })

    await vi.advanceTimersByTimeAsync(7200)
    expect(wrapper.get('.hero-book-cover img').attributes('src')).toBe('/two.png')
    expect(wrapper.get('.home-info-card h2').text()).toBe('第二本')
    wrapper.unmount()
  })

  it('pauses and resumes automatic rotation from the currently selected book', async () => {
    vi.useFakeTimers()
    const wrapper = render()
    const toggle = wrapper.get('.home-rotation-toggle')
    const title = wrapper.get('.home-info-card h2')
    try {
      expect(toggle.text()).toBe('暂停自动轮播')
      await toggle.trigger('focusin')
      await toggle.trigger('click')
      await vi.advanceTimersByTimeAsync(7200)
      expect(toggle.text()).toBe('继续自动轮播')
      expect(title.text()).toBe('第一本')

      await toggle.trigger('click')
      await vi.advanceTimersByTimeAsync(3600)
      expect(title.text()).toBe('第二本')

      await wrapper.setProps({ selectedBookId: 'book-2' })
      expect(toggle.text()).toBe('继续自动轮播')
      await toggle.trigger('click')
      await vi.advanceTimersByTimeAsync(3600)
      expect(title.text()).toBe('第三本')
      await toggle.trigger('click')
      expect(title.text()).toBe('第三本')
    } finally {
      wrapper.unmount()
    }
  })

  it('pauses the carousel until hover and keyboard focus both leave the recommendations', async () => {
    vi.useFakeTimers()
    const wrapper = render()
    const info = wrapper.get('.home-info-card')
    const cover = wrapper.get('.hero-book-cover')
    const arrow = wrapper.get('.shelf-arrow-hit.next')

    await info.trigger('mouseenter')
    await vi.advanceTimersByTimeAsync(3600)
    expect(wrapper.get('.hero-book-cover img').attributes('src')).toBe('/one.png')

    await cover.trigger('focusin')
    await info.trigger('mouseleave')
    await vi.advanceTimersByTimeAsync(3600)
    expect(wrapper.get('.hero-book-cover img').attributes('src')).toBe('/one.png')

    await cover.trigger('focusout', { relatedTarget: arrow.element })
    await arrow.trigger('focusin', { relatedTarget: cover.element })
    await vi.advanceTimersByTimeAsync(3600)
    expect(wrapper.get('.hero-book-cover img').attributes('src')).toBe('/one.png')

    await arrow.trigger('focusout')
    await vi.advanceTimersByTimeAsync(3600)
    expect(wrapper.get('.hero-book-cover img').attributes('src')).toBe('/two.png')
    wrapper.unmount()
  })
})
