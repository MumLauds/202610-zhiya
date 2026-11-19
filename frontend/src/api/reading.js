import { apiRequest } from './client'

export async function createReadingSession({ childProfileId, storyVersionId }) {
  const result = await apiRequest('/reading-sessions', {
    method: 'POST',
    body: {
      childProfileId,
      storyVersionId,
      mode: 'PARENT_CHILD',
    },
  })
  return result.data
}

export async function listReadingSessions(page = 1, pageSize = 30) {
  const result = await apiRequest(`/reading-sessions?page=${page}&pageSize=${pageSize}`)
  return result.data.items
}

export async function getReadingSession(sessionId) {
  const result = await apiRequest(`/reading-sessions/${sessionId}`)
  return result.data
}

export async function addReadingEvent(sessionId, payload) {
  const result = await apiRequest(`/reading-sessions/${sessionId}/events`, {
    method: 'POST',
    body: payload,
  })
  return result.data
}

export async function completeReadingSession(sessionId, payload) {
  const result = await apiRequest(`/reading-sessions/${sessionId}/completions`, {
    method: 'POST',
    body: payload,
  })
  return result.data
}
