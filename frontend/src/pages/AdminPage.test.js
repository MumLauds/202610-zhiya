import { mount } from '@vue/test-utils'
import { expect, it, vi } from 'vitest'
import AdminPage from './AdminPage.vue'

it('shows the draft preview and withdraws the selected retired edition', async () => {
  const draft = { storyVersionId: 2, storybookId: 1, versionNumber: 2, status: 'DRAFT',
    title: '纸船', pages: [{ pageNumber: 1, text: '小船', imageRef: 'asset-2-page-1' }] }
  const retired = { ...draft, storyVersionId: 1, versionNumber: 1, status: 'RETIRED', pages: [] }
  const setStoryStatusAction = vi.fn()
  const view = mount(AdminPage, { props: { admin: { storyVersions: [draft, retired] },
    createStoryAction: vi.fn(), createKnowledgeAction: vi.fn(), updateKnowledgeAction: vi.fn(), setStoryStatusAction } })
  const cards = view.findAll('article')
  expect(cards[0].get('img').attributes('src')).toBe('/api/v1/media/asset-2-page-1')
  await cards[1].get('button').trigger('click')
  expect(setStoryStatusAction).toHaveBeenCalledWith(1, 'WITHDRAWN')
  view.unmount()
})
