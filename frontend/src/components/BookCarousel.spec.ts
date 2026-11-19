import { afterEach, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import BookCarousel from './BookCarousel.vue'

let wrapper: VueWrapper
afterEach(() => { wrapper?.unmount(); vi.unstubAllGlobals() })

it('keeps the selected shelf dot visible after loading more books and wrapping to the first book', async () => {
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  const books = Array.from({ length: 60 }, (_, i) => ({ id: String(i + 1), title: `绘本${i + 1}` }))
  wrapper = mount(BookCarousel, { props: { books: books.slice(0, 30), selectedId: '1' } })
  const strip = wrapper.get<HTMLElement>('.carousel-dots').element
  Object.defineProperty(strip, 'clientWidth', { value: 300 })
  await wrapper.setProps({ books })
  const dots = wrapper.findAll('.carousel-dot')
  dots.forEach((dot, index) => {
    Object.defineProperties(dot.element, {
      offsetLeft: { value: 9 + index * 25 },
      offsetWidth: { value: 22 },
    })
  })

  await wrapper.setProps({ selectedId: '60' })
  expect(strip.scrollLeft + strip.clientWidth).toBeGreaterThanOrEqual((dots[59].element as HTMLElement).offsetLeft + 22)
  await dots[59].trigger('click')
  expect(wrapper.emitted('select').at(-1)).toEqual(['60'])
  await wrapper.get('[aria-label="切换到下一本"]').trigger('click')
  expect(wrapper.emitted('select').at(-1)).toEqual(['1'])
  await wrapper.setProps({ selectedId: '1' })
  expect(strip.scrollLeft).toBeLessThanOrEqual((dots[0].element as HTMLElement).offsetLeft)
})
