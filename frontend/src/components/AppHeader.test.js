import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import AppHeader from './AppHeader.vue'

const render = route => mount(AppHeader, {
  props: {
    route,
    auth: { authenticated: true, nickname: '家长' },
  },
})

describe('primary navigation state', () => {
  it('renames the personal area and highlights it outside growth records', () => {
    const wrapper = render({ name: 'account', section: 'children' })
    const active = wrapper.findAll('.nav-button.active')
    expect(active).toHaveLength(1)
    expect(active[0].text()).toBe('个人中心')
    expect(wrapper.text()).not.toContain('亲子时光')
  })

  it('highlights only growth records on the growth page', () => {
    const wrapper = render({ name: 'account', section: 'growth' })
    const active = wrapper.findAll('.nav-button.active')
    expect(active).toHaveLength(1)
    expect(active[0].text()).toBe('成长记录')
  })
})
