import { apiRequest, createClientEventId, readCsrfCookie } from './client'

const uncertainWrites = new Map()
let adminSession = ''

async function writeAdmin(path, method, body) {
  const session = readCsrfCookie()
  if (session !== adminSession) {
    uncertainWrites.clear()
    adminSession = session
  }
  const signature = JSON.stringify([method, path, body])
  const key = uncertainWrites.get(signature) || createClientEventId('admin')
  try {
    const result = await apiRequest(path, { method, body, headers: { 'Idempotency-Key': key } })
    if (session === adminSession) uncertainWrites.delete(signature)
    return result.data
  } catch (error) {
    if (session === adminSession) {
      if (!error.status || error.status >= 500) uncertainWrites.set(signature, key)
      else uncertainWrites.delete(signature)
    }
    throw error
  }
}

export async function listAdminStoryVersions({ page = 1, pageSize = 50 } = {}) {
  const params = new URLSearchParams({ page: String(page), pageSize: String(pageSize) })
  const result = await apiRequest(`/admin/story-versions?${params}`, { cache: 'no-store' })
  return result.data
}

export async function createAdminStory(payload) {
  return writeAdmin('/admin/storybooks', 'POST', payload)
}

export async function updateAdminStoryStatus(versionId, status) {
  return writeAdmin(`/admin/story-versions/${versionId}`, 'PATCH', { status })
}

export async function listAdminKnowledge({ page = 1, pageSize = 50, status = '', query = '' } = {}) {
  const params = new URLSearchParams({ page: String(page), pageSize: String(pageSize) })
  if (status) params.set('status', status)
  if (query) params.set('query', query)
  const result = await apiRequest(`/admin/knowledge-items?${params}`, { cache: 'no-store' })
  return result.data
}

export async function createAdminKnowledge(payload) {
  return writeAdmin('/admin/knowledge-items', 'POST', payload)
}

export async function updateAdminKnowledge(knowledgeId, payload) {
  return writeAdmin(`/admin/knowledge-items/${knowledgeId}`, 'PATCH', payload)
}
