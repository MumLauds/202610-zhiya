import { afterEach, expect, it, vi } from 'vitest'
import { createAdminStory, updateAdminKnowledge } from './admin'

afterEach(() => {
  document.cookie = 'zhiya_csrf=; Max-Age=0; path=/'
  vi.unstubAllGlobals()
})

it('retains an uncertain admin write key for the same request and releases it after confirmation', async () => {
  document.cookie = 'zhiya_csrf=test-editor; path=/'
  const success = () => new Response(JSON.stringify({ data: { storyVersionId: 7 } }), {
    headers: { 'Content-Type': 'application/json' },
  })
  const fetch = vi.fn().mockRejectedValueOnce(new TypeError('offline')).mockImplementation(async () => success())
  vi.stubGlobal('fetch', fetch)
  const body = { title: '纸船' }
  await expect(createAdminStory(body)).rejects.toMatchObject({ code: 'REQUEST_OUTCOME_UNKNOWN' })
  await expect(createAdminStory({ ...body })).resolves.toEqual({ storyVersionId: 7 })
  await createAdminStory(body)
  const keys = fetch.mock.calls.map(([, options]) => options.headers.get('Idempotency-Key'))
  expect(keys[0]).toBeTruthy()
  expect(keys[1]).toBe(keys[0])
  expect(keys[2]).not.toBe(keys[0])
})

it('does not reuse uncertain knowledge revisions for a different session', async () => {
  const fetch = vi.fn().mockRejectedValue(new TypeError('offline'))
  vi.stubGlobal('fetch', fetch)
  for (const csrf of ['editor-a', 'editor-b']) {
    document.cookie = `zhiya_csrf=${csrf}; path=/`
    await expect(updateAdminKnowledge(4, { status: 'APPROVED' })).rejects.toMatchObject({ code: 'REQUEST_OUTCOME_UNKNOWN' })
  }
  const keys = fetch.mock.calls.map(([, options]) => options.headers.get('Idempotency-Key'))
  expect(keys[1]).not.toEqual(keys[0])
})
