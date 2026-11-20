import { apiRequest } from './client'

export async function generateStory(payload) {
  const result = await apiRequest('/story-generations', {
    method: 'POST',
    body: payload,
    // The backend reserves 65 seconds and allows its model call 60 seconds.
    timeoutMs: 70000,
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
