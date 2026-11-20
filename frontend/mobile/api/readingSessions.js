import { requestJson } from './client'

export class ReadingSessionsApi {
  constructor(request = requestJson) {
    this.request = request
  }

  create({ childProfileId, storyVersionId, signal }) {
    return this.request('/api/v1/reading-sessions', {
      method: 'POST',
      body: { childProfileId, storyVersionId, mode: 'PARENT_CHILD' },
      signal,
    })
  }

  list({ page = 1, pageSize = 20, signal } = {}) {
    const query = new URLSearchParams({ page: String(page), pageSize: String(pageSize) })
    return this.request(`/api/v1/reading-sessions?${query}`, { signal })
  }

  async findResumable({ storyVersionId, signal }) {
    const result = await this.list({ pageSize: 50, signal })
    return result.data?.items?.find(
      (item) => item.storyVersionId === storyVersionId && item.status !== 'FINISHED',
    ) ?? null
  }

  get(sessionId, { signal } = {}) {
    return this.request(`/api/v1/reading-sessions/${sessionId}`, { signal })
  }

  addEvent(sessionId, payload, { signal } = {}) {
    return this.request(`/api/v1/reading-sessions/${sessionId}/events`, {
      method: 'POST',
      body: payload,
      signal,
    })
  }

  submitQuestion(sessionId, payload, { signal } = {}) {
    return this.request(`/api/v1/reading-sessions/${sessionId}/questions`, {
      method: 'POST',
      body: payload,
      signal,
    })
  }

  submitGuidance(sessionId, payload, { signal } = {}) {
    return this.request(`/api/v1/reading-sessions/${sessionId}/guidance`, {
      method: 'POST',
      body: payload,
      signal,
    })
  }

  finish(sessionId, payload, { signal } = {}) {
    return this.request(`/api/v1/reading-sessions/${sessionId}/completions`, {
      method: 'POST',
      body: payload,
      signal,
    })
  }
}
