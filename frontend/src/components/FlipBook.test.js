import { afterEach, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import flipBookScript from '../../public/vendor/openpageflip/index.iife.js?raw'
import FlipBook from './FlipBook.vue'

let wrapper
afterEach(() => { wrapper?.unmount(); vi.unstubAllGlobals() })

it('narrates only the active spread and animates a requested adjacent turn', async () => {
  const book = { on: vi.fn(), flipTo: vi.fn().mockResolvedValue(true), turnTo: vi.fn(), destroy: vi.fn() }
  vi.stubGlobal('OpenPageFlip', { createBook: () => book })
  wrapper = mount(FlipBook, {
    props: { canNarrate: true, pages: [
      { pageNumber: 1, text: '第一页' },
      { pageNumber: 2, text: '第二页' },
    ] },
  })
  expect(wrapper.findAll('.page-narration-text')).toHaveLength(1)
  await wrapper.get('.page-narration-text').trigger('click')
  expect(wrapper.emitted('narrate')).toHaveLength(1)
  expect(wrapper.vm.flipToPage(1)).toBe(true)
  expect(book.flipTo).toHaveBeenCalledWith(2)
  await wrapper.setProps({ pages: [...wrapper.props('pages')] })
  expect(book.turnTo).not.toHaveBeenCalled()
})

it('uses page image refs and placeholders, and restores the actual widget position', async () => {
  let onFlip
  const book = { on: vi.fn((name, handler) => { onFlip = handler }), turnTo: vi.fn((page) => onFlip({ page })), destroy: vi.fn() }
  vi.stubGlobal('OpenPageFlip', { createBook: vi.fn(() => book) })
  wrapper = mount(FlipBook, { props: { title: '绘本', startPage: 0, pages: [{ pageNumber: 1, text: '第一页', imageRef: 'page-one' }, { pageNumber: 3, text: '第三页', imageRef: null }, { pageNumber: 7, text: '第七页', imageRef: 'page-seven' }] } })
  expect(wrapper.findAll('img').map((img) => img.attributes('src'))).toEqual(['/api/v1/media/page-one?variant=reading'])
  expect(wrapper.find('.flip-page-empty').text()).toContain('本页暂无插图')
  await wrapper.find('img').trigger('error')
  expect(wrapper.text()).toContain('插图暂时无法显示')
  onFlip({ page: 4 })
  expect(wrapper.emitted('page-change')).toEqual([[{ storyIndex: 2, bookPage: 4 }]])
  await wrapper.setProps({ startPage: 1 })
  expect(book.turnTo).toHaveBeenLastCalledWith(2)
  wrapper.vm.showPage(0)
  expect(book.turnTo).toHaveBeenLastCalledWith(0)
  expect(wrapper.emitted('page-change')).toHaveLength(1)
  wrapper.unmount()
  expect(book.destroy).toHaveBeenCalledTimes(1)
  wrapper = null
})

it('prioritizes the current image and limits later image requests to both neighbors', async () => {
  const book = { on: vi.fn(), turnTo: vi.fn(), destroy: vi.fn() }
  vi.stubGlobal('OpenPageFlip', { createBook: vi.fn(() => book) })
  const pages = Array.from({ length: 5 }, (_, index) => ({
    pageNumber: index + 1,
    text: `第 ${index + 1} 页`,
    imageRef: `page-${index + 1}`,
  }))
  wrapper = mount(FlipBook, { props: { pages, startPage: 2 } })

  const images = () => wrapper.findAll('img').map((image) => [
    image.attributes('src'),
    image.attributes('fetchpriority'),
  ])
  expect(wrapper.findAll('.flip-page')).toHaveLength(10)
  expect(images()).toEqual([['/api/v1/media/page-3?variant=reading', 'high']])

  await wrapper.get('img').trigger('load')
  expect(images()).toEqual([
    ['/api/v1/media/page-2?variant=reading', 'low'],
    ['/api/v1/media/page-3?variant=reading', 'high'],
    ['/api/v1/media/page-4?variant=reading', 'low'],
  ])

  await wrapper.setProps({ startPage: 3 })
  expect(images()).toEqual([['/api/v1/media/page-4?variant=reading', 'high']])
  await wrapper.get('img').trigger('error')
  expect(wrapper.text()).toContain('插图暂时无法显示')
  expect(images()).toEqual([
    ['/api/v1/media/page-3?variant=reading', 'low'],
    ['/api/v1/media/page-5?variant=reading', 'low'],
  ])
  expect(wrapper.findAll('.flip-page')).toHaveLength(10)
})

it('keeps the prefetched image when a long reading window advances past a repeated page number', async () => {
  const widget = new Function(flipBookScript + '\nreturn OpenPageFlip')()
  const createBook = vi.spyOn(widget, 'createBook')
  vi.stubGlobal('OpenPageFlip', widget)
  vi.stubGlobal('matchMedia', () => ({ matches: true }))
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
  const width = vi.spyOn(Element.prototype, 'clientWidth', 'get').mockReturnValue(840)
  const height = vi.spyOn(Element.prototype, 'clientHeight', 'get').mockReturnValue(560)
  try {
    const pages = [1, 2, 3, 2, 5, 6, 7].map((pageNumber) => ({
      pageNumber,
      imageRef: `page-${pageNumber}`,
    }))
    wrapper = mount(FlipBook, { props: { pages, visitPages: pages, startPage: 2 } })
    const imageSlots = () => wrapper.findAll('.flip-page--illustration')
    const book = createBook.mock.results[0].value
    const setPages = vi.spyOn(book, 'setPages')
    await imageSlots()[2].get('img').trigger('load')
    const previousVisit = imageSlots()[1].get('img').element
    const currentVisit = imageSlots()[3].get('img')
    expect(currentVisit.element).not.toBe(previousVisit)
    expect(currentVisit.attributes('fetchpriority')).toBe('low')
    await currentVisit.trigger('load')

    await wrapper.setProps({ startPage: 3 })

    expect(createBook).toHaveBeenCalledTimes(1)
    expect(setPages).toHaveBeenCalledTimes(1)
    expect(imageSlots()).toHaveLength(5)
    expect(imageSlots()[0].attributes('aria-label')).toBe('第 2 页插图')
    expect(imageSlots()[0].find('img').exists()).toBe(false)
    expect(wrapper.element.contains(previousVisit)).toBe(false)
    expect(imageSlots()[2].get('img').element).toBe(currentVisit.element)
    expect(imageSlots()[2].get('img').attributes('fetchpriority')).toBe('high')
    expect(imageSlots()[3].get('img').attributes('src')).toBe('/api/v1/media/page-5?variant=reading')
    expect(imageSlots()[3].get('img').attributes('fetchpriority')).toBe('low')
  } finally {
    width.mockRestore()
    height.mockRestore()
  }
})
