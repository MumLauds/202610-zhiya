import { mount } from '@vue/test-utils'
import { beforeEach } from 'vitest'
// Compile the page components during collection, outside individual test timeouts.
import '../src/App.vue'

let testSignal
beforeEach(({ signal }) => { testSignal = signal })

export async function mountApp(options) {
  const signal = testSignal
  // After resetModules, the store must read the route and storage set by this test.
  const { default: App } = await import('../src/App.vue')
  signal.throwIfAborted()
  return mount(App, options)
}
