import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import BookCarousel from './BookCarousel.vue'

const books = [
  { id: 'center', title: '第一本', cover: '/one.webp' },
  { id: 'side', title: '第二本', cover: '/two.webp' },
]

let wrapper

beforeEach(() => {
  vi.useFakeTimers()
  vi.stubGlobal('ResizeObserver', class {
    observe() {}
    disconnect() {}
  })
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

it('opens the center book once and keeps it open while the session is being created', async () => {
  vi.stubGlobal('matchMedia', () => ({ matches: false }))
  wrapper = mount(BookCarousel, { props: {
    books, selectedId: 'center', onStart: () => wrapper.setProps({ starting: true }),
  } })
  await wrapper.get('.wheel-book.is-side').trigger('click')
  expect(wrapper.emitted('select')).toEqual([['side']])
  await wrapper.get('.wheel-book.selected').trigger('click')
  expect(wrapper.get('.wheel-book.is-opening .wheel-turning-leaf').exists()).toBe(true)
  expect(wrapper.emitted('start')).toBeUndefined()
  await wrapper.get('.wheel-turning-leaf').trigger('animationend')
  expect(wrapper.emitted('start')).toBeUndefined()

  await wrapper.get('.wheel-cover-back').trigger('animationend')
  expect(wrapper.emitted('start')).toBeUndefined()
  await wrapper.get('.wheel-book.is-opening .wheel-cover').trigger('animationend')
  await vi.runAllTimersAsync()
  expect(wrapper.emitted('start')).toEqual([[books[0]]])
  expect(wrapper.find('.wheel-book.is-opening').exists()).toBe(true)
  await wrapper.setProps({ starting: false })
  expect(wrapper.find('.wheel-book.is-opening').exists()).toBe(false)
})

it('starts reading without animating when reduced motion is requested', async () => {
  vi.stubGlobal('matchMedia', () => ({ matches: true }))
  wrapper = mount(BookCarousel, { props: { books, selectedId: 'center' } })
  await wrapper.get('.wheel-book.selected').trigger('click')
  expect(wrapper.emitted('start')).toEqual([[books[0]]])
  expect(wrapper.find('.wheel-book.is-opening').exists()).toBe(false)
})
