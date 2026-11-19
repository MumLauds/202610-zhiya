import { apiRequest } from './client'

export async function listProfiles() {
  const result = await apiRequest('/child-profiles')
  return result.data.items
}

export async function createProfile(profile) {
  const result = await apiRequest('/child-profiles', {
    method: 'POST',
    body: profile,
  })
  return result.data
}

export async function updateProfile(profileId, profile) {
  const result = await apiRequest(`/child-profiles/${profileId}`, {
    method: 'PATCH',
    body: profile,
  })
  return result.data
}

export async function deleteProfile(profileId) {
  await apiRequest(`/child-profiles/${profileId}`, {
    method: 'DELETE',
  })
}
