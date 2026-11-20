import { afterEach, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import FlipBook from './FlipBook.vue'

let wrapper
afterEach(() => { wrapper?.unmount(); vi.unstubAllGlobals() })

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
