import { expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import LibraryPage from './LibraryPage.vue'

it('shows the applied filter independently of the open category and clears its selection on reset', async () => {
  const wrapper = mount(LibraryPage, {
    props: { filters: { ageBand: 'AGE_4_5', topic: '' } },
  })
  try {
    const selectedOption = () => wrapper.get('.library-option-chip.active[aria-pressed="true"]').text()
    expect(selectedOption()).toBe('4–5 岁')
    expect(wrapper.text()).toContain('当前筛选：4–5 岁')

    const [clear, , topic] = wrapper.findAll('.library-category-chip')
    await topic.trigger('click')
    expect(wrapper.text()).toContain('当前筛选：4–5 岁')
    expect(wrapper.find('.library-option-chip[aria-pressed="true"]').exists()).toBe(false)
    const safety = wrapper.findAll('.library-option-chip').find(button => button.text() === '安全感')
    await safety.trigger('click')
    expect(wrapper.emitted('filter').at(-1)).toEqual([{ ageBand: '', topic: '安全感' }])
    await wrapper.setProps({ filters: { ageBand: '', topic: '安全感' } })
    expect(selectedOption()).toBe('安全感')
    expect(wrapper.text()).toContain('当前筛选：安全感')

    await clear.trigger('click')
    expect(wrapper.emitted('filter').at(-1)).toEqual([{ ageBand: '', topic: '' }])
    await wrapper.setProps({ filters: { ageBand: '', topic: '' } })
    expect(clear.classes()).toContain('active')
    expect(wrapper.text()).not.toContain('当前筛选：')
  } finally {
    wrapper.unmount()
  }
})
