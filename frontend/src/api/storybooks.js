import { apiRequest } from './client'

export async function listStorybooks({ ageBand, topic, page = 1, pageSize = 30 } = {}) {
  const query = new URLSearchParams({ page: String(page), pageSize: String(pageSize) })
  if (ageBand) query.set('ageBand', ageBand)
  if (topic) query.set('topic', topic)
  const result = await apiRequest(`/storybooks?${query}`)
  return result.data.items
}

export async function getStoryVersion(versionId) {
  const result = await apiRequest(`/story-versions/${versionId}`)
  return result.data
}

export async function recommendStorybooks({ childProfileId, topic, minutes } = {}) {
  const result = await apiRequest('/recommendations', {
    method: 'POST',
    body: {
      childProfileId,
      ...(topic ? { topic } : {}),
      ...(minutes ? { minutes } : {}),
    },
  })
  return result.data.items
}
