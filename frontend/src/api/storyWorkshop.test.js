import { beforeEach, describe, expect, it, vi } from 'vitest'
import { apiRequest } from './client'
import { approveStoryVersion, generateStory } from './storyWorkshop'

vi.mock('./client', () => ({ apiRequest: vi.fn() }))

describe('story workshop API contract', () => {
  beforeEach(() => vi.resetAllMocks())

  it('allows the original generation endpoint its sixty-second model budget', async () => {
    apiRequest.mockResolvedValue({ data: { storyVersionId: 31 } })
    const payload = {
      ageBand: 'AGE_4_5',
      topic: '友谊',
      characters: ['小兔'],
      scene: '森林',
      branching: true,
      clientRequestId: 'story-1',
    }
    expect(await generateStory(payload)).toEqual({ storyVersionId: 31 })
    expect(apiRequest).toHaveBeenCalledWith('/story-generations', {
      method: 'POST',
      body: payload,
      timeoutMs: 70000,
    })
  })

  it('sends the version number and reviewed-branches flag on approval', async () => {
    apiRequest.mockResolvedValue({ data: { storyVersionId: 31, status: 'APPROVED_PRIVATE' } })
    expect(await approveStoryVersion(31, 2)).toEqual({ storyVersionId: 31, status: 'APPROVED_PRIVATE' })
    expect(apiRequest).toHaveBeenCalledWith('/story-versions/31/approvals', {
      method: 'POST',
      body: { versionNumber: 2, reviewedAllBranches: true },
    })
  })
})
