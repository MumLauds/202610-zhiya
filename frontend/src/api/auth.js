import { apiRequest } from './client'

export function checkHealth() {
  return apiRequest('/health')
}

export function login(account, password) {
  return apiRequest('/auth/sessions', {
    method: 'POST',
    body: { account, password },
  })
}

export function register(account, password) {
  return apiRequest('/auth/registrations', {
    method: 'POST',
    body: { account, password },
  })
}

export function logout() {
  return apiRequest('/auth/sessions/current', { method: 'DELETE' })
}
