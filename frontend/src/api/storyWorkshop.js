import { apiRequest } from './client'

export async function generateStory(payload) {
  const result = await apiRequest('/story-generations', {
    method: 'POST',
    body: payload,
    // Allow a separate image generation window for an explicit opt-in.
    timeoutMs: payload.generateIllustrations ? 120000 : 70000,
  })
  return result.data
}

export async function approveStoryVersion(versionId, versionNumber) {
  const result = await apiRequest(`/story-versions/${versionId}/approvals`, {
    method: 'POST',
    body: {
      versionNumber,
      reviewedAllBranches: true,
    },
  })
  return result.data
}
