import { apiRequest } from './client'

export async function listAdminStoryVersions({ page = 1, pageSize = 50 } = {}) {
  const params = new URLSearchParams({ page: String(page), pageSize: String(pageSize) })
  const result = await apiRequest(`/admin/story-versions?${params}`, { cache: 'no-store' })
  return result.data
}

export async function createAdminStory(payload) {
  const result = await apiRequest('/admin/storybooks', { method: 'POST', body: payload })
  return result.data
}

export async function updateAdminStoryStatus(versionId, status) {
  const result = await apiRequest(`/admin/story-versions/${versionId}`, {
    method: 'PATCH',
    body: { status },
  })
  return result.data
}

export async function listAdminKnowledge({ page = 1, pageSize = 50, status = '', query = '' } = {}) {
  const params = new URLSearchParams({ page: String(page), pageSize: String(pageSize) })
  if (status) params.set('status', status)
  if (query) params.set('query', query)
  const result = await apiRequest(`/admin/knowledge-items?${params}`, { cache: 'no-store' })
  return result.data
}

export async function createAdminKnowledge(payload) {
  const result = await apiRequest('/admin/knowledge-items', { method: 'POST', body: payload })
  return result.data
}

export async function updateAdminKnowledge(knowledgeId, payload) {
  const result = await apiRequest(`/admin/knowledge-items/${knowledgeId}`, {
    method: 'PATCH',
    body: payload,
  })
  return result.data
}
