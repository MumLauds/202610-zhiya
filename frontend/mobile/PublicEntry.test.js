import { flushPromises, shallowMount } from '@vue/test-utils'
import { afterEach, expect, it, vi } from 'vitest'
import PublicEntry from './PublicEntry.vue'

afterEach(() => vi.unstubAllGlobals())

it.each([503, 'offline'])('keeps the mobile entry on %s and retries', async failure => {
  const fetch = vi.fn()
  if (failure === 'offline') fetch.mockRejectedValueOnce(new TypeError('offline'))
  else fetch.mockResolvedValueOnce(new Response(null, { status: failure }))
  fetch.mockResolvedValueOnce(new Response(null, { status: 200 }))
  vi.stubGlobal('fetch', fetch)
  const view = shallowMount(PublicEntry)
  await flushPromises()
  expect(view.get('[role="alert"]').text()).toContain('重试')
  await view.get('button').trigger('click')
  await flushPromises()
  expect(fetch).toHaveBeenCalledTimes(2)
  expect(view.find('app-stub').exists()).toBe(true)
  view.unmount()
})

it('redirects an expired session to sign-in', async () => {
  vi.stubGlobal('location', { replace: vi.fn() })
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 401 })))
  const view = shallowMount(PublicEntry)
  await flushPromises()
  expect(window.location.replace).toHaveBeenCalledWith('/#auth')
  expect(view.find('app-stub').exists()).toBe(false)
  view.unmount()
})
