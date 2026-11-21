import { afterEach, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import AccountPage from './AccountPage.vue'
import { recordPlanActivity } from '../data/readingPlan'

const render = (section, overrides = {}) => mount(AccountPage, {
  props: {
    section,
    auth: { nickname: '家长甲', account: 'guardian' },
    totals: {},
    saveProfileAction: vi.fn(),
    ...overrides,
  },
})

afterEach(() => localStorage.clear())

it.each([
  ['{', ['15', '1', '5']],
  ['null', ['15', '1', '5']],
  ['{"minutes":20,"books":9}', ['20', '1', '5']],
  ['{"minutes":45,"books":3,"weekly":6}', ['45', '3', '6']],
])('loads valid reading plan fields and defaults the rest (%s)', (raw, expected) => {
  localStorage.setItem('zhiya.plan', raw)
  const wrapper = render('plan')
  try {
    expect(wrapper.findAll('.plan-form input').map(({ element }) => element.value)).toEqual(expected)
  } finally {
    wrapper.unmount()
  }
})

it('refreshes the saved plan progress when visible again after midnight', async () => {
  vi.useFakeTimers()
  const evening = new Date(2026, 10, 21, 23, 30)
  vi.setSystemTime(evening)
  recordPlanActivity('8.2', evening.getTime(), 5 * 60_000, 55)
  const wrapper = render('plan', {
    auth: { accountId: 8 }, activeProfileId: 2,
    profiles: [{ childProfileId: 2, preferredMinutes: 15 }],
  })
  const values = () => wrapper.findAll('.stat-card strong').map(item => item.text())
  try {
    expect(values()).toEqual(['5/15', '1/1', '1/5'])
    vi.setSystemTime(new Date(2026, 10, 22, 1))
    document.dispatchEvent(new Event('visibilitychange'))
    await wrapper.vm.$nextTick()
    expect(values()).toEqual(['0/15', '0/1', '1/5'])
  } finally {
    wrapper.unmount()
    vi.useRealTimers()
  }
})

it('displays account identity without a nickname form and still emits sign-out', async () => {
  const wrapper = render('profile')
  try {
    expect(wrapper.find('form').exists()).toBe(false)
    const [nickname, account] = wrapper.findAll('.profile-form input')
    expect(nickname.element.value).toBe('家长甲')
    expect(account.element.value).toBe('guardian')
    expect(nickname.element.readOnly && account.element.readOnly).toBe(true)
    await wrapper.get('.profile-form button').trigger('click')
    expect(wrapper.emitted('sign-out')).toHaveLength(1)
  } finally {
    wrapper.unmount()
  }
})
