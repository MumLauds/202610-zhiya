import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import WorkshopPage from './WorkshopPage.vue'

const draft = {
  storybookId: 21,
  storyVersionId: 31,
  versionNumber: 1,
  title: '月光下的小兔',
  status: 'PENDING_REVIEW',
  pages: [
    { pageNumber: 1, text: '小兔在森林里听见一阵轻轻的哭声。', imageRef: 'asset-31-page-1', defaultNextPageNumber: 2 },
    { pageNumber: 2, text: '原来小熊的风筝挂在了树上。', imageRef: 'asset-31-page-2', defaultNextPageNumber: null },
    { pageNumber: 3, text: '他们一起想出了新办法。', imageRef: 'asset-31-page-3', defaultNextPageNumber: 4 },
    { pageNumber: 4, text: '月亮升起来时，风筝重新飞上了天空。', imageRef: 'asset-31-page-4', defaultNextPageNumber: null },
  ],
  branches: [
    { branchId: 101, fromPageNumber: 2, nextPageNumber: 3, choiceLabel: '请猫头鹰帮忙', reviewStatus: 'PENDING' },
    { branchId: 102, fromPageNumber: 2, nextPageNumber: 4, choiceLabel: '找来一根长树枝', reviewStatus: 'PENDING' },
  ],
  checkResults: { structurePassed: true, safetyPassed: true, licensePassed: true },
}

function workshop(overrides = {}) {
  return {
    draft: null,
    generationStatus: 'idle',
    generationError: '',
    pendingGeneration: null,
    approvalBusy: false,
    approvedVersion: null,
    continuation: null,
    continuationLoading: false,
    continuationError: '',
    switchPending: null,
    switchBusy: false,
    switchError: '',
    ...overrides,
  }
}

function render(overrides = {}, props = {}) {
  return mount(WorkshopPage, {
    props: {
      workshop: workshop(overrides),
      auth: { authenticated: true, role: 'GUARDIAN' },
      activeProfile: { childProfileId: 7, ageBand: 'AGE_4_5', nickname: '小芽' },
      sourceSessionId: null,
      generateAction: vi.fn(async () => true),
      retryGenerationAction: vi.fn(async () => true),
      resetAction: vi.fn(() => true),
      approveAction: vi.fn(async () => true),
      openShelfAction: vi.fn(async () => true),
      continueReadingAction: vi.fn(async () => true),
      ...props,
    },
  })
}

describe('original story workshop', () => {
  it('shows the initial private-draft workflow and selected story facts', () => {
    const wrapper = render()
    expect(wrapper.get('.workshop-heading h1').text()).toBe('为孩子创作一本原创绘本')
    expect(wrapper.text()).toContain('草稿只对你可见')
    expect(wrapper.text()).toContain('4–6 页')
    expect(wrapper.get('.workshop-blank').text()).toContain('友谊')
  })

  it('previews every page and branch, then enables approval after the parent confirmation', async () => {
    const approveAction = vi.fn(async () => true)
    const wrapper = render({ draft, generationStatus: 'review' }, { approveAction })

    expect(wrapper.findAll('.review-page')).toHaveLength(4)
    expect(wrapper.get('.branch-review').text()).toContain('请猫头鹰帮忙')
    expect(wrapper.get('.branch-review').text()).toContain('找来一根长树枝')
    expect(wrapper.get('.approval-panel .primary').attributes('disabled')).toBeDefined()

    await wrapper.get('.approval-check input').setValue(true)
    expect(wrapper.get('.approval-panel .primary').attributes('disabled')).toBeUndefined()
    await wrapper.get('.approval-panel .primary').trigger('click')
    expect(approveAction).toHaveBeenCalledTimes(1)
  })

  it('shows a recoverable generation result instead of creating a second request', async () => {
    const retryGenerationAction = vi.fn(async () => true)
    const wrapper = render({
      generationStatus: 'unknown',
      generationError: '请求结果尚未确认。',
    }, { retryGenerationAction })

    expect(wrapper.text()).toContain('这次生成可能仍在处理中')
    await wrapper.get('.workshop-state .primary').trigger('click')
    expect(retryGenerationAction).toHaveBeenCalledTimes(1)
  })

  it('shows the shelf error and lets the guardian retry without approving again', async () => {
    const approved = { ...draft, status: 'APPROVED_PRIVATE' }
    const wrapper = render({ draft: approved, approvedVersion: approved, generationStatus: 'approved', shelfError: '在线书架暂不可用' })
    expect(wrapper.get('.approved-panel [role="alert"]').text()).toBe('在线书架暂不可用')
    const retry = wrapper.get('.approved-actions .secondary')
    expect(retry.text()).toContain('重试')
    await retry.trigger('click')
    expect(wrapper.props('openShelfAction')).toHaveBeenCalledOnce()
    expect(wrapper.props('approveAction')).not.toHaveBeenCalled()
  })

  it('lets the guardian start another original after approval', async () => {
    const approved = { storyVersionId: draft.storyVersionId, title: draft.title, status: 'APPROVED_PRIVATE', pages: draft.pages }
    const resetAction = vi.fn(() => true)
    const wrapper = render({}, { resetAction })
    await wrapper.findAll('.choice-chip').find(button => button.text() === '勇气').trigger('click')
    await wrapper.setProps({ workshop: workshop({ draft: approved, approvedVersion: approved, generationStatus: 'approved' }) })
    expect(wrapper.find('.check-strip').exists()).toBe(false)
    await wrapper.get('.draft-heading-actions button').trigger('click')
    expect(resetAction).toHaveBeenCalledTimes(1)
    await wrapper.setProps({ workshop: workshop() })
    await wrapper.get('form').trigger('submit')
    expect(wrapper.props('generateAction')).toHaveBeenCalledWith(expect.objectContaining({ topic: '友谊' }))
  })

  it('lets the guardian close a server-confirmed unknown outcome without submitting again', async () => {
    const resetAction = vi.fn(() => true)
    const wrapper = render({ generationStatus: 'endedUnknown' }, { resetAction })
    expect(wrapper.get('.workshop-submit').attributes('disabled')).toBeDefined()
    await wrapper.get('.workshop-result-state button').trigger('click')
    expect(resetAction).toHaveBeenCalledTimes(1)
    expect(wrapper.props('generateAction')).not.toHaveBeenCalled()
  })

  it('explains continuation limits and starts from the approved resume point', async () => {
    const continueReadingAction = vi.fn(async () => true)
    const approved = {
      ...draft,
      status: 'APPROVED_PRIVATE',
      continuation: { resumePageNumber: 3, sourceRevision: 4 },
    }
    const continuation = {
      profile: { childProfileId: 8, nickname: '小松', ageBand: 'AGE_3_4' },
      session: {
        childProfileId: 8,
        readingSessionId: 55,
        status: 'IN_PROGRESS',
        mode: 'PARENT_CHILD',
        revision: 4,
        visitedPageNumbers: [1, 2],
      },
      version: { storyVersionId: 31, title: '月光下的小兔', status: 'APPROVED_PRIVATE' },
      resumePageNumber: 3,
    }
    const wrapper = render({
      draft: approved,
      approvedVersion: approved,
      generationStatus: 'approved',
      continuation,
    }, { continueReadingAction })

    expect(wrapper.get('.continuation-card').text()).toContain('源会话档案：小松')
    expect(wrapper.text()).toContain('前 2 个已读页面沿用原文和冻结配图')
    expect(wrapper.text()).toContain('从第 3 页继续阅读')
    await wrapper.get('.approved-actions .primary').trigger('click')
    expect(continueReadingAction).toHaveBeenCalledTimes(1)
  })
})
